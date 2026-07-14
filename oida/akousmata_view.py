"""The akousmata history view inside oída.

The shared store has its own app (the akousmata listening navigator,
github.com/sonicfieldlabs/akousmata); oída embeds the same library view —
list, filter, detail with lineage and kinship, audio playback — natively in
its dashboard instead of launching the external app. Card shapes stay
compatible with the navigator's. Oída normally writes memories through its
listen flow and the germ bridge; the embedded list also supports the small
rename/forget operations exposed by its contextual menus.

Lazy on the ``akousma`` package like the germ bridge: oída boots without it
and these routes degrade to 503.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _akousma():
    try:
        import akousma
    except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "the 'akousma' package is not installed; "
            "pip install -e <SFL>/earworm/packages/py-akousma"
        ) from exc
    return akousma


def summary_line(record: dict[str, Any]) -> str:
    summary = record.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    for entry in (record.get("listening") or {}).values():
        if isinstance(entry, dict):
            text = entry.get("summary")
            if isinstance(text, str) and text.strip():
                return text.strip()
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else entry
            for key in ("caption", "summary", "brief", "main_reading", "notes"):
                value = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(value, str) and value.strip():
                    return value.strip()
    prompt = (record.get("lineage") or {}).get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return ", ".join(str(t) for t in record.get("tags") or []) or "(no summary)"


def card(record: dict[str, Any]) -> dict[str, Any]:
    provenance = record.get("provenance") or {}
    audio = record.get("audio") or {}
    lineage = record.get("lineage") or {}
    oida_listen = (record.get("listening") or {}).get("oida.listen") or {}
    payload = oida_listen.get("payload") if isinstance(oida_listen, dict) and isinstance(oida_listen.get("payload"), dict) else oida_listen
    event_id = payload.get("event_id") if isinstance(payload, dict) else None
    return {
        "akousma_id": record["akousma_id"],
        "created_at": record.get("created_at"),
        "summary": summary_line(record),
        "tags": list(record.get("tags") or []),
        "originating_app": provenance.get("originating_app"),
        "origin": provenance.get("origin"),
        "source_type": provenance.get("source_type"),
        "duration_seconds": audio.get("duration_seconds"),
        "has_audio": bool(audio.get("uri")),
        "parent_count": len(lineage.get("parent_akousma_ids") or []),
        "relation_count": len(lineage.get("relations") or []),
        "session_id": record.get("session_id"),
        "event_id": event_id,
    }


def _resolve_audio(store, record: dict[str, Any]) -> Path | None:
    uri = str((record.get("audio") or {}).get("uri") or "")
    if uri.startswith("akousmata://"):
        path = store.resolve_uri(uri)
        return path if path is not None and path.exists() else None
    if uri.startswith("file://"):
        path = Path(uri[7:])
        return path if path.exists() else None
    if uri and Path(uri).expanduser().exists():
        return Path(uri).expanduser()
    return None


def _ref(store, akousma_id: str) -> dict[str, Any]:
    record = store.get(akousma_id)
    if record is None:
        return {"akousma_id": akousma_id, "summary": "(missing record)", "missing": True}
    return {"akousma_id": akousma_id, "summary": summary_line(record), "missing": False}


def build_akousmata_router():
    from fastapi import APIRouter, Body, HTTPException
    from fastapi.responses import FileResponse

    akousma = _akousma()
    router = APIRouter(prefix="/akousmata", tags=["akousmata"])

    def _store():
        return akousma.AkousmataStore()

    @router.get("/records")
    def list_records(
        app: str | None = None,
        origin: str | None = None,
        tag: str | None = None,
        text: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        store = _store()
        try:
            try:
                found = store.query(originating_app=app, origin=origin, tag=tag, text=text, limit=max(1, min(limit, 500)))
            except TypeError:  # pre-v0.2 store without tag/text filters
                found = store.query(originating_app=app, origin=origin, limit=max(1, min(limit, 500)))
            return {"records": [card(r) for r in found]}
        finally:
            store.close()

    @router.get("/tags")
    def tags() -> dict[str, Any]:
        store = _store()
        try:
            return {"tags": store.tags() if hasattr(store, "tags") else []}
        finally:
            store.close()

    @router.get("/records/{akousma_id}")
    def detail(akousma_id: str) -> dict[str, Any]:
        store = _store()
        try:
            record = store.get(akousma_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"akousma not found: {akousma_id}")
            related = store.related(akousma_id) if hasattr(store, "related") else []
            return {
                "record": record,
                "summary": summary_line(record),
                "parents": [_ref(store, pid) for pid in store.parents(akousma_id)],
                "children": [_ref(store, cid) for cid in store.children(akousma_id)],
                "related": [
                    {**link, "summary": _ref(store, link.get("akousma_id", ""))["summary"]}
                    for link in related
                ],
                "audio_available": _resolve_audio(store, record) is not None,
            }
        finally:
            store.close()

    @router.patch("/records/{akousma_id}")
    def rename(akousma_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        summary = str(body.get("summary") or body.get("title") or "").strip()
        if not summary:
            raise HTTPException(status_code=400, detail="memory name cannot be empty")
        if len(summary) > 160:
            raise HTTPException(status_code=400, detail="memory name must be 160 characters or fewer")
        store = _store()
        try:
            record = store.get(akousma_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"akousma not found: {akousma_id}")
            record["summary"] = summary
            store.put(record)
            return {"record": card(record), "renamed": True}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            store.close()

    @router.delete("/records/{akousma_id}")
    def forget(akousma_id: str) -> dict[str, Any]:
        store = _store()
        try:
            if not store.forget(akousma_id, delete_audio=False):
                raise HTTPException(status_code=404, detail=f"akousma not found: {akousma_id}")
            return {"akousma_id": akousma_id, "forgotten": True, "audio_deleted": False}
        finally:
            store.close()

    @router.get("/audio/{akousma_id}")
    def audio(akousma_id: str):
        store = _store()
        try:
            record = store.get(akousma_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"akousma not found: {akousma_id}")
            path = _resolve_audio(store, record)
            if path is None:
                raise HTTPException(status_code=404, detail="no resolvable audio for this memory")
            return FileResponse(path)
        finally:
            store.close()

    return router
