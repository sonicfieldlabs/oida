"""Bounded FIFO dispatch over the existing acquisition boundary; no model tuning."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone

from oida.contracts import now_iso


class QueueFull(ValueError):
    pass


class SourceScheduler:
    def __init__(self, receipts, execute, operation_lock, capacity=8):
        if not 1 <= capacity <= 32:
            raise ValueError("source queue capacity must be between 1 and 32")
        self.receipts = receipts
        self.execute = execute
        self.operation_lock = operation_lock
        self.capacity = capacity
        self.condition = threading.Condition()
        self.pending = deque()
        self.running = None
        self.worker = None
        self.monitor = None
        self.closed = False

    def read(self, identifier):
        return self.receipts.get(identifier)

    def terminal_queued(self, identifier, status, reason):
        with self.receipts.lock:
            item = self.read(identifier)
            if item["status"] != "queued":
                return False
            item.update(status=status, reason=reason, finished_at=now_iso())
            self.receipts.save(item)
            return True

    def submit(self, source_id, request):
        with self.condition:
            if self.closed:
                raise QueueFull("source scheduler is shutting down")
            self.expire()
            if len(self.pending) >= self.capacity:
                raise QueueFull("source queue is full")
            identifier = request.acquisition_id
            deadline = time.time() + request.expires_in_seconds
            item = dict(
                contract="oida/acquisition-receipt/v1",
                id=identifier,
                source_id=source_id,
                status="queued",
                queued_at=now_iso(),
                expires_at=datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
                expiry_basis="must start listening before this deadline",
                requested_seconds=request.seconds,
            )
            with self.receipts.lock:
                if self.receipts.get(identifier) is not None:
                    raise ValueError("acquisition id already exists")
                self.receipts.save(item)
            self.pending.append((source_id, request, deadline))
            if self.worker is None:
                self.worker = threading.Thread(
                    target=self.dispatch, name="oida-source-dispatch", daemon=True
                )
                self.worker.start()
            if self.monitor is None:
                self.monitor = threading.Thread(
                    target=self.watch, name="oida-source-expiry", daemon=True
                )
                self.monitor.start()
            self.condition.notify_all()
            return item

    def expire(self):
        now = time.time()
        remaining = deque()
        for job in self.pending:
            if now >= job[2]:
                self.terminal_queued(
                    job[1].acquisition_id, "expired", "deadline elapsed in queue"
                )
            else:
                remaining.append(job)
        self.pending = remaining
        if self.running and now >= self.running[2]:
            identifier = self.running[1].acquisition_id
            if not self.terminal_queued(
                identifier, "expired", "deadline elapsed waiting for acquisition"
            ):
                # Queue deadline still means start-listening deadline, not model runtime.
                if self.read(identifier)["status"] == "acquiring":
                    self.receipts.cancel(identifier)

    def watch(self):
        with self.condition:
            try:
                while self.pending or self.running:
                    self.expire()
                    self.condition.wait(timeout=0.05)
            finally:
                self.monitor = None

    def dispatch(self):
        try:
            while True:
                with self.condition:
                    self.expire()
                    if not self.pending:
                        self.condition.notify_all()
                        return
                    job = self.pending.popleft()
                    self.running = job
                source_id, request, deadline = job
                acquired = False
                try:
                    while not acquired:
                        if self.read(request.acquisition_id)["status"] != "queued":
                            break
                        if time.time() >= deadline:
                            self.terminal_queued(
                                request.acquisition_id,
                                "expired",
                                "deadline elapsed waiting for acquisition",
                            )
                            break
                        acquired = self.operation_lock.acquire(timeout=0.05)
                    if (
                        acquired
                        and self.read(request.acquisition_id)["status"] == "queued"
                    ):
                        self.execute(source_id, request, deadline)
                except Exception as exc:
                    # Existing acquisition execution records failures once it starts.
                    status = (
                        "refused"
                        if getattr(exc, "status_code", 0) in {400, 404, 423}
                        else "failed"
                    )
                    self.terminal_queued(
                        request.acquisition_id,
                        status,
                        "scheduled source could not start",
                    )
                finally:
                    if acquired:
                        self.operation_lock.release()
                    with self.condition:
                        self.running = None
                        self.condition.notify_all()
        finally:
            with self.condition:
                self.worker = None
                if self.pending and not self.closed:
                    self.worker = threading.Thread(
                        target=self.dispatch, name="oida-source-dispatch", daemon=True
                    )
                    self.worker.start()
                self.condition.notify_all()

    def cancel(self, identifier):
        with self.condition:
            for job in list(self.pending):
                if job[1].acquisition_id == identifier:
                    self.pending.remove(job)
                    result = self.terminal_queued(
                        identifier, "cancelled", "cancelled while queued"
                    )
                    self.condition.notify_all()
                    return result
            if self.running and self.running[1].acquisition_id == identifier:
                if self.terminal_queued(
                    identifier, "cancelled", "cancelled before acquisition"
                ):
                    return True
            return self.receipts.cancel(identifier)

    def status(self):
        with self.condition:
            self.expire()
            with self.receipts.lock:
                executing = next(iter(self.receipts.active), None)
            return dict(
                capacity=self.capacity,
                queued=len(self.pending),
                active_id=executing,
                reserved_job_id=self.running[1].acquisition_id
                if self.running
                else None,
                accepting=not self.closed,
                concurrency=1,
                model_residency="managed by existing engine configuration; scheduler does not tune it",
            )

    def close(self):
        with self.condition:
            self.closed = True
            for job in self.pending:
                self.terminal_queued(
                    job[1].acquisition_id,
                    "cancelled",
                    "owner shutdown before acquisition",
                )
            self.pending.clear()
            if self.running:
                identifier = self.running[1].acquisition_id
                if not self.terminal_queued(
                    identifier, "cancelled", "owner shutdown before acquisition"
                ):
                    self.receipts.cancel(identifier)
            # Direct synchronous captures share the owner and must stop too.
            with self.receipts.lock:
                active_ids = list(self.receipts.active)
            for identifier in active_ids:
                self.receipts.cancel(identifier)
            self.condition.notify_all()
