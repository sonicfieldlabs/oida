"""Canonical read-only archive projection and reproducible inquiry selection."""

import hashlib
import re
import sqlite3
from copy import deepcopy

from fastapi import HTTPException

from oida.owner_journal import canonical
from oida.reasoning.evidence import covenant_blocks_untyped_prose, safe_external_text
from oida.reasoning_context import retained_event


def archive_view(workspace, identifier):
    from akousma.auditum_view import AUDITUM_VIEW_CONTRACT, FORGETTING_RECEIPT_CONTRACT, auditum_view

    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", identifier):
        raise HTTPException(400, "Invalid Auditum reference")
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

    try:
        view = auditum_view(identifier, read_record=lambda ref: records.get(ref), read_receipt=receipt, can_read=permitted, supported_contracts=[AUDITUM_VIEW_CONTRACT, FORGETTING_RECEIPT_CONTRACT])
    except HTTPException:
        raise
    except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
        raise HTTPException(503, "Archive storage is unavailable") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "Archive returned an invalid canonical record or receipt") from exc
    return {"view": view, "record": records.get(identifier) if view["state"] == "available" else None, "events": events, "records": records}


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
