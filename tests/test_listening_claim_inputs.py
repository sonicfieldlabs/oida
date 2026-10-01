"""Optional event claim summaries must not crash or fabricate authority."""
from copy import deepcopy
import wave

import pytest

from oida.engine_stub import StubMossEngine
from oida.listening import listening_event_dict, _claim_hypotheses, _claim_statements
from oida.reporting import report, report_to_dict


@pytest.mark.parametrize("command", [None, {}, {"claim_summary": None}, {"claim_summary": {}}])
def test_event_builder_accepts_absent_optional_claim_summary(tmp_path, command):
    path = tmp_path / "silent.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 1600)
    perception = report_to_dict(report(StubMossEngine(), str(path), passes=[]))
    before = deepcopy(perception)
    event = listening_event_dict(perception, command_output=command)
    assert event["id"] and event["segment"]["data_ref"]["sha256"]
    assert event["aggregate"]["hypotheses"] == []
    assert perception == before


@pytest.mark.parametrize("value", [None, 7, False, "claim", {"statement": "not a list"}])
def test_nonlist_claim_categories_do_not_become_claims(value):
    assert _claim_statements({"measured": value}, "measured") == []
    assert _claim_hypotheses({"inferred": value}, "inferred") == []


def test_valid_claim_lists_keep_statements_and_uncertainty():
    claims = {"inferred": [None, "noise", {}, {"statement": "possible pulse", "basis": "fixture"}]}
    before = deepcopy(claims)
    result = _claim_hypotheses(claims, "inferred")
    assert len(result) == 1
    assert result[0].statement == "possible pulse"
    assert result[0].confidence == "undetermined"
    assert _claim_statements(claims, "inferred") == ["possible pulse"]
    assert claims == before
