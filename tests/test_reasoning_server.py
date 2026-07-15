from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from oida.engine_base import EngineUnavailable
from oida.reasoning.contracts import ModelRole, ReasoningSettings, RoleAssignment
from oida.reasoning.settings import ReasoningSettingsStore
from oida.server import create_app


def _event(event_id: str, title: str = "Pump hum") -> dict:
    return {
        "id": event_id,
        "source": {"type": "file", "label": f"{event_id}.wav"},
        "segment": {
            "duration_ms": 2500,
            "data_ref": {"kind": "external", "uri": f"/no/audio/{event_id}.wav"},
        },
        "aggregate": {
            "title": title,
            "short_summary": f"{title} with a steady pulse.",
            "signal_facts": ["RMS stays stable."],
            "warnings": ["The exact source is uncertain."],
        },
        "features": {"duration_s": 2.5, "rmsDbfs": -24.0},
        "privacy_mode": "session",
        "raw_audio_policy": "external_ref",
    }


class _Client:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.environment = patch.dict(
            os.environ,
            {
                "OIDA_DATA_DIR": str(root / "data"),
                "OIDA_AUDIO_DIR": str(root / "audio"),
                "AKOUSMATA_PATH": str(root / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
                "OIDA_MOSS_PREWARM": "0",
            },
            clear=False,
        )

    def __enter__(self):
        self.environment.__enter__()
        self.client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
        # The mounted FastMCP session manager is process-global and explicitly
        # single-run; endpoint tests do not need to start that lifespan.
        return self.client

    def __exit__(self, *args):
        try:
            self.client.close()
        finally:
            self.environment.__exit__(*args)
            self.tmp.cleanup()


def test_reasoning_settings_provider_and_model_endpoints() -> None:
    with _Client() as client:
        settings = client.get("/reasoning/settings")
        providers = client.get("/reasoning/providers")
        models = client.get("/reasoning/models?provider_id=local_structured")

        assert settings.status_code == 200
        assert settings.json()["active_provider_id"] == "local_structured"
        provider_ids = {item["id"] for item in providers.json()["providers"]}
        assert {
            "local_structured",
            "oida_moss",
            "ollama",
            "openai_compatible",
            "openrouter",
            "codex",
            "claude",
            "hermes",
            "openclaw",
            "opencode",
            "local_audio",
            "google",
            "alibaba",
            "nvidia",
        } <= provider_ids
        assert set(settings.json()["role_assignments"]) == {
            "fast_perception",
            "deep_perception",
            "transcription",
            "music_analysis",
            "conversation",
            "targeted_relisten",
        }
        assert settings.json()["allow_external_audio"] is False
        assert settings.json()["resources"]["resident_mode"] in {"single", "multi"}
        assert models.json()["models"][0]["id"] == "oida-deterministic-v1"
        moss_models = client.get("/reasoning/models?provider_id=oida_moss").json()["models"]
        assert {item["id"] for item in moss_models} >= {"instruct", "thinking"}
        local_audio_models = client.get("/reasoning/models?provider_id=local_audio").json()["models"]
        assert {item["id"] for item in local_audio_models} >= {
            "OpenMOSS-Team/MOSS-Transcribe-Diarize",
            "mispeech/midashenglm-0.6b-fp32",
            "XiaomiMiMo/MiMo-Audio-7B-Base",
            "Qwen/Qwen3-Omni-30B-A3B-Instruct",
            "google/gemma-3n-E2B-it",
            "soham97/mellow",
        }


def test_memory_remember_normalizes_malformed_optional_event_fields() -> None:
    with _Client() as client:
        response = client.post(
            "/memory/remember",
            json={
                "event": {
                    "id": "evt_malformed_optional",
                    "memory": "invalid",
                    "routes": [42],
                    "tags": "not-a-list",
                },
                "tags": ["requested"],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["event"]["memory"]["saved_trace_id"] == body["trace"]["id"]
        assert body["trace"]["tags"] == ["requested"]


def test_report_endpoint_uses_configured_chunk_budget() -> None:
    with patch.dict(os.environ, {"OIDA_MOSS_CHUNK_SECONDS": "12"}, clear=False):
        with _Client() as client:
            with (
                patch("oida.server.report", return_value=object()) as report_mock,
                patch("oida.server.report_to_dict", return_value={"ok": True}),
            ):
                response = client.post("/report", json={"path": "/unused.wav", "profile": "audit"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert report_mock.call_args.kwargs == {"chunk_seconds": 12.0, "overlap_seconds": 5.0}


def test_destructive_cleanup_limits_reject_negative_and_nonfinite_values() -> None:
    with _Client() as client:
        audio_root = Path(os.environ["OIDA_AUDIO_DIR"])
        audio_root.mkdir(parents=True, exist_ok=True)
        recording = audio_root / "keep.wav"
        recording.write_bytes(b"RIFF")

        negative = client.post(
            "/raw-audio/wipe",
            json={"delete_all": False, "max_age_hours": -1},
        )
        nonfinite = client.post(
            "/raw-audio/wipe",
            content='{"delete_all":false,"max_age_hours":NaN}',
            headers={"Content-Type": "application/json"},
        )
        infinite_ring = client.post(
            "/live/start",
            content='{"ring_seconds":Infinity}',
            headers={"Content-Type": "application/json"},
        )
        empty_wipe = client.post("/raw-audio/wipe", json={})
        boolean_limit = client.post(
            "/raw-audio/wipe",
            json={"delete_all": False, "max_files": False},
        )
        valid_infinity_word = client.post(
            "/memory/remember",
            json={"event": {"id": "evt_infinity_word", "aggregate": {"title": "Infinity"}}},
        )
        negative_thinking_budget = client.post(
            "/qa",
            json={"path": "/unused.wav", "question": "What is audible?", "thinking_budget": -1},
        )

        assert negative.status_code == 422
        assert nonfinite.status_code == 400
        assert infinite_ring.status_code == 400
        assert empty_wipe.status_code == 200
        assert empty_wipe.json()["deleted_count"] == 0
        assert boolean_limit.status_code == 422
        assert valid_infinity_word.status_code == 200
        assert negative_thinking_budget.status_code == 422
        assert recording.exists()


def test_engine_unavailable_is_reported_as_service_unavailable() -> None:
    with _Client() as client:
        audio = Path(os.environ["OIDA_AUDIO_DIR"]) / "unavailable.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF")
        with patch(
            "oida.engine_stub.StubMossEngine.generate",
            side_effect=EngineUnavailable("configured runtime is unavailable"),
        ):
            response = client.post(
                "/qa",
                json={"path": str(audio), "question": "What is audible?"},
            )

        assert response.status_code == 503
        assert response.json() == {"detail": "configured runtime is unavailable"}


def test_persisted_perception_roles_are_applied_during_daemon_restart() -> None:
    class FakeEngine:
        def __init__(self) -> None:
            self.assignments: dict[str, str] = {}

        def set_model(self, model_kind: str, model_path: str) -> None:
            self.assignments[model_kind] = model_path

        def runtime_status(self) -> dict:
            return {"assignments": dict(self.assignments), "loaded_models": []}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        environment = {
            "OIDA_DATA_DIR": str(root / "data"),
            "OIDA_AUDIO_DIR": str(root / "audio"),
            "AKOUSMATA_PATH": str(root / "akousmata"),
            "AKOUSMATA_WATCHER": "0",
            "OIDA_MOSS_PREWARM": "0",
        }
        with patch.dict(os.environ, environment, clear=False):
            store = ReasoningSettingsStore(root / "data" / "settings" / "reasoning.json")
            settings = ReasoningSettings()
            roles = dict(settings.roles)
            roles[ModelRole.FAST_PERCEPTION] = RoleAssignment(
                provider_id="oida_moss",
                model_id="custom-fast",
            )
            store.save(settings.model_copy(update={"roles": roles}))
            engine = FakeEngine()
            with (
                patch("oida.server.build_engine", return_value=engine),
                patch(
                    "oida.server.scan_moss_models",
                    return_value=[
                        {
                            "name": "custom-fast",
                            "path": "/models/custom-fast",
                            "kind_hint": "instruct",
                            "description": "fixture",
                            "size_gb": 1.0,
                        }
                    ],
                ),
            ):
                client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
                try:
                    status = client.get("/health")
                    settings_response = client.get("/reasoning/settings")
                finally:
                    client.close()

        assert status.status_code == 200
        assert engine.assignments["instruct"] == "/models/custom-fast"
        assert status.json()["engine"]["instruct_model"] == "/models/custom-fast"
        assert settings_response.json()["application_notes"] == []


def test_conversation_v02_sync_stream_comparison_list_get_and_delete() -> None:
    with _Client() as client:
        comparison = client.post(
            "/conversation/ask",
            json={"event": _event("evt_compare", "Fan"), "question": "What is this?"},
        )
        assert comparison.status_code == 200

        response = client.post(
            "/conversation/ask",
            json={
                "event": _event("evt_primary"),
                "question": "How long is it?",
                "comparison_event_ids": ["evt_compare"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "0.2"
        assert "2.50 seconds" in body["turn"]["answer"]
        assert body["turn"]["audit"]["comparison_event_ids"] == ["evt_compare"]
        assert body["turn"]["answer_blocks"][0]["evidence_refs"]

        streamed = client.post(
            "/conversation/ask/stream",
            json={
                "event": _event("evt_stream"),
                "question": "What happened?",
                "provider_id": "local_structured",
            },
        )
        assert streamed.status_code == 200
        assert "text/event-stream" in streamed.headers["content-type"]
        assert "event: started" in streamed.text
        assert "event: completed" in streamed.text

        conversation_id = body["conversation_id"]
        listed = client.get("/conversation?event_id=evt_primary").json()
        fetched = client.get(f"/conversation/{conversation_id}").json()
        assert listed["count"] == 1
        assert fetched["conversation"]["anchor_event_id"] == "evt_primary"

        mismatch = client.post(
            "/conversation/ask",
            json={
                "event": _event("evt_other"),
                "conversation_id": conversation_id,
                "question": "Replace the anchor?",
            },
        )
        assert mismatch.status_code == 409
        assert "anchored" in mismatch.json()["detail"]

        forged = client.post(
            "/conversation/ask",
            json={
                "event": {**_event("evt_primary"), "aggregate": {"short_summary": "FORGED"}},
                "conversation_id": conversation_id,
                "question": "Use the replacement?",
            },
        )
        assert forged.status_code == 409
        assert "immutable" in forged.json()["detail"]

        duplicate = client.post(
            "/conversation/ask",
            json={
                "event": _event("evt_primary"),
                "conversation_id": conversation_id,
                "question": "Keep the same anchor?",
            },
        )
        assert duplicate.status_code == 200
        assert "FORGED" not in json.dumps(duplicate.json())

        deleted = client.delete(f"/conversation/{conversation_id}")
        assert deleted.json()["deleted"] is True
        assert client.get(f"/conversation/{conversation_id}").status_code == 404


def test_new_active_covenant_filters_an_existing_conversation_without_replacing_its_event() -> None:
    event = _event("evt_governed")
    event["aggregate"]["short_summary"] = "A speaker says launch at dawn."
    event["routes"] = [
        {
            "structured": {
                "claim_summary": {
                    "heard": [
                        {
                            "statement": "The speaker says launch at dawn.",
                            "source": "transcript",
                            "speech_content": True,
                        }
                    ]
                }
            }
        }
    ]
    with _Client() as client:
        comparison = _event("evt_governed_comparison", "Comparison")
        comparison["aggregate"]["short_summary"] = "A speaker says comparison secret 9876."
        comparison["routes"] = [
            {
                "structured": {
                    "claim_summary": {
                        "heard": [
                            {
                                "statement": "The speaker says comparison secret 9876.",
                                "source": "transcript",
                                "speech_content": True,
                            }
                        ]
                    }
                }
            }
        ]
        stored_comparison = client.post(
            "/conversation/ask",
            json={"event": comparison, "question": "What did you hear?", "include_transcript": True},
        )
        assert stored_comparison.status_code == 200

        first = client.post(
            "/conversation/ask",
            json={"event": event, "question": "What did you hear?", "include_transcript": True},
        )
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]

        activated = client.put(
            "/covenant",
            json={
                "name": "quiet-speech",
                "text": "## rules\n- do not reveal: transcript\n",
                "activate": True,
            },
        )
        assert activated.status_code == 200

        governed = client.post(
            "/conversation/ask",
            json={
                "event_id": event["id"],
                "conversation_id": conversation_id,
                "question": "What did you hear?",
                "include_transcript": True,
                "comparison_event_ids": [comparison["id"]],
            },
        )
        assert governed.status_code == 200, governed.text
        assert "launch at dawn" not in json.dumps(governed.json()).lower()
        assert "comparison secret 9876" not in json.dumps(governed.json()).lower()

        streamed = client.post(
            "/conversation/ask/stream",
            json={
                "event_id": event["id"],
                "conversation_id": conversation_id,
                "question": "Compare these listenings.",
                "include_transcript": True,
                "comparison_event_ids": [comparison["id"]],
            },
        )
        assert streamed.status_code == 200
        assert "launch at dawn" not in streamed.text.lower()
        assert "comparison secret 9876" not in streamed.text.lower()

        settings = client.get("/reasoning/settings").json()
        settings["enabled_provider_ids"].append("codex")
        settings["active_provider_id"] = "codex"
        settings["role_assignments"]["conversation"] = {
            "provider_id": "codex",
            "model_id": "gpt-test",
        }
        assert client.put("/reasoning/settings", json=settings).status_code == 200
        prepared = client.post(
            "/conversation/prepare",
            json={
                "event_id": event["id"],
                "conversation_id": conversation_id,
                "question": "Compare these listenings.",
                "include_transcript": True,
                "comparison_event_ids": [comparison["id"]],
            },
        )
        assert prepared.status_code == 200, prepared.text
        serialized_packet = json.dumps(prepared.json()["evidence_packet"]).lower()
        assert "launch at dawn" not in serialized_packet
        assert "comparison secret 9876" not in serialized_packet


def test_host_prepare_commit_uses_enabled_selection_and_one_time_token() -> None:
    with _Client() as client:
        settings = client.get("/reasoning/settings").json()
        settings["enabled_provider_ids"].append("codex")
        settings["active_provider_id"] = "codex"
        settings["active_model_id"] = "gpt-test"
        settings["role_assignments"]["conversation"] = {
            "provider_id": "codex",
            "model_id": "gpt-test",
        }
        saved = client.put("/reasoning/settings", json=settings)
        assert saved.status_code == 200

        prepared = client.post(
            "/conversation/prepare",
            json={"event": _event("evt_host"), "question": "What happened?"},
        )
        assert prepared.status_code == 200
        packet = prepared.json()["evidence_packet"]
        ref = packet["items"][0]["ref"]
        token = prepared.json()["prepare_token"]
        committed = client.post(
            "/conversation/commit",
            json={
                "prepare_token": token,
                "response": {
                    "answer_blocks": [
                        {
                            "kind": "answer",
                            "text": "A host-grounded answer.",
                            "evidence_refs": [ref],
                        }
                    ]
                },
            },
        )
        assert committed.status_code == 200
        assert committed.json()["turn"]["reasoner"]["host_managed"] is True
        assert client.post(
            "/conversation/commit",
            json={"prepare_token": token, "response": {"answer_blocks": []}},
        ).status_code == 400


def test_openrouter_oauth_start_is_pkce_and_loopback() -> None:
    with _Client() as client:
        response = client.post("/reasoning/openrouter/oauth/start")
        assert response.status_code == 200
        body = response.json()
        assert body["authorization_url"].startswith("https://openrouter.ai/auth?")
        assert "code_challenge_method=S256" in body["authorization_url"]
        assert "127.0.0.1" in body["authorization_url"]


def test_settings_reject_incompatible_roles_and_default_ask_uses_saved_provider() -> None:
    with _Client() as client:
        settings = client.get("/reasoning/settings").json()
        settings["role_assignments"]["fast_perception"] = {
            "provider_id": "local_structured",
            "model_id": None,
        }
        rejected = client.put("/reasoning/settings", json=settings)
        assert rejected.status_code == 400
        assert "oida_moss" in rejected.json()["detail"]

        settings = client.get("/reasoning/settings").json()
        settings["enabled_provider_ids"].append("openai_compatible")
        settings["active_provider_id"] = "openai_compatible"
        settings["role_assignments"]["conversation"] = {
            "provider_id": "openai_compatible",
            "model_id": "test-model",
        }
        settings["provider_options"]["openai_compatible"] = {
            "base_url": "http://127.0.0.1:9/v1",
            "default_model": "test-model",
            "supports_json_schema": True,
        }
        assert client.put("/reasoning/settings", json=settings).status_code == 200
        answer = client.post(
            "/conversation/ask",
            json={"event": _event("evt_configured"), "question": "What happened?"},
        )
        assert answer.status_code == 200
        fallback = answer.json()["turn"]["fallback"]
        assert fallback["used"] is True
        assert fallback["from_provider_id"] == "openai_compatible"


def test_global_incognito_forces_local_nonpersistent_reasoning() -> None:
    with _Client() as client:
        settings = client.get("/reasoning/settings").json()
        settings["enabled_provider_ids"].append("codex")
        settings["active_provider_id"] = "codex"
        settings["role_assignments"]["conversation"] = {
            "provider_id": "codex",
            "model_id": "gpt-test",
        }
        assert client.put("/reasoning/settings", json=settings).status_code == 200
        configured = client.post("/background/config", json={"updates": {"incognito": True}})
        assert configured.status_code == 200

        response = client.post(
            "/conversation/ask",
            json={
                "event": _event("evt_incognito_global"),
                "question": "What happened?",
                "provider_id": "codex",
                "include_transcript": True,
                "include_memory_content": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["persistent"] is False
        assert body["turn"]["reasoner"]["provider_id"] == "local_structured"
        assert body["turn"]["audit"]["transcript_included"] is False
        assert body["turn"]["audit"]["memory_content_included"] is False
        assert client.get("/conversation?event_id=evt_incognito_global").json()["count"] == 0
