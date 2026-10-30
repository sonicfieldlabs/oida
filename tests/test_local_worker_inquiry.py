"""Local inquiry framing for the admitted 4B worker: short, cited, fence-tolerant output.

Real retained accounts give the worker ~5k-token packets. Without list caps the model
looped in uncertainties until its budget ran out, and it wrapped valid JSON in a
Markdown fence that the parser rejected. These checks need no model weights.
"""

import json

import jsonschema
import pytest

from oida.reasoning.contracts import EvidencePacket, ReasoningProfile, strict_output_schema
from oida.reasoning.local.worker import inquiry_schema, prepare_request, task_of, unfenced
from oida.reasoning.prompts import PromptCompiler


def compiled():
    packet = EvidencePacket(primary_event_id="a", question="What is heard?", items=[
        dict(ref="event:a:summary:title", kind="summary", value="Music-like material (model account)."),
        dict(ref="event:a:feature:rmsDbfs", kind="feature", value=-12.0),
    ])
    return PromptCompiler().compile(packet=packet, profile=ReasoningProfile(id="default", name="Default"),
                                    evidence_chars=7000)


def request():
    value = compiled()
    return dict(path="not-loaded", schema=strict_output_schema(value.response_schema), max_tokens=2048,
                messages=[dict(role="system", content=value.system_prompt), dict(role="user", content=value.user_prompt)])


def answer(**changes):
    block = dict(kind="fact", claim_scope="acoustic", text="The RMS level is about -12 dBFS.", evidence_refs=["event:a:feature:rmsDbfs"])
    value = dict(contract="oida/reasoning-response/v0.1", answer_blocks=[block], hypotheses=[], uncertainties=[],
                 suggested_questions=[], requested_action=None)
    value.update(changes)
    return value


def test_inquiry_schema_caps_lists_and_requires_a_cited_offered_ref():
    req = request()
    assert task_of(req["schema"]) == "inquiry"
    schema = inquiry_schema(req["schema"], req["messages"][1]["content"])
    assert [schema["properties"][k]["maxItems"] for k in ("answer_blocks", "hypotheses", "uncertainties")] == [6, 3, 4]
    jsonschema.validate(answer(), schema)
    block = answer()["answer_blocks"][0]
    for invalid in (answer(answer_blocks=[block] * 7),
                    answer(uncertainties=[f"Open issue {i}." for i in range(5)]),
                    answer(answer_blocks=[dict(block, evidence_refs=[])]),
                    answer(answer_blocks=[dict(block, evidence_refs=["event:a:feature:invented"])])):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


def test_inquiry_framing_states_the_caps_the_validator_enforces():
    messages, schema, task = prepare_request(request())
    assert task == "inquiry"
    system = messages[0]["content"]
    assert "at most 6 answer_blocks, 3 hypotheses, 4 uncertainties" in system
    assert json.dumps(schema["properties"]["uncertainties"]["maxItems"]) == "4"


@pytest.mark.parametrize("raw", [
    '{"a": 1}', '  {"a": 1}', '```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '\n```json\n{"a": 1}',
])
def test_one_leading_markdown_fence_is_accepted(raw):
    value, _ = json.JSONDecoder().raw_decode(unfenced(raw))
    assert value == {"a": 1}


@pytest.mark.parametrize("raw", ['Here is the JSON: {"a": 1}', '```python\n{"a": 1}', '<think></think>{"a": 1}'])
def test_other_prose_before_the_object_is_still_refused(raw):
    with pytest.raises(json.JSONDecodeError):
        json.JSONDecoder().raw_decode(unfenced(raw))
