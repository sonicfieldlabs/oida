"""Versioned decision-routing contracts with compatible host projections.

Oída accepts its own namespace and the established ``telar/decision-routing``
namespace so existing run snapshots remain readable.
"""

from typing import Any, Literal
import math
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROUTING_SETTINGS_CONTRACT = "oida/routing/settings/v1"
ROUTING_DECISION_CONTRACT = "oida/routing-decision/v1"
# The host may also send its own projection of the same settings shape.
_HOST_SETTINGS_CONTRACT = "telar/decision-routing/settings/v1"

DECISION_ACTIONS = (
    "stop",
    "relisten",
    "analyze",
    "retrieve",
    "reason",
    "generate",
    "switch_source",
    "branch",
    "continue",
)
QUESTION_VERSION = "telar/questions/v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RoutingLimits(Strict):
    deadline_seconds: int = Field(default=120, ge=5, le=900)
    max_output_tokens: int = Field(default=1024, ge=64, le=8192)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_steps: int = Field(default=12, ge=1, le=64)
    max_relistens: int = Field(default=4, ge=0, le=16)
    max_generations: int = Field(default=3, ge=0, le=16)
    max_branches: int = Field(default=5, ge=0, le=16)


class RoutingSettings(Strict):
    """Settings for the bounded decision-routing stage.

    Defined in T3 with the consumer. ``enabled`` defaults to false: legacy
    disabled reasoning must never enable autonomous routing, and a stored
    setting is never flipped on by a reader.
    """

    contract: Literal[ROUTING_SETTINGS_CONTRACT, _HOST_SETTINGS_CONTRACT] = (
        ROUTING_SETTINGS_CONTRACT
    )
    enabled: bool = False
    revision: str | None = Field(default=None, max_length=120)
    provider_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"
    )
    model_id: str | None = Field(default=None, max_length=255)
    allowed_actions: list[str] = Field(default_factory=list, max_length=12)
    fallback: Literal["stop", "provider"] = "stop"
    fallback_provider_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"
    )
    fallback_model_id: str | None = Field(default=None, max_length=255)
    # Sending listening transcripts or memory metadata to an external decision
    # provider needs its own permission; audio permission alone is not it.
    allow_external_text: bool = False
    limits: RoutingLimits = Field(default_factory=RoutingLimits)

    @model_validator(mode="after")
    def coherent(self):
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("Duplicate allowed action")
        unknown = [a for a in self.allowed_actions if a not in DECISION_ACTIONS]
        if unknown:
            raise ValueError(f"Unknown allowed action: {unknown[0]}")
        if self.enabled and not self.provider_id:
            raise ValueError("An enabled decision router needs a provider")
        if self.fallback == "provider" and not self.fallback_provider_id:
            raise ValueError("Provider fallback requires fallback_provider_id")
        if self.fallback_provider_id and self.provider_id == self.fallback_provider_id:
            raise ValueError("Fallback must differ from the primary decision provider")
        if self.fallback_model_id and self.fallback != "provider":
            raise ValueError("A fallback model requires a fallback provider")
        return self


class DecisionCandidate(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    action: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=2000)
    arguments: dict[str, Any] = Field(default_factory=dict)


class DecisionContext(Strict):
    """The immutable, hashed state one decision is asked about."""

    contract: Literal["telar/decision-routing/context/v1"] = (
        "telar/decision-routing/context/v1"
    )
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    agent_id: str | None = Field(default=None, min_length=1, max_length=120)
    budget_scope_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,120}$")
    workspace_id: str | None = Field(default=None, min_length=1, max_length=120)
    owner_generation: str | None = Field(default=None, min_length=1, max_length=120)
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_segment: dict[str, float] | None = None
    rendition: str | None = Field(default=None, min_length=1, max_length=200)
    evidence_ref: str | None = Field(default=None, min_length=1, max_length=255)
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    candidates: list[DecisionCandidate] = Field(default_factory=list, max_length=24)
    last_actions: list[str] = Field(default_factory=list, max_length=32)
    remaining: dict[str, int] = Field(default_factory=dict)
    permitted_operations: list[str] = Field(default_factory=list, max_length=12)
    fresh_until: str | None = Field(default=None, min_length=1, max_length=40)
    reasoning_job_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def bounded_candidates(self):
        if self.source_segment is not None:
            if set(self.source_segment) != {"start_seconds", "seconds"} or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                for value in self.source_segment.values()
            ):
                raise ValueError("source_segment requires finite start_seconds and seconds")
            if self.source_segment["start_seconds"] < 0 or not 0 < self.source_segment["seconds"] <= 60:
                raise ValueError("source_segment is outside the listening bounds")
        ids = [candidate.id for candidate in self.candidates]
        if len(ids) != len(set(ids)) or "abstain" in ids:
            raise ValueError("Candidate IDs must be unique and not reserved")
        if any(value < 0 for value in self.remaining.values()):
            raise ValueError("Remaining budgets cannot be negative")
        return self


class SourceChoice(Strict):
    key: str = Field(min_length=1, max_length=255)
    title: str = Field(default="", max_length=2000)


class RoutingRequest(Strict):
    """One bounded decision request. Same offered-candidate shape as the
    legacy situated decide, plus the decision settings and an optional
    host-prepared context."""

    subject_generated: bool = False
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    chain_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    record_id: str = Field(pattern=r"^[A-Za-z0-9_:-]{1,100}$")
    settings: RoutingSettings | None = None
    context: DecisionContext | None = None
    comparison_ids: list[str] = Field(default_factory=list, max_length=8)
    choices: list[SourceChoice] = Field(default_factory=list, max_length=20)
    available_analysis: list[str] = Field(default_factory=list, max_length=8)
    retained_seconds: float | None = Field(default=None, gt=0, le=86400)
    allowed_actions: list[str] = Field(default_factory=lambda: ["stop"], max_length=8)

    @model_validator(mode="after")
    def bounded_references(self):
        if any(not re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", value) for value in self.comparison_ids):
            raise ValueError("Comparison IDs must name bounded Auditum records")
        return self


class DecisionQuestion(Strict):
    """One narrow judgment asked against one context (plan §7.2)."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    kind: Literal["choice", "score", "noul"]
    instructions: str = Field(min_length=1, max_length=4000)
    criteria: dict[str, str] = Field(default_factory=dict, max_length=25)


class DecisionProposal(Strict):
    """What a provider answered. Never authorizes execution by itself."""

    contract: Literal["telar/decision-routing/proposal/v1"] = (
        "telar/decision-routing/proposal/v1"
    )
    context_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: str = Field(min_length=1, max_length=40)
    candidate_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    question_version: str | None = Field(default=None, min_length=1, max_length=120)
    requested_model: str | None = Field(default=None, min_length=1, max_length=255)
    actual_model: str | None = Field(default=None, min_length=1, max_length=255)
    probabilities: dict[str, float] | None = None
    input_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    usage_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    abstained: bool = False
    error: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def coherent(self):
        if self.abstained and self.candidate_id is not None:
            raise ValueError("An abstention selects no candidate")
        if self.probabilities is not None:
            for key, value in self.probabilities.items():
                if not 0.0 <= float(value) <= 1.0:
                    raise ValueError(f"Probability {key!r} is outside [0, 1]")
        return self
