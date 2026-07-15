from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest

from oida.conversation import ConversationStore
from oida.memory import AkousmataStore
from oida.reasoning.contracts import (
    ProviderDescriptor,
    ProviderResult,
    ProviderSettings,
    ReasoningSettings,
    RoleAssignment,
)
from oida.reasoning.orchestrator import ReasoningOrchestrator, TurnOptions
from oida.reasoning.registry import ProviderRegistry
from oida.reasoning.secrets import EnvironmentSecretStore
from oida.reasoning.settings import ReasoningSettingsStore


def _event(*, privacy_mode: str = "session") -> dict:
    return {
        "id": "evt_primary",
        "source": {"type": "file", "label": "private.wav", "details": {"path": "/private/audio.wav"}},
        "segment": {"duration_ms": 2500, "data_ref": {"kind": "path", "uri": "/private/audio.wav"}},
        "aggregate": {
            "title": "Steady pump",
            "short_summary": "A steady pump hum.",
            "signal_facts": ["RMS stays stable."],
            "warnings": ["The exact source remains uncertain."],
        },
        "features": {"duration_s": 2.5, "rmsDbfs": -24.0},
        "privacy_mode": privacy_mode,
        "raw_audio_policy": "external_ref",
    }


class _QueuedProvider:
    provider_id = "codex"

    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.requests = []

    def probe(self):
        return ProviderDescriptor(
            id="codex",
            name="Fake Codex",
            kind="host_cli",
            locality="unknown",
            enabled=True,
            available=True,
        )

    def list_models(self):
        return []

    def complete(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        payload = reply(request) if callable(reply) else reply
        if isinstance(payload, ProviderResult):
            return payload
        return ProviderResult(
            provider_id="codex",
            model_id=request.model_id,
            status="ok",
            parsed=payload,
            content="{}",
        )


class _Relistener:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        *,
        event,
        question,
        conversation_id,
        turn_id,
        model_id=None,
        time_range=None,
        allow_speech_content=False,
        parent_question=None,
    ):
        self.calls += 1
        return {
            "contract": "oida/relisten/v0.1",
            "id": "relisten_1",
            "base_event_id": event["id"],
            "segment_ref": "segment_1",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "question": question,
            "engine": "fake-local",
            "model": "MOSS-Audio-Thinking",
            "observation": "A soft click repeats near the midpoint.",
            "limitations": ["One local pass; original event unchanged."],
            "created_at": "2026-07-14T00:00:00Z",
            "sha256": "abc",
        }


def _enabled_codex_settings() -> ReasoningSettings:
    settings = ReasoningSettings()
    providers = dict(settings.providers)
    providers["codex"] = ProviderSettings(kind="host_cli", enabled=True, locality="unknown")
    roles = dict(settings.roles)
    roles["conversation"] = RoleAssignment(provider_id="codex", model_id="gpt-test")
    return settings.model_copy(update={"providers": providers, "roles": roles})


def _service(tmp: str, *, provider=None, relistener=None, settings=None):
    settings_store = ReasoningSettingsStore(Path(tmp) / "reasoning.json")
    settings_store.save(settings or ReasoningSettings())
    registry = ProviderRegistry()
    if provider is not None:
        registry.register(provider, enabled=True, configured=(settings or _enabled_codex_settings()).providers["codex"])
    return ReasoningOrchestrator(
        settings_store=settings_store,
        secret_store=EnvironmentSecretStore({}),
        conversations=ConversationStore(Path(tmp) / "conversations"),
        memory=AkousmataStore(root=Path(tmp) / "memory"),
        relistener=relistener,
        registry_factory=(lambda _settings: registry),
    )


def _valid(request):
    packet = request.metadata["evidence_packet"]
    ref = packet["items"][0]["ref"]
    return {
        "answer_blocks": [{"kind": "answer", "text": "Grounded answer.", "evidence_refs": [ref]}],
        "hypotheses": [],
        "uncertainties": [],
        "suggested_questions": [],
    }


def test_local_default_commits_v02_without_paths_and_keeps_anchor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(tmp)
        event = _event()
        before = copy.deepcopy(event)
        result = service.ask(event=event, question="How long is it?")
        stored = service.conversations.get(result["conversation_id"])

        assert event == before
        assert result["version"] == "0.2"
        assert result["turn"]["reasoner"]["provider_id"] == "local_structured"
        assert "2.50 seconds" in result["turn"]["answer"]
        # An external_ref event may retain its local source reference in the
        # durable anchor, but the prompt/evidence/audit turn must not copy it.
        assert "/private/audio.wav" not in str(stored["turns"][0])

        with pytest.raises(ValueError, match="anchored"):
            service.ask(
                event={**event, "id": "evt_other"},
                question="What changed?",
                options=TurnOptions(conversation_id=result["conversation_id"]),
            )


def test_provider_gets_one_repair_then_visible_local_fallback() -> None:
    provider = _QueuedProvider([
        {"answer_blocks": [{"kind": "fact", "text": "Unsupported", "evidence_refs": ["fake"]}]},
        {"answer_blocks": [{"kind": "fact", "text": "Still unsupported", "evidence_refs": ["fake"]}]},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(tmp, provider=provider, settings=_enabled_codex_settings())
        result = service.ask(event=_event(), question="What do you hear?")

    assert len(provider.requests) == 2
    assert result["turn"]["reasoner"]["provider_id"] == "local_structured"
    assert result["turn"]["fallback"]["used"] is True
    assert result["turn"]["fallback"]["from_provider_id"] == "codex"
    assert result["turn"]["remote_model"]["enabled"] is False


def test_provider_repair_can_succeed_without_cross_provider_failover() -> None:
    provider = _QueuedProvider([
        {"answer_blocks": [{"kind": "fact", "text": "Bad ref", "evidence_refs": ["fake"]}]},
        _valid,
    ])
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(tmp, provider=provider, settings=_enabled_codex_settings())
        result = service.ask(event=_event(), question="What do you hear?")

    assert len(provider.requests) == 2
    assert result["turn"]["reasoner"]["provider_id"] == "codex"
    assert result["turn"]["reasoner"]["repaired"] is True
    assert result["turn"]["fallback"] is None


def test_one_targeted_local_relisten_then_one_final_reasoning_pass() -> None:
    def request_relisten(request):
        ref = request.metadata["evidence_packet"]["items"][0]["ref"]
        return {
            "answer_blocks": [{"kind": "dialogue", "text": "A closer pass would help.", "evidence_refs": [ref]}],
            "requested_action": {"type": "targeted_relisten", "question": "Listen for a midpoint click"},
        }

    def answer_relisten(request):
        refs = [item["ref"] for item in request.metadata["evidence_packet"]["items"]]
        relisten_ref = next(ref for ref in refs if ref.startswith("relisten:"))
        return {
            "answer_blocks": [{"kind": "answer", "text": "A click repeats near the midpoint.", "evidence_refs": [relisten_ref]}],
        }

    provider = _QueuedProvider([request_relisten, answer_relisten])
    relistener = _Relistener()
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(
            tmp,
            provider=provider,
            relistener=relistener,
            settings=_enabled_codex_settings(),
        )
        result = service.ask(event=_event(), question="Is there a click?")

    assert relistener.calls == 1
    assert len(provider.requests) == 2
    assert result["turn"]["audit"]["targeted_relisten_count"] == 1
    assert result["turn"]["relisten"]["observation"] is None
    assert result["turn"]["relisten"]["observation_withheld"] is True
    assert result["turn"]["requested_action"] is None


def test_post_relisten_pass_can_use_the_turns_one_repair_budget() -> None:
    def request_relisten(request):
        ref = request.metadata["evidence_packet"]["items"][0]["ref"]
        return {
            "answer_blocks": [
                {"kind": "dialogue", "text": "A closer pass would help.", "evidence_refs": [ref]}
            ],
            "requested_action": {
                "type": "targeted_relisten",
                "question": "Listen for a midpoint click",
            },
        }

    provider = _QueuedProvider(
        [
            request_relisten,
            {"answer_blocks": [{"kind": "fact", "text": "Bad ref", "evidence_refs": ["fake"]}]},
            _valid,
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(
            tmp,
            provider=provider,
            relistener=_Relistener(),
            settings=_enabled_codex_settings(),
        )
        result = service.ask(event=_event(), question="Is there a click?")

    assert len(provider.requests) == 3
    assert result["turn"]["reasoner"]["repaired"] is True
    assert result["turn"]["fallback"] is None


def test_turn_never_uses_a_second_repair_after_relisten() -> None:
    def repaired_relisten(request):
        ref = request.metadata["evidence_packet"]["items"][0]["ref"]
        return {
            "answer_blocks": [
                {"kind": "dialogue", "text": "A closer pass would help.", "evidence_refs": [ref]}
            ],
            "requested_action": {
                "type": "targeted_relisten",
                "question": "Listen for a midpoint click",
            },
        }

    invalid = {"answer_blocks": [{"kind": "fact", "text": "Bad ref", "evidence_refs": ["fake"]}]}
    provider = _QueuedProvider([invalid, repaired_relisten, invalid])
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(
            tmp,
            provider=provider,
            relistener=_Relistener(),
            settings=_enabled_codex_settings(),
        )
        result = service.ask(event=_event(), question="Is there a click?")

    assert len(provider.requests) == 3
    assert result["turn"]["fallback"]["used"] is True


def test_incognito_forces_local_and_never_persists() -> None:
    provider = _QueuedProvider([_valid])
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(tmp, provider=provider, settings=_enabled_codex_settings())
        result = service.ask(event=_event(privacy_mode="incognito"), question="What happened?")

        assert provider.requests == []
        assert result["persistent"] is False
        assert result["turn"]["fallback"]["used"] is True
        assert list((Path(tmp) / "conversations").glob("*.json")) == []


def test_stricter_turn_does_not_replay_prior_transcript_or_memory_content() -> None:
    event = _event()
    event["features"].update({"peakDbfs": -10.0, "sample_rate": 48_000, "channels": 2})
    event["routes"] = [
        {
            "route_id": "voice",
            "structured": {
                "claim_summary": {
                    "heard": [
                        {
                            "statement": "TRANSCRIPT_SECRET",
                            "source": "transcript",
                            "speech_content": True,
                            "basis": "ASR",
                        }
                    ]
                }
            },
        }
    ]

    def first_reply(request):
        items = request.metadata["evidence_packet"]["items"]
        transcript_ref = next(item["ref"] for item in items if item["kind"] == "transcript")
        memory_ref = next(item["ref"] for item in items if item["kind"] == "memory")
        return {
            "answer_blocks": [
                {"kind": "answer", "text": "TRANSCRIPT_SECRET", "evidence_refs": [transcript_ref]},
                {"kind": "fact", "text": "MEMORY_SECRET", "evidence_refs": [memory_ref]},
            ]
        }

    provider = _QueuedProvider([first_reply, _valid])
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(tmp, provider=provider, settings=_enabled_codex_settings())
        memory_event = copy.deepcopy(event)
        memory_event["id"] = "evt_memory"
        memory_event["aggregate"]["title"] = "MEMORY_SECRET"
        memory_event["aggregate"]["short_summary"] = "MEMORY_SECRET"
        service.memory.remember(memory_event)
        first = service.ask(
            event=event,
            question="Use the private context?",
            options=TurnOptions(include_transcript=True, include_memory_content=True),
        )
        service.ask(
            event=event,
            question="Now answer without private context.",
            options=TurnOptions(
                conversation_id=first["conversation_id"],
                include_transcript=False,
                include_memory_content=False,
            ),
        )

    second_prompt = provider.requests[1].user_prompt
    assert "TRANSCRIPT_SECRET" not in second_prompt
    assert "MEMORY_SECRET" not in second_prompt


def test_new_covenant_drops_prior_dialogue_even_when_user_opt_in_remains_true() -> None:
    event = _event()
    event["routes"] = [
        {
            "route_id": "voice",
            "structured": {
                "claim_summary": {
                    "heard": [
                        {
                            "statement": "ACTIVE_COVENANT_SECRET",
                            "source": "transcript",
                            "speech_content": True,
                        }
                    ]
                }
            },
        }
    ]

    def first_reply(request):
        transcript_ref = next(
            item["ref"]
            for item in request.metadata["evidence_packet"]["items"]
            if item["kind"] == "transcript"
        )
        return {
            "answer_blocks": [
                {
                    "kind": "answer",
                    "text": "ACTIVE_COVENANT_SECRET",
                    "evidence_refs": [transcript_ref],
                }
            ]
        }

    provider = _QueuedProvider([first_reply, _valid])
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(tmp, provider=provider, settings=_enabled_codex_settings())
        first = service.ask(
            event=event,
            question="What was said?",
            options=TurnOptions(include_transcript=True),
        )
        governed = copy.deepcopy(event)
        governed["covenant"] = {
            "id": "current/1",
            "rules_applied": ["do_not_reveal:transcript"],
            "withheld": [{"rule": "do_not_reveal", "subject": "transcript", "count": 1}],
        }
        service.ask(
            event=governed,
            question="What can you say now?",
            options=TurnOptions(
                conversation_id=first["conversation_id"],
                include_transcript=True,
            ),
        )

    assert "ACTIVE_COVENANT_SECRET" not in provider.requests[1].user_prompt


def test_prepare_commit_token_is_one_time_and_host_output_is_validated() -> None:
    provider = _QueuedProvider([])
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(tmp, provider=provider, settings=_enabled_codex_settings())
        prepared = service.prepare(event=_event(), question="What happened?")
        packet = prepared["evidence_packet"]
        ref = packet["items"][0]["ref"]
        response = {
            "answer_blocks": [{"kind": "answer", "text": "Host-grounded answer.", "evidence_refs": [ref]}]
        }
        committed = service.commit_prepared(token=prepared["prepare_token"], response=response)

        assert committed["turn"]["reasoner"]["host_managed"] is True
        assert committed["conversation_id"] == prepared["conversation_id"]
        with pytest.raises(ValueError, match="already used"):
            service.commit_prepared(token=prepared["prepare_token"], response=response)


def test_prepare_commit_can_issue_exactly_one_relisten_followup_packet() -> None:
    provider = _QueuedProvider([])
    relistener = _Relistener()
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(
            tmp,
            provider=provider,
            relistener=relistener,
            settings=_enabled_codex_settings(),
        )
        first = service.prepare(event=_event(), question="Is there a click?")
        first_ref = first["evidence_packet"]["items"][0]["ref"]
        followup = service.commit_prepared(
            token=first["prepare_token"],
            response={
                "answer_blocks": [
                    {"kind": "dialogue", "text": "A closer local pass would help.", "evidence_refs": [first_ref]}
                ],
                "requested_action": {
                    "type": "targeted_relisten",
                    "question": "Listen for a midpoint click",
                },
            },
        )
        assert followup["requires_followup"] is True
        assert followup["stage"] == 1
        relisten_ref = next(
            item["ref"]
            for item in followup["evidence_packet"]["items"]
            if item["kind"] == "relisten"
        )
        committed = service.commit_prepared(
            token=followup["prepare_token"],
            response={
                "answer_blocks": [
                    {
                        "kind": "answer",
                        "text": "A click repeats near the midpoint.",
                        "evidence_refs": [relisten_ref],
                    }
                ]
            },
        )

    assert relistener.calls == 1
    assert committed["turn"]["audit"]["targeted_relisten_count"] == 1
