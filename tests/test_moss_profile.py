"""Qualification harness assertions only; no model loading or network calls."""

from pathlib import Path
import runpy

import pytest

from oida.engine_base import EngineResult, selected_model
from oida.reasoning.audio_selection import selected_audio_model, selector
from oida.reasoning.model_catalog import find_model_spec
from oida.recipes import INSTRUCT_CAPTION


@pytest.mark.parametrize("receipt_kind", ["valid", "missing", "wrong"])
def test_routed_canary_checks_receipts_and_resets_context(receipt_kind):
    generate = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/profile-moss-runtime.py")
    )["routed_generate"]
    target = "/local/MOSS-Audio-4B-Instruct"
    actual = selector(find_model_spec("oida_moss", target))

    class Router:
        def generate(self, *args):
            assert selected_model() == target
            requested = selected_audio_model()
            assert requested.model_id == "instruct"
            receipts = (
                []
                if receipt_kind == "missing"
                else [
                    {
                        "requested_audio_model": requested.model_dump(),
                        "actual_audio_model": actual.model_dump()
                        if receipt_kind == "valid"
                        else {},
                    }
                ]
            )
            return EngineResult(
                text="fixture",
                model=target,
                profile="fixture",
                settings=INSTRUCT_CAPTION,
                pass_provenance=receipts,
            )

    if receipt_kind == "valid":
        assert (
            generate(Router(), target, "instruct", "fixture.wav", INSTRUCT_CAPTION).text
            == "fixture"
        )
    else:
        with pytest.raises(RuntimeError):
            generate(Router(), target, "instruct", "fixture.wav", INSTRUCT_CAPTION)
    assert selected_model() is None
    assert selected_audio_model() is None
