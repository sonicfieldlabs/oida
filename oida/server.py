from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import secrets
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

try:
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without deps
    FastAPI = None  # type: ignore[assignment]
    HTTPException = RuntimeError  # type: ignore[assignment]

    def Form(default=None, **_):  # type: ignore[no-redef]
        return default

    BaseModel = object  # type: ignore[assignment,misc]

    def Field(default=None, **_):  # type: ignore[no-redef]
        return default

    FASTAPI_IMPORT_ERROR = exc
else:
    FASTAPI_IMPORT_ERROR = None

from oida.config import REPO_ROOT, load_config, uploads_dir
from oida.acoustic_system import acoustic_system_manifest
from oida.akouo_skills import akouo_manifest, route_preset
from oida.background import BackgroundRuntime
from oida.conversation import ConversationStore
from oida.contracts import AudioSegment, AudioSourceDescriptor, PrivacyMode, RawAudioPolicy, SourceType, audio_segment_from_path, source_for_path
from oida.engine import build_engine
from oida.generation import GenerationStore
from oida.listening import listening_event_dict
from oida.live import LiveManager
from oida.memory import AkousmataStore
from oida.metrics import process_metrics
from oida.native_temp_audio import (
    apply_native_temp_audio_retention_after_analysis,
    cleanup_native_system_audio_temp_files,
    finalize_native_temp_audio_session,
    native_system_audio_temp_status,
)
from oida.raw_audio import (
    cleanup_upload_audio_files,
    finalize_upload_audio_session,
    upload_audio_status,
)
from oida.reporting import caption, direct_analysis, events, forbidden_topics_for_text, music, qa, report, report_to_dict, speech, think, transcribe
from oida.reportschema import dump_model
from oida.route_comparison import compare_route_events
from oida.sonicfield import SonicFieldBridge, terms_from_event
from oida.source_routes import native_system_audio_route_manifest, normalize_system_audio_source_route, system_audio_source_label
from oida.sources import source_registry_dict
from oida.system_audio import system_audio_status_dict
from harness.akouo.command import build_harness_output
from harness.akouo.loader import AkouoLoader
from harness.akouo.routing import available_harness_controls, evidence_level_for_path, routing_plan

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB cap on a single upload/ingest body
_LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
LOGGER = logging.getLogger(__name__)


class _UploadTooLargeError(Exception):
    pass


def _chunk_overlap(config) -> float:
    return 15.0 if config.moss_chunk_seconds >= 300 else 5.0


class EventBroadcaster:
    """Minimal SSE fan-out so every surface (dashboard, mac app, floating
    listener) mirrors one daemon state without polling storms. Sync endpoint
    handlers run in worker threads, so publish() hops onto the event loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        message = {"type": event_type, "at": datetime.now(timezone.utc).isoformat()}
        if payload is not None:
            message["data"] = payload
        try:
            data = f"data: {json.dumps(message, default=str)}\n\n"
        except (TypeError, ValueError):
            return
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                loop.call_soon_threadsafe(self._offer, client, data)
            except RuntimeError:
                return

    @staticmethod
    def _offer(client: asyncio.Queue, data: str) -> None:
        try:
            client.put_nowait(data)
        except asyncio.QueueFull:
            pass

    async def stream(self):
        client: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._clients.add(client)
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(client.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield item
        finally:
            with self._lock:
                self._clients.discard(client)


def _hostname_only(value: str) -> str:
    candidate = value.strip().lower()
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    candidate = candidate.split("/", 1)[0]
    if candidate.startswith("[") and "]" in candidate:  # IPv6 literal, e.g. [::1]:8765
        return candidate[1 : candidate.index("]")]
    if candidate.count(":") == 1:  # host:port
        candidate = candidate.split(":", 1)[0]
    return candidate


class PathRequest(BaseModel):  # type: ignore[misc,valid-type]
    path: str


class TranscribeRequest(PathRequest):
    timestamps: str = "sentence"


class CaptionRequest(PathRequest):
    detail: str = "dense"


class QaRequest(PathRequest):
    question: str
    thinking_budget: int | None = None
    context: str | None = None


class ConversationAskRequest(BaseModel):  # type: ignore[misc,valid-type]
    question: str
    event: dict[str, object] | None = None
    conversation_id: str | None = None
    include_memory: bool = True
    allow_remote_model: bool = False
    provider: str = "local_structured"


class GenerationPromptRequest(BaseModel):  # type: ignore[misc,valid-type]
    event: dict[str, object] | None = None
    intent: str = "transform"
    prompt: str | None = None
    negative_prompt: str | None = None
    adapter: str = "prompt_only"
    duration_s: float | None = None
    generate: bool = False


class GenerationRelistenRequest(BaseModel):  # type: ignore[misc,valid-type]
    generation_id: str
    path: str
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str = "session"
    remember: bool = False


class ThinkRequest(PathRequest):
    instruction: str
    thinking_budget: int | None = None


class ReportRequest(PathRequest):
    profile: str = "default"


class ListenEventRequest(PathRequest):
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str = "session"
    source_type: str = "file"
    source_label: str | None = None
    device_id: str | None = None
    raw_audio_policy: str | None = None


class ListenEventRerunRequest(BaseModel):  # type: ignore[misc,valid-type]
    event: dict[str, object] | None = None
    path: str | None = None
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str | None = None
    raw_audio_policy: str | None = None
    remember: bool = False
    comparison_signal_fields: list[str] | None = None
    comparison_min_abs_signal_delta: float | None = None
    comparison_changed_only: bool = False


class MossAnalysisRequest(PathRequest):
    mode: str = "environment"
    thinking_budget: int | None = None


class AkouoHarnessRequest(PathRequest):
    command: str = "/listen"
    mode: str | None = None
    question: str | None = None
    validate_output: bool = Field(False, alias="validate")


class LiveStartRequest(BaseModel):  # type: ignore[misc,valid-type]
    ring_seconds: float = 60.0
    vad_threshold_dbfs: float = -45.0
    source_type: str = "live_input"
    source_label: str | None = None
    device_id: str | None = None


class LiveSessionRequest(BaseModel):  # type: ignore[misc,valid-type]
    session_id: str


class LiveCaptureRequest(BaseModel):  # type: ignore[misc,valid-type]
    session_id: str
    seconds: float = 10.0
    analyze: bool = False
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None


class MemoryRememberRequest(BaseModel):  # type: ignore[misc,valid-type]
    event: dict[str, object]
    user_notes: str | None = None
    tags: list[str] = Field(default_factory=list)


class MemoryForgetRequest(BaseModel):  # type: ignore[misc,valid-type]
    trace_id: str


class MemorySimilarRequest(BaseModel):  # type: ignore[misc,valid-type]
    event: dict[str, object] | None = None
    trace_id: str | None = None
    limit: int = 5


class BackgroundConfigRequest(BaseModel):  # type: ignore[misc,valid-type]
    updates: dict[str, object] = Field(default_factory=dict)


class BackgroundHistoryPinRequest(BaseModel):  # type: ignore[misc,valid-type]
    event_id: str
    pinned: bool = True


class BackgroundHistoryBatchPinRequest(BaseModel):  # type: ignore[misc,valid-type]
    event_ids: list[str] = Field(default_factory=list)
    pinned: bool = True


class BackgroundHistoryClearRequest(BaseModel):  # type: ignore[misc,valid-type]
    keep_pinned: bool = True


class BackgroundHistoryArchiveRequest(BaseModel):  # type: ignore[misc,valid-type]
    event_ids: list[str] = Field(default_factory=list)
    label: str | None = None


class BackgroundCaptureRequest(BaseModel):  # type: ignore[misc,valid-type]
    session_id: str | None = None
    seconds: float | None = None
    route_preset: str | None = None
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    remember: bool = False


class NativeSystemAudioAnalyzeRequest(PathRequest):
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str = "ephemeral"
    source_label: str = "Native system audio"
    duration_s: float | None = None
    remember: bool = False
    source_route: dict[str, object] | None = None


class NativeSystemAudioCleanupRequest(BaseModel):  # type: ignore[misc,valid-type]
    delete_all: bool = False
    dry_run: bool = False
    max_age_hours: float | None = None
    max_files: int | None = None


class RawAudioWipeRequest(BaseModel):  # type: ignore[misc,valid-type]
    delete_all: bool = True
    dry_run: bool = False
    max_age_hours: float | None = None
    max_files: int | None = None
    include_legacy: bool = False


class SonicFieldExploreRequest(BaseModel):  # type: ignore[misc,valid-type]
    event: dict[str, object] | None = None
    query: str | None = None
    limit_per_surface: int = 5


class CaptureRequestBody(BaseModel):  # type: ignore[misc,valid-type]
    seconds: float | None = None
    route_preset: str | None = None


class CaptureRequestClaimBody(BaseModel):  # type: ignore[misc,valid-type]
    id: str | None = None


class EngineModelBody(BaseModel):  # type: ignore[misc,valid-type]
    model_kind: str = "instruct"
    model: str


class SonicFieldRevealRequest(BaseModel):  # type: ignore[misc,valid-type]
    path: str


def scan_moss_models(weights_dir: Path) -> list[dict[str, object]]:
    """List locally available MOSS checkpoints (weights/<name> with a config.json)."""
    models: list[dict[str, object]] = []
    if not weights_dir.exists():
        return models
    for candidate in sorted(weights_dir.iterdir()):
        if not candidate.is_dir() or not (candidate / "config.json").exists():
            continue
        size_bytes = 0
        for file in candidate.glob("*.safetensors"):
            try:
                size_bytes += file.stat().st_size
            except OSError:
                continue
        kind_hint = "thinking" if "thinking" in candidate.name.lower() else "instruct"
        description = (
            "Reasoning listener: slower, thinks before answering. Best for QA, music analysis, and deep routes."
            if kind_hint == "thinking"
            else "Perception listener: fast captions, transcripts, and event timelines. The default ear."
        )
        models.append(
            {
                "name": candidate.name,
                "path": str(candidate),
                "size_gb": round(size_bytes / 1_073_741_824, 2) if size_bytes else None,
                "kind_hint": kind_hint,
                "description": description,
            }
        )
    return models


def create_app(profile: str | None = None, host: str | None = None, port: int | None = None) -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI dependencies are not installed; run `uv sync` first") from FASTAPI_IMPORT_ERROR

    config = load_config(profile=profile, host=host, port=port)
    engine = build_engine(config)
    live = LiveManager()
    memory = AkousmataStore()
    background = BackgroundRuntime()
    conversations = ConversationStore()
    generations = GenerationStore()
    broadcaster = EventBroadcaster()
    sonicfield = SonicFieldBridge(config.sonicfield_root)
    try:
        uploads_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        LOGGER.warning("could not create audio dir %s", uploads_dir())

    engine_monitor: dict[str, Any] = {
        "state": "stub" if config.profile == "stub" else ("remote" if config.profile == "cuda-server" else "cold"),
        "detail": "stub profile produces no model perception; DSP still listens" if config.profile == "stub" else None,
        "warmed_ms": None,
    }
    engine_monitor_lock = threading.RLock()
    weights_root = REPO_ROOT / "weights"
    available_models = scan_moss_models(weights_root)

    def engine_status() -> dict[str, Any]:
        runtime = engine.runtime_status()
        with engine_monitor_lock:
            if config.profile == "mac-mps" and runtime.get("loaded_models"):
                engine_monitor["state"] = "ready"
            assignments = runtime.get("assignments") or {}
            return {
                "profile": config.profile,
                "state": engine_monitor["state"],
                "detail": engine_monitor["detail"],
                "warmed_ms": engine_monitor["warmed_ms"],
                "loaded_models": runtime.get("loaded_models", []),
                "device": runtime.get("device"),
                "prewarm": config.prewarm,
                "chunk_seconds": config.moss_chunk_seconds,
                "instruct_model": assignments.get("instruct") or (Path(config.instruct_model).name if config.instruct_model else None),
                "thinking_model": assignments.get("thinking") or (Path(config.thinking_model).name if config.thinking_model else None),
                "available_models": available_models,
            }

    def _prewarm_engine(model_kind: str = "instruct") -> None:
        with engine_monitor_lock:
            engine_monitor["state"] = "warming"
            engine_monitor["detail"] = None
        broadcaster.publish("engine", engine_status())
        started = time.perf_counter()
        try:
            engine.prewarm(model_kind)
            with engine_monitor_lock:
                engine_monitor["state"] = "ready"
                engine_monitor["warmed_ms"] = round((time.perf_counter() - started) * 1000)
        except Exception as exc:
            with engine_monitor_lock:
                engine_monitor["state"] = "degraded"
                engine_monitor["detail"] = str(exc)
        broadcaster.publish("engine", engine_status())

    def start_prewarm(model_kind: str = "instruct") -> bool:
        with engine_monitor_lock:
            if config.profile != "mac-mps" or engine_monitor["state"] == "warming":
                return False
            # Claim the warming state atomically: sync FastAPI handlers may run
            # on different worker threads.
            engine_monitor["state"] = "warming"
        threading.Thread(target=_prewarm_engine, args=(model_kind,), name="oida-moss-prewarm", daemon=True).start()
        return True

    @asynccontextmanager
    async def lifespan(_app: Any):
        broadcaster.bind_loop(asyncio.get_running_loop())
        if config.prewarm:
            start_prewarm()
        yield
        # Honor the default delete_after_session native-temp retention policy on shutdown.
        try:
            finalize_native_temp_audio_session(background.config.native_temp_audio_retention)
        except Exception as exc:
            LOGGER.warning("native temp-audio shutdown cleanup failed: %s", exc)
        try:
            finalize_upload_audio_session(background.config.upload_audio_retention)
        except Exception as exc:
            LOGGER.warning("upload-audio shutdown cleanup failed: %s", exc)

    app = FastAPI(title="oida", version="0.1.0", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # oída→germ bridge (three buttons) over the shared akousma store; optional.
    try:
        from .akousma_bridge import build_germ_router

        app.include_router(build_germ_router())
    except ImportError:
        pass  # akousma package not installed; oída still boots without the bridge

    # Shared-store history view (the akousmata library, embedded); optional.
    try:
        from .akousmata_view import build_akousmata_router

        app.include_router(build_akousmata_router())
    except (ImportError, RuntimeError):
        pass  # akousma package not installed; oída still boots without the view

    wildcard_bind = str(config.host) in {"0.0.0.0", "::", ""}
    if wildcard_bind and not config.auth_token:
        raise RuntimeError(
            "Refusing to bind oida on a wildcard host without OIDA_AUTH_TOKEN (or legacy HMM_/AEAR_AUTH_TOKEN). "
            "Use 127.0.0.1 for tokenless local operation."
        )
    if wildcard_bind:
        LOGGER.warning("oida is bound to %s; bearer-token auth is required and loopback Host protection is relaxed.", config.host)
    allowed_hostnames = set(_LOOPBACK_HOSTNAMES)
    if config.host:
        allowed_hostnames.add(str(config.host).strip().lower())

    @app.middleware("http")
    async def _loopback_guard(request: Request, call_next: Any) -> Any:
        if config.auth_token:
            auth_header = request.headers.get("authorization", "")
            scheme, _, token = auth_header.partition(" ")
            # Compare as bytes: str compare_digest raises TypeError on non-ASCII
            # input, which would turn a malformed header into a 500 instead of 401.
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                token.encode("utf-8", "surrogateescape"), config.auth_token.encode("utf-8", "surrogateescape")
            ):
                return JSONResponse(status_code=401, content={"detail": "valid bearer token required"})
        # In localhost mode, refuse non-loopback Host headers (DNS-rebinding) and
        # cross-origin browser requests (CSRF). Wildcard/LAN mode is allowed only
        # when bearer-token auth is configured above.
        if not wildcard_bind:
            host_header = request.headers.get("host", "")
            if host_header and _hostname_only(host_header) not in allowed_hostnames:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "oida is local-first; requests with a non-loopback Host header are refused."},
                )
            origin = request.headers.get("origin")
            if origin and (origin == "null" or _hostname_only(origin) not in allowed_hostnames):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "cross-origin requests are refused; the oida daemon serves only its own dashboard."},
                )
        return await call_next(request)

    def analyze_capture(
        capture: dict[str, object],
        preset_id: str,
        privacy_mode: str = "ephemeral",
        enabled_skill_ids: list[str] | None = None,
        disabled_skill_ids: list[str] | None = None,
    ) -> dict[str, object]:
        preset = route_preset(preset_id)
        path = str(capture["path"])
        broadcaster.publish("listen_started", {"path": path, "route_preset": preset.id, "source": "live-capture"})
        perception = report(engine, path, "oida-live-capture", passes=preset.moss_passes, chunk_seconds=config.moss_chunk_seconds, overlap_seconds=_chunk_overlap(config))
        perception_dict = report_to_dict(perception)
        command_output = build_harness_output(perception_dict, command=preset.akouo_command)
        event = listening_event_dict(
            perception_dict,
            command_output=command_output,
            segment=capture["segment"],
            route_preset_id=preset.id,
            enabled_skill_ids=enabled_skill_ids,
            disabled_skill_ids=disabled_skill_ids,
            privacy_mode=_privacy_mode(privacy_mode),
            raw_audio_policy="temp",
        )
        event = memory.enrich_event(event)
        broadcaster.publish("listen_completed", {"listening_event": event, "route_preset": preset.id})
        return {
            **capture,
            "listening_event": event,
            "perception_report": perception_dict,
            "command_output": command_output,
        }

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "name": "oida",
            "legacy_name": "hmm, aear",
            "profile": config.profile,
            "host": config.host,
            "port": config.port,
            "data_dir": str(config.data_dir),
            "audio_dir": str(config.audio_dir),
            "auth_required": bool(config.auth_token),
            "allow_hf_hub": config.allow_hf_hub,
            "hf_hub_offline": config.hf_hub_offline,
            "engine": engine_status(),
            "sonicfield": {"available": sonicfield.available, "root": str(sonicfield.root) if sonicfield.root else None},
        }

    @app.get("/engine/status")
    def engine_status_endpoint() -> dict[str, object]:
        return engine_status()

    @app.post("/engine/warm")
    def engine_warm_endpoint() -> dict[str, object]:
        started = start_prewarm()
        return {"started": started, **engine_status()}

    @app.post("/engine/model")
    def engine_model_endpoint(req: EngineModelBody) -> dict[str, object]:
        kind = req.model_kind.strip().lower()
        if kind not in {"instruct", "thinking"}:
            raise HTTPException(status_code=400, detail="model_kind must be instruct or thinking")
        selected = next((item for item in available_models if item["name"] == req.model or item["path"] == req.model), None)
        if selected is None:
            valid = ", ".join(str(item["name"]) for item in available_models) or "none found in weights/"
            raise HTTPException(status_code=400, detail=f"unknown MOSS model: {req.model}. Available: {valid}")
        try:
            engine.set_model(kind, str(selected["path"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        started = start_prewarm(kind)
        status = engine_status()
        broadcaster.publish("engine", status)
        return {"assigned": {kind: selected["name"]}, "warming": started, **status}

    @app.get("/events/stream")
    async def events_stream_endpoint() -> Any:
        return StreamingResponse(
            broadcaster.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/sonicfield/status")
    def sonicfield_status_endpoint() -> dict[str, object]:
        return sonicfield.status()

    @app.post("/sonicfield/explore")
    def sonicfield_explore_endpoint(req: SonicFieldExploreRequest) -> dict[str, object]:
        try:
            terms = terms_from_event(req.event, extra_query=req.query)
            if not terms:
                raise ValueError("provide a listening event or a query to explore the Sonic Field")
            limit = max(1, min(int(req.limit_per_surface), 12))
            result = sonicfield.explore(terms, limit_per_surface=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**result, "status": sonicfield.status()}

    @app.post("/sonicfield/reveal")
    def sonicfield_reveal_endpoint(req: SonicFieldRevealRequest) -> dict[str, object]:
        if not sonicfield.available or sonicfield.root is None:
            raise HTTPException(status_code=400, detail="Sonic Field root is not available")
        target = Path(req.path).expanduser().resolve()
        # startswith(str) would accept sibling dirs like ".../sonicfield-evil"
        if not target.is_relative_to(sonicfield.root):
            raise HTTPException(status_code=400, detail="path is outside the Sonic Field root")
        if not target.exists():
            raise HTTPException(status_code=404, detail="path does not exist")
        try:
            subprocess.run(["open", "-R", str(target)], check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=500, detail=f"could not reveal path: {exc}") from exc
        return {"revealed": str(target)}

    @app.get("/")
    def root() -> FileResponse:
        # no-cache: without it browsers reuse a heuristically-cached dashboard
        # after an upgrade (assets are ?v= versioned, the document is not)
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api")
    def api_root() -> dict[str, object]:
        return {
            "name": "oida",
            "legacy_name": "hmm, aear",
            "ok": True,
            "profile": config.profile,
            "docs": "/docs",
            "health": "/health",
            "endpoints": [
                "/oida/status",
                "/engine/status",
                "/engine/warm",
                "/engine/model",
                "/events/stream",
                "/sonicfield/status",
                "/sonicfield/explore",
                "/sonicfield/reveal",
                "/upload",
                "/sample-tone",
                "/transcribe",
                "/events",
                "/caption",
                "/speech",
                "/music",
                "/moss-analysis",
                "/acoustic-system",
                "/sources",
                "/system-audio/status",
                "/background/status",
                "/background/history",
                "/background/history/export",
                "/background/history/pin",
                "/background/history/batch-pin",
                "/background/history/archive",
                "/background/history/clear",
                "/background/config",
                "/background/pause",
                "/background/resume",
                "/background/capture",
                "/background/capture-request",
                "/background/capture-request/claim",
                "/background/capture-request/cancel",
                "/native/system-audio/analyze",
                "/native/system-audio/routes",
                "/native/system-audio/temp",
                "/native/system-audio/cleanup",
                "/raw-audio/status",
                "/raw-audio/wipe",
                "/listen-event",
                "/listen-event/rerun",
                "/akouo/modes",
                "/akouo/skills",
                "/akouo/presets",
                "/akouo/schema",
                "/akouo/route",
                "/akouo/listen",
                "/memory",
                "/memory/export",
                "/memory/trace/{trace_id}",
                "/memory/remember",
                "/memory/forget",
                "/memory/similar",
                "/conversation/ask",
                "/generation/prompt",
                "/generation/history",
                "/generation/{generation_id}",
                "/generation/relisten",
                "/metrics/process",
                "/live/start",
                "/live/ingest",
                "/live/capture",
                "/live/signal/{session_id}",
                "/live/status",
                "/live/stop",
                "/germ/handoff",
                "/germ/link",
                "/qa",
                "/think",
                "/report",
            ],
        }

    @app.get("/oida/status")
    def oida_status_endpoint() -> dict[str, object]:
        return {
            "name": "oida",
            "description": "Local open listening agent built from the oida daemon and AKOUO harness.",
            "profile": config.profile,
            "data_dir": str(config.data_dir),
            "sources": source_registry_dict(),
            "system_audio": system_audio_status_dict(),
            "background": background.status(),
            "akouo": akouo_manifest(),
            "raw_audio": upload_audio_status(background.config.upload_audio_retention),
            "model_policy": {
                "allow_hf_hub": config.allow_hf_hub,
                "hf_hub_offline": config.hf_hub_offline,
                "instruct_model": config.instruct_model,
                "thinking_model": config.thinking_model,
                "note": "Hub model IDs are refused unless OIDA_ALLOW_HF_HUB (legacy HMM_/AEAR_ALLOW_HF_HUB) is set and HF_HUB_OFFLINE is not enabled.",
            },
            "privacy_defaults": {
                "raw_audio_policy": "external_ref_for_files_temp_for_live_captures",
                "memory_save_by_default": False,
                "cloud_models_enabled": False,
                "conversation_remote_models_enabled": False,
                "generation_adapter_enabled": False,
                "generation_default_adapter": "prompt_only",
            },
        }

    @app.get("/sources")
    def sources_endpoint() -> dict[str, object]:
        return source_registry_dict()

    @app.get("/system-audio/status")
    def system_audio_status_endpoint() -> dict[str, object]:
        return system_audio_status_dict()

    @app.get("/background/status")
    def background_status_endpoint() -> dict[str, object]:
        return background.status()

    @app.post("/background/capture-request")
    def background_capture_request_endpoint(req: CaptureRequestBody) -> dict[str, object]:
        request = background.request_capture(seconds=req.seconds, route_preset=req.route_preset)
        broadcaster.publish("capture_requested", request)
        return {"capture_request": request, "background": background.status()}

    @app.post("/background/capture-request/claim")
    def background_capture_request_claim_endpoint(req: CaptureRequestClaimBody) -> dict[str, object]:
        request = background.claim_capture_request(req.id)
        if request:
            broadcaster.publish("capture_claimed", request)
        return {"capture_request": request, "claimed": bool(request)}

    @app.post("/background/capture-request/cancel")
    def background_capture_request_cancel_endpoint(req: CaptureRequestClaimBody) -> dict[str, object]:
        request = background.cancel_capture_request(req.id)
        if request:
            broadcaster.publish("capture_cancelled", request)
        return {"cancelled": bool(request), "capture_request": request}

    @app.get("/background/history")
    def background_history_endpoint(
        route: str | None = None,
        source_type: str | None = None,
        raw_audio_policy: str | None = None,
        privacy_mode: str | None = None,
        q: str | None = None,
        rerunnable: bool | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        return background.filtered_history(
            route=route,
            source_type=source_type,
            raw_audio_policy=raw_audio_policy,
            privacy_mode=privacy_mode,
            q=q,
            rerunnable=rerunnable,
            limit=limit,
        )

    @app.get("/background/history/export")
    def background_history_export_endpoint() -> dict[str, object]:
        return background.export_history()

    @app.post("/background/history/pin")
    def background_history_pin_endpoint(req: BackgroundHistoryPinRequest) -> dict[str, object]:
        try:
            return background.set_pinned_event(req.event_id, pinned=req.pinned)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"recent event is not available: {req.event_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/background/history/batch-pin")
    def background_history_batch_pin_endpoint(req: BackgroundHistoryBatchPinRequest) -> dict[str, object]:
        try:
            return background.set_pinned_events(req.event_ids, pinned=req.pinned)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/background/history/archive")
    def background_history_archive_endpoint(req: BackgroundHistoryArchiveRequest) -> dict[str, object]:
        return background.archive_history(event_ids=req.event_ids, label=req.label)

    @app.post("/background/history/clear")
    def background_history_clear_endpoint(req: BackgroundHistoryClearRequest) -> dict[str, object]:
        return background.clear_history(keep_pinned=req.keep_pinned)

    @app.post("/background/config")
    def background_config_endpoint(req: BackgroundConfigRequest) -> dict[str, object]:
        return background.update_config(dict(req.updates))

    @app.post("/background/pause")
    def background_pause_endpoint() -> dict[str, object]:
        return background.pause()

    @app.post("/background/resume")
    def background_resume_endpoint() -> dict[str, object]:
        return background.resume()

    @app.post("/background/capture")
    def background_capture_endpoint(req: BackgroundCaptureRequest) -> dict[str, object]:
        try:
            action_id = background.begin_action("capture")
            session_id = req.session_id or background.state.active_live_session_id
            if not session_id:
                raise ValueError("no active live session is available for background capture")
            seconds = req.seconds if req.seconds is not None else background.config.default_capture_seconds
            preset_id = req.route_preset or background.config.default_route_preset
            capture = live.capture_last(session_id, seconds)
            analyzed = analyze_capture(
                capture,
                preset_id,
                privacy_mode="incognito" if background.config.incognito else "ephemeral",
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
            )
            event = analyzed.get("listening_event") if isinstance(analyzed.get("listening_event"), dict) else None
            trace = None
            should_remember = (req.remember or background.config.save_events_by_default) and not background.config.incognito
            if should_remember and event:
                trace = memory.remember(event, tags=["background-capture"])
                event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
            background.finish_action(event)
            return {"action_id": action_id, **analyzed, "trace": trace, "background": background.status()}
        except (RuntimeError, ValueError) as exc:
            background.fail_action(str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/upload")
    def upload_endpoint(file: UploadFile = File(...)) -> dict[str, object]:
        saved = save_upload(file)
        return saved

    @app.get("/sample-tone")
    def sample_tone_endpoint() -> dict[str, object]:
        path = sample_tone_path(config.data_dir)
        return {"path": str(path), "sample": True, "raw_audio_policy": "generated_local_fixture"}

    @app.post("/transcribe")
    def transcribe_endpoint(req: TranscribeRequest) -> dict[str, object]:
        try:
            path = _require_existing_path(req.path)
            result, engine_result = transcribe(engine, str(path), req.timestamps)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"transcript": dump_model(result), "engine": dump_model(engine_result)}

    @app.post("/events")
    def events_endpoint(req: PathRequest) -> dict[str, object]:
        try:
            path = _require_existing_path(req.path)
            result, engine_result = events(engine, str(path))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"events": dump_model(result), "engine": dump_model(engine_result)}

    @app.post("/caption")
    def caption_endpoint(req: CaptionRequest) -> dict[str, object]:
        try:
            path = _require_existing_path(req.path)
            result, engine_result = caption(engine, str(path), req.detail)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"caption": dump_model(result), "engine": dump_model(engine_result)}

    @app.post("/speech")
    def speech_endpoint(req: PathRequest) -> dict[str, object]:
        try:
            path = _require_existing_path(req.path)
            result, engine_result = speech(engine, str(path))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"speech": dump_model(result), "engine": dump_model(engine_result)}

    @app.post("/music")
    def music_endpoint(req: PathRequest) -> dict[str, object]:
        try:
            path = _require_existing_path(req.path)
            result, engine_result = music(engine, str(path))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"music": dump_model(result), "engine": dump_model(engine_result)}

    @app.post("/moss-analysis")
    def moss_analysis_endpoint(req: MossAnalysisRequest) -> dict[str, object]:
        try:
            path = _require_existing_path(req.path)
            result, engine_result = direct_analysis(engine, str(path), req.mode, req.thinking_budget)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"analysis": result, "engine": dump_model(engine_result)}

    @app.post("/listen-event")
    def listen_event_endpoint(req: ListenEventRequest) -> dict[str, object]:
        try:
            preset = route_preset(req.route_preset)
            path = _require_existing_path(req.path)
            privacy_mode = _privacy_mode(req.privacy_mode)
            source_type = _audio_source_type(req.source_type)
            raw_audio_policy = _raw_audio_policy(
                req.raw_audio_policy or ("temp" if source_type in {"live_input", "system_output", "buffer"} else "external_ref")
            )
            segment = audio_segment_from_path(
                path,
                source=source_for_path(
                    path,
                    source_type=source_type,
                    label=req.source_label,
                    device_id=req.device_id,
                ),
                privacy_mode=privacy_mode,
                ephemeral=raw_audio_policy in {"temp", "not_stored"},
                metadata={"raw_audio_policy": raw_audio_policy},
            )
            broadcaster.publish(
                "listen_started",
                {"path": str(path), "route_preset": preset.id, "source": source_type},
            )
            perception = report(engine, str(path), "oida", passes=preset.moss_passes, chunk_seconds=config.moss_chunk_seconds, overlap_seconds=_chunk_overlap(config))
            perception_dict = report_to_dict(perception)
            command_output = build_harness_output(perception_dict, command=preset.akouo_command)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                segment=segment,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=privacy_mode,
                raw_audio_policy=raw_audio_policy,
            )
            event = memory.enrich_event(event)
            background.finish_action(event)
            broadcaster.publish("listen_completed", {"listening_event": event, "route_preset": preset.id})
        except ValueError as exc:
            broadcaster.publish("listen_failed", {"detail": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"listening_event": event, "perception_report": perception_dict, "command_output": command_output, "background": background.status()}

    @app.post("/listen-event/rerun")
    def listen_event_rerun_endpoint(req: ListenEventRerunRequest) -> dict[str, object]:
        try:
            preset = route_preset(req.route_preset)
            source_event = dict(req.event or {})
            path, segment, privacy_mode, raw_audio_policy = _rerun_segment(
                source_event,
                path_override=req.path,
                privacy_mode=req.privacy_mode,
                raw_audio_policy=req.raw_audio_policy,
            )
            perception = report(engine, str(path), f"oida-route-rerun-{preset.id}", passes=preset.moss_passes, chunk_seconds=config.moss_chunk_seconds, overlap_seconds=_chunk_overlap(config))
            perception_dict = report_to_dict(perception)
            command_output = build_harness_output(perception_dict, command=preset.akouo_command)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                segment=segment,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=privacy_mode,
                raw_audio_policy=raw_audio_policy,
            )
            event = memory.enrich_event(event)
            trace = None
            if req.remember and privacy_mode != "incognito":
                trace = memory.remember(event, tags=["route-rerun", f"route-{preset.id}"])
                event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
            route_comparison = compare_route_events(
                source_event,
                event,
                signal_fields=req.comparison_signal_fields,
                min_abs_signal_delta=req.comparison_min_abs_signal_delta,
                changed_only=req.comparison_changed_only,
            )
            background.finish_action(event)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "route_rerun": {
                "from_event_id": source_event.get("id"),
                "route_preset": preset.id,
                "path": str(path),
                "raw_audio_policy": raw_audio_policy,
            },
            "route_comparison": route_comparison,
            "trace": trace,
            "listening_event": event,
            "perception_report": perception_dict,
            "command_output": command_output,
            "background": background.status(),
        }

    @app.post("/native/system-audio/analyze")
    def native_system_audio_analyze_endpoint(req: NativeSystemAudioAnalyzeRequest) -> dict[str, object]:
        try:
            preset = route_preset(req.route_preset)
            path = _require_existing_path(req.path)
            source_route = normalize_system_audio_source_route(req.source_route)
            source_label = system_audio_source_label(req.source_label, source_route)
            source = source_for_path(
                path,
                source_type="system_output",
                label=source_label,
                device_id=str(source_route.get("route_id") or ""),
                platform=str(source_route.get("platform") or ""),
                details={
                    "source_route": source_route,
                    "capture_scope": source_route.get("capture_scope"),
                    "capture_adapter": source_route.get("adapter"),
                },
            )
            segment = audio_segment_from_path(
                path,
                source=source,
                privacy_mode=_privacy_mode(req.privacy_mode),
                ephemeral=True,
                metadata={
                    "source_adapter": "macos-screencapturekit-system-audio",
                    "raw_audio_policy": "temp",
                    "capture_duration_s": req.duration_s,
                    "analysis_user_initiated": True,
                    "source_route": source_route,
                    "capture_scope": source_route.get("capture_scope"),
                    "model_input_policy": source_route.get("model_input_policy"),
                    "claim_limits": source_route.get("claim_limits"),
                },
            )
            broadcaster.publish("listen_started", {"path": str(path), "route_preset": preset.id, "source": "system-audio"})
            perception = report(engine, str(path), "oida-native-system-audio", passes=preset.moss_passes, chunk_seconds=config.moss_chunk_seconds, overlap_seconds=_chunk_overlap(config))
            perception_dict = report_to_dict(perception)
            command_output = build_harness_output(perception_dict, command=preset.akouo_command)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                segment=segment,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=_privacy_mode(req.privacy_mode),
                raw_audio_policy="temp",
            )
            event = memory.enrich_event(event)
            trace = None
            if req.remember and _privacy_mode(req.privacy_mode) != "incognito":
                trace = memory.remember(event, tags=["native-system-audio"])
                event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
            background.finish_action(event)
            broadcaster.publish("listen_completed", {"listening_event": event, "route_preset": preset.id})
            retention_cleanup = apply_native_temp_audio_retention_after_analysis(
                background.config.native_temp_audio_retention,
                path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "path": str(path),
            "raw_audio_policy": "temp",
            "retention": "temporary local WAV generated by the native system-output tap",
            "retention_policy": background.config.native_temp_audio_retention,
            "retention_cleanup": retention_cleanup,
            "retention_status": retention_cleanup["status"],
            "source_route": source_route,
            "trace": trace,
            "listening_event": event,
            "perception_report": perception_dict,
            "command_output": command_output,
            "background": background.status(),
        }

    @app.get("/native/system-audio/routes")
    def native_system_audio_routes_endpoint() -> dict[str, object]:
        return native_system_audio_route_manifest()

    @app.get("/native/system-audio/temp")
    def native_system_audio_temp_endpoint() -> dict[str, object]:
        return native_system_audio_temp_status(background.config.native_temp_audio_retention)

    @app.post("/native/system-audio/cleanup")
    def native_system_audio_cleanup_endpoint(req: NativeSystemAudioCleanupRequest) -> dict[str, object]:
        cleanup = cleanup_native_system_audio_temp_files(
            background.config.native_temp_audio_retention,
            delete_all=req.delete_all,
            dry_run=req.dry_run,
            max_age_hours=req.max_age_hours,
            max_files=req.max_files,
        )
        return {**cleanup, "background": background.status()}

    @app.get("/raw-audio/status")
    def raw_audio_status_endpoint() -> dict[str, object]:
        return upload_audio_status(background.config.upload_audio_retention)

    @app.post("/raw-audio/wipe")
    def raw_audio_wipe_endpoint(req: RawAudioWipeRequest) -> dict[str, object]:
        cleanup = cleanup_upload_audio_files(
            background.config.upload_audio_retention,
            delete_all=req.delete_all,
            dry_run=req.dry_run,
            max_age_hours=req.max_age_hours,
            max_files=req.max_files,
            include_legacy=req.include_legacy,
        )
        return {**cleanup, "background": background.status()}

    @app.get("/acoustic-system")
    def acoustic_system_endpoint() -> dict[str, object]:
        return acoustic_system_manifest()

    @app.get("/akouo/modes")
    def akouo_modes_endpoint() -> dict[str, object]:
        loader = AkouoLoader()
        controls = available_harness_controls()
        controls["akouo_root"] = str(loader.root)
        controls["schemas_available"] = loader.schemas_dir.exists()
        controls["skills_available"] = loader.skills_dir.exists()
        return controls

    @app.get("/akouo/skills")
    def akouo_skills_endpoint() -> dict[str, object]:
        return akouo_manifest()

    @app.get("/akouo/presets")
    def akouo_presets_endpoint() -> dict[str, object]:
        manifest = akouo_manifest()
        return {"version": manifest["version"], "route_presets": manifest["route_presets"]}

    @app.get("/akouo/schema")
    def akouo_schema_endpoint() -> dict[str, object]:
        manifest = akouo_manifest()
        return {"version": manifest["version"], "schemas": manifest["schemas"], "valid": manifest["valid"], "errors": manifest["errors"]}

    @app.post("/akouo/route")
    def akouo_route_endpoint(req: AkouoHarnessRequest) -> dict[str, object]:
        try:
            return routing_plan(req.path, command=req.command, evidence_level=evidence_level_for_path(req.path))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/akouo/listen")
    def akouo_listen_endpoint(req: AkouoHarnessRequest) -> dict[str, object]:
        try:
            perception = report(engine, req.path, "akouo")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        perception_dict = report_to_dict(perception)
        try:
            command_output = build_harness_output(perception_dict, command=req.command, mode=req.mode, question=req.question)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if req.validate_output:
            try:
                AkouoLoader().validate("command-output", command_output)
            except jsonschema.ValidationError as exc:
                raise HTTPException(status_code=422, detail=f"command output failed AKOUO schema validation: {exc.message}") from exc
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "perception_report": perception_dict,
            "command_output": command_output,
            "selected_output": (command_output.get("outputs") or [None])[0],
        }

    @app.get("/metrics/process")
    def process_metrics_endpoint() -> dict[str, object]:
        return process_metrics()

    @app.post("/live/start")
    def live_start_endpoint(req: LiveStartRequest) -> dict[str, object]:
        status = live.start(
            req.ring_seconds,
            req.vad_threshold_dbfs,
            source_type=req.source_type,
            source_label=req.source_label,
            device_id=req.device_id,
        )
        background.set_active_live_session(str(status["session_id"]))
        return status

    @app.post("/live/ingest")
    def live_ingest_endpoint(session_id: str = Form(...), file: UploadFile = File(...)) -> dict[str, object]:
        try:
            live.ensure_active(session_id)
            saved = save_upload(file)
            return live.ingest_saved_upload(session_id, saved)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/live/capture")
    def live_capture_endpoint(req: LiveCaptureRequest) -> dict[str, object]:
        try:
            capture = live.capture_last(req.session_id, req.seconds)
            if not req.analyze:
                return capture
            analyzed = analyze_capture(
                capture,
                req.route_preset,
                privacy_mode="ephemeral",
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
            )
            if isinstance(analyzed.get("listening_event"), dict):
                background.finish_action(analyzed["listening_event"])
            return analyzed
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/live/status")
    def live_status_endpoint(req: LiveSessionRequest) -> dict[str, object]:
        try:
            return live.status(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/live/signal/{session_id}")
    def live_signal_endpoint(session_id: str, bands: int = 14) -> dict[str, object]:
        try:
            return live.signal_snapshot(session_id, bands=bands)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/live/stop")
    def live_stop_endpoint(req: LiveSessionRequest) -> dict[str, object]:
        try:
            status = live.stop(req.session_id)
            if background.state.active_live_session_id == req.session_id:
                background.set_active_live_session(None)
            return status
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/conversation/ask")
    def conversation_ask_endpoint(req: ConversationAskRequest) -> dict[str, object]:
        question = str(req.question or "").strip()
        forbidden = forbidden_topics_for_text(question)
        event = dict(req.event) if isinstance(req.event, dict) else None
        if event is None and req.conversation_id:
            try:
                stored = conversations.get(req.conversation_id)
                stored_event = stored.get("event") if isinstance(stored.get("event"), dict) else None
                event = dict(stored_event) if stored_event else None
            except FileNotFoundError:
                event = None
        if event is None and isinstance(background.state.latest_event, dict):
            event = dict(background.state.latest_event)
        if event is None:
            raise HTTPException(status_code=400, detail="conversation requires a listening event")
        if forbidden:
            return {
                "version": "0.1",
                "mode": "event_grounded_conversation",
                "conversation_id": req.conversation_id,
                "event_id": event.get("id"),
                "raw_audio_policy": "Conversation stores derived event JSON and turn text only; raw audio is not copied.",
                "forbidden_topics_triggered": forbidden,
                "turn": {
                    "question": question,
                    "answer": "",
                    "known_facts": [],
                    "hypotheses": [],
                    "evidence": [],
                    "uncertainty_notes": ["The question asks for unsupported acoustic claims."],
                    "memory_context": [],
                    "remote_model": {
                        "enabled": False,
                        "requested": bool(req.allow_remote_model),
                        "provider": req.provider,
                        "note": "Remote model calls are opt-in and disabled for this response.",
                    },
                },
            }
        try:
            result = conversations.ask(
                event=event,
                question=question,
                memory=memory,
                conversation_id=req.conversation_id,
                include_memory=req.include_memory,
                allow_remote_model=req.allow_remote_model,
                provider=req.provider,
            )
            result["forbidden_topics_triggered"] = []
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/generation/prompt")
    def generation_prompt_endpoint(req: GenerationPromptRequest) -> dict[str, object]:
        event = dict(req.event) if isinstance(req.event, dict) else None
        if event is None and isinstance(background.state.latest_event, dict):
            event = dict(background.state.latest_event)
        if event is None:
            raise HTTPException(status_code=400, detail="generation prompt requires a listening event")
        try:
            return generations.create_prompt(
                event,
                intent=req.intent,
                prompt=req.prompt,
                negative_prompt=req.negative_prompt,
                adapter=req.adapter,
                duration_s=req.duration_s,
                generate=req.generate,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/generation/history")
    def generation_history_endpoint(limit: int | None = 20) -> dict[str, object]:
        bounded_limit = max(1, min(100, int(limit or 20)))
        records = generations.list(limit=bounded_limit)
        return {
            "version": "0.1",
            "record_count": len(records),
            "adapter_default": "prompt_only",
            "raw_audio_policy": "Generation history stores derived prompts and external references only; raw audio is not copied.",
            "records": records,
        }

    @app.get("/generation/{generation_id}")
    def generation_get_endpoint(generation_id: str) -> dict[str, object]:
        try:
            return {"generation": generations.get(generation_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/generation/relisten")
    def generation_relisten_endpoint(req: GenerationRelistenRequest) -> dict[str, object]:
        try:
            generation = generations.get(req.generation_id)
            preset = route_preset(req.route_preset)
            output_path = _require_existing_path(req.path)
            privacy_mode = _privacy_mode(req.privacy_mode)
            perception = report(
                engine,
                str(output_path),
                f"oida-generation-relisten-{preset.id}",
                passes=preset.moss_passes,
                chunk_seconds=config.moss_chunk_seconds,
                overlap_seconds=_chunk_overlap(config),
            )
            perception_dict = report_to_dict(perception)
            command_output = build_harness_output(perception_dict, command=preset.akouo_command)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=privacy_mode,
                raw_audio_policy="external_ref",
            )
            event = memory.enrich_event(event)
            trace = None
            if req.remember and privacy_mode != "incognito":
                trace = memory.remember(event, tags=["generation", f"source-{generation.get('source_event_id')}"])
                event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
            route_comparison = compare_route_events(
                generation.get("source_event") if isinstance(generation.get("source_event"), dict) else {},
                event,
                signal_fields=None,
                min_abs_signal_delta=0.0,
                changed_only=False,
            )
            stored = generations.attach_relisten(
                req.generation_id,
                output_path=str(output_path),
                generated_event=event,
                route_comparison=route_comparison,
                persist=privacy_mode != "incognito",
            )
            background.finish_action(event)
            return {
                "generation": stored,
                "trace": trace,
                "listening_event": event,
                "perception_report": perception_dict,
                "command_output": command_output,
                "route_comparison": route_comparison,
                "background": background.status(),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/qa")
    def qa_endpoint(req: QaRequest) -> dict[str, object]:
        forbidden = forbidden_topics_for_text(req.question)
        if forbidden:
            return {"qa": {"question": req.question, "answer": "", "reasoning_trace": None, "thinking_budget": req.thinking_budget}, "forbidden_topics_triggered": forbidden}
        try:
            path = _require_existing_path(req.path)
            result, engine_result = qa(engine, str(path), req.question, req.thinking_budget, context=req.context)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"qa": dump_model(result), "engine": dump_model(engine_result), "forbidden_topics_triggered": []}

    @app.post("/think")
    def think_endpoint(req: ThinkRequest) -> dict[str, object]:
        forbidden = forbidden_topics_for_text(req.instruction)
        if forbidden:
            return {"qa": {"question": req.instruction, "answer": "", "reasoning_trace": None, "thinking_budget": req.thinking_budget}, "forbidden_topics_triggered": forbidden}
        try:
            path = _require_existing_path(req.path)
            result, engine_result = think(engine, str(path), req.instruction, req.thinking_budget)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"qa": dump_model(result), "engine": dump_model(engine_result), "forbidden_topics_triggered": []}

    @app.post("/report")
    def report_endpoint(req: ReportRequest) -> dict[str, object]:
        try:
            result = report(engine, req.path, req.profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return report_to_dict(result)

    @app.get("/memory")
    def memory_list_endpoint(
        q: str | None = None,
        tag: str | None = None,
        source_kind: str | None = None,
        route: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        traces = memory.list(q, tag=tag, source_kind=source_kind, route=route, since=since, until=until, limit=limit)
        return {
            "version": "0.1",
            "trace_count": len(traces),
            "raw_audio_policy": "Saved traces keep derived data; raw audio is only saved or referenced according to each trace.audioPolicy.",
            "traces": traces,
        }

    @app.get("/memory/export")
    def memory_export_endpoint(
        q: str | None = None,
        tag: str | None = None,
        source_kind: str | None = None,
        route: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        return memory.export_json(query=q, tag=tag, source_kind=source_kind, route=route, since=since, until=until, limit=limit)

    @app.get("/memory/trace/{trace_id}")
    def memory_trace_endpoint(trace_id: str) -> dict[str, object]:
        try:
            return {"trace": memory.get(trace_id), "similar": memory.similar_to_trace(trace_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/memory/remember")
    def memory_remember_endpoint(req: MemoryRememberRequest) -> dict[str, object]:
        event = dict(req.event)
        trace = memory.remember(event, user_notes=req.user_notes, tags=req.tags)
        event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
        return {"trace": trace, "event": event}

    @app.post("/memory/forget")
    def memory_forget_endpoint(req: MemoryForgetRequest) -> dict[str, object]:
        try:
            return memory.forget(req.trace_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/memory/similar")
    def memory_similar_endpoint(req: MemorySimilarRequest) -> dict[str, object]:
        limit = max(1, min(25, int(req.limit or 5)))
        try:
            if req.trace_id:
                similar = memory.similar_to_trace(req.trace_id, limit=limit)
            elif req.event:
                similar = memory.similar_to_event(dict(req.event), limit=limit)
            else:
                raise ValueError("memory similarity requires event or trace_id")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"version": "0.1", "similar": similar}

    return app


def save_upload(file: UploadFile) -> dict[str, object]:
    upload_root = uploads_dir()
    upload_root.mkdir(parents=True, exist_ok=True)
    original = sanitize_filename(file.filename or "recording.webm")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = upload_root / f"{stamp}-{original}"
    try:
        total = 0
        with raw_path.open("wb") as handle:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise _UploadTooLargeError()
                handle.write(chunk)
    except _UploadTooLargeError:
        cleanup_failed_upload(raw_path)
        raise HTTPException(
            status_code=413,
            detail=f"upload exceeds the maximum size of {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB",
        ) from None
    except OSError:
        cleanup_failed_upload(raw_path)
        raise

    normalized_path, normalization_error = normalize_audio(raw_path)
    if normalization_error:
        cleanup_failed_upload(raw_path)
        raise HTTPException(status_code=422, detail=normalization_error)
    return {
        "filename": original,
        "content_type": file.content_type,
        "path": str(normalized_path),
        "raw_path": str(raw_path),
        "normalized": normalized_path != raw_path,
        "processing": upload_processing_info(raw_path, normalized_path),
        "sha256": sha256_file(normalized_path),
    }


def sample_tone_path(root: Path) -> Path:
    path = root / "samples" / "oida-tone.wav"
    if path.exists():
        return path
    import numpy as np
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16_000
    t = np.arange(sample_rate, dtype=np.float32) / sample_rate
    samples = (0.15 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(path, samples, sample_rate)
    return path


def normalize_audio(path: Path) -> tuple[Path, str | None]:
    if path.suffix.lower() in {".wav", ".wave"}:
        return path, None
    target = path.with_suffix(".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2", str(target)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return path, "ffmpeg is required to convert uploaded or recorded non-WAV audio to WAV."
    except subprocess.TimeoutExpired:
        return path, "ffmpeg timed out converting the uploaded audio; the file may be malformed."
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        suffix = f" ffmpeg: {detail[-1]}" if detail else ""
        return path, f"Could not convert uploaded audio to WAV.{suffix}"
    if not target.exists():
        return path, "ffmpeg completed but did not produce a WAV file."
    return target, None


def upload_processing_info(raw_path: Path, normalized_path: Path) -> dict[str, object]:
    decoded_to_wav = normalized_path != raw_path
    return {
        "decoded_to_wav": decoded_to_wav,
        "selected_input": str(normalized_path),
        "raw_input": str(raw_path),
        "codec_policy": "Uploaded non-WAV audio is decoded with FFmpeg to PCM WAV for reliable local processing.",
        "moss_input": "MOSS-Audio loads the selected path and internally converts it to 16 kHz mono.",
        "dsp_input": "oida keeps the selected local audio path for DSP measurements before claim mapping.",
    }


def cleanup_failed_upload(raw_path: Path) -> None:
    candidates = {raw_path, raw_path.with_suffix(".wav")}
    for candidate in candidates:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def sanitize_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip("-")
    return stem or "audio-upload"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_existing_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"audio path does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"audio path is not a file: {resolved}")
    return resolved


def _rerun_segment(
    event: dict[str, object],
    *,
    path_override: str | None = None,
    privacy_mode: str | None = None,
    raw_audio_policy: str | None = None,
) -> tuple[Path, AudioSegment, PrivacyMode, RawAudioPolicy]:
    segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
    data_ref = segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
    path_value = path_override or data_ref.get("uri")
    if not path_value:
        raise ValueError("route rerun requires an event segment with data_ref.uri or an explicit path")
    path = Path(str(path_value)).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"route rerun audio path does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"route rerun audio path is not a file: {path}")

    resolved_privacy = _privacy_mode(str(privacy_mode or event.get("privacy_mode") or segment.get("privacy_mode") or "session"))
    resolved_policy = _raw_audio_policy(str(raw_audio_policy or event.get("raw_audio_policy") or _segment_raw_audio_policy(segment) or "external_ref"))
    metadata = dict(segment.get("metadata")) if isinstance(segment.get("metadata"), dict) else {}
    metadata["route_rerun"] = {
        "from_event_id": event.get("id"),
        "previous_route_ids": _event_route_ids(event),
        "user_initiated": True,
    }
    segment_obj = audio_segment_from_path(
        path,
        source=_source_descriptor_from_event(event, path),
        privacy_mode=resolved_privacy,
        ephemeral=bool(segment.get("ephemeral", resolved_policy != "external_ref")),
        metadata=metadata,
    )
    return path, segment_obj, resolved_privacy, resolved_policy


def _source_descriptor_from_event(event: dict[str, object], path: Path) -> AudioSourceDescriptor:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    source_type = str(source.get("type") or "file")
    if source_type not in {"live_input", "system_output", "file", "buffer", "generated", "external_stream"}:
        source_type = "file"
    details = source.get("details") if isinstance(source.get("details"), dict) else {}
    return source_for_path(
        path,
        source_type=source_type,  # type: ignore[arg-type]
        label=str(source.get("label")) if source.get("label") else None,
        device_id=str(source.get("device_id")) if source.get("device_id") else None,
        platform=str(source.get("platform")) if source.get("platform") else None,
        details=details,
    )


def _segment_raw_audio_policy(segment: dict[str, object]) -> str | None:
    metadata = segment.get("metadata") if isinstance(segment.get("metadata"), dict) else {}
    value = metadata.get("raw_audio_policy")
    return str(value) if value else None


def _event_route_ids(event: dict[str, object]) -> list[str]:
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    return [str(route.get("route_id")) for route in routes if isinstance(route, dict) and route.get("route_id")]


def _privacy_mode(value: str) -> PrivacyMode:
    if value in {"ephemeral", "session", "saved", "incognito"}:
        return value  # type: ignore[return-value]
    raise ValueError(f"unknown privacy mode: {value}")


def _raw_audio_policy(value: str) -> RawAudioPolicy:
    if value in {"not_stored", "temp", "saved", "external_ref"}:
        return value  # type: ignore[return-value]
    raise ValueError(f"unknown raw audio policy: {value}")


def _audio_source_type(value: str) -> SourceType:
    if value in {"live_input", "system_output", "file", "buffer", "generated", "external_stream"}:
        return value  # type: ignore[return-value]
    raise ValueError(f"unknown audio source type: {value}")


_app_singleton = None


def __getattr__(name: str) -> Any:
    # Build the FastAPI app lazily. Importing this module (tests, tooling, the CLI
    # entrypoint) must not construct the MOSS engine or mutate sys.path at import time.
    # `uvicorn oida.server:app` still resolves `app` through this hook on first access.
    if name == "app":
        global _app_singleton
        if FastAPI is None:
            return None
        if _app_singleton is None:
            _app_singleton = create_app()
        return _app_singleton
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the oida localhost perception daemon.")
    parser.add_argument("--profile", default=None, choices=["mac-mps", "cuda-server", "stub"])
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None, type=int)
    args = parser.parse_args()
    config = load_config(profile=args.profile, host=args.host, port=args.port)
    import uvicorn

    uvicorn.run(create_app(profile=config.profile, host=config.host, port=config.port), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
