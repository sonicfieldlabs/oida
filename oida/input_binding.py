"""Bind prepared model inputs to execution without granting claim authority."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json

_EXPECTED: ContextVar[dict[str, str] | None] = ContextVar(
    "oida_expected_input_bindings", default=None
)


class InputBindingChanged(ValueError):
    """A different adapter, model or input must not silently satisfy a binding."""


def unknown_binding(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


def input_array_receipt(audio, rate: int) -> dict:
    import numpy as np

    array = np.asarray(audio)
    if (
        array.ndim != 1
        or not isinstance(rate, int)
        or isinstance(rate, bool)
        or rate <= 0
        or array.size == 0
    ):
        raise ValueError(
            "Model input must be a nonempty mono array with a positive rate"
        )
    if array.dtype != np.dtype("float32") or not np.isfinite(array).all():
        raise ValueError("MOSS input must contain finite float32 samples")
    # Canonical byte order makes the representation digest explicit.
    canonical = np.ascontiguousarray(array, dtype="<f4")
    return {
        "status": "known",
        "sample_rate_hz": rate,
        "channels": 1,
        "sample_count": int(array.size),
        "duration_s": float(array.size / rate),
        "sha256": hashlib.sha256(memoryview(canonical).cast("B")).hexdigest(),
        "encoding": "mono-f32le",
        "basis": "array passed to MOSS processor; not physical capture bandwidth",
    }


def binding_for_receipt(receipt: dict) -> dict:
    payload = {
        key: deepcopy(receipt[key])
        for key in (
            "model",
            "provider",
            "model_kind",
            "revision",
            "weights",
            "effective_input",
        )
    }
    identity = {
        key: payload[key]
        for key in ("model", "provider", "model_kind", "revision", "weights")
    }

    def digest(value):
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    return {
        "status": "prepared",
        "binding_id": "binding:sha256:" + digest(payload),
        "model_ref": "model:sha256:" + digest(identity),
        "representation_ref": "input:sha256:" + digest(payload["effective_input"]),
        "receipt": payload,
    }


@contextmanager
def enforce_input_bindings(expected: dict[str, str]):
    if not expected or any(
        not isinstance(k, str)
        or not isinstance(v, str)
        or not v.startswith("binding:sha256:")
        for k, v in expected.items()
    ):
        raise ValueError(
            "Expected input bindings must name model kinds and binding IDs"
        )
    token = _EXPECTED.set(dict(expected))
    try:
        yield
    finally:
        _EXPECTED.reset(token)


def reject_unobservable_binding():
    if _EXPECTED.get() is not None:
        raise InputBindingChanged(
            "Selected adapter cannot honor the prepared input binding"
        )


def verify_input_binding(receipt: dict, *, requested_kind: str) -> dict:
    """Select the prepared invocation, then compare its actual-input identity.

    Recipe roles (music, transcription, targeted_relisten) are not loaded model
    families (thinking, instruct). The digest must retain the actual family;
    looking up that digest must use the same invocation key as preparation.
    """
    binding = binding_for_receipt(receipt)
    expected = _EXPECTED.get()
    if (
        expected is not None
        and expected.get(requested_kind) != binding["binding_id"]
    ):
        raise InputBindingChanged(
            "Model, adapter or preprocessed input changed after preparation"
        )
    return binding


def has_input_bindings():
    return _EXPECTED.get() is not None
