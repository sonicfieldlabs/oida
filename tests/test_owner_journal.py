# ruff: noqa: F811
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from oida.owner_journal import CursorMismatch, OwnerJournal
from oida.source_capture import AcquisitionReceipts
from test_runtime_attribution import client, fixture_request  # noqa: F401
from test_source_scheduler import harness  # noqa: F401
from test_source_capture import setup, radio  # noqa: F401


def test_sequence_survives_restart_and_identical_updates_are_idempotent(tmp_path):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    assert journal.save("acquisition", "one", {"status": "queued"}) == 1
    assert journal.save("acquisition", "one", {"status": "queued"}) == 1
    assert journal.save("acquisition", "one", {"status": "complete"}) == 2
    restored = OwnerJournal(journal.path)
    assert restored.producer_id == journal.producer_id
    result = restored.events(after=1, producer_id=journal.producer_id)
    assert result["events"][0]["payload"] == {"status": "complete"}
    assert result["next_sequence"] == 2
    for args in [
        dict(after=1),
        dict(after=3, producer_id=journal.producer_id),
        dict(producer_id="other"),
    ]:
        with pytest.raises(CursorMismatch):
            restored.events(**args)


def test_concurrent_writers_have_unique_monotonic_owner_sequences(tmp_path):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(
            pool.map(
                lambda i: journal.save("acquisition", str(i), {"status": "queued"}),
                range(30),
            )
        )
    assert sorted(values) == list(range(1, 31))
    result = journal.events(limit=10)
    assert result["has_more"] and result["next_sequence"] == 10
    assert (
        journal.events(after=10, producer_id=journal.producer_id)["events"][0][
            "sequence"
        ]
        == 11
    )


def test_snapshot_pages_are_pinned_despite_updates(tmp_path):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    journal.save("acquisition", "a", {"status": "queued"})
    journal.save("acquisition", "b", {"status": "queued"})
    page = journal.snapshots(limit=1)
    journal.save("acquisition", "b", {"status": "complete"})
    journal.save("acquisition", "c", {"status": "queued"})
    rest = journal.snapshots(
        after=page["next_sequence"],
        producer_id=journal.producer_id,
        at=page["high_water_sequence"],
    )
    assert [r["subject_id"] for r in rest["snapshots"]] == ["b"]
    assert rest["snapshots"][0]["payload"]["status"] == "queued"
    delta = journal.events(
        after=page["high_water_sequence"], producer_id=journal.producer_id
    )
    assert [r["subject_id"] for r in delta["events"]] == ["b", "c"]


def test_snapshot_failure_rolls_back_event_append(tmp_path):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    with journal.connection() as db:
        db.execute(
            "CREATE TRIGGER refuse_snapshot BEFORE INSERT ON snapshots BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        journal.save("acquisition", "one", {"status": "queued"})
    assert journal.events()["high_water_sequence"] == 0
    assert journal.get("acquisition", "one") is None


def test_json_mirror_failure_does_not_lose_committed_transition(tmp_path):
    receipts = AcquisitionReceipts(tmp_path / "receipts")
    receipt = dict(id="one", source_id="fixture", status="queued")
    receipts.save(receipt)
    with patch(
        "oida.source_capture.os.replace", side_effect=OSError("fixture disk mirror")
    ):
        receipts.save({**receipt, "status": "complete"})
    assert json.loads((receipts.root / "one.json").read_text())["status"] == "queued"
    restored = AcquisitionReceipts(receipts.root)
    assert restored.get("one")["status"] == "complete"
    assert restored.journal.events()["high_water_sequence"] == 2


def test_legacy_import_and_restart_outcomes_emit_once(tmp_path):
    root = tmp_path / "receipts"
    root.mkdir()
    (root / "legacy.json").write_text(
        json.dumps(dict(id="legacy", source_id="fixture", status="acquiring"))
    )
    receipts = AcquisitionReceipts(root)
    events = receipts.journal.events()["events"]
    assert [e["payload"]["status"] for e in events] == ["acquiring", "interrupted"]
    assert AcquisitionReceipts(root).journal.events()["high_water_sequence"] == 2


def test_owner_record_and_resume_apis_reuse_canonical_store(client, tmp_path):
    request = fixture_request(tmp_path)
    response = client.post(
        "/gateway/listen", json=dict(path=request["path"], remember=True)
    )
    assert response.status_code == 200, response.text
    identifier = response.json()["akousma_id"]
    journal = client.get("/owner/journal").json()
    references = [e for e in journal["events"] if e["kind"] == "record_reference"]
    assert references and references[-1]["payload"]["akousma_id"] == identifier
    assert "summary" not in json.dumps(references)
    record = client.get("/owner/records/" + identifier)
    assert record.status_code == 200, record.text
    assert record.json()["record"]["akousma_id"] == identifier
    assert (
        record.json()["reference"]["record_sha256"] == record.json()["current_sha256"]
    )
    assert (
        client.get(
            "/owner/journal", params={"after_sequence": journal["next_sequence"]}
        ).status_code
        == 409
    )
    assert (
        client.get(
            "/owner/journal",
            params={
                "producer_id": journal["producer_id"],
                "after_sequence": journal["next_sequence"],
            },
        ).json()["events"]
        == []
    )
    assert client.post("/owner/records/" + identifier + "/reconcile").status_code == 200
    assert (
        client.get("/owner/journal").json()["high_water_sequence"]
        == journal["high_water_sequence"]
    )


def test_unremembered_event_does_not_persist_transcript_or_record_link(
    client, tmp_path
):
    request = fixture_request(tmp_path)
    response = client.post(
        "/gateway/listen", json=dict(path=request["path"], remember=False)
    )
    assert response.status_code == 200, response.text
    assert client.get("/owner/journal").json()["events"] == []
    assert client.get("/owner/journal", params={"limit": 501}).status_code == 422
    assert client.get("/owner/records/missing").status_code == 404


def test_source_lifecycle_outcomes_are_resumable(harness):
    from test_source_scheduler import submit, wait_receipt

    api, entered, release, _, _ = harness
    submit(api, "running")
    assert entered.wait(2)
    first = api.get("/owner/journal").json()
    submit(api, "cancel")
    assert api.post("/sources/acquisitions/cancel/cancel").json()["cancel_requested"]
    submit(api, "stale", ttl=0.08)
    wait_receipt(api, "stale", {"expired"})
    release.set()
    wait_receipt(api, "running", {"complete"})
    with patch("oida.source_api.capture_audio", side_effect=RuntimeError("fixture")):
        submit(api, "failure")
        wait_receipt(api, "failure", {"failed"})
    submit(api, "retry")
    wait_receipt(api, "retry", {"complete"})
    delta = api.get(
        "/owner/journal",
        params=dict(
            producer_id=first["producer_id"], after_sequence=first["next_sequence"]
        ),
    ).json()
    assert all(e["sequence"] > first["next_sequence"] for e in delta["events"])
    snapshots = api.get("/owner/snapshots").json()
    states = {r["subject_id"]: r["payload"] for r in snapshots["snapshots"]}
    assert {k: v["status"] for k, v in states.items()} == dict(
        running="complete",
        cancel="cancelled",
        stale="expired",
        failure="failed",
        retry="complete",
    )
    assert states["retry"]["event_id"]
    assert states["retry"]["source_descriptor"]["producer_id"] == "fixture"
    assert snapshots["producer_id"] != "fixture"
    assert (
        api.get("/owner/snapshots", params=dict(producer_id="wrong")).status_code == 409
    )


def test_real_capture_journal_links_to_canonical_record(setup, radio):
    from test_source_capture import source

    spec = source()
    spec["input"] = radio
    api = setup([spec])
    response = api.post(
        "/sources/capture/fixture/listen",
        json=dict(acquisition_id="journal-capture", seconds=0.25, remember=True),
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert receipt["status"] == "complete"
    events = api.get("/owner/journal").json()["events"]
    transitions = [e["payload"]["status"] for e in events if e["kind"] == "acquisition"]
    assert transitions == ["acquiring", "acquiring", "listening", "committing", "complete"]
    record = api.get("/owner/records/" + receipt["akousma_id"]).json()
    assert record["reference"]["event_id"] == receipt["event_id"]
    assert record["reference"]["record_sha256"] == record["current_sha256"]
