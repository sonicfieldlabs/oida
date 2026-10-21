"""Exact text family admission and framing; deterministic structural evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from oida.reasoning.contracts import ModelDescriptor, strict_output_schema
from oida.reasoning.local.worker import framed_prompt, inscription_schema, task_of
from oida.textual_budget import execution_budget
from oida.textual_sounding import RESULT_SCHEMA, SYSTEM


def packet():
    return dict(path="not-loaded", messages=[dict(role="system", content=SYSTEM),
                 dict(role="user", content='{"output_kind":"work","material":[{"representation_ref":"fixture:source"}]}')],
                schema=strict_output_schema(RESULT_SCHEMA), max_tokens=2048)


def test_only_exact_inscription_schema_is_an_admitted_family():
    assert task_of(packet()["schema"]) == "inscription"
    changed = deepcopy(packet()["schema"])
    changed["properties"]["claims"]["items"]["properties"]["category"]["enum"].append("heard")
    with pytest.raises(ValueError, match="Unsupported"):
        task_of(changed)


def test_inscription_schema_matches_no_structural_family():
    # task_of checks the structural families first; this keeps that order equivalent.
    schema = packet()["schema"]
    assert "Move" not in (schema.get("$defs") or {})
    assert set(schema.get("properties") or {}) != {"candidate_id"}
    assert not ("answer_blocks" in schema["properties"] and "AnswerBlock" in (schema.get("$defs") or {}))


def test_structural_families_need_no_oida_in_the_model_environment():
    """The gateway runs the worker with the model's own Python, without Oída installed."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    from oida.reasoning.contracts import EvidencePacket, ReasoningProfile
    from oida.reasoning.prompts import PromptCompiler
    from oida.routing.providers import CandidateAnswer
    from oida.situated_listener import Decision

    packet_ = EvidencePacket(primary_event_id="a", question="What is heard?",
                             items=[dict(ref="event:a:summary", kind="summary", value="fixture")])
    compiled = PromptCompiler().compile(packet=packet_, profile=ReasoningProfile(id="default", name="Default"),
                                        evidence_chars=7000)
    schemas = dict(planning=strict_output_schema(Decision.model_json_schema()),
                   routing=strict_output_schema(CandidateAnswer.model_json_schema()),
                   inquiry=strict_output_schema(compiled.response_schema),
                   unsupported=packet()["schema"])
    worker = Path(__file__).resolve().parents[1] / "oida/reasoning/local/worker.py"
    code = (
        "import json,runpy,sys\n"
        "class Block:\n"
        " def find_spec(self,name,path=None,target=None):\n"
        "  if name=='oida' or name.startswith('oida.'): raise ImportError('Oida is not installed here')\n"
        "sys.meta_path.insert(0,Block())\n"
        "task_of=runpy.run_path(sys.argv[1])['task_of']\n"
        "out={}\n"
        "for name,schema in json.loads(sys.stdin.read()).items():\n"
        " try: out[name]=task_of(schema)\n"
        " except ValueError as exc: out[name]='refused: '+str(exc)\n"
        "print(json.dumps(out))\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(worker)], input=json.dumps(schemas),
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == dict(planning="planning", routing="routing", inquiry="inquiry",
                                             unsupported="refused: Unsupported local reasoning task")


class Tokenizer:
    def apply_chat_template(self, messages, **options):
        assert options == dict(tokenize=False, add_generation_prompt=True, enable_thinking=False)
        return "<system>" + messages[0]["content"] + "<user>" + messages[1]["content"] + "<assistant>"

    def encode(self, text):
        return list(text.encode())


def test_count_includes_worker_schema_instructions_and_chat_framing():
    request = packet()
    prompt, count, schema = framed_prompt(Tokenizer(), request)
    assert schema["properties"]["claims"]["maxItems"] == 0
    assert request["schema"]["properties"]["claims"].get("maxItems") is None
    assert count == len(prompt.encode())
    assert count > len((SYSTEM + request["messages"][1]["content"]).encode())
    assert "A work has an empty claims array" in prompt
    assert '"measurement_ref"' in prompt
    assert request["messages"][0]["content"] == SYSTEM  # no mutation


def test_output_reserve_closes_the_known_window_without_truncation():
    request = packet()
    request["max_tokens"] = 8000
    with pytest.raises(ValueError, match="output reserve"):
        framed_prompt(Tokenizer(), request)


@pytest.mark.parametrize("kind", ["work", "account"])
def test_schema_enforces_completed_kind_and_explicit_nonresponse(kind):
    import json
    from jsonschema import ValidationError, validate
    schema = inscription_schema(strict_output_schema(RESULT_SCHEMA), json.dumps(dict(
        output_kind=kind, material=[dict(representation_ref="fixture:source")], measurements=[])))
    claim = dict(category="undetermined", statement="A limit remains.", representation_ref="fixture:source",
                 evidence_refs=["fixture:source"], measurement_ref="")
    completed = dict(status="completed", text="finished", reason="", claims=[] if kind == "work" else [claim])
    validate(completed, schema)
    with pytest.raises(ValidationError):
        validate(dict(completed, reason="An explanation is not a finished work"), schema)
    with pytest.raises(ValidationError):
        validate(dict(completed, claims=[claim] if kind == "work" else []), schema)
    validate(dict(status="no_response", text="", claims=[], reason="The agent declines."), schema)
    with pytest.raises(ValidationError):
        validate(dict(completed, claims=[dict(claim, representation_ref="other")]), schema)
    with pytest.raises(ValidationError):
        validate(dict(completed, claims=[dict(claim, measurement_ref="invented")]), schema)


class Registry:
    def __init__(self, *, family=True, response_ref="qualified:fixture"):
        self.calls = []
        self.model = ModelDescriptor(id="m", provider_id="local_ecology", name="fixture", locality="local",
                 context_window=8192, capabilities=["text"], metadata=dict(
                     admitted_tasks=["inscription"] if family else ["planning"], token_counter_ref="qualified:fixture"))
        def transport(method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return SimpleNamespace(data=dict(prompt_tokens=321, context_window=8192,
                        output_token_reserve=2048, method_ref=response_ref, model_id="m"))
        from oida.reasoning.providers.openai_compatible import OpenAICompatibleProvider
        self.adapter = OpenAICompatibleProvider(provider_id="local_ecology", base_url="http://127.0.0.1:19999/v1",
                            enabled=True, api_key_getter=lambda: "fixture", transport=SimpleNamespace(request=transport))
    def list_models(self, _provider):
        return [self.model]
    def get(self, _provider):
        return self.adapter


def test_selected_counter_sends_the_actual_provider_payload_and_schema():
    registry = Registry()
    budget = execution_budget(registry, "local_ecology", "m")
    assert budget["token_counter"](SYSTEM, "material", "m") == 321
    method, url, kwargs = registry.calls[0]
    assert method == "POST" and url.endswith("/v1/token-budget")
    assert kwargs["payload"]["response_format"]["json_schema"]["schema"] == strict_output_schema(RESULT_SCHEMA)
    assert kwargs["payload"]["max_tokens"] == 2048
    with pytest.raises(ValueError, match="another model"):
        budget["token_counter"](SYSTEM, "material", "other")


def test_unadmitted_or_different_framing_is_never_guessed():
    with pytest.raises(ValueError, match="no inscription"):
        execution_budget(Registry(family=False), "local_ecology", "m")
    assert execution_budget(Registry(), "some-provider", "m") == {}
    budget = execution_budget(Registry(response_ref="another:framing"), "local_ecology", "m")
    with pytest.raises(ValueError, match="does not identify"):
        budget["token_counter"](SYSTEM, "material", "m")
