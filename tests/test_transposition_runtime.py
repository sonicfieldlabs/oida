import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from test_runtime_attribution import client as client

VALIDATOR_MODULE = os.environ.get("OIDA_TEST_MASA_VALIDATOR_MODULE")

requires_masa_validator = pytest.mark.skipif(
    not VALIDATOR_MODULE,
    reason="set OIDA_TEST_MASA_VALIDATOR_MODULE for real MASA validation",
)


def request(tmp_path, kind, **params):
    path = tmp_path / "source.wav"
    rate = 48000
    t = np.arange(rate) / rate
    sf.write(
        path,
        np.column_stack(
            [0.1 * np.sin(2 * np.pi * 2000 * t), 0.1 * np.sin(2 * np.pi * 2000 * t)]
        ),
        rate,
        subtype="FLOAT",
    )
    return dict(
        operation_id=kind,
        path=str(path),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        permission="granted",
        permission_ref="synthetic-fixture-owner",
        kind=kind,
        lower_hz=1500.0,
        upper_hz=2500.0,
        remember=True,
        **params,
    )


@pytest.mark.parametrize(
    "kind,params,expected_rate,expected_frequency,expected_duration",
    [
        ("filtered_resample", {"target_rate": 16000}, 16000, 2000, 1),
        ("playback_rate", {"rate_ratio": 0.5}, 24000, 1000, 2),
        ("frequency_translation", {"offset_hz": -1000.0}, 48000, 1000, 1),
        ("pitch_shift", {"cents": 1200.0}, 48000, 4000, 0.5),
    ],
)
@requires_masa_validator
def test_real_dsp_recipe_canonical_links_and_retry(
    client,
    tmp_path,
    monkeypatch,
    kind,
    params,
    expected_rate,
    expected_frequency,
    expected_duration,
):
    monkeypatch.setenv("OIDA_MASA_VALIDATOR_MODULE", VALIDATOR_MODULE)
    req = request(tmp_path, kind, **params)
    response = client.post("/sources/transpositions", json=req)
    assert response.status_code == 200, response.text
    value = response.json()
    samples, rate = sf.read(value["path"], always_2d=True)
    assert rate == expected_rate and samples.shape[1] == 2
    assert len(samples) / rate == pytest.approx(expected_duration)
    frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
    peak = frequencies[np.argmax(abs(np.fft.rfft(samples[:, 0])))]
    assert peak == pytest.approx(expected_frequency, abs=4)
    assert (
        hashlib.sha256(open(value["path"], "rb").read()).hexdigest()
        == value["output_sha256"]
    )
    assert client.get("/owner/records/" + value["akousma_id"]).status_code == 200
    assert client.get("/operations/" + kind).json()["akousma_id"] == value["akousma_id"]
    assert client.post("/sources/transpositions", json=req).status_code == 409


@requires_masa_validator
def test_refusal_hash_mismatch_and_cancellation_leave_no_output(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv("OIDA_MASA_VALIDATOR_MODULE", VALIDATOR_MODULE)
    req = request(tmp_path, "frequency_translation", offset_hz=-1000.0)
    assert (
        client.post(
            "/sources/transpositions",
            json={**req, "operation_id": "denied", "permission": "denied"},
        ).status_code
        == 423
    )
    assert (
        client.post(
            "/sources/transpositions",
            json={**req, "operation_id": "hash", "source_sha256": "0" * 64},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/sources/transpositions",
            json={**req, "operation_id": "fold", "offset_hz": -2000.0},
        ).status_code
        == 400
    )
    import oida.transposition as module

    original = module.process
    entered = threading.Event()
    release = threading.Event()

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    with (
        patch("oida.transposition.process", side_effect=slow),
        ThreadPoolExecutor() as pool,
    ):
        future = pool.submit(client.post, "/sources/transpositions", json=req)
        try:
            assert entered.wait(3)
            assert client.post("/operations/frequency_translation/cancel").json()[
                "cancel_requested"
            ]
        finally:
            release.set()
        assert future.result().status_code == 409
    assert not list((tmp_path / "runtime/transpositions").glob("*.wav"))


def test_filter_rejects_out_of_band_energy_without_mixing_channels(tmp_path):
    from oida.transposition import process, TranspositionRequest

    rate = 48000
    t = np.arange(rate) / rate
    samples = np.column_stack(
        [np.sin(2 * np.pi * 2000 * t) + np.sin(2 * np.pi * 8000 * t), np.zeros(rate)]
    )
    req = TranspositionRequest(
        **request(tmp_path, "filtered_resample", target_rate=16000)
    )
    result, rate, _, _ = process(samples, 48000, req)
    spectrum = abs(np.fft.rfft(result[500:-500, 0]))
    frequency = np.fft.rfftfreq(len(result) - 1000, 1 / rate)
    assert np.max(abs(result[:, 1])) == 0
    assert np.max(spectrum[frequency > 3500]) / np.max(spectrum) < 0.005


@pytest.mark.parametrize("rule", ["max window: 0.1 s", "do not retain: raw audio"])
def test_covenant_refuses_derivative_before_dsp(client, tmp_path, rule):
    response = client.put(
        "/covenant",
        json={
            "name": "derivative-policy",
            "text": "# policy\n## rules\n- " + rule + "\n",
            "activate": True,
        },
    )
    assert response.status_code == 200, response.text
    req = request(tmp_path, "filtered_resample", target_rate=16000)
    with patch("oida.transposition.process") as dsp:
        response = client.post("/sources/transpositions", json=req)
    assert response.status_code == 423, response.text
    dsp.assert_not_called()
