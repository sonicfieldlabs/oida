"""The kind in a pass receipt is the kind that was loaded (G7).

Found by qualifying MOSS-Audio-4B-Thinking for the first time. A request that
selects the model by id leaves `settings.model_kind` at its default of
"instruct", and the receipt recorded the right weights beside the wrong kind — a
record asserting a listening used the instruct model when it used the thinking
one. Nothing in the suite caught it, because nothing had ever run Thinking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from oida.engine_mps import MpsMossEngine
from oida.pass_provenance import pass_receipt


class _Engine(MpsMossEngine):
    """Just the kind resolution; no weights, no device."""

    def __init__(self):  # noqa: D107 - deliberately does not call super
        self.config = SimpleNamespace(
            instruct_model="/w/MOSS-Audio-4B-Instruct",
            thinking_model="/w/MOSS-Audio-4B-Thinking",
        )


@pytest.fixture
def engine():
    return _Engine()


def test_the_thinking_model_is_recorded_as_thinking(engine):
    """The defect, directly: the request said instruct, the weights say thinking."""
    kind, basis = engine._kind_of_loaded("/w/MOSS-Audio-4B-Thinking", "instruct")
    assert kind == "thinking"
    assert "loaded_model" in basis


def test_a_disagreement_is_recorded_rather_than_silently_corrected(engine):
    """A caller asking for one kind and getting another usually means something
    upstream is wrong. Correcting it quietly would hide that."""
    _, basis = engine._kind_of_loaded("/w/MOSS-Audio-4B-Thinking", "instruct")
    assert "the request asked for 'instruct'" in basis


def test_agreement_records_the_plain_basis(engine):
    kind, basis = engine._kind_of_loaded("/w/MOSS-Audio-4B-Instruct", "instruct")
    assert kind == "instruct"
    assert basis == "loaded_model"


def test_an_unconfigured_model_falls_back_to_the_request_and_says_so(engine):
    """An override or an unknown path: the request is the only thing that says
    what this was meant to be, and the basis says that is all it is."""
    kind, basis = engine._kind_of_loaded("/w/some-override", "music")
    assert kind == "music"
    assert "requested" in basis


def test_the_receipt_carries_the_basis():
    receipt = pass_receipt(
        model="/w/MOSS-Audio-4B-Thinking",
        provider="local-moss",
        model_kind="thinking",
        model_kind_basis="loaded_model",
    )
    assert receipt["model_kind"] == "thinking"
    assert receipt["model_kind_basis"] == "loaded_model"


def test_the_basis_defaults_to_requested_not_to_loaded():
    """A receipt built without a basis must not imply the strongest one."""
    receipt = pass_receipt(model="m", provider="p", model_kind="instruct")
    assert receipt["model_kind_basis"] == "requested"
