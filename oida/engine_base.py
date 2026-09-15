from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
from contextvars import ContextVar

from oida.recipes import GenerationSettings


_selected_model: ContextVar[str | None] = ContextVar("listening_model", default=None)


def selected_model() -> str | None:
    return _selected_model.get()


@contextmanager
def use_listening_model(model_id: str | None):
    """Pin all passes and input bindings in this request without global mutation."""
    token = _selected_model.set(model_id)
    try:
        yield
    finally:
        _selected_model.reset(token)


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
    pass_provenance: list[dict] = field(default_factory=list)
    usage: dict | None = None


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

    def prepare_input_binding(
        self, audio_path: str, model_kind: str = "instruct"
    ) -> dict:
        from oida.input_binding import unknown_binding

        return unknown_binding(
            "Adapter does not expose verifiable prepared model input"
        )

    def ensure_ready(self) -> None:
        """Raise EngineUnavailable if a pass could not be served right now.

        The default is that an engine which exists can serve; engines with
        optional dependencies override this so a caller can distinguish a cold
        engine from a broken one without loading weights.
        """
        return None

    def prewarm(self, model_kind: str = "instruct") -> None:
        """Load weights ahead of the first request. Default: nothing to warm."""
        return None

    def runtime_status(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "loaded_models": [],
            "device": None,
            "assignments": {},
        }

    def set_model(self, model_kind: str, model_id: str) -> None:
        raise ValueError(f"the {self.profile} engine does not support model selection")
