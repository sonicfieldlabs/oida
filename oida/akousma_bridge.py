"""oída → germ bridge over the shared akousma protocol.

oída is generative ears; germ is generative voice. After a listen, oída persists an
**akousma** (the sound's memory record) into the shared **akousmata** store and hands germ
an ``akousma_id`` via a deep link. The three UI buttons map to three modes:

- ``sound``   — "open as sound":    load the listened fragment as an audio source in germ.
- ``prompt``  — "open as prompt":   open the listening result as a generation prompt in germ.
- ``lineage`` — "explore lineage":  open germ's genetic-ancestry explorer on this akousma.

Requires the ``akousma`` package (earworm/packages/py-akousma).
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlencode

import akousma

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


AKOUO_CONTRACT = "akouo/v0.6"


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
        if isinstance(value, dict):
            for key in ("summary", "caption", "brief", "main_reading"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    entry["summary"] = text.strip()
                    break
        wrapped[namespace] = entry
    return wrapped


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
) -> dict[str, Any]:
    """Build a valid akousma record from an oída listen result.

    ``audio`` needs at least ``asset_id`` (and ideally ``uri``/``content_hash``/duration).
    ``listening`` is namespaced per producer, e.g. ``{"oida.signal": {...}, "akouo.describe": {...}}``;
    entries are wrapped in the spec v1.1 envelope with akouo.* entries pinned to the
    ``akouo/v0.6`` contract. ``location`` (where it was heard — consent-scoped) and
    ``capture`` (past/future direction + window seconds) are spec v1.2 blocks.
    """
    origin = _normalize_origin(origin)
    enveloped = _envelope_listening(listening or {})
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
    from pydantic import BaseModel

    # Must be a module global: with `from __future__ import annotations` FastAPI
    # resolves the route's string annotation through this module's globals, and a
    # function-local model silently degrades the JSON body into a query param.
    global GermHandoffRequest

    class GermHandoffRequest(BaseModel):
        mode: str
        audio: dict[str, Any]
        listening: dict[str, Any] | None = None
        origin: str = "file"
        device: str | None = None
        session_id: str | None = None
        tags: list[str] | None = None
        summary: str | None = None
        location: dict[str, Any] | None = None
        capture: dict[str, Any] | None = None

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
            )
            _maybe_enrich_songid(record, req.audio)
            return handoff_to_germ(record, req.mode)
        except ValueError as exc:
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
