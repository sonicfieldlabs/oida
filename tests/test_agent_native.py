# ruff: noqa: F811
import hashlib
import time
import os
import numpy as np
import soundfile as sf
import pytest
from test_runtime_attribution import client  # noqa: F401

OPTIONAL_TEST_CONFIG = os.environ.get('OIDA_TEST_SPECTRAL_WORKERS_CONFIG')


def body(tmp_path, memory):
    path = tmp_path / "source.wav"
    sf.write(path, np.sin(np.arange(4800) * 0.1), 48000)
    return dict(
        operation_id="native-" + memory,
        path=str(path),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        permission_ref="owner:test",
        memory=memory,
        derivatives_permitted=memory == "record_audio",
        expires_at=time.time() + 60,
    )


@pytest.mark.parametrize("memory", ["none", "record", "record_audio"])
def test_native_owner_route_retention(client, tmp_path, memory):
    from akousma import AkousmataStore

    result = client.post("/sources/agent-native", json=body(tmp_path, memory))
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["outcome"] == "measured"
    assert "payloads" not in result
    with AkousmataStore(tmp_path / "store") as store:
        record = store.get(result["record"]["akousma_id"])
        assert (record is None) == (memory == "none")
        if memory == "record":
            assert all(
                v["state"] == "omitted"
                for v in record["extensions"]["oida.spectral"]["views"]
            )
            assert not list((tmp_path / "store" / "objects").glob("*.npy"))
        if memory == "record_audio":
            from akousmata_app.derivatives import read_derivative

            view = next(
                v
                for v in record["extensions"]["oida.spectral"]["views"]
                if v["state"] == "retained"
            )
            assert read_derivative(store, record["akousma_id"], view["view_id"])
            assert store.resolve_uri(record["audio"]["uri"]).exists()


def test_aperture_and_permission_refusals(client, tmp_path):
    request = body(tmp_path, "record_audio")
    request["derivatives_permitted"] = False
    assert client.post("/sources/agent-native", json=request).status_code == 400
    request = body(tmp_path, "none")
    request["bands_hz"] = [[39000.0, 41000.0]]
    response = client.post("/sources/agent-native", json=request)
    assert response.status_code == 200
    assert response.json()["outcome"] == "refused"


def test_gateway_native_policy_and_validation(client, tmp_path):
    options = body(tmp_path, "record")
    request = dict(
        path=options["path"], route_preset="agent-native", native_options=options
    )
    assert client.post("/gateway/listen", json=request).status_code == 400
    request["remember"] = True
    response = client.post("/gateway/listen", json=request)
    assert response.status_code == 200, response.text
    from oida.reasoning_context import retained_event

    event = retained_event(response.json()["record"])
    assert event["routes"][0]["structured"]["claim_summary"]["undetermined"]
    assert response.json()["next_action"]["endpoint"] == "/situated/decide"
    request["native_options"] = {"memory": "record"}
    assert client.post("/gateway/listen", json=request).status_code == 400


def test_optional_views_follow_atomic_retention(client, tmp_path, monkeypatch):
    config = OPTIONAL_TEST_CONFIG
    if not config:
        pytest.skip('Optional environment not provisioned')
    monkeypatch.setenv('OIDA_SPECTRAL_WORKERS_CONFIG', config)
    request = body(tmp_path, 'record_audio')
    request['workers'] = ['nsgt', 'kymatio']
    response = client.post('/sources/agent-native', json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    selected = result['record']['extensions']['oida.spectral']['views'][-2:]
    assert all(v['state'] == 'retained' for v in selected), selected
    artifacts = {e['artifact_ref'] for e in result['record']['extensions']['akouo.agent-native']['evidence_objects']}
    assert all(v['object_ref'] in artifacts for v in selected)


def test_optional_workers_off_and_expiry(client, tmp_path, monkeypatch):
    monkeypatch.delenv("OIDA_SPECTRAL_WORKERS_CONFIG", raising=False)
    assert (
        client.get("/sources/agent-native/capabilities").json()["workers"]["nsgt"][
            "status"
        ]
        == "unavailable"
    )
    request = body(tmp_path, "record_audio")
    request["workers"] = ["nsgt", "kymatio"]
    result = client.post("/sources/agent-native", json=request).json()
    assert all(v["state"] == "omitted" for v in result["bundle"]["views"][-2:])
    from akousma import AkousmataStore
    from akousmata_app.derivatives import reconcile_derivatives
    from akousmata_app.records import resolve_audio_path

    with AkousmataStore(tmp_path / "store") as store:
        record = store.get(result["akousma_id"])
        path = resolve_audio_path(store, record)
        assert path.exists()
        store.conn.execute("UPDATE akousmata_derivative_grants SET expires_at=0")
        store.conn.commit()
        assert resolve_audio_path(store, record) is None
        reconcile_derivatives(store)
        assert not path.exists()
