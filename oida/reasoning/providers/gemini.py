"""Google Gemini reasoning provider using the native Generative Language API."""

from __future__ import annotations

import time
import urllib.parse
from collections.abc import Callable
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
    error_result,
    join_url,
    normalized_result,
    require_matching_provider,
    sanitize_error,
    validate_http_url,
)


class GeminiProvider:
    provider_id = "google"

    def __init__(
        self,
        *,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        api_key: str | None = None,
        api_key_getter: Callable[[], str | None] | None = None,
        enabled: bool = False,
        transport: JsonTransport | None = None,
    ) -> None:
        validate_http_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self._api_key = api_key
        self._api_key_getter = api_key_getter
        self._transport = transport or UrllibJsonTransport()

    def _key(self) -> str | None:
        return self._api_key_getter() if self._api_key_getter else self._api_key

    @staticmethod
    def _headers(key: str | None) -> dict[str, str]:
        return {"x-goog-api-key": key} if key else {}

    def probe(self) -> ProviderDescriptor:
        key = self._key() if self.enabled else None
        available = False
        detail = "Disabled"
        if self.enabled:
            if not key:
                detail = "Google API key is not configured"
            else:
                try:
                    self._transport.request(
                        "GET",
                        join_url(self.base_url, "/models"),
                        headers=self._headers(key),
                        timeout=5,
                    )
                    available = True
                    detail = "Google Generative Language API is reachable"
                except (ProviderTransportError, ValueError) as exc:
                    detail = sanitize_error(exc, secrets=[key])
        return ProviderDescriptor(
            id=self.provider_id,
            name="Google Gemini API",
            kind="google",
            locality="external",
            enabled=self.enabled,
            available=available,
            authenticated=bool(key),
            capabilities=["models", "structured_output", "text", "audio"],
            detail=detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        if not self.enabled:
            return []
        key = self._key()
        if not key:
            return []
        response = self._transport.request(
            "GET",
            join_url(self.base_url, "/models"),
            headers=self._headers(key),
            timeout=10,
        )
        data = response.data if isinstance(response.data, dict) else {}
        result: list[ModelDescriptor] = []
        for item in data.get("models", []) if isinstance(data.get("models"), list) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("name") or "").removeprefix("models/")
            methods = item.get("supportedGenerationMethods") or []
            if not model_id or "generateContent" not in methods:
                continue
            result.append(
                ModelDescriptor(
                    id=model_id,
                    provider_id=self.provider_id,
                    name=str(item.get("displayName") or model_id),
                    capabilities=["text", "structured_output", "audio"],
                    locality="external",
                    metadata={"supported_generation_methods": methods},
                )
            )
        return result

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        if not request.model_id:
            return error_result(request, "Google Gemini requires an explicit model")
        key = self._key()
        if not key:
            return error_result(request, "Google API key is not configured")
        model = urllib.parse.quote(request.model_id.removeprefix("models/"), safe="-._")
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": request.system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": request.user_prompt}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": request.response_schema,
            },
        }
        started = time.monotonic()
        try:
            response = self._transport.request(
                "POST",
                join_url(self.base_url, f"/models/{model}:generateContent"),
                payload=payload,
                headers=self._headers(key),
                timeout=request.timeout_seconds,
            )
            data = response.data if isinstance(response.data, dict) else {}
            candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
            candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
            content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
            parts = content.get("parts") if isinstance(content.get("parts"), list) else []
            text = "".join(
                str(part.get("text") or "") for part in parts if isinstance(part, dict)
            )
            if not text:
                raise ProviderTransportError("Gemini response did not contain candidate text")
            usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
            return normalized_result(
                request,
                content=text,
                usage={
                    "input_tokens": usage.get("promptTokenCount"),
                    "output_tokens": usage.get("candidatesTokenCount"),
                    "total_tokens": usage.get("totalTokenCount"),
                },
                latency_ms=(time.monotonic() - started) * 1000,
                metadata={"finish_reason": candidate.get("finishReason")},
            )
        except (ProviderTransportError, ValueError) as exc:
            return error_result(
                request,
                exc,
                latency_ms=(time.monotonic() - started) * 1000,
                secrets=[key],
            )
