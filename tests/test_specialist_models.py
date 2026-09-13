from __future__ import annotations

import sys
import wave
from pathlib import Path

import pytest
from oida.recipes import INSTRUCT_CAPTION
from oida.reasoning.contracts import (
    ProviderKind,
    ProviderLocality,
    ProviderSettings,
)
from oida.reasoning.settings import ReasoningSettings, ReasoningSettingsStore
from oida.reasoning.audio_router import RoutedAudioEngine, EngineUnavailable
from oida.reasoning.audio_selection import selector as audio_selector, use_audio_model
from oida.reasoning.budget import BudgetLedger, quote_cost
from oida.reasoning.model_catalog import (
    FAST_PERCEPTION,
    DEEP_PERCEPTION,
    MUSIC_ANALYSIS,
    TRANSCRIPTION,
    find_model_spec,
    supports_role,
)


class DictSecrets:
    def __init__(self, secrets: dict[tuple[str, str], str]):
        self.secrets = secrets

    def get(self, provider_id: str, field_name: str) -> str | None:
        return self.secrets.get((provider_id, field_name))

    def get_provider_secret(self, provider_id: str, field_name: str) -> str | None:
        return self.secrets.get((provider_id, field_name))


class FakeEngine:
    available = False

    def descriptor(self):
        return None


class FakeTransport:
    def __init__(self, response_payload: dict, status: int = 200):
        self.payload = response_payload
        self.status = status
        self.sent_requests: list[dict] = []

    def request(self, method: str, url: str, *, payload=None, headers=None, timeout=None):
        self.sent_requests.append({
            "method": method,
            "url": url,
            "payload": payload,
            "headers": headers,
            "timeout": timeout,
        })
        from collections import namedtuple
        Response = namedtuple("Response", ["status", "data"])
        return Response(status=self.status, data=self.payload)


def _make_audio(directory: Path, name: str = "sample.wav") -> Path:
    path = directory / name
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(bytes(16000))
    return path


def test_moss_music_catalog_spec_and_input_representation():
    instruct_spec = find_model_spec("oida_moss", "OpenMOSS-Team/MOSS-Music-8B-Instruct")
    assert instruct_spec is not None
    assert instruct_spec.runtime == "cuda_sglang_isolated_runtime"
    assert instruct_spec.license == "Apache-2.0"
    assert instruct_spec.revision == "v1.0"
    assert instruct_spec.sample_rate_hz == 24000
    assert instruct_spec.platforms == ("linux-cuda-sglang",)
    assert MUSIC_ANALYSIS in instruct_spec.roles
    assert FAST_PERCEPTION in instruct_spec.roles

    desc = instruct_spec.descriptor()
    meta = desc.metadata
    assert meta["sample_rate_hz"] == 24000
    assert meta["input_representation"] == {"sample_rate_hz": 24000, "channels": 2}
    assert "Requires a dedicated Linux/CUDA host" in meta["notes"]

    thinking_spec = find_model_spec("oida_moss", "OpenMOSS-Team/MOSS-Music-8B-Thinking")
    assert thinking_spec is not None
    assert thinking_spec.runtime == "cuda_sglang_isolated_runtime"
    assert thinking_spec.license == "Apache-2.0"
    assert thinking_spec.revision == "v1.0"
    assert thinking_spec.sample_rate_hz == 24000
    assert DEEP_PERCEPTION in thinking_spec.roles
    assert MUSIC_ANALYSIS in thinking_spec.roles


def test_step_audio_catalog_spec_and_pricing():
    mini_spec = find_model_spec("stepfun", "stepfun-ai/Step-Audio-2-mini")
    assert mini_spec is not None
    assert mini_spec.provider_id == "stepfun"
    assert mini_spec.locality == ProviderLocality.EXTERNAL
    assert mini_spec.audio_transport == "openai_input_audio"
    assert FAST_PERCEPTION in mini_spec.roles
    assert TRANSCRIPTION in mini_spec.roles

    think_spec = find_model_spec("stepfun", "stepfun-ai/Step-Audio-2-mini-Think")
    assert think_spec is not None
    assert think_spec.provider_id == "stepfun"
    assert DEEP_PERCEPTION in think_spec.roles

    quote = quote_cost("stepfun", "stepfun-ai/Step-Audio-2-mini", audio_seconds=10.0, max_output_tokens=500)
    assert quote is not None
    assert quote["provider_id"] == "stepfun"
    assert quote["worst_case_usd"] > 0
    assert quote["audio_tokens"] == 250


def test_groq_whisper_catalog_spec_and_strict_role_boundary():
    groq_spec = find_model_spec("groq", "whisper-large-v3-turbo")
    assert groq_spec is not None
    assert groq_spec.provider_id == "groq"
    assert groq_spec.roles == (TRANSCRIPTION,)
    assert groq_spec.audio_transport == "openai_transcription"

    # Dedicated transcription only: must refuse general perception and music
    assert supports_role("groq", "whisper-large-v3-turbo", TRANSCRIPTION) is True
    assert supports_role("groq", "whisper-large-v3-turbo", FAST_PERCEPTION) is False
    assert supports_role("groq", "whisper-large-v3-turbo", DEEP_PERCEPTION) is False
    assert supports_role("groq", "whisper-large-v3-turbo", MUSIC_ANALYSIS) is False

    quote = quote_cost("groq", "whisper-large-v3-turbo", audio_seconds=3600.0, max_output_tokens=100)
    assert quote is not None
    # 1 hour of audio should be ~$0.04
    assert 0.039 <= quote["worst_case_usd"] <= 0.045


def test_step_audio_vocal_acoustics_and_anti_demographic_guardrails(tmp_path: Path):
    settings_store = ReasoningSettingsStore(tmp_path / "reasoning.json")
    settings = ReasoningSettings(allow_external_audio=True)
    providers = dict(settings.providers)
    providers["stepfun"] = ProviderSettings(
        kind=ProviderKind.OPENAI_COMPATIBLE,
        enabled=True,
        locality=ProviderLocality.EXTERNAL,
        base_url="https://api.stepfun.com/v1",
        default_model="stepfun-ai/Step-Audio-2-mini",
        options={"audio_capable": True, "audio_transport": "catalog"},
    )
    settings = settings.model_copy(update={"providers": providers})
    settings_store.save(settings)

    # Response includes both valid acoustic observations and demographic guesses
    transport = FakeTransport({
        "model": "stepfun-ai/Step-Audio-2-mini",
        "choices": [{
            "message": {
                "content": "The voice has high jitter and fast tempo. The speaker appears to be a 20-year-old female in an untreated room.",
                "reasoning_content": "Secret Chain of Thought: demographic guessing",
            }
        }],
        "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
    })

    budget = BudgetLedger(tmp_path / "budget")
    budget.update_config({"enabled": True, "total_ceiling_usd": 5.0})

    router = RoutedAudioEngine(
        FakeEngine(),
        settings_store=settings_store,
        secret_store=DictSecrets({("stepfun", "api_key"): "sk-step-test"}),
        budget_ledger=budget,
        transport=transport,
    )

    audio_path = _make_audio(tmp_path)
    spec = find_model_spec("stepfun", "stepfun-ai/Step-Audio-2-mini")
    selection = audio_selector(spec)

    with use_audio_model(selection):
        result = router.generate(str(audio_path), "Describe vocal texture.", INSTRUCT_CAPTION)

    # 1. Transport got system prompt with anti-demographic guidance
    assert len(transport.sent_requests) == 1
    req_payload = transport.sent_requests[0]["payload"]
    sys_msg = next(m["content"] for m in req_payload["messages"] if m["role"] == "system")
    assert "acoustic and vocal specialist" in sys_msg
    assert "Do not infer, guess, or state identity, demographics" in sys_msg

    # 2. Text output was post-sanitized to strip demographic speculation
    assert "[vocal acoustic observation only]" in result.text
    assert "20-year-old female" not in result.text
    assert "high jitter and fast tempo" in result.text

    # 3. Private thinking trace was discarded
    assert result.reasoning_trace is None


def test_groq_whisper_refuses_general_listening(tmp_path: Path):
    settings_store = ReasoningSettingsStore(tmp_path / "reasoning.json")
    settings = ReasoningSettings(allow_external_audio=True)
    providers = dict(settings.providers)
    providers["groq"] = ProviderSettings(
        kind=ProviderKind.OPENAI_COMPATIBLE,
        enabled=True,
        locality=ProviderLocality.EXTERNAL,
        base_url="https://api.groq.com/openai/v1",
        default_model="whisper-large-v3-turbo",
        options={"audio_capable": True, "audio_transport": "openai_transcription"},
    )
    settings = settings.model_copy(update={"providers": providers})
    settings_store.save(settings)

    router = RoutedAudioEngine(
        FakeEngine(),
        settings_store=settings_store,
        secret_store=DictSecrets({("groq", "api_key"): "gsk-test"}),
    )

    audio_path = _make_audio(tmp_path)
    spec = find_model_spec("groq", "whisper-large-v3-turbo")
    selection = audio_selector(spec)

    # Calling with general perception (INSTRUCT_CAPTION has model_kind="perception")
    with use_audio_model(selection):
        with pytest.raises(EngineUnavailable, match="does not support this listening role"):
            router.generate(str(audio_path), "Analyze the acoustics", INSTRUCT_CAPTION)


def test_mac_unsupported_claim_prevention_in_listening_options(tmp_path: Path):
    from fastapi.testclient import TestClient
    from oida.server import app

    client = TestClient(app, base_url="http://127.0.0.1")
    resp = client.get("/listening/options")
    assert resp.status_code == 200
    data = resp.json()
    models = data["models"]

    # Check MOSS-Music models in listening options
    music_instruct = next((m for m in models if m.get("audio_model", {}).get("model_id") == "OpenMOSS-Team/MOSS-Music-8B-Instruct"), None)
    assert music_instruct is not None

    if sys.platform == "darwin":
        # On macOS, MOSS-Music must visibly report available: False with clear reason
        assert music_instruct["available"] is False
        assert "Requires dedicated Linux/CUDA host running SGLang; unsupported on macOS MPS" in music_instruct["unavailable_reason"]

    # Check Step-Audio CUDA worker
    step_worker = next((m for m in models if m.get("audio_model", {}).get("model_id") == "stepfun-ai/Step-Audio-2-mini-worker"), None)
    assert step_worker is not None
    if sys.platform == "darwin":
        assert step_worker["available"] is False
        assert "unsupported on macOS MPS" in step_worker["unavailable_reason"]
