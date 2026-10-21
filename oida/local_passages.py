"""Explicit local passage consultation, using the shared owner's MaterialTerms.

The catalog contains selected locators and hashes, never a corpus or a grant.
No source text is indexed, cached, translated, exported or reconstructed here.
"""

from __future__ import annotations

from contextlib import contextmanager
import asyncio
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator, model_validator

from oida.research_references import Digest, Strict, digest


CONTRACT = "oida/local-passage/v1"
MAX_PASSAGE_BYTES = 16_384
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_CATALOG_BYTES = 512 * 1024
Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,160}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
ROOTS = {
    "aleph": Path.home() / "Documents/CHIXI/ALEPH/wiki",
    "sonicbrain": Path.home() / "Documents/SFL/sonicbrain",
    "listeningmd": Path.home() / "Documents/SFL/listeningmd",
    "akdraft": Path.home() / "Documents/SFL/akdraft",
}


class LocalPassageDenied(PermissionError):
    def __init__(self):
        super().__init__("Selected local context is unavailable under current uses")


class Locator(Strict):
    format: Text
    first_line: int = Field(ge=1)
    last_line: int = Field(ge=1)
    physical_sheet: int | None = Field(default=None, ge=1)
    printed_page: int | None = Field(default=None, ge=1)
    marker: str | None = Field(default=None, max_length=200)
    section: str | None = Field(default=None, max_length=1000)
    document: int | None = Field(default=None, ge=0)
    member: str | None = Field(default=None, max_length=300)
    segments: str | None = Field(default=None, max_length=200)
    printed_page_hints: str | None = Field(default=None, max_length=300)
    mapping_basis: Text

    @model_validator(mode="after")
    def ordered(self):
        if self.last_line < self.first_line:
            raise ValueError("Selected lines must be ordered")
        return self


class Ancestry(Strict):
    status: Literal["primary", "derived", "unknown"]
    source_group: Key | None
    derived_from: list[Key] = Field(max_length=32)
    review: Literal["owner_declared", "unreviewed"]

    @model_validator(mode="after")
    def explicit(self):
        if len(self.derived_from) != len(set(self.derived_from)):
            raise ValueError("Duplicate ancestry")
        if self.status == "derived" and not self.derived_from:
            raise ValueError("Derived sources need declared parents")
        if self.status == "unknown" and (self.source_group or self.derived_from):
            raise ValueError("Unknown ancestry must remain unknown")
        return self


class Passage(Strict):
    passage_id: Key
    corpus: Literal["aleph", "sonicbrain", "listeningmd", "akdraft"]
    work_ref: Key
    title: Text
    creators: list[Text] = Field(min_length=1, max_length=16)
    year: str = Field(min_length=1, max_length=40)
    language: str = Field(min_length=1, max_length=40)
    edition: Text
    locator: Locator
    source_sha256: Digest
    source_bytes: int = Field(ge=1, le=MAX_SOURCE_BYTES)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=1)
    excerpt_sha256: Digest
    extraction_limit: Text
    ancestry: Ancestry
    terms_ref: Key
    editorial_paraphrase: str | None = Field(default=None, max_length=4000)
    editorial_language: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def bounded(self):
        if not self.start_byte < self.end_byte <= self.source_bytes:
            raise ValueError("Select an exact nonempty original byte range")
        if self.end_byte - self.start_byte > MAX_PASSAGE_BYTES:
            raise ValueError("Select a smaller passage; hidden prefixes are forbidden")
        if bool(self.editorial_paraphrase) != bool(self.editorial_language):
            raise ValueError("Label editorial prose separately from source language")
        if self.work_ref in self.ancestry.derived_from:
            raise ValueError("A source cannot derive from itself")
        return self


class CatalogEntry(Passage):
    relative_path: str = Field(min_length=1, max_length=1024)

    @field_validator("relative_path")
    @classmethod
    def local_path(cls, value):
        path = PurePosixPath(value)
        if (path.is_absolute() or str(path) != value or "\\" in value
                or any(p in {".", ".."} for p in path.parts)
                or any(ord(c) < 32 for c in value) or ":" in value):
            raise ValueError("Use an exact relative local file path")
        return value

    def passage(self):
        return Passage.model_validate(self.model_dump(exclude={"relative_path"}))


class Catalog(Strict):
    contract: Literal["oida/local-passage-catalog/v1"] = "oida/local-passage-catalog/v1"
    passages: list[CatalogEntry] = Field(max_length=128)

    @model_validator(mode="after")
    def unique(self):
        if len({p.passage_id for p in self.passages}) != len(self.passages):
            raise ValueError("Duplicate selected passage")
        parents = {}
        work_terms = {}
        for p in self.passages:
            if p.work_ref in parents and parents[p.work_ref] != p.ancestry:
                raise ValueError("Inconsistent work ancestry")
            parents[p.work_ref] = p.ancestry
            if p.work_ref in work_terms and work_terms[p.work_ref] != p.terms_ref:
                raise ValueError("Selected slices of one work must share its withdrawal scope")
            work_terms[p.work_ref] = p.terms_ref

        def visit(key, seen):
            if key in seen:
                raise ValueError("Circular declared source ancestry")
            for parent in parents[key].derived_from:
                if parent in parents:
                    visit(parent, seen | {key})
        for key in parents:
            visit(key, set())
        return self


class SelectedPassage(Strict):
    passage_id: Key
    expected_source_sha256: Digest
    expected_excerpt_sha256: Digest
    terms_revision: Key
    allow_external: bool = False


class LocalReference(Strict):
    contract: Literal[CONTRACT] = CONTRACT
    reference_ref: str = Field(pattern=r"^lpref:[a-f0-9]{64}$")
    role: Literal["context"] = "context"
    origin: Literal["selected_local_passage"] = "selected_local_passage"
    passage: Passage
    selection: SelectedPassage
    excerpt: str = Field(min_length=1, max_length=MAX_PASSAGE_BYTES)
    snapshot_sha256: Digest

    @model_validator(mode="after")
    def exact(self):
        body = self.model_dump(mode="json", exclude={"reference_ref", "snapshot_sha256"})
        expected = digest(body)
        if (self.snapshot_sha256 != expected or self.reference_ref != "lpref:" + expected
                or self.selection.passage_id != self.passage.passage_id
                or self.selection.expected_source_sha256 != self.passage.source_sha256
                or self.selection.expected_excerpt_sha256 != self.passage.excerpt_sha256
                or sha256(self.excerpt.encode("utf-8")).hexdigest() != self.passage.excerpt_sha256
                or len(self.excerpt.encode("utf-8")) != self.passage.end_byte - self.passage.start_byte):
            raise ValueError("Selected passage, exact bytes and snapshot disagree")
        return self

    def source(self):
        # Avoid parallel title/text copies of private material in research custody.
        return dict(kind="local_passage", reference=self.reference_ref,
                    local_reference=self.model_dump(mode="json"))

    def packet_value(self):
        from oida.reasoning.evidence import safe_external_text, safe_selected_passage
        def clean(value):
            if isinstance(value, str):
                return safe_external_text(value, limit=MAX_PASSAGE_BYTES) or ""
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            return value
        text = safe_selected_passage(self.excerpt)
        return dict(contract=CONTRACT, reference_ref=self.reference_ref,
                    passage=clean(self.passage.model_dump(mode="json", exclude={"terms_ref"})),
                    excerpt=text, model_excerpt_sha256=sha256(text.encode()).hexdigest(),
                    model_projection="Locator redaction only; original whitespace retained, no prefix or translation",
                    snapshot_sha256=self.snapshot_sha256,
                    claim_limit="Context only; shared ancestry is not independent confirmation. Editorial prose is not a quotation or translation.")


class LocalExposure(Strict):
    contract: Literal["oida/local-context/v1"] = "oida/local-context/v1"
    subject_ref: Key
    context_basis: Literal["selected_context", "informed_context"] = "selected_context"
    reference_refs: list[Annotated[str, Field(pattern=r"^lpref:[a-f0-9]{64}$")]] = Field(max_length=5)
    prepared_reference_refs: list[str] | None = None
    withheld_reference_refs: list[str] | None = None
    delivery_basis: Literal["prepared evidence, not proof of causal influence"] = "prepared evidence, not proof of causal influence"
    action_authority: Literal["observe_only"] = "observe_only"

    @model_validator(mode="after")
    def partition(self):
        refs, sent, held = self.reference_refs, self.prepared_reference_refs, self.withheld_reference_refs
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate selected local reference")
        if sent is None and held is None:
            if self.context_basis != "selected_context":
                raise ValueError("Selection is not prepared exposure")
        elif (sent is None or held is None or set(sent) & set(held)
                or sorted(sent + held) != sorted(refs)
                or self.context_basis != ("informed_context" if sent else "selected_context")):
            raise ValueError("Prepared and withheld context must partition the selection")
        return self


@contextmanager
def shared_terms(actor_ref):
    from akousma import AkousmataStore
    from akousma.material_terms import MaterialTerms
    store = AkousmataStore()
    try:
        yield MaterialTerms(store, actor_ref=actor_ref)
    finally:
        store.close()


class LocalPassages:
    def __init__(self, manifest=None, *, roots=None, terms_factory=None, actor_ref="owner:oida-local"):
        self.manifest = Path(manifest) if manifest else None
        self.roots = dict(ROOTS if roots is None else roots)
        self.terms_factory = terms_factory or (lambda: shared_terms(actor_ref))

    @classmethod
    def from_environment(cls, **kwargs):
        return cls(os.environ.get("OIDA_LOCAL_PASSAGES_MANIFEST"), **kwargs)

    def catalog(self):
        if self.manifest is None:
            raise LocalPassageDenied()
        try:
            if any(self.manifest.resolve().is_relative_to(Path(p).resolve()) for p in self.roots.values()):
                raise LocalPassageDenied()  # Indexes stay outside source repositories.
            with self.manifest.open("rb") as stream:
                raw = stream.read(MAX_CATALOG_BYTES + 1)
            if len(raw) > MAX_CATALOG_BYTES:
                raise ValueError("Catalog exceeds bounds")
            return Catalog.model_validate_json(raw)
        except (OSError, ValueError):
            raise LocalPassageDenied() from None

    def _entry(self, selection):
        entry = next((p for p in self.catalog().passages if p.passage_id == selection.passage_id), None)
        if (entry is None or entry.source_sha256 != selection.expected_source_sha256
                or entry.excerpt_sha256 != selection.expected_excerpt_sha256):
            raise LocalPassageDenied()
        return entry

    def _permit(self, entry, selection, actions, *, external=False):
        if external and not selection.allow_external:
            raise LocalPassageDenied()
        with self.terms_factory() as terms:
            current = terms.get(entry.terms_ref)
            required = set(actions) | ({"disclose_external"} if external else set())
            if (current is None or current["revision"] != selection.terms_revision
                    or not self._allows_chain(terms, entry, required, self.catalog())):
                raise LocalPassageDenied()

    @staticmethod
    def _allows_chain(terms, entry, actions, catalog):
        by_work = {p.work_ref: p for p in catalog.passages}
        def visit(value, seen):
            if value.work_ref in seen or not all(terms.allows(a, value.terms_ref) for a in actions):
                return False
            # Declared catalog parents retain their current restrictions. An
            # unresolved scholarly lineage is not invented as a permission or
            # independent confirmation; the selected owner declaration still applies.
            return all(visit(by_work[parent], seen | {value.work_ref})
                       for parent in value.ancestry.derived_from if parent in by_work)
        return visit(entry, set())

    def available(self):
        catalog = self.catalog()
        values = []
        with self.terms_factory() as terms:
            for entry in catalog.passages:
                if self._allows_chain(terms, entry, ("read_metadata",), catalog):
                    declaration = terms.get(entry.terms_ref)
                    values.append(dict(passage=entry.passage().model_dump(mode="json"),
                                       terms_revision=declaration["revision"]))
        return values

    def _bytes(self, entry):
        """Read only regular files via no-follow descriptors, including root parents."""
        root = Path(self.roots[entry.corpus])
        if not root.is_absolute():
            raise LocalPassageDenied()
        descriptors = []
        try:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(fd)
            for part in (*root.parts[1:], *PurePosixPath(entry.relative_path).parts[:-1]):
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            fd = os.open(PurePosixPath(entry.relative_path).name,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            descriptors.append(fd)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != entry.source_bytes:
                raise LocalPassageDenied()
            hasher = sha256()
            while chunk := os.read(fd, 65536):
                hasher.update(chunk)
                if os.lseek(fd, 0, os.SEEK_CUR) > MAX_SOURCE_BYTES:
                    raise LocalPassageDenied()
            selected = os.pread(fd, entry.end_byte - entry.start_byte, entry.start_byte)
            after = os.fstat(fd)
            if (hasher.hexdigest() != entry.source_sha256
                    or sha256(selected).hexdigest() != entry.excerpt_sha256
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise LocalPassageDenied()
            return selected.decode("utf-8", errors="strict")
        except (OSError, ValueError, KeyError):
            raise LocalPassageDenied() from None
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def read(self, selection, *, actions=("read", "read_metadata"), external=False):
        entry = self._entry(selection)
        self._permit(entry, selection, actions, external=external)
        excerpt = self._bytes(entry)
        self._permit(entry, selection, actions, external=external)
        body = dict(contract=CONTRACT, role="context", origin="selected_local_passage",
                    passage=entry.passage().model_dump(mode="json"),
                    selection=selection.model_dump(mode="json"), excerpt=excerpt)
        snapshot = digest(body)
        return LocalReference.model_validate({**body, "reference_ref": "lpref:" + snapshot,
                                              "snapshot_sha256": snapshot})

    def check_sources(self, sources, *, actions, external=False):
        for source in sources:
            if source.get("kind") != "local_passage":
                continue
            try:
                ref = LocalReference.model_validate(source.get("local_reference"))
                entry = self._entry(ref.selection)
                if entry.passage() != ref.passage or source.get("reference") != ref.reference_ref:
                    raise LocalPassageDenied()
                self._permit(entry, ref.selection, actions, external=external)
            except (ValueError, TypeError):
                raise LocalPassageDenied() from None

    def check_selections(self, selections, *, actions):
        if len({s.passage_id for s in selections}) != len(selections):
            raise ValueError("Select each local passage once")
        for selection in selections:
            self._permit(self._entry(selection), selection, actions)

    def authorize_conversation(self, conversation, *, actions=("read", "read_metadata")):
        for turn in conversation.get("turns", []):
            self.check_sources((turn.get("research") or {}).get("sources", []), actions=actions)


def local_passage_router(local, require_admin):
    router = APIRouter(prefix="/research/local", tags=["Selected local research"])

    @router.get("/status")
    def status(request: Request):
        require_admin(request)
        return dict(contract=CONTRACT, configured=local.manifest is not None,
                    selection="explicit exact passages; no source indexing or automatic grants",
                    max_passage_bytes=MAX_PASSAGE_BYTES, max_selected=5,
                    export="unavailable", training="unavailable", model_call=False)

    @router.get("/catalog")
    def catalog(request: Request):
        require_admin(request)
        return dict(contract="oida/local-passage-catalog-view/v1", passages=local.available())

    @router.post("/read", openapi_extra={"requestBody":{"required":True, "content":{
        "application/json":{"schema":SelectedPassage.model_json_schema()}}}})
    async def read(request: Request):
        require_admin(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(413, "Selected passage request exceeds 8 KiB")
        try:
            selection = SelectedPassage.model_validate_json(raw)
        except ValueError:
            raise HTTPException(422, "Expected one exact selected passage identity, hashes and terms revision") from None
        reference = await asyncio.to_thread(local.read, selection)
        return reference.model_dump(mode="json")

    return router


def install_denial_handler(app):
    from fastapi.responses import JSONResponse
    @app.exception_handler(LocalPassageDenied)
    async def unavailable(_request, _exc):
        return JSONResponse(status_code=423, content={"detail": str(LocalPassageDenied())})

    @app.middleware("http")
    async def private_cache(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/research/local", "/reasoning/workspace", "/conversation")):
            response.headers["Cache-Control"] = "no-store"
        return response
