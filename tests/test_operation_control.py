from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

import pytest
from oida.operation_control import Operations, checkpoint
from oida.owner_journal import OwnerJournal
from test_runtime_attribution import (
    client as client,
    fixture_request as fixture_request,
)


def test_owner_cancels_inference_before_any_retained_record(client, tmp_path):
    import oida.server as server

    entered = threading.Event()
    release = threading.Event()
    original = server.report

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    request = fixture_request(tmp_path)
    with patch("oida.server.report", side_effect=delayed), ThreadPoolExecutor() as pool:
        future = pool.submit(
            client.post,
            "/gateway/listen",
            json=dict(path=request["path"], remember=True, operation_id="cancel-me"),
        )
        try:
            assert entered.wait(3)
            assert client.post("/operations/cancel-me/cancel").json()[
                "cancel_requested"
            ]
            acknowledgement = client.get("/operations/cancel-me").json()
            assert acknowledgement["status"] == "cancelled"
            assert acknowledgement["worker_settled"] is False
            assert acknowledgement["publication_prevented"] is True
        finally:
            release.set()
        assert future.result().status_code == 409
    journal = client.get("/owner/journal").json()
    assert all(e["kind"] == "operation" for e in journal["events"])
    assert client.get("/operations/cancel-me").json()["worker_settled"] is True
    assert "event_id" not in client.get("/operations/cancel-me").json()
    assert (
        client.post(
            "/gateway/listen", json=dict(path=request["path"], operation_id="cancel-me")
        ).status_code
        == 409
    )
    result = client.post(
        "/gateway/listen",
        json=dict(path=request["path"], remember=True, operation_id="new-attempt"),
    )
    assert result.status_code == 200, result.text
    receipt = client.get("/operations/new-attempt").json()
    assert receipt["akousma_id"] == result.json()["akousma_id"]
    assert client.get("/owner/records/" + receipt["akousma_id"]).status_code == 200
    assert not client.post("/operations/new-attempt/cancel").json()["cancel_requested"]


def test_upload_cancel_removes_normalized_and_raw_files(client, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    paths = []

    def normalize(raw):
        target = raw.with_suffix(".wav")
        target.write_bytes(b"fixture")
        paths.extend([raw, target])
        entered.set()
        assert release.wait(5)
        return target, None

    with (
        patch("oida.server.normalize_audio", side_effect=normalize),
        ThreadPoolExecutor() as pool,
    ):
        future = pool.submit(
            client.post,
            "/upload",
            data={"operation_id": "upload-cancel"},
            files={"file": ("fixture.webm", b"fixture", "audio/webm")},
        )
        try:
            assert entered.wait(3)
            assert client.post("/operations/upload-cancel/cancel").json()[
                "cancel_requested"
            ]
        finally:
            release.set()
        assert future.result().status_code == 409
    assert paths and not any(p.exists() for p in paths)
    assert client.get("/operations/upload-cancel").json()["status"] == "cancelled"


def test_commit_fence_and_restart_receipts(tmp_path):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    operations = Operations(journal)

    def complete():
        checkpoint(seal=True)
        assert not operations.cancel("sealed")
        return {"akousma_id": "fixture-link"}

    assert operations.run("sealed", complete)["akousma_id"] == "fixture-link"
    operations.save("crashed", "running")
    restored = Operations(OwnerJournal(journal.path))
    assert restored.journal.get("operation", "crashed")["status"] == "interrupted"
    with pytest.raises(Exception):
        restored.run("crashed", lambda: {})


def test_cancel_wins_over_worker_failure_and_survives_restart(tmp_path):
    operations = Operations(OwnerJournal(tmp_path / "journal.sqlite3"))

    def fails_late():
        assert operations.cancel("late-failure")
        raise RuntimeError("PRIVATE_WORKER_OUTPUT")

    from oida.operation_control import OperationCancelled

    with pytest.raises(OperationCancelled):
        operations.run("late-failure", fails_late)
    restored = Operations(OwnerJournal(tmp_path / "journal.sqlite3"))
    receipt = restored.journal.get("operation", "late-failure")
    assert receipt["status"] == "cancelled" and "PRIVATE_WORKER_OUTPUT" not in str(
        receipt
    )
    assert restored.cancel("late-failure")


def test_gateway_failure_receipt_has_no_private_input(client):
    result = client.post(
        "/gateway/listen",
        json={"path": "/missing/PRIVATE_INPUT.wav", "operation_id": "failure"},
    )
    assert result.status_code == 400
    receipt = client.get("/operations/failure").json()
    assert receipt["status"] == "refused" and "PRIVATE_INPUT" not in str(receipt)
