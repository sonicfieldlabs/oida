"""Cooperative cancellation with a durable, minimal receipt and commit fence."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import re

from fastapi import HTTPException
from oida.contracts import now_iso

_current = ContextVar('oida_operation', default=None)


class OperationCancelled(HTTPException):
    def __init__(self):
        super().__init__(409, 'operation cancelled; late output discarded')


class Control:
    def __init__(self, *, event=None, lock=None, recheck=None, on_seal=None, identifier=None):
        self.identifier = identifier
        self.event = event if event is not None else threading.Event()
        self.lock = lock if lock is not None else threading.RLock()
        self.recheck = recheck
        self.on_seal = on_seal
        self.sealed = False

    def check(self, *, seal=False):
        with self.lock:
            if self.sealed:
                return
            if self.event.is_set():
                raise OperationCancelled()
            if self.recheck:
                self.recheck()
            if seal and not self.sealed:
                if self.on_seal:
                    self.on_seal()
                self.sealed = True

    def cancel(self):
        with self.lock:
            if self.sealed:
                return False
            self.event.set()
            return True


@contextmanager
def controlled(control):
    token = _current.set(control)
    try:
        control.check()
        yield
    finally:
        _current.reset(token)


def checkpoint(*, seal=False):
    control = _current.get()
    if control is not None:
        control.check(seal=seal)


def current_operation_id():
    control = _current.get()
    return control.identifier if control is not None else None


class Operations:
    def __init__(self, journal):
        self.journal = journal
        self.lock = threading.RLock()
        self.active = {}
        cursor = 0
        pending = []
        boundary = None
        while True:
            page = journal.snapshots(after=cursor, producer_id=journal.producer_id, at=boundary)
            boundary = page["high_water_sequence"]
            for entry in page['snapshots']:
                if entry['kind'] == 'operation' and entry['payload']['status'] in ('running', 'committing'):
                    pending.append(entry['subject_id'])
            if not page['has_more']:
                break
            cursor = page['next_sequence']
        for identifier in pending:
            self.save(identifier, 'interrupted')

    def save(self, identifier, status, **fields):
        value = dict(contract='oida/operation-receipt/v1', id=identifier,
                     status=status, updated_at=now_iso(), **fields)
        self.journal.save('operation', identifier, value)
        return value

    def cancel(self, identifier):
        with self.lock:
            control = self.active.get(identifier)
            if control is None:
                return (self.journal.get('operation', identifier) or {}).get('status') == 'cancelled'
            if not control.cancel():
                return False
            self.save(identifier, 'cancelled')
            return True

    def close(self):
        with self.lock:
            for identifier in list(self.active):
                self.cancel(identifier)

    def run(self, identifier, callback):
        if identifier is None:
            return callback()
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', identifier):
            raise HTTPException(400, 'invalid operation id')
        with self.lock:
            existing = self.journal.get('operation', identifier)
            if existing is not None:
                raise HTTPException(409, {'detail': 'operation already exists; inspect receipt', 'receipt': existing})
            control = Control(lock=self.lock, on_seal=lambda: self.save(identifier, 'committing'), identifier=identifier)
            self.active[identifier] = control
            self.save(identifier, 'running')
        try:
            with controlled(control):
                result = callback()
                checkpoint(seal=True)
            links = {k: result[k] for k in ('akousma_id', 'source_sha256', 'output_sha256', 'derivation_ref') if result.get(k)}
            event = result.get('listening_event') or {}
            if event.get('id'):
                links['event_id'] = event['id']
            self.save(identifier, 'refused' if result.get('outcome') in ('refused', 'withheld') else 'complete', **links)
            return result
        except OperationCancelled:
            self.save(identifier, 'cancelled')
            raise
        except Exception as exc:
            if control.event.is_set():
                self.save(identifier, "cancelled")
                raise OperationCancelled() from exc
            self.save(identifier, 'refused' if isinstance(exc, HTTPException) and exc.status_code in (400,409,423) else 'failed')
            raise
        finally:
            with self.lock:
                self.active.pop(identifier, None)
