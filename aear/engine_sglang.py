from __future__ import annotations

import json
import time
from urllib import request

from aear.config import AearConfig
from aear.engine_base import EngineResult, EngineUnavailable, MossEngine
from aear.recipes import GenerationSettings


class SGLangMossEngine(MossEngine):
    profile = "cuda-server"

    def __init__(self, config: AearConfig) -> None:
        self.base_url = config.sglang_base_url.rstrip("/")

    def generate(
        self,
        audio_path: str,
        prompt: str,
        settings: GenerationSettings,
        thinking_budget: int | None = None,
    ) -> EngineResult:
        payload: dict[str, object] = {
            "model": "moss-audio",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "audio_url", "audio_url": {"url": audio_path}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_new_tokens,
        }
        if thinking_budget is not None:
            payload["custom_params"] = {"thinking_budget": thinking_budget}
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.perf_counter()
        try:
            with request.urlopen(req, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise EngineUnavailable(f"SGLang server unavailable at {self.base_url}") from exc

        choice = result.get("choices", [{}])[0]
        message = choice.get("message", {})
        text = str(message.get("content") or "")
        reasoning = message.get("reasoning_content")
        return EngineResult(
            text=text.strip(),
            model=str(result.get("model") or "moss-audio"),
            profile=self.profile,
            settings=settings,
            reasoning_trace=str(reasoning).strip() if reasoning else None,
            wall_ms=round((time.perf_counter() - start) * 1000),
        )

