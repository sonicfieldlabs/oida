# ruff: noqa: F811
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from akousma import AkousmataStore
from akousma.record_evolution import next_record_errors

from test_source_capture import setup  # noqa: F401


@pytest.fixture
def payload():
    return dict(
        source_record=json.loads(
            (Path(__file__).parent / "fixtures/sources/observation.json").read_text()
        ),
        observation_ref="urn:uuid:00000000-0000-4000-8000-000000000802",
        producer_id="cosmoaudition-fixture",
        consent="granted",
        consent_ref="fixture-permission",
    )


@pytest.fixture
def validator_module(monkeypatch):
    path = os.environ.get("OIDA_TEST_MASA_VALIDATOR_MODULE")
    if not path:
        pytest.skip("set OIDA_TEST_MASA_VALIDATOR_MODULE for real MASA validation")
    monkeypatch.setenv("OIDA_MASA_VALIDATOR_MODULE", path)
    return path


def test_real_masa_mapping_store_and_preserved_snapshot(
    setup, payload, validator_module, tmp_path
):
    client = setup()
    with patch(
        "oida.server.report", side_effect=AssertionError("observations are not audio")
    ):
        response = client.post(
            "/sources/observations", json={**payload, "remember": True}
        )
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert not next_record_errors(record)
    assert "audio" not in record
    assert (
        record["listening"]["akouo.observation"]["payload"]["source_snapshot"]
        == payload["source_record"]
    )
    assert (
        record["listening"]["akouo.observation"]["payload"]["report"]["features"][0][
            "claim"
        ]["confidence"]
        == "undetermined"
    )
    store = AkousmataStore(tmp_path / "store")
    try:
        assert store.get(response.json()["akousma_id"]) == record
    finally:
        store.close()


def test_observation_validation_is_not_optional(setup, payload):
    client = setup()
    response = client.post("/sources/observations", json=payload)
    assert response.status_code == 503, response.text


@pytest.mark.parametrize("field", ["consent", "semantic"])
def test_observation_refusal_does_not_store(
    setup, payload, validator_module, tmp_path, field
):
    if field == "consent":
        payload["consent"] = "denied"
    else:
        payload["source_record"]["observations"][0]["sourceRef"] = "urn:missing-source"
    client = setup()
    response = client.post("/sources/observations", json={**payload, "remember": True})
    assert response.status_code == 400, response.text
    store = AkousmataStore(tmp_path / "store")
    try:
        assert not store.query(limit=1)
    finally:
        store.close()


def test_source_absence_and_staleness_are_not_upgraded(
    setup, payload, validator_module
):
    obs = payload["source_record"]["observations"][0]
    obs["value"] = {"state": "unknown", "reason": "No fixture value"}
    client = setup()
    response = client.post("/sources/observations", json=payload)
    assert response.status_code == 200, response.text
    mapped = response.json()["record"]["listening"]["akouo.observation"]["payload"]
    assert mapped["source_snapshot"] == payload["source_record"]
    assert mapped["report"]["features"][0]["value"]["status"] == "unknown"
    assert response.json()["akousma_id"] is None


def test_observation_operation_links_retry_and_restart(
    setup, payload, validator_module
):
    client = setup()
    request = {**payload, "remember": True, "operation_id": "observation-one"}
    response = client.post("/sources/observations", json=request)
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    route = record["listening"]["oida.observation-route"]["payload"]
    assert route["request"]["relation"] == {
        "of": "observation",
        "ref": payload["observation_ref"],
    }
    assert (
        record["auditum"]["route_decisions"][0]["producer_decision_ref"]
        == route["decision"]["id"]
    )
    assert not route["claim_permissions"]["inferred_allowed"]
    assert not route["claim_permissions"]["heard_allowed"]
    binding = record["extensions"]["earworm_observation"]
    assert binding["observation_ref"] == payload["observation_ref"]
    assert binding["source_record_ref"] == payload["source_record"]["id"]
    receipt = client.get("/operations/observation-one").json()
    assert (
        receipt["status"] == "complete"
        and receipt["akousma_id"] == record["akousma_id"]
    )
    assert (
        client.get("/owner/records/" + record["akousma_id"]).json()["record"] == record
    )
    restarted = setup()
    assert restarted.get("/operations/observation-one").json() == receipt
    assert restarted.post("/sources/observations", json=request).status_code == 409
    assert not restarted.post("/operations/observation-one/cancel").json()[
        "cancel_requested"
    ]


@pytest.mark.parametrize("action", ["cancel", "policy"])
def test_observation_validation_cancellation_and_policy_recheck(
    setup, payload, validator_module, tmp_path, action
):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from oida import observation_source

    client = setup()
    entered, release = threading.Event(), threading.Event()
    original = observation_source.validator

    def delayed(module):
        validate = original(module)

        def check(record):
            entered.set()
            assert release.wait(5)
            return validate(record)

        return check

    request = {**payload, "remember": True, "operation_id": "pending"}
    with (
        patch("oida.observation_source.validator", side_effect=delayed),
        ThreadPoolExecutor() as pool,
    ):
        future = pool.submit(client.post, "/sources/observations", json=request)
        try:
            assert entered.wait(3)
            if action == "cancel":
                assert client.post("/operations/pending/cancel").json()[
                    "cancel_requested"
                ]
            else:
                saved = client.put(
                    "/covenant",
                    json={
                        "name": "no-memory",
                        "text": "# Policy\n## rules\n- do not retain: memory\n",
                        "activate": True,
                    },
                )
                assert saved.status_code == 200, saved.text
        finally:
            release.set()
        assert future.result().status_code == (409 if action == "cancel" else 423)
    assert client.get("/operations/pending").json()["status"] == (
        "cancelled" if action == "cancel" else "refused"
    )
    store = AkousmataStore(tmp_path / "store")
    try:
        assert not store.query(limit=1)
    finally:
        store.close()
    assert all(
        e["kind"] == "operation" for e in client.get("/owner/journal").json()["events"]
    )
    restarted = setup()
    assert restarted.post("/sources/observations", json=request).status_code == 409


def test_observation_missing_reference_has_durable_refusal(
    setup, payload, validator_module
):
    client = setup()
    response = client.post(
        "/sources/observations",
        json={**payload, "observation_ref": "urn:missing", "operation_id": "missing"},
    )
    assert response.status_code == 400, response.text
    receipt = client.get("/operations/missing").json()
    assert receipt["status"] == "refused"
    assert "akousma_id" not in receipt and "source_record" not in receipt


def test_unknown_unit_does_not_invent_receiving_claim(
    setup, payload, validator_module
):
    payload["source_record"]["observations"][0]["unit"] = {
        "state": "unknown",
        "reason": "No fixture unit evidence",
    }
    response = setup().post("/sources/observations", json=payload)
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert (
        record["listening"]["akouo.observation"]["payload"]["report"]["features"] == []
    )
    assert record["extensions"]["earworm_listening_context"]["claims"] == []
    assert (
        record["listening"]["oida.observation-route"]["payload"]["request"]["relation"][
            "of"
        ]
        == "observation"
    )


@pytest.mark.parametrize("name", ["expired", "fresh", "future", "archive", "fixture"])
def test_owner_built_masa_preserves_source_and_reports_receiving_freshness(setup, validator_module, monkeypatch, name):
    from datetime import datetime
    import oida.observation_source as receiver
    data = json.loads((Path(__file__).parent / "fixtures/cosmo-freshness.json").read_text())
    case = next(c for c in data["cases"] if c["name"] == name)
    class Clock:
        @staticmethod
        def now(_tz):
            return datetime.fromisoformat(data["evaluatedAt"].replace("Z", "+00:00"))
    monkeypatch.setattr(receiver, "datetime", Clock)
    response = setup().post("/sources/observations", json=dict(source_record=case["record"], observation_ref=case["observationRef"], producer_id="cosmo-fixture-builder", consent="granted", consent_ref="test-permission"))
    assert response.status_code == 200, response.text
    received = response.json()
    assert received["effective_freshness"]["current"] is (name == "fresh")
    assert received["effective_freshness"]["execution"] == "not_requested"
    assert received["record"]["listening"]["akouo.observation"]["payload"]["source_snapshot"] == case["record"]
    assert received["record"]["listening"]["oida.source"]["payload"]["effective_freshness"] == received["effective_freshness"]


def test_source_declared_current_expires_at_reception():
    from oida.observation_freshness import evaluate_observation
    data = json.loads((Path(__file__).parent / "fixtures/cosmo-freshness.json").read_text())
    case = next(c for c in data["cases"] if c["name"] == "fresh")
    observation = next(o for o in case["record"]["observations"] if o["id"] == case["observationRef"])
    result = evaluate_observation(case["record"], observation, now="2026-09-26T12:30:00Z")
    assert result["status"] == "expired"
    assert observation["freshness"]["status"] == "current"
