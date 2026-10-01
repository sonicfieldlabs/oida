"""Situated decisions reuse evidence/providers while retaining no conversations."""

import json
from types import SimpleNamespace
from copy import deepcopy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from oida.situated_listener import situated_router, Settings
from oida.reasoning_workspace import Automatic
from oida.reasoning.contracts import ProviderResult
from test_reasoning_workspace import workspace as workspace


@pytest.fixture
def harness(workspace, monkeypatch):  # noqa: F811
    w = workspace
    requests = []
    output = {"action": "relisten", "kind": "gap"}

    def complete(req):
        requests.append(req)
        items = json.loads(req.user_prompt)["evidence"]
        ref = items[0]["ref"]
        body = dict(
            summary="The listener needs a closer temporal account.",
            findings=[
                dict(
                    kind=output["kind"],
                    text="An unresolved detail in the event.",
                    evidence_refs=[ref],
                )
            ],
            next_move=dict(
                action=output["action"],
                reason="Resolve the gap.",
                evidence_refs=["invented"] if output.get("bad_ref") else [ref],
                source_key=output.get("key"),
                query=output.get("query"),
                analysis_tasks=output.get("analysis_tasks"),
                segment=output.get("segment"),
            ),
        )
        if output.get("invalid"):
            body["extra"] = "not allowed"
        return ProviderResult(
            provider_id="ollama", model_id="test-model", status="ok", parsed=body
        )

    monkeypatch.setattr(w, "validate_selection", lambda req: None)
    monkeypatch.setattr(
        w.reasoning,
        "registry_factory",
        lambda settings: SimpleNamespace(complete=complete),
    )
    app = FastAPI()
    app.include_router(situated_router(w, lambda req: None))
    return TestClient(app), w, requests, output


def payload(**rules):
    c = Settings(enabled=True, provider_id="ollama").model_dump()
    c["context"] = {"sound": True, "memories": False, "web": False}
    c["rules"].update(autonomous=True, **rules)
    return dict(
        request_id="decision-one",
        chain_id="chain-one",
        record_id="akm_one",
        settings=c,
        allowed_actions=["stop", "relisten", "continue", "branch", "generate"],
    )


def test_decision_is_typed_attributed_and_idempotent_without_chat(harness):
    c, w, calls, _ = harness
    p = payload()
    v = c.post("/situated/decide", json=p).json()
    assert v["status"] == "complete" and v["action"] == "relisten", v
    assert v["record_sha256"] and v["provider_id"] == "ollama"
    assert len(calls) == 1 and calls[0].response_schema["additionalProperties"] is False
    assert c.post("/situated/decide", json=p).json() == v and len(calls) == 1
    assert not w.conversations.list() and not w.values("reasoning_job")
    assert (
        c.post("/situated/decide", json={**p, "record_id": "akm_two"}).status_code
        == 409
    )


@pytest.mark.parametrize("change", ["bad_ref", "invalid"])
def test_invalid_model_output_never_becomes_an_action(harness, change):
    c, w, calls, out = harness
    out[change] = True
    v = c.post("/situated/decide", json=payload()).json()
    assert v["status"] == "failed" and v["action"] == "stop"
    assert w.values("situated_decision")[0]["error"]
    assert not w.conversations.list()


def test_conditions_limits_and_unknown_sound_are_checked(harness):
    c, _, _, out = harness
    out["kind"] = "observation"
    v = c.post("/situated/decide", json=payload()).json()
    assert v["action"] == "stop" and "condition" in v["blockers"][0]
    out.update(action="continue", kind="relation", key="a" * 64)
    p = payload()
    p["request_id"] = "next"
    v = c.post("/situated/decide", json=p).json()
    assert v["action"] == "stop" and any("selection" in b for b in v["blockers"])
    p["request_id"] = "bounded"
    p["allowed_actions"] = ["stop"]
    out["action"] = "relisten"
    out["kind"] = "gap"
    assert c.post("/situated/decide", json=p).json()["action"] == "stop"


def test_context_switches_attention_configuration_and_no_wiki(harness, monkeypatch):
    c, w, calls, out = harness
    scopes = []
    monkeypatch.setattr(
        w,
        "retriever",
        lambda event, question, scope, q: (scopes.append(scope) or [], [], ""),
    )
    p = payload()
    p["settings"].update(
        context=dict(sound=False, memories=False, web=True),
        custom_enabled=True,
        system_prompt="Attend to temporal gaps.",
        attention=dict(
            follow_patterns=False,
            seek_contrast=True,
            trace_recurrence=False,
            widen_field=False,
        ),
    )
    v = c.post("/situated/decide", json=p).json()
    assert v["status"] == "complete", v
    assert scopes == [dict(memories=False, wiki=False, web=True)]
    assert all(
        i["kind"] == "event_anchor"
        for i in json.loads(calls[0].user_prompt)["evidence"]
    )
    assert "Attend to temporal gaps." not in calls[0].system_prompt
    assert "Seek contrasts" in calls[0].system_prompt
    assert "Follow patterns" not in calls[0].system_prompt
    assert v["event_type"] == "attention" and v["latency_ms"] >= 0


def test_local_inspection_never_claims_llm_or_autonomy(harness):
    c, w, calls, _ = harness
    p = payload()
    p["settings"]["provider_id"] = "local_structured"
    v = c.post("/situated/decide", json=p).json()
    assert v["action"] == "stop" and v["basis"] == "deterministic; no LLM used"
    assert not calls and not w.conversations.list()


def test_configuration_retires_old_automatic_chat_queue(harness):
    c, w, _, _ = harness
    w.configure(Automatic(enabled=True))
    before = deepcopy(w.reader("akm_one"))
    p = Settings(enabled=True).model_dump()
    assert c.post("/situated/config", json=p).status_code == 200
    assert w.config()["enabled"] is False
    assert c.get("/situated/config").json() == p
    assert w.reader("akm_one") == before
    p["rules"]["max_steps"] = 100
    assert c.post("/situated/config", json=p).status_code == 422
    p["rules"]["max_steps"] = 1
    p["context"]["wiki"] = True
    assert c.post("/situated/config", json=p).status_code == 422


def test_comparisons_and_provider_usage_stay_attributed(harness):
    c, w, calls, out = harness
    p = payload()
    p["comparison_ids"] = ["akm_two"]
    v = c.post("/situated/decide", json=p).json()
    assert v["status"] == "complete", v
    assert v["compared_records"][0]["record_id"] == "akm_two"
    assert v["compared_records"][0]["sha256"]
    assert len({i["event_id"] for i in v["evidence"] if i.get("event_id")}) >= 1
    assert calls[0].max_output_tokens == 2200


def test_source_scope_and_output_cap_are_enforced(harness):
    c, w, calls, out = harness
    p = payload(continuation="always")
    p["settings"]["boundaries"].update(scope="source", max_output_tokens=512)
    p["choices"] = [dict(key="a" * 64, title="A source")]
    out.update(action="continue", key="a" * 64)
    v = c.post("/situated/decide", json=p).json()
    assert v["action"] == "stop" and any("territory" in s for s in v["blockers"])
    assert calls[0].max_output_tokens == 512


@pytest.mark.parametrize("change", ["analysis", "segment", "unavailable"])
def test_adaptive_moves_cannot_override_permissions(harness, change):
    client, workspace, requests, output = harness
    body = payload()
    if change == "analysis":
        output["analysis_tasks"] = ["transcribe"]
        body["available_analysis"] = ["transcribe"]
    elif change == "unavailable":
        output["analysis_tasks"] = ["transcribe"]
        body["settings"]["rules"]["adaptive_analysis"] = True
    else:
        output["segment"] = {"start_seconds": 0, "seconds": 10}
        body["retained_seconds"] = 10
    value = client.post("/situated/decide", json=body).json()
    assert value["status"] == "complete" and value["action"] == "stop", value
    assert value["blockers"]


def test_adaptive_owner_accepts_only_interval_inside_actual_audio(harness, monkeypatch):
    c, w, calls, out = harness
    old_reader = w.reader
    monkeypatch.setattr(
        w, "reader", lambda id: {**old_reader(id), "audio": {"duration_seconds": 10}}
    )
    out.update(
        analysis_tasks=["transcribe"], segment={"start_seconds": 4, "seconds": 3}
    )
    p = payload(adaptive_analysis=True, adaptive_windows=True)
    p.update(available_analysis=["transcribe"], retained_seconds=100)
    v = c.post("/situated/decide", json=p).json()
    assert v["status"] == "complete" and v["action"] == "relisten", v
    assert json.loads(calls[0].user_prompt)["retained_seconds"] == 10
    out["segment"] = {"start_seconds": 9, "seconds": 3}
    p["request_id"] = "beyond-actual-audio"
    assert c.post("/situated/decide", json=p).json()["action"] == "stop"


def test_generated_subject_cannot_claim_convergence(harness):
    client, workspace, calls, output = harness
    output.update(kind="convergence", action="stop")
    response = client.post(
        "/situated/decide", json={**payload(), "subject_generated": True}
    ).json()
    assert response["status"] != "complete"
    assert "Generated-subject" in response["error"]
    assert json.loads(calls[0].user_prompt)["subject_generated"] is True


def test_a_bounded_local_planner_is_sent_whole_items_within_its_context(harness, monkeypatch):
    """Phase 3: the local planner refused every decision over its validated 8192 tokens."""
    import oida.situated_listener as situated

    c, _, calls, _ = harness
    monkeypatch.setitem(
        situated.BOUNDED_CONTEXT, "ollama", {"evidence_chars": 1, "max_choices": 1}
    )
    p = payload()
    p["choices"] = [dict(key="a" * 64, title="One"), dict(key="b" * 64, title="Two")]
    v = c.post("/situated/decide", json=p).json()
    assert v["status"] == "complete", v
    sent = json.loads(calls[0].user_prompt)
    assert [i["kind"] for i in sent["evidence"]] == ["event_anchor"]
    assert len(sent["choices"]) == 1
    bound = v["evidence_bound"]
    assert bound["sent"] == 1 and bound["offered"] > 1 and bound["withheld_refs"]
    assert [i["ref"] for i in v["evidence"]] == [i["ref"] for i in sent["evidence"]]
