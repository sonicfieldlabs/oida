"""Read-only, bounded context retrieval for record-linked reasoning sessions."""

from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from xml.etree import ElementTree

from oida.contracts import now_iso
from oida.reasoning.evidence import safe_external_text, covenant_blocks_untyped_prose


def read_record(identifier):
    from akousmata_app.paths import open_store

    store = open_store()
    try:
        record = store.get(identifier)
        if not record or not record.get("auditum"):
            raise ValueError("Choose an existing Auditum record from Memory")
        return record
    finally:
        store.close()


def read_raw_records(identifiers):
    """The stored text of many records in one query: id to text, or None when absent."""
    from akousmata_app.paths import open_store

    ids = list(dict.fromkeys(identifiers))
    store = open_store()
    try:
        found = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            rows = store.conn.execute(
                "SELECT akousma_id, record FROM akousmata WHERE akousma_id IN (%s)"
                % ",".join("?" * len(chunk)),
                chunk,
            ).fetchall()
            found.update({row[0]: row[1] for row in rows})
        return {identifier: found.get(identifier) for identifier in ids}
    finally:
        store.close()


def retained_event(record):
    """No audio paths or fresh measurement claims; canonical record is never written."""
    routes = []
    specialist_observations = []
    from akousma.retained_policy import retained_covenant
    covenant = retained_covenant(record)
    for entry in record.get("listening", {}).values():
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get('contract') == 'akouo/agent-report/v0.1':
            inherited = []
            for feature in payload.get('features', [])[:40]:
                claim = deepcopy(feature.get('claim') or {})
                claim['statement'] = 'Retained digital measurement: ' + str(claim.get('statement') or '')[:3000]
                claim.update(source='memory', confidence='undetermined')
                inherited.append(claim)
            if inherited:
                routes.append({'structured': {'claim_summary': {'undetermined': inherited}}})
        from oida.reasoning.specialist_context import project
        specialist_observations.extend(project(payload.get("specialist_evidence")))
        claims = payload.get("claim_summary") or {}
        inherited = []
        for category, values in claims.items():
            if not isinstance(values, list):
                continue
            for value in values[:40]:
                claim = (
                    deepcopy(value)
                    if isinstance(value, dict)
                    else {"statement": str(value)}
                )
                claim["statement"] = (
                    f"Retained {category} account: "
                    + str(claim.get("statement") or "")[:3000]
                )
                claim["source"] = "memory"
                claim["confidence"] = "undetermined"
                inherited.append(claim)
        if inherited:
            routes.append(
                {"structured": {"claim_summary": {"undetermined": inherited}}}
            )
    return dict(
        id=record["akousma_id"],
        privacy_mode="session",
        raw_audio_policy="temp",
        covenant=covenant,
        routes=routes[:12],
        specialist_observations=specialist_observations[:12],
        aggregate={
            "short_summary": str(record.get("summary") or "Retained listening account")[
                :6000
            ],
            "title": str(record.get("summary") or "Retained listening account")[:90],
        },
    )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bing_search(query):
    """Fixed public search endpoint; fetch snippets, never arbitrary result URLs."""
    url = "https://www.bing.com/search?" + urlencode(
        {"format": "rss", "q": query[:240]}
    )
    with build_opener(ProxyHandler({}), NoRedirect()).open(
        Request(
            url, headers={"User-Agent": "ListeningStack/0.1 (owner-requested search)"}
        ),
        timeout=15,
    ) as response:
        raw = response.read(262145)
        if (
            len(raw) > 262144
            or b"<!DOCTYPE" in raw.upper()
            or b"<!ENTITY" in raw.upper()
        ):
            raise ValueError("Search response exceeded its safe bounds")
        root = ElementTree.fromstring(raw)
        if root.tag != "rss":
            raise ValueError("Search returned an unsupported response")
    sources = []
    for item in root.findall("./channel/item")[:6]:
        link = item.findtext("link", "")
        parsed = urlsplit(link)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            continue
        sources.append(
            dict(
                kind="web",
                title=item.findtext("title", "")[:300],
                text=item.findtext("description", "")[:1800],
                url=link[:2000],
                retrieved_at=now_iso(),
                basis="Search-result snippet; full page not retrieved",
            )
        )
    return sources


class SearchResults(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result-link" in attrs.get("class", "").split():
            href = attrs.get("href", "")
            parsed = urlsplit(href)
            url = (
                parse_qs(parsed.query).get("uddg", [href])[0]
                if parsed.hostname == "duckduckgo.com"
                else href
            )
            self.results.append({"title": "", "text": "", "url": url})
            self.capture = "title"
        elif tag == "td" and "result-snippet" in attrs.get("class", "").split():
            self.capture = "text"

    def handle_endtag(self, tag):
        if (tag == "a" and self.capture == "title") or (
            tag == "td" and self.capture == "text"
        ):
            self.capture = None

    def handle_data(self, data):
        if self.capture and self.results:
            self.results[-1][self.capture] += data


def web_search(query):
    # Public HTML/RSS interfaces can return challenges or irrelevant fallbacks.
    # Retain only results with a query-term match and report an empty retrieval
    # honestly. No returned link is fetched or executed.
    candidates = []
    errors = []
    try:
        url = "https://lite.duckduckgo.com/lite/?" + urlencode({"q": query[:240]})
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15
        ) as response:
            raw = response.read(262145)
            if response.status != 200 or len(raw) > 262144:
                raise ValueError("Search unavailable or response too large")
        parser = SearchResults()
        parser.feed(raw.decode("utf-8"))
        candidates = [
            {
                **item,
                "kind": "web",
                "retrieved_at": now_iso(),
                "search_provider": "DuckDuckGo",
                "basis": "Search-result snippet; full page not retrieved",
            }
            for item in parser.results
        ]
    except Exception as exc:
        errors.append(type(exc).__name__)
    if not candidates:
        try:
            candidates = [
                {**item, "search_provider": "Bing"} for item in bing_search(query)
            ]
        except Exception as exc:
            errors.append(type(exc).__name__)
    terms = set(re.findall(r"[^\W_]{4,}", query.lower())) - {
        "this",
        "that",
        "with",
        "from",
        "what",
        "about",
        "which",
        "have",
        "does",
        "listening",
        "record",
    }
    results = []
    for item in candidates:
        parsed = urlsplit(item["url"])
        text = " ".join((item["title"] + " " + item["text"]).lower().split())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            continue
        if terms and not any(term in text for term in terms):
            continue
        results.append(
            {
                **item,
                "title": " ".join(item["title"].split())[:300],
                "text": " ".join(item["text"].split())[:1800],
                "url": item["url"][:2000],
            }
        )
        if len(results) == 6:
            break
    if not results and errors:
        raise ValueError("Public search unavailable; no usable results")
    return results


def wiki_search(query):
    from akousmata_app.wiki import list_pages, read_page

    terms = set(re.findall(r"\w{4,}", query.lower()))
    candidates = []
    pages = list_pages()
    # Read the existing wiki only. Never call its synthesis/writing functions.
    for plural, kind in (
        ("topics", "topic"),
        ("tags", "tag"),
        ("research", "research"),
    ):
        for name in pages.get(plural, [])[:100]:
            text = (read_page(kind, name) or "")[:16000]
            score = sum(1 for word in terms if word in (name + " " + text).lower())
            if score:
                candidates.append(
                    (
                        score,
                        dict(
                            kind="wiki",
                            title=name,
                            text=text[:2400],
                            reference=f"wiki:{kind}:{name}",
                            retrieved_at=now_iso(),
                            basis="Retained local wiki text; not a fresh listening",
                        ),
                    )
                )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:3]]


def memory_search(query, identifier):
    from akousmata_app.paths import open_store
    from akousmata_app.research import ResearchSession

    store = open_store()
    try:
        corpus = ResearchSession(query, seed_ids=[identifier]).gather(store)
        sources = []
        for record in corpus:
            if record["akousma_id"] == identifier or not record.get("auditum"):
                continue
            event = retained_event(record)
            if covenant_blocks_untyped_prose(event.get("covenant")):
                continue
            sources.append(
                dict(
                    kind="memory",
                    title=record.get("summary") or record["akousma_id"],
                    text=event["aggregate"]["short_summary"],
                    reference=record["akousma_id"],
                    retrieved_at=now_iso(),
                    basis="Related retained Auditum; text or lineage match",
                )
            )
            if len(sources) == 3:
                break
        return sources
    finally:
        store.close()


def retrieve(
    event,
    question,
    scope,
    explicit_query="",
    *,
    search=web_search,
    wiki=wiki_search,
    memory=memory_search,
):
    sources, notes = [], []
    if covenant_blocks_untyped_prose(event.get("covenant")):
        return [], ["Additional context withheld by the current covenant"], ""
    summary = (
        safe_external_text(event.get("aggregate", {}).get("short_summary"), limit=500)
        or ""
    )
    query = (
        safe_external_text(explicit_query, limit=240)
        if explicit_query.strip()
        else None
    )
    if not query:
        query = " ".join(re.findall(r"[^\W_]{3,}", summary or question)[:24])[:240]
    for kind, enabled, callback in (
        ("memory", scope.get("memories"), lambda q: memory(q, event["id"])),
        ("wiki", scope.get("wiki"), wiki),
        ("web", scope.get("web"), search),
    ):
        if not enabled:
            continue
        try:
            found = callback(query)
            sources.extend(found)
            if not found:
                notes.append(f"No {kind} results returned")
        except Exception as exc:
            notes.append(
                f"{kind.capitalize()} retrieval unavailable ({type(exc).__name__}); no results from this source were used"
            )
    return sources, notes, query
