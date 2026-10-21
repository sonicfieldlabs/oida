from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from oida.operation_control import Control, OperationCancelled, controlled
from oida.research_references import (
    PageInput,
    PublicRecord,
    RecordInput,
    ResearchReference,
    SearchInput,
    SelectedRecord,
)
from oida.sonicfield_discovery import ENDPOINT, PublicDiscovery
from sonicfield_fixtures import body, record, response, search_data


@pytest.mark.parametrize("sse", [False, True])
def test_fixed_anonymous_transport_preserves_record_depth_and_snapshot(sse):
    calls = []
    expected = record("library", text="Fixture creator. Fixture title (2026).")

    def send(req):
        calls.append(req)
        assert str(req.url) == ENDPOINT and req.method == "POST"
        assert "authorization" not in req.headers and "cookie" not in req.headers
        assert json.loads(req.content)["params"]["name"] == "get_sonic_field_record"
        return response(req, body(expected), sse=sse)

    client = PublicDiscovery(transport=httpx.MockTransport(send))
    result = client.record(
        RecordInput(
            family="library", slug="fixture-listening", expectedId="fixture-listening"
        )
    )
    assert result["discovery"]["data"] == expected and result["outcome"] == "retrieved"
    ref = ResearchReference.model_validate(result["references"][0])
    assert ref.record.content.kind == "summary_or_bibliographic_text"
    assert (
        ref.record.languageStatus == "unknown" and ref.record.rights.status == "unknown"
    )
    assert (
        ref.record.retrieval.contentLanguage == "en"
    )  # Does not fill unknown record language.
    assert ref.ancestry.status == "unknown" and ref.ancestry.source_group is None
    assert (
        len(calls) == 1 and client.status()["availability"] == "last_request_succeeded"
    )


@pytest.mark.parametrize(
    "family",
    [
        "journal",
        "lexicon",
        "library",
        "people",
        "tools",
        "links",
        "works",
        "radar",
        "calendar",
    ],
)
def test_current_nine_families_are_public_context_only(family):
    data = record(family)
    client = PublicDiscovery(
        transport=httpx.MockTransport(lambda r: response(r, body(data)))
    )
    result = client.record(RecordInput(family=family, slug="fixture-listening"))
    assert result["discovery"]["ok"]
    assert result["references"][0]["role"] == "context"
    assert "no identity adoption" in result["authority"]


def test_page_routes_exact_items_and_refuses_ambiguous_or_private_urls():
    for url, family, slug in [
        ("https://sonicfield.org/wiki/o%C3%ADda", "lexicon", "oída"),
        (
            "https://sonicfield.org/library?item=fixture-listening",
            "library",
            "fixture-listening",
        ),
        ("https://sonicfield.org/tools/list?item=birdnet", "tools", "birdnet"),
        ("https://sonicfield.org/people/fixture-person", "people", "fixture-person"),
    ]:
        result = PageInput(url=url).record_input()
        assert (result.family, result.slug) == (family, slug)
    assert (
        PageInput(url="https://sonicfield.org/fixture-article", family="journal")
        .record_input()
        .family
        == "journal"
    )
    for url in [
        "https://sonicfield.org/fixture-article",
        "https://sonicfield.org/library",
        "http://sonicfield.org/wiki/foo",
        "https://sonicfield.org.evil.test/wiki/foo",
        "https://user@sonicfield.org/wiki/foo",
        "https://sonicfield.org/wiki/%2fprivate",
        "https://sonicfield.org/wiki/%252fprivate",
        "https://sonicfield.org/library?item=a&item=b",
        "https://sonicfield.org/wiki/foo#fragment",
        "file:///private/wiki.md",
    ]:
        with pytest.raises(ValueError):
            PageInput(url=url).record_input()
    with pytest.raises(ValueError):
        PageInput(
            url="https://sonicfield.org/wiki/foo", family="library"
        ).record_input()


@pytest.mark.parametrize(
    "change",
    [
        dict(limit=6),
        dict(limit=True),
        dict(query="a"),
        dict(query="one two three four five six seven eight nine"),
        dict(families=["lexicon", "lexicon"]),
        dict(filters={"people": {"kind": "person"}}),
        dict(event={"private": "audio"}),
        dict(query="x\x00y"),
    ],
)
def test_search_refuses_invalid_scope_before_transport(change):
    with pytest.raises(ValidationError):
        SearchInput.model_validate(
            dict(
                query="oída ch’ixi",
                families=["lexicon"],
                **{k: v for k, v in change.items() if k not in {"query", "families"}},
            )
            | {k: v for k, v in change.items() if k in {"query", "families"}}
        )


def test_partial_coverage_and_opaque_continuation_preserved_without_auto_crawl():
    req = SearchInput(
        query="situated listening", families=["lexicon", "library"], limit=2
    )
    data = search_data(req, [record()], partial=True)
    data["coverage"][1].update(
        status="failed",
        error=dict(code="upstream_timeout", message="Fixture failure", retryable=True),
    )
    calls = []

    def send(r):
        calls.append(json.loads(r.content)["params"]["arguments"])
        return response(r, body(data), sse=True)

    client = PublicDiscovery(transport=httpx.MockTransport(send))
    result = client.search(req)
    assert result["outcome"] == "partial" and result["discovery"]["data"] == data
    assert len(calls) == 1
    client.search(req.model_copy(update={"cursor": data["nextCursor"]}))
    assert len(calls) == 2 and calls[1]["cursor"] == "fixture-continuation"
    assert {k: v for k, v in calls[1].items() if k != "cursor"} == calls[0]


@pytest.mark.parametrize(
    "fault,code",
    [
        ("identity", "invalid_upstream_response"),
        ("locale", "invalid_upstream_response"),
        ("version", "invalid_upstream_response"),
        ("full_text", "invalid_upstream_response"),
        ("redirect", "invalid_upstream_response"),
        ("html", "invalid_upstream_response"),
        ("oversized", "response_too_large"),
        ("rate", "upstream_rate_limited"),
        ("missing", "not_found"),
        ("rpc_id", "invalid_upstream_response"),
    ],
)
def test_unavailable_or_unsupported_results_never_fall_back_or_fabricate(fault, code):
    calls = []

    def send(req):
        calls.append(1)
        data = record()
        if fault == "identity":
            data["identity"]["id"] = "changed"
        if fault == "locale":
            data["requestedLocale"] = "es"
        if fault == "full_text":
            data["content"]["level"] = "full_text"
        if fault == "redirect":
            return httpx.Response(
                302, headers={"location": "https://example.org/private"}
            )
        if fault == "html":
            return httpx.Response(
                200,
                text="<html>challenge</html>",
                headers={"content-type": "text/html"},
            )
        if fault == "oversized":
            return httpx.Response(
                200, content=b"x" * 524289, headers={"content-type": "application/json"}
            )
        if fault == "rate":
            return httpx.Response(429)
        if fault == "missing":
            return response(
                req,
                body(
                    error=dict(
                        code="not_found", message="No public record", retryable=False
                    )
                ),
            )
        result = body(data)
        if fault == "version":
            result["schemaVersion"] = "2.0"
        if fault == "rpc_id":
            return httpx.Response(
                200,
                json=dict(
                    jsonrpc="2.0", id="wrong", result=dict(structuredContent=result)
                ),
            )
        return response(req, result)

    client = PublicDiscovery(transport=httpx.MockTransport(send))
    result = client.record(
        RecordInput(
            family="lexicon", slug="fixture-listening", expectedId="fixture-listening"
        )
    )
    assert result["references"] == [] and result["outcome"] == code
    assert result["discovery"]["data"] is None and len(calls) == 1


def test_hard_timeout_and_cancelled_late_output_discard():
    async def slow(req):
        await asyncio.sleep(0.2)
        return response(req, body(record()))

    client = PublicDiscovery(transport=httpx.MockTransport(slow))
    assert (
        client.record(
            RecordInput(family="lexicon", slug="fixture-listening"), timeout=0.01
        )["outcome"]
        == "upstream_timeout"
    )
    control = Control()

    def cancel(req):
        control.cancel()
        return response(req, body(record()))

    client = PublicDiscovery(transport=httpx.MockTransport(cancel))
    with controlled(control), pytest.raises(OperationCancelled):
        client.record(RecordInput(family="lexicon", slug="fixture-listening"))


def test_selected_sources_only_send_identities_and_deduplicate():
    calls = []

    def send(req):
        args = json.loads(req.content)["params"]["arguments"]
        calls.append(args)
        return response(req, body(record(args["family"], args["slug"])))

    client = PublicDiscovery(transport=httpx.MockTransport(send))
    selection = SelectedRecord(
        family="tools", slug="fixture-listening", expectedId="fixture-listening"
    )
    sources, notes, receipts = client.selected([selection, selection])
    assert len(sources) == len(receipts) == len(calls) == 1 and not notes
    assert set(calls[0]) == {"family", "slug", "expectedId", "locale"}


def test_shared_selection_deadline_reports_unexamined_identities(monkeypatch):
    from types import SimpleNamespace
    from oida import sonicfield_discovery as module

    times = iter([0.0, 0.0, 30.0, 31.0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(times)))
    client = PublicDiscovery()
    calls = []
    reference = ResearchReference.from_record(PublicRecord.model_validate(record()))

    def selected(req, **kwargs):
        calls.append(req)
        return dict(
            references=[reference.model_dump()],
            discovery=body(record()),
            outcome="retrieved",
        )

    monkeypatch.setattr(client, "record", selected)
    records = [
        SelectedRecord(family="lexicon", slug=s, expectedId=s)
        for s in ["fixture-listening", "second", "third"]
    ]
    sources, notes, receipts = client.selected(records)
    assert len(calls) == 1 and len(sources) == 1 and len(receipts) == 3
    assert [r["outcome"] for r in receipts] == [
        "retrieved",
        "budget_exhausted",
        "budget_exhausted",
    ]
    assert len(notes) == 1


def test_reference_tampering_and_unsupported_revision_refuse_instead_of_downgrade():
    ref = ResearchReference.from_record(PublicRecord.model_validate(record()))
    for changed in [
        dict(contract="oida/research-reference/v2"),
        dict(excerpt_sha256="a" * 64),
        dict(reference_ref="sfref:" + "b" * 64),
    ]:
        with pytest.raises(ValidationError):
            ResearchReference.model_validate(ref.model_dump() | changed)
    changed = deepcopy(ref.model_dump())
    changed["record"]["content"]["text"] = "Replacement"
    with pytest.raises(ValidationError):
        ResearchReference.model_validate(changed)


def test_relation_targets_stay_leads_until_selected_detail():
    from oida.research_references import RelationsInput

    data = dict(
        focus=record(),
        predicate="links-to",
        relations=[
            dict(
                target=record(slug="target"),
                predicate="links-to",
                evidenceUrl="https://sonicfield.org/wiki/fixture-listening",
            )
        ],
        coverage=dict(
            rawReferences=8,
            invalidReferences=0,
            duplicateReferences=0,
            selfReferences=0,
            uniqueCandidates=8,
            attempted=1,
            resolved=1,
            unavailable=0,
            failed=0,
            unexamined=7,
            returned=1,
            truncated=True,
            scope="public_outgoing_wikilinks",
        ),
        errors=[],
    )
    client = PublicDiscovery(
        transport=httpx.MockTransport(lambda r: response(r, body(data)))
    )
    result = client.relations(RelationsInput(slug="fixture-listening"))
    assert result["discovery"]["ok"] and result["references"] == []
    assert result["discovery"]["data"]["coverage"]["unexamined"] == 7
