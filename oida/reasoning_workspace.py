"""Durable owner reasoning queue. Conversations and provider execution stay in Oída.

Only journal snapshots and ConversationStore are written. The Auditum store is
read-only here; an automatic session can never enqueue itself as a listening.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
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


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memories: bool = True
    wiki: bool = True
    web: bool = False


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

    @staticmethod
    def public_job(job):
        return {
            key: value
            for key, value in job.items()
            if key not in {"event", "request", "request_hash"}
        }

    def enqueue(self, req, mode="manual"):
        with self.lock:
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
            event = self.event_policy(deepcopy(event))
            if event.get("privacy_mode") == "incognito":
                raise HTTPException(
                    423, "Persistent research is unavailable in incognito mode"
                )
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

            def run():
                req = Ask.model_validate(job["request"])
                def check_inquiry():
                    current = self.journal.get("reasoning_job", job["id"]) or {}
                    if current.get("cancel_requested") or (req.deadline_at is not None and time.time() >= req.deadline_at):
                        raise HTTPException(409, "Inquiry cancelled or deadline expired")
                check_inquiry()
                self.validate_selection(req)
                record = self.reader(req.record_id)
                if self.journal.record_digest(record) != job["record_sha256"]:
                    raise HTTPException(409, "Inquiry anchor changed while queued")
                event = self.event_policy(retained_event(record))
                if event.get("privacy_mode") == "incognito":
                    raise HTTPException(
                        423, "Persistent research is unavailable in incognito mode"
                    )
                selection = None
                if req.record_refs or req.inquiry_sha256:
                    from oida.routing.archive import inquiry
                    selection = inquiry(self, req.record_id, req.record_refs)
                    if req.inquiry_sha256 and selection["sha256"] != req.inquiry_sha256:
                        raise HTTPException(409, "Inquiry evidence changed while queued")
                sources, notes, query = self.retriever(
                    event, req.question, req.scope.model_dump(), req.web_query
                )
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
                        research=dict(
                            request_id=job["id"],
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
                        ),
                    ),
                )
                check_inquiry()
                return result

            def recheck():
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
            result = self.operations.run("reason_" + job["id"][:73], run, recheck=recheck, deadline_at=job["request"].get("deadline_at"))
            self.save_job(
                {
                    **job,
                    "status": "complete",
                    "conversation_id": result["conversation_id"],
                    "reasoner": result["turn"].get("reasoner"),
                    "fallback": result["turn"].get("fallback"),
                }
            )
        except Exception as exc:
            cancelled = (self.journal.get("reasoning_job", job["id"]) or {}).get("cancel_requested")
            self.save_job(
                {
                    **job,
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
            for summary in self.conversations.list(
                event_id=job["record_id"], limit=500
            ):
                conversation = self.conversations.get(summary["id"])
                if any(
                    t.get("research", {}).get("request_id") == job["id"]
                    for t in conversation.get("turns", [])
                ):
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
        conversation = self.conversations.get(identifier)
        return {
            **self.conversations.summary(conversation),
            "record_id": conversation["anchor_event_id"],
            "turns": [
                {
                    k: v
                    for k, v in turn.items()
                    if k
                    in {
                        "id",
                        "created_at",
                        "question",
                        "answer",
                        "reasoner",
                        "fallback",
                        "research",
                        "suggested_questions",
                    }
                }
                for turn in conversation.get("turns", [])
            ],
        }


def workspace_router(
    workspace, providers, models, require_admin, invalidate_probes=None
):
    router = APIRouter(prefix="/reasoning/workspace")

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
    def state():
        return dict(
            automatic=workspace.config(),
            jobs=[
                workspace.public_job(j) for j in workspace.values("reasoning_job", 50)
            ],
            sessions=workspace.conversations.list(limit=100),
            error=workspace.last_error,
        )

    @router.get("/sessions/{identifier}")
    def session(identifier: str):
        try:
            return workspace.session(identifier)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, "Reasoning session not found") from exc

    @router.post("/ask", status_code=202)
    def ask(req: Ask, request: Request):
        require_admin(request)
        try:
            return workspace.enqueue(req)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/jobs/{identifier}")
    def job(identifier: str):
        value = workspace.journal.get("reasoning_job", identifier)
        if value is None:
            raise HTTPException(404, "Inquiry job not found")
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
