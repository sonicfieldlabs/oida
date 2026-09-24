from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import jsonschema

try:
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.responses import (
        FileResponse,
        HTMLResponse,
        JSONResponse,
        StreamingResponse,
    )
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, ConfigDict, Field
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without deps
    FastAPI = None  # type: ignore[assignment]
    HTTPException = RuntimeError  # type: ignore[assignment]

    def Form(default=None, **_):  # type: ignore[no-redef]
        return default

    BaseModel = object  # type: ignore[assignment,misc]
    ConfigDict = dict  # type: ignore[assignment,misc]

    def Field(default=None, **_):  # type: ignore[no-redef]
        return default

    FASTAPI_IMPORT_ERROR = exc
else:
    FASTAPI_IMPORT_ERROR = None

from oida.station_aperture import Aperture
from oida.config import REPO_ROOT, load_config, uploads_dir
from oida.acoustic_system import acoustic_system_manifest
from oida.akouo_skills import akouo_manifest, route_preset
from oida.background import BackgroundRuntime
from oida.conversation import ConversationStore
from oida.contracts import (
    AudioSegment,
    AudioSourceDescriptor,
    PrivacyMode,
    RawAudioPolicy,
    SourceType,
    audio_segment_from_path,
    new_id,
    source_for_path,
)
from oida.covenant import CovenantStore, parse_covenant
from oida.dsp import audio_info
from oida.engine import build_engine
from oida.engine_base import EngineUnavailable
from oida.generation import GenerationStore
from oida.gateway import (
    GATEWAY_CONTRACT,
    decision_first_refusal,
    gateway_manifest,
    harness_host_perception,
)
from oida.listening import listening_event_dict
from oida.listening_identity import (
    MAX_LISTENING_IDENTITY_CHARS,
    ListeningIdentityConflict,
    ListeningIdentitySnapshot,
    ListeningIdentityStore,
)
from oida.live import LiveManager
from oida.memory import AkousmataStore, earworm_context_for_event
from oida.metrics import process_metrics
from oida.native_temp_audio import (
    apply_native_temp_audio_retention_after_analysis,
    cleanup_native_system_audio_temp_files,
    finalize_native_temp_audio_session,
    native_system_audio_temp_status,
)
from oida.privacy import redact_event_audio_for_policy
from oida.raw_audio import (
    cleanup_upload_audio_files,
    finalize_upload_audio_session,
    upload_audio_status,
)
from oida.reporting import (
    ALL_MOSS_PASSES,
    caption,
    direct_analysis,
    events,
    forbidden_topics_for_text,
    music,
    qa,
    report,
    report_to_dict,
    speech,
    think,
    transcribe,
)
from oida.reportschema import dump_model
from oida.route_comparison import compare_route_events
from oida.reasoning.contracts import ModelDescriptor, ModelRole
from oida.reasoning.audio_router import RoutedAudioEngine
from oida.reasoning.budget import BudgetLedger
from oida.reasoning.audio_selection import (
    AudioModel,
    resolve as resolve_audio_model,
    selector as audio_selector,
    use_audio_model,
)
from oida.reasoning.model_catalog import find_model_spec
from oida.reasoning.oauth import OpenRouterOAuth
from oida.reasoning.orchestrator import ReasoningOrchestrator, TurnOptions
from oida.reasoning.public_api import public_to_settings, settings_to_public
from oida.reasoning.registry import build_provider_registry
from oida.reasoning.secrets import (
    SecretPersistenceUnavailable,
    SecretStoreError,
    default_secret_store,
)
from oida.reasoning.settings import ReasoningSettingsStore
from oida.reasoning.resources import resource_assessment
from oida.reasoning.validation import ResponseValidationError
from oida.relisten import TargetedRelistener
from oida.sonicfield import SonicFieldBridge, terms_from_event
from oida.songid import identify_song
from oida.source_routes import (
    native_system_audio_route_manifest,
    normalize_system_audio_source_route,
    system_audio_source_label,
)
from oida.source_admission import SourceAdmission
from oida.sources import source_registry_dict
from oida.system_audio import system_audio_status_dict
from harness.akouo.command import build_harness_output
from harness.akouo.loader import AkouoLoader
from harness.akouo.routing import (
    available_harness_controls,
    evidence_level_for_path,
    routing_plan,
)

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB cap on a single upload/ingest body
_LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
LOGGER = logging.getLogger(__name__)


class _UploadTooLargeError(Exception):
    pass


def _chunk_overlap(config) -> float:
    desired = 15.0 if config.moss_chunk_seconds >= 300 else 5.0
    return min(desired, config.moss_chunk_seconds / 2)


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


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON numeric constant is not allowed: {value}")


class OidaRequest(BaseModel):  # type: ignore[misc,valid-type]
    model_config = ConfigDict(allow_inf_nan=False, strict=True)


class PathRequest(OidaRequest):
    path: str


class TranscribeRequest(PathRequest):
    timestamps: str = "sentence"


class CaptionRequest(PathRequest):
    detail: str = "dense"


class QaRequest(PathRequest):
    question: str
    thinking_budget: int | None = Field(default=None, ge=0)
    context: str | None = None


class ConversationAskRequest(OidaRequest):
    question: str = Field(min_length=1, max_length=16_000)
    event: dict[str, object] | None = None
    event_id: str | None = None
    conversation_id: str | None = None
    include_memory: bool = True
    allow_remote_model: bool = False
    provider: str = "local_structured"
    provider_id: str | None = None
    model_id: str | None = None
    profile_id: str | None = None
    comparison_event_ids: list[str] = Field(default_factory=list, max_length=3)
    allow_targeted_relisten: bool | None = None
    include_transcript: bool | None = None
    include_memory_content: bool | None = None


class ConversationCommitRequest(OidaRequest):
    prepare_token: str = Field(min_length=16, max_length=512)
    response: dict[str, object] | str


class ReasoningCredentialRequest(OidaRequest):
    credential: str = Field(min_length=1, max_length=65_536)


class GenerationPromptRequest(OidaRequest):
    event: dict[str, object] | None = None
    intent: str = "transform"
    prompt: str | None = None
    negative_prompt: str | None = None
    adapter: str = "prompt_only"
    duration_s: float | None = Field(default=None, gt=0, le=600)
    generate: bool = False


class GenerationRelistenRequest(OidaRequest):
    generation_id: str
    path: str
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str = "session"
    remember: bool = False


class ThinkRequest(PathRequest):
    instruction: str
    thinking_budget: int | None = Field(default=None, ge=0)


class ReportRequest(PathRequest):
    profile: str = "default"


class ListenEventRequest(PathRequest):
    aperture: Aperture | None = None
    specialist_tasks: list[
        Literal["tag_events", "track_beats", "transcribe", "speech_quality"]
    ] = Field(default_factory=list, max_length=3)
    # T5: explicit specialist deployment per task; task-only selections keep
    # the owner's configured default. An unknown deployment is reported
    # unavailable in its lane; nothing is substituted.
    specialist_deployments: dict[
        Literal["tag_events", "track_beats", "transcribe", "speech_quality"], str
    ] = Field(default_factory=dict, max_length=3)
    speech_vad: bool = True
    speech_alignment: bool = False
    speech_language: Literal["English", "Spanish", "Portuguese"] | None = None
    live_session_id: str | None = Field(default=None, max_length=120)
    live_start_seconds: float | None = Field(default=None, ge=0, le=86400000)
    audio_model: AudioModel | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    listening_mode: str | None = Field(default=None, min_length=1, max_length=100)
    source_admission: SourceAdmission | None = None
    listening_access: dict[str, Any] | None = None
    spectral_request: dict[str, Any] | None = None
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str = "session"
    source_type: str = "file"
    source_label: str | None = None
    device_id: str | None = None
    raw_audio_policy: str | None = None
    allow_external_audio: bool = False
    # spec v1.2 capture semantics: how this listen was triggered relative to
    # time (past = ring-buffer slice before the trigger, future = window
    # recorded after it, live = open-ended) and where it was heard.
    capture_direction: str | None = None
    capture_seconds: float | None = Field(default=None, ge=0, le=86_400)
    capture_trigger: str | None = None
    location: dict[str, Any] | None = None
    # spec v1.3 sovereignty: pin a named covenant for this listen; None uses
    # the active covenant; the layer is empty by default.
    covenant: str | None = None
    # Opt-in only. The UI exposes this beside Music mode; other routes ignore
    # it so recognition cannot be enabled accidentally by a stale client.
    song_id: bool = False
    passes: (
        list[Literal["transcribe", "events", "caption", "speech", "music"]] | None
    ) = None
    context_refs: list[str] | None = Field(default=None, max_length=7)
    focus_question: str | None = Field(default=None, max_length=4096)
    expected_source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AudioProbeRequest(BaseModel):
    model_id: str | None = None
    operation_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class GatewayListenRequest(ListenEventRequest):
    native_options: dict[str, Any] | None = None
    retain_library_audio: bool = False
    library_parent_sound_id: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    # Finding P1-02. Kinship oída cannot observe: that this listening reviews a
    # sound generated from an earlier account. Only the caller knows it, and
    # without this the join lives in the caller's run receipt where no exporter
    # can reach it. Validated and recorded by apply_declared_lineage, which
    # refuses an unrecognised relation type rather than coercing it.
    declared_lineage: dict[str, Any] | None = None
    response_mode: Literal["full", "summary"] = "full"
    ephemeral_delivery: bool = False
    operation_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    # A caller's hard deadline, in epoch seconds. Absent, admitted work runs to completion,
    # as before. Present, the operation stops at its next checkpoint or inside a model
    # generation once it passes, and discards partial output.
    deadline_at: float | None = Field(default=None, gt=0)
    remember: bool = False
    user_notes: str | None = None
    human_listener_id: str | None = None
    human_display_name: str | None = None
    tags: list[str] = Field(default_factory=list)


class GatewayWindowRequest(GatewayListenRequest):
    start_seconds: float = Field(default=0.0, ge=0, le=86400)
    seconds: float = Field(default=10.0, gt=0, le=60)


class GatewayHarnessRequest(OidaRequest):
    perception: dict[str, Any]
    route_preset: str = "basic"
    command: str | None = None
    question: str | None = None
    remember: bool = False
    privacy_mode: str = "session"
    raw_audio_policy: str = "not_stored"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None


class GatewayRouteRequest(OidaRequest):
    object_listened_to: str = "audio input"
    command: str = "/route"
    evidence_level: str = "audio_available"


class ListenEventRerunRequest(OidaRequest):
    event: dict[str, object] | None = None
    path: str | None = None
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str | None = None
    raw_audio_policy: str | None = None
    allow_external_audio: bool = False
    remember: bool = False
    comparison_signal_fields: list[str] | None = None
    comparison_min_abs_signal_delta: float | None = Field(default=None, ge=0)
    comparison_changed_only: bool = False


class MossAnalysisRequest(PathRequest):
    mode: str = "environment"
    thinking_budget: int | None = Field(default=None, ge=0)


class AkouoHarnessRequest(PathRequest):
    command: str = "/listen"
    mode: str | None = None
    question: str | None = None
    validate_output: bool = Field(False, alias="validate")


class LiveStartRequest(OidaRequest):
    ring_seconds: float = Field(default=60.0, ge=1, le=3600)
    vad_threshold_dbfs: float = Field(default=-45.0, ge=-160, le=0)
    source_type: str = "live_input"
    source_label: str | None = None
    device_id: str | None = None


class LiveSessionRequest(OidaRequest):
    session_id: str


class LiveCaptureRequest(OidaRequest):
    session_id: str
    seconds: float = Field(default=10.0, gt=0, le=3600)
    end_seconds: float | None = Field(default=None, gt=0, le=86400000)
    analyze: bool = False
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    song_id: bool = False


class CovenantSaveRequest(OidaRequest):
    name: str
    text: str
    activate: bool = False


class CovenantActivateRequest(OidaRequest):
    name: str | None = None


class ListeningIdentitySaveRequest(OidaRequest):
    text: str = Field(default="", max_length=MAX_LISTENING_IDENTITY_CHARS)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class MemoryRememberRequest(OidaRequest):
    event: dict[str, object]
    user_notes: str | None = None
    human_listener_id: str | None = None
    human_display_name: str | None = None
    tags: list[str] = Field(default_factory=list)


class MemoryForgetRequest(OidaRequest):
    trace_id: str


class HumanListeningRememberRequest(OidaRequest):
    machine_akousma_id: str
    note: str
    human_listener_id: str | None = None
    human_display_name: str | None = None


class MemorySimilarRequest(OidaRequest):
    event: dict[str, object] | None = None
    trace_id: str | None = None
    limit: int = 5


class BackgroundConfigRequest(OidaRequest):
    updates: dict[str, object] = Field(default_factory=dict)


class BackgroundHistoryPinRequest(OidaRequest):
    event_id: str
    pinned: bool = True


class BackgroundHistoryBatchPinRequest(OidaRequest):
    event_ids: list[str] = Field(default_factory=list)
    pinned: bool = True


class BackgroundHistoryClearRequest(OidaRequest):
    keep_pinned: bool = True


class BackgroundHistoryArchiveRequest(OidaRequest):
    event_ids: list[str] = Field(default_factory=list)
    label: str | None = None


class BackgroundCaptureRequest(OidaRequest):
    session_id: str | None = None
    seconds: float | None = Field(default=None, gt=0, le=3600)
    route_preset: str | None = None
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    remember: bool = False
    song_id: bool = False


class NativeSystemAudioAnalyzeRequest(PathRequest):
    route_preset: str = "basic"
    enabled_skill_ids: list[str] | None = None
    disabled_skill_ids: list[str] | None = None
    privacy_mode: str = "ephemeral"
    source_label: str = "Native system audio"
    duration_s: float | None = Field(default=None, gt=0, le=3600)
    remember: bool = False
    source_route: dict[str, object] | None = None
    capture_direction: str | None = None
    capture_trigger: str | None = None
    song_id: bool = False


class NativeSystemAudioCleanupRequest(OidaRequest):
    delete_all: bool = False
    dry_run: bool = False
    max_age_hours: float | None = Field(default=None, ge=0, strict=True)
    max_files: int | None = Field(default=None, ge=0, strict=True)


class RawAudioWipeRequest(OidaRequest):
    delete_all: bool = False
    dry_run: bool = False
    max_age_hours: float | None = Field(default=None, ge=0, strict=True)
    max_files: int | None = Field(default=None, ge=0, strict=True)
    include_legacy: bool = False


class SonicFieldExploreRequest(OidaRequest):
    event: dict[str, object] | None = None
    query: str | None = None
    limit_per_surface: int = 5


class CaptureRequestBody(OidaRequest):
    seconds: float | None = Field(default=None, gt=0, le=3600)
    route_preset: str | None = None
    direction: str | None = None
    source: str | None = None
    enabled_skill_ids: list[str] | None = None
    song_id: bool = False


class CaptureRequestClaimBody(OidaRequest):
    id: str | None = None


class SessionCreateRequest(OidaRequest):
    name: str | None = None


class SessionRenameRequest(OidaRequest):
    name: str


class EventRenameRequest(OidaRequest):
    title: str


class EngineModelBody(OidaRequest):
    model_kind: str = "instruct"
    model: str


class SonicFieldRevealRequest(OidaRequest):
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



def dispatch_deadline(passes, *, now=None, wall=None) -> float:
    """The monotonic time after which no further pass of a listening is dispatched.

    A caller's deadline, when one was sent, is the bound. Otherwise each requested pass
    gets 120 s. A single 120 s bound for the whole listening (13 September) refused the
    second pass of a two-pass route whenever the first ran long on this M1 Max: a Phase 3
    loop's field listening was refused at 190 s under an 1800 s window (24 September).
    """
    from oida.operation_control import current_deadline

    now = time.monotonic() if now is None else now
    caller = current_deadline()
    if caller is not None:
        return now + max(0.0, caller - (time.time() if wall is None else wall))
    return now + 120 * max(1, len(passes or []))

def create_app(
    profile: str | None = None, host: str | None = None, port: int | None = None
) -> Any:
    if FastAPI is None:
        raise RuntimeError(
            "FastAPI dependencies are not installed; run `uv sync` first"
        ) from FASTAPI_IMPORT_ERROR

    config = load_config(profile=profile, host=host, port=port)
    from oida.owner_journal import OwnerJournal

    owner_journal = OwnerJournal(config.data_dir / "owner-journal.sqlite3")
    evidence_setting = os.environ.get("OIDA_APPARATUS_EVIDENCE")
    evidence_manifest = (
        Path(evidence_setting).expanduser().resolve() if evidence_setting else None
    )
    local_engine = build_engine(config)
    live = LiveManager()
    memory = AkousmataStore()
    covenant_store = CovenantStore(config.data_dir)
    listening_identity_store = ListeningIdentityStore(config.data_dir)
    background = BackgroundRuntime()
    conversations = ConversationStore(config.data_dir / "sessions" / "conversations")
    reasoning_settings = ReasoningSettingsStore(
        config.data_dir / "settings" / "reasoning.json"
    )
    reasoning_secrets = default_secret_store()
    reasoning_budget = BudgetLedger(config.trial_dir)
    engine = RoutedAudioEngine(
        local_engine,
        settings_store=reasoning_settings,
        secret_store=reasoning_secrets,
        covenant_store=covenant_store,
        listening_identity_store=listening_identity_store,
        incognito_getter=lambda: bool(background.config.incognito),
        budget_ledger=reasoning_budget,
    )
    generations = GenerationStore()
    broadcaster = EventBroadcaster()
    sonicfield = SonicFieldBridge(config.sonicfield_root)
    navigator_watcher: Any | None = None
    navigator_load_settings: Any | None = None
    navigator_ensure_human_profile: Any | None = None
    mcp_http_app: Any | None = None
    mcp_session_manager: Any | None = None
    try:
        from oida.mcp_server import MCP as _gateway_mcp
        from oida.mcp_server import streamable_http_app as _streamable_http_app

        mcp_http_app = _streamable_http_app()
        mcp_session_manager = _gateway_mcp.session_manager
    except ImportError as exc:
        LOGGER.warning("MCP gateway is unavailable: %s", exc)
    try:
        uploads_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        LOGGER.warning("could not create audio dir %s", uploads_dir())

    engine_monitor: dict[str, Any] = {
        "state": "stub"
        if config.profile == "stub"
        else ("remote" if config.profile == "cuda-server" else "cold"),
        "detail": "stub profile produces no model perception; DSP still listens"
        if config.profile == "stub"
        else None,
        "warmed_ms": None,
    }
    engine_monitor_lock = threading.RLock()
    perception_role_notes: list[str] = []
    weights_root = REPO_ROOT / "weights"
    available_models = scan_moss_models(weights_root)

    def current_listening_identity_snapshot() -> ListeningIdentitySnapshot:
        try:
            return listening_identity_store.snapshot()
        except (OSError, ValueError):
            # Identity is optional orientation. A malformed manual edit stays
            # repairable through /listening without disabling perception.
            return ListeningIdentitySnapshot.empty()

    def prepare_covenant_perception(
        path: str | Path,
        *,
        source_type: str,
        passes: list[str] | tuple[str, ...],
    ) -> tuple[Any | None, list[str], list[str]]:
        """Apply the active Covenant's input/content gates to a new hearing."""

        covenant_engine = covenant_store.engine()
        selected = list(passes)
        rules_applied: list[str] = []
        if covenant_engine is None:
            return None, selected, rules_applied
        refusal = (
            covenant_engine.refuse_source(source_type)
            or covenant_engine.refuse_quiet_hours()
        )
        if refusal:
            raise HTTPException(
                status_code=423,
                detail=f"withheld under covenant {covenant_engine.covenant.id}: {refusal}",
            )
        max_window = covenant_engine.max_window_seconds()
        if max_window is not None:
            info = audio_info(path)
            actual_seconds = (
                float(info.get("durationSeconds") or 0.0)
                if isinstance(info, dict)
                else 0.0
            )
            if actual_seconds <= 0 or actual_seconds > max_window + 1e-6:
                raise HTTPException(
                    status_code=423,
                    detail=(
                        f"withheld under covenant {covenant_engine.covenant.id}: "
                        f"audio must be bounded to {max_window:g} seconds or less"
                    ),
                )
        selected, pass_rules = covenant_engine.filter_passes(selected)
        rules_applied.extend(pass_rules)
        return covenant_engine, selected, rules_applied

    def resolve_moss_model(model_id: str) -> str | None:
        normalized = str(model_id or "").strip()
        if normalized == "instruct":
            return config.instruct_model
        if normalized == "thinking":
            return config.thinking_model
        spec = find_model_spec("oida_moss", normalized)
        # Installed deployments can keep checkpoints outside REPO_ROOT/weights.
        # Prefer an explicitly configured local target of this exact catalogue
        # identity; an unrelated configured model must not satisfy the request.
        if spec is not None and spec.integration_status != "configured_alias":
            for configured in (config.instruct_model, config.thinking_model):
                configured_spec = find_model_spec("oida_moss", configured)
                if (
                    configured_spec is not None
                    and configured_spec.id == spec.id
                    and (Path(configured) / "config.json").is_file()
                ):
                    return configured
        selected = next(
            (
                item
                for item in available_models
                if str(item.get("name")) == normalized
                or str(item.get("path")) == normalized
            ),
            None,
        )
        if selected:
            return str(selected["path"])
        if spec is not None:
            local = next(
                (
                    item
                    for item in available_models
                    if str(item.get("name", "")).lower()
                    in {
                        spec.id.rsplit("/", 1)[-1].lower(),
                        *(alias.lower() for alias in spec.aliases),
                    }
                ),
                None,
            )
            if local:
                return str(local["path"])
            if config.allow_hf_hub:
                return spec.id
        return None

    def moss_model_descriptors() -> list[ModelDescriptor]:
        runtime = engine.runtime_status()
        loaded_names = {
            Path(str(value)).name
            for value in runtime.get("loaded_models", [])
            if str(value).strip()
        }

        def configured_metadata(model_ref: str) -> dict[str, object]:
            path = Path(model_ref).expanduser()
            installed = path.is_dir() and (path / "config.json").exists()
            name = path.name
            return {
                "configured": True,
                "installed": installed,
                "available": installed or config.profile == "cuda-server",
                "loaded": name in loaded_names,
                **({"path": str(path)} if installed else {}),
            }

        aliases = [
            ModelDescriptor(
                id="instruct",
                provider_id="oida_moss",
                name=f"MOSS-Audio Instruct · {Path(config.instruct_model).name}",
                capabilities=["audio", "perception", "fast_perception"],
                locality="local",
                metadata={
                    "role": "fast_perception",
                    **configured_metadata(config.instruct_model),
                },
            ),
            ModelDescriptor(
                id="thinking",
                provider_id="oida_moss",
                name=f"MOSS-Audio Thinking · {Path(config.thinking_model).name}",
                capabilities=[
                    "audio",
                    "perception",
                    "deep_perception",
                    "targeted_relisten",
                ],
                locality="local",
                metadata={
                    "role": "deep_perception",
                    **configured_metadata(config.thinking_model),
                },
            ),
        ]
        checkpoints = [
            ModelDescriptor(
                id=(
                    spec.id
                    if (spec := find_model_spec("oida_moss", str(item["name"])))
                    is not None
                    else str(item["name"])
                ),
                provider_id="oida_moss",
                name=(spec.name if spec is not None else str(item["name"])),
                capabilities=[
                    "audio",
                    "perception",
                    "deep_perception"
                    if item.get("kind_hint") == "thinking"
                    else "fast_perception",
                    *(
                        ["targeted_relisten"]
                        if item.get("kind_hint") == "thinking"
                        else []
                    ),
                ],
                locality="local",
                metadata={
                    "size_gb": item.get("size_gb"),
                    "kind_hint": item.get("kind_hint"),
                    "description": item.get("description"),
                    "installed": True,
                    "available": True,
                    "loaded": str(item.get("name") or "") in loaded_names,
                    "path": str(item.get("path") or ""),
                },
            )
            for item in available_models
            if str(item.get("name")) not in {"instruct", "thinking"}
        ]
        return [*aliases, *checkpoints]

    def reasoning_registry(settings=None):
        selected_settings = settings or reasoning_settings.load()
        registry = build_provider_registry(
            selected_settings,
            secret_store=reasoning_secrets,
            moss_models=moss_model_descriptors(),
            moss_available=(
                config.profile == "cuda-server"
                or (config.profile != "stub" and bool(available_models))
            ),
        )
        registry.trial_budget = reasoning_budget
        return registry

    reasoning = ReasoningOrchestrator(
        settings_store=reasoning_settings,
        secret_store=reasoning_secrets,
        conversations=conversations,
        memory=memory,
        listening_identity_store=listening_identity_store,
        relistener=TargetedRelistener(
            engine,
            covenant_store,
        ),
        registry_factory=reasoning_registry,
    )
    openrouter_oauth = OpenRouterOAuth(reasoning_secrets)

    def engine_status() -> dict[str, Any]:
        runtime = engine.runtime_status()
        with engine_monitor_lock:
            if config.profile == "mac-mps":
                if runtime.get("loaded_models"):
                    engine_monitor["state"] = "ready"
                elif engine_monitor["state"] == "ready":
                    # A previous warm-up timestamp is not proof of residency.
                    # Model switching or a failed load can leave the runtime
                    # empty; expose that as cold so launch recovery runs again.
                    engine_monitor["state"] = "cold"
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
                "instruct_model": assignments.get("instruct")
                or (
                    Path(config.instruct_model).name if config.instruct_model else None
                ),
                "thinking_model": assignments.get("thinking")
                or (
                    Path(config.thinking_model).name if config.thinking_model else None
                ),
                "available_models": available_models,
                "role_application_notes": list(perception_role_notes),
            }

    def enrich_music_id(
        event: dict[str, Any],
        path: Path,
        *,
        preset_id: str,
        requested: bool,
        withheld_reason: str | None = None,
    ) -> None:
        """Attach an opt-in ShazamIO result directly to a listening event.

        Missing dependencies, network errors, and no-match responses stay as
        visible metadata and never suppress the descriptive listening result.
        """
        if not requested or preset_id != "music":
            return
        if withheld_reason:
            event["music_id"] = {
                "provider": "withheld",
                "matched": False,
                "note": withheld_reason,
            }
            return
        result = identify_song(path, enabled=True)
        event["music_id"] = result
        if result.get("matched"):
            tags = event.setdefault("tags", [])
            if isinstance(tags, list) and "music-id" not in tags:
                tags.append("music-id")

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
                engine_monitor["warmed_ms"] = round(
                    (time.perf_counter() - started) * 1000
                )
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
        threading.Thread(
            target=_prewarm_engine,
            args=(model_kind,),
            name="oida-moss-prewarm",
            daemon=True,
        ).start()
        return True

    source_shutdown_callbacks = []
    from oida.operation_control import Operations, checkpoint

    operations = Operations(owner_journal)
    source_shutdown_callbacks.append(operations.close)

    @asynccontextmanager
    async def lifespan(_app: Any):
        async with AsyncExitStack() as stack:
            if mcp_session_manager is not None:
                await stack.enter_async_context(mcp_session_manager.run())
            broadcaster.bind_loop(asyncio.get_running_loop())
            if navigator_watcher is not None and navigator_load_settings is not None:
                try:
                    navigator_settings = navigator_load_settings().get("watcher") or {}
                    if os.getenv(
                        "AKOUSMATA_WATCHER", "1"
                    ) != "0" and navigator_settings.get("enabled", True):
                        navigator_watcher.start(
                            ingest_seconds=float(
                                navigator_settings.get("ingest_seconds", 60)
                            ),
                            lint_minutes=float(
                                navigator_settings.get("lint_minutes", 30)
                            ),
                        )
                except Exception as exc:
                    LOGGER.warning("akousmata watcher startup failed: %s", exc)
            if config.prewarm:
                start_prewarm()
            reasoning_workspace.start()
            try:
                yield
            finally:
                for stop_sources in source_shutdown_callbacks:
                    stop_sources()
                if navigator_watcher is not None:
                    try:
                        navigator_watcher.stop()
                    except Exception as exc:
                        LOGGER.warning("akousmata watcher shutdown failed: %s", exc)
                # Honor the default delete_after_session native-temp retention policy on shutdown.
                try:
                    finalize_native_temp_audio_session(
                        background.config.native_temp_audio_retention
                    )
                except Exception as exc:
                    LOGGER.warning("native temp-audio shutdown cleanup failed: %s", exc)
                try:
                    finalize_upload_audio_session(
                        background.config.upload_audio_retention
                    )
                except Exception as exc:
                    LOGGER.warning("upload-audio shutdown cleanup failed: %s", exc)

    from oida import __version__

    error_response = {
        "description": "The request could not be completed.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"detail": {}},
                    "required": ["detail"],
                }
            },
        },
    }
    html_error_response = {
        400: {
            "description": "The browser authorization callback failed.",
            "content": {"text/html": {"schema": {"type": "string"}}},
        }
    }
    event_stream_response = {
        200: {
            "description": "A server-sent event stream.",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "event": {"type": "string"},
                            "data": {"type": "string"},
                        },
                        "required": ["data"],
                    }
                }
            },
        }
    }
    documented_error_codes = (400, 401, 403, 404, 409, 413, 422, 423, 500, 501, 503)
    app = FastAPI(
        title="oida",
        version=__version__,
        lifespan=lifespan,
        responses={
            code: copy.deepcopy(error_response) for code in documented_error_codes
        },
    )

    workspace_id = os.getenv("LISTENINGSTACK_WORKSPACE_ID")
    workspace_generation = os.getenv("LISTENINGSTACK_WORKSPACE_GENERATION")
    workspace_binding = hashlib.sha256(
        json.dumps(
            [
                "oida",
                workspace_id,
                workspace_generation,
                str(config.data_dir.resolve()),
                str(config.audio_dir.resolve()),
                str(config.trial_dir.resolve()),
                str(Path(os.getenv("AKOUSMATA_PATH", "")).expanduser().resolve()),
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    @app.middleware("http")
    async def _workspace_admission(request: Request, call_next: Any) -> Any:
        if (
            workspace_id
            and workspace_generation
            and request.method.upper()
            not in {
                "GET",
                "HEAD",
                "OPTIONS",
            }
        ):
            supplied = (
                request.headers.get("x-centaur-workspace"),
                request.headers.get("x-centaur-generation"),
                request.headers.get("x-centaur-binding"),
            )
            expected = (workspace_id, workspace_generation, workspace_binding)
            if supplied != expected:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": "oida refused a stale or mismatched workspace binding"
                    },
                )
        return await call_next(request)

    @app.get("/owner/identity")
    def owner_identity() -> dict[str, object]:
        return {
            "contract": "centaur/owner-identity/v1",
            "owner": "oida",
            "mode": "workspace" if workspace_id and workspace_generation else "legacy",
            "workspace_id": workspace_id,
            "generation": workspace_generation,
            "binding": workspace_binding,
            "pid": os.getpid(),
        }

    @app.exception_handler(EngineUnavailable)
    async def engine_unavailable_handler(
        _request: Request, exc: EngineUnavailable
    ) -> JSONResponse:
        LOGGER.warning("audio engine unavailable: %s", exc)
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    if mcp_http_app is not None:
        app.mount("/mcp", mcp_http_app, name="oida-mcp")

    # The complete Akousmata navigator is part of the Oída distribution. It is
    # mounted rather than forked, so standalone and embedded use share one app,
    # one store, and one watcher implementation.
    try:
        from akousmata_app import watcher as _navigator_watcher
        from akousmata_app.server import app as navigator_app
        from akousmata_app.settings import (
            ensure_human_profile as _navigator_ensure_human_profile,
        )
        from akousmata_app.settings import load as _navigator_load_settings

        navigator_watcher = _navigator_watcher
        navigator_load_settings = _navigator_load_settings
        navigator_ensure_human_profile = _navigator_ensure_human_profile
        app.mount("/library", navigator_app, name="akousmata-navigator")
    except ImportError as exc:
        LOGGER.warning("akousmata navigator is unavailable: %s", exc)

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

    def human_account_profile(
        listener_id: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, str]:
        """Resolve Oída's human account against the embedded library profile.

        The default path uses Akousmata's stable, private local ownership id so
        an account created in Oída is editable in the mounted library. Explicit
        request or environment overrides remain possible for external clients.
        """
        profile: dict[str, str] = {}
        if navigator_ensure_human_profile is not None:
            try:
                profile = dict(navigator_ensure_human_profile())
            except (OSError, ValueError, TypeError) as exc:
                LOGGER.warning("could not resolve the Akousmata human profile: %s", exc)
        env_listener = str(os.getenv("OIDA_HUMAN_LISTENER_ID") or "").strip()
        env_display = str(os.getenv("OIDA_HUMAN_DISPLAY_NAME") or "").strip()
        requested_listener = str(listener_id or "").strip()
        requested_display = str(display_name or "").strip()
        resolved_listener = (
            requested_listener
            or env_listener
            or str(profile.get("listener_id") or "").strip()
            or "oida-local-human"
        )
        has_display_override = bool(requested_display or env_display)
        resolved_display = (
            requested_display
            or env_display
            or str(profile.get("display_name") or "").strip()
        )
        uses_local_profile = (
            not requested_listener
            and not env_listener
            and resolved_listener == str(profile.get("listener_id") or "").strip()
        )
        privacy = (
            str(profile.get("privacy") or "private")
            if uses_local_profile and not has_display_override
            else "shared"
            if has_display_override
            else "private"
        )
        return {
            "listener_id": resolved_listener,
            "display_name": resolved_display,
            "privacy": privacy if privacy in {"private", "shared"} else "private",
        }

    wildcard_bind = str(config.host) in {"0.0.0.0", "::", ""}
    if wildcard_bind and not config.auth_token:
        raise RuntimeError(
            "Refusing to bind oida on a wildcard host without OIDA_AUTH_TOKEN (or legacy HMM_/AEAR_AUTH_TOKEN). "
            "Use 127.0.0.1 for tokenless local operation."
        )
    if wildcard_bind:
        LOGGER.warning(
            "oida is bound to %s; bearer-token auth is required and loopback Host protection is relaxed.",
            config.host,
        )
    allowed_hostnames = set(_LOOPBACK_HOSTNAMES)
    if config.host:
        allowed_hostnames.add(str(config.host).strip().lower())
    allowed_hostnames.update(config.trusted_hosts)

    @app.middleware("http")
    async def _strict_json_numbers(request: Request, call_next: Any) -> Any:
        content_type = str(request.headers.get("content-type") or "").lower()
        if "json" in content_type:
            body = await request.body()
            # Python's JSON decoder accepts NaN/Infinity by default, while the
            # response encoder correctly refuses them. Reject only suspected
            # payloads here; valid JSON strings containing those words still
            # pass the strict parse.
            if body and (b"NaN" in body or b"Infinity" in body):
                try:
                    json.loads(body, parse_constant=_reject_json_constant)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "JSON numbers must be finite"},
                    )
        return await call_next(request)

    @app.middleware("http")
    async def _loopback_guard(request: Request, call_next: Any) -> Any:
        if config.auth_token:
            auth_header = request.headers.get("authorization", "")
            scheme, _, token = auth_header.partition(" ")
            # Compare as bytes: str compare_digest raises TypeError on non-ASCII
            # input, which would turn a malformed header into a 500 instead of 401.
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                token.encode("utf-8", "surrogateescape"),
                config.auth_token.encode("utf-8", "surrogateescape"),
            ):
                return JSONResponse(
                    status_code=401, content={"detail": "valid bearer token required"}
                )
        # In localhost mode, refuse non-loopback Host headers (DNS-rebinding) and
        # cross-origin browser requests (CSRF). Wildcard/LAN mode is allowed only
        # when bearer-token auth is configured above.
        if not wildcard_bind:
            host_header = request.headers.get("host", "")
            if host_header and _hostname_only(host_header) not in allowed_hostnames:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "oida is local-first; requests with a non-loopback Host header are refused."
                    },
                )
            origin = request.headers.get("origin")
            if origin and (
                origin == "null" or _hostname_only(origin) not in allowed_hostnames
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "cross-origin requests are refused; the oida daemon serves only its own dashboard."
                    },
                )
        return await call_next(request)

    def require_local_admin(request: Request) -> None:
        host = _hostname_only(request.headers.get("host", ""))
        client_host = str(request.client.host if request.client else "").lower()
        if host not in _LOOPBACK_HOSTNAMES or client_host not in {
            *_LOOPBACK_HOSTNAMES,
            "testclient",
        }:
            raise HTTPException(
                status_code=403,
                detail="reasoning settings, credentials, and OAuth may only be changed from this computer",
            )

    def analyze_capture(
        capture: dict[str, object],
        preset_id: str,
        privacy_mode: str = "ephemeral",
        enabled_skill_ids: list[str] | None = None,
        disabled_skill_ids: list[str] | None = None,
        song_id: bool = False,
    ) -> dict[str, object]:
        preset = route_preset(preset_id)
        path = str(capture["path"])
        covenant_engine, passes, covenant_rules_applied = prepare_covenant_perception(
            path,
            source_type="buffer",
            passes=preset.moss_passes,
        )
        covenant_withheld: list[dict[str, object]] = []
        broadcaster.publish(
            "listen_started",
            {"path": path, "route_preset": preset.id, "source": "live-capture"},
        )
        with engine.request_policy(
            privacy_mode=privacy_mode,
            covenant_engine=covenant_engine,
        ) as audio_policy:
            perception = report(
                engine,
                path,
                "oida-live-capture",
                passes=passes,
                chunk_seconds=config.moss_chunk_seconds,
                overlap_seconds=_chunk_overlap(config),
            )
        perception_dict = report_to_dict(perception)
        if covenant_engine is not None:
            perception_dict, perception_withheld = covenant_engine.redact_perception(
                perception_dict
            )
            covenant_withheld.extend(perception_withheld)
        command_output = build_harness_output(
            perception_dict, command=preset.akouo_command
        )
        if covenant_engine is not None:
            command_output, claim_withheld = covenant_engine.redact_command_output(
                command_output
            )
            covenant_withheld.extend(claim_withheld)
        event = listening_event_dict(
            perception_dict,
            command_output=command_output,
            segment=capture["segment"],
            route_preset_id=preset.id,
            enabled_skill_ids=enabled_skill_ids,
            disabled_skill_ids=disabled_skill_ids,
            privacy_mode=_privacy_mode(privacy_mode),
            raw_audio_policy="temp",
            listening_identity=audio_policy.listening_identity_block(passes),
            # Compared against event labels so an orientation cannot become
            # the account's title. Never stored: the block above is a digest.
            identity_text=audio_policy.listening_identity.text,
        )
        song_identity_withheld = None
        if (
            song_id
            and preset.id == "music"
            and covenant_engine is not None
            and covenant_engine.forbids_song_identity()
        ):
            song_identity_withheld = (
                f"Song identity withheld under covenant {covenant_engine.covenant.id}."
            )
            covenant_withheld.append(
                {"rule": "do_not_reveal", "subject": "song-identity", "count": 1}
            )
        enrich_music_id(
            event,
            Path(path),
            preset_id=preset.id,
            requested=song_id,
            withheld_reason=song_identity_withheld,
        )
        if covenant_engine is not None:
            event["covenant"] = covenant_engine.event_block(
                rules_applied=covenant_rules_applied,
                withheld=covenant_withheld,
            )
        event = memory.enrich_event(event)
        # Session ownership is daemon state and must be attached before the
        # completion event reaches any UI. That keeps dashboard, floating
        # listener, and history subscribers on the same result object.
        background.finish_action(event)
        broadcaster.publish(
            "listen_completed", {"listening_event": event, "route_preset": preset.id}
        )
        return {
            **capture,
            "listening_event": event,
            "perception_report": perception_dict,
            "command_output": command_output,
        }

    def remember_event(
        event: dict[str, Any],
        *,
        tags: list[str] | None = None,
        native_analysis=None,
        user_notes: str | None = None,
        human_listener_id: str | None = None,
        human_display_name: str | None = None,
        declared_lineage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write one result through Oida's compatibility trace and the shared
        Akousmata store. The UI calls both simply "Memory"; callers still get
        the legacy trace id while newer surfaces can navigate the shared record.
        """
        memory_block = (
            event.get("memory") if isinstance(event.get("memory"), dict) else {}
        )
        event["memory"] = memory_block
        trace = memory.remember(event, user_notes=user_notes, tags=tags)
        memory_block["saved_trace_id"] = trace["id"]
        akousma_id: str | None = None
        human_akousma_id: str | None = None
        shared_error: str | None = None
        human_error: str | None = None
        try:
            segment = (
                event.get("segment") if isinstance(event.get("segment"), dict) else {}
            )
            data_ref = (
                segment.get("data_ref")
                if isinstance(segment.get("data_ref"), dict)
                else {}
            )
            source = (
                event.get("source") if isinstance(event.get("source"), dict) else {}
            )
            aggregate = (
                event.get("aggregate")
                if isinstance(event.get("aggregate"), dict)
                else {}
            )
            session = (
                event.get("session") if isinstance(event.get("session"), dict) else {}
            )
            audio: dict[str, Any] = {
                "asset_id": str(
                    segment.get("id") or event.get("id") or new_id("asset")
                ),
                "type": "capture",
            }
            sha256 = data_ref.get("sha256")
            if sha256:
                audio["content_hash"] = f"sha256:{sha256}"
            if isinstance(segment.get("duration_ms"), (int, float)):
                audio["duration_seconds"] = float(segment["duration_ms"]) / 1000
            if isinstance(segment.get("sample_rate"), (int, float)):
                audio["sample_rate"] = int(segment["sample_rate"])
            if isinstance(segment.get("channels"), (int, float)):
                audio["channels"] = int(segment["channels"])
            # Shared memory keeps derived listening by default. A durable or
            # externally referenced path may travel with it; temporary capture
            # paths are deliberately not promoted into durable memory.
            uri = str(data_ref.get("uri") or "")
            if uri and event.get("raw_audio_policy") in {"saved", "external_ref"}:
                audio["uri"] = uri if "://" in uri else f"file://{uri}"

            routes = (
                event.get("routes") if isinstance(event.get("routes"), list) else []
            )
            event_tags = (
                event.get("tags") if isinstance(event.get("tags"), list) else []
            )
            listening: dict[str, Any] = {
                "oida.listen": {
                    "contract": event.get("contract") or "oida/listening-event/v0.3",
                    "summary": aggregate.get("short_summary") or aggregate.get("title"),
                    "title": aggregate.get("title"),
                    "event_id": event.get("id"),
                    "features": event.get("features") or {},
                    "listening_context": event.get("listening_context") or {},
                    "listening_provenance": event.get("listening_provenance") or {},
                    "source_admission": (event.get("source") or {})
                    .get("details", {})
                    .get("source_admission"),
                    "file_window": (segment.get("metadata") or {}).get("file_window"),
                    "live_window": (segment.get("metadata") or {}).get("live_window"),
                    "pass_provenance": event.get("pass_provenance") or [],
                    "specialist_evidence": event.get("specialist_evidence") or [],
                    "listening_task_status": event.get("listening_task_status") or [],
                    "listening_passes": event.get("listening_passes") or [],
                    "route_decisions": event.get("route_decisions") or [],
                    "apparatus": event.get("apparatus") or {},
                    "disagreements": event.get("disagreements") or [],
                    "routes": [
                        {
                            "route_id": route.get("route_id"),
                            "summary": route.get("summary"),
                        }
                        for route in routes
                        if isinstance(route, dict)
                    ],
                }
            }
            for lane in event.get("specialist_evidence") or []:
                if lane.get("status") == "complete":
                    listening["oida.specialist." + lane["task"]] = {
                        "summary": lane["model"]
                        + ": "
                        + lane["evidence"]["result"]["status"],
                        "specialist_evidence": lane["evidence"],
                        "analysis_frame": lane["frame"],
                        "source_sha256": lane["source_sha256"],
                    }
            for index, route in enumerate(routes):
                if not isinstance(route, dict):
                    continue
                structured = route.get("structured")
                if not isinstance(structured, dict):
                    continue
                route_id = str(route.get("route_id") or f"route-{index + 1}")
                namespace = f"akouo.{route_id}"
                if namespace in listening:
                    namespace = f"{namespace}.{index + 1}"
                listening[namespace] = {
                    "route_id": route_id,
                    "route_name": route.get("route_name"),
                    "summary": route.get("summary"),
                    **structured,
                }
            from .akousma_bridge import (
                build_akousma_from_listen,
                build_human_response_akousma,
                persist_akousma,
            )

            origin = {
                "live_input": "live-input",
                "buffer": "live-input",
                "system_output": "system-output",
                "external_stream": "system-output",
                "generated": "generated",
            }.get(str(source.get("type") or ""), "file")
            record = build_akousma_from_listen(
                audio=audio,
                listening=listening,
                origin=origin,
                lineage=declared_lineage,
                device=str(source.get("label") or "") or None,
                session_id=str(session.get("id") or "") or None,
                tags=[
                    *(str(tag) for tag in event_tags if tag),
                    *(str(tag) for tag in (tags or []) if tag),
                ],
                summary=str(
                    aggregate.get("short_summary") or aggregate.get("title") or ""
                )
                or None,
                capture=event.get("capture")
                if isinstance(event.get("capture"), dict)
                else None,
                covenant=event.get("covenant")
                if isinstance(event.get("covenant"), dict)
                else None,
                listening_identity=(
                    event.get("listening_identity")
                    if isinstance(event.get("listening_identity"), dict)
                    else None
                ),
                disagreements=(
                    event.get("disagreements")
                    if isinstance(event.get("disagreements"), list)
                    else None
                ),
                actions=[
                    {
                        "action_id": f"remember_{trace['id']}",
                        "proposal": "Remember this listening in the local Akousmata store.",
                        "status": "executed",
                        "authority": {
                            "mode": "execute_scoped",
                            "scopes": ["memory.remember"],
                            "granted_by": "explicit remember request",
                            "expires_at": None,
                            "requires_confirmation": False,
                            "reversible": True,
                        },
                        "receipt": {
                            "created_at": str(trace.get("createdAt") or "unknown"),
                            "actor": "oida",
                            "result": str(trace["id"]),
                            "recovery": "memory.forget",
                        },
                    }
                ],
            )
            if native_analysis is not None:
                from oida.station_aperture import persist as persist_native

                akousma_id = persist_native(record, native_analysis)
            else:
                akousma_id = persist_akousma(record)
            owner_journal.record_reference(
                akousma_id, event_id=event.get("id"), record=record
            )
            memory_block["akousma_id"] = akousma_id
            if isinstance(user_notes, str) and user_notes.strip():
                try:
                    human_profile = human_account_profile(
                        human_listener_id,
                        human_display_name,
                    )
                    human_record = build_human_response_akousma(
                        record,
                        akousma_id,
                        user_notes,
                        listener_id=human_profile["listener_id"],
                        display_name=human_profile["display_name"] or None,
                        privacy=human_profile["privacy"],
                    )
                    human_akousma_id = persist_akousma(human_record)
                    owner_journal.record_reference(
                        human_akousma_id, event_id=event.get("id"), record=human_record
                    )
                    memory_block["human_akousma_id"] = human_akousma_id
                except Exception as exc:
                    human_error = "linked human Akousmata write failed"
                    LOGGER.warning("linked human Akousmata write failed: %s", exc)
        except Exception as exc:
            # Compatibility memory is already durable at this point. A missing
            # or malformed optional shared-Akousmata installation must not turn
            # Remember into a failed request or lose the local trace.
            shared_error = "shared Akousmata memory write failed"
            LOGGER.warning("shared Akousmata memory write failed: %s", exc)
        return {
            "trace": trace,
            "event": event,
            "akousma_id": akousma_id,
            "human_akousma_id": human_akousma_id,
            "shared_error": shared_error,
            "human_error": human_error,
        }

    def find_listening_event(event_id: str) -> dict[str, Any] | None:
        normalized = str(event_id or "").strip()
        if not normalized:
            return None
        candidates: list[Any] = [
            background.state.latest_event,
            *background.state.pinned_events,
            *background.state.recent_events,
        ]
        active = background.state.active_session
        if isinstance(active, dict):
            candidates.extend(active.get("events") or [])
        for archived in background.state.archived_sessions:
            if isinstance(archived, dict):
                candidates.extend(archived.get("events") or [])
        for candidate in candidates:
            if (
                isinstance(candidate, dict)
                and str(candidate.get("id") or "") == normalized
            ):
                return dict(candidate)
        summaries = conversations.list(event_id=normalized, limit=1)
        if summaries:
            try:
                stored = conversations.get(str(summaries[0]["id"]))
            except (FileNotFoundError, ValueError):
                return None
            event = stored.get("event")
            return dict(event) if isinstance(event, dict) else None
        return None

    def apply_current_conversation_covenant(event: dict[str, Any]) -> dict[str, Any]:
        active = covenant_store.active()
        if active is None:
            return event
        governed = copy.deepcopy(event)
        historical = (
            governed.get("covenant")
            if isinstance(governed.get("covenant"), dict)
            else {}
        )
        block = active.reference()
        applied = {
            str(value)
            for value in historical.get("rules_applied", [])
            if isinstance(value, str)
        }
        withheld = [
            dict(value)
            for value in historical.get("withheld", [])
            if isinstance(value, dict)
        ]
        for rule in active.rules:
            verb = str(rule.get("verb") or "")
            subjects = [str(value) for value in rule.get("subjects") or []]
            if verb not in {"do_not_reveal", "do_not_retain", "ignore", "coarsen"}:
                continue
            for subject in subjects:
                applied.add(f"{verb}:{subject}")
                if verb == "do_not_reveal":
                    withheld.append({"rule": verb, "subject": subject, "count": 1})
                elif verb == "ignore":
                    mapped = (
                        ["speech", "transcript", "speaker-identity"]
                        if subject == "speech"
                        else [subject]
                    )
                    withheld.extend(
                        {"rule": verb, "subject": value, "count": 1} for value in mapped
                    )
                elif verb == "coarsen":
                    # Old free-form claims cannot be rounded reliably. Carry
                    # the rule and withhold location prose; structured event
                    # coordinates were already excluded from evidence packets.
                    withheld.append({"rule": verb, "subject": subject, "count": 1})
        if applied:
            block["rules_applied"] = sorted(applied)
        if withheld:
            unique: dict[tuple[str, str], dict[str, Any]] = {}
            for item in withheld:
                key = (str(item.get("rule") or ""), str(item.get("subject") or ""))
                if key != ("", ""):
                    unique[key] = item
            block["withheld"] = list(unique.values())
        governed["covenant"] = block
        return governed

    def conversation_event(req: ConversationAskRequest) -> dict[str, Any]:
        event: dict[str, Any] | None = None
        if req.conversation_id:
            try:
                stored = conversations.get(req.conversation_id)
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown conversation: {req.conversation_id}",
                ) from exc
            candidate = stored.get("event") if isinstance(stored, dict) else None
            event = dict(candidate) if isinstance(candidate, dict) else None
            supplied_id = req.event_id
            if supplied_id is None and isinstance(req.event, dict):
                supplied_id = str(req.event.get("id") or "") or None
            anchor_id = str(
                stored.get("anchor_event_id") or stored.get("event_id") or ""
            )
            if supplied_id and str(supplied_id) != anchor_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"conversation {req.conversation_id} is anchored to event {anchor_id}",
                )
            if isinstance(req.event, dict):
                supplied_policy = str(
                    req.event.get("raw_audio_policy") or "external_ref"
                )
                supplied_snapshot = redact_event_audio_for_policy(
                    dict(req.event), supplied_policy
                )
                if _stable_json_hash(supplied_snapshot) != _stable_json_hash(
                    event or {}
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"conversation {req.conversation_id} is anchored to an immutable "
                            "listening-event snapshot"
                        ),
                    )
        elif req.event_id:
            event = find_listening_event(req.event_id)
            if event is None:
                raise HTTPException(
                    status_code=404, detail=f"unknown listening event: {req.event_id}"
                )
        elif isinstance(req.event, dict):
            supplied = dict(req.event)
            supplied_id = str(supplied.get("id") or "").strip()
            # Full event payloads remain a compatibility ingress for hosts,
            # but once this daemon knows an event id its canonical snapshot is
            # authoritative. Caller text cannot replace listening evidence.
            event = find_listening_event(supplied_id) if supplied_id else None
            if event is None:
                event = supplied
        elif isinstance(background.state.latest_event, dict):
            event = dict(background.state.latest_event)
        if event is None:
            raise HTTPException(
                status_code=400, detail="conversation requires a listening event"
            )
        event = apply_current_conversation_covenant(event)
        if background.config.incognito:
            event = dict(event)
            event["privacy_mode"] = "incognito"
        return event

    def comparison_events(
        ids: list[str], primary_event_id: object
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in ids[:3]:
            event_id = str(raw or "").strip()
            if not event_id or event_id == str(primary_event_id) or event_id in seen:
                continue
            event = find_listening_event(event_id)
            if event is None:
                raise HTTPException(
                    status_code=404, detail=f"unknown comparison event: {event_id}"
                )
            seen.add(event_id)
            # The active covenant governs every event entering this turn, not
            # only the primary anchor. Otherwise a comparison could reintroduce
            # transcript or other material currently withheld from the user.
            values.append(apply_current_conversation_covenant(event))
        return values

    def conversation_options(
        req: ConversationAskRequest,
        comparisons: list[dict[str, Any]],
    ) -> TurnOptions:
        provider_id = req.provider_id
        if provider_id is None and (
            req.allow_remote_model or req.provider != "local_structured"
        ):
            provider_id = req.provider
        return TurnOptions(
            provider_id=provider_id,
            model_id=req.model_id,
            profile_id=req.profile_id,
            conversation_id=req.conversation_id,
            comparison_events=comparisons,
            include_memory=req.include_memory,
            include_transcript=req.include_transcript,
            include_memory_content=req.include_memory_content,
            allow_targeted_relisten=req.allow_targeted_relisten,
        )

    def apply_perception_roles(settings) -> list[str]:
        notes: list[str] = []
        if str(getattr(local_engine, "profile", "")) == "stub":
            return notes
        for role, model_kind in (
            (ModelRole.FAST_PERCEPTION, "instruct"),
            (ModelRole.DEEP_PERCEPTION, "thinking"),
            (ModelRole.TRANSCRIPTION, "transcription"),
            (ModelRole.MUSIC_ANALYSIS, "music"),
            (ModelRole.TARGETED_RELISTEN, "targeted_relisten"),
        ):
            assignment = settings.roles[role]
            if assignment.provider_id != "oida_moss" or not assignment.model_id:
                continue
            resolved = resolve_moss_model(assignment.model_id)
            if resolved is None:
                notes.append(
                    f"{role.value}: unknown local MOSS model {assignment.model_id}"
                )
                fallback_model = (
                    config.thinking_model
                    if model_kind in {"thinking", "music", "targeted_relisten"}
                    else config.instruct_model
                )
                try:
                    engine.set_model(model_kind, fallback_model)
                except ValueError:
                    pass
                continue
            try:
                engine.set_model(model_kind, resolved)
            except ValueError:
                LOGGER.exception("reasoning role assignment failed for %s", role.value)
                notes.append(f"{role.value}: local model assignment failed")
        return notes

    def record_perception_roles(settings) -> list[str]:
        try:
            notes = apply_perception_roles(settings)
        except Exception:
            LOGGER.exception("reasoning perception role application failed")
            notes = ["perception roles: assignment unavailable"]
        with engine_monitor_lock:
            perception_role_notes[:] = notes
        for note in notes:
            LOGGER.warning("reasoning role assignment was not applied: %s", note)
        return list(notes)

    # Apply durable role choices during daemon construction, before any listen
    # request or optional model prewarm can use the config defaults.
    record_perception_roles(reasoning_settings.load())

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "name": "oida",
            "pid": os.getpid(),
            "legacy_name": "hmm, aear",
            "profile": config.profile,
            "host": config.host,
            "port": config.port,
            "data_dir": str(config.data_dir),
            "audio_dir": str(config.audio_dir),
            "trial_id": reasoning_budget.trial_id,
            "auth_required": bool(config.auth_token),
            "allow_hf_hub": config.allow_hf_hub,
            "hf_hub_offline": config.hf_hub_offline,
            "engine": engine_status(),
            "sonicfield": {
                "available": sonicfield.available,
                "root": str(sonicfield.root) if sonicfield.root else None,
            },
            "music_id": {
                "provider": "shazamio",
                "available": importlib.util.find_spec("shazamio") is not None,
                "default_enabled": False,
            },
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
        if kind not in {
            "instruct",
            "thinking",
            "transcription",
            "music",
            "targeted_relisten",
        }:
            raise HTTPException(
                status_code=400,
                detail="model_kind must be instruct, thinking, transcription, music, or targeted_relisten",
            )
        selected = next(
            (
                item
                for item in available_models
                if item["name"] == req.model or item["path"] == req.model
            ),
            None,
        )
        if selected is None:
            valid = (
                ", ".join(str(item["name"]) for item in available_models)
                or "none found in weights/"
            )
            raise HTTPException(
                status_code=400,
                detail=f"unknown MOSS model: {req.model}. Available: {valid}",
            )
        try:
            engine.set_model(kind, str(selected["path"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        started = start_prewarm(kind)
        status = engine_status()
        broadcaster.publish("engine", status)
        return {"assigned": {kind: selected["name"]}, "warming": started, **status}

    @app.get("/events/stream", responses=event_stream_response)
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
                raise ValueError(
                    "provide a listening event or a query to explore the Sonic Field"
                )
            limit = max(1, min(int(req.limit_per_surface), 12))
            result = sonicfield.explore(terms, limit_per_surface=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**result, "status": sonicfield.status()}

    @app.post("/sonicfield/reveal")
    def sonicfield_reveal_endpoint(req: SonicFieldRevealRequest) -> dict[str, object]:
        if not sonicfield.available or sonicfield.root is None:
            raise HTTPException(
                status_code=400, detail="Sonic Field root is not available"
            )
        target = Path(req.path).expanduser().resolve()
        # startswith(str) would accept sibling dirs like ".../sonicfield-evil"
        if not target.is_relative_to(sonicfield.root):
            raise HTTPException(
                status_code=400, detail="path is outside the Sonic Field root"
            )
        if not target.exists():
            raise HTTPException(status_code=404, detail="path does not exist")
        try:
            open_executable = Path("/usr/bin/open")
            if not open_executable.is_file():
                raise HTTPException(
                    status_code=501,
                    detail="revealing files is unavailable on this platform",
                )
            subprocess.run(
                [str(open_executable), "-R", str(target)], check=False, timeout=5
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning("Sonic Field reveal failed: %s", exc)
            raise HTTPException(
                status_code=500, detail="could not reveal the selected path"
            ) from exc
        return {"revealed": str(target)}

    @app.get("/", response_class=HTMLResponse)
    def root() -> FileResponse:
        # no-cache: without it browsers reuse a heuristically-cached dashboard
        # after an upgrade (assets are ?v= versioned, the document is not)
        return FileResponse(
            static_dir / "index.html", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(
            static_dir / "icons" / "favicon.svg", media_type="image/svg+xml"
        )

    @app.get("/remote", response_class=HTMLResponse)
    def remote_ear_page() -> FileResponse:
        # Phone-first capture surface. Oída serves the page but does not
        # configure or publish a machine-level remote-access service.
        return FileResponse(
            static_dir / "remote.html", headers={"Cache-Control": "no-cache"}
        )

    # ── the listening identity: data_dir()/LISTENING.md ──────────────────
    # Empty by default. This is an operator-authored perspective for model
    # attention and voice, not an evidence source or an enforceable covenant.

    @app.get("/listening")
    def listening_identity_status() -> dict[str, object]:
        try:
            return listening_identity_store.status()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/listening")
    def listening_identity_save(
        body: ListeningIdentitySaveRequest,
    ) -> dict[str, object]:
        try:
            listening_identity_store.save(
                body.text,
                expected_sha256=body.expected_sha256,
            )
        except ListeningIdentityConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        status = listening_identity_store.status()
        broadcaster.publish(
            "listening_identity_changed",
            {
                "filename": status["filename"],
                "active": status["active"],
                "sha256": status["sha256"],
            },
        )
        return status

    # ── the sovereignty layer: covenants (spec v1.3) ─────────────────────
    # Empty by default. Documents are plain local text under
    # data_dir()/covenants/; activating one turns the layer on for every
    # listen surface (dashboard, gateway, remote ear, MCP) until deactivated.

    @app.get("/covenant")
    def covenant_status() -> dict[str, object]:
        active = covenant_store.active()
        return {
            "active": active.to_dict() if active else None,
            "available": covenant_store.list(),
            "default": "no covenant — sovereignty is opted into, never imposed",
        }

    @app.get("/covenant/{name}")
    def covenant_read(name: str) -> dict[str, object]:
        text = covenant_store.read(name)
        if text is None:
            raise HTTPException(status_code=404, detail=f"no covenant named {name!r}")
        return {
            "name": name,
            "text": text,
            "parsed": parse_covenant(text, fallback_name=name).to_dict(),
        }

    @app.put("/covenant")
    def covenant_save(body: CovenantSaveRequest) -> dict[str, object]:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="a covenant needs a name")
        parsed = covenant_store.save(name, body.text)
        if body.activate:
            covenant_store.activate(name)
        broadcaster.publish(
            "covenant_changed", {"name": name, "active": covenant_store.active_name()}
        )
        return {
            "name": name,
            "parsed": parsed.to_dict(),
            "active": covenant_store.active_name(),
        }

    @app.post("/covenant/activate")
    def covenant_activate(body: CovenantActivateRequest) -> dict[str, object]:
        try:
            covenant_store.activate(body.name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        broadcaster.publish(
            "covenant_changed",
            {"name": body.name, "active": covenant_store.active_name()},
        )
        active = covenant_store.active()
        return {"active": active.to_dict() if active else None}

    @app.delete("/covenant/{name}")
    def covenant_delete(name: str) -> dict[str, object]:
        removed = covenant_store.delete(name)
        if not removed:
            raise HTTPException(status_code=404, detail=f"no covenant named {name!r}")
        broadcaster.publish(
            "covenant_changed", {"name": None, "active": covenant_store.active_name()}
        )
        return {"deleted": name, "active": covenant_store.active_name()}

    @app.get("/api")
    def api_root() -> dict[str, object]:
        hidden_paths = {
            "/",
            "/api",
            "/docs",
            "/docs/oauth2-redirect",
            "/favicon.ico",
            "/openapi.json",
            "/redoc",
            "/static",
        }
        endpoints = sorted(
            {
                path
                for route in app.routes
                if isinstance((path := getattr(route, "path", None)), str)
                and path not in hidden_paths
            }
        )
        return {
            "name": "oida",
            "legacy_name": "hmm, aear",
            "ok": True,
            "profile": config.profile,
            "docs": "/docs",
            "health": "/health",
            "endpoints": endpoints,
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

    @app.get("/gateway")
    def gateway_manifest_endpoint() -> dict[str, object]:
        return gateway_manifest(version=__version__)

    @app.get("/gateway/capabilities")
    def gateway_capabilities_endpoint() -> dict[str, object]:
        manifest = gateway_manifest(version=__version__)
        optional_components = copy.deepcopy(manifest.get("optional_components") or {})
        germ = (
            optional_components.get("germ")
            if isinstance(optional_components.get("germ"), dict)
            else {}
        )
        germ["bridge_available"] = any(
            getattr(route, "path", None) == "/germ/handoff" for route in app.routes
        )
        optional_components["germ"] = germ
        try:
            identity_status = listening_identity_store.status()
            listening_identity: dict[str, object] = {
                key: value
                for key, value in identity_status.items()
                if key not in {"text", "path"}
            }
        except ValueError:
            LOGGER.exception("listening identity status failed")
            listening_identity = {
                "filename": "LISTENING.md",
                "active": False,
                "error": "listening identity unavailable",
            }
        return {
            "contract": GATEWAY_CONTRACT,
            "gateway": manifest,
            "engine": engine_status(),
            "akouo": akouo_manifest(),
            "listening_identity": listening_identity,
            "memory": {
                "available": True,
                "trace_count": len(memory.list(limit=None)),
                "accounts": manifest["memory_accounts"],
            },
            "optional_components": optional_components,
            "host_perception_schema": "/gateway/schema/host-perception",
        }

    @app.get("/gateway/schema/host-perception")
    def gateway_host_schema_endpoint() -> dict[str, object]:
        path = REPO_ROOT / "oida" / "schemas" / "host-perception.schema.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/gateway/schema/listening-event")
    def gateway_listening_event_schema_endpoint() -> dict[str, object]:
        path = REPO_ROOT / "oida" / "schemas" / "listening-event.schema.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/gateway/schema/listening-context")
    def gateway_listening_context_schema_endpoint() -> dict[str, object]:
        from akouo_contract import schema_path

        path = schema_path("listening-context")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/gateway/schema/route-outcome")
    def gateway_route_outcome_schema_endpoint() -> dict[str, object]:
        path = REPO_ROOT / "oida" / "schemas" / "route-outcome.schema.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/gateway/route")
    def gateway_route_endpoint(req: GatewayRouteRequest) -> dict[str, object]:
        try:
            return {
                "contract": GATEWAY_CONTRACT,
                "routing_plan": routing_plan(
                    req.object_listened_to,
                    command=req.command,
                    evidence_level=req.evidence_level,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    def background_capture_request_endpoint(
        req: CaptureRequestBody,
    ) -> dict[str, object]:
        request = background.request_capture(
            seconds=req.seconds,
            route_preset=req.route_preset,
            direction=req.direction,
            source=req.source,
            enabled_skill_ids=req.enabled_skill_ids,
            song_id=req.song_id,
        )
        broadcaster.publish("capture_requested", request)
        return {"capture_request": request, "background": background.status()}

    @app.post("/background/capture-request/claim")
    def background_capture_request_claim_endpoint(
        req: CaptureRequestClaimBody,
    ) -> dict[str, object]:
        request = background.claim_capture_request(req.id)
        if request:
            broadcaster.publish("capture_claimed", request)
        return {"capture_request": request, "claimed": bool(request)}

    @app.post("/background/capture-request/cancel")
    def background_capture_request_cancel_endpoint(
        req: CaptureRequestClaimBody,
    ) -> dict[str, object]:
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

    @app.get("/sessions")
    def sessions_endpoint() -> dict[str, object]:
        return background.sessions()

    @app.post("/sessions")
    def session_create_endpoint(req: SessionCreateRequest) -> dict[str, object]:
        session = background.create_session(req.name)
        broadcaster.publish(
            "session_changed", {"action": "created", "session": session}
        )
        return {"session": session, **background.sessions()}

    @app.post("/sessions/{session_id}/activate")
    def session_activate_endpoint(session_id: str) -> dict[str, object]:
        try:
            session = background.activate_session(session_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown listening session: {session_id}"
            ) from exc
        broadcaster.publish(
            "session_changed", {"action": "activated", "session": session}
        )
        return {"session": session, **background.sessions()}

    @app.patch("/sessions/{session_id}")
    def session_rename_endpoint(
        session_id: str, req: SessionRenameRequest
    ) -> dict[str, object]:
        try:
            session = background.rename_session(session_id, req.name)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown listening session: {session_id}"
            ) from exc
        broadcaster.publish(
            "session_changed", {"action": "renamed", "session": session}
        )
        return {"session": session, **background.sessions()}

    @app.patch("/sessions/{session_id}/events/{event_id}")
    def session_event_rename_endpoint(
        session_id: str, event_id: str, req: EventRenameRequest
    ) -> dict[str, object]:
        try:
            event = background.rename_event(session_id, event_id, req.title)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown listening result: {event_id}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        broadcaster.publish(
            "session_changed",
            {
                "action": "event_renamed",
                "session_id": session_id,
                "listening_event": event,
            },
        )
        return {"listening_event": event, **background.sessions()}

    @app.delete("/sessions/{session_id}/events/{event_id}")
    def session_event_delete_endpoint(
        session_id: str, event_id: str
    ) -> dict[str, object]:
        try:
            event = background.delete_event(session_id, event_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown listening result: {event_id}"
            ) from exc
        broadcaster.publish(
            "session_changed",
            {
                "action": "event_deleted",
                "session_id": session_id,
                "listening_event": event,
            },
        )
        return {"deleted": True, "listening_event": event, **background.sessions()}

    @app.post("/sessions/{session_id}/remember")
    def session_remember_endpoint(session_id: str) -> dict[str, object]:
        events = background.session_events(session_id)
        if not events:
            raise HTTPException(
                status_code=404,
                detail=f"listening session has no stored results: {session_id}",
            )
        remembered = []
        for event in events:
            remembered.append(
                remember_event(
                    dict(event), tags=[f"session:{session_id}", "listening-session"]
                )
            )
        return {
            "session_id": session_id,
            "remembered_count": len(remembered),
            "trace_ids": [item["trace"]["id"] for item in remembered],
            "akousma_ids": [
                item["akousma_id"] for item in remembered if item.get("akousma_id")
            ],
            "shared_errors": [
                item["shared_error"] for item in remembered if item.get("shared_error")
            ],
        }

    @app.post("/sessions/{session_id}/archive")
    def session_archive_endpoint(session_id: str) -> dict[str, object]:
        try:
            session = background.archive_session(session_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown listening session: {session_id}"
            ) from exc
        broadcaster.publish(
            "session_changed", {"action": "archived", "session": session}
        )
        return {"session": session, **background.sessions()}

    @app.post("/sessions/{session_id}/restore")
    def session_restore_endpoint(session_id: str) -> dict[str, object]:
        try:
            session = background.restore_session(session_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown archived session: {session_id}"
            ) from exc
        broadcaster.publish(
            "session_changed", {"action": "restored", "session": session}
        )
        return {"session": session, **background.sessions()}

    @app.delete("/sessions/{session_id}")
    def session_delete_endpoint(session_id: str) -> dict[str, object]:
        try:
            session = background.delete_session(session_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown listening session: {session_id}"
            ) from exc
        broadcaster.publish(
            "session_changed", {"action": "deleted", "session": session}
        )
        return {"deleted": True, "session": session, **background.sessions()}

    @app.get("/background/history/export")
    def background_history_export_endpoint() -> dict[str, object]:
        return background.export_history()

    @app.post("/background/history/pin")
    def background_history_pin_endpoint(
        req: BackgroundHistoryPinRequest,
    ) -> dict[str, object]:
        try:
            return background.set_pinned_event(req.event_id, pinned=req.pinned)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"recent event is not available: {req.event_id}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/background/history/batch-pin")
    def background_history_batch_pin_endpoint(
        req: BackgroundHistoryBatchPinRequest,
    ) -> dict[str, object]:
        try:
            return background.set_pinned_events(req.event_ids, pinned=req.pinned)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/background/history/archive")
    def background_history_archive_endpoint(
        req: BackgroundHistoryArchiveRequest,
    ) -> dict[str, object]:
        return background.archive_history(event_ids=req.event_ids, label=req.label)

    @app.post("/background/history/clear")
    def background_history_clear_endpoint(
        req: BackgroundHistoryClearRequest,
    ) -> dict[str, object]:
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
                raise ValueError(
                    "no active live session is available for background capture"
                )
            seconds = (
                req.seconds
                if req.seconds is not None
                else background.config.default_capture_seconds
            )
            preset_id = req.route_preset or background.config.default_route_preset
            capture = live.capture_last(session_id, seconds)
            analyzed = analyze_capture(
                capture,
                preset_id,
                privacy_mode="incognito"
                if background.config.incognito
                else "ephemeral",
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                song_id=req.song_id,
            )
            event = (
                analyzed.get("listening_event")
                if isinstance(analyzed.get("listening_event"), dict)
                else None
            )
            trace = None
            should_remember = (
                req.remember or background.config.save_events_by_default
            ) and not background.config.incognito
            if should_remember and event:
                retention_checker = covenant_store.engine()
                memory_refusal = (
                    retention_checker.forbids_retention("memory")
                    if retention_checker is not None
                    else None
                )
                if memory_refusal:
                    event.setdefault("covenant", {}).setdefault("withheld", []).append(
                        {"rule": "do_not_retain", "subject": "memory", "count": 1}
                    )
                else:
                    trace = memory.remember(event, tags=["background-capture"])
                    event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
            return {
                "action_id": action_id,
                **analyzed,
                "trace": trace,
                "background": background.status(),
            }
        except (RuntimeError, ValueError) as exc:
            background.fail_action(str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/upload")
    def upload_endpoint(
        file: UploadFile = File(...), operation_id: str | None = Form(None)
    ) -> dict[str, object]:
        return operations.run(operation_id, lambda: save_upload(file))

    @app.get("/operations/{identifier}")
    def operation_receipt(identifier: str):
        receipt = owner_journal.get("operation", identifier)
        if receipt is None:
            raise HTTPException(404, "unknown operation")
        return receipt

    @app.post("/operations/{identifier}/cancel")
    def cancel_operation(identifier: str):
        return {
            "cancel_requested": operations.cancel(identifier),
            "scope": "before publication commit; model computation may finish but late output is discarded",
        }

    @app.get("/sample-tone")
    def sample_tone_endpoint() -> dict[str, object]:
        path = sample_tone_path(config.data_dir)
        return {
            "path": str(path),
            "sample": True,
            "raw_audio_policy": "generated_local_fixture",
        }

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
            result, engine_result = direct_analysis(
                engine, str(path), req.mode, req.thinking_budget
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"analysis": result, "engine": dump_model(engine_result)}

    def validate_listening_selection(
        model_id,
        listening_mode,
        audio_model=None,
        route_preset_id="basic",
        explicit_passes=None,
    ):
        from harness.types import LISTENING_MODES

        if listening_mode is not None and listening_mode not in LISTENING_MODES:
            raise HTTPException(400, "Unknown AKOÚŌ listening modality")
        if audio_model is not None:
            if model_id is not None:
                raise HTTPException(400, "Choose model_id or audio_model, not both")
            try:
                spec = resolve_audio_model(audio_model)
                preset = route_preset(route_preset_id)
                target_passes = (
                    explicit_passes
                    if explicit_passes is not None
                    else preset.moss_passes
                )
                required = {"transcribe": "transcription", "music": "music_analysis"}
                roles = {
                    required.get(name, "fast_perception") for name in target_passes
                }
                if preset.direct_moss_modes and explicit_passes is None:
                    roles.add("deep_perception")
                if not roles.issubset(spec.roles):
                    raise ValueError(
                        "Selected model does not support all listening route roles"
                    )
                if spec.provider_id != "oida_moss":
                    with use_audio_model(audio_model):
                        from oida.reasoning.contracts import ModelRole

                        engine._assignment(
                            reasoning_settings.load(),
                            ModelRole(next(iter(roles), "fast_perception")),
                        )
                    return None
                model_id = spec.id
            except (ValueError, EngineUnavailable) as exc:
                raise HTTPException(400, str(exc)) from exc
        if model_id is None:
            return None
        resolved = resolve_moss_model(model_id)
        if config.profile == "stub" or resolved is None:
            raise HTTPException(400, "Selected audio model is unavailable")
        if (
            config.profile != "cuda-server"
            and not (Path(resolved) / "config.json").is_file()
        ):
            raise HTTPException(400, "Selected audio model is not installed locally")
        return resolved

    @app.get("/listening/options")
    def listening_options():
        models = []
        seen = set()
        descriptors = moss_model_descriptors()
        for item in descriptors:
            metadata = item.metadata
            key = Path(str(metadata.get("path") or item.id)).name
            if key in seen:
                continue
            seen.add(key)
            checkpoint_meta = next(
                (
                    d.metadata
                    for d in descriptors
                    if d.metadata.get("description")
                    and Path(str(d.metadata.get("path", ""))).name == key
                ),
                {},
            )
            models.append(
                dict(
                    id=item.id,
                    name=f"Configured local · {key}",
                    capabilities=item.capabilities,
                    description=checkpoint_meta.get(
                        "description", "Local audio understanding model"
                    ),
                    size_gb=checkpoint_meta.get("size_gb"),
                    installed=bool(metadata.get("installed")),
                    loaded=bool(metadata.get("loaded")),
                    available=config.profile != "stub"
                    and bool(metadata.get("available")),
                    qualification_level="local_runtime_detected",
                )
            )
        from oida.reasoning.model_catalog import MODEL_SPECS, find_model_spec
        from oida.reasoning.contracts import ProviderLocality

        # Preserve legacy IDs while advertising the equivalent explicit selector.
        for row in models:
            spec = find_model_spec("oida_moss", row["id"])
            if spec is not None and not spec.selectable:
                row.update(
                    available=False,
                    qualification_level="experimental_unqualified",
                    unavailable_reason="Model runtime qualification pending",
                )
            if spec is not None and spec.selectable:
                row["audio_model"] = audio_selector(spec).model_dump()
                row["catalogued"] = True
        configured = reasoning_settings.load()
        budget_state = reasoning_budget.state()
        budget_enabled = bool(
            budget_state.get("enabled", False)
            or budget_state.get("config", {}).get("enabled", False)
        )
        import sys

        is_mac = sys.platform == "darwin"
        for spec in MODEL_SPECS:
            if (
                spec.provider_id
                not in {
                    "google",
                    "alibaba",
                    "local_audio",
                    "stepfun",
                    "groq",
                    "oida_moss",
                }
                or not spec.audio_transport
            ):
                continue
            if (
                spec.id in seen
                or spec.name in seen
                or (
                    spec.provider_id == "oida_moss"
                    and any(m.get("id") == spec.id for m in models)
                )
            ):
                continue
            provider = configured.providers.get(spec.provider_id)
            has_cred = bool(provider and provider.credential_ref)
            probed = engine.is_model_probed(spec.provider_id, spec.id)
            prov_enabled = bool(provider and provider.enabled)
            local_target = (
                resolve_moss_model(spec.id) if spec.provider_id == "oida_moss" else None
            )
            local_installed = bool(
                local_target and (Path(local_target) / "config.json").is_file()
            )
            local_loaded = bool(local_target) and any(
                descriptor.metadata.get("loaded")
                and Path(str(descriptor.metadata.get("path", ""))).name
                == Path(local_target).name
                for descriptor in descriptors
            )

            if not spec.selectable:
                avail = False
                unavail_reason = "Adapter or model runtime qualification pending"
            elif local_installed and config.profile != "stub":
                avail = True
                unavail_reason = None
            elif (
                spec.provider_id in {"local_audio", "oida_moss"}
                or spec.locality == ProviderLocality.LOCAL
            ):
                if is_mac and any(
                    p in spec.platforms
                    for p in ("linux-cuda12", "cuda-sglang", "linux-cuda-sglang")
                ):
                    avail = False
                    unavail_reason = "Requires dedicated Linux/CUDA host running SGLang; unsupported on macOS MPS"
                else:
                    avail = False
                    unavail_reason = "Local deployment qualification pending"
            elif not budget_enabled:
                avail = False
                unavail_reason = "Cloud listening trial is not enabled"
            elif not prov_enabled:
                avail = False
                unavail_reason = f"{spec.provider_id} provider is disabled"
            elif has_cred is False:
                avail = False
                unavail_reason = f"{spec.provider_id} API key is not configured"
            elif not probed:
                avail = False
                unavail_reason = "Model probe pending"
            else:
                avail = True
                unavail_reason = None

            models.append(
                dict(
                    id=audio_selector(spec).deployment_id,
                    name=(
                        spec.name
                        + (
                            " · registered ID / local checkpoint"
                            if local_installed
                            else " · pinned Hugging Face revision"
                        )
                    )
                    if spec.provider_id == "oida_moss" and "/" in spec.id
                    else spec.name,
                    audio_model=audio_selector(spec).model_dump(),
                    capabilities=spec.capabilities,
                    description=spec.notes or "Registered audio deployment",
                    installed=local_installed
                    if spec.locality == ProviderLocality.LOCAL
                    else None,
                    loaded=local_loaded,
                    available=avail,
                    catalogued=True,
                    configured=(
                        local_target in {config.instruct_model, config.thinking_model}
                    )
                    if spec.provider_id == "oida_moss"
                    else prov_enabled,
                    reachable=None,  # A past probe is not a current reachability check.
                    inference_tested=probed,
                    unavailable_reason=unavail_reason,
                    qualification_level="local_runtime_detected"
                    if local_installed and avail
                    else spec.integration_status,
                    license=spec.license,
                    revision=spec.revision,
                    sample_rate_hz=spec.sample_rate_hz,
                    input_representation={
                        "sample_rate_hz": spec.sample_rate_hz,
                        "channels": None,
                        "status": "unverified_provider_preprocessing",
                    },
                )
            )
        from akouo_contract.listening_tasks import compound_listening_routes

        return dict(
            models=models,
            engine=engine_status(),
            specialist_tasks=specialists.options(),
            compound_routes=compound_listening_routes(),
            route_presets=akouo_manifest()["route_presets"],
            controls=available_harness_controls(),
        )

    # Each owner admits its own future adapters. Existing selections are unchanged.
    from akousma.resource_admission import admission_status
    from oida.specialists.runtime import Specialists

    specialists = Specialists()
    app.state.model_deployments = specialists.registry

    @app.get("/capabilities")
    def model_capabilities():
        return dict(
            contract="listening-stack/capability-catalog/v1",
            owner="oida",
            legacy_models=listening_options()["models"],
            deployments=app.state.model_deployments.catalog(),
            admission=admission_status(),
            legacy_policy="Existing model settings preserved; catalog availability is not deployment validation",
        )

    @app.post("/listen-event")
    def listen_event_endpoint(req: ListenEventRequest) -> dict[str, object]:
        if req.aperture is not None:
            raise HTTPException(400, "Use the gateway for portable aperture requests")
        return execute_listen_event(req)

    def execute_listen_event(
        req: ListenEventRequest, *, file_window=None, native_analysis=None
    ) -> dict[str, object]:
        from oida.engine_base import use_listening_model

        if req.passes is not None and len(set(req.passes)) != len(req.passes):
            raise HTTPException(400, "Duplicate explicit pass")
        if len(req.specialist_tasks) != len(set(req.specialist_tasks)):
            raise HTTPException(400, "Duplicate specialist task")
        if set(req.specialist_deployments) - set(req.specialist_tasks):
            raise HTTPException(
                400, "A specialist deployment names a task that was not selected"
            )
        model = validate_listening_selection(
            req.model_id,
            req.listening_mode,
            req.audio_model,
            req.route_preset,
            explicit_passes=req.passes,
        )
        with use_listening_model(model), use_audio_model(req.audio_model):
            return run_listen_event(
                req, file_window=file_window, native_analysis=native_analysis
            )

    def run_listen_event(
        req: ListenEventRequest, *, file_window=None, native_analysis=None
    ) -> dict[str, object]:
        try:
            checkpoint()
            preset = route_preset(req.route_preset)
            path = _require_existing_path(req.path)
            if req.expected_source_sha256:
                from oida.pass_provenance import audio_fingerprint

                if (
                    audio_fingerprint(Path(path))["sha256"]
                    != req.expected_source_sha256
                ):
                    raise ValueError("Retained excerpt content changed")
            privacy_mode = _privacy_mode(req.privacy_mode)
            source_type = _audio_source_type(req.source_type)
            raw_audio_policy = _raw_audio_policy(
                req.raw_audio_policy
                or (
                    "temp"
                    if source_type in {"live_input", "system_output", "buffer"}
                    else "external_ref"
                )
            )
            if req.source_admission is not None:
                req.source_admission.check_policy(source_type, raw_audio_policy)
            capture_info = _capture_info(req)
            location = _validated_location(req.location)
            if (req.listening_access is None) != (req.spectral_request is None):
                raise ValueError(
                    "listening_access and spectral_request must be supplied together"
                )

            # The sovereignty layer (spec v1.3): empty by default; the active
            # covenant — or one pinned by name on this request — gates what is
            # listened to, revealed, and retained. Withholding is honest,
            # attributed absence, carried on the event's covenant block.
            try:
                covenant_engine = covenant_store.engine(override_name=req.covenant)
            except FileNotFoundError as exc:
                raise ValueError(str(exc)) from exc
            covenant_rules_applied: list[str] = []
            covenant_withheld: list[dict[str, object]] = []
            passes = (
                list(req.passes) if req.passes is not None else list(preset.moss_passes)
            )
            if covenant_engine is not None:
                refusal = (
                    covenant_engine.refuse_source(source_type)
                    or covenant_engine.refuse_quiet_hours()
                )
                if refusal:
                    broadcaster.publish(
                        "listen_withheld",
                        {
                            "covenant": covenant_engine.covenant.id,
                            "rule": refusal,
                            "source": source_type,
                        },
                    )
                    raise HTTPException(
                        status_code=423,
                        detail=f"withheld under covenant {covenant_engine.covenant.id}: {refusal}",
                    )
                max_window = covenant_engine.max_window_seconds()
                if max_window is not None:
                    info = audio_info(path)
                    actual_seconds = (
                        float(info.get("durationSeconds") or 0.0)
                        if isinstance(info, dict)
                        else 0.0
                    )
                    if actual_seconds <= 0 or actual_seconds > max_window + 1e-6:
                        broadcaster.publish(
                            "listen_withheld",
                            {
                                "covenant": covenant_engine.covenant.id,
                                "rule": f"max_window:{max_window:g}",
                                "source": source_type,
                            },
                        )
                        raise HTTPException(
                            status_code=423,
                            detail=(
                                f"withheld under covenant {covenant_engine.covenant.id}: "
                                f"the supplied audio must already be bounded to {max_window:g} seconds or less"
                            ),
                        )
                if capture_info and capture_info.get("seconds") is not None:
                    clamped, window_rule = covenant_engine.clamp_window(
                        float(capture_info["seconds"])
                    )
                    if window_rule:
                        capture_info["seconds"] = clamped
                        covenant_rules_applied.append(f"max_window:{float(clamped):g}")
                passes, pass_rules = covenant_engine.filter_passes(passes)
                covenant_rules_applied.extend(pass_rules)
                location, location_withheld = covenant_engine.apply_location(location)
                covenant_withheld.extend(location_withheld)
                if covenant_engine.forbids_retention("raw-audio") and source_type in {
                    "live_input",
                    "system_output",
                    "buffer",
                }:
                    if raw_audio_policy in {"saved", "external_ref"}:
                        raw_audio_policy = _raw_audio_policy("temp")
                        covenant_rules_applied.append("do_not_retain:raw-audio")

            metadata: dict[str, object] = {"raw_audio_policy": raw_audio_policy}
            if file_window is not None:
                metadata["file_window"] = file_window
            if req.live_session_id is not None and req.live_start_seconds is not None:
                metadata["live_window"] = dict(
                    session_id=req.live_session_id,
                    start_seconds=req.live_start_seconds,
                    duration_seconds=req.capture_seconds,
                    clock="session captured-audio time; not wall-clock",
                )
            if capture_info:
                metadata["capture"] = capture_info
            if location:
                metadata["location"] = location
            from oida.stage_timing import stage

            with stage("prepare_segment"):
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
                    captured_at=req.source_admission.source_time
                    if req.source_admission
                    else None,
                    metadata=metadata,
                )
            if req.source_admission is not None:
                # Covenants may narrow retention after the first policy check.
                req.source_admission.check_policy(source_type, raw_audio_policy)
                segment.source.details["source_admission"] = (
                    req.source_admission.receipt(segment)
                )
            apparatus_decision = None
            if req.spectral_request is not None:
                try:
                    from oida.apparatus_gate import gate_spectral_request
                    from oida.apparatus_evidence import EvidenceUnavailable
                    from oida.reporting import prepare_report_bindings

                    apparatus_decision = gate_spectral_request(
                        path,
                        req.listening_access,
                        req.spectral_request,
                        evidence_manifest=evidence_manifest,
                        prepare_bindings=lambda source: prepare_report_bindings(
                            engine, str(path), passes, source, config.moss_chunk_seconds
                        ),
                    )
                except (ImportError, FileNotFoundError, StopIteration) as exc:
                    raise HTTPException(
                        503,
                        "Apparatus contracts unavailable; install compatible packaged dependencies",
                    ) from exc
                except EvidenceUnavailable as exc:
                    raise HTTPException(503, str(exc)) from exc
                owner_journal.save(
                    "apparatus_decision",
                    apparatus_decision["decision"]["id"],
                    apparatus_decision,
                )
                broadcaster.publish(
                    "listen_decided", {"apparatus_decision": apparatus_decision}
                )
                if not apparatus_decision["measurement_permitted"]:
                    raise HTTPException(
                        status_code=409,
                        detail={"apparatus_decision": apparatus_decision},
                    )
            broadcaster.publish(
                "listen_started",
                {"path": str(path), "route_preset": preset.id, "source": source_type},
            )
            from contextlib import nullcontext
            from oida.input_binding import enforce_input_bindings

            binding_guard = (
                enforce_input_bindings(apparatus_decision["execution_bindings"])
                if apparatus_decision and apparatus_decision.get("execution_bindings")
                else nullcontext()
            )
            focus = req.focus_question or ""
            if req.context_refs:
                if privacy_mode == "incognito":
                    raise ValueError("Incognito refuses retained account context")
                notes = [retained_account(ref) for ref in req.context_refs]
                focus += (
                    "\nPrior accounts (model interpretations, not ground truth):\n"
                    + json.dumps(notes, ensure_ascii=False)
                )
            with (
                binding_guard,
                engine.request_policy(
                    privacy_mode=privacy_mode,
                    covenant_engine=covenant_engine,
                    source_type=source_type,
                    allow_external_source=bool(
                        getattr(req, "allow_external_audio", False)
                    ),
                    deadline=dispatch_deadline(passes),
                    focus=focus,
                ) as audio_policy,
            ):
                from oida.specialists.dispatch import interpret

                with stage("interpret"):
                    perception, interpretation_status = interpret(
                        engine,
                        str(path),
                        "oida",
                        passes=passes,
                        partial=bool(req.specialist_tasks),
                        report_fn=report,
                        chunk_seconds=config.moss_chunk_seconds,
                        overlap_seconds=_chunk_overlap(config),
                    )
            with stage("specialists"):
                specialist_evidence = (
                    specialists.execute(
                        path,
                        req.specialist_tasks,
                        asset_id=segment.id,
                        covenant=covenant_engine,
                        source_sha256=segment.data_ref.sha256,
                        speech_options={
                            "vad": req.speech_vad,
                            "alignment": req.speech_alignment,
                            "language": req.speech_language,
                        },
                        deployments=req.specialist_deployments,
                    )
                    if req.specialist_tasks
                    else []
                )
            for lane in specialist_evidence:
                lane["time_origin"] = (
                    metadata.get("file_window")
                    or metadata.get("live_window")
                    or {"start_seconds": 0, "clock": "source asset time"}
                )
            checkpoint()
            current_checker = covenant_store.engine(override_name=req.covenant)
            if current_checker is not None:
                if (
                    current_checker.refuse_source(source_type)
                    or current_checker.refuse_quiet_hours()
                ):
                    raise HTTPException(
                        423,
                        "withheld under current covenant: source no longer permitted",
                    )
                if current_checker.covenant != (
                    covenant_engine.covenant if covenant_engine else None
                ):
                    raise HTTPException(
                        423,
                        "withheld under current covenant: policy changed during listening",
                    )
            elif covenant_engine is not None:
                raise HTTPException(
                    423,
                    "withheld under current covenant: policy changed during listening",
                )
            perception_dict = report_to_dict(perception)
            if covenant_engine is not None:
                perception_dict, perception_withheld = (
                    covenant_engine.redact_perception(perception_dict)
                )
                covenant_withheld.extend(perception_withheld)
            command_output = build_harness_output(
                perception_dict, command=preset.akouo_command, mode=req.listening_mode
            )
            if covenant_engine is not None:
                command_output, claim_withheld = covenant_engine.redact_command_output(
                    command_output
                )
                covenant_withheld.extend(claim_withheld)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                segment=segment,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=privacy_mode,
                raw_audio_policy=raw_audio_policy,
                listening_identity=audio_policy.listening_identity_block(passes),
                # Compared against event labels so an orientation cannot become
                # the account's title. Never stored: the block above is a digest.
                identity_text=audio_policy.listening_identity.text,
            )
            if specialist_evidence:
                event["specialist_evidence"] = specialist_evidence
                event["listening_task_status"] = [
                    dict(task="dsp", status="complete"),
                    interpretation_status,
                ]
            if apparatus_decision is not None:
                event["apparatus_decision"] = apparatus_decision
            song_identity_withheld = None
            if (
                req.song_id
                and preset.id == "music"
                and covenant_engine is not None
                and covenant_engine.forbids_song_identity()
            ):
                song_identity_withheld = f"Song identity withheld under covenant {covenant_engine.covenant.id}."
                covenant_withheld.append(
                    {"rule": "do_not_reveal", "subject": "song-identity", "count": 1}
                )
            enrich_music_id(
                event,
                path,
                preset_id=preset.id,
                requested=req.song_id,
                withheld_reason=song_identity_withheld,
            )
            if capture_info:
                event["capture"] = capture_info
            if location:
                event["location"] = location
            if covenant_engine is not None:
                event["covenant"] = covenant_engine.event_block(
                    rules_applied=covenant_rules_applied, withheld=covenant_withheld
                )
            if apparatus_decision and apparatus_decision.get("measurement_permitted"):
                refreshed = gate_spectral_request(
                    path,
                    req.listening_access,
                    req.spectral_request,
                    evidence_manifest=evidence_manifest,
                    prepare_bindings=lambda source: prepare_report_bindings(
                        engine, str(path), passes, source, config.moss_chunk_seconds
                    ),
                )
                owner_journal.save(
                    "apparatus_decision", refreshed["decision"]["id"], refreshed
                )
                if not refreshed["measurement_permitted"] or refreshed.get(
                    "execution_bindings"
                ) != apparatus_decision.get("execution_bindings"):
                    raise HTTPException(
                        409,
                        "Apparatus approval or prepared input changed during execution",
                    )
            if native_analysis is not None:
                native_analysis["recheck"]()
                from oida.station_aperture import receipt

                event["acoustics"] = receipt(native_analysis)
            checkpoint(seal=True)
            with stage("memory_similarity"):
                event = memory.enrich_event(event)
            if getattr(req, "ephemeral_delivery", False):
                # Public delivery may not become history even when an owner opts
                # into retaining ordinary incognito sessions.
                background.finish_action(None)
            else:
                background.finish_action(event)
                broadcaster.publish(
                    "listen_completed",
                    {"listening_event": event, "route_preset": preset.id},
                )
        except ValueError as exc:
            broadcaster.publish("listen_failed", {"detail": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "listening_event": event,
            "perception_report": perception_dict,
            "command_output": command_output,
            "background": background.status()
            if getattr(req, "response_mode", "full") == "full"
            else {},
        }

    @app.post("/gateway/listen")
    def gateway_listen_endpoint(req: GatewayListenRequest) -> dict[str, object]:
        if req.route_preset == "agent-native":
            if req.audio_model is not None:
                raise HTTPException(
                    400,
                    "Native listening requires its own observable input binding; audio_model is unsupported",
                )
            if req.aperture is not None:
                raise HTTPException(
                    400,
                    "Portable apertures accompany ordinary listening routes; the native route uses native_options",
                )
            from oida.agent_native import NativeRequest

            if req.source_type != "file" or req.native_options is None:
                raise HTTPException(
                    400, "Agent-native requires a file and explicit native_options"
                )
            payload = dict(req.native_options)
            if (
                req.covenant is not None
                or req.source_admission is not None
                or req.listening_access is not None
                or req.spectral_request is not None
            ):
                raise HTTPException(
                    400,
                    "Native files use the active covenant and owner-resolved evidence",
                )
            memory = payload.get("memory", "none")
            if req.remember != (memory != "none") or req.retain_library_audio != (
                memory == "record_audio"
            ):
                raise HTTPException(
                    400, "Gateway memory flags must match native_options.memory"
                )
            if req.ephemeral_delivery and memory != "none":
                raise HTTPException(
                    400, "Ephemeral delivery refuses native persistence"
                )
            if (
                req.privacy_mode == "incognito"
                and payload.get("memory", "none") != "none"
            ):
                raise HTTPException(423, "Incognito refuses native persistence")
            payload.update(
                path=req.path,
                operation_id=req.operation_id or payload.get("operation_id"),
            )
            try:
                native_request = NativeRequest.model_validate(payload)
            except ValueError as exc:
                raise HTTPException(400, "Invalid native options") from exc
            return native_listen(native_request)
        return operations.run(
            req.operation_id,
            timed(lambda: gateway_listen_impl(req)),
            deadline_at=checked_deadline(req.deadline_at),
        )

    @app.post("/gateway/listen-window")
    def gateway_window_endpoint(req: GatewayWindowRequest) -> dict[str, object]:
        def execute_window():
            from oida.audio_window import audio_window

            validate_listening_selection(
                req.model_id,
                req.listening_mode,
                req.audio_model,
                req.route_preset,
                explicit_passes=req.passes,
            )
            if (
                req.source_type != "file"
                or req.source_admission is not None
                or req.spectral_request is not None
                or req.listening_access is not None
            ):
                raise HTTPException(
                    400, "File windows accept ordinary uploaded-file listening only"
                )
            if req.expected_source_sha256:
                from oida.pass_provenance import audio_fingerprint

                if (
                    audio_fingerprint(Path(req.path))["sha256"]
                    != req.expected_source_sha256
                ):
                    raise HTTPException(409, "Retained excerpt content changed")
            source_preflight("file", req.seconds, req.route_preset, req.remember)
            path = _require_existing_path(req.path)
            try:
                with audio_window(
                    path,
                    start_seconds=req.start_seconds,
                    seconds=req.seconds,
                    temp_dir=config.data_dir / "source-capture-temp",
                ) as (window, metadata):
                    if (
                        req.expected_source_sha256
                        and metadata["source_sha256"] != req.expected_source_sha256
                    ):
                        raise HTTPException(409, "Retained excerpt content changed")
                    request = GatewayListenRequest.model_validate(
                        {
                            **req.model_dump(
                                exclude={
                                    "start_seconds",
                                    "seconds",
                                    "expected_source_sha256",
                                }
                            ),
                            "path": str(window),
                            "raw_audio_policy": "not_stored"
                            if req.ephemeral_delivery
                            else "temp",
                            "source_label": req.source_label or path.name,
                            "capture_seconds": metadata["duration_seconds"],
                            "capture_direction": "past",
                            "capture_trigger": "manual-file-window",
                        }
                    )
                    return gateway_listen_impl(request, file_window=metadata)
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(400, str(exc)) from exc

        return operations.run(
            req.operation_id, timed(execute_window), deadline_at=checked_deadline(req.deadline_at)
        )

    def timed(callback):
        """Run a listening and return where its time went beside its result."""
        from oida import stage_timing

        def run():
            with stage_timing.collecting() as summary:
                result = callback()
                timings = summary()
            return {**result, "timings": timings} if isinstance(result, dict) else result

        return run

    def checked_deadline(value):
        """A deadline must lie in the future and within four hours; anything else is refused."""
        if value is None:
            return None
        remaining = value - time.time()
        if remaining <= 0:
            from oida.operation_control import DeadlineReached

            raise DeadlineReached("operation deadline already passed; nothing was started")
        if remaining > 4 * 3600:
            raise HTTPException(400, "deadline_at must be within four hours")
        return value

    def gateway_listen_impl(
        req: GatewayListenRequest, *, file_window=None
    ) -> dict[str, object]:
        if req.aperture is None:
            return gateway_listen_prepared(req, file_window=file_window)
        from oida.station_aperture import prepare

        validate_listening_selection(
            req.model_id,
            req.listening_mode,
            req.audio_model,
            req.route_preset,
            explicit_passes=req.passes,
        )
        initial_policy = covenant_store.engine(override_name=req.covenant)

        def preflight(path):
            checker = covenant_store.engine(override_name=req.covenant)
            if (checker.covenant if checker else None) != (
                initial_policy.covenant if initial_policy else None
            ):
                raise HTTPException(423, "Covenant changed during aperture analysis")
            if req.aperture.views and req.aperture.retention.expires_at <= time.time():
                raise HTTPException(
                    423, "Spectral retention permission expired during analysis"
                )
            if req.aperture.views and (
                req.privacy_mode == "incognito"
                or background.config.incognito
                or req.ephemeral_delivery
                or req.raw_audio_policy == "not_stored"
            ):
                raise HTTPException(
                    423, "Private delivery forbids retained spectral views"
                )
            if checker:
                info = audio_info(path)
                limit = checker.max_window_seconds()
                if (
                    checker.refuse_source(req.source_type)
                    or checker.refuse_quiet_hours()
                    or (limit is not None and info["durationSeconds"] > limit)
                ):
                    raise HTTPException(
                        423, "Aperture withheld by source or window covenant"
                    )
                if req.aperture.views and (
                    checker.forbids_retention("raw-audio")
                    or checker.forbids_retention("memory")
                ):
                    raise HTTPException(423, "Spectral retention withheld by covenant")

        with prepare(
            req, root=config.data_dir / "source-capture-temp", preflight=preflight
        ) as (bound, native):
            return gateway_listen_prepared(
                bound, file_window=file_window, native_analysis=native
            )

    def gateway_listen_prepared(
        req: GatewayListenRequest, *, file_window=None, native_analysis=None
    ) -> dict[str, object]:
        if req.source_type == "system_output" and (
            req.retain_library_audio or req.raw_audio_policy not in {None, "temp"}
        ):
            raise HTTPException(
                400, "System output permits temporary audio and record-only memory"
            )
        if req.ephemeral_delivery and (
            req.remember
            or req.privacy_mode != "incognito"
            or req.raw_audio_policy != "not_stored"
            or req.song_id
        ):
            raise HTTPException(
                400,
                "Ephemeral delivery requires incognito, no storage, no recognition and remember=false",
            )
        try:
            result = execute_listen_event(
                req, file_window=file_window, native_analysis=native_analysis
            )
        except HTTPException as exc:
            if exc.status_code != 423:
                raise
            checker = covenant_store.engine(override_name=req.covenant)
            if checker is None:
                raise
            source_type = _audio_source_type(req.source_type)
            detail = str(exc.detail)
            decision = decision_first_refusal(
                perception_path="oida_owned",
                source_type=source_type,
                refusal=detail.split(": ", 1)[-1],
                covenant_engine=checker,
                remember=req.remember,
                gate="capture"
                if "bounded" in detail or "window" in detail
                else "input",
            )
            broadcaster.publish(
                "listen_decided",
                {
                    "route_outcome": decision["route_outcome"],
                    "source": source_type,
                },
            )
            return {
                **decision,
                "background": background.status()
                if req.response_mode == "full"
                else {},
            }
        event = result["listening_event"]
        trace = None
        remembered: dict[str, Any] | None = None
        memory_retention_rule = None
        if isinstance(event, dict) and event.get("covenant"):
            checker = covenant_store.engine(override_name=req.covenant)
            if checker is not None:
                memory_retention_rule = checker.forbids_retention("memory")
        if req.remember and memory_retention_rule:
            # Retention refused under the covenant: reported, never silent.
            event.setdefault("covenant", {}).setdefault("withheld", []).append(
                {"rule": "do_not_retain", "subject": "memory", "count": 1}
            )
        elif req.remember and req.privacy_mode != "incognito":
            from oida.stage_timing import stage

            with stage("remember"):
                remembered = remember_event(
                    event,
                    native_analysis=native_analysis,
                    user_notes=req.user_notes,
                    human_listener_id=req.human_listener_id,
                    human_display_name=req.human_display_name,
                    tags=req.tags,
                    declared_lineage=req.declared_lineage,
                )
            trace = remembered["trace"]
        if native_analysis is not None and req.aperture.views:
            saved_native = bool(remembered and remembered.get("akousma_id"))
            event["acoustics"]["retention_status"] = (
                "retained" if saved_native else "failed"
            )
            event["acoustics"]["views"] = [
                v
                if saved_native or v["state"] != "retained"
                else {**v, "state": "omitted", "reason": "Shared publication failed"}
                for v in native_analysis["bundle"]["views"]
            ]
        library_audio = None
        if native_analysis is not None and req.aperture.views:
            library_audio = dict(
                status="retained"
                if remembered and remembered.get("akousma_id")
                else "unavailable",
                record_id=remembered.get("akousma_id") if remembered else None,
                retention="governed-spectral-bundle",
                expires_at=req.aperture.retention.expires_at,
            )
        elif req.retain_library_audio:
            from oida.library_capture import retain_capture
            from oida.stage_timing import stage

            checker = covenant_store.engine(override_name=req.covenant)
            with stage("library_retain"):
                library_audio = retain_capture(
                    Path(req.path),
                    uploads_dir(),
                    record_id=remembered.get("akousma_id") if remembered else None,
                    event_id=str(event.get("id") or ""),
                    label=req.source_label or Path(req.path).name,
                    duration_seconds=(event.get("features") or {}).get("duration_s")
                    or req.capture_seconds,
                    source_type=req.source_type,
                    file_window=file_window,
                    parent_sound_id=req.library_parent_sound_id,
                    blocked=req.privacy_mode == "incognito"
                    or req.raw_audio_policy == "not_stored"
                    or req.ephemeral_delivery
                    or bool(
                        checker
                        and (
                            checker.forbids_retention("raw-audio")
                            or checker.forbids_retention("memory")
                        )
                    ),
                )
        if req.response_mode == "summary":
            from oida.listening_response import compact_listening_result

            return {
                **compact_listening_result(
                    event, remembered=remembered, remember_requested=req.remember
                ),
                "library_audio": library_audio,
            }
        earworm = (
            trace.get("earworm")
            if isinstance(trace, dict)
            else earworm_context_for_event(event)
        )
        return {
            "contract": GATEWAY_CONTRACT,
            "perception_path": "oida_owned",
            "status": "complete",
            "outcome": "listened",
            "library_audio": library_audio,
            **result,
            "earworm": earworm,
            "trace": trace,
            "akousma_id": remembered.get("akousma_id") if remembered else None,
            "human_akousma_id": remembered.get("human_akousma_id")
            if remembered
            else None,
            "shared_error": remembered.get("shared_error") if remembered else None,
            "human_error": remembered.get("human_error") if remembered else None,
        }

    @app.post("/remote/listen")
    def remote_listen_endpoint(
        file: UploadFile = File(...),
        direction: str = Form("past"),
        seconds: float = Form(30.0, gt=0, le=3600),
        route_preset_name: str = Form("basic"),
        lat: float | None = Form(None, ge=-90, le=90),
        lon: float | None = Form(None, ge=-180, le=180),
        accuracy_m: float | None = Form(None, ge=0),
        altitude_m: float | None = Form(None, ge=-12_000, le=100_000),
        location_label: str | None = Form(None),
        notes: str | None = Form(None),
        tags: str | None = Form(None),
        remember: bool = Form(True),
        device: str | None = Form(None),
        armed_at: str | None = Form(None),
    ) -> dict[str, object]:
        """The remote ear: a phone records (past ring slice or future window),
        uploads the sound with its optional GPS fix, and gets the listening
        back. The server keeps the WAV, writes the akousma — the sound plus
        its listening file — into the shared store, and returns the results
        for the remote UI."""
        saved = save_upload(file)
        location: dict[str, Any] | None = None
        if lat is not None and lon is not None:
            location = {"lat": lat, "lon": lon, "source": "gps"}
            if accuracy_m is not None:
                location["accuracy_m"] = accuracy_m
            if altitude_m is not None:
                location["altitude_m"] = altitude_m
            if location_label:
                location["label"] = location_label
        tag_list = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
        request = GatewayListenRequest(
            path=str(saved["path"]),
            route_preset=route_preset_name,
            privacy_mode="session",
            source_type="live_input",
            source_label="oída remote ear",
            raw_audio_policy="external_ref",
            capture_direction=direction,
            capture_seconds=seconds,
            capture_trigger="remote-ear",
            location=location,
            # The remote route writes one richer, audio-retaining Akousma below.
            # Suppress the gateway's generic shared write so one request cannot
            # create two machine accounts for the same listening.
            remember=False,
            user_notes=notes,
            tags=["remote-ear", *tag_list],
        )
        result = gateway_listen_endpoint(request)
        event = (
            result["listening_event"]
            if isinstance(result.get("listening_event"), dict)
            else {}
        )
        remote_info: dict[str, object] = {
            "direction": direction,
            "seconds": seconds,
            "stored_path": str(saved["path"]),
        }
        event_covenant = (
            event.get("covenant") if isinstance(event.get("covenant"), dict) else None
        )
        retention_checker = covenant_store.engine() if event_covenant else None
        if retention_checker is not None and retention_checker.forbids_retention(
            "raw-audio"
        ):
            # The sound is heard and released: uploaded audio is removed and
            # the akousma (if any) will carry no uri — attributed, not silent.
            cleanup_failed_upload(Path(str(saved["raw_path"])))
            remote_info.pop("stored_path", None)
            remote_info["raw_audio_withheld"] = "do_not_retain:raw-audio"
        if retention_checker is not None and retention_checker.forbids_retention(
            "memory"
        ):
            remote_info["akousma_withheld"] = "do_not_retain:memory"
            if isinstance(event, dict):
                event.setdefault("covenant", {}).setdefault("withheld", []).append(
                    {"rule": "do_not_retain", "subject": "memory", "count": 1}
                )
            return {**result, "remote": remote_info}
        if not remember:
            return {**result, "remote": remote_info}

        trace = memory.remember(event, user_notes=None, tags=["remote-ear", *tag_list])
        event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
        result["trace"] = trace
        result["earworm"] = trace.get("earworm")
        try:
            import akousma as _akousma

            from .akousma_bridge import (
                build_akousma_from_listen,
                build_human_response_akousma,
                persist_akousma,
            )

            store = _akousma.AkousmataStore()
            try:
                wav_path = Path(str(saved["path"]))
                uri: str | None = None
                if wav_path.exists():
                    uri = store.put_audio(
                        wav_path.read_bytes(), ext=wav_path.suffix.lstrip(".") or "wav"
                    )
                features = (
                    event.get("features")
                    if isinstance(event.get("features"), dict)
                    else {}
                )
                aggregate = (
                    event.get("aggregate")
                    if isinstance(event.get("aggregate"), dict)
                    else {}
                )
                audio: dict[str, Any] = {
                    "asset_id": f"remote_{event.get('id') or _akousma.new_id('cap')}",
                    "type": "capture",
                    "content_hash": f"sha256:{saved['sha256']}",
                }
                if uri:
                    audio["uri"] = uri
                if isinstance(features.get("duration_s"), (int, float)):
                    audio["duration_seconds"] = float(features["duration_s"])
                if isinstance(features.get("sample_rate"), (int, float)):
                    audio["sample_rate"] = int(features["sample_rate"])
                if isinstance(features.get("channels"), (int, float)):
                    audio["channels"] = int(features["channels"])
                perception_dict = (
                    result.get("perception_report")
                    if isinstance(result.get("perception_report"), dict)
                    else {}
                )
                command_output = (
                    result.get("command_output")
                    if isinstance(result.get("command_output"), dict)
                    else {}
                )
                listening: dict[str, Any] = {}
                signal = perception_dict.get("signal_interpretation")
                if isinstance(signal, dict) and signal:
                    listening["oida.signal"] = signal
                listening["oida.remote"] = {
                    "contract": event.get("contract") or "oida/listening-event/v0.3",
                    "summary": aggregate.get("short_summary") or aggregate.get("title"),
                    "aggregate": aggregate,
                    "claim_summary": command_output.get("claim_summary"),
                    "route_preset": route_preset_name,
                    "event_id": event.get("id"),
                    "listening_context": event.get("listening_context") or {},
                    "listening_provenance": event.get("listening_provenance") or {},
                    "source_admission": (event.get("source") or {})
                    .get("details", {})
                    .get("source_admission"),
                    "pass_provenance": event.get("pass_provenance") or [],
                    "listening_passes": event.get("listening_passes") or [],
                    "route_decisions": event.get("route_decisions") or [],
                    "apparatus": event.get("apparatus") or {},
                    "disagreements": event.get("disagreements") or [],
                    "routes": event.get("routes") or [],
                }
                # the covenant may have withheld or coarsened the location and
                # attached its identity — the record carries the event's truth
                event_location = (
                    event.get("location")
                    if isinstance(event.get("location"), dict)
                    else None
                )
                record = build_akousma_from_listen(
                    audio=audio,
                    listening=listening,
                    origin="live-input",
                    device=device or "phone microphone via oída remote ear",
                    tags=["remote-ear", *tag_list],
                    summary=str(
                        aggregate.get("short_summary") or aggregate.get("title") or ""
                    )
                    or None,
                    location=event_location,
                    capture={
                        "direction": direction,
                        "seconds": seconds,
                        "trigger": "remote-ear",
                        "armed_at": armed_at,
                    },
                    covenant=event_covenant,
                    listening_identity=(
                        event.get("listening_identity")
                        if isinstance(event.get("listening_identity"), dict)
                        else None
                    ),
                    disagreements=(
                        event.get("disagreements")
                        if isinstance(event.get("disagreements"), list)
                        else None
                    ),
                    actions=[
                        {
                            "action_id": f"remote_store_{event.get('id') or 'listen'}",
                            "proposal": "Store this remote-ear listening in local Akousmata memory.",
                            "status": "executed",
                            "authority": {
                                "mode": "execute_scoped",
                                "scopes": ["memory.remember", "audio.store_local"],
                                "granted_by": "remote-ear request",
                                "expires_at": None,
                                "requires_confirmation": False,
                                "reversible": True,
                            },
                            "receipt": {
                                "created_at": str(event.get("created_at") or "unknown"),
                                "actor": "oida.remote-ear",
                                "result": "local akousma write requested",
                                "recovery": "akousmata delete/forget",
                            },
                        }
                    ],
                )
                akousma_id = persist_akousma(record, store=store)
                owner_journal.record_reference(
                    akousma_id, event_id=event.get("id"), record=record
                )
                remote_info["akousma_id"] = akousma_id
                result["akousma_id"] = akousma_id
                if uri:
                    remote_info["audio_uri"] = uri
                if isinstance(event, dict):
                    event.setdefault("memory", {})["akousma_id"] = akousma_id
                if isinstance(notes, str) and notes.strip():
                    try:
                        human_profile = human_account_profile()
                        human_record = build_human_response_akousma(
                            record,
                            akousma_id,
                            notes,
                            listener_id=human_profile["listener_id"],
                            display_name=human_profile["display_name"] or None,
                            privacy=human_profile["privacy"],
                        )
                        human_akousma_id = persist_akousma(human_record, store=store)
                        owner_journal.record_reference(
                            human_akousma_id,
                            event_id=event.get("id"),
                            record=human_record,
                        )
                        remote_info["human_akousma_id"] = human_akousma_id
                        result["human_akousma_id"] = human_akousma_id
                    except Exception as exc:
                        LOGGER.warning(
                            "remote listen: linked human Akousma write failed: %s", exc
                        )
                        result["human_error"] = "linked human Akousmata write failed"
            finally:
                store.close()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — the listen already succeeded; report the store miss honestly
            LOGGER.warning("remote listen: akousma write failed: %s", exc)
            remote_info["akousma_error"] = "shared Akousmata memory write failed"
            result["shared_error"] = "shared Akousmata memory write failed"
        return {**result, "remote": remote_info}

    @app.post("/gateway/harness")
    def gateway_harness_endpoint(req: GatewayHarnessRequest) -> dict[str, object]:
        schema_path = REPO_ROOT / "oida" / "schemas" / "host-perception.schema.json"
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator(schema).validate(req.perception)
            declared_context = (
                req.perception.get("listening_context")
                if isinstance(req.perception.get("listening_context"), dict)
                else None
            )
            if (
                declared_context
                and declared_context.get("contract") == "akouo/listening-context/v2"
            ):
                AkouoLoader().validate("listening-context", declared_context)
            result = harness_host_perception(
                req.perception,
                route_preset_id=req.route_preset,
                command=req.command,
                question=req.question,
                remember=req.remember,
                memory=memory,
                privacy_mode=_privacy_mode(req.privacy_mode),
                raw_audio_policy=_raw_audio_policy(req.raw_audio_policy),
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                covenant_engine=covenant_store.engine(),
                listening_identity_snapshot=current_listening_identity_snapshot(),
            )
            listening_event = result.get("listening_event")
            if isinstance(listening_event, dict):
                background.finish_action(listening_event)
                broadcaster.publish(
                    "host_listen_completed",
                    {
                        "listening_event": listening_event,
                        "host": req.perception.get("host"),
                        "route_preset": req.route_preset,
                    },
                )
            else:
                broadcaster.publish(
                    "host_listen_decided",
                    {
                        "route_outcome": result.get("route_outcome"),
                        "host": req.perception.get("host"),
                        "route_preset": req.route_preset,
                    },
                )
            return result
        except jsonschema.ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"host perception failed schema validation: {exc.message}",
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
            source = (
                source_event.get("source")
                if isinstance(source_event.get("source"), dict)
                else {}
            )
            covenant_engine, passes, covenant_rules_applied = (
                prepare_covenant_perception(
                    path,
                    source_type=str(source.get("type") or "file"),
                    passes=preset.moss_passes,
                )
            )
            covenant_withheld: list[dict[str, object]] = []
            with engine.request_policy(
                privacy_mode=privacy_mode,
                covenant_engine=covenant_engine,
                covenant_block=(
                    source_event.get("covenant")
                    if isinstance(source_event.get("covenant"), dict)
                    else None
                ),
            ) as audio_policy:
                perception = report(
                    engine,
                    str(path),
                    f"oida-route-rerun-{preset.id}",
                    passes=passes,
                    chunk_seconds=config.moss_chunk_seconds,
                    overlap_seconds=_chunk_overlap(config),
                )
            perception_dict = report_to_dict(perception)
            if covenant_engine is not None:
                perception_dict, perception_withheld = (
                    covenant_engine.redact_perception(perception_dict)
                )
                covenant_withheld.extend(perception_withheld)
            command_output = build_harness_output(
                perception_dict, command=preset.akouo_command
            )
            if covenant_engine is not None:
                command_output, claim_withheld = covenant_engine.redact_command_output(
                    command_output
                )
                covenant_withheld.extend(claim_withheld)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                segment=segment,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=privacy_mode,
                raw_audio_policy=raw_audio_policy,
                listening_identity=audio_policy.listening_identity_block(passes),
                # Compared against event labels so an orientation cannot become
                # the account's title. Never stored: the block above is a digest.
                identity_text=audio_policy.listening_identity.text,
            )
            if covenant_engine is not None:
                event["covenant"] = covenant_engine.event_block(
                    rules_applied=covenant_rules_applied,
                    withheld=covenant_withheld,
                )
            event = memory.enrich_event(event)
            trace = None
            memory_refusal = (
                covenant_engine.forbids_retention("memory")
                if covenant_engine is not None
                else None
            )
            if req.remember and memory_refusal:
                event.setdefault("covenant", {}).setdefault("withheld", []).append(
                    {"rule": "do_not_retain", "subject": "memory", "count": 1}
                )
            elif req.remember and privacy_mode != "incognito":
                trace = memory.remember(
                    event, tags=["route-rerun", f"route-{preset.id}"]
                )
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
    def native_system_audio_analyze_endpoint(
        req: NativeSystemAudioAnalyzeRequest,
    ) -> dict[str, object]:
        try:
            preset = route_preset(req.route_preset)
            path = _require_existing_path(req.path)
            covenant_engine = covenant_store.engine()
            covenant_rules_applied: list[str] = []
            covenant_withheld: list[dict[str, object]] = []
            passes = list(preset.moss_passes)
            if covenant_engine is not None:
                refusal = (
                    covenant_engine.refuse_source("system_output")
                    or covenant_engine.refuse_quiet_hours()
                )
                if refusal:
                    broadcaster.publish(
                        "listen_withheld",
                        {
                            "covenant": covenant_engine.covenant.id,
                            "rule": refusal,
                            "source": "system_output",
                        },
                    )
                    raise HTTPException(
                        status_code=423,
                        detail=f"withheld under covenant {covenant_engine.covenant.id}: {refusal}",
                    )
                passes, pass_rules = covenant_engine.filter_passes(passes)
                covenant_rules_applied.extend(pass_rules)
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
                    "capture_direction": req.capture_direction,
                    "capture_trigger": req.capture_trigger,
                    "analysis_user_initiated": True,
                    "source_route": source_route,
                    "capture_scope": source_route.get("capture_scope"),
                    "model_input_policy": source_route.get("model_input_policy"),
                    "claim_limits": source_route.get("claim_limits"),
                },
            )
            broadcaster.publish(
                "listen_started",
                {
                    "path": str(path),
                    "route_preset": preset.id,
                    "source": "system-audio",
                },
            )
            with engine.request_policy(
                privacy_mode=_privacy_mode(req.privacy_mode),
                covenant_engine=covenant_engine,
            ) as audio_policy:
                perception = report(
                    engine,
                    str(path),
                    "oida-native-system-audio",
                    passes=passes,
                    chunk_seconds=config.moss_chunk_seconds,
                    overlap_seconds=_chunk_overlap(config),
                )
            perception_dict = report_to_dict(perception)
            if covenant_engine is not None:
                perception_dict, perception_withheld = (
                    covenant_engine.redact_perception(perception_dict)
                )
                covenant_withheld.extend(perception_withheld)
            command_output = build_harness_output(
                perception_dict, command=preset.akouo_command
            )
            if covenant_engine is not None:
                command_output, claim_withheld = covenant_engine.redact_command_output(
                    command_output
                )
                covenant_withheld.extend(claim_withheld)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                segment=segment,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=_privacy_mode(req.privacy_mode),
                raw_audio_policy="temp",
                listening_identity=audio_policy.listening_identity_block(passes),
                # Compared against event labels so an orientation cannot become
                # the account's title. Never stored: the block above is a digest.
                identity_text=audio_policy.listening_identity.text,
            )
            song_identity_withheld = None
            if (
                req.song_id
                and preset.id == "music"
                and covenant_engine is not None
                and covenant_engine.forbids_song_identity()
            ):
                song_identity_withheld = f"Song identity withheld under covenant {covenant_engine.covenant.id}."
                covenant_withheld.append(
                    {"rule": "do_not_reveal", "subject": "song-identity", "count": 1}
                )
            enrich_music_id(
                event,
                path,
                preset_id=preset.id,
                requested=req.song_id,
                withheld_reason=song_identity_withheld,
            )
            if req.capture_direction or req.duration_s or req.capture_trigger:
                event["capture"] = {
                    "direction": _capture_direction(req.capture_direction or "past"),
                    "seconds": req.duration_s,
                    "trigger": req.capture_trigger or "native-listener",
                }
            memory_retention_rule = (
                covenant_engine.forbids_retention("memory")
                if covenant_engine is not None
                else None
            )
            if req.remember and memory_retention_rule:
                covenant_rules_applied.append("do_not_retain:memory")
                covenant_withheld.append(
                    {"rule": "do_not_retain", "subject": "memory", "count": 1}
                )
            if covenant_engine is not None:
                event["covenant"] = covenant_engine.event_block(
                    rules_applied=covenant_rules_applied, withheld=covenant_withheld
                )
            event = memory.enrich_event(event)
            trace = None
            if (
                req.remember
                and not memory_retention_rule
                and _privacy_mode(req.privacy_mode) != "incognito"
            ):
                trace = memory.remember(event, tags=["native-system-audio"])
                event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
            background.finish_action(event)
            broadcaster.publish(
                "listen_completed",
                {"listening_event": event, "route_preset": preset.id},
            )
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
        return native_system_audio_temp_status(
            background.config.native_temp_audio_retention
        )

    @app.post("/native/system-audio/cleanup")
    def native_system_audio_cleanup_endpoint(
        req: NativeSystemAudioCleanupRequest,
    ) -> dict[str, object]:
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
        return {
            "version": manifest["version"],
            "route_presets": manifest["route_presets"],
        }

    @app.get("/akouo/schema")
    def akouo_schema_endpoint() -> dict[str, object]:
        manifest = akouo_manifest()
        return {
            "version": manifest["version"],
            "schemas": manifest["schemas"],
            "valid": manifest["valid"],
            "errors": manifest["errors"],
        }

    @app.post("/akouo/route")
    def akouo_route_endpoint(req: AkouoHarnessRequest) -> dict[str, object]:
        try:
            return routing_plan(
                req.path,
                command=req.command,
                evidence_level=evidence_level_for_path(req.path),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/akouo/listen")
    def akouo_listen_endpoint(req: AkouoHarnessRequest) -> dict[str, object]:
        try:
            with engine.request_policy(
                privacy_mode="ephemeral",
                covenant_engine=covenant_store.engine(),
            ) as audio_policy:
                perception = report(engine, req.path, "akouo")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        perception_dict = report_to_dict(perception)
        try:
            command_output = build_harness_output(
                perception_dict,
                command=req.command,
                mode=req.mode,
                question=req.question,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if req.validate_output:
            try:
                AkouoLoader().validate("command-output", command_output)
            except jsonschema.ValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"command output failed AKOUO schema validation: {exc.message}",
                ) from exc
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "perception_report": perception_dict,
            "command_output": command_output,
            "selected_output": (command_output.get("outputs") or [None])[0],
            "listening_identity": audio_policy.listening_identity_block(
                ALL_MOSS_PASSES
            ),
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
    def live_ingest_endpoint(
        session_id: str = Form(...), file: UploadFile = File(...)
    ) -> dict[str, object]:
        try:
            live.ensure_active(session_id)
            saved = save_upload(file)
            return live.ingest_saved_upload(session_id, saved)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/live/capture")
    def live_capture_endpoint(req: LiveCaptureRequest) -> dict[str, object]:
        try:
            capture = (
                live.capture_window(
                    req.session_id, end_seconds=req.end_seconds, seconds=req.seconds
                )
                if req.end_seconds is not None
                else live.capture_last(req.session_id, req.seconds)
            )
            if not req.analyze:
                return capture
            analyzed = analyze_capture(
                capture,
                req.route_preset,
                privacy_mode="ephemeral",
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                song_id=req.song_id,
            )
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

    @app.get("/reasoning/settings")
    def reasoning_settings_endpoint() -> dict[str, object]:
        try:
            loaded = reasoning_settings.load()
            return {
                **settings_to_public(
                    loaded,
                    incognito=bool(background.config.incognito),
                ),
                "application_notes": list(perception_role_notes),
                "resources": resource_assessment(
                    loaded,
                    resident_mode=config.resident_mode,
                    model_overrides={
                        "instruct": config.instruct_model,
                        "thinking": config.thinking_model,
                    },
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.put("/reasoning/settings")
    def reasoning_settings_update_endpoint(
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, object]:
        require_local_admin(request)
        try:
            previous = reasoning_settings.load()
            updated = public_to_settings(payload, previous)
            saved = reasoning_settings.save(updated)
            for provider_id in set(previous.providers) | set(saved.providers):
                if previous.providers.get(provider_id) != saved.providers.get(
                    provider_id
                ):
                    reasoning_budget.invalidate_probes(provider_id)
            notes = record_perception_roles(saved)
            return {
                **settings_to_public(
                    saved, incognito=bool(background.config.incognito)
                ),
                "application_notes": notes,
                "resources": resource_assessment(
                    saved,
                    resident_mode=config.resident_mode,
                    model_overrides={
                        "instruct": config.instruct_model,
                        "thinking": config.thinking_model,
                    },
                ),
            }
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def public_provider_descriptor(descriptor) -> dict[str, Any]:
        provider_id = descriptor.id
        host = descriptor.kind.value == "host_cli"
        network_provider = descriptor.kind.value in {
            "ollama",
            "openai_compatible",
            "openrouter",
            "google",
        }
        return {
            **descriptor.model_dump(mode="json"),
            "label": descriptor.name,
            "installed": bool(descriptor.available) if host else True,
            "reachable": descriptor.available if descriptor.enabled else None,
            "status": (
                "disabled"
                if not descriptor.enabled
                else "ready"
                if descriptor.available
                else "unavailable"
            ),
            "note": descriptor.detail,
            "credential_supported": provider_id
            in {
                "openrouter",
                "openai_compatible",
                "local_audio",
                "google",
                "alibaba",
                "nvidia",
                "opencode",
            },
            "oauth_supported": provider_id == "openrouter",
            "endpoint_configurable": provider_id
            in {
                "ollama",
                "openai_compatible",
                "local_audio",
                "google",
                "alibaba",
                "nvidia",
            },
            "network_provider": network_provider,
        }

    @app.get("/reasoning/providers")
    def reasoning_providers_endpoint() -> dict[str, object]:
        try:
            registry = reasoning_registry()
            return {
                "version": "0.1",
                "providers": [
                    public_provider_descriptor(value)
                    for value in registry.descriptors()
                ],
            }
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/reasoning/models")
    def reasoning_models_endpoint(provider_id: str) -> dict[str, object]:
        try:
            registry = reasoning_registry()
            if provider_id not in registry.ids():
                raise HTTPException(
                    status_code=404, detail=f"unknown reasoning provider: {provider_id}"
                )
            models = [
                value.model_dump(mode="json")
                for value in registry.list_models(provider_id)
            ]
            return {"version": "0.1", "provider_id": provider_id, "models": models}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/reasoning/providers/{provider_id}/probe")
    def reasoning_provider_probe_endpoint(provider_id: str) -> dict[str, object]:
        try:
            registry = reasoning_registry()
            if provider_id not in registry.ids():
                raise HTTPException(
                    status_code=404, detail=f"unknown reasoning provider: {provider_id}"
                )
            return {"provider": public_provider_descriptor(registry.probe(provider_id))}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def credential_name(provider_id: str) -> str:
        return "server_password" if provider_id == "opencode" else "api_key"

    @app.put("/reasoning/providers/{provider_id}/credential")
    def reasoning_provider_credential_endpoint(
        provider_id: str,
        body: ReasoningCredentialRequest,
        request: Request,
    ) -> dict[str, object]:
        require_local_admin(request)
        try:
            settings = reasoning_settings.load()
            provider = settings.providers.get(provider_id)
            if provider_id == "typesafe":
                # The TypeSafe credential lives in the secret store only; the
                # provider stays out of the reasoning registry so Jev remains
                # selectable under decision routing, never language reasoning.
                reasoning_secrets.set(provider_id, body.credential, "api_key")
                if reasoning_budget is not None:
                    reasoning_budget.invalidate_probes(provider_id)
                return {
                    "provider_id": provider_id,
                    "credential_saved": True,
                    "stored_securely": True,
                }
            if provider is None or (
                provider.kind.value not in {"openai_compatible", "openrouter", "google"}
                and provider_id != "opencode"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="this provider does not accept a stored credential",
                )
            name = credential_name(provider_id)
            reasoning_secrets.set(provider_id, body.credential, name)
            providers = dict(settings.providers)
            providers[provider_id] = provider.model_copy(
                update={"credential_ref": name}
            )
            reasoning_settings.save(
                settings.model_copy(update={"providers": providers})
            )
            reasoning_budget.invalidate_probes(provider_id)
            return {
                "provider_id": provider_id,
                "credential_saved": True,
                "stored_securely": True,
            }
        except SecretPersistenceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except SecretStoreError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/reasoning/providers/{provider_id}/credential")
    def reasoning_provider_credential_delete_endpoint(
        provider_id: str,
        request: Request,
    ) -> dict[str, object]:
        require_local_admin(request)
        try:
            settings = reasoning_settings.load()
            if provider_id == "typesafe":
                deleted = reasoning_secrets.delete(provider_id, "api_key")
                if reasoning_budget is not None:
                    reasoning_budget.invalidate_probes(provider_id)
                return {"provider_id": provider_id, "credential_deleted": deleted}
            provider = settings.providers.get(provider_id)
            if provider is None:
                raise HTTPException(
                    status_code=404, detail=f"unknown reasoning provider: {provider_id}"
                )
            deleted = reasoning_secrets.delete(
                provider_id, credential_name(provider_id)
            )
            providers = dict(settings.providers)
            providers[provider_id] = provider.model_copy(
                update={"credential_ref": None}
            )
            reasoning_settings.save(
                settings.model_copy(update={"providers": providers})
            )
            reasoning_budget.invalidate_probes(provider_id)
            return {"provider_id": provider_id, "credential_deleted": deleted}
        except (SecretStoreError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/reasoning/providers/openrouter/oauth/start")
    @app.post("/reasoning/openrouter/oauth/start")
    def reasoning_openrouter_oauth_start_endpoint(
        request: Request,
    ) -> dict[str, object]:
        require_local_admin(request)
        callback = (
            str(request.base_url).rstrip("/") + "/reasoning/openrouter/oauth/callback"
        )
        try:
            return openrouter_oauth.start(callback)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/reasoning/providers/openrouter/oauth/callback",
        response_class=HTMLResponse,
        responses=html_error_response,
    )
    @app.get(
        "/reasoning/openrouter/oauth/callback",
        response_class=HTMLResponse,
        responses=html_error_response,
    )
    def reasoning_openrouter_oauth_callback_endpoint(
        request: Request,
        code: str,
        state: str,
    ) -> Any:
        require_local_admin(request)
        try:
            openrouter_oauth.exchange(code=code, state=state)
        except (
            ValueError,
            SecretStoreError,
            SecretPersistenceUnavailable,
            RuntimeError,
        ) as exc:
            LOGGER.warning("OpenRouter OAuth exchange failed: %s", type(exc).__name__)
            return HTMLResponse(
                status_code=400,
                content=(
                    "<!doctype html><meta charset='utf-8'><title>Oída · OpenRouter</title>"
                    "<h1>OpenRouter connection failed</h1>"
                    "<p>Return to Oída and try the connection again.</p>"
                ),
            )
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>Oída · OpenRouter</title>"
            "<h1>OpenRouter connected</h1><p>The credential is stored securely. You can close this window.</p>"
        )

    def retained_account(identifier):
        from oida.reasoning_context import read_record, retained_event
        from oida.reasoning.evidence import covenant_blocks_untyped_prose

        account = retained_event(read_record(identifier))
        if covenant_blocks_untyped_prose(account.get("covenant")):
            raise HTTPException(423, "Retained account is withheld by covenant")
        return account

    @app.get("/reasoning/accounts/{identifier}")
    def retained_account_endpoint(identifier: str):
        return retained_account(identifier)

    @app.get("/reasoning/budget")
    def reasoning_budget_get_endpoint() -> dict[str, Any]:
        return reasoning_budget.state()

    @app.put("/reasoning/budget")
    def reasoning_budget_update_endpoint(
        body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        require_local_admin(request)
        try:
            # Found while enabling the Gemini trial for G7. update_config returns
            # a BudgetConfig model and this route is typed as returning a dict,
            # so FastAPI rejected the *response* with a 500 after the config had
            # already been applied - the caller was told the request failed
            # while the ledger had in fact been changed. Return the ledger's
            # own state view, which is what GET returns, so the two agree.
            reasoning_budget.update_config(body)
            return reasoning_budget.state()
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/reasoning/providers/{provider_id}/probe-audio")
    def reasoning_provider_probe_audio_endpoint(
        provider_id: str,
        request: Request,
        body: AudioProbeRequest | None = None,
    ) -> dict[str, Any]:
        if request is not None:
            require_local_admin(request)
        model_id = body.model_id if body else None

        def probe():
            result = engine.probe_audio(provider_id, model_id)
            if not result.get("ok"):
                raise HTTPException(status_code=400, detail=result)
            return result

        return operations.run(body.operation_id if body else None, probe)

    @app.get("/conversation")
    def conversation_list_endpoint(
        event_id: str | None = None, limit: int = 100
    ) -> dict[str, object]:
        values = conversations.list(event_id=event_id, limit=limit)
        return {"version": "0.2", "conversations": values, "count": len(values)}

    @app.post("/conversation/ask")
    def conversation_ask_endpoint(req: ConversationAskRequest) -> dict[str, object]:
        event = conversation_event(req)
        comparisons = comparison_events(req.comparison_event_ids, event.get("id"))
        try:
            return reasoning.ask(
                event=event,
                question=req.question,
                options=conversation_options(req, comparisons),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/conversation/ask/stream", responses=event_stream_response)
    async def conversation_ask_stream_endpoint(req: ConversationAskRequest) -> Any:
        event = conversation_event(req)
        comparisons = comparison_events(req.comparison_event_ids, event.get("id"))
        options = conversation_options(req, comparisons)

        async def stream():
            yield (
                "event: started\ndata: "
                + json.dumps(
                    {
                        "type": "started",
                        "provider_id": options.provider_id or "configured",
                    }
                )
                + "\n\n"
            )
            try:
                result = await asyncio.to_thread(
                    reasoning.ask,
                    event=event,
                    question=req.question,
                    options=options,
                )
                relisten = (
                    result.get("turn", {}).get("relisten")
                    if isinstance(result.get("turn"), dict)
                    else None
                )
                if relisten:
                    yield (
                        "event: relisten_completed\ndata: "
                        + json.dumps(
                            {"type": "relisten_completed", "relisten": relisten},
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                yield (
                    "event: completed\ndata: "
                    + json.dumps(
                        {"type": "completed", "response": result}, ensure_ascii=False
                    )
                    + "\n\n"
                )
            except Exception:
                LOGGER.exception("reasoning conversation stream failed")
                yield (
                    "event: error\ndata: "
                    + json.dumps(
                        {"type": "error", "detail": "reasoning request failed"},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/conversation/prepare")
    def conversation_prepare_endpoint(req: ConversationAskRequest) -> dict[str, object]:
        event = conversation_event(req)
        comparisons = comparison_events(req.comparison_event_ids, event.get("id"))
        try:
            return reasoning.prepare(
                event=event,
                question=req.question,
                options=conversation_options(req, comparisons),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/conversation/commit")
    def conversation_commit_endpoint(
        req: ConversationCommitRequest,
    ) -> dict[str, object]:
        try:
            return reasoning.commit_prepared(
                token=req.prepare_token,
                response=req.response,
            )
        except ResponseValidationError as exc:
            raise HTTPException(
                status_code=422, detail={"message": str(exc), "errors": exc.errors}
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/conversation/{conversation_id}")
    def conversation_get_endpoint(conversation_id: str) -> dict[str, object]:
        try:
            return {
                "version": "0.2",
                "conversation": conversations.get(conversation_id),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/conversation/{conversation_id}")
    def conversation_delete_endpoint(conversation_id: str) -> dict[str, object]:
        if not conversations.delete(conversation_id):
            raise HTTPException(
                status_code=404, detail=f"unknown conversation: {conversation_id}"
            )
        return {"version": "0.2", "conversation_id": conversation_id, "deleted": True}

    @app.post("/generation/prompt")
    def generation_prompt_endpoint(req: GenerationPromptRequest) -> dict[str, object]:
        event = dict(req.event) if isinstance(req.event, dict) else None
        if event is None and isinstance(background.state.latest_event, dict):
            event = dict(background.state.latest_event)
        if event is None:
            raise HTTPException(
                status_code=400, detail="generation prompt requires a listening event"
            )
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
    def generation_relisten_endpoint(
        req: GenerationRelistenRequest,
    ) -> dict[str, object]:
        try:
            generation = generations.get(req.generation_id)
            preset = route_preset(req.route_preset)
            output_path = _require_existing_path(req.path)
            privacy_mode = _privacy_mode(req.privacy_mode)
            covenant_engine, passes, covenant_rules_applied = (
                prepare_covenant_perception(
                    output_path,
                    source_type="generated",
                    passes=preset.moss_passes,
                )
            )
            covenant_withheld: list[dict[str, object]] = []
            with engine.request_policy(
                privacy_mode=privacy_mode,
                covenant_engine=covenant_engine,
                covenant_block=(
                    generation.get("source_event", {}).get("covenant")
                    if isinstance(generation.get("source_event"), dict)
                    and isinstance(
                        generation.get("source_event", {}).get("covenant"), dict
                    )
                    else None
                ),
            ) as audio_policy:
                perception = report(
                    engine,
                    str(output_path),
                    f"oida-generation-relisten-{preset.id}",
                    passes=passes,
                    chunk_seconds=config.moss_chunk_seconds,
                    overlap_seconds=_chunk_overlap(config),
                )
            perception_dict = report_to_dict(perception)
            if covenant_engine is not None:
                perception_dict, perception_withheld = (
                    covenant_engine.redact_perception(perception_dict)
                )
                covenant_withheld.extend(perception_withheld)
            command_output = build_harness_output(
                perception_dict, command=preset.akouo_command
            )
            if covenant_engine is not None:
                command_output, claim_withheld = covenant_engine.redact_command_output(
                    command_output
                )
                covenant_withheld.extend(claim_withheld)
            event = listening_event_dict(
                perception_dict,
                command_output=command_output,
                route_preset_id=preset.id,
                enabled_skill_ids=req.enabled_skill_ids,
                disabled_skill_ids=req.disabled_skill_ids,
                privacy_mode=privacy_mode,
                raw_audio_policy="external_ref",
                listening_identity=audio_policy.listening_identity_block(passes),
                # Compared against event labels so an orientation cannot become
                # the account's title. Never stored: the block above is a digest.
                identity_text=audio_policy.listening_identity.text,
            )
            if covenant_engine is not None:
                event["covenant"] = covenant_engine.event_block(
                    rules_applied=covenant_rules_applied,
                    withheld=covenant_withheld,
                )
            event = memory.enrich_event(event)
            trace = None
            memory_refusal = (
                covenant_engine.forbids_retention("memory")
                if covenant_engine is not None
                else None
            )
            if req.remember and memory_refusal:
                event.setdefault("covenant", {}).setdefault("withheld", []).append(
                    {"rule": "do_not_retain", "subject": "memory", "count": 1}
                )
            elif req.remember and privacy_mode != "incognito":
                trace = memory.remember(
                    event,
                    tags=["generation", f"source-{generation.get('source_event_id')}"],
                )
                event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
            route_comparison = compare_route_events(
                generation.get("source_event")
                if isinstance(generation.get("source_event"), dict)
                else {},
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
            return {
                "qa": {
                    "question": req.question,
                    "answer": "",
                    "reasoning_trace": None,
                    "thinking_budget": req.thinking_budget,
                },
                "forbidden_topics_triggered": forbidden,
            }
        try:
            path = _require_existing_path(req.path)
            result, engine_result = qa(
                engine,
                str(path),
                req.question,
                req.thinking_budget,
                context=req.context,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "qa": dump_model(result),
            "engine": dump_model(engine_result),
            "forbidden_topics_triggered": [],
        }

    @app.post("/think")
    def think_endpoint(req: ThinkRequest) -> dict[str, object]:
        forbidden = forbidden_topics_for_text(req.instruction)
        if forbidden:
            return {
                "qa": {
                    "question": req.instruction,
                    "answer": "",
                    "reasoning_trace": None,
                    "thinking_budget": req.thinking_budget,
                },
                "forbidden_topics_triggered": forbidden,
            }
        try:
            path = _require_existing_path(req.path)
            result, engine_result = think(
                engine, str(path), req.instruction, req.thinking_budget
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "qa": dump_model(result),
            "engine": dump_model(engine_result),
            "forbidden_topics_triggered": [],
        }

    @app.post("/report")
    def report_endpoint(req: ReportRequest) -> dict[str, object]:
        try:
            with engine.request_policy(
                privacy_mode="ephemeral",
                covenant_engine=covenant_store.engine(),
            ) as audio_policy:
                result = report(
                    engine,
                    req.path,
                    req.profile,
                    chunk_seconds=config.moss_chunk_seconds,
                    overlap_seconds=_chunk_overlap(config),
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = report_to_dict(result)
        payload["listening_identity"] = audio_policy.listening_identity_block(
            ALL_MOSS_PASSES
        )
        return payload

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
        traces = memory.list(
            q,
            tag=tag,
            source_kind=source_kind,
            route=route,
            since=since,
            until=until,
            limit=limit,
        )
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
        return memory.export_json(
            query=q,
            tag=tag,
            source_kind=source_kind,
            route=route,
            since=since,
            until=until,
            limit=limit,
        )

    @app.get("/memory/trace/{trace_id}")
    def memory_trace_endpoint(trace_id: str) -> dict[str, object]:
        try:
            return {
                "trace": memory.get(trace_id),
                "similar": memory.similar_to_trace(trace_id),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/memory/remember")
    def memory_remember_endpoint(req: MemoryRememberRequest) -> dict[str, object]:
        event = dict(req.event)
        return remember_event(
            event,
            user_notes=req.user_notes,
            human_listener_id=req.human_listener_id,
            human_display_name=req.human_display_name,
            tags=req.tags,
        )

    @app.post("/memory/human-listening")
    def memory_human_listening_endpoint(
        req: HumanListeningRememberRequest,
    ) -> dict[str, object]:
        from .akousma_bridge import build_human_response_akousma, persist_akousma

        store = None
        try:
            import akousma

            store = akousma.AkousmataStore()
            machine = store.get(req.machine_akousma_id)
            if not isinstance(machine, dict):
                raise KeyError(req.machine_akousma_id)
            if akousma.record_class(machine) not in {"agent", "hybrid", "plural_other"}:
                raise ValueError(
                    "the response target must be a machine or machine-involved listening record"
                )
            human_profile = human_account_profile(
                req.human_listener_id,
                req.human_display_name,
            )
            human = build_human_response_akousma(
                machine,
                req.machine_akousma_id,
                req.note,
                listener_id=human_profile["listener_id"],
                display_name=human_profile["display_name"] or None,
                privacy=human_profile["privacy"],
            )
            human_akousma_id = persist_akousma(human, store=store)
            owner_journal.record_reference(human_akousma_id, record=human)
            return {
                "machine_akousma_id": req.machine_akousma_id,
                "human_akousma_id": human_akousma_id,
                "record_class": "human",
                "heard_claimed": False,
            }
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="machine Akousmata record not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if store is not None:
                store.close()

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

    from oida.source_api import source_router

    def source_preflight(source_type, seconds, preset_id, remember):
        if preset_id is not None:
            route_preset(preset_id)
        checker = covenant_store.engine()
        if checker is None:
            return
        refusal = checker.refuse_source(source_type) or checker.refuse_quiet_hours()
        if refusal:
            raise HTTPException(423, "source acquisition withheld by active covenant")
        limit = checker.max_window_seconds()
        if seconds is not None and limit is not None and seconds > limit:
            raise HTTPException(423, "source acquisition exceeds covenant window")
        if remember and checker.forbids_retention("memory"):
            raise HTTPException(423, "source remembering withheld by active covenant")

    from oida.transposition import transposition_router

    def transposition_preflight(source_type, seconds, preset_id, remember):
        source_preflight(source_type, seconds, preset_id, remember)
        checker = covenant_store.engine()
        if checker is not None and checker.forbids_retention("raw-audio"):
            raise HTTPException(
                423, "audio derivative retention withheld by active covenant"
            )

    app.include_router(
        transposition_router(
            config.data_dir, operations, owner_journal, transposition_preflight
        )
    )

    from oida.agent_native import native_router

    native_routes, native_listen = native_router(
        operations,
        owner_journal,
        source_preflight,
        transposition_preflight,
        lambda: background.config.incognito,
        lambda: apply_current_conversation_covenant({}).get("covenant", {}),
    )
    app.include_router(native_routes)

    from oida.ensemble_runtime import runtime_router

    app.include_router(
        runtime_router(operations, owner_journal, source_preflight, reasoning)
    )
    from oida.record_exchange import exchange_router

    app.include_router(exchange_router(operations, owner_journal, source_preflight))
    from oida.reasoning_workspace import Workspace, workspace_router

    def workspace_event_policy(event):
        event = apply_current_conversation_covenant(event)
        if background.config.incognito:
            event = {**event, "privacy_mode": "incognito"}
        return event

    reasoning_workspace = Workspace(
        owner_journal,
        conversations,
        reasoning,
        reasoning_settings,
        operations,
        workspace_event_policy,
    )
    source_shutdown_callbacks.append(reasoning_workspace.close)
    app.include_router(
        workspace_router(
            reasoning_workspace,
            reasoning_providers_endpoint,
            reasoning_models_endpoint,
            require_local_admin,
            reasoning_budget.invalidate_probes,
        )
    )
    from oida.research_bridge import research_router

    app.include_router(research_router())
    from oida.agent_reports import retained_report_router

    app.include_router(
        retained_report_router(operations, owner_journal, source_preflight)
    )
    from oida.digital_sectors import sector_router

    app.include_router(
        sector_router(
            operations,
            owner_journal,
            source_preflight,
            os.environ.get("OIDA_MASA_VALIDATOR_MODULE"),
        )
    )

    from oida.device_inputs import input_router

    app.include_router(input_router(live, source_shutdown_callbacks))
    app.include_router(
        source_router(
            config.data_dir,
            lambda payload: gateway_listen_endpoint(
                GatewayListenRequest.model_validate(payload)
            ),
            source_preflight,
            shutdown_callbacks=source_shutdown_callbacks,
            journal=owner_journal,
            operations=operations,
            validate_listening=validate_listening_selection,
            retention_forbidden=lambda: bool(
                (checker := covenant_store.engine())
                and (
                    checker.forbids_retention("raw-audio")
                    or checker.forbids_retention("memory")
                )
            ),
            scanned_roots=(uploads_dir(),),
        )
    )

    return app


_CAPTURE_DIRECTIONS = {"past", "future", "live"}


def _capture_direction(value: str) -> str:
    direction = str(value or "").strip().lower()
    if direction not in _CAPTURE_DIRECTIONS:
        raise ValueError(
            f"capture_direction must be one of {sorted(_CAPTURE_DIRECTIONS)}, got {direction!r}"
        )
    return direction


def _capture_info(req: "ListenEventRequest") -> dict[str, object] | None:
    """Normalize the spec v1.2 capture block from a listen request."""
    if (
        req.capture_direction is None
        and req.capture_seconds is None
        and not req.capture_trigger
    ):
        return None
    info: dict[str, object] = {}
    if req.capture_direction is not None:
        info["direction"] = _capture_direction(req.capture_direction)
    if req.capture_seconds is not None:
        seconds = float(req.capture_seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("capture_seconds must be a finite number >= 0")
        info["seconds"] = seconds
    if req.capture_trigger:
        info["trigger"] = str(req.capture_trigger)
    info["triggered_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return info


def _validated_location(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Light spec v1.2 location validation; extra keys pass through."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("location must be an object with lat/lon")
    lat, lon = value.get("lat"), value.get("lon")
    if (
        not isinstance(lat, (int, float))
        or not math.isfinite(float(lat))
        or not -90.0 <= float(lat) <= 90.0
    ):
        raise ValueError("location.lat must be a number in [-90, 90]")
    if (
        not isinstance(lon, (int, float))
        or not math.isfinite(float(lon))
        or not -180.0 <= float(lon) <= 180.0
    ):
        raise ValueError("location.lon must be a number in [-180, 180]")
    normalized = {**value, "lat": float(lat), "lon": float(lon)}
    for key in ("accuracy_m", "altitude_m"):
        if key not in value or value[key] is None:
            continue
        item = value[key]
        if not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise ValueError(f"location.{key} must be a finite number")
        if key == "accuracy_m" and float(item) < 0:
            raise ValueError("location.accuracy_m must be >= 0")
        normalized[key] = float(item)
    return normalized


def save_upload(file: UploadFile) -> dict[str, object]:
    from oida.operation_control import checkpoint, OperationCancelled

    checkpoint()
    upload_root = uploads_dir()
    upload_root.mkdir(parents=True, exist_ok=True)
    original = sanitize_filename(file.filename or "recording.webm")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = upload_root / f"{stamp}-{secrets.token_hex(4)}-{original}"
    try:
        total = 0
        with raw_path.open("wb") as handle:
            while True:
                checkpoint()
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
    except (OSError, OperationCancelled):
        cleanup_failed_upload(raw_path)
        raise

    try:
        checkpoint()
        normalized_path, normalization_error = normalize_audio(raw_path)
        checkpoint(seal=True)
    except OperationCancelled:
        cleanup_failed_upload(raw_path)
        raise
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
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return (
            path,
            "ffmpeg is required to convert uploaded or recorded non-WAV audio to WAV.",
        )
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(path),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(target),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return (
            path,
            "ffmpeg timed out converting the uploaded audio; the file may be malformed.",
        )
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
    data_ref = (
        segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
    )
    path_value = path_override or data_ref.get("uri")
    if not path_value:
        raise ValueError(
            "route rerun requires an event segment with data_ref.uri or an explicit path"
        )
    path = Path(str(path_value)).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"route rerun audio path does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"route rerun audio path is not a file: {path}")

    resolved_privacy = _privacy_mode(
        str(
            privacy_mode
            or event.get("privacy_mode")
            or segment.get("privacy_mode")
            or "session"
        )
    )
    resolved_policy = _raw_audio_policy(
        str(
            raw_audio_policy
            or event.get("raw_audio_policy")
            or _segment_raw_audio_policy(segment)
            or "external_ref"
        )
    )
    metadata = (
        dict(segment.get("metadata"))
        if isinstance(segment.get("metadata"), dict)
        else {}
    )
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


def _source_descriptor_from_event(
    event: dict[str, object], path: Path
) -> AudioSourceDescriptor:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    source_type = str(source.get("type") or "file")
    if source_type not in {
        "live_input",
        "system_output",
        "file",
        "buffer",
        "generated",
        "external_stream",
    }:
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
    metadata = (
        segment.get("metadata") if isinstance(segment.get("metadata"), dict) else {}
    )
    value = metadata.get("raw_audio_policy")
    return str(value) if value else None


def _event_route_ids(event: dict[str, object]) -> list[str]:
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    return [
        str(route.get("route_id"))
        for route in routes
        if isinstance(route, dict) and route.get("route_id")
    ]


def _stable_json_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _privacy_mode(value: str) -> PrivacyMode:
    if value in {"ephemeral", "session", "saved", "incognito"}:
        return value  # type: ignore[return-value]
    raise ValueError(f"unknown privacy mode: {value}")


def _raw_audio_policy(value: str) -> RawAudioPolicy:
    if value in {"not_stored", "temp", "saved", "external_ref"}:
        return value  # type: ignore[return-value]
    raise ValueError(f"unknown raw audio policy: {value}")


def _audio_source_type(value: str) -> SourceType:
    if value in {
        "live_input",
        "system_output",
        "file",
        "buffer",
        "generated",
        "external_stream",
    }:
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
    parser = argparse.ArgumentParser(
        description="Run the oida localhost perception daemon."
    )
    parser.add_argument(
        "--profile", default=None, choices=["mac-mps", "cuda-server", "stub"]
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None, type=int)
    args = parser.parse_args()
    config = load_config(profile=args.profile, host=args.host, port=args.port)
    import uvicorn

    uvicorn.run(
        create_app(profile=config.profile, host=config.host, port=config.port),
        host=config.host,
        port=config.port,
    )


if __name__ == "__main__":
    main()
