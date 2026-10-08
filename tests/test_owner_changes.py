"""Generated metadata only: durable invalidation/replay, gaps and cleanup."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from oida.owner_changes import CONTRACT, change_router
from oida.owner_journal import CursorMismatch, OwnerJournal


def endpoint(journal):
    return next(
        r.endpoint for r in change_router(journal).routes if r.path == "/owner/changes"
    )


def request():
    async def disconnected():
        return False

    return SimpleNamespace(is_disconnected=disconnected)


def unpack(raw):
    value = json.loads(raw[6:])
    assert set(value) == {"contract", "kind", "producer_id", "sequence", "replay"}
    assert value["contract"] == CONTRACT
    return value


def test_committed_change_notifies_without_private_payload(tmp_path):
    async def run():
        journal = OwnerJournal(tmp_path / "journal.sqlite3")
        response = await endpoint(journal)(request(), 0, None)
        iterator = response.body_iterator
        assert unpack(await anext(iterator))["kind"] == "snapshot"
        task = asyncio.create_task(anext(iterator))
        await asyncio.to_thread(
            journal.save, "operation", "PRIVATE_SUBJECT", {"text": "PRIVATE_CANARY"}
        )
        raw = await asyncio.wait_for(task, 1)
        assert b"PRIVATE" not in raw
        assert unpack(raw)["sequence"] == 1
        await iterator.aclose()
        assert not journal._listeners

    asyncio.run(run())


def test_resume_coalesces_and_pruned_range_marks_gap(tmp_path):
    async def run():
        journal = OwnerJournal(tmp_path / "journal.sqlite3")
        for i in range(6):
            journal.save("operation", str(i), {"text": "PRIVATE_CANARY"})
        for prune, expected in [(False, "change"), (True, "gap")]:
            if prune:
                with journal.connection() as db:
                    db.execute("DELETE FROM events WHERE sequence < 5")
            response = await endpoint(journal)(request(), 1, journal.producer_id)
            raw = await anext(response.body_iterator)
            value = unpack(raw)
            assert value["kind"] == expected and value["replay"]
            assert value["sequence"] == 6 and b"PRIVATE" not in raw
            await response.body_iterator.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("after,producer", [(1, None), (100, "valid"), (0, "other")])
def test_bad_resume_refused_before_stream(tmp_path, after, producer):
    from fastapi import HTTPException

    async def run():
        journal = OwnerJournal(tmp_path / "journal.sqlite3")
        with pytest.raises(HTTPException) as exc:
            await endpoint(journal)(request(), after, producer)
        assert exc.value.status_code == 409

    asyncio.run(run())


def test_watermark_reads_no_retained_payload(tmp_path, monkeypatch):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    journal.save("record_reference", "private", {"text": "PRIVATE_CANARY"})
    monkeypatch.setattr(
        "oida.owner_journal.json.loads", lambda _: pytest.fail("payload decoded")
    )
    assert journal.change_watermark()["sequence"] == 1
    with pytest.raises(CursorMismatch):
        journal.change_watermark(after=2, producer_id=journal.producer_id)


def test_other_process_commit_recovered_by_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr("oida.owner_changes.HEARTBEAT", 0.02)

    async def run():
        journal = OwnerJournal(tmp_path / "journal.sqlite3")
        other = OwnerJournal(journal.path)
        response = await endpoint(journal)(request(), 0, None)
        assert unpack(await anext(response.body_iterator))["sequence"] == 0
        other.save("operation", "one", {"text": "PRIVATE_CANARY"})
        value = unpack(await asyncio.wait_for(anext(response.body_iterator), 1))
        assert value["sequence"] == 1 and value["kind"] == "change"
        await response.body_iterator.aclose()

    asyncio.run(run())


def test_capacity_duration_and_cleanup_are_bounded(tmp_path, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr("oida.owner_changes.DURATION", 0.025)

    async def run():
        journal = OwnerJournal(tmp_path / "journal.sqlite3")
        api = endpoint(journal)
        responses = [await api(request(), 0, None) for _ in range(4)]
        with pytest.raises(HTTPException) as exc:
            await api(request(), 0, None)
        assert exc.value.status_code == 429
        for response in responses:
            values = [unpack(raw) async for raw in response.body_iterator]
            assert len(values) == 1
        response = await api(request(), 0, None)
        assert unpack(await anext(response.body_iterator))["kind"] == "snapshot"
        await response.body_iterator.aclose()
        assert not journal._listeners

    asyncio.run(run())


def test_rollback_and_identical_save_publish_no_change(tmp_path):
    async def run():
        journal = OwnerJournal(tmp_path / "journal.sqlite3")
        journal.save("operation", "one", {"status": "complete"})
        event = asyncio.Event()
        with journal.watch_changes(asyncio.get_running_loop(), event):
            journal.save("operation", "one", {"status": "complete"})
            await asyncio.sleep(0)
            assert not event.is_set()
            with journal.connection() as db:
                db.execute(
                    "CREATE TRIGGER refuse_snapshot BEFORE INSERT ON snapshots BEGIN SELECT RAISE(ABORT,'fixture'); END"
                )
            with pytest.raises(Exception, match="fixture"):
                journal.save("operation", "two", {"status": "complete"})
            await asyncio.sleep(0)
            assert not event.is_set() and journal.change_watermark()["sequence"] == 1

    asyncio.run(run())
