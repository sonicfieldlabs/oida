"""Bounded recipes on the real queue/registry/custody with authored fixtures only."""

from copy import deepcopy
from hashlib import sha256
import json
import time
import wave

import httpx2 as httpx
import pytest
from fastapi import HTTPException

from oida.reasoning_workspace import Ask
from oida.research_recipes import active, canonical_bytes
from oida.relisten import TargetedRelistener
from oida.sonicfield_discovery import PublicDiscovery
from sonicfield_fixtures import body, record as public_record, response, search_data
from test_local_passages import pilot as pilot, workspace as base_workspace, Provider
from test_relisten import _Engine


def workspace(tmp_path, pilot, provider=None):
    w, s, record = base_workspace(tmp_path, pilot, provider)
    if provider is None:
        from oida.reasoning.registry import ProviderRegistry

        def registry(settings):
            value = ProviderRegistry()
            value.register(
                s.local_provider,
                enabled=True,
                configured=settings.providers["local_structured"],
            )
            return value

        s.registry_factory = registry
    return w, s, record


def inquiry(w, pilot, *, limits=None, **changes):
    recipe = dict(
        name="selected_context_return",
        capabilities=["private_read", "reason"],
        limits=limits or {},
    )
    req = Ask.model_validate(
        dict(
            request_id="recipe-one",
            record_id="akm_fixture",
            question="Frame the retained difference; do not infer its culture",
            scope=dict(memories=False, wiki=False, local_passages=True),
            local_passages=[pilot["selection"]().model_dump()],
            comparison=dict(
                kind="initial_account",
                reference="akm_fixture",
                sha256=w.journal.record_digest(w.reader("akm_fixture")),
            ),
            recipe=recipe,
        )
        | changes
    )
    preview = w.preview_recipe(req)
    return req.model_copy(update={"recipe_plan_sha256": preview["plan_sha256"]})


def job(w):
    return w.journal.get("reasoning_job", "recipe-one")


def test_preview_is_read_only_and_exact_dispatch_is_idempotent(tmp_path, pilot):
    pilot["grant"]()
    w, s, record = workspace(tmp_path, pilot)
    initial = deepcopy(record)
    req = inquiry(w, pilot)
    assert w.values("reasoning_job") == [] and s.conversations.list() == []
    with pytest.raises(HTTPException, match="409"):
        w.enqueue(req.model_copy(update={"question": "A changed selection"}))
    assert w.values("reasoning_job") == []
    w.enqueue(req)
    w.execute_one()
    assert job(w)["status"] == "complete", job(w)
    assert w.enqueue(req)["id"] == req.request_id
    view = w.session(job(w)["conversation_id"])
    receipt = view["turns"][0]["research"]["recipe_execution"]
    assert receipt["used"] == dict(public_calls=0, provider_calls=1, audio_calls=0)
    assert [s["name"] for s in receipt["steps"]] == ["selected_context", "reason"]
    assert 0 < receipt["retained_result_bytes"] <= req.recipe.limits.retained_bytes
    assert receipt["plan_sha256"] == req.recipe_plan_sha256 and record == initial
    assert len(view["turns"]) == 1
    w.close()


@pytest.mark.parametrize(
    "change",
    [
        dict(capabilities=["reason"]),
        dict(
            limits=dict(public_calls=0, audio_calls=1), name="selected_context_return"
        ),
        dict(
            searches=[dict(query="listening")],
            capabilities=["private_read", "public_search", "reason"],
        ),
    ],
)
def test_recipe_does_not_expand_capabilities_or_private_queries(
    tmp_path, pilot, change
):
    pilot["grant"]()
    w, _, _ = workspace(tmp_path, pilot)
    base = dict(name="selected_context_return", capabilities=["private_read", "reason"])
    if change.get("limits", {}).get("audio_calls") == 1:
        # An extra audio allowance alone is harmless: it cannot enable an action.
        req = inquiry(w, pilot, recipe=base | change)
        assert w.preview_recipe(req)["steps"] == ["selected_context", "reason"]
    else:
        with pytest.raises(HTTPException, match="409"):
            inquiry(w, pilot, recipe=base | change)
    assert w.values("reasoning_job") == []
    w.close()


def test_repair_cannot_dispatch_after_reserved_provider_limit(tmp_path, pilot):
    pilot["grant"]()
    provider = Provider(reply=lambda _: {"broken": True})
    w, s, _ = workspace(tmp_path, pilot, provider)
    req = inquiry(
        w, pilot, provider_id="ollama", limits=dict(provider_calls=1, output_tokens=300)
    )
    w.enqueue(req)
    w.execute_one()
    assert job(w)["status"] == "failed" and "provider_calls limit" in job(w)["detail"]
    assert len(provider.requests) == 1 and provider.requests[0].max_output_tokens == 300
    assert (
        s.conversations.list() == []
        and job(w)["recipe_execution"]["used"]["provider_calls"] == 1
    )
    w.close()


def test_provider_fallback_stops_without_commit_and_keeps_its_reason(tmp_path, pilot):
    pilot["grant"]()
    provider = Provider(reply=lambda _: {"broken": True})
    w, s, _ = workspace(tmp_path, pilot, provider)
    w.enqueue(inquiry(w, pilot, provider_id="ollama"))
    w.execute_one()
    detail = job(w)["detail"]
    assert job(w)["status"] == "failed", job(w)
    assert detail.startswith("Recipe stopped on provider fallback; no substitute response committed")
    assert ". Selected provider: " in detail and len(detail) <= 600
    assert len(provider.requests) == 2 and s.conversations.list() == []
    assert job(w)["recipe_execution"]["status"] == "stopped"
    w.close()


@pytest.mark.parametrize("failure", ["cancel", "deadline", "withdraw", "storage"])
def test_stop_conditions_prevent_late_commit(tmp_path, pilot, failure):
    pilot["grant"]()

    def reply(request):
        if failure == "cancel":
            w.cancel_job("recipe-one")
        if failure == "deadline":
            active().deadline_at = time.time() - 1
        if failure == "withdraw":
            pilot["grant"](active=False)
        ref = next(
            i["ref"]
            for i in request.metadata["evidence_packet"]["items"]
            if i["source"] == "local_passage"
        )
        return dict(
            answer_blocks=[
                dict(
                    kind="interpretation",
                    text=(
                        "x" * 11000 if failure == "storage" else "Synthetic difference"
                    ),
                    evidence_refs=[ref],
                )
            ],
            hypotheses=[],
            uncertainties=[],
            suggested_questions=[],
        )

    provider = Provider(reply=reply)
    w, s, _ = workspace(tmp_path, pilot, provider)
    req = inquiry(
        w,
        pilot,
        provider_id="ollama",
        limits=dict(retained_bytes=16384) if failure == "storage" else {},
    )
    w.enqueue(req)
    w.execute_one()
    assert job(w)["status"] in {"failed", "cancelled"}, job(w)
    assert len(provider.requests) == 1
    if failure == "storage":
        assert "retained-result byte limit" in job(w)["detail"]
    # Read the owner metadata after regranting only to inspect test results.
    if failure == "withdraw":
        pilot["grant"]()
    assert s.conversations.list() == []
    assert job(w)["recipe_execution"]["status"] in {"stopped", "cancelled"}
    w.close()


def test_unknown_dispatch_is_not_replayed_on_restart(tmp_path, pilot):
    pilot["grant"]()
    provider = Provider()
    w, _, _ = workspace(tmp_path, pilot, provider)
    w.enqueue(inquiry(w, pilot, provider_id="ollama"))
    value = job(w)
    value.update(
        status="running",
        recipe_execution=dict(used=dict(provider_calls=1), status="running"),
    )
    w.save_job(value)
    w.start()
    w.close()
    assert job(w)["status"] == "interrupted" and provider.requests == []
    assert job(w)["recipe_execution"]["used"]["provider_calls"] == 1


def test_public_search_keeps_leads_separate_and_counts_actual_calls(tmp_path, pilot):
    w, s, record = workspace(tmp_path, pilot)
    calls = []

    def transport(r):
        rpc = json.loads(r.content)
        name = rpc["params"]["name"]
        args = rpc["params"]["arguments"]
        calls.append((name, args))
        if name == "search_sonic_field":
            from oida.research_references import SearchInput

            data = search_data(
                SearchInput.model_validate(args),
                [public_record(slug="unselected-lead")],
                more=True,
            )
        else:
            data = public_record(slug="selected")
        return response(r, body(data))

    w.public = PublicDiscovery(transport=httpx.MockTransport(transport))
    req = Ask.model_validate(
        dict(
            request_id="recipe-one",
            record_id="akm_fixture",
            question="Frame this retained account",
            scope=dict(memories=False, wiki=False, sonicfield=True),
            sonicfield_records=[
                dict(family="lexicon", slug="selected", expectedId="selected")
            ],
            comparison=dict(
                kind="initial_account",
                reference="akm_fixture",
                sha256=w.journal.record_digest(record),
            ),
            recipe=dict(
                name="selected_context_return",
                capabilities=["public_search", "public_read", "reason"],
                searches=[dict(query="listening", families=["lexicon"])],
                limits=dict(public_calls=2),
            ),
        )
    )
    req.recipe_plan_sha256 = w.preview_recipe(req)["plan_sha256"]
    w.enqueue(req)
    w.execute_one()
    assert job(w)["status"] == "complete", job(w)
    turn = w.session(job(w)["conversation_id"])["turns"][0]
    assert (
        len(calls) == 2
        and turn["research"]["recipe_execution"]["used"]["public_calls"] == 2
    )
    assert [
        r["research_reference"]["record"]["identity"]["id"]
        for r in turn["research"]["sources"]
    ] == ["selected"]
    assert (
        turn["research"]["recipe_execution"]["discoveries"][0]["next_cursor"]
        == "fixture-continuation"
    )
    assert all("Frame this" not in json.dumps(args) for _, args in calls)
    w.close()


@pytest.mark.parametrize("clip_seconds", [2, 61])
def test_explicit_local_audio_pass_uses_linked_original_without_theory_text(
    tmp_path, pilot, clip_seconds
):
    pilot["grant"]()
    w, s, record = workspace(tmp_path, pilot)
    audio = tmp_path / "authored-silence.wav"
    with wave.open(str(audio), "wb") as f:
        f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        f.writeframes(b"\0\0" * (16000 * clip_seconds))
    audio_hash = sha256(audio.read_bytes()).hexdigest()
    event = dict(
        id="evt_fixture",
        privacy_mode="session",
        raw_audio_policy="external_ref",
        segment=dict(
            duration_ms=clip_seconds * 1000,
            data_ref=dict(kind="path", uri=str(audio), sha256=audio_hash),
        ),
    )
    record["listening"] = {
        "fixture": dict(payload=dict(listening_event_id=event["id"]))
    }
    # The real canonical reader remains authoritative; only event lookup is injected.
    w.acoustic_reader = lambda identifier: (
        deepcopy(event) if identifier == event["id"] else None
    )
    engine = _Engine()
    s.relistener = TargetedRelistener(engine, model_resolver=lambda value: "MOSS-Audio-4B-Thinking" if value in {"thinking", "MOSS-Audio-4B-Thinking"} else None)
    recipe = dict(
        name="local_relisten_return",
        capabilities=["private_read", "local_relisten", "reason"],
        limits=dict(audio_calls=1),
        relisten=dict(
            event_id=event["id"],
            expected_audio_sha256=audio_hash,
            question="Are there any repeated impacts?",
            model_id="thinking",
            start_s=0,
            end_s=2,
        ),
    )
    req = inquiry(w, pilot, recipe=recipe)
    original = deepcopy(record)
    w.enqueue(req)
    w.execute_one()
    if clip_seconds > 60:
        assert (
            job(w)["status"] == "failed" and "already bounded to 60" in job(w)["detail"]
        )
        assert engine.calls == [] and s.conversations.list() == []
        w.close()
        return
    assert job(w)["status"] == "complete", job(w)
    assert len(engine.calls) == 1 and "Escucha y diferencia" not in engine.calls[0][1]
    turn = w.session(job(w)["conversation_id"])["turns"][0]
    assert (
        turn["relisten"]["provenance"]["source_binding"]["observed_sha256"]
        == audio_hash
    )
    assert turn["research"]["recipe_execution"]["used"]["audio_calls"] == 1
    reasoning = json.dumps(turn["audit"])
    shared = turn["relisten"].get("observation_shared_to_reasoner")
    # The audit's "shared" means the reasoner received the observation (P8b, 21 Oct 2026).
    assert f'"relisten_observation_shared_to_reasoner": {json.dumps(bool(shared))}' in reasoning
    if shared:
        assert f"relisten:{turn['relisten']['id']}:observation:0" in reasoning
    assert (
        "one local targeted pass" in turn["research"]["comparison"]["acoustic_access"]
    )
    assert record == original
    assert canonical_bytes(turn) < req.recipe.limits.retained_bytes
    w.close()


def test_committed_recipe_is_reconciled_after_restart_without_new_provider_call(
    tmp_path, pilot
):
    pilot["grant"]()
    provider = Provider()
    w, s, _ = workspace(tmp_path, pilot, provider)
    w.enqueue(inquiry(w, pilot, provider_id="ollama"))
    w.execute_one()
    value = job(w)
    receipt = deepcopy(value["recipe_execution"])
    assert value["status"] == "complete"
    value["status"] = "running"
    value.pop("conversation_id")
    value.pop("recipe_execution")
    w.save_job(value)
    w.start()
    w.close()
    assert job(w)["status"] == "complete" and job(w)["recipe_execution"] == receipt
    assert len(provider.requests) == 1 and len(s.conversations.list()) == 1


def test_changed_selected_material_stops_before_provider_dispatch(tmp_path, pilot):
    pilot["grant"]()
    provider = Provider()
    w, s, _ = workspace(tmp_path, pilot, provider)
    w.enqueue(inquiry(w, pilot, provider_id="ollama"))
    pilot["source"].write_bytes(b"Replaced source bytes")
    w.execute_one()
    assert (
        job(w)["status"] == "failed"
        and provider.requests == []
        and s.conversations.list() == []
    )
    assert job(w)["recipe_execution"]["used"]["provider_calls"] == 0
    w.close()


def test_mounted_recipe_preview_and_public_mcp_do_not_expose_private_recipe(
    tmp_path, pilot, monkeypatch
):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from oida.reasoning_workspace import workspace_router
    from oida import mcp_server as mcp

    pilot["grant"]()
    w, _, _ = workspace(tmp_path, pilot)
    req = inquiry(w, pilot)
    app = FastAPI()
    app.include_router(workspace_router(w, lambda: {}, lambda _: {}, lambda _: None))
    http = TestClient(app)
    assert (
        http.get("/reasoning/workspace/recipes").json()["automatic_source_selection"]
        is False
    )
    response = http.post(
        "/reasoning/workspace/recipes/preview", json=req.model_dump(mode="json")
    )
    assert (
        response.status_code == 200
        and response.json()["plan_sha256"] == req.recipe_plan_sha256
    )
    assert w.values("reasoning_job") == []
    with pytest.raises(ValueError, match="private passage/audio"):
        asyncio.run(mcp.oida_research_recipe_preview(req.model_dump(mode="json")))
    w.enqueue(req)
    monkeypatch.setattr(mcp, "get_json", lambda *args: w.public_job(job(w)))
    with pytest.raises(ValueError, match="public-context recipe jobs only") as exc:
        asyncio.run(mcp.oida_research_recipe_status(req.request_id))
    assert pilot["entry"].terms_ref not in str(exc.value)
    with pytest.raises(ValueError, match="stable explicit request ID"):
        asyncio.run(mcp.oida_research_recipe_start({}))
    w.close()


def test_public_mcp_wrapper_uses_exact_ids_and_returns_only_its_own_turn(monkeypatch):
    import asyncio
    from oida import mcp_server as mcp
    from oida.research_recipes import plan

    req = Ask.model_validate(
        dict(
            request_id="stable-recipe",
            record_id="akm_fixture",
            question="Frame this account",
            comparison=dict(
                kind="initial_account", reference="akm_fixture", sha256="c" * 64
            ),
            scope=dict(memories=False, wiki=False, sonicfield=True),
            sonicfield_records=[
                dict(family="lexicon", slug="selected", expectedId="selected")
            ],
            recipe=dict(
                name="selected_context_return", capabilities=["reason", "public_read"]
            ),
        )
    )
    prepared = plan(req, "c" * 64)
    req.recipe_plan_sha256 = prepared["plan_sha256"]
    value = dict(
        id=req.request_id,
        status="complete",
        conversation_id="conv_fixture",
        recipe_plan=prepared,
    )
    calls = []

    def post(server, path, payload):
        calls.append((path, payload))
        return prepared if path.endswith("preview") else value

    def get(server, path):
        calls.append((path, None))
        if "/sessions/" in path:
            return dict(
                turns=[
                    dict(
                        id="other",
                        research=dict(request_id="another"),
                        answer_blocks=[dict(text="UNRELATED_PRIVATE_CANARY")],
                    ),
                    dict(
                        id="selected-turn",
                        question=req.question,
                        answer_blocks=[dict(text="Public contextual frame")],
                        research=dict(request_id=req.request_id, sources=[]),
                    ),
                ]
            )
        return value

    monkeypatch.setattr(mcp, "post_json", post)
    monkeypatch.setattr(mcp, "get_json", get)
    assert (
        asyncio.run(mcp.oida_research_recipe_preview(req.model_dump(mode="json")))[
            "plan_sha256"
        ]
        == prepared["plan_sha256"]
    )
    assert (
        asyncio.run(mcp.oida_research_recipe_start(req.model_dump(mode="json")))["id"]
        == req.request_id
    )
    result = asyncio.run(
        mcp.oida_research_recipe_status(req.request_id, include_result=True)
    )
    assert result["result"][
        "id"
    ] == "selected-turn" and "UNRELATED_PRIVATE_CANARY" not in json.dumps(result)
    asyncio.run(mcp.oida_research_recipe_cancel(req.request_id))
    assert sum(path == "/reasoning/workspace/ask" for path, _ in calls) == 1
    assert calls[-1][0] == "/reasoning/workspace/jobs/stable-recipe/cancel"
