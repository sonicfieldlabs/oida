"""HTTP reasoning providers: Ollama, OpenAI-compatible APIs, and OpenRouter."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRequest,
    ProviderResult,
)

from .base import (
    JsonTransport,
    ProviderTransportError,
    UrllibJsonTransport,
    endpoint_locality,
    error_result,
    join_url,
    normalized_result,
    require_matching_provider,
    sanitize_error,
    validate_http_url,
)


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


class OllamaProvider:
    provider_id = "ollama"

    def __init__(
        self,
        *,
        provider_id: str = "ollama",
        base_url: str = "http://127.0.0.1:11434",
        enabled: bool = True,
        transport: JsonTransport | None = None,
    ) -> None:
        validate_http_url(base_url)
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self._transport = transport or UrllibJsonTransport()

    def probe(self) -> ProviderDescriptor:
        available = False
        detail = "Disabled"
        locality = endpoint_locality(self.base_url)
        if self.enabled or locality == "local":
            try:
                self._transport.request("GET", join_url(self.base_url, "/api/tags"), timeout=3)
                available = True
                detail = "Ollama is reachable" + (" (disabled)" if not self.enabled else "")
            except (ProviderTransportError, ValueError) as exc:
                detail = str(exc)
        return ProviderDescriptor(
            id=self.provider_id,
            name="Ollama",
            kind="ollama",
            locality=locality,
            enabled=self.enabled,
            available=available,
            authenticated=available,
            capabilities=["models", "structured_output", "text"],
            detail=detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        if not self.enabled:
            return []
        response = self._transport.request(
            "GET", join_url(self.base_url, "/api/tags"), timeout=5
        )
        models: list[ModelDescriptor] = []
        for item in response.data.get("models", []) if isinstance(response.data, dict) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("model") or item.get("name") or "").strip()
            if not model_id:
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            lowered = model_id.lower()
            is_cloud = ":" in lowered and lowered.endswith("cloud")
            models.append(
                ModelDescriptor(
                    id=model_id,
                    provider_id=self.provider_id,
                    name=model_id,
                    capabilities=["text", "structured_output"],
                    locality="external" if is_cloud else endpoint_locality(self.base_url),
                    metadata={
                        "family": details.get("family"),
                        "parameter_size": details.get("parameter_size"),
                        "quantization_level": details.get("quantization_level"),
                        "cloud": is_cloud,
                    },
                )
            )
        return models

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        if not request.model_id:
            return error_result(request, "Ollama requires an explicit model")
        started = time.monotonic()
        payload: dict[str, Any] = {
            "model": request.model_id,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
            # Deliberate opt-in: Ollama may return a separate thinking field,
            # which Oída never stores or exposes.
            "think": bool(request.metadata.get("thinking", False)),
            "format": request.response_schema or "json",
            "options": {},
        }
        if request.temperature is not None:
            payload["options"]["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["options"]["num_predict"] = request.max_output_tokens
        try:
            response = self._transport.request(
                "POST",
                join_url(self.base_url, "/api/chat"),
                payload=payload,
                timeout=request.timeout_seconds,
            )
            data = response.data if isinstance(response.data, dict) else {}
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            content = message.get("content")
            if not isinstance(content, str):
                raise ProviderTransportError("Ollama response did not contain message.content")
            return normalized_result(
                request,
                content=content,
                usage={
                    "input_tokens": data.get("prompt_eval_count"),
                    "output_tokens": data.get("eval_count"),
                },
                latency_ms=_elapsed_ms(started),
                metadata={"done_reason": data.get("done_reason")},
            )
        except (ProviderTransportError, ValueError) as exc:
            return error_result(request, exc, latency_ms=_elapsed_ms(started))


class OpenAICompatibleProvider:
    provider_id = "openai_compatible"

    def __init__(
        self,
        *,
        provider_id: str = "openai_compatible",
        base_url: str = "http://127.0.0.1:8000/v1",
        api_key: str | None = None,
        api_key_getter: Callable[[], str | None] | None = None,
        enabled: bool = False,
        supports_json_schema: bool = True,
        display_name: str = "OpenAI-compatible API",
        provider_kind: str = "openai_compatible",
        capabilities: list[str] | None = None,
        force_stream: bool = False,
        extra_headers: Mapping[str, str] | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        validate_http_url(base_url)
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self.supports_json_schema = supports_json_schema
        self.display_name = display_name
        self.provider_kind = provider_kind
        self.capabilities = list(capabilities or ["models", "structured_output", "text"])
        self.force_stream = force_stream
        self._api_key = api_key
        self._api_key_getter = api_key_getter
        self._extra_headers = dict(extra_headers or {})
        self._transport = transport or UrllibJsonTransport()

    def _key(self) -> str | None:
        return self._api_key_getter() if self._api_key_getter else self._api_key

    def _headers(self, key: str | None) -> dict[str, str]:
        headers = dict(self._extra_headers)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def probe(self) -> ProviderDescriptor:
        available = False
        detail = "Disabled"
        key: str | None = None
        locality = endpoint_locality(self.base_url)
        if self.enabled or locality == "local":
            try:
                key = self._key() if self.enabled else None
                self._transport.request(
                    "GET",
                    join_url(self.base_url, "/models"),
                    headers=self._headers(key),
                    timeout=5,
                )
                available = True
                detail = "Endpoint is reachable" + (" (disabled)" if not self.enabled else "")
            except (ProviderTransportError, ValueError) as exc:
                detail = sanitize_error(exc, secrets=[key] if key else [])
        return ProviderDescriptor(
            id=self.provider_id,
            name=self.display_name,
            kind=self.provider_kind,
            locality=locality,
            enabled=self.enabled,
            available=available,
            authenticated=bool(key) if locality == "external" else available,
            capabilities=self.capabilities,
            detail=detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        if not self.enabled:
            return []
        key = self._key()
        response = self._transport.request(
            "GET",
            join_url(self.base_url, "/models"),
            headers=self._headers(key),
            timeout=10,
        )
        data = response.data.get("data", []) if isinstance(response.data, dict) else []
        result: list[ModelDescriptor] = []
        for item in data:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            model_id = str(item["id"])
            result.append(
                ModelDescriptor(
                    id=model_id,
                    provider_id=self.provider_id,
                    name=str(item.get("name") or model_id),
                    capabilities=[value for value in self.capabilities if value != "models"],
                    locality=endpoint_locality(self.base_url),
                    metadata={"owned_by": item.get("owned_by")},
                )
            )
        return result

    def _completion_payload(self, request: ProviderRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model_id,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": self.force_stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if self.supports_json_schema and request.response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "oida_response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        if self.force_stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        if not request.model_id:
            return error_result(request, "An explicit model is required")
        started = time.monotonic()
        key: str | None = None
        try:
            key = self._key()
            response = self._transport.request(
                "POST",
                join_url(self.base_url, "/chat/completions"),
                payload=self._completion_payload(request),
                headers=self._headers(key),
                timeout=request.timeout_seconds,
            )
            data = response.data if isinstance(response.data, dict) else {}
            choices = data.get("choices") if isinstance(data.get("choices"), list) else []
            message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                raise ProviderTransportError("Completion response did not contain text content")
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            return normalized_result(
                request,
                content=content,
                usage=usage,
                latency_ms=_elapsed_ms(started),
                metadata={
                    "response_id": data.get("id"),
                    "finish_reason": choices[0].get("finish_reason") if choices else None,
                },
            )
        except (ProviderTransportError, ValueError) as exc:
            return error_result(
                request,
                exc,
                latency_ms=_elapsed_ms(started),
                secrets=[key] if key else [],
            )


class OpenRouterProvider(OpenAICompatibleProvider):
    provider_id = "openrouter"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_getter: Callable[[], str | None] | None = None,
        enabled: bool = False,
        app_url: str | None = None,
        app_title: str = "Oída",
        transport: JsonTransport | None = None,
    ) -> None:
        headers: dict[str, str] = {"X-OpenRouter-Title": app_title}
        if app_url:
            headers["HTTP-Referer"] = app_url
        super().__init__(
            provider_id=self.provider_id,
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            api_key_getter=api_key_getter,
            enabled=enabled,
            supports_json_schema=True,
            display_name="OpenRouter",
            provider_kind="openrouter",
            capabilities=["models", "structured_output", "text", "audio"],
            extra_headers=headers,
            transport=transport,
        )

    def probe(self) -> ProviderDescriptor:
        base = super().probe()
        return ProviderDescriptor(
            id=self.provider_id,
            name="OpenRouter",
            kind="openrouter",
            locality="external",
            enabled=base.enabled,
            available=base.available,
            authenticated=base.authenticated,
            capabilities=base.capabilities,
            detail=base.detail,
        )
