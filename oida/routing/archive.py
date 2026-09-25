"""Canonical read-only archive projection and reproducible inquiry selection."""

import hashlib
import re
import sqlite3
from copy import deepcopy

from fastapi import HTTPException

from oida.owner_journal import canonical
from oida.reasoning.evidence import covenant_blocks_untyped_prose, safe_external_text
from oida.reasoning_context import retained_event


def _resolvers(workspace):
    """The permission check and the forgetting lookup every archive read shares."""
    records, events = {}, {}

    def permitted(ref):
        try:
            record = workspace.reader(ref)
        except (FileNotFoundError, KeyError, ValueError):
            records[ref] = None
            return True
        except HTTPException as exc:
            if exc.status_code in {401, 403, 423}:
                return False
            if exc.status_code == 404:
                records[ref] = None
                return True
            raise
        if record is None:
            records[ref] = None
            return True
        if not isinstance(record, dict) or record.get("akousma_id") != ref:
            raise ValueError("Archive record identity does not match its reference")
        try:
            event = workspace.event_policy(retained_event(deepcopy(record)))
        except HTTPException as exc:
            if exc.status_code in {401, 403, 423}:
                return False
            raise
        if event.get("privacy_mode") == "incognito" or covenant_blocks_untyped_prose(event.get("covenant")):
            return False
        records[ref], events[ref] = record, event
        return True

    def receipt(ref):
        resolver = getattr(workspace, "forgetting_reader", None)
        if resolver:
            return resolver(ref)
        from akousmata_app.paths import open_store
        store = open_store()
        try:
            return store.forgotten(ref)
        finally:
            store.close()

    return records, events, permitted, receipt


def _archive_errors(call):
    try:
        return call()
    except HTTPException:
        raise
    except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
        raise HTTPException(503, "Archive storage is unavailable") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "Archive returned an invalid canonical record or receipt") from exc


def archive_view(workspace, identifier):
    from akousma.auditum_view import AUDITUM_VIEW_CONTRACT, FORGETTING_RECEIPT_CONTRACT, auditum_view

    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", identifier):
        raise HTTPException(400, "Invalid Auditum reference")
    records, events, permitted, receipt = _resolvers(workspace)
    view = _archive_errors(lambda: auditum_view(identifier, read_record=lambda ref: records.get(ref), read_receipt=receipt, can_read=permitted, supported_contracts=[AUDITUM_VIEW_CONTRACT, FORGETTING_RECEIPT_CONTRACT]))
    return {"view": view, "record": records.get(identifier) if view["state"] == "available" else None, "events": events, "records": records}


def archive_state(workspace, identifier):
    """The anchor's state and record, resolved exactly as ``auditum_view`` resolves its anchor,
    without resolving its references.

    A record's own state never depends on its relations; ``auditum_view`` resolves them only
    to list them. GERM's digest check needs the state alone, and resolving every relation of
    every memory a render drew on (policy and a deep copy per related record) took about 2.3 s
    for 113 records (25 September 2026).
    """
    from akousma import validation_errors
    from akousma.auditum_view import forgetting_receipt_view

    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", identifier):
        raise HTTPException(400, "Invalid Auditum reference")
    records, _events, permitted, receipt = _resolvers(workspace)

    def resolve():
        allowed = permitted(identifier)
        if type(allowed) is not bool:
            raise TypeError("can_read must return a boolean")
        if not allowed:
            return "withheld", None
        record = records.get(identifier)
        if record is not None:
            if validation_errors(record) or record["akousma_id"] != identifier:
                raise ValueError("Invalid or mismatched resolved record")
            return "available", record
        found = receipt(identifier)
        if found is None:
            return "unavailable", None
        forgetting_receipt_view(found, identifier)
        return "forgotten", None

    state, record = _archive_errors(resolve)
    return {"state": state, "record": record}


class _Pure:
    """What a stored record yields independently of any policy, keyed by the SHA-256 of its
    stored text: whether it is a valid Auditum for its id, its retained event and its canonical
    digest. Each is a pure function of those bytes, so a memo keyed by them is exact; nothing
    about permission is kept. A memory render drew on up to 110 records of about 300 KB each,
    and parsing and re-deriving them on every playback request cost seconds (25 September)."""

    def __init__(self, limit=1024):
        import threading
        from collections import OrderedDict

        self.limit, self.items, self.lock = limit, OrderedDict(), threading.Lock()

    def get(self, identifier, raw):
        key = hashlib.sha256(raw.encode()).hexdigest()
        with self.lock:
            hit = self.items.get(key)
            if hit is not None and hit[0] == identifier:
                self.items.move_to_end(key)
                return hit[1]
        import json

        from akousma import validation_errors

        from oida.routing.service import record_digest

        record = json.loads(raw)
        value = None
        if isinstance(record, dict) and record.get("auditum"):
            identity = record.get("akousma_id") == identifier
            value = {
                "identity": identity,
                "event": retained_event(record) if identity else None,
                "schema_ok": not validation_errors(record),
                "digest": record_digest(record),
            }
        with self.lock:
            self.items[key] = (identifier, value)
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        return value


_PURE = _Pure()


def archive_states(workspace, identifiers):
    """State and digest for many anchors, resolved as ``archive_state`` resolves one.

    With a workspace that reads the canonical store directly, the records are read in one query
    and their pure derivations come from ``_PURE``; policy (``event_policy``, the covenant check,
    forgetting receipts) is applied afresh to every one. Any other workspace is read one record
    at a time through ``archive_state``.
    """
    from oida.routing.service import record_digest

    raw_records = getattr(workspace, "raw_records", None)
    if raw_records is None:
        out = {}
        for identifier in identifiers:
            value = archive_state(workspace, identifier)
            out[identifier] = {"state": value["state"], "record_sha256": record_digest(value["record"])}
        return out
    from akousma.auditum_view import forgetting_receipt_view

    _records, _events, _permitted, receipt = _resolvers(workspace)

    def resolve(identifier, raw):
        # The same order as archive_state: identity, then policy, then schema validation.
        pure = _PURE.get(identifier, raw) if raw is not None else None
        if pure is not None:
            if not pure["identity"]:
                raise ValueError("Archive record identity does not match its reference")
            try:
                event = workspace.event_policy(deepcopy(pure["event"]))
            except HTTPException as exc:
                if exc.status_code in {401, 403, 423}:
                    return "withheld", None
                raise
            if event.get("privacy_mode") == "incognito" or covenant_blocks_untyped_prose(event.get("covenant")):
                return "withheld", None
            if not pure["schema_ok"]:
                raise ValueError("Invalid or mismatched resolved record")
            return "available", pure["digest"]
        found = receipt(identifier)
        if found is None:
            return "unavailable", None
        forgetting_receipt_view(found, identifier)
        return "forgotten", None

    raws = _archive_errors(lambda: raw_records(list(identifiers)))
    out = {}
    for identifier in identifiers:
        state, digest = _archive_errors(lambda: resolve(identifier, raws.get(identifier)))
        out[identifier] = {"state": state, "record_sha256": digest}
    return out


def inquiry(workspace, identifier, selected_refs=None):
    projection = archive_view(workspace, identifier)
    view = projection["view"]
    if view["state"] != "available":
        raise HTTPException(423 if view["state"] == "withheld" else 404, "Inquiry anchor is " + view["state"])
    available = {ref["record_ref"] for ref in view.get("references", []) if ref["state"] == "available"}
    selected = list(dict.fromkeys(selected_refs or []))
    if len(selected) > 8 or not set(selected) <= available:
        raise HTTPException(409, "Inquiry references must be current, readable relations of the anchor")
    records, sources, snapshots = projection["records"], [], []
    for ref in [identifier, *selected]:
        event = projection["events"][ref]
        text = safe_external_text(event.get("aggregate", {}).get("short_summary"), limit=1200) or "Retained acoustic account"
        snapshots.append({"record_ref": ref, "record_sha256": hashlib.sha256(canonical(records[ref]).encode()).hexdigest(), "permitted_event_sha256": hashlib.sha256(canonical(event).encode()).hexdigest()})
        sources.append({"kind": "memory", "reference": ref, "title": text[:160], "text": text, "basis": "Explicitly selected canonical Auditum relation; retained interpretation, not a new measurement"})
    words = []
    for source in sources:
        for word in re.findall(r"[^\W_]{3,}", source["text"].lower()):
            if word not in words:
                words.append(word)
    query = " ".join(words[:24])[:240]
    value = {"contract": "oida/inquiry-selection/v1", "anchor": identifier, "record_refs": selected, "snapshots": snapshots, "sources": sources, "selected_terms": words[:24], "web_query": query}
    value["sha256"] = hashlib.sha256(canonical(value).encode()).hexdigest()
    value["available_relations"] = [{"record_ref": ref["record_ref"], "state": ref["state"]} for ref in view.get("references", [])][:16]
    return value
