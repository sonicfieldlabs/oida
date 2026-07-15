from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import os
from unittest.mock import patch

import pytest

from bench_adapter.client import post_payload
from harness.http_client import get_json
from oida.chunker import plan_chunks
from oida.conversation import ConversationStore
from oida.config import load_config
from oida.engine_base import EngineUnavailable
from oida.engine_mps import MpsMossEngine
from oida.engine_sglang import SGLangMossEngine
from oida.generation import GenerationStore
from oida.lifecycle import doctor
from oida.reasoning.providers.base import JsonResponse
from oida.recipes import GenerationSettings


class FakeTransport:
    def __init__(self, response: JsonResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> JsonResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def _event() -> dict[str, object]:
    return {
        "id": "evt_concurrent",
        "aggregate": {"title": "Rain", "short_summary": "Steady rain."},
        "privacy_mode": "session",
        "raw_audio_policy": "external_ref",
    }


def _settings() -> GenerationSettings:
    return GenerationSettings(
        model_kind="thinking",
        temperature=0.1,
        top_p=0.9,
        top_k=50,
        max_new_tokens=128,
    )


@pytest.mark.parametrize(
    ("chunk_seconds", "overlap_seconds"),
    [
        (0.0, 0.0),
        (-1.0, 0.0),
        (float("nan"), 0.0),
        (1.0, -0.1),
        (1.0, float("inf")),
        (1.0, 1.0),
        (1.0, 2.0),
    ],
)
def test_chunk_planning_rejects_non_progressing_settings(
    chunk_seconds: float,
    overlap_seconds: float,
) -> None:
    with patch("oida.chunker.audio_duration", return_value=10.0):
        with pytest.raises(ValueError):
            plan_chunks("fixture.wav", chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "infinity", "not-a-number"])
def test_config_rejects_invalid_chunk_budgets(value: str) -> None:
    with patch.dict(os.environ, {"OIDA_MOSS_CHUNK_SECONDS": value}, clear=False):
        with pytest.raises(ValueError, match="OIDA_MOSS_CHUNK_SECONDS"):
            load_config(profile="stub")


def test_chunk_planning_caps_pathological_pass_counts() -> None:
    with patch("oida.chunker.audio_duration", return_value=100.0):
        with pytest.raises(ValueError, match="safety limit"):
            plan_chunks("fixture.wav", chunk_seconds=0.001, overlap_seconds=0.0)


def test_stale_conversation_snapshots_do_not_lose_turns(tmp_path: Path) -> None:
    store = ConversationStore(root=tmp_path / "conversations")
    event = _event()
    created = store.append_structured_turn(event=event, turn={"answer": "first"})
    first_snapshot = store.prepare(event=event, conversation_id=created["conversation_id"])
    second_snapshot = store.prepare(event=event, conversation_id=created["conversation_id"])

    store.append_turn(event=event, turn={"answer": "second"}, **first_snapshot)
    store.append_turn(event=event, turn={"answer": "third"}, **second_snapshot)

    stored = store.get(created["conversation_id"])
    assert [turn["answer"] for turn in stored["turns"]] == ["first", "second", "third"]


def test_generation_store_ignores_non_object_records_and_rejects_them_on_get(tmp_path: Path) -> None:
    store = GenerationStore(root=tmp_path / "generations")
    store.records_dir.mkdir(parents=True)
    invalid_path = store.records_dir / "invalid.json"
    invalid_path.write_text(json.dumps(["not", "a", "record"]), encoding="utf-8")

    assert store.list() == []
    with pytest.raises(ValueError, match="invalid generation record"):
        store.get("invalid")


def test_sglang_uses_hardened_transport_and_server_visible_audio_path(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.touch()
    transport = FakeTransport(
        JsonResponse(
            200,
            {
                "model": "moss-audio",
                "choices": [
                    {
                        "message": {
                            "content": [{"type": "text", "text": "Rain is audible."}],
                            "reasoning_content": "private trace",
                        }
                    }
                ],
            },
            {},
        )
    )
    engine = SGLangMossEngine(
        SimpleNamespace(
            sglang_base_url="http://127.0.0.1:30000",
            sglang_thinking_processor="serialized-processor",
        ),  # type: ignore[arg-type]
        transport=transport,
    )

    result = engine.generate(str(audio), "Describe it.", _settings(), thinking_budget=64)

    assert result.text == "Rain is audible."
    assert result.reasoning_trace == "private trace"
    call = transport.calls[0]
    assert call["url"] == "http://127.0.0.1:30000/v1/chat/completions"
    payload = call["payload"]
    assert isinstance(payload, dict)
    assert payload["separate_reasoning"] is True
    assert payload["custom_logit_processor"] == "serialized-processor"
    assert payload["custom_params"] == {"thinking_budget": 64}
    assert payload["messages"][0]["content"][0]["audio_url"]["url"] == str(audio.resolve())


def test_sglang_rejects_invalid_response_shape(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.touch()
    engine = SGLangMossEngine(
        SimpleNamespace(sglang_base_url="http://127.0.0.1:30000"),  # type: ignore[arg-type]
        transport=FakeTransport(JsonResponse(200, {"choices": []}, {})),
    )

    with pytest.raises(EngineUnavailable, match="SGLang server unavailable"):
        engine.generate(str(audio), "Describe it.", _settings())


def test_sglang_refuses_to_claim_an_unconfigured_thinking_budget(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.touch()
    engine = SGLangMossEngine(
        SimpleNamespace(sglang_base_url="http://127.0.0.1:30000"),  # type: ignore[arg-type]
        transport=FakeTransport(JsonResponse(200, {}, {})),
    )

    with pytest.raises(EngineUnavailable, match="OIDA_SGLANG_THINKING_PROCESSOR"):
        engine.generate(str(audio), "Describe it.", _settings(), thinking_budget=64)


def test_embedded_runtime_refuses_to_claim_an_unsupported_thinking_budget() -> None:
    engine = MpsMossEngine(SimpleNamespace(moss_audio_repo=None))  # type: ignore[arg-type]

    with pytest.raises(EngineUnavailable, match="embedded Transformers runtime"):
        engine.generate("unused.wav", "Describe it.", _settings(), thinking_budget=64)


def test_cli_http_clients_reject_plaintext_non_loopback_servers() -> None:
    with pytest.raises(ValueError, match="loopback"):
        get_json("http://example.com", "/health")
    with pytest.raises(ValueError, match="loopback"):
        post_payload("http://example.com/results", {"ok": True})


def test_doctor_checks_the_unambiguous_distribution_name(tmp_path: Path) -> None:
    requested: list[str] = []

    def installed_version(name: str) -> str:
        requested.append(name)
        return "1.0"

    config = SimpleNamespace(
        instruct_model=str(tmp_path / "instruct"),
        thinking_model=str(tmp_path / "thinking"),
        data_dir=tmp_path / "data",
        audio_dir=tmp_path / "audio",
    )
    with (
        patch("oida.lifecycle.load_config", return_value=config),
        patch("oida.lifecycle.importlib.metadata.version", side_effect=installed_version),
        patch("oida.lifecycle.gateway_status", return_value={}),
        patch("oida.lifecycle.shutil.which", return_value="/usr/bin/tool"),
        patch("oida.lifecycle._gateway_environment", return_value={}),
    ):
        result = doctor()

    assert result["packages"]["sonicfield-oida"] == "1.0"
    assert "sonicfield-oida" in requested
    assert "oida" not in requested
