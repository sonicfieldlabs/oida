"""The decision-routing service: idempotent, journaled, provider-dispatched.

Covers the T3 acceptance locally: the rules provider decides without any
model call, proposals that name unoffered actions are blocked, decisions are
idempotent and restart-safe, and unsupported probability fields stay absent.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oida.routing.contracts import ROUTING_SETTINGS_CONTRACT, RoutingSettings
from oida.routing.service import routing_router
from test_reasoning_workspace import workspace as workspace  # noqa: F401,F811


@pytest.fixture
def client(workspace, monkeypatch):  # noqa: F811
    w = workspace
    monkeypatch.setattr(w, "validate_selection", lambda req: None)
    app = FastAPI()
    app.include_router(routing_router(w, lambda req: None))
    return TestClient(app), w


def payload(**overrides):
    settings = RoutingSettings(
        enabled=True, provider_id="rules", allowed_actions=["stop", "generate"]
    ).model_dump(mode="json")
    body = dict(
        request_id="routing-one",
        chain_id="chain-one",
        record_id="akm_one",
        settings=settings,
        allowed_actions=["stop", "generate"],
    )
    body.update(overrides)
    return body


def test_host_wire_shape_accepts_revision_choices_and_eight_comparisons(client):
    c, _ = client
    body = payload(
        choices=[{"key": "a" * 64, "title": "Sound"}], comparison_ids=["akm_one"] * 8
    )
    body["settings"]["revision"] = None
    assert c.post("/routing/decide", json=body).status_code == 200


def test_privacy_refusal_is_not_journaled(client, monkeypatch):
    c, w = client
    monkeypatch.setattr(
        w, "event_policy", lambda event: {**event, "privacy_mode": "incognito"}
    )
    assert c.post("/routing/decide", json=payload()).status_code == 423
    assert w.journal.get("routing_decision", "routing-one") is None


def test_host_workspace_and_source_window_are_bound_before_journaling(client, monkeypatch):
    c, w = client
    monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_ID", "workspace-one")
    monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_GENERATION", "generation-one")
    context = {
        "workspace_id": "workspace-two",
        "owner_generation": "generation-one",
        "source_segment": {"start_seconds": 0.0, "seconds": 10.0},
    }
    assert c.post("/routing/decide", json=payload(context=context)).status_code == 409
    assert w.journal.get("routing_decision", "routing-one") is None
    context["workspace_id"] = "workspace-one"
    context["source_segment"]["start_seconds"] = 12.0
    assert c.post("/routing/decide", json=payload(context=context)).status_code == 409
    assert w.journal.get("routing_decision", "routing-one") is None


def test_comparison_ids_and_missing_records_fail_before_journaling(client):
    c, w = client
    assert c.post("/routing/decide", json=payload(comparison_ids=["bad.id"])).status_code == 422
    response = c.post("/routing/decide", json=payload(comparison_ids=["akm_missing"]))
    assert response.status_code == 404
    assert w.journal.get("routing_decision", "routing-one") is None


def test_jev_protocol_qualification_is_explicit_and_credential_bound(client, monkeypatch):
    from oida.routing.contracts import DecisionProposal
    from oida.routing.providers import context_digest

    c, w = client

    class Secrets:
        key = "synthetic-test-key"

        def get(self, provider, name):
            return self.key

    secrets = Secrets()
    monkeypatch.setattr(w.reasoning, "secret_store", secrets)
    assert not next(p for p in c.get("/routing/options").json()["providers"] if p["id"] == "typesafe")["available"]

    class FakeJev:
        def __init__(self, *args, **kwargs):
            pass

        def decide(self, context, questions, settings):
            selected = context["candidates"][0]
            return DecisionProposal(
                context_sha256=context_digest(context),
                action=selected["action"], candidate_id=selected["id"],
                actual_model="jev-1.13.0",
            )

    monkeypatch.setattr("oida.routing.service.TypesafeDecisionProvider", FakeJev)
    result = c.post("/routing/qualify/typesafe")
    assert result.status_code == 200 and len(result.json()["completed_cases"]) == 3
    assert next(p for p in c.get("/routing/options").json()["providers"] if p["id"] == "typesafe")["available"]

    class FailedJev(FakeJev):
        def decide(self, context, questions, settings):
            raise TimeoutError("synthetic transport failure")

    monkeypatch.setattr("oida.routing.service.TypesafeDecisionProvider", FailedJev)
    assert c.post("/routing/qualify/typesafe").status_code == 409
    assert w.journal.get("routing_qualification", "typesafe")["status"] == "failed"
    assert not next(p for p in c.get("/routing/options").json()["providers"] if p["id"] == "typesafe")["available"]
    secrets.key = "rotated-test-key"
    assert not next(p for p in c.get("/routing/options").json()["providers"] if p["id"] == "typesafe")["available"]


def test_admission_binds_candidate_action_arguments_and_context():
    from oida.routing.contracts import DecisionProposal
    from oida.routing.providers import context_digest
    from oida.routing.service import admission_blockers

    context = {
        "allowed_actions": ["stop", "generate"],
        "candidates": [{"id": "stop", "action": "stop", "arguments": {}}],
    }
    good = DecisionProposal(
        context_sha256=context_digest(context), action="stop", candidate_id="stop"
    )
    assert admission_blockers(context, good) == []
    for patch in (
        {"action": "generate"},
        {"candidate_id": None},
        {"arguments": {"query": "invented"}},
        {"context_sha256": "0" * 64},
    ):
        assert admission_blockers(context, good.model_copy(update=patch))


def test_saved_action_restrictions_are_enforced(client):
    c, _ = client
    body = payload()
    body["settings"]["allowed_actions"] = ["stop"]
    assert c.post("/routing/decide", json=body).json()["action"] == "stop"


def test_zero_generation_budget_stops(client):
    c, _ = client
    body = payload()
    body["settings"]["limits"]["max_generations"] = 0
    assert c.post("/routing/decide", json=body).json()["action"] == "stop"


def test_external_fallback_cannot_bypass_disclosure(client, monkeypatch):
    from oida.routing.contracts import DecisionProposal
    from oida.routing.providers import context_digest

    class Local:
        locality = "local"
        kind = "rules"

        def decide(self, context, **kwargs):
            return DecisionProposal(
                context_sha256=context_digest(context), action="stop", abstained=True
            )

    class External:
        locality = "external"

        def decide(self, *args, **kwargs):
            pytest.fail("External fallback invoked without disclosure")

    monkeypatch.setattr(
        "oida.routing.service.build_decision_registry",
        lambda w: {"rules": Local(), "external": External()},
    )
    c, _ = client
    body = payload()
    body["settings"].update(fallback="provider", fallback_provider_id="external")
    assert c.post("/routing/decide", json=body).status_code == 409


def test_local_admin_required_for_mutations(workspace):
    from fastapi import HTTPException

    def refuse(request):
        raise HTTPException(403, "Local admin only")

    app = FastAPI()
    app.include_router(routing_router(workspace, refuse))
    c = TestClient(app)
    assert c.post("/routing/config", json={}).status_code == 403
    assert c.post("/routing/decide", json=payload()).status_code == 403
    assert c.post("/routing/archive/visibility", json={"ids": ["akm_one"]}).status_code == 403


def test_archive_visibility_batches_current_policy(client, monkeypatch):
    c, _ = client
    calls = []

    def view(workspace, identifier):
        calls.append(identifier)
        return {"view": {"state": "withheld" if identifier == "akm_hidden" else "available"}}

    monkeypatch.setattr("oida.routing.archive.archive_view", view)
    response = c.post("/routing/archive/visibility", json={"ids": ["akm_one", "akm_hidden"]})
    assert response.status_code == 200
    assert response.json() == {"states": {"akm_one": "available", "akm_hidden": "withheld"}}
    assert calls == ["akm_one", "akm_hidden"]
    assert c.post("/routing/archive/visibility", json={"ids": ["akm_one", "akm_one"]}).status_code == 400
    assert c.post("/routing/archive/visibility", json={"ids": ["bad.id"]}).status_code == 400
    assert c.post("/routing/archive/visibility", json={"ids": ["akm_one"], "unused": True}).status_code == 422


def test_archive_digests_give_state_and_the_digest_germ_compares_without_content(client, monkeypatch):
    import hashlib
    import json as j

    c, _ = client
    record = {"akousma_id": "akm_one", "summary": "Réverbération", "value": 1.5}

    def state(workspace, identifier):
        if identifier == "akm_hidden":
            return {"state": "withheld", "record": None}
        return {"state": "available", "record": record}

    monkeypatch.setattr("oida.routing.archive.archive_state", state)
    response = c.post("/routing/archive/digests", json={"ids": ["akm_one", "akm_hidden"]})
    assert response.status_code == 200
    views = response.json()["views"]
    expected = hashlib.sha256(
        j.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    assert views["akm_one"] == {"state": "available", "record_sha256": expected}
    assert views["akm_hidden"] == {"state": "withheld", "record_sha256": None}
    assert "Réverbération" not in response.text, "no record content travels"
    assert c.post("/routing/archive/digests", json={"ids": []}).status_code == 422
    assert c.post("/routing/archive/digests", json={"ids": ["a", "a"]}).status_code == 400


def test_production_workspace_mounts_routing(workspace):
    from oida.reasoning_workspace import workspace_router

    app = FastAPI()
    app.include_router(
        workspace_router(workspace, lambda: {}, lambda p: {}, lambda r: None)
    )
    assert "/reasoning/workspace/routing/decide" in app.openapi()["paths"]


def test_rules_provider_decides_without_any_model_call(client):
    c, _ = client
    value = c.post("/routing/decide", json=payload()).json()
    assert value["status"] == "complete" and value["action"] == "generate", value
    assert value["provider_id"] == "rules"
    assert value["basis"] == "deterministic rubric; no model used"
    # Unsupported probability fields stay absent, never fabricated.
    assert value.get("probabilities") is None
    assert value["decision"]["next_move"]["action"] == "generate"
    assert value["proposal"]["candidate_id"] == "generate"


def test_stop_only_choice_stops_and_records_the_blocker_free_proposal(client):
    c, _ = client
    value = c.post("/routing/decide", json=payload(allowed_actions=["stop"])).json()
    assert value["action"] == "stop" and value["status"] == "complete"


def test_decision_is_idempotent_and_conflicting_requests_are_refused(client):
    c, _ = client
    first = c.post("/routing/decide", json=payload()).json()
    again = c.post("/routing/decide", json=payload()).json()
    assert first["id"] == again["id"] == "routing-one"
    conflicting = c.post("/routing/decide", json=payload(allowed_actions=["stop"]))
    assert conflicting.status_code == 409


def test_disabled_routing_is_refused_and_never_executes(client):
    c, _ = client
    disabled = payload(
        settings=RoutingSettings(enabled=False, provider_id="rules").model_dump(
            mode="json"
        )
    )
    response = c.post("/routing/decide", json=disabled)
    assert response.status_code == 409
    assert "Decision routing is off" in response.json()["detail"]


def test_unknown_provider_is_refused(client):
    c, _ = client
    unknown = payload(
        settings=RoutingSettings(
            enabled=True, provider_id="nonexistent", allowed_actions=["stop"]
        ).model_dump(mode="json")
    )
    response = c.post("/routing/decide", json=unknown)
    assert response.status_code == 400
    assert "Unknown decision provider" in response.json()["detail"]


def test_restarted_running_decisions_become_interrupted(client):
    c, w = client
    prior = dict(
        id="routing-old",
        chain_id="chain-old",
        record_id="akm_one",
        status="running",
        contract="oida/routing-decision/v1",
    )
    w.journal.save("routing_decision", "routing-old", prior)
    app = FastAPI()
    app.include_router(routing_router(w, lambda req: None))
    records = [r for r in w.values("routing_decision", 10) if r["id"] == "routing-old"]
    assert records[0]["status"] == "interrupted"
    assert "restarted" in records[0]["error"]


def test_config_round_trip_with_revision_compare_and_swap(client):
    c, _ = client
    stored = c.get("/routing/config").json()
    assert (
        stored["contract"] == ROUTING_SETTINGS_CONTRACT and stored["enabled"] is False
    )
    saved = c.post(
        "/routing/config", json={"enabled": True, "provider_id": "rules"}
    ).json()
    assert saved["enabled"] is True
    assert c.get("/routing/config").json()["enabled"] is True


def test_options_list_rules_first_and_report_provider_availability(client, monkeypatch):
    c, w = client
    from oida.reasoning.contracts import (
        ProviderDescriptor,
        ProviderKind,
        ProviderLocality,
    )
    from oida.reasoning.registry import ProviderRegistry

    class DownProvider:
        provider_id = "ollama"
        kind = "model"

        def probe(self):
            return ProviderDescriptor(
                id="ollama",
                name="Ollama",
                kind=ProviderKind.OLLAMA,
                locality=ProviderLocality.LOCAL,
                enabled=False,
                available=False,
                detail="HTTP connection failed",
            )

        def list_models(self):
            return []

    registry = ProviderRegistry()
    registry.register(DownProvider(), enabled=False)
    monkeypatch.setattr(w.reasoning, "registry_factory", lambda settings: registry)
    providers = c.get("/routing/options").json()["providers"]
    assert providers[0]["id"] == "rules" and providers[0]["available"] is True
    by_id = {p["id"]: p for p in providers}
    assert (
        by_id["ollama"]["available"] is False
    )  # visible with its reason, never invoked


def test_host_prepared_candidates_are_honored(client):
    c, _ = client
    context = {
        "contract": "telar/decision-routing/context/v1",
        "candidates": [
            {
                "id": "relisten-1",
                "action": "relisten",
                "description": "Deep route pass",
                "arguments": {},
            },
            {
                "id": "stop",
                "action": "stop",
                "description": "Nothing useful remains",
                "arguments": {},
            },
        ],
    }
    body = payload(
        context=context,
        allowed_actions=["stop", "relisten"],
        settings=RoutingSettings(
            enabled=True,
            provider_id="rules",
            allowed_actions=["stop", "relisten"],
        ).model_dump(mode="json"),
    )
    value = c.post("/routing/decide", json=body).json()
    assert value["action"] == "relisten"
    assert value["proposal"]["candidate_id"] == "relisten-1"


def test_history_records_decisions(client):
    c, _ = client
    c.post("/routing/decide", json=payload())
    history = c.get("/routing/history").json()
    assert any(d["id"] == "routing-one" for d in history["decisions"])


def test_receipt_retains_exact_prepared_context(client):
    from oida.routing.providers import context_digest

    c, _ = client
    value = c.post("/routing/decide", json=payload()).json()
    assert value["proposal"]["context_sha256"] == context_digest(
        value["prepared_context"]
    )
    assert value["attempts"][0]["provider_id"] == "rules"


def test_missing_credential_is_refused_as_unavailable_not_unknown(client):
    c, w = client
    body = payload(
        settings={
            "contract": "oida/routing/settings/v1",
            "enabled": True,
            "provider_id": "typesafe",
            "allow_external_text": True,
            "limits": {"deadline_seconds": 30},
        }
    )
    response = c.post("/routing/decide", json=body)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "typesafe" in detail and "not available" in detail
    assert "Unknown" not in detail


def test_host_context_carries_the_evidence_bundle_identity(client):
    c, _ = client
    context = {
        "contract": "telar/decision-routing/context/v1",
        "evidence_ref": "bundle-e661678913bdc2c3",
        "evidence_sha256": "e" * 64,
        "source_sha256": "a" * 64,
        "candidates": [
            {
                "id": "stop",
                "action": "stop",
                "description": "Nothing remains",
                "arguments": {},
            }
        ],
    }
    body = payload(
        context=context,
        allowed_actions=["stop"],
        settings={
            "contract": "oida/routing/settings/v1",
            "enabled": True,
            "provider_id": "rules",
            "allowed_actions": ["stop"],
            "limits": {"deadline_seconds": 30},
        },
    )
    value = c.post("/routing/decide", json=body).json()
    assert value["action"] == "stop"
    assert value["evidence_ref"] == "bundle-e661678913bdc2c3"
    assert value["evidence_sha256"] == "e" * 64
