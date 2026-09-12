from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import os
import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from oida.engine_base import selected_model, use_listening_model
from oida.engine_mps import MpsMossEngine
from oida.server import create_app


def test_request_models_isolate_threads_and_restore_after_error():
    engine = object.__new__(MpsMossEngine)
    engine.config = SimpleNamespace(
        instruct_model="default-instruct", thinking_model="default-thinking"
    )
    engine._model_overrides = {"music": "assigned-music"}
    barrier = Barrier(2)

    def run(model):
        try:
            with use_listening_model(model):
                barrier.wait(timeout=5)
                assert {
                    engine.model_id_for_kind(k)
                    for k in ["instruct", "thinking", "music", "transcription"]
                } == {model}
                raise ValueError("simulated failed pass")
        except ValueError:
            assert selected_model() is None
        return engine.model_id_for_kind("music")

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert (
            list(pool.map(run, ["request-one", "request-two"]))
            == ["assigned-music"] * 2
        )
    assert engine.model_id_for_kind("instruct") == "default-instruct"
    assert engine._model_overrides == {"music": "assigned-music"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("OIDA_", "HMM_", "AEAR_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "memory"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    manifest = tmp_path / "capture.json"
    manifest.write_text(
        json.dumps(
            dict(
                contract="oida/capture-sources/v1",
                sources=[
                    dict(
                        id="test-radio",
                        adapter="radio",
                        input="http://127.0.0.1:1/test.wav",
                        sample_rate=16000,
                        channels=1,
                        max_seconds=10.0,
                        producer_id="test",
                        consent="granted",
                        consent_ref="test-only",
                    )
                ],
            )
        )
    )
    monkeypatch.setenv("OIDA_CAPTURE_SOURCES", str(manifest))
    return TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")


def test_invalid_selections_fail_before_audio_access(client):
    for selection, text in [
        (dict(model_id="invented"), "model"),
        (dict(listening_mode="invented"), "modality"),
    ]:
        response = client.post(
            "/gateway/listen", json=dict(path="/does-not-exist.wav", **selection)
        )
        assert response.status_code == 400
        assert text in response.json()["detail"]


def test_modes_use_existing_harness_and_stub_is_not_selectable(client, tmp_path):
    options = client.get("/listening/options").json()
    assert not any(m["available"] for m in options["models"])
    assert "musical-aesthetic-listening" in options["controls"]["modes"]
    audio = tmp_path / "tone.wav"
    sf.write(
        audio,
        np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000).astype("float32") * 0.05,
        16000,
    )
    response = client.post(
        "/gateway/listen",
        json=dict(
            path=str(audio),
            route_preset="signal",
            listening_mode="musical-aesthetic-listening",
            remember=False,
        ),
    )
    assert response.status_code == 200, response.text
    event = response.json()["listening_event"]
    # Check the canonical harness result, not a UI-only label.
    assert event["aggregate"]["detailed_summary"].startswith(
        "musical-aesthetic-listening selected from /tech"
    )
    assert [p["model"] for p in event["pass_provenance"]] == ["dsp-only"]


def test_capture_refuses_unknown_selection_before_acquiring(client):
    for endpoint in ["listen", "jobs"]:
        response = client.post(
            "/sources/capture/test-radio/" + endpoint,
            json=dict(
                acquisition_id="invalid-" + endpoint, seconds=1.0, model_id="unknown"
            ),
        )
        assert response.status_code == 400
        assert "model" in response.json()["detail"]
        assert (
            client.get("/sources/acquisitions/invalid-" + endpoint).status_code == 404
        )


@pytest.mark.parametrize("extension,subtype", [("wav", "FLOAT"), ("flac", "PCM_24")])
def test_native_capture_decodes_float_wav_and_compressed_audio(
    tmp_path, extension, subtype
):
    from oida.dsp import load_audio
    from oida.live import write_capture

    samples = np.column_stack(
        [np.linspace(-0.2, 0.2, 48000), np.linspace(0.1, -0.1, 48000)]
    ).astype("float32")
    source = tmp_path / ("native." + extension)
    sf.write(source, samples, 48000, subtype=subtype)
    audio = load_audio(source)
    assert audio.sample_rate == 48000 and audio.channels == 2
    np.testing.assert_allclose(audio.samples, samples, atol=1e-6)
    assert load_audio(source, max_seconds=0.25).samples.shape == (12000, 2)
    target = write_capture(
        [dict(path=str(source))], tmp_path / "buffer.wav", max_seconds=0.25
    )
    clipped = load_audio(target)
    assert clipped.sample_rate == 48000 and clipped.channels == 2
    np.testing.assert_allclose(clipped.samples, samples[-12000:], atol=1 / 32768)


def test_summary_response_does_not_include_history_and_saves_auditum(
    client, tmp_path, monkeypatch
):
    from oida.background import BackgroundRuntime

    monkeypatch.setattr(
        BackgroundRuntime, "status", lambda self: {"padding": "x" * (3 * 1024 * 1024)}
    )
    path = tmp_path / "tone.wav"
    sf.write(path, np.zeros(16000, dtype="float32"), 16000)
    full = client.post(
        "/gateway/listen", json=dict(path=str(path), route_preset="signal")
    )
    assert full.status_code == 200 and len(full.content) > 2 * 1024 * 1024
    response = client.post(
        "/gateway/listen",
        json=dict(
            path=str(path),
            route_preset="signal",
            response_mode="summary",
            remember=True,
        ),
    )
    assert response.status_code == 200, response.text
    assert len(response.content) < 128 * 1024
    result = response.json()
    assert result["memory_status"] == "saved" and result["akousma_id"]
    assert "background" not in result and "earworm" not in result
    stored = client.get("/owner/records/" + result["akousma_id"]).json()["record"]
    assert stored["auditum"]["listenings"]
    assert (
        stored["listening"]["oida.listen"]["payload"]["event_id"]
        == result["listening_event"]["id"]
    )


def test_file_window_retains_real_duration_offset_and_source_hash(client, tmp_path):
    from oida.dsp import sha256_file

    path = tmp_path / "long.wav"
    sf.write(path, np.sin(np.arange(25 * 16000) * 0.1).astype("float32") * 0.05, 16000)
    original_hash = sha256_file(path)
    response = client.post(
        "/gateway/listen-window",
        json=dict(
            path=str(path),
            start_seconds=12.0,
            seconds=10.0,
            route_preset="signal",
            response_mode="summary",
            remember=True,
            operation_id="window-one",
        ),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["memory_status"] == "saved"
    event = result["listening_event"]
    assert event["segment"]["duration_ms"] == 10000
    window = event["segment"]["metadata"]["file_window"]
    assert window == dict(
        start_seconds=12.0,
        duration_seconds=10.0,
        source_duration_seconds=25.0,
        source_sha256=original_hash,
    )
    assert sha256_file(path) == original_hash
    assert not __import__("pathlib").Path(event["segment"]["data_ref"]["uri"]).exists()
    stored = client.get("/owner/records/" + result["akousma_id"]).json()["record"]
    assert stored["listening"]["oida.listen"]["payload"]["file_window"] == window
    assert stored["audio"]["duration_seconds"] == 10.0
    assert "uri" not in stored["audio"]
    assert (
        client.post(
            "/gateway/listen-window",
            json=dict(
                path=str(path), start_seconds=30.0, seconds=10.0, route_preset="signal"
            ),
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/gateway/listen-window", json=dict(path=str(path), seconds=61.0)
        ).status_code
        == 422
    )
