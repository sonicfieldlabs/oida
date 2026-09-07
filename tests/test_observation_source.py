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
