from __future__ import annotations

from pathlib import Path

from aear.engine_base import EngineResult, MossEngine
from aear.recipes import GenerationSettings


class StubMossEngine(MossEngine):
    profile = "stub"

    def __init__(self, reason: str = "MOSS-Audio weights are not configured") -> None:
        self.reason = reason

    def generate(
        self,
        audio_path: str,
        prompt: str,
        settings: GenerationSettings,
        thinking_budget: int | None = None,
    ) -> EngineResult:
        name = Path(audio_path).name
        lowered = prompt.lower()
        if "transcribe" in lowered:
            text = ""
        elif "sound event" in lowered or "distinct sound event" in lowered:
            text = ""
        elif "speaker" in lowered:
            text = "present: false\nsummary: unavailable because stub engine did not decode audio"
        elif "music" in lowered:
            text = "present: false\nsummary: unavailable because stub engine did not decode audio"
        else:
            text = f"Stub engine did not listen to {name}. Configure MOSS-Audio to produce perception evidence."
        return EngineResult(
            text=text,
            model="stub/no-audio-model",
            profile=self.profile,
            settings=settings,
            reasoning_trace=None,
            wall_ms=0,
            unavailable_reason=self.reason,
        )

