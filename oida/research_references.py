"""Optional owner research context, separate from acoustic and custody protocols.

Discovery 1.1 is projected here without granting source rights or tool authority.
An exact reference snapshot is retained in the existing reasoning turn; the
bounded evidence packet carries a content-addressed citation back to it.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, quote, unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REFERENCE_CONTRACT = "oida/research-reference/v1"
CONTEXT_CONTRACT = "oida/research-context/v1"
Family = Literal[
    "journal",
    "lexicon",
    "library",
    "people",
    "tools",
    "links",
    "works",
    "radar",
    "calendar",
]
Locale = Literal["en", "es"]
FAMILIES = (
    "journal",
    "lexicon",
    "library",
    "people",
    "tools",
    "links",
    "works",
    "radar",
    "calendar",
)
DEFAULT_FAMILIES = ["journal", "lexicon", "library", "people"]
RESOURCE_TYPES = dict(
    zip(
        FAMILIES,
        (
            "journal_article",
            "lexicon_entry",
            "library_item",
            "people_profile",
            "tool",
            "link",
            "work",
            "radar_review",
            "calendar_item",
        ),
    )
)
CONTENT_KINDS = dict(
    zip(
        FAMILIES,
        (
            "article",
            "definition",
            "summary_or_bibliographic_text",
            "biography",
            "description",
            "description",
            "description",
            "review",
            "event_summary",
        ),
    )
)
Short = Annotated[str, Field(max_length=4000)]
Identifier = Annotated[str, Field(min_length=1, max_length=512)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def text_digest(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def public_url(value: str) -> str:
    p = urlsplit(value)
    if (
        p.scheme not in {"https", "http"}
        or not p.hostname
        or p.username is not None
        or p.password is not None
        or len(value) > 4096
        or re.search(r"[\s\\\x00-\x1f\x7f]", value)
    ):
        raise ValueError("Use a bounded HTTP(S) reference URL without credentials")
    # Validate the port as well; links are metadata only and are never fetched here.
    _ = p.port
    return value


def valid_slug(value: str) -> str:
    decoded = unquote(value, errors="strict")
    if (
        not decoded
        or not decoded[0].isalnum()
        or any(
            not (c.isalnum() or unicodedata.category(c).startswith("M") or c in "._-─")
            for c in decoded
        )
    ):
        raise ValueError(
            "Use an exact public slug without URL syntax or nested encoding"
        )
    return value


class RecordInput(Strict):
    family: Family
    slug: Identifier
    locale: Locale = "en"
    expectedId: Identifier | None = None

    _slug = field_validator("slug")(valid_slug)


class SelectedRecord(RecordInput):
    # A selected search/detail identity is required before attaching to a subject.
    expectedId: Identifier


class PageInput(Strict):
    url: str = Field(min_length=1, max_length=4096)
    locale: Locale = "en"
    # Root article URLs are shared by Journal and Radar. Do not guess the family.
    family: Family | None = None
    expectedId: Identifier | None = None

    def record_input(self) -> RecordInput:
        public_url(self.url)
        p = urlsplit(self.url)
        if (
            p.scheme != "https"
            or p.hostname not in {"sonicfield.org", "www.sonicfield.org"}
            or p.port not in {None, 443}
            or p.fragment
        ):
            raise ValueError("Choose an exact public Sonic Field page")
        path = p.path.rstrip("/")
        query = parse_qs(p.query, keep_blank_values=True, strict_parsing=True)
        item_routes = {
            "/library": "library",
            "/tools/list": "tools",
            "/links/list": "links",
            "/works": "works",
            "/calendar": "calendar",
        }
        if path in item_routes:
            if set(query) != {"item"} or len(query["item"]) != 1:
                raise ValueError("Choose one exact item, not a catalog listing")
            family, slug = item_routes[path], query["item"][0]
        elif path.startswith("/wiki/") and not query:
            family, slug = "lexicon", unquote(path[6:], errors="strict")
        elif path.startswith("/people/") and not query:
            family, slug = "people", unquote(path[8:], errors="strict")
        elif self.family in {"journal", "radar"} and not query and path.count("/") == 1:
            family, slug = self.family, unquote(path[1:], errors="strict")
        else:
            raise ValueError(
                "Use an exact supported page; root articles need journal or radar family"
            )
        if self.family is not None and self.family != family:
            raise ValueError("Page URL and selected family differ")
        return RecordInput(
            family=family, slug=slug, locale=self.locale, expectedId=self.expectedId
        )


FILTER_KEYS = {
    "journal": {
        "type",
        "topic",
        "author",
        "year",
        "source",
        "category",
        "series",
        "contentLanguage",
        "sort",
    },
    "lexicon": {"type", "tag", "confidence", "letter", "sort"},
    "library": {"type", "author", "year", "yearFrom", "yearTo", "tag", "track", "sort"},
    "people": {"kind", "birthplace", "letter", "sort"},
    "tools": {"category", "layer", "access", "license", "status", "tag", "sort"},
    "links": {"category"},
    "works": set(),
    "radar": set(),
    "calendar": {"type", "mode"},
}


class SearchInput(Strict):
    query: str = Field(min_length=1, max_length=120)
    families: list[Family] = Field(
        default_factory=lambda: list(DEFAULT_FAMILIES), min_length=1, max_length=9
    )
    locale: Locale = "en"
    limit: int = Field(default=5, ge=1, le=5)
    filters: dict[Family, dict[str, str]] = Field(default_factory=dict, max_length=9)
    cursor: str | None = Field(default=None, max_length=32768)

    @field_validator("query")
    @classmethod
    def literal_query(cls, value):
        value = " ".join(value.split())
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("Use public-safe literal terms")
        normalized = unicodedata.normalize("NFKD", value.casefold())
        terms = {t for t in re.findall(r"[^\W_]+", normalized) if len(t) >= 2}
        if not 1 <= len(terms) <= 8:
            raise ValueError("Use one to eight literal topic terms")
        return value

    @model_validator(mode="after")
    def supported_filters(self):
        if len(self.families) != len(set(self.families)):
            raise ValueError("Select each family once")
        for family, values in self.filters.items():
            if family not in self.families or set(values) - FILTER_KEYS[family]:
                raise ValueError(
                    "Filters must name supported fields in selected families"
                )
            if any(
                not v or len(v) > 200 or re.search(r"[\x00-\x1f\x7f]", v)
                for v in values.values()
            ):
                raise ValueError("Filter values must be bounded public strings")
        return self


class RelationsInput(Strict):
    family: Literal["lexicon"] = "lexicon"
    slug: Identifier
    predicate: Literal["links-to"] = "links-to"
    locale: Locale = "en"
    limit: int = Field(default=5, ge=1, le=5)

    _slug = field_validator("slug")(valid_slug)


class Identity(Strict):
    family: Family
    id: Identifier
    slug: Identifier
    resourceType: str = Field(min_length=1, max_length=40)

    _slug = field_validator("slug")(valid_slug)

    @model_validator(mode="after")
    def type_matches(self):
        if self.resourceType != RESOURCE_TYPES[self.family]:
            raise ValueError("Public resource type differs from its family")
        return self


class Content(Strict):
    level: Literal["metadata", "excerpt"]
    kind: str = Field(max_length=40)
    format: Literal["text", "markdown"]
    text: str | None = Field(max_length=1200)
    sourceField: Short | None
    truncated: bool
    availableCharacters: int = Field(ge=0)
    trust: Literal["untrusted_source_text"]


class Retrieval(Strict):
    url: str
    retrievedAt: str = Field(min_length=1, max_length=100)
    validatedAt: str = Field(min_length=1, max_length=100)
    status: int = Field(ge=100, le=599)
    conditional: bool
    etag: Short | None
    cacheControl: Short | None
    contentLanguage: Short | None
    schemaVersion: Short | None = None

    _url = field_validator("url")(public_url)


class Rights(Strict):
    status: Short
    evidenceUrl: str | None

    @field_validator("evidenceUrl")
    @classmethod
    def safe_url(cls, value):
        return public_url(value) if value is not None else None


class EventFacts(Strict):
    startsAt: Short | None
    endsAt: Short | None
    timezone: Short | None
    allDay: bool
    precision: Short | None
    submissionDeadline: Short | None
    closed: bool
    mode: Literal["online", "physical"] | None
    location: Short | None
    organizer: Short | None


class PublicRecord(Strict):
    identity: Identity
    title: str = Field(max_length=300)
    canonicalUrl: str
    canonicalEvidence: Literal["source_contract", "negotiated_markdown"]
    sourceUrl: str | None
    sourceName: Short | None
    upstreamCanonicalUrl: str | None
    creators: list[Annotated[str, Field(max_length=300)]] = Field(max_length=20)
    publishedAt: Short | None
    modifiedAt: Short | None
    year: Short | None
    tags: list[Annotated[str, Field(max_length=100)]] = Field(max_length=40)
    requestedLocale: Locale
    contentLanguage: Short | None
    languageStatus: Literal["matches_requested", "fallback", "unknown"]
    content: Content
    rights: Rights
    evidenceConfidence: Short | None
    event: EventFacts | None
    retrieval: Retrieval
    provenanceRetrieval: Retrieval | None
    warnings: list[Short] = Field(max_length=30)

    @field_validator("canonicalUrl", "sourceUrl", "upstreamCanonicalUrl")
    @classmethod
    def safe_url(cls, value):
        return public_url(value) if value is not None else None

    @model_validator(mode="after")
    def faithful_record(self):
        p = urlsplit(self.canonicalUrl)
        if (
            p.scheme != "https"
            or p.hostname not in {"sonicfield.org", "www.sonicfield.org"}
            or p.port not in {None, 443}
        ):
            raise ValueError("Canonical identity must belong to public Sonic Field")
        if self.content.kind != CONTENT_KINDS[self.identity.family]:
            raise ValueError("Content kind differs from its collection")
        slug = quote(self.identity.slug, safe="")
        paths = {
            "lexicon": "/wiki/" + slug,
            "people": "/people/" + slug,
            "journal": "/" + slug,
            "radar": "/" + slug,
            "library": "/library?item=" + slug,
            "tools": "/tools/list?item=" + slug,
            "links": "/links/list?item=" + slug,
            "works": "/works?item=" + slug,
            "calendar": "/calendar?item=" + slug,
        }
        if self.canonicalUrl != "https://sonicfield.org" + paths[self.identity.family]:
            raise ValueError(
                "Canonical item link differs from the public identity contract"
            )
        if len(self.content.text or "") > self.content.availableCharacters:
            raise ValueError("Content depth is inconsistent")
        return self


class Ancestry(Strict):
    status: Literal["unknown", "declared"] = "unknown"
    derived_from: list[Identifier] = Field(default_factory=list, max_length=32)
    source_group: Identifier | None = None
    review: Literal["unreviewed", "accepted", "contested", "rejected", "superseded"] = (
        "unreviewed"
    )


Count = Annotated[int, Field(ge=0)]


class PublicFailure(Strict):
    code: Short
    message: Short
    retryable: bool


class Paging(Strict):
    number: Count
    size: Count
    total: Count
    totalPages: Count
    next: Count | None


class Coverage(Strict):
    family: Family
    query: Short
    filters: dict[str, Short] = Field(max_length=16)
    requestedPage: Count | None
    matching: Literal["upstream_query", "local_page_filter"]
    page: Paging | None
    candidates: Count
    unseenCandidates: Count
    returned: Count
    retrieval: Retrieval | None
    status: Literal["ok", "exhausted", "failed"]
    error: PublicFailure | None

    @model_validator(mode="after")
    def failure_is_attributed(self):
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("Coverage failure is missing or contradicts its status")
        return self


class RankedRecord(PublicRecord):
    score: float = Field(allow_inf_nan=False)
    matchReasons: list[Short] = Field(max_length=64)


class SearchData(Strict):
    results: list[RankedRecord] = Field(max_length=5)
    query: Short
    queryStrategy: Literal["literal_topic_query"]
    rankingScope: Literal["retrieved_candidates"]
    coverage: list[Coverage] = Field(max_length=9)
    partial: bool
    nextCursor: str | None = Field(max_length=32768)
    hasMore: bool
    continuationStopReason: Literal["session_limit"] | None

    @model_validator(mode="after")
    def honest_coverage(self):
        if self.partial != any(c.status == "failed" for c in self.coverage):
            raise ValueError("Partial flag contradicts family failures")
        if len({c.family for c in self.coverage}) != len(self.coverage):
            raise ValueError("Repeated family coverage")
        if self.nextCursor is not None and not self.hasMore:
            raise ValueError("Continuation contradicts remaining coverage")
        if (
            self.hasMore
            and self.nextCursor is None
            and self.continuationStopReason is None
        ):
            raise ValueError(
                "Remaining candidates need continuation or an explicit stop"
            )
        return self


class OutgoingCoverage(Strict):
    rawReferences: Count
    invalidReferences: Count
    duplicateReferences: Count
    selfReferences: Count
    uniqueCandidates: Count
    attempted: Count
    resolved: Count
    unavailable: Count
    failed: Count
    unexamined: Count
    returned: Count
    truncated: bool
    scope: Literal["public_outgoing_wikilinks"]


class OutgoingEdge(Strict):
    target: PublicRecord
    evidenceUrl: str
    predicate: Literal["links-to"]

    _url = field_validator("evidenceUrl")(public_url)


class OutgoingFailure(Strict):
    slug: Identifier
    error: PublicFailure

    _slug = field_validator("slug")(valid_slug)


class RelationsData(Strict):
    focus: PublicRecord
    predicate: Literal["links-to"]
    relations: list[OutgoingEdge] = Field(max_length=5)
    coverage: OutgoingCoverage
    errors: list[OutgoingFailure] = Field(max_length=20)


class ResearchReference(Strict):
    contract: Literal[REFERENCE_CONTRACT] = REFERENCE_CONTRACT
    reference_ref: str = Field(pattern=r"^sfref:[a-f0-9]{64}$")
    role: Literal["context"] = "context"
    origin: Literal["public_discovery"] = "public_discovery"
    record: PublicRecord
    ancestry: Ancestry = Field(default_factory=Ancestry)
    excerpt_sha256: Digest
    snapshot_sha256: Digest

    @model_validator(mode="after")
    def exact_snapshot(self):
        body = self.model_dump(
            mode="json", exclude={"reference_ref", "snapshot_sha256"}
        )
        expected = digest(body)
        if (
            self.snapshot_sha256 != expected
            or self.reference_ref != "sfref:" + expected
            or self.excerpt_sha256 != text_digest(self.record.content.text)
        ):
            raise ValueError("Research reference bytes or snapshot identity changed")
        return self

    @classmethod
    def from_record(cls, record: PublicRecord):
        body = dict(
            contract=REFERENCE_CONTRACT,
            role="context",
            origin="public_discovery",
            record=record.model_dump(mode="json"),
            ancestry=Ancestry().model_dump(mode="json"),
            excerpt_sha256=text_digest(record.content.text),
        )
        snapshot = digest(body)
        return cls.model_validate(
            {**body, "reference_ref": "sfref:" + snapshot, "snapshot_sha256": snapshot}
        )

    def source(self):
        return dict(
            kind="sonicfield",
            title=self.record.title,
            text=self.record.content.text or "",
            reference=self.reference_ref,
            research_reference=self.model_dump(mode="json"),
        )

    def packet_value(self):
        from oida.reasoning.evidence import safe_external_text

        excerpt = safe_external_text(self.record.content.text, limit=1200) or ""

        def safe(value, limit=512):
            return safe_external_text(value, limit=limit) if value is not None else None

        identity = self.record.identity.model_dump()
        identity["id"] = safe(identity["id"])
        ancestry = self.ancestry.model_dump()
        ancestry["derived_from"] = [safe(v) for v in ancestry["derived_from"]]
        ancestry["source_group"] = safe(ancestry["source_group"])
        return dict(
            reference_ref=self.reference_ref,
            snapshot_sha256=self.snapshot_sha256,
            title=safe_external_text(self.record.title, limit=300),
            excerpt=excerpt,
            source_identity=identity,
            role=self.role,
            source_field=safe(self.record.content.sourceField, 120),
            content_level=self.record.content.level,
            content_kind=self.record.content.kind,
            available_characters=self.record.content.availableCharacters,
            truncated=self.record.content.truncated,
            excerpt_sha256=self.excerpt_sha256,
            model_excerpt_sha256=text_digest(excerpt),
            language=safe(self.record.contentLanguage, 35),
            language_status=self.record.languageStatus,
            rights_status=safe(self.record.rights.status, 240),
            ancestry=ancestry,
            source_revision=dict(
                modified_at=safe(self.record.modifiedAt, 100),
                etag=safe(self.record.retrieval.etag, 160),
                retrieved_at=safe(self.record.retrieval.retrievedAt, 100),
                validated_at=safe(self.record.retrieval.validatedAt, 100),
            ),
        )


class ResearchContext(Strict):
    contract: Literal[CONTEXT_CONTRACT] = CONTEXT_CONTRACT
    subject_ref: Identifier
    context_basis: Literal["selected_context", "informed_context"] = "selected_context"
    reference_refs: list[Annotated[str, Field(pattern=r"^sfref:[a-f0-9]{64}$")]] = (
        Field(min_length=1, max_length=5)
    )
    prepared_reference_refs: (
        list[Annotated[str, Field(pattern=r"^sfref:[a-f0-9]{64}$")]] | None
    ) = Field(default=None, max_length=5)
    withheld_reference_refs: (
        list[Annotated[str, Field(pattern=r"^sfref:[a-f0-9]{64}$")]] | None
    ) = Field(default=None, max_length=5)
    delivery_basis: Literal["prepared evidence, not proof of causal influence"] = (
        "prepared evidence, not proof of causal influence"
    )
    action_authority: Literal["observe_only"] = "observe_only"
    claim_limit: Literal[
        "context is not acoustic evidence or independent confirmation"
    ] = "context is not acoustic evidence or independent confirmation"

    @model_validator(mode="after")
    def attributable_preparation(self):
        selected = set(self.reference_refs)
        if len(selected) != len(self.reference_refs):
            raise ValueError("Select each reference snapshot once")
        if (self.prepared_reference_refs is None) != (
            self.withheld_reference_refs is None
        ):
            raise ValueError("Preparation needs both supplied and withheld references")
        if self.prepared_reference_refs is None:
            if self.context_basis != "selected_context":
                raise ValueError("Selection alone does not establish supplied context")
            return self
        prepared, withheld = (
            set(self.prepared_reference_refs),
            set(self.withheld_reference_refs),
        )
        if prepared & withheld or prepared | withheld != selected:
            raise ValueError(
                "Prepared and withheld references must partition the selection"
            )
        if (self.context_basis == "informed_context") != bool(prepared):
            raise ValueError("Context basis differs from the prepared reference input")
        return self
