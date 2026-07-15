"""Capability and runtime manifest for Oída-supported reasoning/audio models.

The catalog is deliberately declarative.  A model appearing here means Oída
knows how it may be assigned and what transport/runtime it expects; it does not
mean weights are installed, an API is enabled, or a model was tested on this
machine.  Those states are exposed separately in descriptor metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from oida.reasoning.contracts import ModelDescriptor, ProviderLocality


FAST_PERCEPTION = "fast_perception"
DEEP_PERCEPTION = "deep_perception"
TRANSCRIPTION = "transcription"
MUSIC_ANALYSIS = "music_analysis"
CONVERSATION = "conversation"
TARGETED_RELISTEN = "targeted_relisten"

AUDIO_ROLES = frozenset(
    {
        FAST_PERCEPTION,
        DEEP_PERCEPTION,
        TRANSCRIPTION,
        MUSIC_ANALYSIS,
        TARGETED_RELISTEN,
    }
)


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider_id: str
    name: str
    roles: tuple[str, ...]
    locality: ProviderLocality
    runtime: str
    integration_status: str
    source_url: str
    license: str | None = None
    context_window: int | None = None
    weight_gb: float | None = None
    min_ram_gb: float | None = None
    recommended_ram_gb: float | None = None
    audio_transport: str | None = None
    platforms: tuple[str, ...] = ()
    notes: str | None = None
    dependencies: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    selectable: bool = True

    @property
    def capabilities(self) -> list[str]:
        values = [*self.roles]
        if any(role in AUDIO_ROLES for role in self.roles) or self.audio_transport:
            values.append("audio")
        if CONVERSATION in self.roles:
            values.extend(["text", "structured_output"])
        return list(dict.fromkeys(values))

    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            id=self.id,
            provider_id=self.provider_id,
            name=self.name,
            capabilities=self.capabilities,
            locality=self.locality,
            context_window=self.context_window,
            metadata={
                "catalog": True,
                "supported_roles": list(self.roles),
                "runtime": self.runtime,
                "integration_status": self.integration_status,
                "source_url": self.source_url,
                "license": self.license,
                "weight_gb": self.weight_gb,
                "min_ram_gb": self.min_ram_gb,
                "recommended_ram_gb": self.recommended_ram_gb,
                "audio_transport": self.audio_transport,
                "platforms": list(self.platforms),
                "notes": self.notes,
                "dependencies": list(self.dependencies),
                "selectable": self.selectable,
                # Catalog entries describe support, not local installation.
                "installed": None,
                "tested_on_this_machine": False,
            },
        )


def _moss_audio(
    model_id: str,
    name: str,
    *,
    roles: tuple[str, ...],
    status: str = "supported_uninstalled",
    notes: str | None = None,
) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        provider_id="oida_moss",
        name=name,
        roles=roles,
        locality=ProviderLocality.LOCAL,
        runtime="embedded_mps_or_sglang",
        integration_status=status,
        source_url=f"https://huggingface.co/{model_id}",
        license="Apache-2.0",
        weight_gb=18.1 if "8B" in model_id else 9.5,
        min_ram_gb=24 if "8B" in model_id else 16,
        recommended_ram_gb=48 if "8B" in model_id else 24,
        audio_transport="embedded_moss",
        platforms=("macos-mps", "cuda-sglang", "cpu"),
        notes=notes,
        aliases=(model_id.rsplit("/", 1)[-1],),
    )


MODEL_SPECS: tuple[ModelSpec, ...] = (
    # The two stable aliases preserve existing installations and resolve to
    # OIDA_MOSS_*_MODEL.  Their exact footprint depends on that configured path.
    ModelSpec(
        id="instruct",
        provider_id="oida_moss",
        name="Configured MOSS-Audio Instruct",
        roles=(FAST_PERCEPTION, TRANSCRIPTION),
        locality=ProviderLocality.LOCAL,
        runtime="embedded_mps_or_sglang",
        integration_status="configured_alias",
        source_url="https://github.com/OpenMOSS/MOSS-Audio",
        license="Apache-2.0",
        min_ram_gb=16,
        recommended_ram_gb=24,
        audio_transport="embedded_moss",
        platforms=("macos-mps", "cuda-sglang", "cpu"),
        notes="Resolves to Oída's configured instruct checkpoint; no download occurs unless Hub access is explicitly enabled.",
    ),
    ModelSpec(
        id="thinking",
        provider_id="oida_moss",
        name="Configured MOSS-Audio Thinking",
        roles=(DEEP_PERCEPTION, MUSIC_ANALYSIS, TARGETED_RELISTEN),
        locality=ProviderLocality.LOCAL,
        runtime="embedded_mps_or_sglang",
        integration_status="configured_alias",
        source_url="https://github.com/OpenMOSS/MOSS-Audio",
        license="Apache-2.0",
        min_ram_gb=16,
        recommended_ram_gb=24,
        audio_transport="embedded_moss",
        platforms=("macos-mps", "cuda-sglang", "cpu"),
        notes="Resolves to Oída's configured thinking checkpoint; hidden reasoning traces are never exposed as conversation evidence.",
    ),
    _moss_audio(
        "OpenMOSS-Team/MOSS-Audio-4B-Instruct",
        "MOSS-Audio 4B Instruct",
        roles=(FAST_PERCEPTION, TRANSCRIPTION),
    ),
    _moss_audio(
        "OpenMOSS-Team/MOSS-Audio-4B-Thinking",
        "MOSS-Audio 4B Thinking",
        roles=(DEEP_PERCEPTION, MUSIC_ANALYSIS, TARGETED_RELISTEN),
    ),
    _moss_audio(
        "OpenMOSS-Team/MOSS-Audio-8B-Instruct",
        "MOSS-Audio 8B Instruct",
        roles=(FAST_PERCEPTION, TRANSCRIPTION),
        notes="General audio instruction model. The 8B checkpoint is RAM-heavy on unified-memory Macs.",
    ),
    _moss_audio(
        "OpenMOSS-Team/MOSS-Audio-8B-Thinking",
        "MOSS-Audio 8B Thinking",
        roles=(DEEP_PERCEPTION, MUSIC_ANALYSIS, TARGETED_RELISTEN),
        notes="General audio reasoning model; Oída consumes only its final answer, not private chain-of-thought.",
    ),
    _moss_audio(
        "OpenMOSS-Team/MOSS-Music-8B-Instruct",
        "MOSS-Music 8B Instruct",
        roles=(MUSIC_ANALYSIS, FAST_PERCEPTION),
        status="experimental_embedded_or_sglang",
        notes="Music-specialized checkpoint. The project recommends SGLang for best quality; embedded MPS support remains experimental.",
    ),
    _moss_audio(
        "OpenMOSS-Team/MOSS-Music-8B-Thinking",
        "MOSS-Music 8B Thinking",
        roles=(MUSIC_ANALYSIS, DEEP_PERCEPTION, TARGETED_RELISTEN),
        status="experimental_embedded_or_sglang",
        notes="Music-specialized reasoning checkpoint. Prefer a CUDA/SGLang host when available.",
    ),
    ModelSpec(
        id="OpenMOSS-Team/MOSS-Transcribe-Diarize",
        provider_id="local_audio",
        name="MOSS Transcribe + Diarize 0.9B",
        roles=(TRANSCRIPTION,),
        locality=ProviderLocality.LOCAL,
        runtime="local_transcription_endpoint",
        integration_status="supported_local_host",
        source_url="https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize",
        license="Apache-2.0",
        weight_gb=2.0,
        min_ram_gb=6,
        recommended_ram_gb=8,
        audio_transport="openai_transcription",
        platforms=("cuda-vllm", "cuda-sglang", "transformers"),
        notes="Dedicated multilingual ASR/diarization model. Oída expects an OpenAI-compatible /audio/transcriptions bridge for this preset.",
        aliases=("MOSS-Transcribe-Diarize",),
    ),
    ModelSpec(
        id="mispeech/midashenglm-7b-0804-fp32",
        provider_id="local_audio",
        name="MiDashengLM 7B 0804 FP32",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, MUSIC_ANALYSIS, TARGETED_RELISTEN),
        locality=ProviderLocality.LOCAL,
        runtime="local_openai_audio_endpoint",
        integration_status="supported_local_host_unverified",
        source_url="https://huggingface.co/mispeech/midashenglm-7b-0804-fp32",
        license="Apache-2.0",
        weight_gb=33.2,
        min_ram_gb=40,
        recommended_ram_gb=48,
        audio_transport="openai_audio_url",
        platforms=("cuda-vllm", "transformers"),
        notes="The requested FP32 checkpoint is very large. The upstream project recommends newer BF16 checkpoints for practical use.",
        aliases=("midashenglm-7b-0804-fp32",),
    ),
    ModelSpec(
        id="mispeech/midashenglm-0.6b-fp32",
        provider_id="local_audio",
        name="MiDashengLM 0.6B FP32",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TARGETED_RELISTEN),
        locality=ProviderLocality.LOCAL,
        runtime="local_openai_audio_endpoint",
        integration_status="supported_local_host_unverified",
        source_url="https://huggingface.co/mispeech/midashenglm-0.6b-fp32",
        license="Apache-2.0",
        weight_gb=2.5,
        min_ram_gb=6,
        recommended_ram_gb=8,
        audio_transport="openai_audio_url",
        platforms=("cuda-vllm", "transformers"),
        notes="Compact general audio-language model; suitable for experimentation and constrained local hosts.",
        aliases=("midashenglm-0.6b-fp32",),
    ),
    ModelSpec(
        id="XiaomiMiMo/MiMo-Audio-7B-Instruct",
        provider_id="local_audio",
        name="MiMo-Audio 7B Instruct",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, TARGETED_RELISTEN, CONVERSATION),
        locality=ProviderLocality.LOCAL,
        runtime="local_cuda_audio_endpoint",
        integration_status="supported_local_host_unverified",
        source_url="https://github.com/XiaomiMiMo/MiMo-Audio",
        license="Apache-2.0",
        weight_gb=17.0,
        min_ram_gb=32,
        recommended_ram_gb=48,
        audio_transport="openai_audio_url",
        platforms=("linux-cuda12",),
        dependencies=("XiaomiMiMo/MiMo-Audio-Tokenizer",),
        notes="The official runtime requires Linux, CUDA 12+, Python 3.12, FlashAttention, and the separate audio tokenizer.",
        aliases=("MiMo-Audio-7B-Instruct",),
    ),
    ModelSpec(
        id="XiaomiMiMo/MiMo-Audio-7B-Base",
        provider_id="local_audio",
        name="MiMo-Audio 7B Base",
        roles=(),
        locality=ProviderLocality.LOCAL,
        runtime="local_cuda_foundation_model",
        integration_status="catalogued_not_role_assignable",
        source_url="https://huggingface.co/XiaomiMiMo/MiMo-Audio-7B-Base",
        license="Apache-2.0",
        weight_gb=17.0,
        min_ram_gb=32,
        recommended_ram_gb=48,
        audio_transport="openai_audio_url",
        platforms=("linux-cuda12",),
        dependencies=("XiaomiMiMo/MiMo-Audio-Tokenizer",),
        notes="Foundation/few-shot checkpoint, exposed for setup visibility but not assignable to an Oída production role.",
        aliases=("MiMo-Audio-7B-Base",),
        selectable=False,
    ),
    ModelSpec(
        id="XiaomiMiMo/MiMo-Audio-Tokenizer",
        provider_id="local_audio",
        name="MiMo Audio Tokenizer",
        roles=(),
        locality=ProviderLocality.LOCAL,
        runtime="local_dependency",
        integration_status="dependency_only",
        source_url="https://huggingface.co/XiaomiMiMo/MiMo-Audio-Tokenizer",
        license="Apache-2.0",
        weight_gb=2.5,
        min_ram_gb=4,
        recommended_ram_gb=8,
        platforms=("linux-cuda12",),
        notes="Required tokenizer dependency for MiMo-Audio, not a standalone Oída role model.",
        aliases=("MiMo-Audio-Tokenizer",),
        selectable=False,
    ),
    ModelSpec(
        id="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        provider_id="local_audio",
        name="Qwen3-Omni 30B-A3B Instruct (local host)",
        roles=(FAST_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION),
        locality=ProviderLocality.LOCAL,
        runtime="local_cuda_openai_audio_endpoint",
        integration_status="supported_untested_large",
        source_url="https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct",
        license="Apache-2.0",
        weight_gb=62,
        min_ram_gb=64,
        recommended_ram_gb=96,
        audio_transport="openai_audio_url",
        platforms=("linux-cuda-vllm", "transformers"),
        notes="Configuration-only support. This checkpoint is intentionally not loaded or tested on the current machine.",
        aliases=("Qwen3-Omni-30B-A3B-Instruct",),
    ),
    ModelSpec(
        id="Qwen/Qwen3-Omni-30B-A3B-Thinking",
        provider_id="local_audio",
        name="Qwen3-Omni 30B-A3B Thinking (local host)",
        roles=(DEEP_PERCEPTION, MUSIC_ANALYSIS, TARGETED_RELISTEN, CONVERSATION),
        locality=ProviderLocality.LOCAL,
        runtime="local_cuda_openai_audio_endpoint",
        integration_status="supported_untested_large",
        source_url="https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Thinking",
        license="Apache-2.0",
        weight_gb=62,
        min_ram_gb=64,
        recommended_ram_gb=96,
        audio_transport="openai_audio_url",
        platforms=("linux-cuda-vllm", "transformers"),
        notes="Configuration-only support. Oída consumes final responses and does not expose hidden reasoning traces.",
        aliases=("Qwen3-Omni-30B-A3B-Thinking",),
    ),
    ModelSpec(
        id="google/gemma-3n-E2B-it",
        provider_id="local_audio",
        name="Gemma 3n E2B IT",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TRANSCRIPTION, CONVERSATION, TARGETED_RELISTEN),
        locality=ProviderLocality.LOCAL,
        runtime="local_openai_audio_endpoint",
        integration_status="supported_local_host_gated",
        source_url="https://huggingface.co/google/gemma-3n-E2B-it",
        license="Gemma terms",
        weight_gb=6.0,
        min_ram_gb=8,
        recommended_ram_gb=12,
        context_window=32_000,
        audio_transport="openai_audio_url",
        platforms=("transformers", "cuda", "mps-runtime-dependent"),
        notes="Open weights require acceptance of Google's Gemma terms. Runtime memory depends strongly on precision and offloading.",
        aliases=("gemma-3n-E2B-it",),
    ),
    ModelSpec(
        id="google/gemma-3n-E4B-it",
        provider_id="local_audio",
        name="Gemma 3n E4B IT",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION, TARGETED_RELISTEN),
        locality=ProviderLocality.LOCAL,
        runtime="local_openai_audio_endpoint",
        integration_status="supported_local_host_gated",
        source_url="https://huggingface.co/google/gemma-3n-E4B-it",
        license="Gemma terms",
        weight_gb=8.0,
        min_ram_gb=12,
        recommended_ram_gb=16,
        context_window=32_000,
        audio_transport="openai_audio_url",
        platforms=("transformers", "cuda", "mps-runtime-dependent"),
        notes="Open weights require acceptance of Google's Gemma terms. Runtime memory depends strongly on precision and offloading.",
        aliases=("gemma-3n-E4B-it",),
    ),
    ModelSpec(
        id="soham97/mellow",
        provider_id="local_audio",
        name="Mellow 167M (experimental)",
        roles=(DEEP_PERCEPTION, TARGETED_RELISTEN),
        locality=ProviderLocality.LOCAL,
        runtime="custom_local_audio_bridge",
        integration_status="experimental_bridge_required",
        source_url="https://github.com/soham97/mellow",
        license="MIT",
        weight_gb=0.67,
        min_ram_gb=4,
        recommended_ram_gb=8,
        audio_transport="openai_audio_url",
        platforms=("custom-python-runtime",),
        notes="Small experimental audio-language model with limited training concepts. It is not a general text reasoner and needs an Oída-compatible local bridge.",
        aliases=("mellow",),
    ),
    ModelSpec(
        id="gemini-3.5-flash",
        provider_id="google",
        name="Gemini 3.5 Flash",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION),
        locality=ProviderLocality.EXTERNAL,
        runtime="google_generative_language_api",
        integration_status="supported_api",
        source_url="https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash",
        context_window=1_048_576,
        audio_transport="gemini_inline_data",
        notes="Cloud audio is sent only after the separate external-audio opt-in and is always blocked in incognito mode.",
    ),
    ModelSpec(
        id="qwen3.5-omni-plus",
        provider_id="alibaba",
        name="Qwen3.5-Omni Plus",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION),
        locality=ProviderLocality.EXTERNAL,
        runtime="alibaba_openai_compatible_api",
        integration_status="supported_api_streaming",
        source_url="https://www.alibabacloud.com/help/en/model-studio/qwen-omni",
        audio_transport="openai_input_audio",
        notes="Alibaba Model Studio API preset. Current Omni output is streamed and Oída aggregates only final response text.",
    ),
    ModelSpec(
        id="qwen3.5-omni-flash",
        provider_id="alibaba",
        name="Qwen3.5-Omni Flash",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION),
        locality=ProviderLocality.EXTERNAL,
        runtime="alibaba_openai_compatible_api",
        integration_status="supported_api_streaming",
        source_url="https://www.alibabacloud.com/help/en/model-studio/qwen-omni",
        audio_transport="openai_input_audio",
        notes="Alibaba Model Studio API preset. This is a hosted Qwen3.5 model, not an alias for the open Qwen3-Omni 30B checkpoint.",
    ),
    ModelSpec(
        id="qwen3-omni-flash",
        provider_id="alibaba",
        name="Qwen3-Omni Flash",
        roles=(FAST_PERCEPTION, DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION),
        locality=ProviderLocality.EXTERNAL,
        runtime="alibaba_openai_compatible_api",
        integration_status="supported_api_streaming",
        source_url="https://www.alibabacloud.com/help/en/model-studio/qwen-omni",
        audio_transport="openai_input_audio",
        notes="Hosted API model with optional thinking support. It is distinct from a self-hosted open-weight checkpoint.",
    ),
    ModelSpec(
        id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        provider_id="nvidia",
        name="Nemotron 3 Nano Omni 30B-A3B Reasoning",
        roles=(DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION),
        locality=ProviderLocality.EXTERNAL,
        runtime="nvidia_nim_openai_api",
        integration_status="supported_api_unverified",
        source_url="https://build.nvidia.com/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        context_window=256_000,
        audio_transport="openai_audio_url",
        notes="Hosted NVIDIA prototype. Audio prompts use final-answer mode; larger inline payloads use a temporary NVCF asset that Oída deletes after the request.",
    ),
    ModelSpec(
        id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        provider_id="openrouter",
        name="Nemotron 3 Nano Omni 30B-A3B Reasoning · free",
        roles=(DEEP_PERCEPTION, TRANSCRIPTION, MUSIC_ANALYSIS, CONVERSATION),
        locality=ProviderLocality.EXTERNAL,
        runtime="openrouter_api",
        integration_status="supported_api_sensitive_data_warning",
        source_url="https://openrouter.ai/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        audio_transport="openai_input_audio",
        notes="Free prototype route. Do not send confidential recordings or personal voices; provider trial logging/usage terms apply.",
    ),
)


def specs_for_provider(provider_id: str, *, selectable_only: bool = False) -> list[ModelSpec]:
    return [
        spec
        for spec in MODEL_SPECS
        if spec.provider_id == provider_id and (spec.selectable or not selectable_only)
    ]


def catalog_descriptors(provider_id: str) -> list[ModelDescriptor]:
    return [spec.descriptor() for spec in specs_for_provider(provider_id)]


def find_model_spec(provider_id: str, model_id: str | None) -> ModelSpec | None:
    normalized = str(model_id or "").strip().lower()
    if not normalized:
        return None
    basename = normalized.rsplit("/", 1)[-1]
    for spec in MODEL_SPECS:
        if spec.provider_id != provider_id:
            continue
        candidates = {spec.id.lower(), spec.id.rsplit("/", 1)[-1].lower()}
        candidates.update(alias.lower() for alias in spec.aliases)
        if normalized in candidates or basename in candidates:
            return spec
    return None


def supports_role(
    provider_id: str,
    model_id: str | None,
    role: str,
    *,
    provider_options: dict[str, Any] | None = None,
) -> bool:
    """Return whether an assignment has a known compatible execution path.

    Unknown exact IDs remain possible for the two explicit generic endpoint
    providers when their operator declares audio capability.  Catalogued model
    IDs are always checked strictly so the UI cannot assign a tokenizer or base
    checkpoint to a task it cannot perform.
    """

    if role == CONVERSATION:
        if provider_id == "oida_moss":
            return False
        if provider_id == "local_structured":
            return model_id in {None, "", "oida-deterministic-v1"}
        spec = find_model_spec(provider_id, model_id)
        return CONVERSATION in spec.roles if spec else True

    if role not in AUDIO_ROLES or provider_id == "local_structured":
        return False
    spec = find_model_spec(provider_id, model_id)
    if spec is not None:
        return spec.selectable and role in spec.roles
    if provider_id == "oida_moss":
        # Locally scanned MOSS checkpoints may not be in the built-in catalog.
        return True
    options = provider_options or {}
    return provider_id == "local_audio" or bool(options.get("audio_capable", False))


def all_provider_ids() -> set[str]:
    return {spec.provider_id for spec in MODEL_SPECS}


def model_sources(specs: Iterable[ModelSpec] = MODEL_SPECS) -> list[str]:
    return list(dict.fromkeys(spec.source_url for spec in specs))
