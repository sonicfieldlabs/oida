from __future__ import annotations

from oida.config import OidaConfig
from oida.engine_base import EngineUnavailable, MossEngine
from oida.engine_mps import MpsMossEngine
from oida.engine_sglang import SGLangMossEngine
from oida.engine_stub import StubMossEngine


def build_engine(config: OidaConfig) -> MossEngine:
    if config.profile == "stub":
        return StubMossEngine()
    if config.profile == "cuda-server":
        return SGLangMossEngine(config)
    if config.profile == "mac-mps":
        engine = MpsMossEngine(config)
        # A failed primary used to fall back to the stub silently, producing an
        # account labelled stub/no-audio-model with no signal to the operator
        # beyond a receipt field. Decided 15 September: a backup model is
        # optional; if one is configured it substitutes and the receipt names
        # the substitution; if none is, the pass refuses. The stub is a profile,
        # not a fallback.
        if config.backup_model:
            backup = MpsMossEngine(config)
            for kind in ("instruct", "thinking", "transcription", "music", "targeted_relisten"):
                backup.set_model(kind, config.backup_model)
            return FallbackEngine(primary=engine, fallback=backup, backup_model=config.backup_model)
        return engine
    raise ValueError(f"unknown oida engine profile: {config.profile}")


class FallbackEngine(MossEngine):
    """A primary engine with a named substitute.

    Constructed by build_engine only when a backup model is configured. Tests
    construct it directly with a stub fallback to exercise the refusal paths;
    that remains valid, and is the only way a stub now stands behind mac-mps.
    """

    def __init__(self, primary: MossEngine, fallback: MossEngine, backup_model: str | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.backup_model = backup_model
        self.profile = primary.profile

    def generate(self, *args, **kwargs):
        try:
            return self.primary.generate(*args, **kwargs)
        except EngineUnavailable as exc:
            from oida.reasoning.audio_selection import selected_audio_model
            if selected_audio_model() is not None:
                raise
            result = self.fallback.generate(*args, **kwargs)
            provenance = list(result.pass_provenance or [])
            # The substitution is a fact about this pass and travels with it.
            for item in provenance:
                if isinstance(item, dict):
                    item["substituted_for_unavailable_primary"] = {
                        "primary": getattr(self.primary, "profile", None),
                        "reason": str(exc),
                        "backup_model": self.backup_model,
                    }
            return result.__class__(
                text=result.text,
                model=result.model,
                profile=self.profile,
                settings=result.settings,
                reasoning_trace=result.reasoning_trace,
                wall_ms=result.wall_ms,
                unavailable_reason=str(exc),
                pass_provenance=provenance,
            )

    def prepare_input_binding(self, audio_path: str, model_kind: str = "instruct") -> dict:
        return self.primary.prepare_input_binding(audio_path, model_kind)

    def prewarm(self, model_kind: str = "instruct") -> None:
        self.primary.prewarm(model_kind)

    def runtime_status(self) -> dict[str, object]:
        from oida.reasoning.audio_selection import selected_audio_model

        status = dict(self.primary.runtime_status())
        permitted = selected_audio_model() is None
        status["configured_backup_model"] = self.backup_model
        status["configured_fallback_profile"] = self.fallback.profile
        status["fallback_permitted_for_selection"] = permitted
        # A cold engine and an engine that cannot start look identical from
        # outside unless the reason is carried out. Without this, a fallback to
        # the stub reads as "not loaded yet" forever, and the only place the
        # truth appeared was one field of a pass receipt after the fact.
        reason = self.primary_unavailable_reason()
        if reason:
            status["primary_unavailable_reason"] = reason
            # Configuration is not a completed substitution or backup-readiness
            # receipt. Explicit deployment selection refuses fallback entirely.
            status["falls_back_to_stub"] = permitted and self.fallback.profile == "stub"
        return status

    def primary_unavailable_reason(self) -> str | None:
        """Why the real engine cannot serve, or None when it can."""
        try:
            self.primary.ensure_ready()
        except EngineUnavailable as exc:
            return str(exc)
        except Exception as exc:  # a broken runtime is still unavailable
            return f"{type(exc).__name__}: {exc}"
        return None

    def set_model(self, model_kind: str, model_id: str) -> None:
        self.primary.set_model(model_kind, model_id)
