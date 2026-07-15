"""Descriptor-only bridge for Oída's local MOSS-Audio perception models."""

from __future__ import annotations

from collections.abc import Iterable

from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRequest,
    ProviderResult,
)

from .base import error_result, require_matching_provider


class MossCatalogProvider:
    """Expose perception roles without pretending MOSS is a text reasoner."""

    provider_id = "oida_moss"

    def __init__(
        self,
        *,
        enabled: bool = True,
        available: bool = False,
        models: Iterable[ModelDescriptor] | None = None,
        detail: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.available = available
        self._models = list(models) if models is not None else [
            ModelDescriptor(
                id="instruct",
                provider_id=self.provider_id,
                name="MOSS-Audio Instruct",
                capabilities=["audio", "perception", "fast_perception"],
                locality="local",
                metadata={"role": "fast_perception"},
            ),
            ModelDescriptor(
                id="thinking",
                provider_id=self.provider_id,
                name="MOSS-Audio Thinking",
                capabilities=["audio", "perception", "deep_perception", "targeted_relisten"],
                locality="local",
                metadata={"role": "deep_perception"},
            ),
        ]
        self.detail = detail or (
            "Local MOSS-Audio perception is available"
            if available
            else "MOSS-Audio perception is configured; engine availability is not yet known"
        )

    def probe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            id=self.provider_id,
            name="Oída MOSS-Audio",
            kind="openai_compatible",
            locality="local",
            enabled=self.enabled,
            available=self.available,
            authenticated=True,
            capabilities=["audio", "perception", "targeted_relisten", "models"],
            detail=self.detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        return list(self._models)

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        return error_result(
            request,
            "MOSS-Audio is a perception/re-listening provider, not a text conversation reasoner",
        )
