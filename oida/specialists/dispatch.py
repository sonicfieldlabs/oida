"""Wrap the existing interpreter; optional lanes do not replace its settings."""

from oida.engine_base import EngineUnavailable
from oida.reporting import report
from oida.operation_control import checkpoint


def interpret(
    engine,
    path,
    profile,
    *,
    passes,
    chunk_seconds,
    overlap_seconds,
    partial=False,
    report_fn=None,
):
    report_fn = report_fn or report
    try:
        value = report_fn(
            engine,
            path,
            profile,
            passes=passes,
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
        )
        unavailable = (
            getattr(value.engine, "unavailable_reason", None)
            if hasattr(value, "engine")
            else None
        )
        status = (
            "not_requested"
            if not passes
            else "unavailable"
            if unavailable
            else "complete"
        )
        return value, {
            "task": "interpretation",
            "status": status,
            **({"reason": unavailable} if unavailable else {}),
        }
    except (EngineUnavailable, RuntimeError):
        if not partial:
            raise
        checkpoint()
        value = report_fn(
            engine,
            path,
            profile,
            passes=[],
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
        )
        return value, {
            "task": "interpretation",
            "status": "failed",
            "reason": "Audio interpreter failed; DSP and optional specialist evidence retained",
        }
