"""Public retrieval through mounted owner routes and retained research turns."""

import json
import asyncio
from copy import deepcopy

import httpx2 as httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from oida.owner_journal import OwnerJournal
from oida.operation_control import Operations
from oida.reasoning.evidence import EvidencePacketBuilder
from oida.reasoning_workspace import Ask, Workspace
from oida.research_references import PublicRecord, ResearchReference
from oida.sonicfield_discovery import PublicDiscovery
from sonicfield_fixtures import body, record, response
from test_reasoning_orchestrator import (
    _QueuedProvider,
    _enabled_codex_settings,
    _service,
    _event,
)


def test_snapshot_and_citation_roundtrip_through_real_reasoning_custody(tmp_path):
    public_record = record(
        "library", text="Fixture bibliography only, not the full paper."
    )
    reference = ResearchReference.from_record(
        PublicRecord.model_validate(public_record)
    )

    def answer(request):
        packet = request.metadata["evidence_packet"]
        ref = next(x for x in packet["items"] if x["source"] == "sonicfield")
        assert ref["ref"] == "context:" + reference.reference_ref
        assert ref["value"]["rights_status"] == "unknown"
        assert ref["value"]["content_kind"] == "summary_or_bibliographic_text"
        assert "https://" not in json.dumps(packet)
        return dict(
            answer_blocks=[
                dict(
                    kind="answer",
                    text="This is a bibliographic frame, not evidence of this sound’s source.",
                    evidence_refs=[ref["ref"]],
                )
            ],
            hypotheses=[],
            uncertainties=[],
            suggested_questions=[],
        )

    provider = _QueuedProvider([answer])
    service = _service(
        str(tmp_path / "service"), provider=provider, settings=_enabled_codex_settings()
    )
    journal = OwnerJournal(tmp_path / "journal.sqlite")
    original = dict(
        akousma_id="akm_fixture",
        auditum={"contract": "earworm/auditum/v2"},
        summary="Retained uncertain event",
        listening={},
    )
    before = deepcopy(original)
    public = PublicDiscovery(
        transport=httpx.MockTransport(
            lambda r: response(r, body(public_record), sse=True)
        )
    )
    w = Workspace(
        journal,
        service.conversations,
        service,
        service.settings_store,
        Operations(journal),
        lambda e: e,
        reader=lambda _: deepcopy(original),
        retriever=lambda *a: ([], [], "local query unused by public retrieval"),
        public=public,
    )
    selection = dict(
        family="library", slug="fixture-listening", expectedId="fixture-listening"
    )
    req = Ask(
        request_id="selected-public",
        record_id="akm_fixture",
        question="How does this concept frame the account?",
        provider_id="codex",
        scope=dict(memories=False, wiki=False, web=False, sonicfield=True),
        sonicfield_records=[selection],
    )
    w.enqueue(req)
    assert w.execute_one()
    job = journal.get("reasoning_job", "selected-public")
    assert job["status"] == "complete", job
    turn = service.conversations.get(job["conversation_id"])["turns"][0]
    research = turn["research"]
    assert research["sources"][0]["research_reference"] == reference.model_dump(
        mode="json"
    )
    assert research["exposure"]["subject_ref"] == "akm_fixture"
    assert research["exposure"]["reference_refs"] == [reference.reference_ref]
    assert research["exposure"]["context_basis"] == "informed_context"
    assert turn["answer_blocks"][0]["evidence_refs"] == [
        "context:" + reference.reference_ref
    ]
    # Reopening the persisted session does not downgrade the source to title/text.
    assert w.session(job["conversation_id"])["turns"][0]["research"] == research
    assert (
        service._conversation_history(
            job["conversation_id"],
            allow_transcript=True,
            allow_memory_content=True,
            context_scope=dict(sonicfield=False),
        )
        == []
    )
    assert original == before and len(provider.requests) == 1
    w.close()


def test_public_scope_and_current_covenant_precede_egress(tmp_path):
    service = _service(str(tmp_path / "service"))
    journal = OwnerJournal(tmp_path / "journal.sqlite")
    calls = []
    original = dict(
        akousma_id="akm_fixture",
        auditum={"contract": "earworm/auditum/v2"},
        summary="private place",
        listening={},
    )
    public = PublicDiscovery(transport=httpx.MockTransport(lambda r: calls.append(r)))
    w = Workspace(
        journal,
        service.conversations,
        service,
        service.settings_store,
        Operations(journal),
        lambda e: e,
        reader=lambda _: deepcopy(original),
        retriever=lambda *a: ([], [], "private place"),
        public=public,
    )
    selection = dict(
        family="lexicon", slug="fixture-listening", expectedId="fixture-listening"
    )
    with pytest.raises(HTTPException, match="409"):
        w.enqueue(
            Ask(
                record_id="akm_fixture",
                question="Explain",
                sonicfield_records=[selection],
            )
        )
    w.enqueue(
        Ask(
            request_id="no-selection",
            record_id="akm_fixture",
            question="Explain",
            scope=dict(memories=False, wiki=False, web=False, sonicfield=True),
        )
    )
    w.execute_one()
    assert calls == []
    w.event_policy = lambda e: {
        **e,
        "covenant": {"withheld": [{"rule": "do_not_reveal", "subject": "speaker"}]},
    }
    w.enqueue(
        Ask(
            request_id="withheld-public",
            record_id="akm_fixture",
            question="Explain",
            scope=dict(memories=False, wiki=False, web=False, sonicfield=True),
            sonicfield_records=[selection],
        )
    )
    w.execute_one()
    assert calls == []
    w.event_policy = lambda e: {**e, "privacy_mode": "incognito"}
    with pytest.raises(HTTPException, match="423"):
        w.enqueue(
            Ask(
                record_id="akm_fixture",
                question="Explain",
                scope=dict(sonicfield=True),
                sonicfield_records=[selection],
            )
        )
    assert calls == []
    w.close()


def test_reference_packet_preserves_excerpt_hash_after_path_redaction():
    raw = record(
        text="A fixture at /Users/private/source.wav and https://example.org/source remains untrusted."
    )
    reference = ResearchReference.from_record(PublicRecord.model_validate(raw))
    packet = EvidencePacketBuilder().build(
        event={"id": "evt", "aggregate": {}},
        question="Explain",
        references=[reference.source()],
    )
    context = next(x for x in packet.items if x.source == "sonicfield")
    assert context.value["reference_ref"] == reference.reference_ref
    assert context.value["excerpt_sha256"] != context.value["model_excerpt_sha256"]
    assert "/Users/private" not in json.dumps(context.model_dump())
    assert reference.record.content.text == raw["content"]["text"]


def test_reference_metadata_cannot_bypass_packet_path_filtering():
    raw = record()
    raw["identity"]["id"] = "/Users/private/source.wav"
    raw["content"]["sourceField"] = "/Users/private/field"
    raw["retrieval"]["etag"] = "/Users/private/etag"
    reference = ResearchReference.from_record(PublicRecord.model_validate(raw))
    packet = EvidencePacketBuilder().build(
        event={"id": "evt", "aggregate": {}},
        question="Explain",
        references=[reference.source()],
    )
    assert "/Users/private" not in json.dumps(packet.model_dump())
    assert reference.record.identity.id == "/Users/private/source.wav"


def test_mcp_public_wrappers_forward_exact_selected_arguments(monkeypatch):
    from oida import mcp_server as m

    calls = []
    monkeypatch.setattr(m, "_server", lambda: "http://127.0.0.1:fixture")

    def post(origin, path, body):
        calls.append((origin, path, body))
        return {"fixture": True}

    monkeypatch.setattr(m, "post_json", post)
    asyncio.run(
        m.oida_sonic_field_search(
            "oída ch’ixi",
            families=["lexicon"],
            cursor="opaque",
            operation_id="read-one",
        )
    )
    assert calls[-1][1:] == (
        "/sonicfield/search",
        dict(
            query="oída ch’ixi",
            locale="en",
            limit=5,
            filters={},
            families=["lexicon"],
            cursor="opaque",
            operation_id="read-one",
        ),
    )
    asyncio.run(
        m.oida_sonic_field_record(
            family="library", slug="fixture", expected_id="exact-id"
        )
    )
    assert calls[-1][1:] == (
        "/sonicfield/record",
        dict(family="library", slug="fixture", expectedId="exact-id", locale="en"),
    )
    asyncio.run(
        m.oida_sonic_field_record(page_url="https://sonicfield.org/wiki/fixture")
    )
    assert calls[-1][1] == "/sonicfield/page"
    asyncio.run(m.oida_sonic_field_relations("fixture", limit=2, operation_id="links"))
    assert calls[-1][2] == dict(
        slug="fixture", locale="en", limit=2, operation_id="links"
    )
    for tool in asyncio.run(m.MCP.list_tools()):
        if tool.name.startswith("oida_sonic_field_"):
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False


def test_bounded_prompt_cannot_cite_or_claim_exposure_to_withheld_references(
    tmp_path, monkeypatch
):
    from oida.reasoning.orchestrator import TurnOptions
    from oida.research_references import ResearchContext
    from oida.reasoning import orchestrator as module

    reference = ResearchReference.from_record(
        PublicRecord.model_validate(record(text="x" * 1200))
    )
    cited = "context:" + reference.reference_ref
    monkeypatch.setattr(module, "_inquiry_bound", lambda _: 600)

    def unsupported(request):
        assert cited not in request.user_prompt
        assert all(
            item["ref"] != cited
            for item in request.metadata["evidence_packet"]["items"]
        )
        # Deliberately guess an offered but omitted identity. It must not validate.
        return dict(
            answer_blocks=[
                dict(
                    kind="answer",
                    text="A claim from omitted context.",
                    evidence_refs=[cited],
                )
            ],
            hypotheses=[],
            uncertainties=[],
            suggested_questions=[],
        )

    provider = _QueuedProvider([unsupported, unsupported])
    service = _service(
        str(tmp_path / "bounded"), provider=provider, settings=_enabled_codex_settings()
    )
    event = _event()
    exposure = ResearchContext(
        subject_ref=event["id"], reference_refs=[reference.reference_ref]
    )
    result = service.ask(
        event=event,
        question="Explain",
        options=TurnOptions(
            provider_id="codex",
            include_memory=False,
            research=dict(
                scope={"sonicfield": True},
                sources=[reference.source()],
                exposure=exposure.model_dump(),
            ),
        ),
    )
    turn = result["turn"]
    assert turn["fallback"]["used"] is True and len(provider.requests) == 2
    assert cited not in turn["audit"]["evidence_refs"]
    assert cited in turn["audit"]["offered_evidence_refs"]
    assert cited in turn["audit"]["withheld_evidence_refs"]
    assert turn["research"]["exposure"]["context_basis"] == "selected_context"
    assert turn["research"]["exposure"]["prepared_reference_refs"] == []
    assert turn["research"]["exposure"]["withheld_reference_refs"] == [
        reference.reference_ref
    ]
    assert (
        turn["research"]["sources"][0]["research_reference"]["reference_ref"]
        == reference.reference_ref
    )


@pytest.fixture
def owner_client(tmp_path, monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(("OIDA_", "HMM_", "AEAR_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "oida"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("OIDA_SONICFIELD_ROOT", str(tmp_path / "missing-local-site"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    calls = []

    def mock_call(self, name, args, **kwargs):
        calls.append((name, args))
        return body(record(args["family"], args["slug"]))

    monkeypatch.setattr(PublicDiscovery, "_call", mock_call)
    from oida.server import create_app

    return TestClient(create_app(profile="stub"), base_url="http://127.0.0.1"), calls


def test_mounted_public_owner_routes_validate_before_io_and_publish_contracts(
    owner_client,
):
    client, calls = owner_client
    status = client.get("/sonicfield/public/status").json()
    assert status["availability"] == "not_probed" and calls == []
    assert (
        client.get("/sonicfield/schema/reference").json()["properties"]["contract"][
            "const"
        ]
        == "oida/research-reference/v1"
    )
    assert client.get("/sonicfield/schema/context").status_code == 200
    for payload in [
        dict(query="x", event={"private": "place"}),
        dict(query="listening", limit=6),
    ]:
        assert client.post("/sonicfield/search", json=payload).status_code == 422
    assert calls == []
    result = client.post(
        "/sonicfield/page",
        json=dict(
            url="https://sonicfield.org/library?item=fixture-listening",
            operation_id="public-read",
        ),
    )
    assert result.status_code == 200, result.text
    assert (
        result.json()["references"][0]["record"]["content"]["kind"]
        == "summary_or_bibliographic_text"
    )
    assert len(calls) == 1
    assert client.get("/operations/public-read").json()["worker_settled"] is True
    assert (
        client.post("/sonicfield/page", json=dict(url="file:///private.md")).status_code
        == 400
    )
    assert len(calls) == 1
