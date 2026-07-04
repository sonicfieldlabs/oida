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
