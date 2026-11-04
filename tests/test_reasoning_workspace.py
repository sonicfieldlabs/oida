"""Owner reasoning: durable separation, exactly-once arrivals, bounded context."""

from copy import deepcopy

import pytest
from fastapi import HTTPException

from oida.owner_journal import OwnerJournal, canonical
from oida.operation_control import Operations
from oida.reasoning_workspace import Workspace, Ask, Automatic
from oida.reasoning_context import retained_event, retrieve
from oida.reasoning.evidence import EvidencePacketBuilder
from test_reasoning_orchestrator import _service


@pytest.fixture
def workspace(tmp_path):
    service = _service(str(tmp_path / "service"))
    journal = OwnerJournal(tmp_path / "journal.sqlite")
    records = {
        identifier: dict(
            akousma_id=identifier,
            auditum={"contract": "earworm/auditum/v2"},
            summary="A low mechanical hum",
            audio={"path": "/private/raw.wav"},
            listening={
                "test": {
                    "payload": {
                        "claim_summary": {
                            "measured": [{"statement": "RMS -24 dBFS", "source": "dsp"}]
                        }
                    }
                }
            },
        )
        for identifier in ("akm_one", "akm_two", "akm_old")
    }
    originals = deepcopy(records)
    owner = Workspace(
        journal,
        service.conversations,
        service,
        service.settings_store,
        Operations(journal),
        lambda event: event,
        reader=lambda id: deepcopy(records[id]),
        retriever=lambda *args: ([], [], "test query"),
    )
    yield owner
    owner.close()
    assert records == originals  # Canonical data never receives reasoning writes.


def test_session_followup_and_idempotence_are_separate(workspace):
    w = workspace
    req = Ask(request_id="one", record_id="akm_one", question="What was heard?")
    assert w.enqueue(req)["status"] == "queued"
    assert w.enqueue(req)["id"] == "one"
    with pytest.raises(HTTPException, match="409"):
        w.enqueue(req.model_copy(update={"question": "Different question"}))
    assert w.execute_one()
    job = w.journal.get("reasoning_job", "one")
    assert job["status"] == "complete", job
    session = w.session(job["conversation_id"])
    assert session["record_id"] == "akm_one" and len(session["turns"]) == 1
    assert session["turns"][0]["research"]["record_sha256"]
    assert "/private/raw.wav" not in canonical(session)
    w.enqueue(
        Ask(
            request_id="two",
            record_id="akm_one",
            question="Explain further",
            conversation_id=session["id"],
        )
    )
    w.execute_one()
    assert len(w.session(session["id"])["turns"]) == 2
    with pytest.raises(HTTPException, match="409"):
        w.enqueue(
            Ask(
                record_id="akm_two",
                question="wrong anchor",
                conversation_id=session["id"],
            )
        )


def test_long_request_ids_have_independent_execution_and_cancellation(workspace):
    import threading

    w = workspace
    first, second = "a" * 73 + "first", "a" * 73 + "second"
    for identifier in (first, second):
        w.enqueue(Ask(request_id=identifier, record_id="akm_one", question="Explain"))
    one = w.journal.get("reasoning_job", first)["operation_id"]
    two = w.journal.get("reasoning_job", second)["operation_id"]
    assert one != two and max(len(one), len(two)) <= 80
    entered, release = threading.Event(), threading.Event()
    errors = []

    def run():
        try:
            def wait():
                entered.set()
                assert release.wait(5)
                return {}
            w.operations.run(one, wait)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(5)
    try:
        assert w.cancel_job(second)["status"] == "cancelled"
        assert not w.operations.active[one].event.is_set()
    finally:
        release.set()
        worker.join(5)
    assert not errors and w.journal.get("operation", one)["status"] == "complete"


def test_legacy_queued_job_gets_new_identity_and_does_not_reuse_truncated_receipt(workspace):
    w = workspace
    identifier = "b" * 80
    w.enqueue(Ask(request_id=identifier, record_id="akm_one", question="Explain"))
    job = w.journal.get("reasoning_job", identifier)
    del job["operation_id"]
    w.save_job(job)
    legacy = "reason_" + identifier[:73]
    w.operations.save(legacy, "complete")
    assert w.execute_one()
    job = w.journal.get("reasoning_job", identifier)
    assert job["status"] == "complete" and job["operation_id"] != legacy
    assert w.journal.get("operation", legacy)["status"] == "complete"


def test_owner_abandonment_prevents_late_admission_without_replaying_or_cancelling(workspace):
    w = workspace
    receipt = w.abandon_submission("lost")
    assert receipt["status"] == "not_admitted" and receipt["automatic_replay"] is False
    assert w.abandon_submission("lost") == receipt
    with pytest.raises(HTTPException, match="explicitly abandoned"):
        w.enqueue(Ask(request_id="lost", record_id="akm_one", question="Late arrival"))
    assert w.journal.get("reasoning_job", "lost") is None
    job = w.enqueue(Ask(request_id="accepted", record_id="akm_one", question="Already accepted"))
    assert w.abandon_submission("accepted")["status"] == "queued"
    assert w.journal.get("reasoning_abandonment", job["id"]) is None
    assert w.execute_one()


def test_conversation_anchor_filter_precedes_private_source_authorization(tmp_path):
    import json
    from oida.conversation import ConversationStore

    calls = []
    root = tmp_path / "sessions"
    root.mkdir()
    for identifier in ("wanted", "other"):
        (root / (identifier + ".json")).write_text(json.dumps(dict(id=identifier, event_id=identifier, turns=[dict(research=dict(sources=[dict(kind="local_passage")]))])))
    store = ConversationStore(root, local_passage_policy=lambda value, **kw: calls.append(value["id"]))
    assert len(store.list(event_id="wanted")) == 1
    assert calls == ["wanted"]


def test_auto_future_arrivals_only_no_feedback_or_replay(workspace):
    w = workspace
    w.journal.record_reference("akm_old")
    w.configure(Automatic(enabled=True))
    w.scan()
    assert not w.values("reasoning_job")
    w.journal.record_reference("akm_old", event_id="changed")
    w.journal.record_reference("akm_one")
    w.scan()
    w.scan()
    assert len(w.values("reasoning_job")) == 1
    w.execute_one()
    w.scan()
    assert len(w.values("reasoning_job")) == 1
    w.journal.record_reference("akm_one", event_id="new reference")
    w.scan()
    assert len(w.values("reasoning_job")) == 1
    w.journal.record_reference("akm_two")
    w.scan()
    w.configure(Automatic(enabled=False))
    assert sorted(j["status"] for j in w.values("reasoning_job")) == [
        "cancelled",
        "complete",
    ]
    w.configure(Automatic(enabled=True))
    w.scan()
    assert len(w.values("reasoning_job")) == 2


def test_disabled_provider_and_incognito_refuse_before_retrieval(workspace):
    with pytest.raises(HTTPException, match="409"):
        workspace.enqueue(
            Ask(record_id="akm_one", question="Explain", provider_id="codex")
        )
    workspace.event_policy = lambda event: {**event, "privacy_mode": "incognito"}
    with pytest.raises(HTTPException, match="423"):
        workspace.enqueue(Ask(record_id="akm_one", question="Explain"))
    assert workspace.values("reasoning_job") == []


def test_control_run_owns_reasoning_without_changing_global_automatic(workspace):
    from oida.operation_control import Control, controlled

    w = workspace
    w.configure(Automatic(enabled=True))

    def remember():
        w.journal.record_reference("akm_one")
        return {"akousma_id": "akm_one"}

    w.operations.run("control-file", remember)
    # Radio acquisitions use their own cancellation control, with the same ID.
    with controlled(Control(identifier="control-radio")):
        w.journal.record_reference("akm_two")
    w.scan()
    assert not w.values("reasoning_job")
    assert w.config()["enabled"] is True
    assert w.journal.get("record_reference", "akm_one")["operation_id"] == "control-file"
    # Re-references keep the attribution; unrelated Stack arrivals still expand.
    w.journal.record_reference("akm_one", event_id="later-reference")
    w.journal.record_reference("akm_old")
    w.scan()
    assert [j["record_id"] for j in w.values("reasoning_job")] == ["akm_old"]
    # Control's explicit On action is allowed exactly once and stays separate.
    w.enqueue(Ask(request_id="control_reason", record_id="akm_one", question="Explain"))
    w.scan()
    assert len(w.values("reasoning_job")) == 2


def test_shutdown_interrupt_reconciles_committed_session_without_retry(workspace):
    w = workspace
    w.enqueue(Ask(request_id="recover", record_id="akm_one", question="Explain"))
    w.execute_one()
    job = w.journal.get("reasoning_job", "recover")
    w.save_job({**job, "status": "running", "conversation_id": None})
    w.start()
    w.close()
    assert w.journal.get("reasoning_job", "recover")["status"] == "complete"
    assert len(w.conversations.list()) == 1


def test_context_switches_and_search_failure_do_not_fake_results():
    calls = []

    def search(query):
        calls.append(query)
        return [
            {
                "kind": "web",
                "title": "Reference",
                "text": "Independent context",
                "url": "https://example.org",
                "retrieved_at": "today",
            }
        ]

    event = {"id": "one", "aggregate": {"short_summary": "Birdsong and traffic"}}
    sources, notes, query = retrieve(event, "Explain", {"web": False}, search=search)
    assert sources == [] and calls == []
    sources, notes, query = retrieve(event, "Explain", {"web": True}, search=search)
    assert len(sources) == 1 and calls == [query]
    packet = EvidencePacketBuilder().build(
        event=event, question="Explain", references=sources
    )
    ref = [item for item in packet.items if item.kind == "reference"][0]
    assert ref.source == "web" and "example.org" not in canonical(packet.model_dump())

    def fail(query):
        raise TimeoutError()

    sources, notes, _ = retrieve(event, "Explain", {"web": True}, search=fail)
    assert sources == [] and "unavailable" in notes[0]


def test_scope_off_excludes_earlier_enriched_turns(workspace):
    w = workspace
    w.enqueue(
        Ask(
            request_id="web",
            record_id="akm_one",
            question="Web context",
            scope={"web": True},
        )
    )
    w.execute_one()
    identifier = w.journal.get("reasoning_job", "web")["conversation_id"]
    history = w.reasoning._conversation_history(
        identifier,
        allow_transcript=True,
        allow_memory_content=True,
        context_scope={"web": False, "wiki": True, "memories": True},
    )
    assert history == []
    history = w.reasoning._conversation_history(
        identifier,
        allow_transcript=True,
        allow_memory_content=True,
        context_scope={"web": True, "wiki": True, "memories": True},
    )
    assert len(history) == 2


def test_cancelled_response_cannot_commit_conversation(workspace):
    from oida.operation_control import Control, controlled

    w = workspace
    control = Control()
    with pytest.raises(HTTPException, match="cancelled"):
        with controlled(control):
            control.cancel()
            w.reasoning.ask(
                event=retained_event(w.reader("akm_one")), question="Explain"
            )
    assert w.conversations.list() == []


def test_strict_harness_schema_closes_all_objects_without_mutating_contract():
    from oida.reasoning.contracts import reasoning_response_schema, strict_output_schema
    from jsonschema import validate

    original = reasoning_response_schema()
    before = deepcopy(original)
    strict = strict_output_schema(original)
    assert original == before

    def inspect(value):
        if isinstance(value, list):
            for item in value:
                inspect(item)
        elif isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for item in value.values():
                inspect(item)

    inspect(strict)
    validate(
        {
            "contract": "oida/reasoning-response/v0.1",
            "answer_blocks": [
                {
                    "kind": "answer",
                    "text": "Fixture",
                    "claim_scope": "acoustic",
                    "evidence_refs": ["event:one:anchor"],
                }
            ],
            "hypotheses": [],
            "uncertainties": [],
            "suggested_questions": [],
            "requested_action": None,
        },
        strict,
    )


def test_web_html_parser_preserves_actual_links_and_excerpts():
    from oida.reasoning_context import SearchResults

    parser = SearchResults()
    parser.feed(
        '<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Facoustics">Acoustic <b>ecology</b></a><td class="result-snippet">Retained <b>excerpt</b>.</td>'
    )
    assert parser.results == [
        {
            "url": "https://example.org/acoustics",
            "title": "Acoustic ecology",
            "text": "Retained excerpt.",
        }
    ]


def test_background_auto_runs_without_dashboard_and_recovers_configuration(workspace):
    import time

    w = workspace
    w.configure(
        Automatic(enabled=True, scope={"web": False, "wiki": False, "memories": False})
    )
    recovered = Workspace(
        w.journal,
        w.conversations,
        w.reasoning,
        w.settings,
        w.operations,
        w.event_policy,
        reader=w.reader,
        retriever=w.retriever,
    )
    assert recovered.config()["enabled"]
    recovered.start()
    try:
        w.journal.record_reference("akm_two")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            jobs = recovered.values("reasoning_job")
            if jobs and jobs[0]["status"] == "complete":
                break
            time.sleep(0.05)
        assert len(jobs) == 1 and jobs[0]["status"] == "complete"
        session = recovered.session(jobs[0]["conversation_id"])
        assert session["turns"][0]["research"]["mode"] == "automatic"
    finally:
        recovered.close()


def test_codex_records_model_reported_by_harness():
    from oida.reasoning.providers.codex import CodexProvider
    from test_reasoning_providers import provider_request

    provider = CodexProvider(
        executable="/bin/echo",
        turn_executor=lambda **kw: {
            "content": '{"answer":"Fixture"}',
            "model_id": "actual-model",
        },
    )
    result = provider.complete(provider_request("codex", model_id=None))
    assert result.model_id == "actual-model"
