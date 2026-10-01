from copy import deepcopy
import json

import jsonschema
import pytest

from oida.reasoning.local.worker import request_schema
from oida.situated_listener import Decision


def setup():
    envelope = {"evidence": [{"ref": "a"}], "allowed_actions": ["stop", "relisten"]}
    value = {"summary": "An unresolved event", "findings": [],
             "next_move": {"action": "relisten", "reason": "Resolve the gap", "evidence_refs": ["a"]}}
    return envelope, value


def test_constraints_preserve_source_schema_and_valid_bounded_proposal():
    envelope, value = setup()
    base = Decision.model_json_schema()
    before = deepcopy(base)
    schema = request_schema(base, json.dumps(envelope))
    assert base == before
    jsonschema.validate(value, schema)


@pytest.mark.parametrize("damage", ["task", "interval", "reference", "comparison", "action"])
def test_unoffered_output_is_not_valid_under_request_schema(damage):
    envelope, value = setup()
    if damage == "task":
        value["next_move"]["analysis_tasks"] = ["tag_events"]
    elif damage == "interval":
        value["next_move"]["segment"] = {"start_seconds": 0, "seconds": 10}
    elif damage == "reference":
        value["next_move"]["evidence_refs"] = ["invented"]
    elif damage == "comparison":
        value["findings"] = [{"kind": "divergence", "text": "Difference", "evidence_refs": ["a"]}]
    else:
        value["next_move"]["action"] = "generate"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, request_schema(Decision.model_json_schema(), json.dumps(envelope)))


def test_offered_autonomous_analysis_and_interval_remain_possible():
    envelope, value = setup()
    envelope.update(available_analysis=["transcribe"], retained_seconds=12)
    value["next_move"].update(analysis_tasks=["transcribe"], segment={"start_seconds": 4, "seconds": 3})
    jsonschema.validate(value, request_schema(Decision.model_json_schema(), json.dumps(envelope)))
    for rules in ({"adaptive_analysis": False}, {"adaptive_windows": False}):
        envelope["rules"] = rules
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, request_schema(Decision.model_json_schema(), json.dumps(envelope)))


def test_two_available_refs_do_not_permit_a_single_ref_comparison():
    envelope, value = setup()
    envelope["evidence"].append({"ref": "b"})
    value["findings"] = [{"kind": "divergence", "text": "Difference", "evidence_refs": ["a"]}]
    schema = request_schema(Decision.model_json_schema(), json.dumps(envelope))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)
    value["findings"][0]["evidence_refs"] = ["a", "b"]
    jsonschema.validate(value, schema)


def test_stop_cannot_carry_adaptive_actions():
    envelope, value = setup()
    envelope.update(available_analysis=["transcribe"], retained_seconds=12)
    value["next_move"].update(action="stop", analysis_tasks=["transcribe"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, request_schema(Decision.model_json_schema(), json.dumps(envelope)))
