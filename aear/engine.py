from __future__ import annotations

from aear.config import AearConfig
from aear.engine_base import EngineUnavailable, MossEngine
from aear.engine_mps import MpsMossEngine
from aear.engine_sglang import SGLangMossEngine
from aear.engine_stub import StubMossEngine


def build_engine(config: AearConfig) -> MossEngine:
    if config.profile == "stub":
        return StubMossEngine()
    if config.profile == "cuda-server":
        return SGLangMossEngine(config)
    if config.profile == "mac-mps":
        engine = MpsMossEngine(config)
        if config.require_model:
            return engine
        return FallbackEngine(primary=engine, fallback=StubMossEngine("mac-mps profile configured but MOSS runtime is not ready"))
    raise ValueError(f"unknown hmm engine profile: {config.profile}")


class FallbackEngine(MossEngine):
    def __init__(self, primary: MossEngine, fallback: MossEngine) -> None:
        self.primary = primary
        self.fallback = fallback
        self.profile = primary.profile

    def generate(self, *args, **kwargs):
        try:
            return self.primary.generate(*args, **kwargs)
        except EngineUnavailable as exc:
            result = self.fallback.generate(*args, **kwargs)
            return result.__class__(
                text=result.text,
                model=result.model,
                profile=self.profile,
                settings=result.settings,
                reasoning_trace=result.reasoning_trace,
                wall_ms=result.wall_ms,
                unavailable_reason=str(exc),
            )

    def prewarm(self, model_kind: str = "instruct") -> None:
        self.primary.prewarm(model_kind)

    def runtime_status(self) -> dict[str, object]:
        return self.primary.runtime_status()

    def set_model(self, model_kind: str, model_id: str) -> None:
        self.primary.set_model(model_kind, model_id)
