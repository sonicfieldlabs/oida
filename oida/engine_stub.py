from __future__ import annotations

from pathlib import Path

from oida.engine_base import EngineResult, MossEngine
from oida.recipes import GenerationSettings
from oida.pass_provenance import pass_receipt


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
        from oida.input_binding import reject_unobservable_binding
        reject_unobservable_binding()
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
            pass_provenance=[pass_receipt(model="stub/no-audio-model", provider="stub",
                model_kind=settings.model_kind,
                weights={"status": "not_applicable", "reason": "No model weights used"},
                effective_input={"status": "not_applicable", "reason": "No model consumed audio"})],
        )

