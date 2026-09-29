from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from oida.owner_journal import OwnerJournal
from oida.routing.costs import RoutingCosts, CostSettings, CostReconciliation
from oida.reasoning.specialist_context import project
from oida.reasoning.evidence import EvidencePacketBuilder
from oida.routing.typesafe import external_state
from test_routing_service import client as client, payload
from test_reasoning_workspace import workspace as workspace


@pytest.mark.parametrize("revocation", ["anchor", "comparison", "missing", "changed"])
def test_retained_decision_dependencies_fail_closed(client, monkeypatch, revocation):
    c, w = client
    original = w.reader
    def reader(identifier):
        record = original(identifier)
        if identifier == "akm_two":
            record["listening"]["test"]["payload"]["claim_summary"]["measured"][0]["statement"] = "PRIVATE_CANARY"
        return record
    monkeypatch.setattr(w, "reader", reader)
    body = payload(comparison_ids=["akm_two"])
    assert "PRIVATE_CANARY" in c.post("/routing/decide", json=body).text
    if revocation in {"anchor", "comparison"}:
        denied = "akm_one" if revocation == "anchor" else "akm_two"
        monkeypatch.setattr(w, "event_policy", lambda event: {**event, "privacy_mode": "incognito"} if event["id"] == denied else event)
    else:
        def changed(identifier):
            if identifier == "akm_two" and revocation == "missing":
                raise KeyError(identifier)
            value = reader(identifier)
            if identifier == "akm_two":
                value["summary"] = "Changed record"
            return value
        monkeypatch.setattr(w, "reader", changed)
    responses = [c.get("/routing/history"), c.get("/routing/jobs/routing-one"), c.post("/routing/decide", json=body), c.post("/routing/jobs", json=body)]
    assert all("PRIVATE_CANARY" not in response.text for response in responses)
    assert c.post("/routing/disclosure", json={"ids": ["routing-one"]}).json()["states"] == {"routing-one": False}
    assert "PRIVATE_CANARY" in str(w.journal.get("routing_decision", "routing-one"))  # Internal receipt unchanged.


def test_pending_poll_does_not_prepare_evidence(client, monkeypatch):
    c, w = client
    monkeypatch.setattr(w.routing_queue, "start", lambda: None)
    assert c.post("/routing/jobs", json=payload()).status_code == 202
    monkeypatch.setattr(w, "reader", lambda _: (_ for _ in ()).throw(AssertionError("Pending polls must be cheap")))
    for _ in range(32):
        value = c.get("/routing/jobs/routing-one").json()
        assert value["status"] == "queued" and "result" not in value


@pytest.mark.parametrize("waiters", [1, 3, 8, 32])
def test_pending_poll_load_uses_one_journal_read_per_poll(client, monkeypatch, waiters):
    from concurrent.futures import ThreadPoolExecutor
    import time
    c, w = client
    monkeypatch.setattr(w.routing_queue, "start", lambda: None)
    c.post("/routing/jobs", json=payload())
    connections = []
    original = w.journal.connection
    def connect():
        connections.append(1)
        return original()
    monkeypatch.setattr(w.journal, "connection", connect)
    monkeypatch.setattr(w, "reader", lambda _: (_ for _ in ()).throw(AssertionError("No context work while polling")))
    def poll(_):
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            assert c.get("/routing/jobs/routing-one").json()["status"] == "queued"
            samples.append((time.perf_counter() - start) * 1000)
        return samples
    cpu, wall = time.process_time(), time.perf_counter()
    with ThreadPoolExecutor(max_workers=waiters) as pool:
        samples = sorted(value for batch in pool.map(poll, range(waiters)) for value in batch)
    assert len(connections) == waiters * 5
    print(f"poll_load waiters={waiters} requests={len(samples)} sqlite_reads={len(connections)} p95_ms={samples[min(len(samples)-1,int(len(samples)*.95))]:.2f} cpu_ms={(time.process_time()-cpu)*1000:.2f} wall_ms={(time.perf_counter()-wall)*1000:.2f}")


def test_qualification_overall_deadline_and_status(client, monkeypatch):
    from types import SimpleNamespace
    from oida.routing.contracts import DecisionProposal
    from oida.routing.providers import context_digest
    import oida.routing.service as service
    c, w = client
    monkeypatch.setattr(w.reasoning, "secret_store", SimpleNamespace(get=lambda *args: "synthetic"))
    clock, deadlines = [0.0], []
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    class Slow:
        def __init__(self, *args): pass
        def decide(self, context, questions, settings):
            deadlines.append(settings.limits.deadline_seconds)
            clock[0] += 25
            move = context["candidates"][0]
            return DecisionProposal(context_sha256=context_digest(context), action=move["action"], candidate_id=move["id"], actual_model="jev-1.13.0")
    monkeypatch.setattr(service, "TypesafeDecisionProvider", Slow)
    assert c.post("/routing/qualify/typesafe").status_code == 409
    assert deadlines == [30, 20]
    status = c.get("/routing/qualify/typesafe").json()
    assert status["status"] == "failed" and "credential_fingerprint" not in status


def test_qualification_is_single_flight(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    import oida.routing.service as service
    c, w = client
    monkeypatch.setattr(w.reasoning, "secret_store", SimpleNamespace(get=lambda *args: "synthetic"))
    entered, release = Event(), Event()
    class Pending:
        def __init__(self, *args): pass
        def decide(self, *args):
            entered.set()
            assert release.wait(5)
            raise TimeoutError()
    monkeypatch.setattr(service, "TypesafeDecisionProvider", Pending)
    with ThreadPoolExecutor() as pool:
        first = pool.submit(c.post, "/routing/qualify/typesafe")
        assert entered.wait(5)
        try:
            assert c.get("/routing/qualify/typesafe").json()["status"] == "checking"
            assert c.post("/routing/qualify/typesafe").status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 409


@pytest.mark.parametrize("dependency", ["inquiry", "retrieved_memory"])
def test_reasoning_dependency_revocation_withholds_derived_decision(client, monkeypatch, dependency):
    import hashlib
    from oida.owner_journal import canonical
    from oida.reasoning_context import retained_event
    import oida.routing.archive as archive
    c, w = client
    current = w.reader("akm_two")
    event = w.event_policy(retained_event(current))
    permitted = [True]
    source = {"kind": "memory", "reference": "akm_two", "record_sha256": w.journal.record_digest(current), "permitted_event_sha256": hashlib.sha256(canonical(event).encode()).hexdigest()}
    selection = {"record_refs": ["akm_two"], "sha256": "selection"}
    monkeypatch.setattr(archive, "archive_view", lambda *args: {"view": {"state": "available" if permitted[0] else "withheld"}, "record": current, "events": {"akm_two": event}})
    monkeypatch.setattr(archive, "inquiry", lambda *args: {"sha256": "selection" if permitted[0] else "changed"})
    monkeypatch.setattr(w, "session", lambda _: {"turns": [{"answer": "DERIVED_PRIVATE_CANARY", "research": {"sources": [source] if dependency == "retrieved_memory" else []}}]})
    w.journal.save("reasoning_job", "reasoning-one", {"status": "complete", "record_id": "akm_one", "conversation_id": "session", "inquiry_selection": selection if dependency == "inquiry" else None})
    body = payload(context={"reasoning_job_ids": ["reasoning-one"]})
    assert "DERIVED_PRIVATE_CANARY" in c.post("/routing/decide", json=body).text
    permitted[0] = False
    for response in [c.get("/routing/history"), c.get("/routing/jobs/routing-one"), c.post("/routing/decide", json=body)]:
        assert "DERIVED_PRIVATE_CANARY" not in response.text


def test_qualification_restart_and_credential_change_fail_closed(workspace, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    import oida.routing.service as service
    from oida.routing.contracts import DecisionProposal
    from oida.routing.providers import context_digest
    workspace.journal.save("routing_qualification", "typesafe", {"status": "checking", "attempt_id": "old"})
    key = ["synthetic"]
    monkeypatch.setattr(workspace.reasoning, "secret_store", SimpleNamespace(get=lambda *args: key[0]))
    class Changed:
        def __init__(self, *args): pass
        def decide(self, context, questions, settings):
            key[0] = "changed"
            move = context["candidates"][0]
            return DecisionProposal(context_sha256=context_digest(context), action=move["action"], candidate_id=move["id"], actual_model="jev-1.13.0")
    monkeypatch.setattr(service, "TypesafeDecisionProvider", Changed)
    app = FastAPI()
    app.include_router(service.routing_router(workspace, lambda _: None))
    c = TestClient(app)
    assert c.get("/routing/qualify/typesafe").json()["status"] == "interrupted"
    assert c.post("/routing/qualify/typesafe").status_code == 409
    assert c.get("/routing/qualify/typesafe").json()["status"] == "failed"


def test_real_transport_has_an_absolute_body_deadline():
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    from threading import Thread
    import time
    from oida.routing.typesafe import _post_json
    class SlowBody(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(.1)
            except OSError:
                pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowBody)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            _post_json(f"http://127.0.0.1:{server.server_port}", "/fixture", "synthetic-only", {}, deadline_seconds=1.2)
        assert time.monotonic() - started < 2
    finally:
        server.shutdown()
        server.server_close()


def test_invoice_overrun_revokes_revision_even_after_correction(tmp_path):
    costs = RoutingCosts(OwnerJournal(tmp_path / "cost.sqlite"))
    price = {"provider_id": "fixture", "model_id": "fixture", "max_request_usd": .1,
             "basis": "Synthetic provider enforced ceiling", "revision": "v1",
             "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
             "provider_enforced_bound": True}
    costs.configure(CostSettings(prices=[price]))
    costs.reserve("first:0", "scope", "fixture", "fixture", 1)
    costs.settle("first:0")
    assert costs.reconcile("first:0", CostReconciliation(billed_usd=.2, receipt_reference="invoice:one"))["exceeds_basis"]
    costs.reconcile("first:0", CostReconciliation(billed_usd=.05, receipt_reference="invoice:corrected"))
    with pytest.raises(HTTPException, match="cost basis"):
        costs.reserve("second:0", "scope", "fixture", "fixture", 1)
    costs.configure(CostSettings(prices=[{**price, "revision": "v2", "max_request_usd": .3}]))
    assert costs.reserve("third:0", "scope", "fixture", "fixture", 1)["reserved_usd"] == .3


def test_specialists_survive_external_projection_without_locators():
    lanes = [{"status": "complete", "task": task, "evidence": {
        "deployment_id": "fixture", "result": {"status": "ok", **result}, "secret": "DO_NOT_COPY"}}
        for task, result in [("tag_events", {"labels": [{"label": "Rain", "score": .8}]}),
                             ("speech_quality", {"sig": 3.0, "bak": 2.8, "ovrl": 2.9}),
                             ("transcribe", {"text": "Look at /private/audio.wav"}),
                             ("track_beats", {"beats_seconds": [0, .5, 1]})]]
    observations = project(lanes)
    assert len(observations) == 4
    packet = EvidencePacketBuilder().build(event={"id": "fixture", "specialist_observations": observations},
        question="Choose route", include_transcript=True)
    rendered = external_state({"evidence": [item.model_dump(mode="json") for item in packet.items]})
    text = str(rendered)
    assert "Rain" in text and "3.0" in text and "beats_seconds" in text
    assert "DO_NOT_COPY" not in text and "/private/audio.wav" not in text
    assert "speech-domain" in text


def test_malformed_quality_and_structured_external_values_are_bounded():
    value = project([{"status": "complete", "task": "speech_quality", "evidence": {"result": {"sig": float("nan"), "bak": True, "ovrl": 100}}}])[0]
    assert set(value["result"]) == {"limitation"}
    state = external_state({"evidence": [{"value": {"task": "transcribe", "result": "malformed"}}] * 20})
    assert len(state["evidence"]) == 12 and state["evidence_omitted_count"] == 8
    assert all(len(row["text"]) <= 500 for row in state["evidence"])
