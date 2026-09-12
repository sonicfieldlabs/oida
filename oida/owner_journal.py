"""Owner-local durable sequencing; public projection cursors belong to the application."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from oida.contracts import now_iso


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class CursorMismatch(ValueError):
    pass


class OwnerJournal:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, subject TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_subject_sequence ON events(kind,subject,sequence);
                CREATE TABLE IF NOT EXISTS snapshots (
                    kind TEXT NOT NULL, subject TEXT NOT NULL,
                    sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(kind,subject));
            """)
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('producer_id',?)",
                ("oida:" + str(uuid4()),),
            )
            self.producer_id = db.execute(
                "SELECT value FROM metadata WHERE key='producer_id'"
            ).fetchone()[0]

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def save(self, kind: str, subject: str, payload: dict):
        encoded = canonical(payload)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT sequence,payload FROM snapshots WHERE kind=? AND subject=?",
                (kind, subject),
            ).fetchone()
            if previous and previous[1] == encoded:
                return previous[0]
            created = now_iso()
            sequence = db.execute(
                "INSERT INTO events(kind,subject,created_at,payload) VALUES (?,?,?,?)",
                (kind, subject, created, encoded),
            ).lastrowid
            db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?) ON CONFLICT(kind,subject) DO UPDATE SET sequence=excluded.sequence,payload=excluded.payload",
                (kind, subject, sequence, encoded),
            )
            return sequence

    def get(self, kind, subject):
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM snapshots WHERE kind=? AND subject=?",
                (kind, subject),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def record_reference(self, record_id, *, event_id=None, record=None):
        from oida.operation_control import current_operation_id

        previous = self.get("record_reference", record_id) or {}
        value = dict(
            akousma_id=record_id,
            event_id=event_id or previous.get("event_id"),
            status="referenced",
            basis="canonical store remains authoritative; this is a retained link",
        )
        digest = (
            self.record_digest(record)
            if record is not None
            else previous.get("record_sha256")
        )
        if digest is not None:
            value["record_sha256"] = digest
        operation_id = current_operation_id() or previous.get("operation_id")
        if operation_id is not None:
            value["operation_id"] = operation_id
        self.save("record_reference", record_id, value)

    def _cursor(self, db, producer_id, after):
        high = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
        if producer_id is not None and producer_id != self.producer_id:
            raise CursorMismatch("producer identity changed; obtain a new snapshot")
        if after and producer_id is None:
            raise CursorMismatch("resumption requires the producer identity")
        if after < 0 or after > high:
            raise CursorMismatch("sequence is outside this producer journal")
        return high

    def events(self, *, after=0, producer_id=None, limit=100):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.connection() as db:
            db.execute("BEGIN")
            high = self._cursor(db, producer_id, after)
            rows = db.execute(
                "SELECT sequence,kind,subject,created_at,payload FROM events WHERE sequence>? AND sequence<=? ORDER BY sequence LIMIT ?",
                (after, high, limit + 1),
            ).fetchall()
            more = len(rows) > limit
            events = [
                dict(
                    producer_id=self.producer_id,
                    sequence=s,
                    kind=k,
                    subject_id=i,
                    created_at=t,
                    payload=json.loads(p),
                )
                for s, k, i, t, p in rows[:limit]
            ]
            return dict(
                contract="oida/owner-journal/v1",
                producer_id=self.producer_id,
                events=events,
                next_sequence=events[-1]["sequence"] if events else after,
                high_water_sequence=high,
                has_more=more,
                visibility="owner_only",
            )

    def snapshots(self, *, after=0, producer_id=None, limit=100, at=None):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.connection() as db:
            db.execute("BEGIN")
            high = self._cursor(db, producer_id, after)
            boundary = high if at is None else at
            if not 0 <= boundary <= high or after > boundary:
                raise CursorMismatch(
                    "snapshot watermark is outside this producer journal"
                )
            if at is not None and producer_id is None:
                raise CursorMismatch(
                    "snapshot continuation requires the producer identity"
                )
            # Reconstruct as of a pinned boundary so updates cannot disappear between pages.
            rows = db.execute(
                """SELECT e.sequence,e.kind,e.subject,e.payload FROM events e
                JOIN (SELECT kind,subject,MAX(sequence) AS latest FROM events WHERE sequence<=?
                      GROUP BY kind,subject) s ON s.latest=e.sequence
                WHERE e.sequence>? ORDER BY e.sequence LIMIT ?""",
                (boundary, after, limit + 1),
            ).fetchall()
            values = [
                dict(
                    producer_id=self.producer_id,
                    sequence=s,
                    kind=k,
                    subject_id=i,
                    payload=json.loads(p),
                )
                for s, k, i, p in rows[:limit]
            ]
            return dict(
                contract="oida/owner-snapshot/v1",
                producer_id=self.producer_id,
                snapshots=values,
                next_sequence=values[-1]["sequence"] if values else after,
                high_water_sequence=boundary,
                has_more=len(rows) > limit,
                visibility="owner_only",
            )

    def record_digest(self, record):
        return hashlib.sha256(canonical(record).encode()).hexdigest()
