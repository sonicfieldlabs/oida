"""Selected local context: exact bytes, current uses, egress and retained reads.

All text is authored synthetic material. No private wiki, real model or network.
"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

from akousma import AkousmataStore
from akousma.material_terms import MaterialTerms
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from oida.conversation import ConversationStore
from oida.local_passages import (
    Catalog, CatalogEntry, LocalExposure, LocalPassageDenied, LocalPassages,
    LocalReference, SelectedPassage, install_denial_handler, local_passage_router,
)
from oida.operation_control import Operations
from oida.owner_journal import OwnerJournal
from oida.reasoning.contracts import ProviderDescriptor, ProviderResult, ProviderSettings
from oida.reasoning.orchestrator import TurnOptions
from oida.reasoning.registry import ProviderRegistry
from oida.reasoning_workspace import Ask, Automatic, Workspace
from test_reasoning_orchestrator import _event, _service


READ = ["read", "read_metadata"]
USE = READ + ["execute", "retain", "retain_metadata"]


@pytest.fixture
def pilot(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    original = "Header\nEscucha y diferencia, sin fundir.\r\nOtra línea situada.\nFooter\n".encode()
    source = root / "work.md"
    source.write_bytes(original)
    start, end = len(b"Header\n"), len(original) - len(b"Footer\n")
    entry = CatalogEntry.model_validate(dict(
        passage_id="p-01", corpus="sonicbrain", relative_path="work.md",
        work_ref="work:fixture", title="Synthetic work", creators=["Fixture author"],
        year="2026", language="es", edition="Synthetic edition, not a wiki book",
        locator=dict(format="PDF-derived text", first_line=2, last_line=3,
                     physical_sheet=50, printed_page=54, marker="synthetic marker",
                     mapping_basis="Synthetic mapping; no visual source certification"),
        source_sha256=sha256(original).hexdigest(), source_bytes=len(original),
        start_byte=start, end_byte=end, excerpt_sha256=sha256(original[start:end]).hexdigest(),
        extraction_limit="Authored fixture, no audio or community authority",
        ancestry=dict(status="primary", source_group="work:fixture", derived_from=[], review="owner_declared"),
        terms_ref="fixture:p-01", editorial_paraphrase="Differences remain distinct.", editorial_language="en",
    ))
    manifest = tmp_path / "catalog.json"
    def catalog(entries=None):
        manifest.write_text(Catalog(passages=entries or [entry]).model_dump_json())
    catalog()
    clock = ["2026-10-20T00:00:00+00:00"]
    @contextmanager
    def terms_factory():
        store = AkousmataStore(tmp_path / "canonical")
        try:
            yield MaterialTerms(store, actor_ref="owner:fixture", clock=lambda: clock[0])
        finally:
            store.close()
    def grant(actions=USE, **kwargs):
        with terms_factory() as terms:
            old = terms.get(entry.terms_ref)
            return terms.set(entry.terms_ref, actions=actions, basis="Synthetic owner declaration only",
                             expected_revision=old["revision"] if old else None, **kwargs)
    def selection(**kwargs):
        with terms_factory() as terms:
            declaration = terms.get(entry.terms_ref)
        return SelectedPassage(passage_id=entry.passage_id, expected_source_sha256=entry.source_sha256,
                               expected_excerpt_sha256=entry.excerpt_sha256,
                               terms_revision=declaration["revision"] if declaration else "missing", **kwargs)
    local = LocalPassages(manifest, roots={"sonicbrain": root}, terms_factory=terms_factory)
    return dict(local=local, entry=entry, source=source, original=original,
                catalog=catalog, manifest=manifest, grant=grant, selection=selection,
                terms_factory=terms_factory, clock=clock)


class Provider:
    def __init__(self, provider_id="ollama", reply=None):
        self.provider_id, self.reply, self.requests = provider_id, reply, []
        self.base_url = "http://127.0.0.1:11434"

    def probe(self):
        return ProviderDescriptor(id=self.provider_id, name="Synthetic provider", kind="ollama",
                                  locality="local" if self.provider_id == "ollama" else "unknown",
                                  enabled=True, available=True)

    def list_models(self):
        return []

    def complete(self, request):
        self.requests.append(request)
        payload = self.reply(request) if self.reply else dict(
            answer_blocks=[dict(kind="answer", text="The selected account preserves a difference.",
                evidence_refs=[next(i["ref"] for i in request.metadata["evidence_packet"]["items"]
                                    if i["source"] == "local_passage")])],
            hypotheses=[], uncertainties=["This context does not identify the sound's culture."], suggested_questions=[])
        return ProviderResult(provider_id=self.provider_id, status="ok", parsed=payload, model_id="fixture")


def service(tmp_path, pilot, provider=None):
    s = _service(str(tmp_path / "service"))
    s.local_passages = pilot["local"]
    s.conversations = replace(s.conversations, local_passage_policy=pilot["local"].authorize_conversation)
    if provider:
        settings = s.settings_store.load()
        config = ProviderSettings(kind="ollama" if provider.provider_id == "ollama" else "host_cli",
                                  enabled=True, locality="unknown", base_url="http://127.0.0.1:11434" if provider.provider_id == "ollama" else None)
        s.settings_store.save(settings.model_copy(update={"providers": {**settings.providers, provider.provider_id: config}}))
        registry = ProviderRegistry()
        registry.register(provider, enabled=True, configured=config)
        s.registry_factory = lambda _: registry
    return s


def options(ref, provider="local_structured"):
    return TurnOptions(provider_id=provider, include_memory=False, research=dict(
        scope=dict(local_passages=True), sources=[ref.source()],
        local_exposure=LocalExposure(subject_ref="evt_primary", reference_refs=[ref.reference_ref]).model_dump()))


def workspace(tmp_path, pilot, provider=None):
    s = service(tmp_path, pilot, provider)
    journal = OwnerJournal(tmp_path / "journal.sqlite")
    record = dict(akousma_id="akm_fixture", auditum={"contract": "earworm/auditum/v2"},
                  summary="Retained uncertain account", listening={})
    w = Workspace(journal, s.conversations, s, s.settings_store, Operations(journal), lambda e: e,
                  reader=lambda _: deepcopy(record), retriever=lambda *a: ([], [], "unchanged query"))
    return w, s, record


def test_exact_selected_bytes_locator_language_and_separate_editorial_prose(pilot):
    p = pilot
    assert p["local"].available() == []  # No grant arises from the catalog.
    p["grant"](READ)
    ref = p["local"].read(p["selection"]())
    expected = p["original"][p["entry"].start_byte:p["entry"].end_byte]
    assert ref.excerpt.encode() == expected and b"\r\n" in expected
    assert ref.passage.locator.printed_page == 54 and ref.passage.locator.physical_sheet == 50
    assert ref.passage.language == "es" and ref.passage.editorial_language == "en"
    assert LocalReference.model_validate_json(ref.model_dump_json()) == ref
    assert ref.packet_value()["excerpt"].encode() == expected
    assert "excerpt" not in json.loads(p["manifest"].read_text())["passages"][0]
    assert p["source"].read_bytes() == p["original"]


def test_authority_precedes_source_read_and_rechecks_after_read(pilot, monkeypatch):
    p = pilot
    called = []
    original = p["local"]._bytes
    monkeypatch.setattr(p["local"], "_bytes", lambda entry: called.append(entry))
    with pytest.raises(LocalPassageDenied):
        p["local"].read(p["selection"]())
    assert called == []
    p["grant"](READ)
    selected = p["selection"]()
    def withdraw(entry):
        text = original(entry)
        p["grant"](READ, active=False)
        return text
    monkeypatch.setattr(p["local"], "_bytes", withdraw)
    with pytest.raises(LocalPassageDenied):
        p["local"].read(selected)


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "root_symlink", "changed_bytes", "missing"])
def test_source_must_still_be_regular_exact_bytes_inside_selected_root(pilot, kind):
    p = pilot
    p["grant"]()
    if kind == "changed_bytes":
        p["source"].write_bytes(b"X" + p["original"][1:])
    elif kind == "missing":
        p["source"].unlink()
    elif kind == "file_symlink":
        other = p["source"].with_name("other.md")
        other.write_bytes(p["original"])
        p["source"].unlink()
        p["source"].symlink_to(other)
    elif kind == "parent_symlink":
        p["source"].parent.joinpath("link").symlink_to(p["source"].parent, target_is_directory=True)
        p["catalog"]([p["entry"].model_copy(update={"relative_path": "link/work.md"})])
    else:
        root = p["source"].parent
        link = root.with_name("root-link")
        link.symlink_to(root, target_is_directory=True)
        p["local"].roots["sonicbrain"] = link
    with pytest.raises(LocalPassageDenied):
        p["local"].read(p["selection"]())


@pytest.mark.parametrize("path", ["../work.md", "/tmp/work.md", "a/../work.md", "a//work.md", "file:///work.md", "a\\work.md"])
def test_catalog_cannot_select_paths_outside_its_exact_local_roots(pilot, path):
    with pytest.raises(ValueError):
        CatalogEntry.model_validate(pilot["entry"].model_dump() | dict(relative_path=path))


def test_changed_selection_expiry_and_revision_never_reuse_old_grant(pilot):
    p = pilot
    p["grant"](expires_at="2026-10-20T00:01:00Z")
    selected = p["selection"]()
    for change in [dict(passage_id="not-selected"), dict(expected_source_sha256="a"*64),
                   dict(expected_excerpt_sha256="b"*64), dict(terms_revision="old")]:
        with pytest.raises(LocalPassageDenied):
            p["local"].read(selected.model_copy(update=change))
    p["clock"][0] = "2026-10-20T00:02:00+00:00"
    with pytest.raises(LocalPassageDenied):
        p["local"].read(selected)
    p["grant"]()
    with pytest.raises(LocalPassageDenied):
        p["local"].read(selected)
    assert p["local"].read(p["selection"]())


def test_manifest_is_not_a_whole_corpus_or_an_ancestry_confirmation(pilot):
    e = pilot["entry"]
    for update in [dict(end_byte=e.source_bytes + 1), dict(source_bytes=20000, end_byte=18000),
                   dict(editorial_language=None)]:
        with pytest.raises(ValueError):
            CatalogEntry.model_validate(e.model_dump() | update)
    duplicate = e.model_copy(update={"passage_id": "p-02"})
    a = e.model_copy(update={"ancestry": e.ancestry.model_copy(update={"status": "derived", "derived_from": ["work:other"]})})
    b = duplicate.model_copy(update={"work_ref": "work:other", "ancestry": e.ancestry.model_copy(update={"status": "derived", "derived_from": ["work:fixture"]})})
    with pytest.raises(ValueError, match="Circular"):
        Catalog(passages=[a,b])
    shared = Catalog(passages=[e,duplicate])
    assert shared.passages[0].ancestry.source_group == shared.passages[1].ancestry.source_group


def test_derived_projection_cannot_bypass_a_known_parent_withdrawal(pilot):
    p = pilot
    p["grant"]()
    child = p["entry"].model_copy(update={"passage_id":"child", "work_ref":"work:projection", "terms_ref":"fixture:projection",
        "ancestry":p["entry"].ancestry.model_copy(update={"status":"derived", "derived_from":["work:fixture"]})})
    p["catalog"]([p["entry"],child])
    with p["terms_factory"]() as terms:
        grant = terms.set(child.terms_ref, actions=USE, basis="Synthetic derived consultation")
    selected = p["selection"]().model_copy(update={"passage_id":"child", "terms_revision":grant["revision"]})
    ref = p["local"].read(selected)
    assert ref.passage.ancestry.source_group == "work:fixture"
    p["grant"](USE, active=False)
    with pytest.raises(LocalPassageDenied):
        p["local"].read(selected)
    with pytest.raises(LocalPassageDenied):
        p["local"].check_sources([ref.source()], actions=READ)
    assert p["local"].available() == []


def test_private_context_roundtrips_in_existing_custody_then_withdrawal_withholds_all_views(tmp_path, pilot):
    pilot["grant"]()
    provider = Provider()
    w, s, record = workspace(tmp_path, pilot, provider)
    original = deepcopy(record)
    req = Ask(request_id="selected-local", record_id="akm_fixture", question="Compare this frame with the account",
              provider_id="ollama", scope=dict(memories=False, wiki=False, local_passages=True),
              local_passages=[pilot["selection"]()])
    assert w.enqueue(req)["status"] == "queued"
    assert w.execute_one()
    job = w.journal.get("reasoning_job", req.request_id)
    assert job["status"] == "complete", job
    session = w.session(job["conversation_id"])
    turn = session["turns"][0]
    exposure = turn["research"]["local_exposure"]
    assert exposure["context_basis"] == "informed_context"
    assert exposure["prepared_reference_refs"] == exposure["reference_refs"]
    snapshot = turn["research"]["sources"][0]["local_reference"]
    assert snapshot["excerpt"].encode() in pilot["original"]
    assert "relative_path" not in json.dumps(provider.requests[0].metadata)
    assert "/Users/" not in json.dumps(provider.requests[0].metadata)
    assert s._conversation_history(job["conversation_id"], allow_transcript=True,
                                    allow_memory_content=True, context_scope=dict(local_passages=True)) == []
    # Even a reader without this configured catalog cannot downgrade the snapshot.
    with pytest.raises(LocalPassageDenied):
        ConversationStore(s.conversations.root).get(job["conversation_id"])
    pilot["grant"](USE, active=False)
    with pytest.raises(LocalPassageDenied):
        w.session(job["conversation_id"])
    with pytest.raises(LocalPassageDenied):
        w.public_job(job)
    assert s.conversations.list() == [] and w.visible_jobs() == []
    with pytest.raises(LocalPassageDenied):
        w.enqueue(req)
    assert record == original and len(provider.requests) == 1
    assert s.conversations.delete(job["conversation_id"])  # Erasure doesn't reconstruct a denied payload.
    w.close()


@pytest.mark.parametrize("allow,grant_external,permitted", [(False,False,False),(True,False,False),(False,True,False),(True,True,True)])
def test_external_model_requires_both_selected_use_and_current_disclosure(tmp_path, pilot, allow, grant_external, permitted):
    pilot["grant"](USE + (["disclose_external"] if grant_external else []))
    ref = pilot["local"].read(pilot["selection"](allow_external=allow))
    provider = Provider("codex")
    s = service(tmp_path, pilot, provider)
    if permitted:
        result = s.ask(event=_event(), question="Compare", options=options(ref, "codex"))
        assert result["turn"]["research"]["local_exposure"]["context_basis"] == "informed_context"
        assert len(provider.requests) == 1
    else:
        with pytest.raises(LocalPassageDenied):
            s.ask(event=_event(), question="Compare", options=options(ref, "codex"))
        assert provider.requests == [] and s.conversations.list() == []


def test_revocation_during_provider_call_discards_response_before_commit(tmp_path, pilot):
    pilot["grant"]()
    ref = pilot["local"].read(pilot["selection"]())
    def withdraw(request):
        pilot["grant"](USE, active=False)
        return dict(answer_blocks=[dict(kind="answer", text="Must be discarded", evidence_refs=[request.metadata["evidence_packet"]["items"][0]["ref"]])],
                    hypotheses=[], uncertainties=[], suggested_questions=[])
    provider = Provider(reply=withdraw)
    s = service(tmp_path, pilot, provider)
    with pytest.raises(LocalPassageDenied):
        s.ask(event=_event(), question="Compare", options=options(ref, "ollama"))
    assert not list(s.conversations.root.glob("*.json")) and len(provider.requests) == 1


def test_bounded_model_cannot_cite_or_receive_a_withheld_private_item(tmp_path, pilot, monkeypatch):
    from oida.reasoning.bounded import BOUNDED_CONTEXT
    monkeypatch.setitem(BOUNDED_CONTEXT, "ollama", dict(inquiry_chars=100))
    pilot["grant"]()
    ref = pilot["local"].read(pilot["selection"]())
    cited = "context:" + ref.reference_ref
    def unsupported(request):
        packet = request.metadata["evidence_packet"]
        assert cited not in {i["ref"] for i in packet["items"]}
        assert ref.excerpt not in request.user_prompt and ref.excerpt not in json.dumps(request.metadata)
        return dict(answer_blocks=[dict(kind="answer", text="An unsupported citation", evidence_refs=[cited])],
                    hypotheses=[], uncertainties=[], suggested_questions=[])
    provider = Provider(reply=unsupported)
    s = service(tmp_path, pilot, provider)
    result = s.ask(event=_event(), question="Compare", options=options(ref, "ollama"))
    turn = result["turn"]
    assert turn["fallback"]["used"] and len(provider.requests) == 2
    assert turn["research"]["local_exposure"]["prepared_reference_refs"] == []
    assert turn["research"]["local_exposure"]["withheld_reference_refs"] == [ref.reference_ref]
    assert cited not in turn["audit"]["evidence_refs"]
    assert cited in turn["audit"]["withheld_evidence_refs"]


def test_host_prepare_and_mislabelled_external_provider_cannot_export_local_only_context(tmp_path, pilot):
    pilot["grant"]()
    ref = pilot["local"].read(pilot["selection"]())
    provider = Provider()
    s = service(tmp_path, pilot, provider)
    with pytest.raises(LocalPassageDenied):
        s.prepare(event=_event(), question="Compare", options=options(ref, "ollama"))
    # A local settings label cannot make a host CLI's upstream model local.
    host = Provider("codex")
    settings = s.settings_store.load()
    s.settings_store.save(settings.model_copy(update={"providers":{**settings.providers,
        "codex":ProviderSettings(kind="host_cli", enabled=True, locality="local")}}))
    registry = ProviderRegistry()
    registry.register(host, enabled=True)
    s.registry_factory = lambda _: registry
    with pytest.raises(LocalPassageDenied):
        s.ask(event=_event(), question="Compare", options=options(ref, "codex"))
    assert provider.requests == host.requests == []


def test_scope_retention_and_automatic_selection_fail_before_queue_writes(tmp_path, pilot):
    pilot["grant"](READ)
    w, _, _ = workspace(tmp_path, pilot)
    def request(**changes):
        return Ask.model_validate(dict(record_id="akm_fixture", question="Compare", scope=dict(local_passages=True),
                                       local_passages=[pilot["selection"]().model_dump()]) | changes)
    with pytest.raises(LocalPassageDenied):
        w.enqueue(request())
    assert w.values("reasoning_job") == []
    pilot["grant"]()
    for change in [dict(scope=dict(local_passages=False)), dict(scope=dict(local_passages=True, web=True)), dict(web_query="private words")]:
        with pytest.raises(HTTPException):
            w.enqueue(request(**change))
    with pytest.raises(HTTPException):
        w.configure(Automatic(enabled=True, scope=dict(local_passages=True)))
    assert w.values("reasoning_job") == []
    w.close()


def test_private_read_routes_are_bounded_current_and_never_export_or_infer(pilot):
    app = FastAPI()
    install_denial_handler(app)
    calls = []
    def admin(request):
        calls.append(request.url.path)
        if request.client.host != "testclient":
            raise HTTPException(403, "Local only")
    app.include_router(local_passage_router(pilot["local"], admin))
    client = TestClient(app)
    assert client.get("/research/local/status").json()["model_call"] is False
    assert client.get("/research/local/catalog").json()["passages"] == []
    pilot["grant"](READ)
    payload = pilot["selection"]().model_dump()
    response = client.post("/research/local/read", json=payload)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    for extra in [dict(path="/etc/passwd"),dict(export=True),dict(training=True),dict(query="infer")]:
        assert client.post("/research/local/read", json=payload | extra).status_code == 422
    assert client.post("/research/local/export", json=payload).status_code == 404
    assert client.post("/research/local/read", content=b"x"*8193).status_code == 413
    assert TestClient(app, client=("192.0.2.1", 1000)).get("/research/local/catalog").status_code == 403
    pilot["grant"](READ, active=False)
    denied = client.post("/research/local/read", json=payload)
    assert denied.status_code == 423 and "p-01" not in denied.text and "Escucha" not in denied.text


def test_mounted_owner_private_routes_and_conversation_reads_recheck_uses(tmp_path, pilot, monkeypatch):
    import os
    import oida.local_passages as module
    from oida.server import create_app
    for key in list(os.environ):
        if key.startswith(("OIDA_", "HMM_", "AEAR_", "LISTENINGSTACK_WORKSPACE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "owner"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("OIDA_SONICFIELD_ROOT", str(tmp_path / "missing-site"))
    monkeypatch.setenv("OIDA_LOCAL_PASSAGES_MANIFEST", str(pilot["manifest"]))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "unused-default-store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    monkeypatch.setattr(module, "ROOTS", pilot["local"].roots)
    monkeypatch.setattr(module, "shared_terms", lambda actor: pilot["terms_factory"]())
    app = create_app(profile="stub")
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/research/local/status").json()["configured"]
    pilot["grant"]()
    read = client.post("/research/local/read", json=pilot["selection"]().model_dump())
    assert read.status_code == 200 and read.json()["passage"]["locator"]["printed_page"] == 54
    remote = TestClient(app, client=("192.0.2.1", 50000), base_url="http://127.0.0.1")
    assert remote.get("/research/local/catalog").status_code == 403
    s = service(tmp_path, pilot)
    # Persist to the same owner conversation directory using the real custody seam.
    s.conversations = ConversationStore(tmp_path / "owner/sessions/conversations", local_passage_policy=pilot["local"].authorize_conversation)
    result = s.ask(event=_event(), question="Compare", options=options(LocalReference.model_validate(read.json())))
    identifier = result["conversation_id"]
    assert client.get("/conversation/" + identifier).status_code == 200
    assert remote.get("/conversation/" + identifier).status_code == 403
    assert remote.get("/conversation").json()["conversations"] == []
    for suffix in ["ask", "ask/stream", "prepare"]:
        assert remote.post("/conversation/" + suffix,
                           json=dict(conversation_id=identifier, question="Continue")).status_code == 403
    pilot["grant"](USE, active=False)
    assert client.get("/conversation/" + identifier).status_code == 423
    assert client.get("/conversation").json()["conversations"] == []
