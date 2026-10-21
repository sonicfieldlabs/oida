"""Synthetic catalog responses; no live external requests or copied research text."""

import json

import httpx2 as httpx

from oida.research_references import CONTENT_KINDS, RESOURCE_TYPES


def record(
    family="lexicon",
    slug="fixture-listening",
    *,
    text="A fixture concept; not a claim about the selected recording.",
):
    item = family in {"library", "tools", "links", "works", "calendar"}
    routes = {
        "library": "library",
        "tools": "tools/list",
        "links": "links/list",
        "works": "works",
        "calendar": "calendar",
    }
    path = routes.get(
        family,
        "wiki" if family == "lexicon" else "people" if family == "people" else "",
    )
    url = "https://sonicfield.org/" + (
        path + "?item=" + slug if item else (path + "/" if path else "") + slug
    )
    return dict(
        identity=dict(
            family=family, id=slug, slug=slug, resourceType=RESOURCE_TYPES[family]
        ),
        title="Oída Ch’ixi — fixture",
        canonicalUrl=url,
        canonicalEvidence="source_contract",
        sourceUrl="https://example.org/reference",
        sourceName=None,
        upstreamCanonicalUrl=None,
        creators=["Fixture creator"],
        publishedAt=None,
        modifiedAt=None,
        year="2026",
        tags=[],
        requestedLocale="en",
        contentLanguage=None,
        languageStatus="unknown",
        content=dict(
            level="excerpt",
            kind=CONTENT_KINDS[family],
            format="text",
            text=text,
            sourceField="fixture",
            truncated=False,
            availableCharacters=len(text or ""),
            trust="untrusted_source_text",
        ),
        rights=dict(status="unknown", evidenceUrl=None),
        evidenceConfidence=None,
        event=None,
        retrieval=dict(
            url="https://sonicfield.org/api/mobile/v1/" + family + "/" + slug,
            retrievedAt="2026-10-21T00:00:00Z",
            validatedAt="2026-10-21T00:00:00Z",
            status=200,
            conditional=False,
            etag='W/"fixture-v1"',
            cacheControl="public, max-age=60",
            contentLanguage="en",
            schemaVersion="2026-08-29",
        ),
        provenanceRetrieval=None,
        warnings=["content_language_not_declared_by_api"],
    )


def body(data=None, *, error=None):
    return dict(
        schemaVersion="1.1",
        ok=error is None,
        data=data if error is None else None,
        error=error,
    )


def response(request, output, *, sse=False):
    rpc = dict(
        jsonrpc="2.0",
        id=json.loads(request.content)["id"],
        result=dict(structuredContent=output),
    )
    if sse:
        return httpx.Response(
            200,
            content=("event: message\ndata: " + json.dumps(rpc) + "\n\n").encode(),
            headers={"content-type": "text/event-stream"},
        )
    return httpx.Response(200, json=rpc)


def coverage(family, query, *, status="ok", returned=1):
    return dict(
        family=family,
        query=query,
        filters={},
        requestedPage=1,
        matching="upstream_query",
        page=dict(number=1, size=10, total=3, totalPages=1, next=None),
        candidates=3,
        unseenCandidates=3,
        returned=returned,
        retrieval=None,
        status=status,
        error=(
            dict(code="upstream_timeout", message="Fixture timeout", retryable=True)
            if status == "failed"
            else None
        ),
    )


def search_data(req, records, *, partial=False, more=True):
    return dict(
        results=[
            dict(**r, score=2.0, matchReasons=["literal fixture match"])
            for r in records
        ],
        query=req.query,
        queryStrategy="literal_topic_query",
        rankingScope="retrieved_candidates",
        coverage=[
            coverage(
                f,
                req.query,
                returned=sum(r["identity"]["family"] == f for r in records),
            )
            for f in req.families
        ],
        partial=partial,
        nextCursor="fixture-continuation" if more else None,
        hasMore=more,
        continuationStopReason=None,
    )
