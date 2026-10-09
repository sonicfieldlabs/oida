"""Focused engineering checks for queue ordering, cancellation and durable budgets."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from oida.owner_journal import OwnerJournal
from oida.routing.jobs import DecisionQueue
from oida.routing.costs import CostSettings, RoutingCosts, CostReconciliation


def payload(identifier, agent="a"):
    return {"request_id": identifier, "chain_id": "chain", "context": {"agent_id": agent}, "settings": {"limits": {"deadline_seconds": 10}}}


def test_agent_turns_are_fair_and_cancelled_queue_entries_never_execute(tmp_path):
    started, release = threading.Event(), threading.Event()
    order = []
    def execute(request):
        order.append(request["request_id"])
        if len(order) == 1:
            started.set()
            release.wait(3)
        return {"status": "complete", "id": request["request_id"]}
    queue = DecisionQueue(OwnerJournal(tmp_path / "owner.sqlite"), execute)
    try:
        queue.submit(payload("a1"))
        assert started.wait(2)
        queue.submit(payload("a2"))
        queue.submit(payload("cancelled"))
        queue.submit(payload("b1", "b"))
        queue.submit(payload("c1", "c"))
        assert queue.cancel("cancelled")["status"] == "cancelled"
        release.set()
        assert queue.wait("a2")["status"] == "complete"
        assert order == ["a1", "b1", "c1", "a2"]
        queue.submit(payload("a1"))
        assert order.count("a1") == 1
        with pytest.raises(HTTPException) as conflict:
            queue.submit(payload("a1", "another-agent"))
        assert conflict.value.status_code == 409
    finally:
        release.set()
        queue.close()


def test_restart_resumes_unstarted_work_but_never_replays_running_call(tmp_path):
    journal = OwnerJournal(tmp_path / "owner.sqlite")
    first = DecisionQueue(journal, lambda request: pytest.fail("not started"))
    first.start = lambda: None
    first.submit(payload("queued"))
    first.submit(payload("uncertain", "b"))
    with journal.connection() as db:
        db.execute("UPDATE routing_jobs SET status='running' WHERE id='uncertain'")
    calls = []
    restarted = DecisionQueue(journal, lambda request: calls.append(request["request_id"]) or {"status": "complete"})
    try:
        restarted.start()
        restarted.wait("queued")
        assert calls == ["queued"]
        assert restarted.get("uncertain")["status"] == "interrupted"
    finally:
        restarted.close()


def test_running_cancellation_discards_late_output(tmp_path):
    started, release = threading.Event(), threading.Event()
    def execute(request):
        started.set()
        release.wait(2)
        return {"status": "complete", "action": "generate"}
    queue = DecisionQueue(OwnerJournal(tmp_path / "owner.sqlite"), execute)
    try:
        queue.submit(payload("one"))
        assert started.wait(1)
        queue.cancel("one")
        release.set()
        with pytest.raises(HTTPException, match="cancelled"):
            queue.wait("one")
        assert queue.get("one")["result"] is None
    finally:
        release.set()
        queue.close()


def test_queue_capacity_and_expired_work_never_call_provider(tmp_path):
    journal = OwnerJournal(tmp_path / "owner.sqlite")
    queue = DecisionQueue(journal, lambda request: pytest.fail("expired work executed"))
    queue.start = lambda: None
    try:
        for agent in ("a", "b", "c", "d"):
            for index in range(8):
                queue.submit(payload(f"{agent}{index}", agent))
        with pytest.raises(HTTPException) as full:
            queue.submit(payload("overflow", "e"))
        assert full.value.status_code == 429
        assert full.value.headers["Retry-After"] == "1"
        with journal.connection() as db:
            db.execute("UPDATE routing_jobs SET deadline_at=0 WHERE id='a0'")
        assert queue._claim()[0] != "a0"
        assert queue.get("a0")["status"] == "expired"
        with pytest.raises(HTTPException, match="expired"):
            queue.wait("a0")
    finally:
        queue.close()


def test_cost_reservations_share_scope_and_keep_unknown_charges(tmp_path):
    journal = OwnerJournal(tmp_path / "owner.sqlite")
    costs = RoutingCosts(journal)
    costs.configure(CostSettings(prices=[{"provider_id": "fixture", "model_id": "pinned", "max_request_usd": 0.1, "basis": "Synthetic provider-enforced ceiling", "revision": "fixture-v1", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "provider_enforced_bound": True}]))
    def reserve(index):
        try:
            return costs.reserve(str(index), "swarm", "fixture", "pinned", .2)
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(reserve, range(3)))
    assert results.count(409) == 1
    admitted = [row for row in results if isinstance(row, dict)]
    for row in admitted:
        assert costs.settle(row["id"])["status"] == "unresolved"
    assert costs.state()["committed_usd"] == pytest.approx(.2)
    # Omitting a later request cap cannot discard the shared scope's ceiling.
    with pytest.raises(HTTPException):
        costs.reserve("later", "swarm", "fixture", "pinned", None)
    costs.reconcile(admitted[0]["id"], CostReconciliation(billed_usd=0, receipt_reference="fixture provider confirmed no charge"))
    costs.reserve("after-reconciliation", "swarm", "fixture", "pinned", None)


def test_generation_fence_expires_old_queued_work(tmp_path, monkeypatch):
    journal = OwnerJournal(tmp_path / "owner.sqlite")
    queue = DecisionQueue(journal, lambda request: pytest.fail("stale workspace request"))
    queue.start = lambda: None
    monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_GENERATION", "old")
    queue.submit(payload("one"))
    monkeypatch.setenv("LISTENINGSTACK_WORKSPACE_GENERATION", "new")
    queue._run()
    assert queue.get("one")["status"] == "interrupted"


def test_unpriced_active_attempt_cannot_be_reconciled(tmp_path):
    journal = OwnerJournal(tmp_path / "owner.sqlite")
    queue = DecisionQueue(journal, lambda request: {})
    queue.start = lambda: None
    queue.submit(payload("active"))
    costs = RoutingCosts(journal)
    costs.reserve("active:0", "scope", "external", "model", None)
    receipt = CostReconciliation(billed_usd=0, receipt_reference="invoice:123")
    with pytest.raises(HTTPException, match="active provider"):
        costs.reconcile("active:0", receipt)
    queue.cancel("active")
    assert costs.reconcile("active:0", receipt)["status"] == "reconciled"


def test_canonical_inquiry_binds_relations_and_current_permissions():
    from types import SimpleNamespace
    import akousma
    from oida.routing.archive import archive_view, inquiry
    def account():
        return akousma.auditum(route_decisions=[akousma.route_decision("fixture-stop", gate="input", outcome="abstain", subject="synthetic fixture", reason="No inference performed", actor="test")])
    parent = akousma.new_akousma(audio={"asset_id": "fixture-parent"}, originating_app="oida", summary="Soft irregular impacts", auditum=account())
    child = akousma.new_akousma(audio={"asset_id": "fixture-child"}, originating_app="oida", summary="Bright repeating impacts", auditum=account(), parent_akousma_ids=[parent["akousma_id"]])
    records = {r["akousma_id"]: r for r in (parent, child)}
    withheld = set()
    def policy(event):
        if event["id"] in withheld:
            raise HTTPException(423, "withheld")
        return event
    workspace = SimpleNamespace(reader=records.get, event_policy=policy, forgetting_reader=lambda ref: None)
    selection = inquiry(workspace, child["akousma_id"], [parent["akousma_id"]])
    assert selection["record_refs"] == [parent["akousma_id"]]
    assert "impacts" in selection["selected_terms"]
    records[parent["akousma_id"]]["summary"] = "Updated account"
    assert inquiry(workspace, child["akousma_id"], [parent["akousma_id"]])["sha256"] != selection["sha256"]
    withheld.add(parent["akousma_id"])
    assert archive_view(workspace, child["akousma_id"])["view"]["references"][0]["state"] == "withheld"
    with pytest.raises(HTTPException, match="readable relations"):
        inquiry(workspace, child["akousma_id"], [parent["akousma_id"]])


def test_archive_state_is_the_views_anchor_state_without_resolving_references():
    from types import SimpleNamespace
    import akousma
    from oida.routing.archive import archive_state, archive_view

    def account():
        return akousma.auditum(route_decisions=[akousma.route_decision("fixture-stop", gate="input", outcome="abstain", subject="synthetic fixture", reason="No inference performed", actor="test")])

    parent = akousma.new_akousma(audio={"asset_id": "p"}, originating_app="oida", summary="Soft impacts", auditum=account())
    child = akousma.new_akousma(audio={"asset_id": "c"}, originating_app="oida", summary="Bright impacts", auditum=account(), parent_akousma_ids=[parent["akousma_id"]])
    records = {r["akousma_id"]: r for r in (parent, child)}
    withheld, reads = set(), []

    def reader(ref):
        reads.append(ref)
        return records.get(ref)

    def policy(event):
        if event["id"] in withheld:
            raise HTTPException(423, "withheld")
        return event

    workspace = SimpleNamespace(reader=reader, event_policy=policy, forgetting_reader=lambda ref: None)
    for case in ("available", "withheld", "unavailable"):
        if case == "withheld":
            withheld.add(child["akousma_id"])
        if case == "unavailable":
            withheld.clear()
            records.pop(child["akousma_id"])
        reads.clear()
        state = archive_state(workspace, child["akousma_id"])
        assert reads == [child["akousma_id"]], "no reference is read"
        view = archive_view(workspace, child["akousma_id"])
        assert state["state"] == view["view"]["state"] == case
        assert state["record"] == view["record"]


def test_batch_archive_states_match_archive_state_and_apply_policy_every_time():
    import json
    from types import SimpleNamespace
    import akousma
    from oida.routing import archive
    from oida.routing.service import record_digest

    def account():
        return akousma.auditum(route_decisions=[akousma.route_decision("fixture-stop", gate="input", outcome="abstain", subject="synthetic fixture", reason="No inference performed", actor="test")])

    a = akousma.new_akousma(audio={"asset_id": "a"}, originating_app="oida", summary="Soft impacts", auditum=account())
    b = akousma.new_akousma(audio={"asset_id": "b"}, originating_app="oida", summary="Bright impacts", auditum=account())
    records = {r["akousma_id"]: r for r in (a, b)}
    withheld, policy_calls = set(), []

    def policy(event):
        policy_calls.append(event["id"])
        if event["id"] in withheld:
            raise HTTPException(423, "withheld")
        return event

    plain = SimpleNamespace(reader=records.get, event_policy=policy, forgetting_reader=lambda ref: None)
    batch = SimpleNamespace(reader=records.get, event_policy=policy, forgetting_reader=lambda ref: None,
                            raw_records=lambda ids: {i: (json.dumps(records[i]) if i in records else None) for i in ids})
    ids = [a["akousma_id"], b["akousma_id"], "akm_missing"]
    for round_ in range(3):
        if round_ == 1:
            withheld.add(b["akousma_id"])
        if round_ == 2:
            records[a["akousma_id"]]["summary"] = "Changed account"
        expected = {i: {"state": archive.archive_state(plain, i)["state"],
                        "record_sha256": record_digest(archive.archive_state(plain, i)["record"])} for i in ids}
        policy_calls.clear()
        assert archive.archive_states(batch, ids) == expected
        assert sorted(policy_calls) == sorted(ids[:2]), "policy runs on every record, every request"
    assert expected[a["akousma_id"]]["record_sha256"] == record_digest(records[a["akousma_id"]])
