"""Fixed, explicitly selected research recipes on the existing owner queue.

No planner-generated tools, automatic source selection, paging or host connection.
The context-local meter reserves calls durably before dispatch, including repair.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator

from oida.contracts import now_iso
from oida.operation_control import checkpoint
from oida.research_references import Strict, SearchInput, RelationsInput, digest

_active = ContextVar("oida_research_recipe", default=None)
CONTRACT = "oida/research-recipe/v1"


class Limits(Strict):
    time_seconds: int = Field(default=120, ge=10, le=600)
    max_steps: int = Field(default=5, ge=2, le=5)
    public_calls: int = Field(default=5, ge=0, le=9)
    provider_calls: int = Field(default=2, ge=1, le=2)
    audio_calls: int = Field(default=0, ge=0, le=1)
    retained_bytes: int = Field(default=1048576, ge=16384, le=1048576)
    output_tokens: int = Field(default=1200, ge=256, le=2200)


class AcousticReturn(Strict):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_:-]{1,100}$")
    expected_audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    question: str = Field(min_length=1, max_length=1000)
    model_id: str = Field(min_length=1, max_length=256)
    start_s: float = Field(ge=0, le=86400)
    end_s: float = Field(gt=0, le=86400)
    context_policy: Literal["independent"] = "independent"

    @model_validator(mode="after")
    def interval(self):
        if (
            not self.question.strip()
            or not self.model_id.strip()
            or not 0 < self.end_s - self.start_s <= 60
        ):
            raise ValueError(
                "Select an acoustic question, model and interval of at most 60 seconds"
            )
        return self


class Recipe(Strict):
    name: Literal["selected_context_return", "local_relisten_return"]
    capabilities: list[
        Literal[
            "public_read",
            "private_read",
            "public_search",
            "reference_leads",
            "reason",
            "local_relisten",
        ]
    ] = Field(min_length=1, max_length=6)
    searches: list[SearchInput] = Field(default_factory=list, max_length=3)
    leads: RelationsInput | None = None
    relisten: AcousticReturn | None = None
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def fixed_shape(self):
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Select each capability once")
        if (self.name == "local_relisten_return") != (self.relisten is not None):
            raise ValueError("The acoustic recipe requires one explicit local return")
        if any(s.cursor or s.limit > 5 for s in self.searches):
            raise ValueError(
                "Recipes read a first bounded page only; continuation is a separate selection"
            )
        return self


def plan(req, anchor_sha256):
    recipe = req.recipe
    required = {"reason"}
    if req.sonicfield_records:
        required.add("public_read")
    if req.local_passages:
        required.add("private_read")
    if recipe.searches:
        required.add("public_search")
    if recipe.leads:
        required.add("reference_leads")
    if recipe.relisten:
        required.add("local_relisten")
    if set(recipe.capabilities) != required:
        raise HTTPException(
            409, "Recipe capabilities must match the exact selected operations"
        )
    if (
        req.scope.memories
        or req.scope.wiki
        or req.scope.web
        or req.record_refs
        or req.web_query
    ):
        raise HTTPException(
            409,
            "A recipe uses selected context only, without automatic memory/wiki/web expansion",
        )
    if (
        req.comparison is None
        or not 1 <= len(req.sonicfield_records) + len(req.local_passages) <= 5
    ):
        raise HTTPException(
            409, "Select a comparison baseline and one to five fresh sources"
        )
    if req.local_passages and (recipe.searches or recipe.leads):
        raise HTTPException(
            409, "Discover public leads separately before selecting private passages"
        )
    steps = (["public_search"] if recipe.searches else []) + (
        ["reference_leads"] if recipe.leads else []
    )
    steps += (
        ["selected_context"]
        + (["local_relisten"] if recipe.relisten else [])
        + ["reason"]
    )
    calls = len(recipe.searches) + bool(recipe.leads) + len(req.sonicfield_records)
    if (
        len(steps) > recipe.limits.max_steps
        or calls > recipe.limits.public_calls
        or int(recipe.relisten is not None) > recipe.limits.audio_calls
    ):
        raise HTTPException(409, "Selected steps exceed the recipe limits")
    body = req.model_dump(
        mode="json", exclude={"request_id", "deadline_at", "recipe_plan_sha256"}
    )
    value = dict(
        contract=CONTRACT,
        anchor_sha256=anchor_sha256,
        selection=body,
        steps=steps,
        limits=recipe.limits.model_dump(),
        public_calls_planned=calls,
        execution="not executed",
        source_selection="explicit IDs only; discovery leads are not auto-selected",
        acoustic_context="independent selected question; research text is not supplied to the audio model",
        stop_conditions=[
            "missing/partial selected context",
            "budget or deadline",
            "cancellation/current-use denial",
            "invalid model output/fallback",
            "restart or uncertain outcome",
        ],
        replay="never replay a dispatched call; inspect the original request ID",
    )
    return {**value, "plan_sha256": digest(value)}


def canonical_bytes(value):
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )


class Budget:
    def __init__(self, prepared, *, deadline_at, notify):
        self.prepared, self.deadline_at, self.notify = prepared, deadline_at, notify
        self.used = dict(public_calls=0, provider_calls=0, audio_calls=0)
        self.steps, self.discoveries = [], []
        self.retained_bytes = 0
        self.status = "running"

    def snapshot(self):
        return dict(
            contract="oida/research-recipe-execution/v1",
            name=self.prepared["selection"]["recipe"]["name"],
            plan_sha256=self.prepared["plan_sha256"],
            anchor_sha256=self.prepared["anchor_sha256"],
            limits=self.prepared["limits"],
            deadline_at=self.deadline_at,
            used=dict(self.used),
            steps=deepcopy(self.steps),
            discoveries=deepcopy(self.discoveries),
            retained_result_bytes=self.retained_bytes,
            status=self.status,
            acoustic_context=self.prepared["acoustic_context"],
            claim_limit="Execution and changed text do not establish improvement, independence or causal influence",
        )

    def check(self):
        checkpoint()
        if time.time() >= self.deadline_at:
            raise HTTPException(409, "Recipe deadline reached; no later step admitted")

    def stage(self, name):
        self.check()
        expected = self.prepared["steps"]
        if len(self.steps) >= len(expected) or expected[len(self.steps)] != name:
            raise HTTPException(409, "Recipe stopped at an unexpected or repeated step")
        self.steps.append(dict(name=name, started_at=now_iso()))
        self.notify(self.snapshot())

    def consume(self, kind):
        self.check()
        if self.used[kind] >= self.prepared["limits"][kind]:
            raise HTTPException(
                409, "Recipe " + kind + " limit reached before dispatch"
            )
        self.used[kind] += 1
        self.notify(self.snapshot())  # Durable reservation precedes dispatch.
        self.check()

    def size(self, value):
        self.check()
        if canonical_bytes(value) > self.prepared["limits"]["retained_bytes"]:
            raise HTTPException(
                409, "Recipe retained-result byte limit reached before commit"
            )

    def discovery(self, result):
        body = result.get("discovery") or {}
        data = body.get("data") or {}
        self.discoveries.append(
            dict(
                outcome=result.get("outcome"),
                operation=result.get("operation"),
                references=[
                    dict(
                        reference=r["reference_ref"],
                        identity=r["record"]["identity"],
                        title=r["record"]["title"],
                    )
                    for r in result.get("references", [])
                ],
                coverage=data.get("coverage"),
                partial=data.get("partial"),
                next_cursor=data.get("nextCursor"),
                leads=data.get("relations"),
                error=body.get("error"),
                selection="leads only; none automatically supplied as context",
            )
        )
        self.size(self.discoveries)
        self.notify(self.snapshot())
        if not body.get("ok") or data.get("partial"):
            raise HTTPException(
                409,
                "Recipe stopped after incomplete public discovery; inspect coverage before a new selection",
            )


@contextmanager
def bounded(budget):
    token = _active.set(budget)
    try:
        yield
    finally:
        _active.reset(token)


def active():
    return _active.get()


def consume(kind):
    if (budget := active()) is not None:
        budget.consume(kind)


def finalize(turn, stored_event):
    budget = active()
    if budget is None:
        return
    if turn.get("fallback"):
        from oida.reasoning.evidence import safe_external_text

        note = safe_external_text(str((turn["fallback"] or {}).get("note") or ""), limit=240)
        raise HTTPException(
            409,
            "Recipe stopped on provider fallback; no substitute response committed"
            + (f". Selected provider: {note}" if note else ""),
        )
    # Whole context items must actually fit the prepared packet; no silent loss.
    research = turn.get("research") or {}
    exposures = [research[k] for k in ("exposure", "local_exposure") if research.get(k)]
    if not exposures or any(
        e.get("withheld_reference_refs") or not e.get("prepared_reference_refs")
        for e in exposures
    ):
        raise HTTPException(
            409,
            "Recipe stopped because selected context was withheld from the prepared input",
        )
    budget.status = "complete"
    for _ in range(5):
        turn["research"]["recipe_execution"] = budget.snapshot()
        size = canonical_bytes(dict(turn=turn, event=stored_event))
        if size == budget.retained_bytes:
            break
        budget.retained_bytes = size
    budget.size(dict(turn=turn, event=stored_event))
    budget.notify(budget.snapshot())


def descriptions():
    return dict(
        contract=CONTRACT,
        names=["selected_context_return", "local_relisten_return"],
        limits=Limits().model_dump(),
        automatic_source_selection=False,
        automatic_continuation=False,
        host_connection="not configured by recipes",
        storage_scope="new turn and its event projection, plus bounded queue/step receipts; not total physical disk usage",
    )
