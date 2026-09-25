"""The local planner worker admits three task families and narrows each to its offer."""

import json

import pytest

from oida.reasoning.contracts import reasoning_response_schema, strict_output_schema
from oida.reasoning.local import worker
from oida.routing.providers import CandidateAnswer
from oida.situated_listener import Decision


def test_task_families_are_recognised_from_the_schema_actually_sent():
    assert worker.task_of(strict_output_schema(Decision.model_json_schema())) == "planning"
    assert worker.task_of(strict_output_schema(CandidateAnswer.model_json_schema())) == "routing"
    assert worker.task_of(strict_output_schema(reasoning_response_schema())) == "inquiry"
    with pytest.raises(ValueError, match="Unsupported local reasoning task"):
        worker.task_of({"type": "object", "properties": {"text": {"type": "string"}}})


def test_routing_answer_is_narrowed_to_offered_candidates():
    base = strict_output_schema(CandidateAnswer.model_json_schema())
    content = json.dumps({"candidates": [{"id": "stop"}, {"id": "relisten"}, {"id": "stop"}]})
    schema = worker.routing_schema(base, content)
    assert schema["properties"]["candidate_id"] == {"type": "string", "enum": ["stop", "relisten"]}
    with pytest.raises(ValueError, match="Routing requires offered candidates"):
        worker.routing_schema(base, json.dumps({"candidates": []}))


def test_inquiry_citations_are_narrowed_to_packet_refs():
    base = strict_output_schema(reasoning_response_schema())
    content = 'EVIDENCE_PACKET_UNTRUSTED_JSON (9 bytes):\n{"items":[{"kind":"summary","ref":"event:a:summary"}]}'
    schema = worker.inquiry_schema(base, content)
    for name in ("AnswerBlock", "ReasoningHypothesis"):
        assert schema["$defs"][name]["properties"]["evidence_refs"]["items"]["enum"] == ["event:a:summary"]
    assert base["$defs"]["AnswerBlock"]["properties"]["evidence_refs"] != schema["$defs"]["AnswerBlock"]["properties"]["evidence_refs"]
