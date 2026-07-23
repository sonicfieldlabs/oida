"""Oída → GERM bridge over the shared akousma protocol.

Oída hears; GERM cultivates. After a listen, Oída persists an **akousma** (the
sound's memory record) into the shared **Akousmata** store and hands GERM an
``akousma_id`` via a deep link. The three UI buttons map to three modes:

- ``sound``   — "open as sound":    load the listened fragment as an audio source in GERM.
- ``prompt``  — "open as prompt":   open the listening result as a generation prompt in GERM.
- ``lineage`` — "explore lineage":  open GERM's ancestry explorer on this akousma.

Requires the ``akousma`` package (earworm/packages/py-akousma).
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Literal
from urllib.parse import urlencode

import akousma

from oida.listening_identity import (
    LISTENING_IDENTITY_CONTRACT,
    LISTENING_IDENTITY_FILENAME,
    LISTENING_IDENTITY_ROLE,
)

MODES = ("sound", "prompt", "lineage")


def germ_base_url() -> str:
    """Germ's base URL for deep links (``OIDA_GERM_URL``, default local dashboard)."""
    return os.getenv("OIDA_GERM_URL", "http://127.0.0.1:5178").rstrip("/")


def germ_deep_link(akousma_id: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    query = urlencode({"akousma": akousma_id, "mode": mode})
    return f"{germ_base_url()}/import?{query}"


# oída's internal source vocabulary is underscored (memory.py); the akousma
# provenance.origin enum is hyphenated. Normalize before validation so an
# unmapped origin cannot fail store.put.
_ORIGIN_ALIASES = {
    "live_input": "live-input",
    "system_output": "system-output",
    "buffer": "live-input",
    "external_stream": "system-output",
    "external-stream": "system-output",
}


def _normalize_origin(origin: str) -> str:
    origin = (origin or "file").strip().lower()
    return _ORIGIN_ALIASES.get(origin, origin.replace("_", "-"))


def _origin_to_source_type(origin: str) -> str:
    """Map oída's capture origin to Earworm's provenance source_type vocabulary."""
    return {
        "live-input": "recorded",
        "system-output": "recorded",
        "file": "imported",
        "generated": "generated",
    }.get(origin, "unknown")


AKOUO_CONTRACT = "akouo/v0.8"
OIDA_LISTENING_CONTRACT = "oida/listening-event/v0.2"


def _envelope_listening(listening: dict[str, Any]) -> dict[str, Any]:
    """Wrap raw producer payloads in the akousma spec v1.1 listening envelope
    (``{contract?, created_at, summary?, payload}``). Entries already enveloped
    pass through untouched; akouo.* entries get the contract pin."""
    wrapped: dict[str, Any] = {}
    for namespace, value in (listening or {}).items():
        if isinstance(value, dict) and "payload" in value and set(value) <= {"contract", "created_at", "summary", "payload"}:
            wrapped[namespace] = value
            continue
        entry: dict[str, Any] = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload": value,
        }
        if namespace.startswith("akouo."):
            entry["contract"] = AKOUO_CONTRACT
        elif namespace == "oida.listen":
            entry["contract"] = OIDA_LISTENING_CONTRACT
        if isinstance(value, dict):
            for key in ("summary", "caption", "brief", "main_reading"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    entry["summary"] = text.strip()
                    break
        wrapped[namespace] = entry
    return wrapped


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _auditum_from_listening(
    listening: dict[str, Any],
    *,
    covenant: dict[str, Any] | None = None,
    disagreements: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Derive one attributable auditum without inventing an ear swarm.

    Route reports remain distinct, but they share the single ``oida`` listener
    identity. A namespace is a report boundary, not a new agent.
    """
    if not listening:
        return None
    listenings: list[dict[str, Any]] = []
    namespace_ids: dict[str, str] = {}
    honest_absences: list[dict[str, Any]] = []
    seen_absences: set[tuple[str, str, str, str | None]] = set()
    for index, (namespace, entry) in enumerate(listening.items()):
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        listening_id = f"lst_{index + 1}_{re.sub(r'[^a-z0-9]+', '_', namespace.lower()).strip('_') or 'report'}"
        namespace_ids[namespace] = listening_id
        token = _pointer_token(namespace)
        contract = str(entry.get("contract") or payload.get("contract") or OIDA_LISTENING_CONTRACT)
        report: dict[str, Any] = {
            "listening_id": listening_id,
            "listener_id": "oida",
            "listener_type": "agent",
            "created_at": str(entry.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            "report_namespace": namespace,
            "contract": contract,
        }
        if isinstance(payload.get("listening_context"), dict):
            report["context_ref"] = f"#/listening/{token}/payload/listening_context"
        if isinstance(payload.get("apparatus"), dict):
            report["apparatus_ref"] = f"#/listening/{token}/payload/apparatus"
        if isinstance(payload.get("claim_summary"), dict):
            report["claim_set_ref"] = f"#/listening/{token}/payload/claim_summary"
        elif isinstance(payload.get("listening_claims"), dict):
            report["claim_set_ref"] = f"#/listening/{token}/payload/listening_claims"
        if covenant:
            report["covenant_ref"] = "#/covenant"
        route_ids: list[str] = []
        if isinstance(payload.get("route_id"), str):
            route_ids.append(payload["route_id"])
        if isinstance(payload.get("routes"), list):
            route_ids.extend(
                str(route.get("route_id"))
                for route in payload["routes"]
                if isinstance(route, dict) and route.get("route_id")
            )
        if namespace.startswith("akouo."):
            route_ids.append(namespace.removeprefix("akouo."))
        if route_ids:
            report["route"] = list(dict.fromkeys(route_ids))
        listenings.append(report)

        context = payload.get("listening_context") if isinstance(payload.get("listening_context"), dict) else {}
        for absence in context.get("honest_absences", []):
            if not isinstance(absence, dict):
                continue
            kind = str(absence.get("kind") or "undetermined")
            subject = str(absence.get("subject") or "").strip()
            attributed_to = str(absence.get("attributed_to") or "").strip()
            key = (kind, subject, attributed_to, listening_id)
            if not subject or not attributed_to or key in seen_absences:
                continue
            seen_absences.add(key)
            item: dict[str, Any] = {
                "id": f"abs_{len(honest_absences) + 1}",
                "kind": kind,
                "subject": subject,
                "attributed_to": attributed_to,
                "listening_id": listening_id,
            }
            for field in ("count", "note"):
                if field in absence:
                    item[field] = absence[field]
            honest_absences.append(item)

    if not listenings:
        return None

    covenant_id = str((covenant or {}).get("id") or "covenant")
    for withheld in (covenant or {}).get("withheld", []):
        if not isinstance(withheld, dict):
            continue
        subject = str(withheld.get("subject") or "").strip()
        rule = str(withheld.get("rule") or "withheld").strip()
        attributed_to = f"{covenant_id}:{rule}"
        key = ("withheld", subject, attributed_to, None)
        if not subject or key in seen_absences:
            continue
        seen_absences.add(key)
        honest_absences.append({
            "id": f"abs_{len(honest_absences) + 1}",
            "kind": "withheld",
            "subject": subject,
            "attributed_to": attributed_to,
            "rule": rule,
            "count": withheld.get("count") if isinstance(withheld.get("count"), int) else 1,
        })

    checked_disagreements: list[dict[str, Any]] = []
    known_ids = {item["listening_id"] for item in listenings}
    for index, disagreement in enumerate(disagreements or []):
        if not isinstance(disagreement, dict):
            continue
        raw_ids = disagreement.get("listening_ids") if isinstance(disagreement.get("listening_ids"), list) else []
        ids = [namespace_ids.get(str(value), str(value)) for value in raw_ids]
        ids = list(dict.fromkeys(value for value in ids if value in known_ids))
        positions: list[dict[str, Any]] = []
        for position in disagreement.get("positions", []):
            if not isinstance(position, dict):
                continue
            position_id = namespace_ids.get(str(position.get("listening_id")), str(position.get("listening_id") or ""))
            statement = str(position.get("statement") or "").strip()
            if position_id not in ids or not statement:
                continue
            item = {"listening_id": position_id, "statement": statement}
            category = position.get("claim_category")
            if category in {"heard", "measured", "inferred", "interpreted", "speculative", "undetermined"}:
                item["claim_category"] = category
            positions.append(item)
        subject = str(disagreement.get("subject") or "").strip()
        if len(ids) < 2 or len(positions) < 2 or not subject:
            continue
        checked_disagreements.append({
            "id": str(disagreement.get("id") or f"dis_{index + 1}"),
            "subject": subject,
            "listening_ids": ids,
            "positions": positions,
            "status": disagreement.get("status") if disagreement.get("status") in {"preserved", "resolved", "undetermined"} else "preserved",
            "resolution_note": disagreement.get("resolution_note"),
        })

    return akousma.auditum(
        listenings=listenings,
        disagreements=checked_disagreements,
        honest_absences=honest_absences,
        actions=actions or [],
    )


def _derive_summary(listening: dict[str, Any]) -> str | None:
    """Skimmable one-liner for the record, preferring oída's own signal caption."""
    for namespace in ("oida.signal", "oida.moss", "akouo.describe"):
        entry = listening.get(namespace)
        if isinstance(entry, dict):
            if isinstance(entry.get("summary"), str) and entry["summary"].strip():
                return entry["summary"].strip()
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else entry
            for key in ("caption", "brief", "summary", "main_reading"):
                text = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return None


def _checked_location(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a spec v1.2 location dict through the akousma builder."""
    if not value:
        return None
    label = value.get("label")
    return akousma.location(
        value.get("lat"),
        value.get("lon"),
        accuracy_m=value.get("accuracy_m"),
        altitude_m=value.get("altitude_m"),
        label=str(label).strip() or None if label is not None else None,
        source=value.get("source") or "gps",
        captured_at=value.get("captured_at"),
    )


def _checked_capture(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a spec v1.2 capture dict through the akousma builder."""
    if not value:
        return None
    return akousma.capture(
        value.get("direction"),
        seconds=value.get("seconds"),
        trigger=value.get("trigger"),
        armed_at=value.get("armed_at"),
        triggered_at=value.get("triggered_at"),
    )


def _checked_covenant(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a spec v1.3 covenant dict through the akousma builder. The
    block carries identity and honest absence — never the covenant's text."""
    if not value:
        return None
    return akousma.covenant(
        value.get("id"),
        name=value.get("name"),
        version=value.get("version"),
        contract=value.get("contract") or AKOUO_CONTRACT,
        sha256_hex=value.get("sha256"),
        extends=value.get("extends"),
        rules_applied=value.get("rules_applied"),
        withheld=value.get("withheld"),
        commitments=value.get("commitments"),
        note=value.get("note"),
    )


def _checked_listening_identity(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only content-free identity provenance in a shared akousma."""

    if not isinstance(value, dict):
        return None
    digest = str(value.get("sha256") or "").lower()
    declared = str(value.get("declared_sha256") or "").lower()
    block: dict[str, Any] = {
        "contract": LISTENING_IDENTITY_CONTRACT,
        "filename": LISTENING_IDENTITY_FILENAME,
        "active": value.get("active") is True,
        "sha256": digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None,
        "truncated": value.get("truncated") is True,
        "application": str(value.get("application") or "unknown")[:80],
        "applied_to": [
            str(item)[:120]
            for item in value.get("applied_to", [])[:32]
            if isinstance(item, str) and item
        ] if isinstance(value.get("applied_to"), list) else [],
        "content_included": False,
        "role": LISTENING_IDENTITY_ROLE,
    }
    if re.fullmatch(r"[0-9a-f]{64}", declared):
        block["declared_sha256"] = declared
    return block


def build_akousma_from_listen(
    *,
    audio: dict[str, Any],
    listening: dict[str, Any] | None = None,
    origin: str = "file",
    device: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    summary: str | None = None,
    location: dict[str, Any] | None = None,
    capture: dict[str, Any] | None = None,
    covenant: dict[str, Any] | None = None,
    listening_identity: dict[str, Any] | None = None,
    disagreements: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    auditum: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a valid akousma record from an oída listen result.

    ``audio`` needs at least ``asset_id`` (and ideally ``uri``/``content_hash``/duration).
    ``listening`` is namespaced per producer, e.g. ``{"oida.signal": {...}, "akouo.describe": {...}}``;
    entries are wrapped in the spec v1.1 envelope with akouo.* entries pinned to the
    ``akouo/v0.8`` contract. ``location`` (where it was heard — consent-scoped) and
    ``capture`` (past/future direction + window seconds) are spec v1.2 blocks.
    """
    origin = _normalize_origin(origin)
    enveloped = _envelope_listening(listening or {})
    identity_extension = _checked_listening_identity(listening_identity)
    extensions = {"oida.listening_identity": identity_extension} if identity_extension else None
    record = akousma.new_akousma(
        audio=audio,
        originating_app="oida",
        source_type=_origin_to_source_type(origin),
        origin=origin,
        listening=enveloped,
        operation="listen",
        tags=tags,
        session_id=session_id,
        summary=summary or _derive_summary(enveloped),
        location=_checked_location(location),
        capture=_checked_capture(capture),
        covenant=_checked_covenant(covenant),
        auditum=auditum or _auditum_from_listening(
            enveloped,
            covenant=covenant,
            disagreements=disagreements,
            actions=actions,
        ),
        extensions=extensions,
    )
    if device:
        record["provenance"]["device"] = device
    return record


def _maybe_link_recurrence(record: dict[str, Any], store: "akousma.AkousmataStore") -> None:
    """When the same audio content already lives in the store, link the new record
    to the most recent holder as ``same_source_as`` (spec v1.1 relations). Best-effort:
    registration must never fail because kinship lookup did."""
    try:
        content_hash = str(record.get("audio", {}).get("content_hash") or "")
        if not content_hash or not hasattr(store, "find_by_hash"):
            return
        matches = [r for r in store.find_by_hash(content_hash) if r.get("akousma_id") != record.get("akousma_id")]
        if not matches:
            return
        newest = matches[0]
        relations = record.setdefault("lineage", {}).setdefault("relations", [])
        if any(rel.get("target_akousma_id") == newest["akousma_id"] for rel in relations):
            return
        relations.append(
            akousma.relation("same_source_as", newest["akousma_id"], note="Same audio content hash already in the akousmata.")
        )
    except Exception:
        pass


def _maybe_enrich_songid(record: dict[str, Any], audio: dict[str, Any]) -> None:
    """Attach ``extensions.songid`` when ``OIDA_SONGID`` is enabled. Best-effort:
    the handoff must never fail because identification did."""
    uri = str(audio.get("uri") or "")
    path = uri[7:] if uri.startswith("file://") else uri
    if not path:
        return
    try:
        from .songid import enrich_akousma as _enrich
        from .songid import songid_enabled

        if songid_enabled() and os.path.exists(path):
            _enrich(record, path)
    except Exception:
        pass


def persist_akousma(
    record: dict[str, Any],
    *,
    store: "akousma.AkousmataStore | None" = None,
) -> str:
    """Persist ``record`` to the shared akousmata store (with recurrence
    linking) and return its id. Used by the germ handoff and the remote ear."""
    owns_store = store is None
    store = store or akousma.AkousmataStore()
    try:
        _maybe_link_recurrence(record, store)
        return store.put(record)
    finally:
        if owns_store:
            store.close()


def handoff_to_germ(
    record: dict[str, Any],
    mode: str,
    *,
    store: "akousma.AkousmataStore | None" = None,
) -> dict[str, Any]:
    """Persist ``record`` to the shared akousmata store and return the germ deep link.

    Returns ``{"akousma_id", "mode", "germ_url"}``. Backing the three oída buttons.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    akousma_id = persist_akousma(record, store=store)
    return {"akousma_id": akousma_id, "mode": mode, "germ_url": germ_deep_link(akousma_id, mode)}


def build_germ_router():
    """FastAPI router backing the three oída→germ buttons. Imported lazily so this
    module stays usable without FastAPI (e.g. in cross-app tests)."""
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, ConfigDict

    # Must be a module global: with `from __future__ import annotations` FastAPI
    # resolves the route's string annotation through this module's globals, and a
    # function-local model silently degrades the JSON body into a query param.
    global GermHandoffRequest

    class GermHandoffRequest(BaseModel):
        model_config = ConfigDict(allow_inf_nan=False, extra="forbid", strict=True)

        mode: Literal["sound", "prompt", "lineage"]
        audio: dict[str, Any]
        listening: dict[str, Any] | None = None
        origin: str = "file"
        device: str | None = None
        session_id: str | None = None
        tags: list[str] | None = None
        summary: str | None = None
        location: dict[str, Any] | None = None
        capture: dict[str, Any] | None = None
        covenant: dict[str, Any] | None = None
        listening_identity: dict[str, Any] | None = None

    router = APIRouter(prefix="/germ", tags=["germ"])

    @router.post("/handoff")
    def germ_handoff(req: GermHandoffRequest) -> dict[str, Any]:
        try:
            record = build_akousma_from_listen(
                audio=req.audio,
                listening=req.listening,
                origin=req.origin,
                device=req.device,
                session_id=req.session_id,
                tags=req.tags,
                summary=req.summary,
                location=req.location,
                capture=req.capture,
                covenant=req.covenant,
                listening_identity=req.listening_identity,
            )
            _maybe_enrich_songid(record, req.audio)
            return handoff_to_germ(record, req.mode)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/link")
    def germ_link(akousma_id: str, mode: str = "lineage") -> dict[str, Any]:
        try:
            return {
                "akousma_id": akousma_id,
                "mode": mode,
                "germ_url": germ_deep_link(akousma_id, mode),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
