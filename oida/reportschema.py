from __future__ import annotations

from typing import Any, Literal

try:
    from pydantic import BaseModel, ConfigDict, Field
except ModuleNotFoundError:  # Allows local stdlib tests before uv sync.
    BaseModel = object  # type: ignore[assignment,misc]
    ConfigDict = dict  # type: ignore[assignment]

    class _FallbackField:
        def __init__(self, default: Any = None, default_factory: Any = None) -> None:
            self.default = default
            self.default_factory = default_factory

        def value(self) -> Any:
            if self.default_factory is not None:
                return self.default_factory()
            return self.default

    def Field(default: Any = None, **kwargs: Any) -> Any:  # type: ignore[misc]
        return _FallbackField(default=default, default_factory=kwargs.get("default_factory"))


if BaseModel is object:

    class JsonModel:
        def __init__(self, **data: Any) -> None:
            annotations: dict[str, Any] = {}
            for cls in reversed(type(self).mro()):
                annotations.update(getattr(cls, "__annotations__", {}))
            for key in annotations:
                if key in data:
                    setattr(self, key, data.pop(key))
                    continue
                default = getattr(type(self), key, None)
                if isinstance(default, _FallbackField):
                    setattr(self, key, default.value())
                elif default is not None:
                    setattr(self, key, default)
            for key, value in data.items():
                setattr(self, key, value)

        def model_dump(self, **_: Any) -> dict[str, Any]:
            return {key: _dump_value(value) for key, value in vars(self).items()}

        @classmethod
        def model_validate(cls, data: dict[str, Any]) -> "JsonModel":
            return cls(**data)

else:

    class JsonModel(BaseModel):  # type: ignore[misc,valid-type]
        model_config = ConfigDict(extra="forbid")


def _dump_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump_value(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {key: _dump_value(item) for key, item in vars(value).items()}
    return value


class SourceInfo(JsonModel):
    path: str
    duration_s: float | None = None
    sr_native: int | None = None
    channels: int | None = None
    sha256: str | None = None


class ChunkInfo(JsonModel):
    i: int
    t0: float
    t1: float
    source_path: str | None = None


class EngineParams(JsonModel):
    temperature: float
    top_p: float
    top_k: int


class EngineInfo(JsonModel):
    daemon: str = "oida/0.1"
    model: str
    profile: str
    params: EngineParams
    thinking_budget: int | None = Field(default=None, ge=0)
    chunks: list[ChunkInfo] = Field(default_factory=list)
    wall_ms: int | None = None
    unavailable_reason: str | None = None


class TranscriptSegment(JsonModel):
    t0: float | None = None
    t1: float | None = None
    text: str
    confidence: Literal["high", "medium", "low", "undetermined"] = "medium"


class Transcript(JsonModel):
    present: bool = False
    language: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Event(JsonModel):
    t0: float | None = None
    t1: float | None = None
    label: str
    description: str = ""
    corroborated_by_dsp: bool = False
    confidence: Literal["high", "medium", "low", "undetermined"] = "medium"


class Caption(JsonModel):
    dense: str | None = None
    brief: str | None = None


class Speech(JsonModel):
    present: bool = False
    dimensions: dict[str, str] = Field(default_factory=dict)
    identity_caution: bool = True
    notes: list[str] = Field(default_factory=list)


class Music(JsonModel):
    present: bool = False
    description: str | None = None
    tempo_feel: str | None = None
    dsp_bpm_candidate: float | None = None
    moss_bpm_candidate: float | None = None
    notes: list[str] = Field(default_factory=list)


class QaItem(JsonModel):
    question: str
    answer: str
    reasoning_trace: str | None = None
    thinking_budget: int | None = Field(default=None, ge=0)


class PerceptionReport(JsonModel):
    version: str = "0.1"
    source: SourceInfo
    engine: EngineInfo
    dsp: dict[str, Any] = Field(default_factory=dict)
    transcript: Transcript = Field(default_factory=Transcript)
    events: list[Event] = Field(default_factory=list)
    caption: Caption = Field(default_factory=Caption)
    speech: Speech = Field(default_factory=Speech)
    music: Music = Field(default_factory=Music)
    qa: list[QaItem] = Field(default_factory=list)
    model_uncertainty_notes: list[str] = Field(default_factory=list)
    forbidden_topics_triggered: list[str] = Field(default_factory=list)
    signal_interpretation: dict[str, Any] | None = None
    moss_passes: list[str] = Field(default_factory=list)


def dump_model(model: Any) -> dict[str, Any]:
    return _dump_value(model)
