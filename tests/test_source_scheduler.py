from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from oida.source_api import source_router
from oida.source_capture import AcquisitionReceipts, CaptureInterrupted


def wait_receipt(client, identifier, statuses, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/sources/acquisitions/" + identifier)
        if response.status_code == 200 and response.json()["status"] in statuses:
            return response.json()
        time.sleep(0.01)
    raise AssertionError(response.text)


@pytest.fixture
def harness(tmp_path, monkeypatch):
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
                        max_seconds=1.0,
                        producer_id="fixture",
                        consent="granted",
                        consent_ref="fixture",
                    )
                ],
            )
        )
    )
    monkeypatch.setenv("OIDA_CAPTURE_SOURCES", str(manifest))
    monkeypatch.setenv("OIDA_SOURCE_QUEUE_CAPACITY", "2")
    callbacks = []
    captures = []
    entered = threading.Event()
    release = threading.Event()

    def capture(source, seconds, path, cancel):
        captures.append(seconds)
        sf.write(path, np.zeros(round(16000 * seconds), dtype=np.float32), 16000)

    def listen(payload):
        if len(captures) == 1:
            entered.set()
            assert release.wait(4)
        return dict(
            listening_event=dict(
                id="event-" + str(len(captures)),
                source=dict(details=dict(source_admission=payload["source_admission"])),
            ),
            akousma_id=None,
        )

    app = FastAPI()
    app.state.refuse = False

    def preflight(*args):
        if app.state.refuse:
            raise HTTPException(423, "changed fixture covenant")

    app.include_router(
        source_router(
            tmp_path / "runtime", listen, preflight, shutdown_callbacks=callbacks
        )
    )
    with patch("oida.source_api.capture_audio", side_effect=capture):
        client = TestClient(app)
        try:
            yield client, entered, release, captures, callbacks
        finally:
            release.set()
            for callback in callbacks:
                callback()
            deadline = time.monotonic() + 4
            while (
                client.get("/sources/scheduler").json()["active_id"]
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)


def submit(client, identifier, seconds=0.1, ttl=3):
    return client.post(
        "/sources/capture/fixture/jobs",
        json=dict(acquisition_id=identifier, seconds=seconds, expires_in_seconds=ttl),
    )


def test_queue_bounds_fifo_and_control_while_listening_blocks(harness):
    client, entered, release, captures, _ = harness
    assert submit(client, "first").status_code == 202
    assert entered.wait(2)
    assert submit(client, "second", 0.2).status_code == 202
    assert submit(client, "third", 0.3).status_code == 202
    started = time.perf_counter()
    assert submit(client, "overflow").status_code == 429
    assert client.get("/sources/scheduler").json()["queued"] == 2
    assert time.perf_counter() - started < 1
    # Synchronous route shares the same execution lock and cannot bypass the worker.
    assert (
        client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="direct", seconds=0.1),
        ).status_code
        == 409
    )
    release.set()
    for identifier in ("first", "second", "third"):
        result = wait_receipt(client, identifier, {"complete"})
        assert result["event_id"]
        assert result["timing"]["execution_seconds"] >= 0
        assert result["owner_peak_rss"]["status"] in {"known", "unknown"}
    assert captures == [0.1, 0.2, 0.3]
    assert submit(client, "second").status_code == 400


def test_expiry_monitor_runs_while_model_is_busy_and_cancel_is_responsive(harness):
    client, entered, release, captures, _ = harness
    submit(client, "blocker")
    assert entered.wait(2)
    submit(client, "stale", 0.2, 0.08)
    submit(client, "cancel", 0.3)
    started = time.perf_counter()
    assert client.post("/sources/acquisitions/cancel/cancel").json()["cancel_requested"]
    assert time.perf_counter() - started < 1
    assert wait_receipt(client, "stale", {"expired"})["expiry_basis"]
    assert wait_receipt(client, "cancel", {"cancelled"})
    assert captures == [0.1]
    release.set()
    wait_receipt(client, "blocker", {"complete"})


def test_shutdown_cancels_queue_and_discards_late_model_output(harness):
    client, entered, release, _, callbacks = harness
    submit(client, "running")
    assert entered.wait(2)
    submit(client, "pending")
    callbacks[0]()
    assert wait_receipt(client, "pending", {"cancelled"})
    assert client.get("/sources/scheduler").json()["accepting"] is False
    assert submit(client, "late").status_code == 429
    release.set()
    assert wait_receipt(client, "running", {"cancelled"})


def test_expiry_during_acquisition_never_listens(harness):
    client, _, _, _, _ = harness

    def capture(source, seconds, path, cancel):
        assert cancel.wait(2)
        raise CaptureInterrupted("deadline")

    with patch("oida.source_api.capture_audio", side_effect=capture):
        assert submit(client, "expiring", ttl=0.08).status_code == 202
        assert (
            wait_receipt(client, "expiring", {"expired"})["reason"]
            == "deadline elapsed before publication"
        )


def test_dispatch_rechecks_changed_covenant(harness):
    client, entered, release, captures, _ = harness
    submit(client, "first")
    assert entered.wait(2)
    submit(client, "later")
    client.app.state.refuse = True
    release.set()
    assert wait_receipt(client, "later", {"refused"})
    assert captures == [0.1]


def test_backend_failure_can_retry_with_new_identity(harness):
    client, _, release, _, _ = harness
    with patch(
        "oida.source_api.capture_audio", side_effect=RuntimeError("fixture backend")
    ):
        submit(client, "failure")
        assert wait_receipt(client, "failure", {"failed"})
    release.set()
    assert submit(client, "retry").status_code == 202
    assert wait_receipt(client, "retry", {"complete"})


def test_waiting_behind_synchronous_call_expires_without_capture(harness):
    client, entered, release, captures, _ = harness
    output = []
    thread = threading.Thread(
        target=lambda: output.append(
            client.post(
                "/sources/capture/fixture/listen",
                json=dict(acquisition_id="direct", seconds=0.1),
            )
        )
    )
    thread.start()
    try:
        assert entered.wait(2)
        assert client.get("/sources/scheduler").json()["active_id"] == "direct"
        submit(client, "queued", ttl=0.08)
        assert wait_receipt(client, "queued", {"expired"})
        assert captures == [0.1]
    finally:
        release.set()
        thread.join(timeout=4)
    assert not thread.is_alive()
    assert output[0].json()["receipt"]["status"] == "complete"


def test_restart_never_replays_queued_work(tmp_path):
    root = tmp_path / "receipts"
    receipts = AcquisitionReceipts(root)
    receipts.save(
        dict(
            contract="oida/acquisition-receipt/v1",
            id="pending",
            source_id="fixture",
            status="queued",
        )
    )
    restarted = AcquisitionReceipts(root)
    assert json.loads((root / "pending.json").read_text())["status"] == "interrupted"
    assert not restarted.active


def test_restart_expires_stale_queue_and_leaves_completed_receipts(tmp_path):
    receipts = AcquisitionReceipts(tmp_path / "receipts")
    receipts.save(
        dict(
            id="stale",
            source_id="fixture",
            status="queued",
            expires_at="2000-01-01T00:00:00+00:00",
        )
    )
    complete = dict(
        id="done", source_id="fixture", status="complete", event_id="event-old"
    )
    receipts.save(complete)
    AcquisitionReceipts(receipts.root)
    assert json.loads((receipts.root / "stale.json").read_text())["status"] == "expired"
    assert json.loads((receipts.root / "done.json").read_text()) == complete


def test_active_listening_cancellation_and_policy_change_discard_late_results(harness):
    client, entered, release, _, _ = harness
    submit(client, "late-result")
    assert entered.wait(2)
    assert client.post("/sources/acquisitions/late-result/cancel").json()["cancel_requested"]
    assert client.post("/sources/acquisitions/late-result/cancel").json()["cancel_requested"]
    release.set()
    deadline=time.monotonic()+3
    while client.get('/sources/scheduler').json()['active_id'] and time.monotonic()<deadline:
        time.sleep(.01)
    receipt=client.get('/sources/acquisitions/late-result').json()
    assert receipt['status']=='cancelled' and 'event_id' not in receipt


def test_policy_refusal_after_listening_discards_result(harness):
    client, entered, release, _, _ = harness
    submit(client, 'policy-change')
    assert entered.wait(2)
    client.app.state.refuse=True
    release.set()
    receipt=wait_receipt(client,'policy-change',{'refused'})
    assert 'event_id' not in receipt and 'akousma_id' not in receipt
