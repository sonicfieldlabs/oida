"""Durable owner reasoning queue. Conversations and provider execution stay in Oída.

Only journal snapshots and ConversationStore are written. The Auditum store is
read-only here; an automatic session can never enqueue itself as a listening.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
import hashlib
import re
import json
import threading
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from oida.contracts import now_iso
from oida.owner_journal import canonical
from oida.reasoning.orchestrator import TurnOptions
from oida.reasoning_context import read_record, retained_event, retrieve
from oida.research_references import ResearchContext, SelectedRecord
from oida.local_passages import LocalExposure, LocalPassageDenied, SelectedPassage
from oida.research_views import Comparison, resolve_comparison, research_record, research_session
from oida.research_recipes import Recipe, Budget, bounded, plan as recipe_plan, canonical_bytes


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memories: bool = True
    wiki: bool = True
    web: bool = False
    sonicfield: bool = False
    local_passages: bool = False


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(default="local_structured", min_length=1, max_length=100)
    model_id: str | None = Field(default=None, max_length=200)
    scope: Scope = Field(default_factory=Scope)


class Ask(Selection):
    request_id: str = Field(
        default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9_-]{1,80}$"
    )
    record_id: str = Field(pattern=r"^[A-Za-z0-9_:-]{1,100}$")
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    question: str = Field(min_length=1, max_length=8000)
    web_query: str = Field(default="", max_length=240)
    record_refs: list[str] = Field(default_factory=list, max_length=8)
    inquiry_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    deadline_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    sonicfield_records: list[SelectedRecord] = Field(default_factory=list, max_length=5)
    local_passages: list[SelectedPassage] = Field(default_factory=list, max_length=5)
    comparison: Comparison | None = None
    recipe: Recipe | None = None
    recipe_plan_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Automatic(Selection):
    enabled: bool = False
    question: str = Field(
        default="Expand this listening account with relevant context. Distinguish observations, interpretations, and uncertainty, and suggest a useful follow-up.",
        min_length=1,
        max_length=8000,
    )


class Workspace:
    def __init__(
        self,
        journal,
        conversations,
        reasoning,
        settings,
        operations,
        event_policy,
        *,
        reader=read_record,
        retriever=retrieve,
        public=None,
        local=None,
        acoustic_reader=None,
    ):
        self.journal, self.conversations, self.reasoning = (
            journal,
            conversations,
            reasoning,
        )
        self.settings, self.operations, self.event_policy = (
            settings,
            operations,
            event_policy,
        )
        self.reader, self.retriever = reader, retriever
        self.acoustic_reader = acoustic_reader
        from oida.sonicfield_discovery import PublicDiscovery
        self.public = public if public is not None else PublicDiscovery()
        self.local = local if local is not None else getattr(reasoning, "local_passages", None)
        if reader is read_record:
            # The canonical reader reads the store; batch archive digests may read it too.
            from oida.reasoning_context import read_raw_records

            self.raw_records = read_raw_records
        self.lock = threading.RLock()
        self.stopped, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.last_error = None

    def values(self, kind, limit=100):
        with self.journal.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM snapshots WHERE kind=? ORDER BY sequence DESC LIMIT ?",
                    (kind, limit),
                )
            ]

    def config(self):
        return (
            self.journal.get("reasoning_config", "automatic")
            or Automatic().model_dump()
        )

    def validate_selection(self, req):
        if req.provider_id == "local_ecology":
            registry = self.reasoning.registry_factory(self.settings.load())
            models = (
                registry.list_models(req.provider_id)
                if registry.get(req.provider_id) is not None
                else []
            )
            if req.model_id is None:
                req.model_id = next(
                    (m.id for m in models if m.metadata.get("recommended")), None
                )
            if not any(m.id == req.model_id for m in models):
                raise HTTPException(409, "Choose an admitted local planning model")
            return
        configured = self.settings.load().providers.get(req.provider_id)
        if req.provider_id == "oida_moss" or configured is None:
            raise HTTPException(400, "Choose a text reasoning provider")
        if not configured.enabled:
            raise HTTPException(
                409, "Enable the selected provider before starting research"
            )

    def configure(self, req):
        with self.lock:
            if req.enabled and req.scope.local_passages:
                raise HTTPException(409, "Local passages require a fresh manual selection for each inquiry")
            if req.enabled:
                self.validate_selection(req)
            old = self.config()
            value = req.model_dump()
            if req.enabled and not old.get("enabled"):
                with self.journal.connection() as db:
                    value["cursor"] = db.execute(
                        "SELECT COALESCE(MAX(sequence),0) FROM events"
                    ).fetchone()[0]
                value["enabled_at"] = now_iso()
            else:
                value.update(
                    {key: old[key] for key in ("cursor", "enabled_at") if key in old}
                )
            self.journal.save("reasoning_config", "automatic", value)
            if not req.enabled:
                for job in self.values("reasoning_job"):
                    if job["mode"] == "automatic" and job["status"] == "queued":
                        self.save_job(
                            {
                                **job,
                                "status": "cancelled",
                                "detail": "Automatic mode switched off before execution",
                            }
                        )
            self.wake.set()
            return value

    def save_job(self, job):
        job["updated_at"] = now_iso()
        self.journal.save("reasoning_job", job["id"], job)
        return job

    def check_local_job(self, job, *, actions=("read", "read_metadata")):
        selections = (job.get("request") or {}).get("local_passages", [])
        if selections:
            if self.local is None:
                raise LocalPassageDenied()
            self.local.check_selections([SelectedPassage.model_validate(s) for s in selections], actions=actions)

    def visible_jobs(self, limit=50, *, include_local=True):
        values = []
        for job in self.values("reasoning_job", limit):
            if not include_local and (job.get("request") or {}).get("local_passages"):
                continue
            try:
                values.append(self.public_job(job))
            except LocalPassageDenied:
                continue
        return values

    def public_job(self, job):
        self.check_local_job(job)
        return {
            key: value
            for key, value in job.items()
            if key not in {"event", "request", "request_hash"}
        }

    def preview_recipe(self, req):
        from oida.research_views import current_anchor
        if req.recipe is None:
            raise HTTPException(409,"Select a bounded research recipe")
        record, _ = current_anchor(self,req.record_id)
        self.validate_selection(req)
        if req.sonicfield_records and not req.scope.sonicfield:
            raise HTTPException(409,"Selected public sources require their context scope")
        if req.local_passages:
            if self.local is None or not req.scope.local_passages:
                raise LocalPassageDenied()
            self.local.check_selections(req.local_passages,actions=("read","read_metadata","execute","retain","retain_metadata"))
        anchor = self.journal.record_digest(record)
        resolve_comparison(self,req,anchor)
        if req.recipe.relisten:
            self.recipe_acoustic_event(req,record)
        prepared = recipe_plan(req,anchor)
        if canonical_bytes(prepared)>req.recipe.limits.retained_bytes:
            raise HTTPException(409,"Recipe selection exceeds the retained-byte limit")
        return prepared

    def acoustic_choices(self, record):
        ids = {p.get("payload",{}).get("listening_event_id") for p in record.get("listening",{}).values() if isinstance(p,dict) and isinstance(p.get("payload"),dict)}
        reference = self.journal.get("record_reference",record["akousma_id"]) or {}
        ids.add(reference.get("event_id"))
        choices = []
        if self.acoustic_reader:
            for identifier in sorted(i for i in ids if isinstance(i,str))[:12]:
                event = self.acoustic_reader(identifier)
                if not isinstance(event,dict) or event.get("id")!=identifier:
                    continue
                event = self.event_policy(deepcopy(event))
                from oida.reasoning.evidence import covenant_blocks_untyped_prose
                if event.get("privacy_mode")=="incognito" or covenant_blocks_untyped_prose(event.get("covenant")):
                    continue
                ref = (event.get("segment") or {}).get("data_ref") or {}
                if re.fullmatch(r"[a-f0-9]{64}",str(ref.get("sha256") or "")):
                    choices.append(dict(event_id=identifier,sha256=ref["sha256"],basis="owner-linked source metadata; actual file and bounds rechecked before inference"))
        return choices

    def recipe_acoustic_event(self, req, record=None):
        record = self.reader(req.record_id) if record is None else record
        selected = req.recipe.relisten
        if not any(c["event_id"]==selected.event_id and c["sha256"]==selected.expected_audio_sha256 for c in self.acoustic_choices(record)):
            raise HTTPException(409,"Selected acoustic source is not currently linked and available; no audio was reconstructed")
        event = self.acoustic_reader(selected.event_id)
        if not isinstance(event,dict) or event.get("id")!=selected.event_id or ((event.get("segment") or {}).get("data_ref") or {}).get("sha256")!=selected.expected_audio_sha256:
            raise HTTPException(409,"Selected acoustic source changed")
        return self.event_policy(deepcopy(event))

    def enqueue(self, req, mode="manual"):
        with self.lock:
            if req.local_passages:
                if not req.scope.local_passages or mode != "manual":
                    raise HTTPException(409, "Local passages require explicit manual context scope")
                if req.scope.web or req.web_query:
                    raise HTTPException(409, "Private passage consultation cannot initiate a public search")
                if self.local is None:
                    raise LocalPassageDenied()
                self.local.check_selections(req.local_passages,
                    actions=("read", "read_metadata", "execute", "retain", "retain_metadata"))
            if req.sonicfield_records and not req.scope.sonicfield:
                raise HTTPException(409, "Selected Sonic Field records require explicit public context scope")
            if req.deadline_at is not None and req.deadline_at <= time.time():
                raise HTTPException(409, "Inquiry deadline expired")
            selection = None
            if req.record_refs or req.inquiry_sha256:
                from oida.routing.archive import inquiry
                if req.record_refs and not req.scope.memories:
                    raise HTTPException(409, "Selected relations require memory context")
                selection = inquiry(self, req.record_id, req.record_refs)
                if req.inquiry_sha256 and req.inquiry_sha256 != selection["sha256"]:
                    raise HTTPException(409, "Inquiry evidence changed before admission")
                if req.web_query and req.web_query != selection["web_query"]:
                    raise HTTPException(409, "Inquiry query differs from the selected evidence")
            payload = req.model_dump()
            if req.comparison is None:
                payload.pop("comparison")  # Preserve pre-P5 request-idempotency bytes.
            for name in ("recipe","recipe_plan_sha256"):
                if payload[name] is None:
                    payload.pop(name)
            digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
            old = self.journal.get("reasoning_job", req.request_id)
            if old:
                if old["request_hash"] != digest:
                    raise HTTPException(
                        409, "Request ID is already attached to a different question"
                    )
                return self.public_job(old)
            self.validate_selection(req)
            if not req.question.strip():
                raise HTTPException(400, "Enter a question")
            if (
                sum(
                    j["status"] in {"queued", "running"}
                    for j in self.values("reasoning_job")
                )
                >= 32
            ):
                raise HTTPException(
                    429, "Reasoning queue is full; wait for a session to finish"
                )
            if req.conversation_id:
                conversation = self.conversations.get(req.conversation_id)
                if conversation.get("anchor_event_id") != req.record_id:
                    raise HTTPException(
                        409, "This session belongs to a different Auditum record"
                    )
                event = conversation["event"]
                record_hash = (
                    (conversation.get("turns") or [{}])[0]
                    .get("research", {})
                    .get("record_sha256")
                )
            record = self.reader(req.record_id)
            event = retained_event(record)
            record_hash = self.journal.record_digest(record)
            resolve_comparison(self, req, record_hash)
            event = self.event_policy(deepcopy(event))
            if event.get("privacy_mode") == "incognito":
                raise HTTPException(
                    423, "Persistent research is unavailable in incognito mode"
                )
            prepared = self.preview_recipe(req) if req.recipe else None
            if prepared and prepared["plan_sha256"] != req.recipe_plan_sha256:
                raise HTTPException(409,"Preview the exact recipe selection before dispatch")
            job = dict(
                id=req.request_id,
                status="queued",
                mode=mode,
                record_id=req.record_id,
                conversation_id=req.conversation_id,
                question=req.question,
                record_sha256=record_hash,
                created_at=now_iso(),
                request=payload,
                request_hash=digest,
                event=event,
                provider_id=req.provider_id,
                model_id=req.model_id,
                inquiry_selection=selection,
            )
            if prepared:
                job.update(recipe_plan=prepared,deadline_at=min(time.time()+req.recipe.limits.time_seconds,req.deadline_at or float("inf")))
                if canonical_bytes(job)>req.recipe.limits.retained_bytes:
                    raise HTTPException(409,"Recipe queue payload exceeds its retained-byte limit")
            self.save_job(job)
            self.wake.set()
            return self.public_job(job)

    def scan(self):
        with self.lock:
            config = self.config()
            if not config.get("enabled"):
                return
            cursor = config.get("cursor", 0)
            with self.journal.connection() as db:
                rows = db.execute(
                    "SELECT sequence,subject FROM events WHERE kind='record_reference' AND sequence>? ORDER BY sequence LIMIT 25",
                    (cursor,),
                ).fetchall()
            for sequence, identifier in rows:
                # A later edit/reference to an existing record is not a new arrival.
                with self.journal.connection() as db:
                    first = db.execute(
                        "SELECT MIN(sequence) FROM events WHERE kind='record_reference' AND subject=?",
                        (identifier,),
                    ).fetchone()[0]
                reference = self.journal.get("record_reference", identifier) or {}
                # Control owns the reasoning decision for its entire run. Keep
                # global automatic expansion from overriding Off or duplicating On.
                managed = str(reference.get("operation_id", "")).startswith(
                    ("control-", "situated-", "discovery-")
                )
                if first == sequence and not managed:
                    request_id = (
                        "auto_"
                        + hashlib.sha256(
                            (self.journal.producer_id + identifier).encode()
                        ).hexdigest()[:40]
                    )
                    if not self.journal.get("reasoning_job", request_id):
                        selection = {
                            k: config[k]
                            for k in ("provider_id", "model_id", "scope", "question")
                        }
                        try:
                            self.enqueue(
                                Ask(
                                    request_id=request_id,
                                    record_id=identifier,
                                    **selection,
                                ),
                                "automatic",
                            )
                        except HTTPException as exc:
                            if exc.status_code in {409, 429}:
                                raise
                            self.last_error = str(exc.detail)
                        except (ValueError, FileNotFoundError) as exc:
                            self.last_error = str(exc)
                config["cursor"] = sequence
                self.journal.save("reasoning_config", "automatic", config)

    def execute_one(self):
        with self.lock:
            queued = [
                j for j in self.values("reasoning_job") if j["status"] == "queued"
            ]
            if not queued:
                return False
            job = min(queued, key=lambda j: j["created_at"])
            self.save_job({**job, "status": "running"})
        try:
            admitted_memories = {}
            budget = None
            if job.get("recipe_plan"):
                def reserve(receipt):
                    with self.lock:
                        current = self.journal.get("reasoning_job",job["id"]) or job
                        self.save_job({**current,"recipe_execution":receipt})
                budget = Budget(job["recipe_plan"],deadline_at=job["deadline_at"],notify=reserve)

            def run():
                req = Ask.model_validate(job["request"])
                def check_inquiry():
                    current = self.journal.get("reasoning_job", job["id"]) or {}
                    deadline = job.get("deadline_at",req.deadline_at)
                    if current.get("cancel_requested") or (deadline is not None and time.time() >= deadline):
                        raise HTTPException(409, "Inquiry cancelled or deadline expired")
                check_inquiry()
                self.validate_selection(req)
                record = self.reader(req.record_id)
                if self.journal.record_digest(record) != job["record_sha256"]:
                    raise HTTPException(409, "Inquiry anchor changed while queued")
                comparison = resolve_comparison(self, req, job["record_sha256"])
                event = self.event_policy(retained_event(record))
                if event.get("privacy_mode") == "incognito":
                    raise HTTPException(
                        423, "Persistent research is unavailable in incognito mode"
                    )
                if req.recipe:
                    if self.preview_recipe(req)["plan_sha256"] != job["recipe_plan"]["plan_sha256"]:
                        raise HTTPException(409,"Recipe selection changed while queued")
                    if req.recipe.searches:
                        budget.stage("public_search")
                        for query in req.recipe.searches:
                            budget.discovery(self.public.search(query))
                    if req.recipe.leads:
                        budget.stage("reference_leads")
                        budget.discovery(self.public.relations(req.recipe.leads))
                    budget.stage("selected_context")
                selection = None
                if req.record_refs or req.inquiry_sha256:
                    from oida.routing.archive import inquiry
                    selection = inquiry(self, req.record_id, req.record_refs)
                    if req.inquiry_sha256 and selection["sha256"] != req.inquiry_sha256:
                        raise HTTPException(409, "Inquiry evidence changed while queued")
                sources, notes, query = self.retriever(
                    event, req.question, req.scope.model_dump(), req.web_query
                )
                public_receipts = []
                if req.scope.sonicfield:
                    from oida.reasoning.evidence import covenant_blocks_untyped_prose
                    if covenant_blocks_untyped_prose(event.get("covenant")):
                        notes.append("Selected public context withheld by the current covenant")
                    elif req.sonicfield_records:
                        selected_public, public_notes, public_receipts = self.public.selected(req.sonicfield_records)
                        sources = selected_public + sources
                        notes.extend(public_notes)
                    else:
                        notes.append("No Sonic Field records selected; no public request was made")
                if req.local_passages:
                    from oida.reasoning.evidence import covenant_blocks_untyped_prose
                    if covenant_blocks_untyped_prose(event.get("covenant")):
                        raise LocalPassageDenied()
                    local_sources = [self.local.read(s, actions=("read", "read_metadata", "execute", "retain", "retain_metadata")).source()
                                     for s in req.local_passages]
                    sources = local_sources + sources
                if selection and req.scope.memories:
                    selected = selection["sources"]
                    selected_refs = {item["reference"] for item in selected}
                    sources = selected + [item for item in sources if item.get("reference") not in selected_refs]
                # Search hits are locators, not an enduring permission grant.
                from oida.routing.archive import archive_view
                admitted_sources = []
                for source in sources[:12]:
                    if source.get("kind") == "memory":
                        ref = source.get("reference")
                        if not ref:
                            continue
                        try:
                            current = archive_view(self, ref)
                            if current["view"]["state"] != "available":
                                notes.append("A memory reference is no longer readable")
                                continue
                            from oida.reasoning.evidence import safe_external_text
                            text = safe_external_text(current["events"][ref]["aggregate"]["short_summary"], limit=1200)
                            source = {**source, "text": text or "", "title": (text or "Retained account")[:160], "record_sha256": self.journal.record_digest(current["record"])}
                            admitted_memories[ref] = (source["record_sha256"], hashlib.sha256(canonical(current["events"][ref]).encode()).hexdigest())
                            source["permitted_event_sha256"] = admitted_memories[ref][1]
                        except HTTPException:
                            notes.append("A memory reference could not be revalidated")
                            continue
                    admitted_sources.append(source)
                sources = admitted_sources
                if budget:
                    if len([s for s in sources if s.get("kind") in {"sonicfield","local_passage"}]) != len(req.sonicfield_records)+len(req.local_passages):
                        raise HTTPException(409,"Recipe stopped because a selected source could not be read")
                    budget.size(dict(sources=sources,event=event,receipt=budget.snapshot()))
                from oida.operation_control import checkpoint

                checkpoint()
                check_inquiry()
                result = self.reasoning.ask(
                    event=event,
                    question=req.question,
                    options=TurnOptions(
                        provider_id=req.provider_id,
                        model_id=req.model_id,
                        conversation_id=req.conversation_id,
                        include_memory=False,
                        include_memory_content=False,
                        include_transcript=True,
                        allow_targeted_relisten=False,
                        include_conversation_history=comparison is None,
                        explicit_relisten=req.recipe.relisten.model_dump() if req.recipe and req.recipe.relisten else None,
                        relisten_event=self.recipe_acoustic_event(req,record) if req.recipe and req.recipe.relisten else None,
                        research=dict(
                            request_id=job["id"],
                            comparison=comparison,
                            record_id=req.record_id,
                            requested_provider_id=req.provider_id,
                            requested_model_id=req.model_id,
                            record_sha256=job["record_sha256"],
                            mode=job["mode"],
                            scope=req.scope.model_dump(),
                            sources=sources,
                            retrieval_notes=notes,
                            query=query,
                            inquiry_selection=selection,
                            input_sources_sha256=hashlib.sha256(canonical(sources).encode()).hexdigest(),
                            public_retrieval=public_receipts,
                            local_exposure=(LocalExposure(subject_ref=req.record_id,
                                reference_refs=[s["reference"] for s in sources if s.get("kind") == "local_passage"]
                            ).model_dump() if any(s.get("kind") == "local_passage" for s in sources) else None),
                            exposure=(ResearchContext(subject_ref=req.record_id,
                                reference_refs=[s["reference"] for s in sources if s.get("kind") == "sonicfield"]
                            ).model_dump() if any(s.get("kind") == "sonicfield" for s in sources) else None),
                        ),
                    ),
                )
                check_inquiry()
                return result

            def recheck():
                self.check_local_job(job, actions=("read", "read_metadata", "execute", "retain", "retain_metadata"))
                resolve_comparison(self, Ask.model_validate(job["request"]), job["record_sha256"])
                req = Ask.model_validate(job["request"])
                if req.recipe and req.recipe.relisten:
                    self.recipe_acoustic_event(req)
                current = self.journal.get("reasoning_job", job["id"]) or {}
                if current.get("cancel_requested"):
                    raise HTTPException(409, "Inquiry cancelled")
                from oida.reasoning.evidence import covenant_blocks_untyped_prose
                record = self.reader(job["record_id"])
                if self.journal.record_digest(record) != job["record_sha256"]:
                    raise HTTPException(409, "Inquiry anchor changed")
                current_event = self.event_policy(retained_event(record))
                if current_event.get("privacy_mode") == "incognito" or covenant_blocks_untyped_prose(current_event.get("covenant")):
                    raise HTTPException(423, "Inquiry permission changed")
                if job.get("inquiry_selection"):
                    from oida.routing.archive import inquiry
                    selection = job["inquiry_selection"]
                    if inquiry(self, job["record_id"], selection["record_refs"])["sha256"] != selection["sha256"]:
                        raise HTTPException(409, "Selected inquiry evidence changed")
                from oida.routing.archive import archive_view
                for ref, expected in admitted_memories.items():
                    projection = archive_view(self, ref)
                    if projection["view"]["state"] != "available":
                        raise HTTPException(423, "Retrieved memory permission changed")
                    actual = (self.journal.record_digest(projection["record"]), hashlib.sha256(canonical(projection["events"][ref]).encode()).hexdigest())
                    if actual != expected:
                        raise HTTPException(409, "Retrieved memory evidence changed")
            with bounded(budget) if budget else nullcontext():
                result = self.operations.run("reason_" + job["id"][:73], run, recheck=recheck, deadline_at=job.get("deadline_at",job["request"].get("deadline_at")))
            self.save_job(
                {
                    **(self.journal.get("reasoning_job",job["id"]) or job),
                    "status": "complete",
                    "conversation_id": result["conversation_id"],
                    "reasoner": result["turn"].get("reasoner"),
                    "fallback": result["turn"].get("fallback"),
                }
            )
        except Exception as exc:
            cancelled = (self.journal.get("reasoning_job", job["id"]) or {}).get("cancel_requested")
            if budget:
                budget.status = "cancelled" if cancelled else "stopped"
                receipt = budget.snapshot()
                receipt["stop_reason"] = str(getattr(exc,"detail",str(exc)))[:600]
                reserve(receipt)
            self.save_job(
                {
                    **(self.journal.get("reasoning_job",job["id"]) or job),
                    "status": "cancelled" if cancelled else "failed",
                    "detail": str(getattr(exc, "detail", str(exc)))[:600],
                }
            )
        return True

    def cancel_job(self, identifier):
        with self.lock:
            job = self.journal.get("reasoning_job", identifier)
            if job is None:
                raise HTTPException(404, "Inquiry job not found")
            if job["status"] in {"queued", "running"}:
                operation_id = "reason_" + identifier[:73]
                accepted = self.operations.cancel(operation_id)
                operation = self.journal.get("operation", operation_id) or {}
                if not accepted and operation.get("status") in {"committing", "complete"}:
                    return self.public_job(job)
                job.update(cancel_requested=True, status="cancelled" if job["status"] == "queued" else job["status"])
                self.save_job(job)
            return self.public_job(job)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        # An interrupted provider request may have committed its conversation.
        # Reconcile by request ID; never blindly repeat a network model call.
        for job in self.values("reasoning_job"):
            if job["status"] != "running":
                continue
            found = None
            found_turn = None
            for summary in self.conversations.list(
                event_id=job["record_id"], limit=500
            ):
                conversation = self.conversations.get(summary["id"])
                found_turn = next((t for t in conversation.get("turns",[]) if t.get("research",{}).get("request_id")==job["id"]),None)
                if found_turn:
                    found = summary["id"]
                    break
            self.save_job(
                {
                    **job,
                    "status": "complete" if found else "interrupted",
                    "conversation_id": found,
                    "detail": "Recovered saved session"
                    if found
                    else "Service stopped during research; start a new turn to retry",
                }
                | ({"recipe_execution":found_turn["research"]["recipe_execution"]} if found_turn and found_turn.get("research",{}).get("recipe_execution") else {})
            )
        self.stopped.clear()
        self.thread = threading.Thread(
            target=self.loop, name="oida-reasoning-workspace", daemon=True
        )
        self.thread.start()

    def loop(self):
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                self.scan()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(getattr(exc, "detail", str(exc)))[:500]
            if self.stopped.is_set():
                break
            try:
                if self.execute_one():
                    continue
            except Exception as exc:
                self.last_error = str(exc)[:500]
            self.wake.wait(2)

    def close(self):
        if getattr(self, "routing_queue", None):
            self.routing_queue.close()
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)

    def session(self, identifier):
        return research_session(self, identifier)


def workspace_router(
    workspace, providers, models, require_admin, invalidate_probes=None
):
    router = APIRouter(prefix="/reasoning/workspace")

    def private_read(request):
        try:
            require_admin(request)
            return True
        except HTTPException:
            return False

    @router.get("/options")
    def options():
        return {
            **providers(),
            "automatic": workspace.config(),
            "web_search": "DuckDuckGo / Bing search-result snippets",
        }

    @router.get("/models")
    def model_list(provider_id: str):
        return models(provider_id)

    @router.get("/recipes")
    def recipes(request: Request):
        require_admin(request)
        from oida.research_recipes import descriptions
        return descriptions()

    @router.post("/recipes/preview")
    def preview_recipe(req: Ask, request: Request):
        require_admin(request)
        return workspace.preview_recipe(req)

    @router.post("/enable")
    def enable(selection: Selection, request: Request):
        require_admin(request)
        with workspace.lock:
            settings = workspace.settings.load()
            provider = settings.providers.get(selection.provider_id)
            if not provider or selection.provider_id == "oida_moss":
                raise HTTPException(400, "Unknown text reasoning provider")
            updated = settings.model_copy(
                update={
                    "providers": {
                        **settings.providers,
                        selection.provider_id: provider.model_copy(
                            update={"enabled": True}
                        ),
                    }
                }
            )
            workspace.settings.save(updated)
            if invalidate_probes is not None:
                invalidate_probes(selection.provider_id)
        return options()

    @router.get("/state")
    def state(request: Request):
        local = private_read(request)
        return dict(
            automatic=workspace.config(),
            jobs=workspace.visible_jobs(include_local=local),
            sessions=workspace.conversations.list(limit=100, include_local=local),
            error=workspace.last_error,
        )

    @router.get("/sessions/{identifier}")
    def session(identifier: str, request: Request):
        try:
            value = workspace.session(identifier)
            if workspace.conversations.contains_local(value):
                require_admin(request)
            return value
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, "Reasoning session not found") from exc

    @router.get("/records/{identifier}")
    def record(identifier: str, request: Request):
        require_admin(request)
        if not re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", identifier):
            raise HTTPException(422, "Invalid retained account identity")
        return research_record(workspace, identifier)

    @router.post("/ask", status_code=202)
    def ask(req: Ask, request: Request):
        require_admin(request)
        try:
            return workspace.enqueue(req)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/jobs/{identifier}")
    def job(identifier: str, request: Request):
        value = workspace.journal.get("reasoning_job", identifier)
        if value is None:
            raise HTTPException(404, "Inquiry job not found")
        if (value.get("request") or {}).get("local_passages"):
            require_admin(request)
        from oida.routing.archive import archive_view
        projection = archive_view(workspace, value["record_id"])
        if projection["view"]["state"] != "available":
            raise HTTPException(423 if projection["view"]["state"] == "withheld" else 404, "Inquiry anchor is not readable")
        event = projection["events"][value["record_id"]]
        from oida.reasoning.evidence import covenant_blocks_untyped_prose
        if event.get("privacy_mode") == "incognito" or covenant_blocks_untyped_prose(event.get("covenant")):
            raise HTTPException(423, "Inquiry is withheld by current policy")
        return workspace.public_job(value)

    @router.post("/jobs/{identifier}/cancel")
    def cancel_job(identifier: str, request: Request):
        require_admin(request)
        return workspace.cancel_job(identifier)

    @router.post("/automatic")
    def automatic(req: Automatic, request: Request):
        require_admin(request)
        return workspace.configure(req)

    from oida.discovery_tools import discovery_tools_router

    router.include_router(discovery_tools_router(workspace, require_admin))
    from oida.situated_listener import situated_router

    router.include_router(situated_router(workspace, require_admin))
    from oida.routing.service import routing_router

    router.include_router(routing_router(workspace, require_admin))
    return router
