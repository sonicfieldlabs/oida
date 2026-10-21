"""Bounded, anonymous public Discovery client. Never fetch arbitrary/source URLs.

One request maps to one of the three published read-only data tools. No account,
audio, display mutation, retry, recursive crawl, persistent cache or tool executor.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import threading
import time
from uuid import uuid4

import httpx2 as httpx
from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError

from oida.contracts import now_iso
from oida.operation_control import checkpoint, remaining_timeout
from oida.research_references import (
    PageInput,
    PublicRecord,
    RecordInput,
    ResearchContext,
    SearchData,
    RelationsData,
    RelationsInput,
    ResearchReference,
    SearchInput,
    SelectedRecord,
    Strict,
)

ENDPOINT = "https://discovery.sonicfield.org/mcp"
MAX_RESPONSE_BYTES = 524288
TIMEOUT_SECONDS = 25.0
RESULT_CONTRACT = "oida/sonicfield-result/v1"
ERROR_CODES = {
    "invalid_request",
    "unsupported_filter",
    "unsupported_resource",
    "unsupported_relation",
    "invalid_cursor",
    "stale_cursor",
    "upstream_unavailable",
    "upstream_timeout",
    "upstream_rate_limited",
    "not_found",
    "invalid_upstream_response",
    "upstream_filter_ignored",
    "upstream_no_progress",
    "response_too_large",
    "budget_exhausted",
    "cancelled",
}


class DiscoveryError(ValueError):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def _strict_json(raw):
    def reject(_):
        raise ValueError("Non-finite JSON")

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate JSON property")
            obj[key] = value
        return obj

    return json.loads(raw, parse_constant=reject, object_pairs_hook=unique)


def decode_rpc(raw: bytes, content_type: str, request_id: str) -> dict:
    """JSON and the bounded SSE message response used by the deployed MCP edge."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DiscoveryError(
            "response_too_large", "Discovery response exceeded 512 KiB"
        )
    try:
        if content_type.split(";", 1)[0].strip().lower() == "application/json":
            messages = [_strict_json(raw)]
        elif content_type.split(";", 1)[0].strip().lower() == "text/event-stream":
            text = raw.decode("utf-8").replace("\r\n", "\n")
            messages = []
            for frame in text.split("\n\n"):
                data = "\n".join(
                    line[5:].lstrip(" ")
                    for line in frame.splitlines()
                    if line.startswith("data:")
                )
                if data:
                    messages.append(_strict_json(data))
        else:
            raise ValueError("Unsupported content type")
        matching = [
            m for m in messages if isinstance(m, dict) and m.get("id") == request_id
        ]
        if len(matching) != 1 or matching[0].get("jsonrpc") != "2.0":
            raise ValueError("Missing, repeated or mismatched RPC result")
        rpc = matching[0]
        if "error" in rpc or not isinstance(rpc.get("result"), dict):
            raise ValueError("RPC call did not return a tool result")
        result = rpc["result"]
        body = result.get("structuredContent")
        if not isinstance(body, dict):
            raise ValueError("Structured Discovery result is required")
        if (
            set(body) != {"schemaVersion", "ok", "data", "error"}
            or body["schemaVersion"] != "1.1"
            or type(body["ok"]) is not bool
        ):
            raise ValueError("Unsupported Discovery result contract")
        if body["ok"]:
            if (
                body["error"] is not None
                or not isinstance(body["data"], dict)
                or result.get("isError") is True
            ):
                raise ValueError("Result branch mismatch")
        else:
            err = body["error"]
            if (
                body["data"] is not None
                or not isinstance(err, dict)
                or set(err) != {"code", "message", "retryable"}
                or err["code"] not in ERROR_CODES
                or type(err["retryable"]) is not bool
                or not isinstance(err["message"], str)
                or len(err["message"]) > 4000
            ):
                raise ValueError("Invalid failure branch")
        return body
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise DiscoveryError(
            "invalid_upstream_response",
            "Discovery returned an unsupported or malformed result",
        ) from exc


class PublicDiscovery:
    def __init__(self, *, transport=None):
        # Dependency injection is test-only; endpoint and public operation allowlist are fixed.
        self._transport = transport
        self._slots = threading.BoundedSemaphore(3)
        self._state_lock = threading.Lock()
        self._last = None

    def status(self):
        with self._state_lock:
            last = deepcopy(self._last)
        return dict(
            endpoint=ENDPOINT,
            configured=True,
            schema="1.1",
            last_request=last,
            availability="not_probed"
            if last is None
            else ("last_request_succeeded" if last["ok"] else "last_request_failed"),
            limits=dict(
                selected_sources=5,
                response_bytes=MAX_RESPONSE_BYTES,
                operation_seconds=TIMEOUT_SECONDS,
                concurrency=3,
                retries=0,
                automatic_hops=0,
            ),
            private_input="never inferred; caller supplies public-safe query or identity",
            tools="catalog only; no executor",
            cache="no owner cache; upstream retrieval/validation times retained",
        )

    async def _request(self, name, arguments, request_id, timeout):
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
                timeout=timeout,
            ) as client:
                async with client.stream(
                    "POST",
                    ENDPOINT,
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2025-03-26",
                        "User-Agent": "sonicfield-oida/public-discovery-v1",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                ) as response:
                    if response.status_code != 200:
                        status = response.status_code
                        code = (
                            "upstream_rate_limited"
                            if status == 429
                            else "upstream_unavailable"
                        )
                        if 300 <= status < 400:
                            code = "invalid_upstream_response"
                        raise DiscoveryError(
                            code,
                            f"Public Discovery returned HTTP {status}",
                            status in {429, 502, 503, 504},
                        )
                    length = response.headers.get("content-length")
                    if length and (
                        not length.isdigit() or int(length) > MAX_RESPONSE_BYTES
                    ):
                        raise DiscoveryError(
                            "response_too_large",
                            "Discovery response exceeded its byte limit",
                        )
                    chunks, count = [], 0
                    async for chunk in response.aiter_bytes():
                        checkpoint()
                        count += len(chunk)
                        if count > MAX_RESPONSE_BYTES:
                            raise DiscoveryError(
                                "response_too_large",
                                "Discovery response exceeded 512 KiB",
                            )
                        chunks.append(chunk)
                    checkpoint()
                    return decode_rpc(
                        b"".join(chunks),
                        response.headers.get("content-type", ""),
                        request_id,
                    )

    def _call(self, name, arguments, *, timeout=TIMEOUT_SECONDS):
        if name not in {
            "search_sonic_field",
            "get_sonic_field_record",
            "get_sonic_field_relations",
        }:
            raise ValueError("Only public read-only Discovery data tools are supported")
        checkpoint()
        if not self._slots.acquire(blocking=False):
            raise DiscoveryError(
                "budget_exhausted", "Public retrieval concurrency limit reached", True
            )
        try:
            from oida.research_recipes import consume
            consume("public_calls")
            result = asyncio.run(
                self._request(
                    name,
                    arguments,
                    uuid4().hex,
                    remaining_timeout(min(timeout, TIMEOUT_SECONDS)),
                )
            )
            checkpoint()
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise DiscoveryError(
                "upstream_timeout", "Public retrieval deadline reached", True
            ) from exc
        except httpx.HTTPError as exc:
            raise DiscoveryError(
                "upstream_unavailable", "Public Discovery could not be reached", True
            ) from exc
        finally:
            self._slots.release()
        return result

    def _wrap(self, body, references, *, operation):
        with self._state_lock:
            self._last = dict(
                operation=operation,
                observed_at=now_iso(),
                ok=body["ok"],
                error_code=(body["error"] or {}).get("code"),
            )
        outcome = "retrieved" if body["ok"] else body["error"]["code"]
        if body["ok"] and body["data"].get("partial"):
            outcome = "partial"
        return dict(
            contract=RESULT_CONTRACT,
            outcome=outcome,
            discovery=body,
            references=[r.model_dump(mode="json") for r in references],
            authority="reference context only; no identity adoption, model execution or tool permission",
        )

    def failure(self, exc, operation):
        return self._wrap(
            dict(
                schemaVersion="1.1",
                ok=False,
                data=None,
                error=dict(code=exc.code, message=str(exc), retryable=exc.retryable),
            ),
            [],
            operation=operation,
        )

    def _record(self, raw):
        try:
            return PublicRecord.model_validate(raw)
        except ValidationError as exc:
            raise DiscoveryError(
                "invalid_upstream_response",
                "Public record failed the supported Discovery 1.1 contract",
            ) from exc

    def record(self, req: RecordInput, *, timeout=TIMEOUT_SECONDS):
        try:
            body = self._call(
                "get_sonic_field_record",
                req.model_dump(exclude_none=True),
                timeout=timeout,
            )
            refs = []
            if body["ok"]:
                record = self._record(body["data"])
                if (
                    record.identity.family != req.family
                    or record.identity.slug != req.slug
                    or record.requestedLocale != req.locale
                    or (
                        req.expectedId is not None
                        and record.identity.id != req.expectedId
                    )
                ):
                    raise DiscoveryError(
                        "invalid_upstream_response",
                        "Selected public identity or locale changed",
                    )
                refs = [ResearchReference.from_record(record)]
            return self._wrap(body, refs, operation="record")
        except DiscoveryError as exc:
            return self.failure(exc, "record")

    def page(self, req: PageInput):
        return self.record(req.record_input())

    def search(self, req: SearchInput):
        try:
            body = self._call("search_sonic_field", req.model_dump(exclude_none=True))
            refs = []
            if body["ok"]:
                data = body["data"]
                parsed = SearchData.model_validate(data)
                if (
                    parsed.query != req.query
                    or len(parsed.results) > req.limit
                    or {c.family for c in parsed.coverage} != set(req.families)
                    or any(
                        c.query != req.query
                        or c.filters != req.filters.get(c.family, {})
                        or (c.error is not None and c.error.code not in ERROR_CODES)
                        for c in parsed.coverage
                    )
                ):
                    raise DiscoveryError(
                        "invalid_upstream_response",
                        "Search coverage or result bounds changed",
                    )
                seen = set()
                for ranked in parsed.results:
                    raw = ranked.model_dump(exclude={"score", "matchReasons"})
                    record = self._record(raw)
                    identity = (record.identity.family, record.identity.id)
                    if (
                        record.identity.family not in req.families
                        or record.requestedLocale != req.locale
                        or identity in seen
                    ):
                        raise DiscoveryError(
                            "invalid_upstream_response",
                            "Search returned a repeated or unselected identity",
                        )
                    seen.add(identity)
                    refs.append(ResearchReference.from_record(record))
            return self._wrap(body, refs, operation="search")
        except (DiscoveryError, ValidationError, TypeError, AttributeError) as exc:
            if not isinstance(exc, DiscoveryError):
                exc = DiscoveryError(
                    "invalid_upstream_response", "Malformed public search response"
                )
            return self.failure(exc, "search")

    def relations(self, req: RelationsInput):
        try:
            body = self._call("get_sonic_field_relations", req.model_dump())
            if body["ok"]:
                data = body["data"]
                parsed = RelationsData.model_validate(data)
                focus = parsed.focus
                if (
                    focus.identity.family != "lexicon"
                    or focus.identity.slug != req.slug
                    or focus.requestedLocale != req.locale
                ):
                    raise DiscoveryError(
                        "invalid_upstream_response", "Outgoing Lexicon focus changed"
                    )
                if (
                    len(parsed.relations) > req.limit
                    or parsed.coverage.returned != len(parsed.relations)
                    or any(e.error.code not in ERROR_CODES for e in parsed.errors)
                ):
                    raise DiscoveryError(
                        "invalid_upstream_response", "Outgoing reference bounds changed"
                    )
                for row in parsed.relations:
                    if (
                        row.target.identity.family != "lexicon"
                        or row.target.requestedLocale != req.locale
                    ):
                        raise DiscoveryError(
                            "invalid_upstream_response", "Unsupported relationship"
                        )
            # Relation targets are leads; selected detail hydration is a separate action.
            return self._wrap(body, [], operation="relations")
        except (DiscoveryError, ValidationError, KeyError, TypeError) as exc:
            if not isinstance(exc, DiscoveryError):
                exc = DiscoveryError(
                    "invalid_upstream_response", "Malformed outgoing reference response"
                )
            return self.failure(exc, "relations")

    def selected(self, records: list[SelectedRecord]):
        if not 1 <= len(records) <= 5:
            raise ValueError("Select one to five exact public records")
        deadline = time.monotonic() + remaining_timeout(TIMEOUT_SECONDS)
        sources, notes, receipts = [], [], []
        seen = set()
        for record in records:
            checkpoint()
            key = (record.family, record.expectedId)
            if key in seen:
                continue
            seen.add(key)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                note = (
                    "Public selected-source retrieval stopped at its operation deadline"
                )
                if note not in notes:
                    notes.append(note)
                receipts.append(
                    dict(
                        identity=record.model_dump(),
                        outcome="budget_exhausted",
                        error=dict(
                            code="budget_exhausted",
                            message="Shared operation deadline reached before this identity",
                            retryable=True,
                        ),
                    )
                )
                continue
            result = self.record(record, timeout=remaining)
            receipts.append(
                dict(
                    identity=record.model_dump(),
                    outcome=result["outcome"],
                    error=result["discovery"]["error"],
                )
            )
            sources.extend(
                ResearchReference.model_validate(r).source()
                for r in result["references"]
            )
            if not result["discovery"]["ok"]:
                notes.append(
                    "Selected public reference unavailable: " + result["outcome"]
                )
        return sources, notes, receipts


class OperationInput(Strict):
    operation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    deadline_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class SearchRequest(SearchInput, OperationInput):
    pass


class RecordRequest(RecordInput, OperationInput):
    pass


class PageRequest(PageInput, OperationInput):
    pass


class RelationsRequest(RelationsInput, OperationInput):
    pass


def public_router(discovery: PublicDiscovery, operations):
    router = APIRouter(prefix="/sonicfield", tags=["public research"])

    def execute(req, model, callback):
        data = req.model_dump(exclude={"operation_id", "deadline_at"})
        try:
            selected = model.model_validate(data)
            return operations.run(
                req.operation_id,
                lambda: callback(selected),
                deadline_at=req.deadline_at,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/public/status")
    def status():
        return discovery.status()

    @router.get("/schema/{kind}")
    def schema(kind: str):
        models = {"reference": ResearchReference, "context": ResearchContext}
        if kind not in models:
            raise HTTPException(404, "Unknown research contract")
        return models[kind].model_json_schema()

    @router.post("/search")
    def search(req: SearchRequest):
        return execute(req, SearchInput, discovery.search)

    @router.post("/record")
    def record(req: RecordRequest):
        return execute(req, RecordInput, discovery.record)

    @router.post("/page")
    def page(req: PageRequest):
        return execute(req, PageInput, discovery.page)

    @router.post("/relations")
    def relations(req: RelationsRequest):
        return execute(req, RelationsInput, discovery.relations)

    return router
