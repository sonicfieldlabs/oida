"""Provider fixtures establish execution plumbing, not model competence."""

from pathlib import Path
import pytest
import json
from test_reasoning_orchestrator import _service
from test_reasoning_providers import FakeTransport
from oida.reasoning.providers.base import JsonResponse
from oida.reasoning.providers.openai_compatible import OpenAICompatibleProvider
from oida.reasoning.contracts import ReasoningSettings, ProviderSettings, RoleAssignment
from oida.reasoning.registry import ProviderRegistry


def service_with_transport(tmp_path, *, external=False, invalid=False):
    base_url = "https://example.invalid/v1" if external else "http://127.0.0.1:9000/v1"
    settings = ReasoningSettings()
    configured = ProviderSettings(
        kind="openai_compatible", enabled=True, base_url=base_url
    )
    settings = settings.model_copy(
        update={
            "providers": {**settings.providers, "openai_compatible": configured},
            "roles": {
                **settings.roles,
                "conversation": RoleAssignment(
                    provider_id="openai_compatible", model_id="fixture-text-model"
                ),
            },
        }
    )
    answer = (
        {}
        if invalid
        else dict(
            answer_blocks=[
                dict(
                    kind="answer",
                    text="Retained evidence remains unverified.",
                    evidence_refs=["event:retained:anchor"],
                )
            ],
            hypotheses=[],
            uncertainties=[],
            suggested_questions=[],
        )
    )
    transport = FakeTransport(
        lambda call: JsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {"content": json.dumps(answer)},
                        "finish_reason": "stop",
                    }
                ]
            },
            {},
        )
    )
    provider = OpenAICompatibleProvider(
        base_url=base_url, enabled=True, transport=transport
    )
    registry = ProviderRegistry()
    registry.register(provider, enabled=True, configured=configured)
    service = _service(str(tmp_path), settings=settings)
    service.registry_factory = lambda settings: registry
    return service, transport


def event():
    return dict(
        id="retained",
        privacy_mode="session",
        raw_audio_policy="temp",
        aggregate={"short_summary": "Retained account"},
        routes=[
            dict(
                structured={
                    "claim_summary": {
                        "undetermined": [
                            dict(
                                statement="Retained measured claim: fixture band energy",
                                source="memory",
                                confidence="undetermined",
                            )
                        ]
                    }
                }
            )
        ],
    )


def test_local_provider_execution_keeps_inherited_claim_and_no_conversation(tmp_path):
    service, transport = service_with_transport(tmp_path)
    value = service.evaluate_retained(event=event(), question="Inspect this record")
    assert value["execution"]["provider_id"] == "openai_compatible"
    assert value["execution"]["model_id"] == "fixture-text-model"
    assert value["execution"]["fallback"] is None
    claims = [i for i in value["evidence_packet"]["items"] if i["kind"] == "claim"]
    assert len(claims) == 1 and claims[0]["source"] == "memory"
    assert claims[0]["category"] == "undetermined"
    assert len(transport.calls) == 1
    assert not list((Path(tmp_path) / "conversations").rglob("*.json"))


def test_external_provider_refused_before_execution(tmp_path):
    service, transport = service_with_transport(tmp_path, external=True)
    with pytest.raises(ValueError, match="local provider"):
        service.evaluate_retained(event=event(), question="Inspect")
    assert transport.calls == []


def test_invalid_model_response_cannot_be_promoted_from_fallback(tmp_path):
    service, transport = service_with_transport(tmp_path, invalid=True)
    with pytest.raises(ValueError, match="fallback is not a model pass"):
        service.evaluate_retained(event=event(), question="Inspect")
    assert len(transport.calls) == 2
    assert not list((tmp_path / "conversations").rglob("*.json"))
