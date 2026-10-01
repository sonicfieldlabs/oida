from __future__ import annotations

import json
import io
import os
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from oida.server import create_app
from oida.source_capture import (
    AcquisitionReceipts,
    CaptureSource,
    capture_audio,
    capture_command,
    CaptureInterrupted,
)


@pytest.mark.parametrize("identifier", ["../outside", "/absolute", "valid\n", "", "x" * 81])
def test_receipts_reject_unsafe_identifier_before_journal_write(tmp_path, identifier):
    receipts = AcquisitionReceipts(tmp_path / "receipts")
    with pytest.raises(ValueError, match="Invalid acquisition identifier"):
        receipts.save({"id": identifier, "status": "queued"})
    assert receipts.journal.get("acquisition", identifier) is None
    assert not list(receipts.root.iterdir())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for key in list(os.environ):
        if (
            key.startswith(("OIDA_", "HMM_", "AEAR_"))
            and key != "OIDA_TEST_MASA_VALIDATOR_MODULE"
        ):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")

    def app(sources=()):
        manifest = tmp_path / "sources.json"
        manifest.write_text(
            json.dumps(dict(contract="oida/capture-sources/v1", sources=list(sources)))
        )
        monkeypatch.setenv("OIDA_CAPTURE_SOURCES", str(manifest))
        return TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")

    return app


def source(**kwargs):
    return {
        **dict(
            id="fixture",
            adapter="radio",
            input="http://127.0.0.1:1/source.wav",
            sample_rate=96000,
            channels=1,
            max_seconds=1.0,
            producer_id="fixture",
            consent="granted",
            consent_ref="fixture-permission",
            apparatus={"status": "unknown"},
        ),
        **kwargs,
    }


@pytest.fixture
def radio(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    sf.write(tmp_path / "source.wav", np.zeros(96000, dtype=np.float32), 96000)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/source.wav"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_ffmpeg_radio_owner_listen_and_durable_receipt(setup, radio, tmp_path):
    spec = source()
    spec["input"] = radio
    client = setup([spec])
    response = client.post(
        "/sources/capture/fixture/listen",
        json=dict(acquisition_id="one", seconds=0.25, remember=True),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    receipt = body["receipt"]
    assert receipt["status"] == "complete", body
    assert receipt["akousma_id"]
    assert (
        receipt["source_admission"]["sampled_representation"]["sample_rate_hz"] == 96000
    )
    assert receipt["source_admission"]["source_time_basis"] == "local-acquisition-start"
    assert (
        receipt["source_descriptor"]["source_time_basis"] == "local-acquisition-start"
    )
    assert receipt["raw_audio_deleted"]
    assert not Path(
        body["result"]["listening_event"]["segment"]["data_ref"]["uri"]
    ).exists()
    assert client.get("/sources/acquisitions/one").json() == receipt
    # Finished acquisition IDs cannot accidentally duplicate remembered output.
    assert (
        client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="one", seconds=0.25),
        ).status_code
        == 400
    )
    restarted = setup([spec])
    assert restarted.get("/sources/acquisitions/one").json() == receipt


def test_rate_mismatch_failed_then_corrected_retry(setup, radio):
    spec = source()
    spec.update(input=radio, sample_rate=48000)
    client = setup([spec])
    body = client.post(
        "/sources/capture/fixture/listen",
        json=dict(acquisition_id="wrong", seconds=0.25),
    ).json()
    assert body["receipt"]["status"] == "failed" and body["result"] is None
    spec["sample_rate"] = 96000
    client = setup([spec])
    assert (
        client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="retry", seconds=0.25),
        ).json()["receipt"]["status"]
        == "complete"
    )


def test_cancel_before_listening_and_cleanup(setup):
    client = setup([source()])
    entered = threading.Event()
    outputs = []

    def blocked(source, seconds, path, cancel):
        entered.set()
        assert cancel.wait(3)
        raise CaptureInterrupted("cancelled")

    with (
        patch("oida.source_api.capture_audio", side_effect=blocked),
        patch("oida.server.report", side_effect=AssertionError("must not infer")),
    ):
        t = threading.Thread(
            target=lambda: outputs.append(
                client.post(
                    "/sources/capture/fixture/listen",
                    json=dict(acquisition_id="cancel", seconds=0.25),
                )
            )
        )
        t.start()
        assert entered.wait(3)
        assert client.post("/sources/acquisitions/cancel/cancel").json()[
            "cancel_requested"
        ]
        t.join(timeout=5)
        assert not t.is_alive()
    assert outputs[0].json()["receipt"]["status"] == "cancelled"
    assert client.post("/sources/acquisitions/cancel/cancel").json()["cancel_requested"]


def test_restart_marks_nonterminal_without_replay(tmp_path):
    manager = AcquisitionReceipts(tmp_path / "receipts")
    item, _ = manager.begin("interrupted", "fixture")
    restarted = AcquisitionReceipts(tmp_path / "receipts")
    assert (
        json.loads((restarted.root / "interrupted.json").read_text())["status"]
        == "interrupted"
    )
    assert not restarted.active


def test_covenant_preflight_and_denied_consent_never_capture(setup):
    spec = source()
    spec["consent"] = "denied"
    client = setup([spec])
    with patch(
        "oida.source_api.capture_audio", side_effect=AssertionError("must not capture")
    ):
        assert (
            client.post(
                "/sources/capture/fixture/listen",
                json=dict(acquisition_id="no", seconds=0.25),
            ).status_code
            == 400
        )
    # A separate integration fixture exercises the actual covenant store elsewhere.
    from oida.source_api import source_router
    from fastapi import FastAPI, HTTPException

    def refuse(*args):
        raise HTTPException(423, "fixture covenant")

    app = FastAPI()
    app.include_router(
        source_router(
            Path(os.environ["OIDA_DATA_DIR"]) / "other", lambda _: None, refuse
        )
    )
    c = TestClient(app)
    assert (
        c.post(
            "/sources/observations",
            json=dict(
                source_record={},
                observation_ref="x",
                producer_id="x",
                consent="granted",
                consent_ref="x",
            ),
        ).status_code
        == 423
    )


def test_device_command_never_resamples_and_pre_cancel_does_not_spawn(tmp_path):
    spec = source()
    spec.update(adapter="avfoundation", input=":0")
    config = CaptureSource.model_validate(spec)
    with patch("oida.source_capture.shutil.which", return_value="/fake/ffmpeg"):
        command = capture_command(config, 0.25, tmp_path / "capture.wav")
    assert (
        "-ar" not in command
        and "-ac" not in command
        and command[command.index("-f") + 1] == "avfoundation"
    )
    cancel = threading.Event()
    cancel.set()
    with patch(
        "oida.source_capture.subprocess.Popen",
        side_effect=AssertionError("must not spawn"),
    ):
        with pytest.raises(CaptureInterrupted):
            capture_audio(config, 0.25, tmp_path / "capture.wav", cancel)


@pytest.mark.parametrize("sample_rate,channels", [(44100, 1), (48000, 2)])
def test_public_radio_guard_preserves_native_format(tmp_path, sample_rate, channels):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    payload = io.BytesIO()
    sf.write(
        payload,
        np.zeros((sample_rate // 4, channels), dtype=np.float32),
        sample_rate,
        format="WAV",
    )
    configured = CaptureSource.model_validate(
        source(
            input="https://radio.example/audio",
            sample_rate=192000,
            channels=2,
            network_policy="public_radio",
            retention="temp_only",
        )
    )
    output = tmp_path / "capture.wav"
    fetched = SimpleNamespace(content_type="audio/wav", data=payload.getvalue())
    with patch("oida.public_fetch.PublicFetcher.get", return_value=fetched) as get:
        capture_audio(configured, 0.1, output, threading.Event())
    info = sf.info(output)
    assert (info.samplerate, info.channels) == (sample_rate, channels)
    assert get.call_args.kwargs["limit"] == 32 * 1024**2
    assert get.call_args.kwargs["stream_seconds"] == 2.1


def test_public_radio_rejects_playlist_before_decoder(tmp_path):
    configured = CaptureSource.model_validate(
        source(
            input="https://radio.example/playlist",
            network_policy="public_radio",
            retention="temp_only",
        )
    )
    fetched = SimpleNamespace(content_type="audio/mpeg", data=b"#EXTM3U\n")
    with patch("oida.public_fetch.PublicFetcher.get", return_value=fetched):
        with pytest.raises(ValueError, match="playlists"):
            capture_audio(configured, 0.1, tmp_path / "capture.wav", threading.Event())


def test_capture_worker_stops_child_when_owner_pipe_closes(tmp_path):
    import subprocess
    import sys
    import time

    pid_file = tmp_path / "child.pid"
    child = [
        sys.executable,
        "-c",
        "import os,time,pathlib;pathlib.Path("
        + repr(str(pid_file))
        + ").write_text(str(os.getpid()));time.sleep(30)",
    ]
    worker = subprocess.Popen(
        [sys.executable, "-m", "oida.capture_worker"], stdin=subprocess.PIPE
    )
    try:
        worker.stdin.write((json.dumps(child) + "\n").encode())
        worker.stdin.flush()
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        worker.stdin.close()
        assert worker.wait(timeout=5) != 0
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)


def test_restart_cleans_only_owned_capture_temp(setup, tmp_path):
    owned = tmp_path / "runtime/source-capture-temp/orphan"
    owned.mkdir(parents=True)
    (owned / "capture.wav").write_bytes(b"partial fixture")
    outside = tmp_path / "keep.wav"
    outside.write_bytes(b"keep")
    (owned.parent / "linked").symlink_to(outside)
    setup()
    assert not list(owned.parent.iterdir())
    assert outside.read_bytes() == b"keep"
