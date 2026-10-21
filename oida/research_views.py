"""Read projections and explicit comparison baselines for retained research.

These are application views, not canonical influence edges or new audio passes.
"""

from copy import deepcopy
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator

from oida.reasoning.evidence import covenant_blocks_untyped_prose
from oida.reasoning_context import retained_event
from oida.research_references import Digest, Strict, digest


class Comparison(Strict):
    kind: Literal["initial_account", "reasoning_turn"]
    reference: str = Field(pattern=r"^[A-Za-z0-9_:-]{1,100}$")
    sha256: Digest
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")

    @model_validator(mode="after")
    def complete(self):
        if (self.kind == "reasoning_turn") != (self.conversation_id is not None):
            raise ValueError("A prior turn needs its exact conversation")
        return self


def current_anchor(workspace, identifier):
    try:
        record = workspace.reader(identifier)
    except (KeyError, FileNotFoundError, ValueError):
        raise HTTPException(404, "Retained account is unavailable") from None
    if (
        not isinstance(record, dict)
        or record.get("akousma_id") != identifier
        or not record.get("auditum")
    ):
        raise HTTPException(404, "Retained account is unavailable")
    event = workspace.event_policy(retained_event(deepcopy(record)))
    if event.get("privacy_mode") == "incognito" or covenant_blocks_untyped_prose(
        event.get("covenant")
    ):
        raise HTTPException(423, "Retained research is withheld by current policy")
    return record, event


def account_view(event):
    aggregate = event.get("aggregate") or {}
    claims = []
    for route in event.get("routes", [])[:12]:
        for category, values in (
            (route.get("structured") or {}).get("claim_summary") or {}
        ).items():
            for claim in values[:40] if isinstance(values, list) else []:
                if isinstance(claim, dict) and isinstance(claim.get("statement"), str):
                    claims.append(
                        dict(
                            category=category,
                            statement=claim["statement"],
                            source=claim.get("source"),
                            confidence=claim.get("confidence"),
                        )
                    )
    return dict(
        reference=event.get("id"),
        title=aggregate.get("title"),
        summary=aggregate.get("short_summary"),
        claims=claims,
        acoustic_access="retained account only; no new audio pass",
    )


def resolve_comparison(workspace, req, record_sha256):
    selected = req.comparison
    if selected is None:
        return None
    if selected.kind == "initial_account":
        if selected.reference != req.record_id or selected.sha256 != record_sha256:
            raise HTTPException(
                409, "Selected initial account changed; inspect it again"
            )
    else:
        if req.conversation_id != selected.conversation_id:
            raise HTTPException(409, "Select a prior turn from this conversation")
        conversation = workspace.conversations.get(selected.conversation_id)
        if conversation.get("anchor_event_id") != req.record_id:
            raise HTTPException(
                409, "Selected comparison belongs to a different account"
            )
        turn = next(
            (
                t
                for t in conversation.get("turns", [])
                if t.get("id") == selected.reference
            ),
            None,
        )
        if turn is None or digest(turn) != selected.sha256:
            raise HTTPException(409, "Selected prior turn changed; inspect it again")
    return dict(
        contract="oida/research-comparison/v1",
        prior_account=selected.model_dump(mode="json"),
        subject_ref=req.record_id,
        source_relation="same_retained_account",
        purpose="display comparison; prior turn text is not model input",
        acoustic_access="retained account only; no new audio pass",
        claim_limit="Differences do not establish improvement, independence or causal influence",
    )


def research_record(workspace, identifier, limit=25):
    record, event = current_anchor(workspace, identifier)
    candidates = workspace.conversations.list(event_id=identifier, limit=limit + 1)
    return dict(
        contract="oida/research-record/v1",
        record_id=identifier,
        record_sha256=workspace.journal.record_digest(record),
        initial_account=account_view(event),
        acoustic_returns=workspace.acoustic_choices(record),
        sessions=candidates[:limit],
        has_more=len(candidates) > limit,
        limit=limit,
        jobs=[
            {k: j.get(k) for k in ("id", "status", "conversation_id")}
            for j in workspace.visible_jobs(limit=100)
            if j.get("record_id") == identifier
        ][:25],
        coverage="Newest permitted conversations only; absence is limited to this view",
    )


def research_session(workspace, identifier):
    conversation = workspace.conversations.get(identifier)
    record_id = conversation["anchor_event_id"]
    record, _ = current_anchor(workspace, record_id)
    current_sha = workspace.journal.record_digest(record)
    turns = []
    for turn in conversation.get("turns", []):
        projected = {
            k: v
            for k, v in turn.items()
            if k
            in {
                "id",
                "created_at",
                "question",
                "answer",
                "answer_blocks",
                "hypotheses",
                "uncertainties",
                "reasoner",
                "fallback",
                "research",
                "suggested_questions",
                "audit",
                "relisten",
            }
        }
        projected["turn_sha256"] = digest(turn)
        turns.append(projected)
    initial_sha = next(
        (
            t.get("research", {}).get("record_sha256")
            for t in turns
            if t.get("research", {}).get("record_sha256")
        ),
        None,
    )
    return dict(
        **workspace.conversations.summary(conversation),
        contract="oida/research-session/v1",
        record_id=record_id,
        initial_account=account_view(conversation.get("event") or {}),
        initial_record_sha256=initial_sha,
        current_record_sha256=current_sha,
        anchor_state="changed"
        if initial_sha and initial_sha != current_sha
        else "matched"
        if initial_sha
        else "unknown",
        turns=turns,
    )
