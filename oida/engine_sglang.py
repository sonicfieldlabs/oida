from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from oida.config import OidaConfig
from oida.engine_base import EngineResult, EngineUnavailable, MossEngine
from oida.recipes import GenerationSettings
from oida.reasoning.providers.base import (
    JsonTransport,
    ProviderTransportError,
    UrllibJsonTransport,
    join_url,
    validate_http_url,
)


class SGLangMossEngine(MossEngine):
    profile = "cuda-server"

    def __init__(self, config: OidaConfig, *, transport: JsonTransport | None = None) -> None:
        self.base_url = config.sglang_base_url.rstrip("/")
        validate_http_url(self.base_url)
        self.thinking_processor = getattr(config, "sglang_thinking_processor", None)
        self._transport = transport or UrllibJsonTransport()
        self._model_overrides: dict[str, str] = {}

    def model_id_for_kind(self, model_kind: str) -> str:
        return self._model_overrides.get(model_kind, "moss-audio")

    def set_model(self, model_kind: str, model_id: str) -> None:
        if model_kind not in {"instruct", "thinking", "transcription", "music", "targeted_relisten"}:
            raise ValueError(f"unknown model kind: {model_kind}")
        self._model_overrides[model_kind] = model_id

    def runtime_status(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "loaded_models": [],
            "device": "remote-cuda",
            "thinking_budget_supported": bool(self.thinking_processor),
            "assignments": {
                kind: self.model_id_for_kind(kind)
                for kind in ("instruct", "thinking", "transcription", "music", "targeted_relisten")
            },
        }

    def generate(
        self,
        audio_path: str,
        prompt: str,
        settings: GenerationSettings,
        thinking_budget: int | None = None,
    ) -> EngineResult:
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"audio path does not exist or is not a file: {audio_path}")
        payload: dict[str, object] = {
            "model": self.model_id_for_kind(settings.model_kind),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        # The OpenMOSS SGLang fork accepts a server-visible
                        # filesystem path in audio_url. Resolve it so local and
                        # shared-mount deployments receive an unambiguous path.
                        {"type": "audio_url", "audio_url": {"url": str(path)}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_new_tokens,
            "separate_reasoning": True,
        }
        if thinking_budget is not None:
            if thinking_budget < 0:
                raise ValueError("thinking_budget must be greater than or equal to zero")
            if not self.thinking_processor:
                raise EngineUnavailable(
                    "SGLang thinking budgets require OIDA_SGLANG_THINKING_PROCESSOR; "
                    "custom_params alone does not enforce a budget"
                )
            payload["custom_logit_processor"] = self.thinking_processor
            payload["custom_params"] = {"thinking_budget": thinking_budget}
        start = time.perf_counter()
        try:
            response = self._transport.request(
                "POST",
                join_url(self.base_url, "/v1/chat/completions"),
                payload=payload,
                timeout=300,
            )
            result = response.data
            if not isinstance(result, dict):
                raise ProviderTransportError("SGLang returned a non-object response")
            choices = result.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ProviderTransportError("SGLang returned no completion choice")
            message = choices[0].get("message")
            if not isinstance(message, dict):
                raise ProviderTransportError("SGLang returned an invalid completion message")
        except (ProviderTransportError, OSError, ValueError) as exc:
            raise EngineUnavailable(f"SGLang server unavailable at {self.base_url}") from exc

        text = _message_text(message.get("content"))
        reasoning = message.get("reasoning_content")
        return EngineResult(
            text=text.strip(),
            model=str(result.get("model") or self.model_id_for_kind(settings.model_kind)),
            profile=self.profile,
            settings=settings,
            reasoning_trace=str(reasoning).strip() if reasoning else None,
            wall_ms=round((time.perf_counter() - start) * 1000),
        )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {None, "text"}
        )
    return ""
