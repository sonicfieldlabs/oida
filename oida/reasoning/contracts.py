from __future__ import annotations

import ipaddress
import math
import re
from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REASONING_SETTINGS_CONTRACT = "oida/reasoning-settings/v0.2"
EVIDENCE_PACKET_CONTRACT = "oida/evidence-packet/v0.1"
REASONING_RESPONSE_CONTRACT = "oida/reasoning-response/v0.1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderKind(StrEnum):
    LOCAL_STRUCTURED = "local_structured"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENROUTER = "openrouter"
    GOOGLE = "google"
    HOST_CLI = "host_cli"


class ProviderLocality(StrEnum):
    LOCAL = "local"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class ProviderStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class ModelRole(StrEnum):
    FAST_PERCEPTION = "fast_perception"
    DEEP_PERCEPTION = "deep_perception"
    TRANSCRIPTION = "transcription"
    MUSIC_ANALYSIS = "music_analysis"
    CONVERSATION = "conversation"
    TARGETED_RELISTEN = "targeted_relisten"


class Tone(StrEnum):
    PLAIN = "plain"
    WARM = "warm"
    RESEARCH = "research"
    POETIC = "poetic"


class Depth(StrEnum):
    BRIEF = "brief"
    BALANCED = "balanced"
    DEEP = "deep"


class Initiative(StrEnum):
    ANSWER_ONLY = "answer_only"
    SUGGEST_FOLLOWUPS = "suggest_followups"
    DIALOGUE = "dialogue"


class Focus(StrEnum):
    SIGNAL = "signal"
    MUSIC = "music"
    PRODUCTION = "production"
    VOICE = "voice"
    ECOLOGY = "ecology"
    ACCESSIBILITY = "accessibility"
    MEMORY = "memory"


class ReasoningProfile(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    tone: Tone = Tone.WARM
    depth: Depth = Depth.BALANCED
    initiative: Initiative = Initiative.SUGGEST_FOLLOWUPS
    focus: list[Focus] = Field(default_factory=list, max_length=7)
    language: str = Field(default="auto", min_length=2, max_length=35)
    custom_instructions: str = Field(default="", max_length=4000)

    @field_validator("custom_instructions")
    @classmethod
    def normalize_custom_instructions(cls, value: str) -> str:
        return value.strip()


class ProviderSettings(StrictModel):
    kind: ProviderKind
    enabled: bool = False
    locality: ProviderLocality = ProviderLocality.UNKNOWN
    base_url: str | None = Field(default=None, max_length=2048)
    default_model: str | None = Field(default=None, max_length=255)
    credential_ref: str | None = Field(default=None, max_length=255)
    options: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @field_validator("base_url")
    @classmethod
    def reject_credentials_in_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not embed credentials")
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_FIELD_PARTS):
                raise ValueError("base_url must not contain secret-bearing query parameters")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("base_url contains an invalid port") from exc
        host = parsed.hostname.lower().rstrip(".")
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if parsed.scheme == "http" and not loopback:
            raise ValueError("plain HTTP base_url is allowed only on a loopback host")
        return value.strip().rstrip("/")

    @field_validator("options")
    @classmethod
    def reject_secret_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        _assert_no_secret_fields(value)
        return value


class RoleAssignment(StrictModel):
    provider_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    model_id: str | None = Field(default=None, max_length=255)


def default_profiles() -> dict[str, ReasoningProfile]:
    profile = ReasoningProfile(id="grounded-companion", name="Grounded companion")
    return {profile.id: profile}


def default_role_assignments() -> dict[ModelRole, RoleAssignment]:
    return {
        ModelRole.FAST_PERCEPTION: RoleAssignment(provider_id="oida_moss", model_id="instruct"),
        ModelRole.DEEP_PERCEPTION: RoleAssignment(provider_id="oida_moss", model_id="thinking"),
        ModelRole.TRANSCRIPTION: RoleAssignment(provider_id="oida_moss", model_id="instruct"),
        ModelRole.MUSIC_ANALYSIS: RoleAssignment(provider_id="oida_moss", model_id="thinking"),
        ModelRole.CONVERSATION: RoleAssignment(provider_id="local_structured"),
        ModelRole.TARGETED_RELISTEN: RoleAssignment(provider_id="oida_moss", model_id="thinking"),
    }


def default_provider_settings() -> dict[str, ProviderSettings]:
    providers = {
        "local_structured": ProviderSettings(
            kind=ProviderKind.LOCAL_STRUCTURED,
            enabled=True,
            locality=ProviderLocality.LOCAL,
        ),
        "oida_moss": ProviderSettings(
            kind=ProviderKind.OPENAI_COMPATIBLE,
            enabled=True,
            locality=ProviderLocality.LOCAL,
        ),
        "ollama": ProviderSettings(
            kind=ProviderKind.OLLAMA,
            enabled=False,
            locality=ProviderLocality.LOCAL,
            base_url="http://127.0.0.1:11434",
        ),
        "openai_compatible": ProviderSettings(
            kind=ProviderKind.OPENAI_COMPATIBLE,
            enabled=False,
            locality=ProviderLocality.UNKNOWN,
        ),
        "openrouter": ProviderSettings(
            kind=ProviderKind.OPENROUTER,
            enabled=False,
            locality=ProviderLocality.EXTERNAL,
            base_url="https://openrouter.ai/api/v1",
            options={"audio_capable": True, "audio_transport": "openai_input_audio"},
        ),
        "local_audio": ProviderSettings(
            kind=ProviderKind.OPENAI_COMPATIBLE,
            enabled=False,
            locality=ProviderLocality.LOCAL,
            base_url="http://127.0.0.1:8001/v1",
            options={"audio_capable": True, "audio_transport": "catalog"},
        ),
        "google": ProviderSettings(
            kind=ProviderKind.GOOGLE,
            enabled=False,
            locality=ProviderLocality.EXTERNAL,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            default_model="gemini-3.5-flash",
        ),
        "alibaba": ProviderSettings(
            kind=ProviderKind.OPENAI_COMPATIBLE,
            enabled=False,
            locality=ProviderLocality.EXTERNAL,
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            default_model="qwen3.5-omni-flash",
            options={"audio_capable": True, "audio_transport": "catalog", "stream": True},
        ),
        "nvidia": ProviderSettings(
            kind=ProviderKind.OPENAI_COMPATIBLE,
            enabled=False,
            locality=ProviderLocality.EXTERNAL,
            base_url="https://integrate.api.nvidia.com/v1",
            default_model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            options={
                "audio_capable": True,
                "audio_transport": "catalog",
                # The prototype exposes OpenAI-compatible chat, but Oída does
                # not assume native JSON-schema enforcement. Its own validator
                # and one bounded repair attempt remain authoritative.
                "supports_json_schema": False,
            },
        ),
    }
    for provider_id in ("codex", "claude", "hermes", "openclaw", "opencode"):
        providers[provider_id] = ProviderSettings(
            kind=ProviderKind.HOST_CLI,
            enabled=False,
            locality=ProviderLocality.UNKNOWN,
        )
    return providers


class ReasoningSettings(StrictModel):
    contract: Literal[REASONING_SETTINGS_CONTRACT] = REASONING_SETTINGS_CONTRACT
    providers: dict[str, ProviderSettings] = Field(
        default_factory=default_provider_settings,
        min_length=1,
        max_length=64,
    )
    roles: dict[ModelRole, RoleAssignment] = Field(
        default_factory=default_role_assignments,
        min_length=6,
        max_length=6,
    )
    profiles: dict[str, ReasoningProfile] = Field(
        default_factory=default_profiles,
        min_length=1,
        max_length=64,
    )
    active_profile_id: str = "grounded-companion"
    include_transcript: bool = False
    include_memory_content: bool = False
    allow_targeted_relisten: bool = True
    allow_external_audio: bool = False

    @model_validator(mode="after")
    def validate_references(self) -> "ReasoningSettings":
        expected_roles = set(ModelRole)
        if set(self.roles) != expected_roles:
            missing = sorted(role.value for role in expected_roles - set(self.roles))
            extra = sorted(str(role) for role in set(self.roles) - expected_roles)
            detail = ", ".join([*(f"missing {value}" for value in missing), *(f"extra {value}" for value in extra)])
            raise ValueError(f"all {len(expected_roles)} model roles are required{': ' + detail if detail else ''}")
        if self.active_profile_id not in self.profiles:
            raise ValueError("active_profile_id must reference a configured profile")
        for provider_id in self.providers:
            if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
                raise ValueError(f"invalid provider id: {provider_id!r}")
        if "oida_moss" not in self.providers or not self.providers["oida_moss"].enabled:
            raise ValueError("oida_moss is the always-on local perception provider")
        for profile_id, profile in self.profiles.items():
            if profile_id != profile.id:
                raise ValueError(f"profile key {profile_id!r} must match profile.id")
        for role, assignment in self.roles.items():
            if assignment.provider_id not in self.providers:
                raise ValueError(f"role {role.value!r} references unknown provider {assignment.provider_id!r}")
            from oida.reasoning.model_catalog import supports_role

            provider = self.providers[assignment.provider_id]
            selected_model = assignment.model_id or provider.default_model
            if not supports_role(
                assignment.provider_id,
                selected_model,
                role.value,
                provider_options=provider.options,
            ):
                hint = (
                    " Choose oida_moss or another explicitly audio-capable provider."
                    if role != ModelRole.CONVERSATION
                    else ""
                )
                raise ValueError(
                    f"model {selected_model or 'provider default'!r} on {assignment.provider_id!r} "
                    f"does not support role {role.value!r}.{hint}"
                )
            if role == ModelRole.TARGETED_RELISTEN and provider.locality != ProviderLocality.LOCAL:
                raise ValueError("targeted_relisten must use a local provider")
        fast_model = self.roles[ModelRole.FAST_PERCEPTION].model_id
        if fast_model == "thinking":
            raise ValueError("fast_perception cannot use the MOSS thinking alias")
        for role in (
            ModelRole.DEEP_PERCEPTION,
            ModelRole.MUSIC_ANALYSIS,
            ModelRole.TARGETED_RELISTEN,
        ):
            if self.roles[role].model_id == "instruct":
                raise ValueError(f"{role.value} cannot use the MOSS instruct alias")
        return self


class ProviderDescriptor(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    kind: ProviderKind
    locality: ProviderLocality
    enabled: bool = False
    available: bool = False
    authenticated: bool | None = None
    capabilities: list[str] = Field(default_factory=list)
    detail: str | None = None


class ModelDescriptor(StrictModel):
    id: str = Field(min_length=1, max_length=255)
    provider_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=255)
    capabilities: list[str] = Field(default_factory=list)
    locality: ProviderLocality = ProviderLocality.UNKNOWN
    context_window: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderRequest(StrictModel):
    provider_id: str = Field(min_length=1, max_length=120)
    model_id: str | None = Field(default=None, max_length=255)
    system_prompt: str = Field(min_length=1, max_length=32_000)
    user_prompt: str = Field(min_length=1, max_length=256_000)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=2048, ge=64, le=65_536)
    stream: bool = False
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=900.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ProviderResult(StrictModel):
    provider_id: str = Field(min_length=1, max_length=120)
    model_id: str | None = Field(default=None, max_length=255)
    status: ProviderStatus
    content: str | None = None
    parsed: dict[str, Any] | None = None
    usage: ProviderUsage | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    error: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


EvidenceKind = Literal[
    "event_anchor",
    "summary",
    "claim",
    "feature",
    "route",
    "uncertainty",
    "transcript",
    "memory",
    "relisten",
]


class EvidenceItem(StrictModel):
    ref: str = Field(min_length=1, max_length=255)
    kind: EvidenceKind
    value: Any
    event_id: str | None = None
    category: str | None = None
    confidence: str | None = None
    basis: str | None = None
    source: str | None = None
    time_range: dict[str, float] | None = None


class EvidencePermissions(StrictModel):
    transcript_included: bool = False
    memory_content_included: bool = False
    raw_audio_included: Literal[False] = False
    external_safe: bool = True


class EvidencePacket(StrictModel):
    contract: Literal[EVIDENCE_PACKET_CONTRACT] = EVIDENCE_PACKET_CONTRACT
    primary_event_id: str = Field(min_length=1, max_length=255)
    comparison_event_ids: list[str] = Field(default_factory=list, max_length=3)
    question: str = Field(min_length=1, max_length=16_000)
    items: list[EvidenceItem] = Field(default_factory=list, max_length=2048)
    permissions: EvidencePermissions = Field(default_factory=EvidencePermissions)
    covenant: dict[str, Any] | None = None
    untrusted_data: Literal[True] = True

    @model_validator(mode="after")
    def unique_refs(self) -> "EvidencePacket":
        refs = [item.ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("evidence refs must be unique")
        return self


class AnswerBlock(StrictModel):
    kind: Literal["answer", "fact", "interpretation", "dialogue"] = "answer"
    text: str = Field(min_length=1, max_length=12_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)


class ReasoningHypothesis(StrictModel):
    statement: str = Field(min_length=1, max_length=4000)
    confidence: Literal["high", "medium", "low", "undetermined"] = "undetermined"
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)


class RequestedAction(StrictModel):
    type: Literal["targeted_relisten"]
    question: str = Field(min_length=1, max_length=4000)
    time_range: dict[str, float] | None = None

    @field_validator("time_range")
    @classmethod
    def validate_time_range(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        if set(value) != {"start_s", "end_s"}:
            raise ValueError("time_range requires only start_s and end_s")
        start = value.get("start_s")
        end = value.get("end_s")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < 0
            or float(end) <= float(start)
        ):
            raise ValueError("time_range must be finite, non-negative, and end after start")
        return {"start_s": float(start), "end_s": float(end)}


class ReasoningResponse(StrictModel):
    contract: Literal[REASONING_RESPONSE_CONTRACT] = REASONING_RESPONSE_CONTRACT
    answer_blocks: list[AnswerBlock] = Field(min_length=1, max_length=64)
    hypotheses: list[ReasoningHypothesis] = Field(default_factory=list, max_length=32)
    uncertainties: list[Annotated[str, Field(min_length=1, max_length=4000)]] = Field(
        default_factory=list,
        max_length=64,
    )
    suggested_questions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list,
        max_length=12,
    )
    requested_action: RequestedAction | None = None

    @property
    def answer(self) -> str:
        return "\n\n".join(block.text for block in self.answer_blocks)


def reasoning_response_schema() -> dict[str, Any]:
    return ReasoningResponse.model_json_schema()


_SECRET_FIELD_PARTS = ("api_key", "apikey", "token", "secret", "password", "authorization", "credential_value")
_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _assert_no_secret_fields(value: Any, *, path: str = "options") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_FIELD_PARTS):
                raise ValueError(f"secret-bearing field {path}.{key} is not allowed in non-secret settings")
            _assert_no_secret_fields(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_secret_fields(item, path=f"{path}[{index}]")
