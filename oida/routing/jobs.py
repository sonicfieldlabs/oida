"""Durable, bounded routing admission with fair agent turns and no remote replay."""

from __future__ import annotations

import contextvars
import hashlib
import json
import sqlite3
import os
from datetime import datetime
import threading
import time
from contextlib import contextmanager

from fastapi import HTTPException

from oida.owner_journal import canonical

_control = contextvars.ContextVar("routing_control", default=None)
TERMINAL = {"complete", "failed", "cancelled", "expired", "interrupted"}


def workspace_binding():
    return canonical({key: os.getenv(key) for key in ("LISTENINGSTACK_WORKSPACE_ID", "LISTENINGSTACK_WORKSPACE_GENERATION")})


def checkpoint():
    control = _control.get()
    if control:
        queue, identifier = control
        row = queue.get(identifier)
        if row["cancel_requested"]:
            raise HTTPException(409, "Decision cancelled")
        if time.time() >= row["deadline_at"]:
            raise HTTPException(409, "Decision deadline expired")


def remaining_seconds(default=120.0):
    control = _control.get()
    if not control:
        return default
    checkpoint()
    queue, identifier = control
    return max(0.001, min(default, queue.get(identifier)["deadline_at"] - time.time()))


@contextmanager
def execution_control(queue, identifier):
    token = _control.set((queue, identifier))
    try:
        checkpoint()
        yield
    finally:
        _control.reset(token)


class DecisionQueue:
    capacity = 32
    per_agent_capacity = 8

    def __init__(self, journal, execute):
        self.journal, self.execute = journal, execute
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.worker = None
        self.closed = False
        with journal.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS routing_jobs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    agent TEXT NOT NULL, chain_id TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL,
                    queued_at REAL NOT NULL, deadline_at REAL NOT NULL,
                    started_at REAL, finished_at REAL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    result TEXT, error_code INTEGER, error TEXT);
                CREATE INDEX IF NOT EXISTS routing_jobs_pending ON routing_jobs(status,agent,sequence);
                CREATE TABLE IF NOT EXISTS routing_agent_turns (agent TEXT PRIMARY KEY, turn INTEGER NOT NULL);
            """)
            if "binding" not in {row[1] for row in db.execute("PRAGMA table_info(routing_jobs)")}:
                db.execute("ALTER TABLE routing_jobs ADD COLUMN binding TEXT")
            # A provider may already have billed a running request. Preserve its
            # uncertainty; only requests that never started are resumed.
            db.execute("UPDATE routing_jobs SET status='interrupted',finished_at=?,error='Owner restarted during provider work; review before a new request',error_code=409 WHERE status='running'", (time.time(),))

    def start(self):
        with self.lock:
            if not self.closed and (self.worker is None or not self.worker.is_alive()):
                self.worker = threading.Thread(target=self._run, name="oida-routing", daemon=True)
                self.worker.start()
        self.wake.set()

    def close(self):
        self.closed = True
        with self.journal.connection() as db:
            db.execute("UPDATE routing_jobs SET cancel_requested=1 WHERE status='running'")
        self.wake.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)

    def submit(self, payload):
        if self.closed:
            raise HTTPException(503, "Decision owner is shutting down")
        identifier = payload["request_id"]
        fingerprint = hashlib.sha256(canonical(payload).encode()).hexdigest()
        agent = (payload.get("context") or {}).get("agent_id") or payload["chain_id"]
        now = time.time()
        deadline = now + payload["settings"]["limits"]["deadline_seconds"]
        fresh = (payload.get("context") or {}).get("fresh_until")
        if fresh:
            try:
                instant = datetime.fromisoformat(fresh.replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    raise ValueError()
                deadline = min(deadline, instant.timestamp())
            except ValueError as exc:
                raise HTTPException(422, "Invalid decision freshness deadline") from exc
        if deadline <= now:
            raise HTTPException(409, "Decision context expired before queuing")
        with self.journal.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT fingerprint FROM routing_jobs WHERE id=?", (identifier,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise HTTPException(409, "Decision ID already belongs to another request")
            else:
                total, own = db.execute("SELECT count(*),coalesce(sum(agent=?),0) FROM routing_jobs WHERE status IN ('queued','running')", (agent,)).fetchone()
                if total >= self.capacity or own >= self.per_agent_capacity:
                    raise HTTPException(429, "Decision queue capacity reached; retry with the same request ID", headers={"Retry-After": "1"})
                db.execute("INSERT INTO routing_jobs(id,fingerprint,agent,chain_id,payload,status,queued_at,deadline_at,binding) VALUES (?,?,?,?,?,'queued',?,?,?)", (identifier, fingerprint, agent, payload["chain_id"], canonical(payload), now, deadline, workspace_binding()))
        self.start()
        return self.get(identifier)

    def get(self, identifier):
        with self.journal.connection() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT sequence,id,agent,chain_id,status,queued_at,deadline_at,started_at,finished_at,cancel_requested,result,error_code,error FROM routing_jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise HTTPException(404, "Decision job not found")
        value = dict(row)
        value["result"] = json.loads(value["result"]) if value["result"] else None
        value["queue_wait_ms"] = round(1000 * ((value["started_at"] or value["finished_at"] or time.time()) - value["queued_at"]))
        value["execution_ms"] = round(1000 * ((value["finished_at"] or time.time()) - value["started_at"])) if value["started_at"] else None
        return value

    def cancel(self, identifier):
        self.get(identifier)
        with self.journal.connection() as db:
            db.execute("UPDATE routing_jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,finished_at=CASE WHEN status='queued' THEN ? ELSE finished_at END WHERE id=? AND status IN ('queued','running')", (time.time(), identifier))
        self.wake.set()
        return self.get(identifier)

    def state(self):
        with self.journal.connection() as db:
            counts = dict(db.execute("SELECT status,count(*) FROM routing_jobs GROUP BY status"))
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute("SELECT id,agent,chain_id,status,queued_at,started_at,finished_at,cancel_requested,deadline_at FROM routing_jobs ORDER BY sequence DESC LIMIT 100")]
        now = time.time()
        for value in rows:
            value["queue_wait_ms"] = round(1000 * ((value["started_at"] or value["finished_at"] or now) - value["queued_at"]))
            value["execution_ms"] = round(1000 * ((value["finished_at"] or now) - value["started_at"])) if value["started_at"] else None
        return {"capacity": self.capacity, "per_agent_capacity": self.per_agent_capacity, "counts": counts, "jobs": rows}

    def _claim(self):
        with self.journal.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("UPDATE routing_jobs SET status='expired',finished_at=?,error_code=409,error='Decision deadline expired in queue' WHERE status='queued' AND deadline_at<=?", (now, now))
            db.execute("UPDATE routing_jobs SET status='interrupted',finished_at=?,error_code=409,error='Workspace generation changed; no queued request was replayed' WHERE status='queued' AND (binding IS NULL OR binding!=?)", (now, workspace_binding()))
            row = db.execute("SELECT j.id,j.payload,j.agent FROM routing_jobs j LEFT JOIN routing_agent_turns t ON t.agent=j.agent WHERE j.status='queued' ORDER BY coalesce(t.turn,0),j.sequence LIMIT 1").fetchone()
            if row:
                turn = db.execute("SELECT coalesce(max(turn),0)+1 FROM routing_agent_turns").fetchone()[0]
                db.execute("INSERT INTO routing_agent_turns VALUES (?,?) ON CONFLICT(agent) DO UPDATE SET turn=excluded.turn", (row[2], turn))
                db.execute("UPDATE routing_jobs SET status='running',started_at=? WHERE id=?", (now, row[0]))
        return row

    def _run(self):
        while not self.closed:
            row = self._claim()
            if row is None:
                self.wake.clear()
                if not self.wake.wait(1):
                    # Clear worker under the same lock used by submit/start;
                    # enqueue after the empty read must not strand a job.
                    with self.lock:
                        with self.journal.connection() as db:
                            pending = db.execute("SELECT 1 FROM routing_jobs WHERE status='queued' LIMIT 1").fetchone()
                        if not pending:
                            self.worker = None
                            return
                continue
            identifier, encoded, _ = row
            result, code, error = None, None, None
            status = "complete"
            try:
                with execution_control(self, identifier):
                    result = self.execute(json.loads(encoded))
                    checkpoint()
                if result.get("status") in TERMINAL - {"complete"}:
                    status, error = result["status"], result.get("error")
            except HTTPException as exc:
                status, code, error = "failed", exc.status_code, str(exc.detail)[:800]
            except Exception as exc:
                status, code, error = "failed", 500, "Decision failed: " + type(exc).__name__
            with self.journal.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                cancelled, deadline = db.execute("SELECT cancel_requested,deadline_at FROM routing_jobs WHERE id=?", (identifier,)).fetchone()
                if cancelled or time.time() >= deadline:
                    status, result, code = ("cancelled" if cancelled else "expired"), None, 409
                    error = "Decision cancelled" if cancelled else "Decision deadline expired"
                db.execute("UPDATE routing_jobs SET status=?,result=?,error_code=?,error=?,finished_at=? WHERE id=?", (status, canonical(result) if result else None, code, error, time.time(), identifier))
            self.wake.set()

    def wait(self, identifier):
        while True:
            value = self.get(identifier)
            if value["status"] in TERMINAL:
                if value["error_code"]:
                    raise HTTPException(value["error_code"], value["error"])
                return value["result"] or value
            time.sleep(0.025)
