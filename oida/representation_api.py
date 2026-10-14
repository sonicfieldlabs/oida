"""Versioned local-owner inscription and LISTENING.md lifecycle endpoints."""

import base64
from contextlib import contextmanager
import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from akousma import AkousmataStore
from akousma.material_terms import MaterialTerms
from akousma.representation_store import RepresentationStore
from akouo_contract import root as akouo_root
from akouo_contract.listening_profiles import load_listening_presets, _scope, SUPPORTED_CONTROLS
from .listening_profiles import ListeningProfiles
from .representation_inputs import CONVENTIONS, representation
from .textual_sounding import TextualExecution


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftBody(Body):
    document_id: str = Field(min_length=1, max_length=255)
    text: str = Field(max_length=1048576)
    expected_sha256: str | None = None


class FreezeBody(Body):
    document_id: str
    expected_sha256: str
    authors: list[str] = Field(min_length=1, max_length=32)
    terms_ref: str
    purpose: str = "orientation"
    previous_revision: str | None = None
    fork: bool = False


class AuthorityBody(Body):
    scope_path: list[dict]
    party_refs: list[str] = Field(min_length=1, max_length=32)
    actions: list[str] = Field(min_length=1, max_length=4)
    basis: str = Field(min_length=1, max_length=4096)


class AdoptBody(Body):
    revision_ref: str
    scope_path: list[dict]
    party_refs: list[str]
    authority_ref: str
    expected_generation: int = Field(ge=0)
    replace_ref: str | None = None
    expires_at: str | None = None


class RevokeBody(Body):
    authority_ref: str
    expected_generation: int = Field(ge=0)


class TermsBody(Body):
    actions: list[str] = Field(max_length=10)
    basis: str = Field(min_length=1, max_length=4096)
    expected_revision: str | None = None
    active: bool = True
    expires_at: str | None = None


class SourceBody(Body):
    material_base64: str = Field(max_length=1398104)
    form: str = "text"
    media_type: str = "text/plain"
    terms_ref: str
    source_class: str = "authored_work"
    origin: dict | None = None
    convention: dict | None = None


class ExecuteBody(Body):
    source: SourceBody | None = None
    source_ref: str | None = None
    intent: str
    provider_id: str
    model_id: str
    scope_path: list[dict] | None = None
    authority_ref: str | None = None
    remember: bool = False
    selection: list[dict] | None = None
    context_inputs: list[dict] = Field(default_factory=list, max_length=32)
    context_selections: dict | None = None
    expected_profile_generation: int | None = Field(default=None, ge=0)
    return_to: dict | None = None
    operation_ref: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class RevisitBody(Body):
    representation_ref: str
    context_basis: str = "independent"
    account_refs: list[str] = Field(default_factory=list, max_length=32)


def build_router(
    *,
    data_dir,
    memory_root,
    workspace_ref,
    actor_ref,
    registry_factory,
    require_admin,
    legacy_covenant=None,
    execution_budget_factory=None,
):
    router = APIRouter(tags=["Primary inscriptions"])

    def host_scope(path):
        _scope(path)
        if path[0] != dict(kind="workspace", ref=workspace_ref):
            raise PermissionError("Use this authenticated workspace")
        if any(item["kind"] not in {"workspace", "workflow"} for item in path):
            raise ValueError("Run/swarm scopes need a coordinator adapter; invocations are owner assigned")
        return path

    def host_parties(parties):
        if parties != [actor_ref]:
            raise PermissionError("This host has no authenticated multi-party coordinator")

    @contextmanager
    def context():
        store = AkousmataStore(memory_root())
        profiles = None
        try:
            terms = MaterialTerms(store, actor_ref=actor_ref)
            memory = RepresentationStore(store, authorize=terms.permit)
            profiles = ListeningProfiles(
                data_dir / "listening-profiles.sqlite3",
                workspace_ref=workspace_ref,
                actor_ref=actor_ref,
                material_terms=terms,
            )
            yield profiles, memory, terms
        finally:
            if profiles:
                profiles.close()
            store.close()

    def run(request, action):
        require_admin(request)
        try:
            with context() as parts:
                return action(*parts)
        except PermissionError:
            raise HTTPException(
                403, "Current owner authority or material terms refuse this operation"
            ) from None
        except KeyError:
            raise HTTPException(
                404, "Source, revision or retained operation is unavailable"
            ) from None
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(
                422, "Invalid representation, binding, convention or context budget"
            ) from None

    @router.get("/representations/capabilities")
    def capabilities(request: Request):
        require_admin(request)
        return dict(
            contract="oida/representation-execution/v1",
            status="opt_in",
            conventions=CONVENTIONS,
            max_source_bytes=1048576,
            max_context_bytes=65536,
            max_prompt_bytes=131072,
            output_forms=["text"],
            output_kinds=["account", "work"],
            audio_required=False,
            adoption_default="none",
            workspace_ref=workspace_ref,
            actor_ref=actor_ref,
            token_budget="provider-dependent; unknown without a qualified counter",
            supported_scopes=["workspace", "workflow"],
            coordinator_scopes="unavailable; no authenticated run/swarm adapter or participant roster",
            supported_controls=list(SUPPORTED_CONTROLS),
            authenticated_party_refs=[actor_ref],
        )

    @router.get("/listening/profiles")
    def profiles_status(request: Request):
        return run(request, lambda p, _m, _t: p.state())

    @router.get("/representations/providers")
    def text_providers(request: Request):
        require_admin(request)
        registry = registry_factory()
        providers = []
        for provider_id in registry.enabled_ids():
            if provider_id == "local_structured":
                continue
            descriptor = registry.probe(provider_id)
            if "text" in descriptor.capabilities:
                providers.append(descriptor.model_dump(mode="json", include={
                    "id", "name", "enabled", "available", "authenticated", "capabilities", "locality"}))
        return dict(contract="oida/text-providers/v1", providers=providers,
                    selection="explicit; configure other providers in the owner")

    @router.get("/listening/profiles/presets")
    def presets(request: Request):
        require_admin(request)
        return load_listening_presets()

    @router.post("/listening/profiles/presets/{name}")
    def import_preset(name: str, request: Request):
        def load(p, _m, _t):
            entry = next(
                (v for v in load_listening_presets()["presets"] if v["id"] == name),
                None,
            )
            if entry is None:
                raise KeyError(name)
            folder = akouo_root() / entry["directory"]
            document = json.loads((folder / "document.json").read_text())
            profile = json.loads((folder / "profile.json").read_text())
            p.import_canonical(
                document, (folder / "LISTENING.md").read_bytes(), profile
            )
            return dict(
                document=document,
                profile=profile,
                text=(folder / "LISTENING.md").read_text(),
                adoption="not_adopted",
            )

        return run(request, load)

    @router.put("/listening/profiles/drafts")
    def save_draft(body: DraftBody, request: Request):
        return run(
            request,
            lambda p, _m, _t: p.save_draft(
                body.document_id,
                body.text.encode("utf-8"),
                expected_sha256=body.expected_sha256,
            ),
        )

    @router.get("/listening/profiles/drafts/{document_id}")
    def get_draft(document_id: str, request: Request):
        return run(request, lambda p, _m, _t: p.draft(document_id))

    @router.post("/listening/profiles/revisions")
    def freeze(body: FreezeBody, request: Request):
        return run(request, lambda p, _m, _t: p.freeze(**body.model_dump()))

    @router.get("/listening/profiles/revisions/{revision_ref}")
    def revision(revision_ref: str, request: Request):
        def load(p, _m, _t):
            doc, material, profile = p.resolve(revision_ref)
            return dict(document=doc, profile=profile, text=material.decode())

        return run(request, load)

    @router.post("/listening/profiles/authorities")
    def authority(body: AuthorityBody, request: Request):
        def act(p, _m, _t):
            host_scope(body.scope_path)
            host_parties(body.party_refs)
            return p.grant_authority(**body.model_dump())
        return run(request, act)

    @router.delete("/listening/profiles/authorities/{authority_ref}")
    def revoke_authority(authority_ref: str, request: Request):
        return run(request, lambda p, _m, _t: p.revoke_authority(authority_ref))

    @router.post("/listening/profiles/adoptions")
    def adopt(body: AdoptBody, request: Request):
        def act(p, _m, _t):
            host_scope(body.scope_path)
            host_parties(body.party_refs)
            return p.adopt(**body.model_dump())
        return run(request, act)

    @router.post("/listening/profiles/adoptions/{adoption_ref}/revoke")
    def revoke(adoption_ref: str, body: RevokeBody, request: Request):
        return run(
            request, lambda p, _m, _t: p.revoke(adoption_ref, **body.model_dump())
        )

    @router.put("/representations/terms/{terms_ref}")
    def terms(terms_ref: str, body: TermsBody, request: Request):
        return run(request, lambda _p, _m, t: t.set(terms_ref, **body.model_dump()))

    @router.get("/representations/terms/{terms_ref}")
    def term_status(terms_ref: str, request: Request):
        return run(request, lambda _p, _m, t: t.get(terms_ref))

    @router.post("/representations/execute")
    def execute(body: ExecuteBody, request: Request):
        def act(profiles, memory, _terms):
            selected_scope = host_scope(body.scope_path or [dict(kind="workspace", ref=workspace_ref)])
            if (body.source is None) == (body.source_ref is None):
                raise ValueError("Choose exactly one new or retained source")
            if body.source:
                metadata = body.source.model_dump()
                data = base64.b64decode(metadata.pop("material_base64"), validate=True)
                source = representation(data, **metadata)
            else:
                source = memory.representation(body.source_ref)
                data = memory.read_bytes(body.source_ref)
            context_material = []
            for item in body.context_inputs:
                if not {"representation_ref", "role"} <= set(item) or set(item) - {"representation_ref", "role", "selection"}:
                    raise ValueError(
                        "Context needs an exact retained identity and role"
                    )
                identity = item["representation_ref"]
                context_material.append(
                    (
                        memory.representation(identity),
                        memory.read_bytes(identity),
                        item["role"],
                        item.get("selection"),
                    )
                )
            registry = registry_factory()
            budget = (execution_budget_factory(registry, body.provider_id, body.model_id)
                      if execution_budget_factory else {})
            service = TextualExecution(
                profiles=profiles,
                memory=memory,
                registry=registry,
                legacy_covenant=legacy_covenant,
                **budget,
            )
            return service.execute(
                source,
                data,
                intent=body.intent,
                provider_id=body.provider_id,
                model_id=body.model_id,
                scope_path=selected_scope,
                authority_ref=body.authority_ref,
                remember=body.remember,
                selection=body.selection,
                context_inputs=context_material,
                context_selections=body.context_selections,
                return_to=body.return_to,
                operation_ref=body.operation_ref,
                expected_profile_generation=body.expected_profile_generation,
            )

        return run(request, act)

    @router.get("/representations/records")
    def memory_records(
        request: Request, limit: int = Query(50, ge=1, le=200), output_kind: str | None = None,
        form: str | None = None, query: str | None = Query(None, max_length=200),
        cursor: str | None = Query(None, max_length=43),
    ):
        return run(
            request,
            lambda _p, m, _t: dict(
                contract="akousmata/representations/v1",
                **m.page(limit=limit, output_kind=output_kind, form=form, query=query, cursor=cursor),
            ),
        )

    @router.get("/representations/records/{record_id}")
    def memory_detail(record_id: str, request: Request):
        from akousmata_app.representations import RepresentationNavigator

        def load(_p, memory, _terms):
            result = RepresentationNavigator(memory.store, actor_ref=actor_ref).detail(
                record_id
            )
            if result is None:
                raise KeyError(record_id)
            result["primary_representation_ref"] = memory.operation(
                result["record"]["extensions"]["earworm_representation"]["operation_ref"]
            )["primary_representation_ref"]
            return result

        return run(request, load)

    @router.get("/representations/executions/{operation_ref}")
    def execution(operation_ref: str, request: Request):
        def load(p, _m, _t):
            row = p.conn.execute(
                "SELECT receipt FROM executions WHERE operation_ref=?", (operation_ref,)
            ).fetchone()
            if row is None:
                raise KeyError(operation_ref)
            value = json.loads(row["receipt"])
            refs = value.get("material_terms_refs")
            if not refs or not all(_t.allows("read_metadata", ref) for ref in refs):
                raise PermissionError("Current terms withhold this execution metadata")
            return value

        return run(request, load)

    @router.post("/representations/records/{record_id}/revisit")
    def revisit(record_id: str, body: RevisitBody, request: Request):
        from akousmata_app.representations import RepresentationNavigator
        return run(request, lambda _p, m, _t: RepresentationNavigator(
            m.store, actor_ref=actor_ref).revisit(record_id, **body.model_dump()))

    def station_detail(memory, record_id):
        from akousmata_app.representations import RepresentationNavigator
        record = memory.get(record_id)
        if record is None:
            raise KeyError(record_id)
        block = record["extensions"]["earworm_representation"]
        # Metadata is disclosure too. A partially disclosed record cannot reveal
        # denied identities, text, commitments or lineage through its envelope.
        if not all(memory.authorize("disclose_local", rep,
                   resolve_representation=lambda identity: memory.representation(identity)) is True
                   for rep in block["representations"]):
            raise PermissionError("Current terms withhold local Station disclosure")
        detail = RepresentationNavigator(memory.store, actor_ref=actor_ref).detail(record_id)
        detail["primary_representation_ref"] = memory.operation(block["operation_ref"])["primary_representation_ref"]
        return dict(contract="khipu/station-inscription/v2", audience="local",
                    acoustic_execution=False, **{k: v for k, v in detail.items() if k != "contract"})

    @router.get("/representations/station")
    def station_records(request: Request, limit: int = Query(50, ge=1, le=200),
                        output_kind: str | None = None, form: str | None = None,
                        query: str | None = Query(None, max_length=200),
                        cursor: str | None = Query(None, max_length=43)):
        def load(_p, memory, _terms):
            def disclosed(record):
                block = record["extensions"]["earworm_representation"]
                return all(memory.authorize("disclose_local", rep,
                           resolve_representation=memory._representation) is True
                           for rep in block["representations"])
            page = memory.page(limit=limit, output_kind=output_kind, form=form,
                               query=query, cursor=cursor, include_record=disclosed,
                               audience="station-local")
            return dict(contract="khipu/station-inscriptions/v2", **page,
                        audience="local", acoustic_execution=False)
        return run(request, load)

    @router.get("/representations/station/{record_id}")
    def station_record(record_id: str, request: Request):
        return run(request, lambda _p, m, _t: station_detail(m, record_id))

    return router
