from __future__ import annotations

from dataclasses import dataclass

from aear.recipes import GenerationSettings


class EngineUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class EngineResult:
    text: str
    model: str
    profile: str
    settings: GenerationSettings
    reasoning_trace: str | None = None
    wall_ms: int | None = None
    unavailable_reason: str | None = None


class MossEngine:
    profile = "base"

    def generate(
        self,
        audio_path: str,
        prompt: str,
        settings: GenerationSettings,
        thinking_budget: int | None = None,
    ) -> EngineResult:
        raise NotImplementedError

