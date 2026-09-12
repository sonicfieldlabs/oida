# ruff: noqa: F811
import time
import hashlib
import numpy as np
import soundfile as sf
import pytest
from test_runtime_attribution import client  # noqa: F401


def request(tmp_path, **fields):
    path = tmp_path / "station.wav"
    sf.write(path, np.sin(np.arange(4800) * 0.1), 48000)
    return dict(path=str(path), remember=True, aperture=dict(mode="centaur"), **fields)


@pytest.mark.parametrize("endpoint", ["/gateway/listen", "/gateway/listen-window"])
def test_gateway_preserves_model_and_persists_native_evidence(
    client, tmp_path, endpoint
):
    from akousma import AkousmataStore

    body = request(tmp_path, response_mode="summary")
    if endpoint.endswith("window"):
        body.update(start_seconds=0.0, seconds=0.1)
    response = client.post(endpoint, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["akousma_id"], result
    assert not result.get("shared_error"), result
    event = result["listening_event"]
    assert event["acoustics"]["outcome"] == "measured"
    assert (
        event["acoustics"]["aperture"]["source_sha256"]
        == event["segment"]["data_ref"]["sha256"]
    )
    assert event["pass_provenance"] and all(
        p["provider"] == "stub" for p in event["pass_provenance"]
    )
    with AkousmataStore(tmp_path / "store") as store:
        record = store.get(result["akousma_id"])
        assert "oida.listen" in record["listening"]
        assert "akouo.agent-native" in record["listening"]
        assert (
            record["extensions"]["oida.spectral"]["record_ref"] == result["akousma_id"]
        )
        assert all(
            v["state"] == "omitted"
            for v in record["extensions"]["oida.spectral"]["views"]
        )
    assert not list((tmp_path / "runtime" / "source-capture-temp").glob("aperture-*"))


def test_retained_views_have_one_governed_audio_copy(client, tmp_path):
    from akousma import AkousmataStore
    from akousmata_app.derivatives import read_derivative

    body = request(tmp_path, retain_library_audio=True)
    body["aperture"].update(
        views=["complex_stft"],
        retention=dict(
            derivatives_permitted=True,
            expires_at=time.time() + 60,
            permission_ref="operator:test",
        ),
    )
    response = client.post("/gateway/listen", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["akousma_id"], result
    assert result["library_audio"]["retention"] == "governed-spectral-bundle"
    with AkousmataStore(tmp_path / "store") as store:
        record = store.get(result["akousma_id"])
        view = next(
            v
            for v in record["extensions"]["oida.spectral"]["views"]
            if v["state"] == "retained"
        )
        assert read_derivative(store, record["akousma_id"], view["view_id"])
    assert not list((tmp_path / "audio").rglob("library-captures/*.wav"))


def test_refusals_before_model_or_storage(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "oida.server.report", lambda *a, **kw: pytest.fail("model must not run")
    )
    body = request(tmp_path)
    for change in [
        dict(bands_hz=[[39000.0, 41000.0]]),
        dict(views=["complex_stft"]),
        dict(views=["nsgt"]),
    ]:
        body["aperture"] = dict(mode="centaur", **change)
        response = client.post("/gateway/listen", json=body)
        assert response.status_code == 409, response.text
    assert client.get("/sources/agent-native/capabilities").json()[
        "aperture_contracts"
    ] == ["listeningstack/aperture-request/v1"]


def test_preview_binds_file_limits_without_analysis_or_writes(
    client, tmp_path, monkeypatch
):
    body = request(tmp_path)
    monkeypatch.setattr(
        "oida.agent_native.native_measure",
        lambda *a: pytest.fail("preview must not measure"),
    )
    before = {
        str(p): p.read_bytes() for p in (tmp_path / "runtime").rglob("*") if p.is_file()
    }
    response = client.post(
        "/sources/agent-native/preview",
        json=dict(aperture=body["aperture"], path=body["path"], seconds=10.0),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["limits"][1]["status"] == "supported"
    assert result["limits"][1]["declared"]["sample_rate_hz"] == 48000
    assert result["time_scales"]["admitted_window_s"] == 0.1
    assert all(
        row["status"] == "undetermined"
        for row in [result["limits"][0], *result["limits"][2:]]
    )
    assert before == {
        str(p): p.read_bytes() for p in (tmp_path / "runtime").rglob("*") if p.is_file()
    }
    assert not (tmp_path / "store" / "index.sqlite").exists()


def test_requested_bands_have_separate_measurements_and_resolution_limits(tmp_path):
    from oida.agent_native import NativeRequest, native_measure

    rate = 192000
    t = np.arange(19200) / rate
    path = tmp_path / "bands.wav"
    sf.write(
        path,
        0.25 * np.sin(2 * np.pi * 1000 * t) + 0.25 * np.sin(2 * np.pi * 40000 * t),
        rate,
        subtype="FLOAT",
    )
    result = native_measure(
        NativeRequest(
            operation_id="bands",
            path=str(path),
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            permission_ref="fixture",
            bands_hz=[[39000.0, 41000.0]],
        )
    )
    bands = result["aperture"]["band_measurements"]
    assert bands[-1]["spectral_energy_sum"] > 0.01
    assert all(b["band_hz"] == [39000, 41000] for b in bands)
    narrow = native_measure(
        NativeRequest(
            operation_id="narrow",
            path=str(path),
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            permission_ref="fixture",
            bands_hz=[[1.0, 2.0]],
        )
    )
    assert all(
        b["status"] == "undetermined" for b in narrow["aperture"]["band_measurements"]
    )


def test_retention_policy_and_snapshot_recheck(client, tmp_path, monkeypatch):
    body = request(tmp_path, retain_library_audio=True)
    body["aperture"].update(
        views=["complex_stft"],
        retention=dict(
            derivatives_permitted=True,
            expires_at=time.time() + 60,
            permission_ref="operator:test",
        ),
    )
    monkeypatch.setattr(
        "oida.server.report", lambda *a, **kw: pytest.fail("model must not run")
    )
    for update in [
        dict(privacy_mode="incognito"),
        dict(raw_audio_policy="not_stored"),
        dict(source_type="system_output"),
    ]:
        response = client.post("/gateway/listen", json={**body, **update})
        assert response.status_code in (409, 423), response.text
    body["aperture"]["retention"]["expires_at"] = 1.0
    assert client.post("/gateway/listen", json=body).status_code == 409


def test_radio_aperture_reaches_actual_gateway(setup, radio):
    from test_source_capture import source

    spec = source()
    spec["input"] = radio
    owner = setup([spec])
    response = owner.post(
        "/sources/capture/fixture/listen",
        json=dict(
            acquisition_id="aperture-radio",
            seconds=0.25,
            remember=True,
            aperture=dict(mode="beyond"),
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["receipt"]["status"] == "complete", body
    assert body["receipt"]["akousma_id"], body
    assert body["result"]["listening_event"]["acoustics"]["outcome"] == "measured"
    assert body["receipt"]["raw_audio_deleted"]


from test_source_capture import setup, radio  # noqa: F401,E402


def test_changed_original_does_not_change_the_admitted_model_bytes(
    client, tmp_path, monkeypatch
):
    from oida import agent_native

    original_measure = agent_native.native_measure
    body = request(tmp_path)
    original_hash = hashlib.sha256(
        __import__("pathlib").Path(body["path"]).read_bytes()
    ).hexdigest()

    def measure(req):
        result = original_measure(req)
        sf.write(body["path"], np.zeros(4800), 48000)
        return result

    monkeypatch.setattr(agent_native, "native_measure", measure)
    response = client.post("/gateway/listen", json=body)
    assert response.status_code == 200, response.text
    event = response.json()["listening_event"]
    assert event["segment"]["data_ref"]["sha256"] == original_hash
    assert event["acoustics"]["aperture"]["source_sha256"] == original_hash


def test_expiry_during_model_is_refused_before_publication(
    client, tmp_path, monkeypatch
):
    from oida import server

    body = request(tmp_path, retain_library_audio=True)
    body["aperture"].update(
        views=["complex_stft"],
        retention=dict(
            derivatives_permitted=True,
            expires_at=time.time() + 60,
            permission_ref="operator:test",
        ),
    )
    original_report = server.report

    def report(*args, **kwargs):
        result = original_report(*args, **kwargs)
        expired = time.time() + 120
        monkeypatch.setattr("oida.server.time.time", lambda: expired)
        return result

    monkeypatch.setattr(server, "report", report)
    response = client.post("/gateway/listen", json=body)
    assert response.status_code == 423, response.text
    assert not list((tmp_path / "store").rglob("*.npy"))


def test_explicit_cloud_selection_catalog_and_pre_capture_refusal(client, tmp_path, monkeypatch):
    models = client.get('/listening/options').json()['models']
    row = next(m for m in models if m.get('audio_model', {}).get('model_id') == 'gemini-3.5-flash-lite')
    assert row['catalogued'] and not row['available']
    assert row['inference_tested'] is False and row['reachable'] is None
    monkeypatch.setattr('oida.server.report', lambda *a, **kw: pytest.fail('must not infer'))
    body = request(tmp_path, audio_model=row['audio_model'])
    for endpoint in ['/gateway/listen', '/gateway/listen-window']:
        response = client.post(endpoint, json=body)
        assert response.status_code == 400, response.text
        assert 'pending M2' in response.text
    body['model_id'] = 'thinking'
    assert 'not both' in client.post('/gateway/listen', json=body).text
