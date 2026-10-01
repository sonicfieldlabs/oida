"""The TypeSafe (Jev) adapter and transport, exercised with synthetic fixtures.

No credential and no network: every HTTP behavior is exercised through an
injected ``send``. Covers §7.3 — successful choices, abstention, malformed
answers, invalid probabilities, auth failures, request rejection, retry with
Retry-After, overload exhaustion, timeouts and stale candidates — plus the
service-level external-text gate and explicit fallback.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oida.routing.providers import context_digest
from oida.routing.service import routing_router
from oida.routing.typesafe import (
    PINNED_MODEL,
    TypesafeDecisionProvider,
    external_state,
)
from oida.routing.contracts import DecisionQuestion, DecisionProposal, RoutingSettings
from test_reasoning_workspace import workspace as workspace  # noqa: F811,F401


def context():
    return {
        "goal": "Decide the next bounded listening action",
        "candidates": [
            {
                "id": "generate",
                "action": "generate",
                "description": "Create a response",
                "arguments": {},
            },
            {
                "id": "stop",
                "action": "stop",
                "description": "Nothing useful remains",
                "arguments": {},
            },
        ],
        "allowed_actions": ["stop", "generate"],
        "evidence": [
            {
                "ref": "event-1:anchor",
                "kind": "event_anchor",
                "value": "Intermittent impacts",
            },
            {"ref": "event-1:claim:1", "kind": "claim", "value": "Low mechanical hum"},
        ],
        "remaining": {"generations": 1},
        "subject_generated": False,
    }


def question():
    return DecisionQuestion(
        id="next_action",
        kind="choice",
        instructions="Select the useful next bounded action.",
        criteria={
            "generate": "Create a response",
            "stop": "Stop",
            "abstain": "Nothing useful",
        },
    )


def question_view():
    return {
        "next_action": {
            "type": "choice",
            "instructions": {"question": "q", "state_note": "note"},
            "criteria": {"generate": "d", "stop": "d", "abstain": "d"},
        }
    }


def provider(send=None, api_key="staged-key"):
    return TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: api_key,
        transport=lambda *a, **k: transport_call(a, k),
    )


def transport_call(args, kwargs):
    """Default transport unless a test injects its own."""
    from oida.routing.typesafe import _post_json

    return _post_json(*args, **kwargs)


def settings(**overrides):
    return RoutingSettings(enabled=True, provider_id="typesafe", **overrides)


def systemone_response(payload):
    return lambda base, path, key, body, **kw: payload


# --- state minimization (§7.3) -----------------------------------------------


def test_external_state_minimizes_and_never_carries_paths_or_arguments():
    context = {
        "goal": "goal",
        "candidates": [
            {
                "id": "generate",
                "action": "generate",
                "description": "d",
                "arguments": {"local_path": "/private/x"},
            }
        ],
        "allowed_actions": ["stop", "generate"],
        "evidence": [
            {"ref": f"ref-{i}", "kind": "claim", "value": "word " * 300}
            for i in range(20)
        ],
        "remaining": {"generations": 1},
        "subject_generated": False,
    }
    state = external_state(context)
    assert len(state["evidence"]) == 12 and len(state["candidates"]) == 1
    assert all(len(item["text"]) <= 500 for item in state["evidence"])
    rendered = json.dumps(state)
    assert "/private/x" not in rendered and "arguments" not in rendered


# --- decision mapping ---------------------------------------------------------


def test_successful_choice_becomes_a_typed_proposal():
    body_seen = {}

    def send(request, timeout):
        body_seen["body"] = json.loads(request.data)
        return 200, {}, json.dumps(success_response()).encode()

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(context(), [question()], settings())
    assert proposal.action == "generate" and proposal.candidate_id == "generate"
    assert (
        proposal.actual_model == "jev-1.13.0"
        and proposal.requested_model == PINNED_MODEL
    )
    assert proposal.probabilities == {"generate": 0.72, "stop": 0.18, "abstain": 0.1}
    assert proposal.usage_tokens == 316
    sent = body_seen["body"]
    assert (
        sent["model"] == PINNED_MODEL
        and sent["state"]["candidates"][0]["id"] == "generate"
    )


def test_jev_refuses_unpinned_request_or_changed_actual_version():
    calls = []
    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *args, **kwargs: (calls.append(args), success_response())[1],
    )
    denied = p.decide(context(), [question()], settings(model_id="jev-latest"))
    assert denied.abstained and not calls
    wrong = success_response()
    wrong["model"] = "jev-future"
    p._transport = lambda *args, **kwargs: wrong
    denied = p.decide(context(), [question()], settings(model_id=PINNED_MODEL))
    assert denied.abstained and "different model" in denied.error


def _run(send, args, kwargs):
    from oida.routing.typesafe import _post_json

    base_url, path, key, body = args
    return _post_json(base_url, path, key, body, send=send, **kwargs)


def success_response():
    return dict(
        model="jev-1.13.0",
        answers={
            "next_action": dict(
                type="choice",
                choice="generate",
                probabilities={"generate": 0.72, "stop": 0.18, "abstain": 0.1},
                confidence=0.8,
            )
        },
        usage={"input_tokens": 296, "output_tokens": 20},
    )


def test_abstention_choice_becomes_an_abstained_proposal():
    def send(request, timeout):
        body = success_response()
        body["answers"]["next_action"].update(choice="abstain")
        body["answers"]["next_action"]["probabilities"] = {
            "generate": 0.0,
            "stop": 0.0,
            "abstain": 1.0,
        }
        return 200, {}, json.dumps(body).encode()

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(context(), [question()], settings())
    assert proposal.abstained and proposal.action == "abstain"


# --- response validation ------------------------------------------------------


def _broken(mutation):
    def send(request, timeout):
        body = success_response()
        mutation(body)
        return 200, {}, json.dumps(body).encode()

    return TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )


def test_missing_answers_are_refused():
    proposal = _broken(lambda b: b["answers"].pop("next_action")).decide(
        context(), [question()], settings()
    )
    assert proposal.abstained and "missing answers" in proposal.error


def test_extra_answers_are_refused():
    def add_extra(body):
        body["answers"]["surprise"] = {"type": "choice", "choice": "stop"}

    proposal = _broken(add_extra).decide(context(), [question()], settings())
    assert proposal.abstained and "unexpected" in proposal.error


def test_distribution_keys_must_match_the_criteria():
    def mutate(body):
        body["answers"]["next_action"]["probabilities"] = {
            "generate": 1.0,
            "surprise": 0.0,
        }
        body["answers"]["next_action"]["choice"] = "generate"

    proposal = _broken(mutate).decide(context(), [question()], settings())
    assert proposal.abstained and "offered options" in proposal.error


def test_probabilities_must_sum_to_one():
    def mutate(body):
        body["answers"]["next_action"]["probabilities"] = {
            "generate": 0.9,
            "stop": 0.18,
            "abstain": 0.0,
        }

    proposal = _broken(mutate).decide(context(), [question()], settings())
    assert proposal.abstained and "sum" in proposal.error


def test_probabilities_outside_the_unit_interval_are_refused():
    def mutate(body):
        body["answers"]["next_action"]["probabilities"] = {
            "generate": 1.4,
            "stop": -0.4,
            "abstain": 0.0,
        }

    proposal = _broken(mutate).decide(context(), [question()], settings())
    assert proposal.abstained and "outside" in proposal.error


def test_choice_outside_the_options_is_refused():
    def mutate(body):
        body["answers"]["next_action"]["choice"] = "relisten"

    proposal = _broken(mutate).decide(context(), [question()], settings())
    assert proposal.abstained and "offered option" in proposal.error


def test_missing_actual_model_is_refused():
    def mutate(body):
        body.pop("model")

    proposal = _broken(mutate).decide(context(), [question()], settings())
    assert proposal.abstained and "model" in proposal.error


def test_non_typed_answer_is_refused():
    def mutate(body):
        body["answers"]["next_action"]["type"] = "noul"

    proposal = _broken(mutate).decide(context(), [question()], settings())
    assert proposal.abstained and "typed choice" in proposal.error


def test_unoffered_candidate_names_are_refused_even_from_a_valid_answer():
    # The answer is a well-formed choice inside the criteria that were sent,
    # but names a candidate the host never offered — the stale-candidate case.
    ghost_questions = {
        "next_action": {
            "type": "choice",
            "instructions": "q",
            "criteria": {"generate": "d", "stop": "d", "abstain": "d", "ghost": "d"},
        }
    }
    response = dict(
        model="jev-1.13.0",
        answers={
            "next_action": dict(
                type="choice",
                choice="ghost",
                probabilities={
                    "generate": 0.0,
                    "stop": 0.0,
                    "abstain": 0.0,
                    "ghost": 1.0,
                },
                confidence=1.0,
            )
        },
        usage={"input_tokens": 10, "output_tokens": 2},
    )
    p = TypesafeDecisionProvider("https://api.typesafe.ai", lambda: "key")
    proposal = p._proposal_from_response(
        context(), "0" * 64, "1" * 64, ghost_questions, response, PINNED_MODEL
    )
    assert proposal.abstained and "unoffered" in proposal.error


# --- transport behavior (§7.3) -------------------------------------------------


def test_missing_credential_is_terminal():
    p = TypesafeDecisionProvider("https://api.typesafe.ai", lambda: None)
    proposal = p.decide(context(), [question()], settings())
    assert proposal.abstained and "authentication failed" in proposal.error


def test_401_is_terminal_and_attributed():
    calls = []

    def send(request, timeout):
        calls.append(1)
        return 401, {}, json.dumps({"detail": "invalid api key"}).encode()

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(context(), [question()], settings())
    assert proposal.abstained and "authentication failed" in proposal.error
    assert len(calls) == 1  # 401 is terminal, never retried


def test_422_is_terminal_and_reported():
    def send(request, timeout):
        return 422, {}, json.dumps({"detail": "malformed question"}).encode()

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(context(), [question()], settings())
    assert proposal.abstained and "failed validation" in proposal.error
    assert "malformed question" not in proposal.error  # remote bodies are untrusted


def test_429_honors_retry_after_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    attempts = []

    def send(request, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            return (
                429,
                {"Retry-After": "1"},
                json.dumps({"detail": "slow down"}).encode(),
            )
        return 200, {}, json.dumps(success_response()).encode()

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(
        context(), [question()], settings(limits=RoutingLimits(deadline_seconds=60))
    )
    assert proposal.action == "generate" and len(attempts) == 3


from oida.routing.contracts import RoutingLimits  # noqa: E402


def test_529_exhausting_the_budget_overloads_to_a_typed_abstention(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def send(request, timeout):
        return 529, {}, json.dumps({"detail": "overloaded"}).encode()

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(context(), [question()], settings())
    assert proposal.abstained and "overloaded" in proposal.error


def test_connection_failure_abstains():
    def send(request, timeout):
        raise OSError("connection refused")

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(context(), [question()], settings())
    assert proposal.abstained and "transport failed" in proposal.error


def test_deadline_expiry_abstains(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def send(request, timeout):
        return 429, {}, json.dumps({"detail": "rate limited"}).encode()

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(
        context(), [question()], settings(limits=RoutingLimits(deadline_seconds=5))
    )
    assert proposal.abstained and "rate limited" in proposal.error


def test_unreadable_response_abstains():
    def send(request, timeout):
        return 200, {}, b"not json at all"

    p = TypesafeDecisionProvider(
        "https://api.typesafe.ai",
        lambda: "key",
        transport=lambda *a, **k: _run(send, a, k),
    )
    proposal = p.decide(context(), [question()], settings())
    assert proposal.abstained and "valid JSON" in proposal.error


# --- service-level: disclosure gate and explicit fallback ----------------------


@pytest.fixture
def client(workspace, monkeypatch):  # noqa: F811
    w = workspace
    monkeypatch.setattr(w, "validate_selection", lambda req: None)
    app = FastAPI()
    app.include_router(routing_router(w, lambda req: None))
    return TestClient(app), w


class _FailingExternalProvider:
    provider_id = "typesafe"
    kind = "model"
    locality = "external"

    def decide(self, context, questions=None, settings=None):
        return __import__(
            "oida.routing.contracts", fromlist=["DecisionProposal"]
        ).DecisionProposal(
            contract="telar/decision-routing/proposal/v1",
            context_sha256=context_digest(context),
            action="abstain",
            abstained=True,
            error="primary transport failed",
        )


class _RulesFallbackProvider:
    provider_id = "rules"
    kind = "rules"
    locality = "local"

    def decide(self, context, questions=None, settings=None):

        return DecisionProposal(
            contract="telar/decision-routing/proposal/v1",
            context_sha256=context_digest(context),
            action="stop",
            candidate_id="stop",
            arguments={"reason": "Rules fallback after the external router failed"},
            question_version="telar/questions/v1",
        )


def typesafe_request(**overrides):
    settings = dict(
        contract="oida/routing/settings/v1",
        enabled=True,
        provider_id="typesafe",
        allowed_actions=["stop", "generate"],
        allow_external_text=True,
        fallback="provider",
        fallback_provider_id="rules",
        limits={"deadline_seconds": 30},
    )
    body = dict(
        request_id="ts-one",
        chain_id="chain-one",
        record_id="akm_one",
        settings=settings,
        allowed_actions=["stop", "generate"],
    )
    body.update(overrides)
    return body


def test_external_text_gate_refuses_without_its_own_permission(client, monkeypatch):
    http, w = client
    monkeypatch.setattr(
        "oida.routing.service.build_decision_registry",
        lambda workspace: {"typesafe": _FailingExternalProvider()},
    )
    body = typesafe_request()
    body["settings"]["allow_external_text"] = False
    response = http.post("/routing/decide", json=body)
    assert response.status_code == 409
    assert "own permission" in response.json()["detail"]


def test_provider_fallback_runs_when_the_primary_abstains(client, monkeypatch):
    http, w = client
    monkeypatch.setattr(
        "oida.routing.service.build_decision_registry",
        lambda workspace: {
            "typesafe": _FailingExternalProvider(),
            "rules": _RulesFallbackProvider(),
        },
    )
    value = http.post("/routing/decide", json=typesafe_request()).json()
    assert value["action"] == "stop", value
    assert value["fallback"] == {"from": "typesafe", "to": "rules"}
    assert value["provider_id"] == "rules"  # attribute the actual successful attempt
    assert value["basis"]  # the record names what actually answered


def test_fallback_disabled_means_stop_not_silent_swap(client, monkeypatch):
    http, w = client
    monkeypatch.setattr(
        "oida.routing.service.build_decision_registry",
        lambda workspace: {"typesafe": _FailingExternalProvider()},
    )
    body = typesafe_request()
    body["settings"]["fallback"] = "stop"
    body["settings"].pop("fallback_provider_id")
    value = http.post("/routing/decide", json=body).json()
    assert value["fallback"] is None and value["action"] == "stop"
    assert value["status"] == "complete"  # abstention is visible, not hidden
