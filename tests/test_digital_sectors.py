import hashlib
import numpy as np
import soundfile as sf
import pytest
from test_observation_source import setup as setup, validator_module as validator_module


def request(tmp_path, frequency=1000.0, duration=1.0):
    rate = 48000
    t = np.arange(round(rate * duration)) / rate
    path = tmp_path / "synthetic.wav"
    sf.write(path, 0.2 * np.sin(2 * np.pi * frequency * t), rate, subtype="FLOAT")
    return dict(
        operation_id="band",
        path=str(path),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        lower_hz=frequency * 0.8,
        upper_hz=frequency * 1.2,
        end_s=duration,
        permission="granted",
        permission_ref="fixture-owner",
        evidence_class="synthetic_declared",
        remember=True,
    )


def test_real_band_energy_sector_report_and_owner_links(
    setup, validator_module, tmp_path
):
    client = setup()
    req = request(tmp_path)
    response = client.post("/sources/spectrum", json=req)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["report"]["features"][0]["value"]["value"] == pytest.approx(
        0.02, rel=0.001
    )
    assert out["report"]["features"][0]["category"] == "measured"
    assert "physical" in out["report"]["limitations"][0]
    assert out["text"] and out["report"]["recipients"][0]["type"] == "human"
    from akousma.agent_sectors import agent_sector_view

    record = out["record"]
    sector = record["extensions"]["earworm_agent_sector"]["entries"][0]
    assert agent_sector_view(record, sector["sector_id"])["measurements"]
    assert client.get("/owner/records/" + out["akousma_id"]).json()["record"] == record
    assert client.post("/sources/spectrum", json=req).status_code == 409


def test_low_frequency_resolution_and_hash_refusal(setup, validator_module, tmp_path):
    client = setup()
    req = request(tmp_path, frequency=2.0, duration=1.0)
    assert client.post("/sources/spectrum", json=req).status_code == 400
    req = request(tmp_path, frequency=2.0, duration=10.0)
    req["operation_id"] = "resolved"
    response = client.post("/sources/spectrum", json=req)
    assert response.status_code == 200, response.text
    assert response.json()["report"]["features"][0]["value"]["value"] == pytest.approx(
        0.02, rel=0.001
    )
    req["operation_id"] = "wrong"
    req["source_sha256"] = "0" * 64
    assert client.post("/sources/spectrum", json=req).status_code == 400


def test_retained_report_has_independent_recipients_and_actual_text(
    setup, validator_module, tmp_path
):
    client = setup()
    original = client.post("/sources/spectrum", json=request(tmp_path)).json()["record"]
    identifier = original["akousma_id"]
    response = client.post(
        "/owner/records/" + identifier + "/agent-report",
        json={
            "operation_id": "review",
            "remember": True,
            "recipients": [{"id": "agent:reviewer", "type": "agent"}],
        },
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["report"]["recipients"] == [{"id": "agent:reviewer", "type": "agent"}]
    assert value["report"]["features"][0]["category"] == "undetermined"
    assert (
        value["text"]
        and value["record"]["listening"]["oida.report-text"]["payload"]["text"]
        == value["text"]
    )
    assert (
        value["record"]["listening"]["oida.retained-source"]["payload"]["record"]
        == original
    )
    assert client.get("/owner/records/" + identifier).json()["record"] == original
    assert (
        client.post(
            "/owner/records/" + identifier + "/agent-report",
            json={"operation_id": "review"},
        ).status_code
        == 409
    )


def test_cancelled_measurement_has_no_canonical_result(
    setup, validator_module, tmp_path
):
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch
    import threading
    import oida.digital_sectors as module

    client = setup()
    req = request(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = module.measure

    def delayed(req):
        result = original(req)
        entered.set()
        assert release.wait(5)
        return result

    with (
        patch("oida.digital_sectors.measure", side_effect=delayed),
        ThreadPoolExecutor() as pool,
    ):
        future = pool.submit(client.post, "/sources/spectrum", json=req)
        try:
            assert entered.wait(3)
            assert client.post("/operations/band/cancel").json()["cancel_requested"]
        finally:
            release.set()
        assert future.result().status_code == 409
    assert client.get("/operations/band").json()["status"] == "cancelled"
    assert all(
        e["kind"] == "operation" for e in client.get("/owner/journal").json()["events"]
    )


def test_observation_report_retains_attribution_and_source(
    setup, validator_module, tmp_path
):
    import json
    from pathlib import Path

    client = setup()
    source = json.loads(
        (Path(__file__).parent / "fixtures/sources/observation.json").read_text()
    )
    result = client.post(
        "/sources/observations",
        json=dict(
            source_record=source,
            observation_ref=source["observations"][0]["id"],
            producer_id="fixture",
            consent="granted",
            consent_ref="fixture-owner",
            remember=True,
        ),
    )
    assert result.status_code == 200, result.text
    original = result.json()["record"]
    response = client.post(
        "/owner/records/" + original["akousma_id"] + "/agent-report",
        json=dict(operation_id="observation-review", remember=True),
    )
    assert response.status_code == 200, response.text
    output = response.json()
    assert (
        output["record"]["listening"]["oida.retained-source"]["payload"]["record"]
        == original
    )
    assert all(f["category"] == "undetermined" for f in output["report"]["features"])


def test_model_account_report_uses_actual_retained_receipts(setup, tmp_path):
    client = setup()
    req = request(tmp_path)
    source = client.post(
        "/gateway/listen", json={"path": req["path"], "remember": True}
    )
    assert source.status_code == 200, source.text
    identifier = source.json()["akousma_id"]
    response = client.post(
        "/owner/records/" + identifier + "/agent-report",
        json={"operation_id": "model-review"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["report"]["features"][0]["name"] == "model_pass_attribution"
    assert response.json()["report"]["features"][0]["category"] == "undetermined"
