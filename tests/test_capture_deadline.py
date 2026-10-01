"""A caller's deadline binds a radio capture and its listening (Phase 3, 24 September 2026).

Phase 2b sent Telar's cancel window to file listenings only; a radio listen ran past it.
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oida.operation_control import checkpoint
from oida.source_api import source_router
from oida.source_capture import CaptureInterrupted


@pytest.fixture
def station(tmp_path, monkeypatch):
    manifest = tmp_path / "sources.json"
    manifest.write_text(
        json.dumps(
            dict(
                contract="oida/capture-sources/v1",
                sources=[
                    dict(
                        id="fixture",
                        adapter="radio",
                        input="http://127.0.0.1:1/unrequested",
                        sample_rate=16000,
                        channels=1,
                        max_seconds=5.0,
                        producer_id="fixture",
                        consent="granted",
                        consent_ref="fixture",
                    )
                ],
            )
        )
    )
    monkeypatch.setenv("OIDA_CAPTURE_SOURCES", str(manifest))
    state = {"captures": 0, "listen_seconds": 0.0, "listened": 0}

    def listen(payload):
        state["listened"] += 1
        stop = time.time() + state["listen_seconds"]
        while time.time() < stop:
            checkpoint()  # the engine's own checkpoints, as a MOSS pass would reach them
            time.sleep(0.02)
        return dict(
            listening_event=dict(
                id="event",
                source=dict(details=dict(source_admission=payload["source_admission"])),
            ),
            akousma_id=None,
        )

    def capture(source, seconds, path, cancel):
        state["captures"] += 1
        sf.write(path, np.zeros(round(16000 * seconds), dtype=np.float32), 16000)

    app = FastAPI()
    callbacks = []
    app.include_router(
        source_router(
            tmp_path / "runtime", listen, lambda *a: None, shutdown_callbacks=callbacks
        )
    )
    with patch("oida.source_api.capture_audio", side_effect=capture) as patched:
        try:
            yield TestClient(app), state, patched
        finally:
            for callback in callbacks:
                callback()


def listen(client, identifier, seconds, deadline):
    return client.post(
        "/sources/capture/fixture/listen",
        json=dict(acquisition_id=identifier, seconds=seconds, deadline_at=deadline),
    )


def test_a_deadline_shorter_than_the_window_starts_nothing(station):
    client, state, _ = station
    response = listen(client, "too-short", 2.0, time.time() + 1.5)
    assert response.status_code == 409 and "capture window" in response.text
    assert state["captures"] == 0
    assert listen(client, "distant", 1.0, time.time() + 5 * 3600).status_code == 400


def test_a_listening_past_the_deadline_publishes_nothing_and_says_why(station):
    client, state, _ = station
    state["listen_seconds"] = 5
    started = time.time()
    response = listen(client, "late-listening", 0.5, time.time() + 2.0)
    assert response.status_code == 200, response.text
    assert time.time() - started < 4
    receipt = response.json()["receipt"]
    assert receipt["status"] == "expired" and receipt["reason_code"] == "deadline"
    assert response.json()["result"] is None and receipt["deadline_at"] > started


def test_a_capture_still_running_at_the_deadline_is_stopped(station):
    client, state, patched = station

    def slow(source, seconds, path, cancel):
        assert cancel.wait(5), "the deadline must stop an acquisition in progress"
        raise CaptureInterrupted("cancelled")

    patched.side_effect = slow
    started = time.time()
    response = listen(client, "late-capture", 1.0, time.time() + 2.2)
    assert time.time() - started < 4
    receipt = response.json()["receipt"]
    assert receipt["status"] == "expired" and receipt["reason_code"] == "deadline"
    assert state["listened"] == 0


def test_without_a_deadline_nothing_changes(station):
    client, _, _ = station
    response = client.post(
        "/sources/capture/fixture/listen",
        json=dict(acquisition_id="plain", seconds=0.5),
    )
    receipt = response.json()["receipt"]
    assert receipt["status"] == "complete" and "reason_code" not in receipt
    assert "deadline_at" not in receipt
