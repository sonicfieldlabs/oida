"""Reasoning provider registry and settings-backed adapter factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderKind,
    ProviderLocality,
    ProviderRequest,
    ProviderResult,
    ProviderSettings,
    ReasoningSettings,
)
from oida.reasoning.deterministic import DeterministicLocalProvider
from oida.reasoning.model_catalog import catalog_descriptors
from oida.reasoning.providers.base import ProviderAdapter, endpoint_locality, error_result
from oida.reasoning.providers.claude import ClaudeProvider
from oida.reasoning.providers.codex import CodexProvider
from oida.reasoning.providers.hermes import HermesProvider
from oida.reasoning.providers.gemini import GeminiProvider
from oida.reasoning.providers.moss_catalog import MossCatalogProvider
from oida.reasoning.providers.openai_compatible import (
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
)
from oida.reasoning.providers.openclaw import OpenClawProvider
from oida.reasoning.providers.opencode import OpenCodeProvider
from oida.reasoning.secrets import SecretStore, default_secret_store


@dataclass(frozen=True)
class _RegisteredProvider:
    adapter: ProviderAdapter
    enabled: bool
    configured: ProviderSettings | None = None


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, _RegisteredProvider] = {}

    def register(
        self,
        adapter: ProviderAdapter,
        *,
        enabled: bool | None = None,
        configured: ProviderSettings | None = None,
    ) -> None:
        provider_id = adapter.provider_id
        if provider_id in self._providers:
            raise ValueError(f"Reasoning provider {provider_id!r} is already registered")
        self._providers[provider_id] = _RegisteredProvider(
            adapter=adapter,
            enabled=bool(enabled if enabled is not None else True),
            configured=configured,
        )

    def ids(self) -> list[str]:
        return sorted(self._providers)

    def get(self, provider_id: str) -> ProviderAdapter | None:
        entry = self._providers.get(provider_id)
        return entry.adapter if entry else None

    def probe(self, provider_id: str) -> ProviderDescriptor:
        entry = self._providers.get(provider_id)
        if entry is None:
            raise KeyError(provider_id)
        try:
            descriptor = entry.adapter.probe()
        except Exception as exc:  # a broken optional host must not break settings
            descriptor = ProviderDescriptor(
                id=provider_id,
                name=provider_id,
                kind=entry.configured.kind if entry.configured else ProviderKind.HOST_CLI,
                locality=entry.configured.locality if entry.configured else ProviderLocality.UNKNOWN,
                enabled=entry.enabled,
                available=False,
                authenticated=None,
                capabilities=[],
                detail=f"Provider probe failed: {type(exc).__name__}",
            )
        locality = descriptor.locality
        if entry.configured:
            # Never let a stored "local" label downgrade a network endpoint
            # that the adapter can prove is external.
            if entry.configured.locality == ProviderLocality.EXTERNAL:
                locality = ProviderLocality.EXTERNAL
        return descriptor.model_copy(update={"enabled": entry.enabled, "locality": locality})

    def descriptors(self) -> list[ProviderDescriptor]:
        return [self.probe(provider_id) for provider_id in self.ids()]

    def list_models(self, provider_id: str) -> list[ModelDescriptor]:
        entry = self._providers.get(provider_id)
        if entry is None:
            raise KeyError(provider_id)
        if not entry.enabled:
            models = self._configured_model(entry)
        else:
            try:
                models = entry.adapter.list_models()
            except Exception:
                models = []
        configured = self._configured_model(entry)
        known = {model.id for model in models}
        models.extend(model for model in configured if model.id not in known)
        by_id = {model.id: index for index, model in enumerate(models)}
        for catalog in catalog_descriptors(provider_id):
            index = by_id.get(catalog.id)
            if index is None:
                by_id[catalog.id] = len(models)
                models.append(catalog)
                continue
            existing = models[index]
            models[index] = catalog.model_copy(
                update={
                    "capabilities": list(
                        dict.fromkeys([*catalog.capabilities, *existing.capabilities])
                    ),
                    "metadata": {**catalog.metadata, **existing.metadata},
                    "locality": existing.locality,
                }
            )
        return models

    @staticmethod
    def _configured_model(entry: _RegisteredProvider) -> list[ModelDescriptor]:
        config = entry.configured
        if config is None or not config.default_model:
            return []
        locality = ProviderLocality.UNKNOWN
        base_url = getattr(entry.adapter, "base_url", None)
        if config.kind in {
            ProviderKind.OLLAMA,
            ProviderKind.OPENAI_COMPATIBLE,
            ProviderKind.OPENROUTER,
            ProviderKind.GOOGLE,
        } and isinstance(base_url, str):
            locality = ProviderLocality(endpoint_locality(base_url))
        model_lower = config.default_model.lower()
        if config.kind == ProviderKind.OLLAMA and ":" in model_lower and model_lower.endswith("cloud"):
            locality = ProviderLocality.EXTERNAL
        return [
            ModelDescriptor(
                id=config.default_model,
                provider_id=entry.adapter.provider_id,
                name=config.default_model,
                capabilities=["text", "configured"],
                locality=locality,
                metadata={"configured_default": True},
            )
        ]

    def models(self) -> dict[str, list[ModelDescriptor]]:
        return {provider_id: self.list_models(provider_id) for provider_id in self.ids()}

    def locality(
        self, provider_id: str, model_id: str | None = None
    ) -> ProviderLocality:
        if model_id:
            model = next(
                (item for item in self.list_models(provider_id) if item.id == model_id),
                None,
            )
            if model is not None:
                return model.locality
        return self.probe(provider_id).locality

    def complete(self, request: ProviderRequest) -> ProviderResult:
        entry = self._providers.get(request.provider_id)
        if entry is None:
            return error_result(request, f"Unknown reasoning provider: {request.provider_id}")
        if not entry.enabled:
            return error_result(request, f"Reasoning provider {request.provider_id!r} is disabled")
        if request.model_id is None and entry.configured and entry.configured.default_model:
            request = request.model_copy(update={"model_id": entry.configured.default_model})
        try:
            return entry.adapter.complete(request)
        except Exception as exc:
            return error_result(request, f"Provider adapter failed: {type(exc).__name__}")


def build_provider_registry(
    settings: ReasoningSettings | None = None,
    *,
    secret_store: SecretStore | None = None,
    moss_models: list[ModelDescriptor] | None = None,
    moss_available: bool = False,
    local_provider: ProviderAdapter | None = None,
) -> ProviderRegistry:
    """Create all built-ins, then overlay persisted non-secret settings.

    Host adapters remain visible for discovery when not configured, but the
    registry refuses to invoke them until their provider setting is enabled.
    """

    settings = settings or ReasoningSettings()
    secrets = secret_store or default_secret_store()
    registry = ProviderRegistry()

    local_provider = local_provider or DeterministicLocalProvider()
    local_cfg = settings.providers.get("local_structured")
    registry.register(
        local_provider,
        enabled=bool(local_cfg.enabled) if local_cfg else True,
        configured=local_cfg,
    )

    moss_cfg = settings.providers.get("oida_moss")
    registry.register(
        MossCatalogProvider(
            enabled=bool(moss_cfg.enabled) if moss_cfg else True,
            available=moss_available,
            models=moss_models,
        ),
        enabled=bool(moss_cfg.enabled) if moss_cfg else True,
        configured=moss_cfg,
    )

    def configured(provider_id: str) -> ProviderSettings | None:
        return settings.providers.get(provider_id)

    def enabled(provider_id: str) -> bool:
        item = configured(provider_id)
        return bool(item.enabled) if item else False

    def option(provider_id: str, name: str, default: Any = None) -> Any:
        item = configured(provider_id)
        return item.options.get(name, default) if item else default

    def credential(provider_id: str, fallback_name: str = "api_key"):
        item = configured(provider_id)
        name = item.credential_ref if item and item.credential_ref else fallback_name

        def get() -> str | None:
            try:
                return secrets.get(provider_id, name)
            except (ValueError, RuntimeError):
                return None

        return get

    ollama_cfg = configured("ollama")
    registry.register(
        OllamaProvider(
            base_url=(ollama_cfg.base_url if ollama_cfg and ollama_cfg.base_url else "http://127.0.0.1:11434"),
            enabled=enabled("ollama"),
        ),
        enabled=enabled("ollama"),
        configured=ollama_cfg,
    )

    compatible_cfg = configured("openai_compatible")
    registry.register(
        OpenAICompatibleProvider(
            base_url=(
                compatible_cfg.base_url
                if compatible_cfg and compatible_cfg.base_url
                else "http://127.0.0.1:8000/v1"
            ),
            api_key_getter=credential("openai_compatible"),
            enabled=enabled("openai_compatible"),
            supports_json_schema=bool(option("openai_compatible", "supports_json_schema", True)),
        ),
        enabled=enabled("openai_compatible"),
        configured=compatible_cfg,
    )

    openrouter_cfg = configured("openrouter")
    registry.register(
        OpenRouterProvider(
            api_key_getter=credential("openrouter"),
            enabled=enabled("openrouter"),
            app_url=option("openrouter", "app_url"),
            app_title=str(option("openrouter", "app_title", "Oída")),
        ),
        enabled=enabled("openrouter"),
        configured=openrouter_cfg,
    )

    local_audio_cfg = configured("local_audio")
    registry.register(
        OpenAICompatibleProvider(
            provider_id="local_audio",
            base_url=(
                local_audio_cfg.base_url
                if local_audio_cfg and local_audio_cfg.base_url
                else "http://127.0.0.1:8001/v1"
            ),
            api_key_getter=credential("local_audio"),
            enabled=enabled("local_audio"),
            supports_json_schema=bool(option("local_audio", "supports_json_schema", True)),
            display_name="Local audio model host",
            capabilities=["models", "structured_output", "text", "audio", "transcription"],
        ),
        enabled=enabled("local_audio"),
        configured=local_audio_cfg,
    )

    google_cfg = configured("google")
    registry.register(
        GeminiProvider(
            base_url=(
                google_cfg.base_url
                if google_cfg and google_cfg.base_url
                else "https://generativelanguage.googleapis.com/v1beta"
            ),
            api_key_getter=credential("google"),
            enabled=enabled("google"),
        ),
        enabled=enabled("google"),
        configured=google_cfg,
    )

    alibaba_cfg = configured("alibaba")
    registry.register(
        OpenAICompatibleProvider(
            provider_id="alibaba",
            base_url=(
                alibaba_cfg.base_url
                if alibaba_cfg and alibaba_cfg.base_url
                else "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
            ),
            api_key_getter=credential("alibaba"),
            enabled=enabled("alibaba"),
            supports_json_schema=bool(option("alibaba", "supports_json_schema", False)),
            display_name="Alibaba Model Studio",
            capabilities=["models", "structured_output", "text", "audio", "streaming"],
            force_stream=bool(option("alibaba", "stream", True)),
        ),
        enabled=enabled("alibaba"),
        configured=alibaba_cfg,
    )

    nvidia_cfg = configured("nvidia")
    registry.register(
        OpenAICompatibleProvider(
            provider_id="nvidia",
            base_url=(
                nvidia_cfg.base_url
                if nvidia_cfg and nvidia_cfg.base_url
                else "https://integrate.api.nvidia.com/v1"
            ),
            api_key_getter=credential("nvidia"),
            enabled=enabled("nvidia"),
            supports_json_schema=bool(option("nvidia", "supports_json_schema", True)),
            display_name="NVIDIA NIM API",
            capabilities=["models", "structured_output", "text", "audio"],
        ),
        enabled=enabled("nvidia"),
        configured=nvidia_cfg,
    )

    host_types: dict[str, type[CodexProvider | ClaudeProvider | HermesProvider | OpenClawProvider]] = {
        "codex": CodexProvider,
        "claude": ClaudeProvider,
        "hermes": HermesProvider,
        "openclaw": OpenClawProvider,
    }
    for provider_id, provider_type in host_types.items():
        cfg = configured(provider_id)
        adapter = provider_type(executable=str(option(provider_id, "executable", provider_id)))
        registry.register(adapter, enabled=enabled(provider_id), configured=cfg)

    opencode_cfg = configured("opencode")
    has_opencode_url = bool(opencode_cfg and opencode_cfg.base_url)
    opencode_managed = bool(option("opencode", "managed", not has_opencode_url))
    registry.register(
        OpenCodeProvider(
            base_url=(opencode_cfg.base_url if opencode_cfg and opencode_cfg.base_url else None),
            managed=opencode_managed,
            executable=str(option("opencode", "executable", "opencode")),
            password_getter=credential("opencode", "server_password"),
            username=str(option("opencode", "username", "opencode")),
            enabled=enabled("opencode"),
        ),
        enabled=enabled("opencode"),
        configured=opencode_cfg,
    )

    # Any additional configured Ollama/OpenAI-compatible endpoint becomes a
    # first-class provider without requiring new adapter code.
    reserved = set(registry.ids()) | {"local_structured", "oida_moss"}
    for provider_id, cfg in settings.providers.items():
        if provider_id in reserved:
            continue
        if cfg.kind == ProviderKind.OLLAMA:
            registry.register(
                OllamaProvider(
                    provider_id=provider_id,
                    base_url=cfg.base_url or "http://127.0.0.1:11434",
                    enabled=cfg.enabled,
                ),
                enabled=cfg.enabled,
                configured=cfg,
            )
        elif cfg.kind == ProviderKind.OPENAI_COMPATIBLE:
            registry.register(
                OpenAICompatibleProvider(
                    provider_id=provider_id,
                    base_url=cfg.base_url or "http://127.0.0.1:8000/v1",
                    api_key_getter=credential(provider_id),
                    enabled=cfg.enabled,
                    supports_json_schema=bool(cfg.options.get("supports_json_schema", True)),
                ),
                enabled=cfg.enabled,
                configured=cfg,
            )
    return registry
