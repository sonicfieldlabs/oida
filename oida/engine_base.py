from __future__ import annotations

from dataclasses import dataclass

from oida.recipes import GenerationSettings


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

    def prewarm(self, model_kind: str = "instruct") -> None:
        """Load weights ahead of the first request. Default: nothing to warm."""
        return None

    def runtime_status(self) -> dict[str, object]:
        return {"profile": self.profile, "loaded_models": [], "device": None, "assignments": {}}

    def set_model(self, model_kind: str, model_id: str) -> None:
        raise ValueError(f"the {self.profile} engine does not support model selection")

