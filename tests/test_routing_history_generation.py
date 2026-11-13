"""Historical disclosure revalidates rights without authorizing stale execution."""
from copy import deepcopy

import pytest

from test_routing_service import client as client, payload
from test_reasoning_workspace import workspace as workspace


def decision(client, monkeypatch):
    c, w = client
    monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_ID", "workspace-one")
    monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_GENERATION", "generation-one")
    body = payload(comparison_ids=["akm_two"], context={
        "workspace_id": "workspace-one", "owner_generation": "generation-one",
    })
    response = c.post("/routing/decide", json=body)
    assert response.status_code == 200, response.text
    before = deepcopy(w.journal.get("routing_decision", "routing-one"))
    monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_GENERATION", "generation-two")
    return body, before


def test_history_survives_restart_without_readmitting_old_work(client, monkeypatch):
    c, w = client
    body, before = decision(client, monkeypatch)
    assert c.post("/routing/disclosure", json={"ids": ["routing-one"]}).json()["states"]["routing-one"] is True
    historical = c.get("/routing/history").json()["decisions"][0]
    assert historical == before
    assert c.get("/routing/jobs/routing-one").json()["result"] == before
    for route in ("/routing/decide", "/routing/jobs"):
        assert c.post(route, json=body).status_code == 409
        assert c.post(route, json={**body, "request_id": "stale-new-id"}).status_code == 409
    assert w.journal.get("routing_decision", "routing-one") == before
    assert w.journal.get("routing_decision", "stale-new-id") is None


@pytest.mark.parametrize("change", ["workspace", "anchor", "comparison", "changed", "missing", "forgotten"])
def test_restart_does_not_restore_denied_or_changed_dependencies(client, monkeypatch, change):
    c, w = client
    _, before = decision(client, monkeypatch)
    if change == "workspace":
        monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_ID", "workspace-two")
    elif change in {"anchor", "comparison"}:
        denied = "akm_one" if change == "anchor" else "akm_two"
        monkeypatch.setattr(w, "event_policy", lambda event: {**event, "privacy_mode": "incognito"} if event["id"] == denied else event)
    else:
        reader = w.reader
        def altered(identifier):
            if identifier == "akm_two" and change == "missing":
                raise KeyError(identifier)
            if identifier == "akm_two" and change == "forgotten":
                return None  # Canonical owner no longer returns the forgotten record.
            record = reader(identifier)
            if identifier == "akm_two":
                record["summary"] = "Different retained evidence"
            return record
        monkeypatch.setattr(w, "reader", altered)
    assert c.post("/routing/disclosure", json={"ids": ["routing-one"]}).json()["states"]["routing-one"] is False
    assert c.get("/routing/history").json()["decisions"][0]["status"] == "withheld_or_unavailable"
    assert w.journal.get("routing_decision", "routing-one") == before


def curate(record, status):
    """What Akousmata's `set_consent` does to a record, and nothing more."""
    record.setdefault("provenance", {})["consent_status"] = status
    record["provenance"]["rights_note"] = "Operator attestation"
    record.setdefault("extensions", {}).setdefault("akousmata.app", {})["consent_set_by"] = "human"


@pytest.mark.parametrize("status", ["licensed", "owned", "restricted"])
def test_recording_rights_after_a_decision_does_not_read_as_changed_evidence(client, monkeypatch, status):
    """13 November 2026: consent recorded on a session's records, so their text could be
    published, withheld every decision of that session. A record marked restricted since still
    withholds them, and so does any other change."""
    c, w = client
    original = w.reader

    def reader(identifier):
        # As every stored record is: with its provenance, before any rights were recorded.
        return {**original(identifier), "provenance": {"origin": "file"}}

    monkeypatch.setattr(w, "reader", reader)
    _, before = decision(client, monkeypatch)

    def curated(identifier):
        record = reader(identifier)
        curate(record, status)
        return record

    monkeypatch.setattr(w, "reader", curated)
    disclosed = c.post("/routing/disclosure", json={"ids": ["routing-one"]}).json()["states"]["routing-one"]
    assert disclosed is (status != "restricted")
    assert w.journal.get("routing_decision", "routing-one") == before

    def curated_and_changed(identifier):
        record = curated(identifier)
        if identifier == "akm_two":
            record["summary"] = "Different retained evidence"
        return record

    monkeypatch.setattr(w, "reader", curated_and_changed)
    assert c.post("/routing/disclosure", json={"ids": ["routing-one"]}).json()["states"]["routing-one"] is False
