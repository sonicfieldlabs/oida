import json
from pathlib import Path

import pytest

from oida.reporting import DEGENERATE_NOTE, _guard_prose, looks_degenerate

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "caption_sanity.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", FIXTURES["degenerate"])
def test_unstable_decoding_is_declined_at_any_length(text):
    assert looks_degenerate(text)
    assert _guard_prose(text) == (None, DEGENERATE_NOTE)


@pytest.mark.parametrize("text", FIXTURES["accounts"])
def test_complete_accounts_are_kept(text):
    assert not looks_degenerate(text)


def test_short_sentence_is_an_account_and_short_fragment_is_not():
    assert not looks_degenerate("Steady hum.")
    assert looks_degenerate("A radioŀ")
    assert looks_degenerate("An audio kısmı")


def test_answers_to_a_question_may_be_short():
    for answer in ("Yes", "No", "Two", "Silence"):
        assert looks_degenerate(answer)
        assert not looks_degenerate(answer, allow_short=True)
        assert _guard_prose(answer, allow_short=True) == (answer, None)


def test_each_rule_on_its_own():
    assert looks_degenerate("The recording is calm � and steady throughout the window.")
    assert looks_degenerate("A low hum with 绿化意襄 under a faint hiss across the window.")
    assert looks_degenerate("A continuousﻳ hum fills the window from start to end.")
    assert looks_degenerate("The audio\U0001f138 has a steady hum from start to end of it.")
    assert looks_degenerate("This audio.upload has a steady hum from start to end of it.")
    assert looks_degenerate("The audioATORY has a steady hum from start to end of it.")
    assert looks_degenerate("A hum, then ------------------------------------------ quiet.")
    assert not looks_degenerate("A 50–60 Hz hum, about 3 µs rise, and a hiss; e.g. HVAC.")
    assert not looks_degenerate('{"category":"Ambient","acoustic_properties":"a steady low hum"}')


def test_empty_text_is_not_degenerate():
    assert not looks_degenerate(None)
    assert not looks_degenerate("")
    assert not looks_degenerate("   ")
