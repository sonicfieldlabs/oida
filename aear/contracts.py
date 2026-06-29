from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from aear.dsp import inspect_path

SourceType = Literal["live_input", "system_output", "file", "buffer", "generated", "external_stream"]
AudioFormat = Literal["pcm_f32", "pcm_i16", "wav", "flac", "mp3", "other"]
PrivacyMode = Literal["ephemeral", "session", "saved", "incognito"]
RawAudioPolicy = Literal["not_stored", "temp", "saved", "external_ref"]


@dataclass(frozen=True)
class AudioDataRef:
    kind: Literal["path", "memory", "external", "none"]
    uri: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class AudioSourceDescriptor:
    type: SourceType
    label: str
    device_id: str | None = None
    platform: str | None = None
    supported: bool = True
    status: str = "ready"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioTimeRange:
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class AudioSegment:
    id: str
    source: AudioSourceDescriptor
    created_at: str
    duration_ms: int
    sample_rate: int
    channels: int
    format: AudioFormat
    data_ref: AudioDataRef
    bit_depth: int | None = None
    captured_at: str | None = None
    time_range: AudioTimeRange | None = None
    ephemeral: bool = True
    user_initiated: bool = True
    privacy_mode: PrivacyMode = "ephemeral"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ListeningHypothesis:
    statement: str
    confidence: str = "undetermined"
    basis: str | None = None


@dataclass(frozen=True)
class ListeningNextAction:
    id: str
    label: str
    route_preset: str | None = None


@dataclass(frozen=True)
class ListeningAggregate:
    title: str
    short_summary: str
    detailed_summary: str
    primary_tags: list[str] = field(default_factory=list)
    hypotheses: list[ListeningHypothesis] = field(default_factory=list)
    signal_facts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_actions: list[ListeningNextAction] = field(default_factory=list)


@dataclass(frozen=True)
class ListeningRouteResult:
    route_id: str
    route_name: str
    skill_ids: list[str]
    model_observations: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    uncertainty: list[str] = field(default_factory=list)
    suggested_next_routes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AkousmataLinks:
    similar_trace_ids: list[str] = field(default_factory=list)
    saved_trace_id: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ListeningArtifact:
    kind: str
    label: str
    ref: str


@dataclass(frozen=True)
class ListeningEvent:
    id: str
    created_at: str
    source: AudioSourceDescriptor
    segment: AudioSegment
    routes: list[ListeningRouteResult]
    aggregate: ListeningAggregate
    features: dict[str, Any]
    memory: AkousmataLinks = field(default_factory=AkousmataLinks)
    artifacts: list[ListeningArtifact] = field(default_factory=list)
    user_notes: str | None = None
    tags: list[str] = field(default_factory=list)
    privacy_mode: PrivacyMode = "session"
    raw_audio_policy: RawAudioPolicy = "external_ref"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value


def audio_format_for_path(path: str | Path) -> AudioFormat:
    suffix = Path(path).suffix.lower()
    if suffix in {".wav", ".wave"}:
        return "wav"
    if suffix == ".flac":
        return "flac"
    if suffix == ".mp3":
        return "mp3"
    return "other"


def source_for_path(
    path: str | Path,
    source_type: SourceType = "file",
    label: str | None = None,
    *,
    device_id: str | None = None,
    platform: str | None = None,
    details: dict[str, Any] | None = None,
) -> AudioSourceDescriptor:
    audio_path = Path(path)
    if label is None:
        if source_type == "buffer":
            label = "Captured buffer"
        elif source_type == "system_output":
            label = "System audio"
        elif source_type == "live_input":
            label = "Live input"
        else:
            label = audio_path.name or "Audio file"
    return AudioSourceDescriptor(
        type=source_type,
        label=label,
        device_id=device_id,
        platform=platform,
        details={"path": str(audio_path.expanduser().resolve()), **(details or {})},
    )


def audio_segment_from_path(
    path: str | Path,
    *,
    source: AudioSourceDescriptor | None = None,
    source_type: SourceType = "file",
    privacy_mode: PrivacyMode = "session",
    raw_sha256: str | None = None,
    ephemeral: bool = False,
    user_initiated: bool = True,
    captured_at: str | None = None,
    time_range: AudioTimeRange | None = None,
    metadata: dict[str, Any] | None = None,
) -> AudioSegment:
    dsp = inspect_path(path)
    audio_path = Path(path).expanduser().resolve()
    duration_s = float(dsp.get("durationSeconds") or 0.0)
    sample_rate = int(dsp.get("sampleRate") or 0)
    channels = int(dsp.get("channelCount") or 0)
    return AudioSegment(
        id=new_id("seg"),
        source=source or source_for_path(audio_path, source_type=source_type),
        created_at=now_iso(),
        captured_at=captured_at,
        duration_ms=round(duration_s * 1000),
        sample_rate=sample_rate,
        channels=channels,
        format=audio_format_for_path(audio_path),
        data_ref=AudioDataRef(kind="path", uri=str(audio_path), sha256=raw_sha256 or str(dsp.get("sha256") or "")),
        time_range=time_range,
        ephemeral=ephemeral,
        user_initiated=user_initiated,
        privacy_mode=privacy_mode,
        metadata={**(metadata or {}), "dsp": dsp},
    )
