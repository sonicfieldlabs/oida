"""Provider-neutral, evidence-grounded reasoning primitives for Oída."""

from oida.reasoning.contracts import (
    AnswerBlock,
    Depth,
    EvidenceItem,
    EvidencePacket,
    EvidencePermissions,
    Focus,
    Initiative,
    ModelDescriptor,
    ModelRole,
    ProviderDescriptor,
    ProviderKind,
    ProviderLocality,
    ProviderRequest,
    ProviderResult,
    ProviderSettings,
    ProviderStatus,
    ReasoningHypothesis,
    ReasoningProfile,
    ReasoningResponse,
    ReasoningSettings,
    RequestedAction,
    RoleAssignment,
    Tone,
    reasoning_response_schema,
)
from oida.reasoning.deterministic import DeterministicLocalProvider, LocalStructuredProvider
from oida.reasoning.evidence import EvidencePacketBuilder
from oida.reasoning.prompts import CompiledPrompt, PromptCompiler
from oida.reasoning.registry import ProviderRegistry, build_provider_registry
from oida.reasoning.secrets import SecretStore, default_secret_store
from oida.reasoning.settings import ReasoningSettingsStore
from oida.reasoning.validation import ResponseValidationError, ResponseValidator

__all__ = [
    "AnswerBlock",
    "CompiledPrompt",
    "Depth",
    "DeterministicLocalProvider",
    "EvidenceItem",
    "EvidencePacket",
    "EvidencePacketBuilder",
    "EvidencePermissions",
    "Focus",
    "Initiative",
    "LocalStructuredProvider",
    "ModelDescriptor",
    "ModelRole",
    "PromptCompiler",
    "ProviderDescriptor",
    "ProviderKind",
    "ProviderLocality",
    "ProviderRequest",
    "ProviderRegistry",
    "ProviderResult",
    "ProviderSettings",
    "ProviderStatus",
    "ReasoningHypothesis",
    "ReasoningProfile",
    "ReasoningResponse",
    "ReasoningSettings",
    "ReasoningSettingsStore",
    "RequestedAction",
    "ResponseValidationError",
    "ResponseValidator",
    "RoleAssignment",
    "SecretStore",
    "Tone",
    "build_provider_registry",
    "default_secret_store",
    "reasoning_response_schema",
]
