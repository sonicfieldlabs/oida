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


def build_akousma_from_listen(
    *,
    audio: dict[str, Any],
    listening: dict[str, Any] | None = None,
    origin: str = "file",
    device: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build a valid akousma record from an oída listen result.

    ``audio`` needs at least ``asset_id`` (and ideally ``uri``/``content_hash``/duration).
    ``listening`` is namespaced per producer, e.g. ``{"oida.signal": {...}, "akouo.describe": {...}}``.
    """
    origin = _normalize_origin(origin)
    record = akousma.new_akousma(
        audio=audio,
        originating_app="oida",
        source_type=_origin_to_source_type(origin),
        origin=origin,
        listening=listening or {},
        operation="listen",
        tags=tags,
        session_id=session_id,
    )
    if device:
        record["provenance"]["device"] = device
    return record


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
    owns_store = store is None
    store = store or akousma.AkousmataStore()
    try:
        akousma_id = store.put(record)
    finally:
        if owns_store:
            store.close()
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
