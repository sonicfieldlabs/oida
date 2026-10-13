"""Bounded invalidations of the durable owner journal, never an evidence replay."""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .owner_journal import CursorMismatch

CONTRACT = "oida/owner-change-stream/v1"
FRAME_LIMIT = 1024
BYTE_LIMIT = 65536
FRAME_COUNT = 100
DURATION = 60
HEARTBEAT = 5
CONNECTIONS = 4


def encode(value):
    raw = ("data: " + json.dumps(value, separators=(",", ":")) + "\n\n").encode()
    if len(raw) > FRAME_LIMIT:
        raise ValueError("change frame exceeds its limit")
    return raw


def change_router(journal):
    router = APIRouter()
    connected = 0

    @router.get("/owner/changes/capabilities")
    def capabilities():
        return dict(
            contract=CONTRACT,
            owner="oida",
            route="/owner/changes",
            payload="invalidation_only",
            coverage="owner_journal",
            replay="coalesced_watermark",
            duration_seconds=DURATION,
            frame_bytes=FRAME_LIMIT,
            stream_bytes=BYTE_LIMIT,
            frame_count=FRAME_COUNT,
            heartbeat_seconds=HEARTBEAT,
        )

    @router.get("/owner/changes")
    async def changes(
        request: Request,
        after_sequence: int = Query(0, ge=0, le=2**53 - 1),
        producer_id: str | None = Query(None, max_length=64),
    ):
        nonlocal connected
        if connected >= CONNECTIONS:
            raise HTTPException(429, "Owner change stream capacity reached")
        connected += 1
        released = False

        def release():
            nonlocal connected, released
            if not released:
                connected -= 1
                released = True

        class BoundStream(StreamingResponse):
            async def __call__(self, scope, receive, send):
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    release()

        try:
            first = await asyncio.to_thread(
                journal.change_watermark, after=after_sequence, producer_id=producer_id
            )
        except BaseException as exc:
            release()
            if isinstance(exc, CursorMismatch):
                raise HTTPException(409, str(exc)) from exc
            raise

        async def stream():
            event = asyncio.Event()
            started = time.monotonic()
            sent = 0
            sequence = first["sequence"]
            kind = (
                "gap"
                if first["gap"]
                else "change"
                if producer_id and sequence > after_sequence
                else "snapshot"
            )
            value = dict(
                contract=CONTRACT,
                kind=kind,
                producer_id=first["producer_id"],
                sequence=sequence,
                replay=bool(producer_id and sequence > after_sequence),
            )
            try:
                with journal.watch_changes(asyncio.get_running_loop(), event):
                    frame = encode(value)
                    sent += len(frame)
                    yield frame
                    for _ in range(FRAME_COUNT - 1):
                        remaining = DURATION - (time.monotonic() - started)
                        if remaining <= 0 or await request.is_disconnected():
                            break
                        # Clearing before the durable read avoids losing a commit
                        # between reading the watermark and installing the waiter.
                        event.clear()
                        mark = await asyncio.to_thread(
                            journal.change_watermark,
                            after=sequence,
                            producer_id=first["producer_id"],
                        )
                        if mark["sequence"] == sequence:
                            try:
                                await asyncio.wait_for(
                                    event.wait(), min(HEARTBEAT, remaining)
                                )
                            except TimeoutError:
                                pass
                            if (
                                time.monotonic() - started >= DURATION
                                or await request.is_disconnected()
                            ):
                                break
                            mark = await asyncio.to_thread(
                                journal.change_watermark,
                                after=sequence,
                                producer_id=first["producer_id"],
                            )
                        kind = (
                            "gap"
                            if mark["gap"]
                            else "change"
                            if mark["sequence"] > sequence
                            else "heartbeat"
                        )
                        sequence = mark["sequence"]
                        frame = encode(
                            dict(
                                contract=CONTRACT,
                                kind=kind,
                                producer_id=first["producer_id"],
                                sequence=sequence,
                                replay=False,
                            )
                        )
                        if sent + len(frame) > BYTE_LIMIT:
                            break
                        sent += len(frame)
                        yield frame
            finally:
                release()

        return BoundStream(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
