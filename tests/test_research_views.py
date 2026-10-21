"""P5 owner projections and explicit comparison references, with synthetic data."""

from copy import deepcopy
import pytest
from fastapi import HTTPException

from oida.reasoning_workspace import Ask
from oida.research_references import digest
from oida.research_views import research_record, research_session
from test_local_passages import pilot as pilot, workspace, Provider, USE


def inquiry(w, **changes):
    record = w.reader("akm_fixture")
    return Ask.model_validate(
        dict(
            request_id="p5-inquiry",
            record_id="akm_fixture",
            question="Compare a frame with this retained account",
            scope=dict(memories=False, wiki=False),
            comparison=dict(
                kind="initial_account",
                reference="akm_fixture",
                sha256=w.journal.record_digest(record),
            ),
        )
        | changes
    )


def test_owner_projection_retains_initial_snapshot_and_reports_changed_current_anchor(
    tmp_path, pilot
):
    w, service, record = workspace(tmp_path, pilot)
    w.enqueue(inquiry(w))
    w.execute_one()
    job = w.journal.get("reasoning_job", "p5-inquiry")
    assert job["status"] == "complete", job
    original = deepcopy(record)
    selected = research_record(w, "akm_fixture")
    view = research_session(w, job["conversation_id"])
    assert selected["sessions"][0]["id"] == job["conversation_id"]
    assert view["initial_account"]["summary"] == record["summary"]
    assert view["anchor_state"] == "matched"
    raw = service.conversations.get(job["conversation_id"])["turns"][0]
    assert view["turns"][0]["turn_sha256"] == digest(raw)
    assert "answer_blocks" in view["turns"][0]
    assert raw["research"]["comparison"]["source_relation"] == "same_retained_account"
    record["summary"] = "New canonical account, not the old encounter"
    changed = research_session(w, job["conversation_id"])
    assert changed["anchor_state"] == "changed"
    assert changed["initial_account"]["summary"] == original["summary"]
    assert (
        research_record(w, "akm_fixture")["initial_account"]["summary"]
        == record["summary"]
    )
    w.event_policy = lambda e: {**e, "privacy_mode": "incognito"}
    with pytest.raises(HTTPException, match="423"):
        research_session(w, job["conversation_id"])
    w.event_policy = lambda e: e
    w.reader = lambda _: (_ for _ in ()).throw(KeyError())
    with pytest.raises(HTTPException, match="404"):
        research_session(w, job["conversation_id"])
    w.close()


def test_stale_or_foreign_comparison_is_refused_before_queue_mutation(tmp_path, pilot):
    w, _, _ = workspace(tmp_path, pilot)
    for comparison in [
        dict(kind="initial_account", reference="other", sha256="a" * 64),
        dict(kind="initial_account", reference="akm_fixture", sha256="a" * 64),
    ]:
        with pytest.raises(HTTPException, match="409"):
            w.enqueue(inquiry(w, comparison=comparison))
    assert w.values("reasoning_job") == []
    w.enqueue(inquiry(w))
    w.execute_one()
    job = w.journal.get("reasoning_job", "p5-inquiry")
    view = w.session(job["conversation_id"])
    turn = view["turns"][0]
    for comparison in [
        dict(
            kind="reasoning_turn",
            reference=turn["id"],
            sha256="b" * 64,
            conversation_id=view["id"],
        ),
        dict(
            kind="reasoning_turn",
            reference=turn["id"],
            sha256=turn["turn_sha256"],
            conversation_id="foreign",
        ),
    ]:
        with pytest.raises(HTTPException, match="409"):
            w.enqueue(
                inquiry(
                    w,
                    request_id="next",
                    conversation_id=view["id"],
                    comparison=comparison,
                )
            )
    assert len(w.values("reasoning_job")) == 1
    w.close()


def test_explicit_prior_comparison_is_not_replayed_to_model_and_private_withdrawal_still_applies(
    tmp_path, pilot
):
    pilot["grant"]()
    provider = Provider()
    w, service, _ = workspace(tmp_path, pilot, provider)
    base = dict(
        provider_id="ollama",
        scope=dict(memories=False, wiki=False, local_passages=True),
        local_passages=[pilot["selection"]().model_dump()],
    )
    w.enqueue(inquiry(w, **base))
    w.execute_one()
    job = w.journal.get("reasoning_job", "p5-inquiry")
    assert job["status"] == "complete", job
    view = w.session(job["conversation_id"])
    turn = view["turns"][0]
    req = inquiry(
        w,
        request_id="return",
        conversation_id=view["id"],
        **base,
        comparison=dict(
            kind="reasoning_turn",
            reference=turn["id"],
            sha256=turn["turn_sha256"],
            conversation_id=view["id"],
        ),
    )
    w.enqueue(req)
    w.execute_one()
    result = w.journal.get("reasoning_job", "return")
    assert result["status"] == "complete", result
    assert len(provider.requests) == 2
    assert "PRIOR DIALOGUE" not in provider.requests[-1].user_prompt
    latest = service.conversations.get(view["id"])["turns"][-1]
    assert latest["research"]["comparison"]["prior_account"]["reference"] == turn["id"]
    assert latest["relisten"] is None
    pilot["grant"](USE, active=False)
    with pytest.raises(PermissionError):
        w.session(view["id"])
    assert research_record(w, "akm_fixture")["sessions"] == []
    w.close()
