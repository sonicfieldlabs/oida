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
        # The stub never fabricates perception text. Empty output keeps captions,
        # events, and hypotheses clean so the DSP signal listener supplies the
        # summary and the evidence level honestly stays at measured_signal.
        Path(audio_path)  # keep signature parity; path validity is the caller's concern
        lowered = prompt.lower()
        if "speaker" in lowered or "music" in lowered:
            text = "present: false"
        else:
            text = ""
        return EngineResult(
            text=text,
            model="stub/no-audio-model",
            profile=self.profile,
            settings=settings,
            reasoning_trace=None,
            wall_ms=0,
            unavailable_reason=self.reason,
        )

