from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from oida.engine_base import EngineResult, MossEngine
from oida.reasoning.audio_router import RoutedAudioEngine
from oida.reasoning.contracts import ModelRole, ReasoningSettings, RoleAssignment
from oida.reasoning.model_catalog import find_model_spec
from oida.reasoning.providers.base import JsonResponse
from oida.reasoning.resources import resource_assessment
from oida.reasoning.secrets import SecretStore
from oida.reasoning.settings import ReasoningSettingsStore, migrate_reasoning_settings
from oida.recipes import (
    INSTRUCT_CAPTION,
    MUSIC_REASONING,
    THINKING_REASONING,
    TRANSCRIPTION_EXTRACTION,
)


class FakeEngine(MossEngine):
    profile = "fake-local"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.assignments: dict[str, str] = {}

    def generate(self, audio_path, prompt, settings, thinking_budget=None):
        self.calls.append(
            {
                "audio_path": audio_path,
                "prompt": prompt,
                "settings": settings,
                "thinking_budget": thinking_budget,
            }
        )
        return EngineResult(
            text="local observation",
            model="local/model",
            profile=self.profile,
            settings=settings,
        )

    def set_model(self, model_kind: str, model_id: str) -> None:
        self.assignments[model_kind] = model_id

    def runtime_status(self) -> dict[str, object]:
        return {"assignments": dict(self.assignments), "loaded_models": []}


class DictSecrets(SecretStore):
    def __init__(self, values: dict[tuple[str, str], str] | None = None) -> None:
        self.values = values or {}

    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        return self.values.get((provider_id, name))

    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        self.values[(provider_id, name)] = value

    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        return self.values.pop((provider_id, name), None) is not None


class FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return JsonResponse(status=200, data=self.response, headers={})


class SequenceTransport:
    def __init__(self, responses: list[JsonResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected transport request: {method} {url}")
        return self.responses.pop(0)


def _audio(root: Path, *, size: int = 36) -> Path:
    path = root / "private.wav"
    path.write_bytes(b"RIFF" + b"\x00" * max(0, size - 4))
    return path


def _router(
    root: Path,
    settings: ReasoningSettings,
    *,
    transport: Any,
    secrets: DictSecrets | None = None,
    binary_uploader: Any | None = None,
) -> tuple[RoutedAudioEngine, FakeEngine]:
    store = ReasoningSettingsStore(root / "reasoning.json")
    store.save(settings)
    local = FakeEngine()
    return (
        RoutedAudioEngine(
            local,
            settings_store=store,
            secret_store=secrets or DictSecrets(),
            transport=transport,  # type: ignore[arg-type]
            binary_uploader=binary_uploader,
        ),
        local,
    )


def test_v01_settings_migrate_to_six_roles_and_new_provider_presets() -> None:
    migrated = migrate_reasoning_settings(
        {
            "contract": "oida/reasoning-settings/v0.1",
            "providers": {
                "local_structured": {"kind": "local_structured", "enabled": True, "locality": "local"},
                "oida_moss": {"kind": "openai_compatible", "enabled": True, "locality": "local"},
            },
            "roles": {
                "fast_perception": {"provider_id": "oida_moss", "model_id": "instruct"},
                "deep_perception": {"provider_id": "oida_moss", "model_id": "thinking"},
                "conversation": {"provider_id": "local_structured", "model_id": None},
                "targeted_relisten": {"provider_id": "oida_moss", "model_id": "thinking"},
            },
        }
    )
    settings = ReasoningSettings.model_validate(migrated)
    assert set(settings.roles) == set(ModelRole)
    assert settings.roles[ModelRole.TRANSCRIPTION].model_id == "instruct"
    assert settings.roles[ModelRole.MUSIC_ANALYSIS].model_id == "thinking"
    assert {"local_audio", "google", "alibaba", "nvidia"} <= set(settings.providers)
    assert settings.allow_external_audio is False


def test_local_audio_host_receives_audio_without_external_opt_in_or_path_leak() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings()
        providers = dict(settings.providers)
        providers["local_audio"] = providers["local_audio"].model_copy(update={"enabled": True})
        roles = dict(settings.roles)
        roles[ModelRole.FAST_PERCEPTION] = RoleAssignment(
            provider_id="local_audio",
            model_id="mispeech/midashenglm-0.6b-fp32",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport(
            {"model": "midasheng", "choices": [{"message": {"content": "a bell"}}]}
        )
        router, local = _router(root, settings, transport=transport)
        path = _audio(root)

        result = router.generate(str(path), "Describe the sound.", INSTRUCT_CAPTION)

        assert result.text == "a bell"
        assert result.profile == "local-audio-host"
        assert not local.calls
        payload = transport.calls[0]["payload"]
        serialized = str(payload)
        assert str(path) not in serialized
        audio_url = payload["messages"][1]["content"][0]["audio_url"]["url"]
        assert audio_url.startswith("data:audio/")


def test_local_audio_thinking_budget_requires_and_sends_the_sglang_processor() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings()
        providers = dict(settings.providers)
        local_audio = providers["local_audio"]
        providers["local_audio"] = local_audio.model_copy(
            update={
                "enabled": True,
                "options": {
                    **local_audio.options,
                    "sglang_thinking_processor": "serialized-processor",
                },
            }
        )
        roles = dict(settings.roles)
        roles[ModelRole.DEEP_PERCEPTION] = RoleAssignment(
            provider_id="local_audio",
            model_id="OpenMOSS-Team/MOSS-Audio-8B-Thinking",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport(
            {
                "model": "moss-audio",
                "choices": [
                    {
                        "message": {
                            "content": "A bell is audible.",
                            "reasoning_content": "private trace",
                        }
                    }
                ],
            }
        )
        router, local = _router(root, settings, transport=transport)

        result = router.generate(
            str(_audio(root)),
            "Describe deeply.",
            THINKING_REASONING,
            thinking_budget=64,
        )

        assert result.text == "A bell is audible."
        assert result.reasoning_trace == "private trace"
        assert not local.calls
        payload = transport.calls[0]["payload"]
        assert payload["separate_reasoning"] is True
        assert payload["custom_logit_processor"] == "serialized-processor"
        assert payload["custom_params"] == {"thinking_budget": 64}


def test_local_audio_thinking_budget_without_processor_falls_back_before_network() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings()
        providers = dict(settings.providers)
        providers["local_audio"] = providers["local_audio"].model_copy(
            update={"enabled": True}
        )
        roles = dict(settings.roles)
        roles[ModelRole.DEEP_PERCEPTION] = RoleAssignment(
            provider_id="local_audio",
            model_id="OpenMOSS-Team/MOSS-Audio-8B-Thinking",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport({})
        router, local = _router(root, settings, transport=transport)

        result = router.generate(
            str(_audio(root)),
            "Describe deeply.",
            THINKING_REASONING,
            thinking_budget=64,
        )

        assert result.text == "local observation"
        assert len(local.calls) == 1
        assert not transport.calls
        assert "sglang_thinking_processor" in str(
            router.runtime_status()["last_audio_routing_warning"]
        )


def test_external_audio_requires_opt_in_and_incognito_still_forces_local() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings()
        providers = dict(settings.providers)
        providers["google"] = providers["google"].model_copy(update={"enabled": True})
        roles = dict(settings.roles)
        roles[ModelRole.FAST_PERCEPTION] = RoleAssignment(
            provider_id="google",
            model_id="gemini-3.5-flash",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport({"candidates": [{"content": {"parts": [{"text": "cloud"}]}}]})
        router, local = _router(
            root,
            settings,
            transport=transport,
            secrets=DictSecrets({("google", "api_key"): "secret"}),
        )
        path = _audio(root)

        blocked = router.generate(str(path), "Describe.", INSTRUCT_CAPTION)
        assert blocked.text == "local observation"
        assert len(local.calls) == 1
        assert not transport.calls

        enabled = settings.model_copy(update={"allow_external_audio": True})
        router.settings_store.save(enabled)
        with router.request_policy(privacy_mode="incognito"):
            incognito = router.generate(str(path), "Describe.", INSTRUCT_CAPTION)
        assert incognito.text == "local observation"
        assert len(local.calls) == 2
        assert not transport.calls


def test_historical_raw_audio_covenant_blocks_external_audio() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings(allow_external_audio=True)
        providers = dict(settings.providers)
        providers["google"] = providers["google"].model_copy(update={"enabled": True})
        roles = dict(settings.roles)
        roles[ModelRole.FAST_PERCEPTION] = RoleAssignment(
            provider_id="google",
            model_id="gemini-3.5-flash",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport({"candidates": [{"content": {"parts": [{"text": "cloud"}]}}]})
        router, local = _router(
            root,
            settings,
            transport=transport,
            secrets=DictSecrets({("google", "api_key"): "secret"}),
        )

        with router.request_policy(
            covenant_block={
                "rules_applied": ["do_not_reveal:raw-audio"],
                "withheld": [{"rule": "do_not_reveal", "subject": "raw-audio"}],
            }
        ):
            result = router.generate(str(_audio(root)), "Describe.", INSTRUCT_CAPTION)

        assert result.text == "local observation"
        assert len(local.calls) == 1
        assert not transport.calls


def test_local_audio_provider_refuses_a_non_loopback_endpoint() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings(allow_external_audio=True)
        providers = dict(settings.providers)
        providers["local_audio"] = providers["local_audio"].model_copy(
            update={"enabled": True, "base_url": "https://audio.example/v1"}
        )
        roles = dict(settings.roles)
        roles[ModelRole.FAST_PERCEPTION] = RoleAssignment(
            provider_id="local_audio",
            model_id="mispeech/midashenglm-0.6b-fp32",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport(
            {"choices": [{"message": {"content": "must not be used"}}]}
        )
        router, local = _router(root, settings, transport=transport)

        result = router.generate(str(_audio(root)), "Describe.", INSTRUCT_CAPTION)

        assert result.text == "local observation"
        assert len(local.calls) == 1
        assert not transport.calls


def test_google_audio_route_runs_after_explicit_opt_in() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings(allow_external_audio=True)
        providers = dict(settings.providers)
        providers["google"] = providers["google"].model_copy(update={"enabled": True})
        roles = dict(settings.roles)
        roles[ModelRole.MUSIC_ANALYSIS] = RoleAssignment(
            provider_id="google",
            model_id="gemini-3.5-flash",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport(
            {"candidates": [{"content": {"parts": [{"text": "layered percussion"}]}}]}
        )
        router, local = _router(
            root,
            settings,
            transport=transport,
            secrets=DictSecrets({("google", "api_key"): "secret"}),
        )

        result = router.generate(str(_audio(root)), "Analyze the music.", MUSIC_REASONING)

        assert result.text == "layered percussion"
        assert result.profile == "google-api"
        assert not local.calls
        assert transport.calls[0]["headers"] == {"x-goog-api-key": "secret"}


def test_alibaba_audio_uses_streaming_text_output_and_a_base64_data_url() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings(allow_external_audio=True)
        providers = dict(settings.providers)
        providers["alibaba"] = providers["alibaba"].model_copy(update={"enabled": True})
        roles = dict(settings.roles)
        roles[ModelRole.MUSIC_ANALYSIS] = RoleAssignment(
            provider_id="alibaba",
            model_id="qwen3.5-omni-flash",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        transport = FakeTransport(
            {"model": "qwen3.5-omni-flash", "choices": [{"message": {"content": "music"}}]}
        )
        router, local = _router(
            root,
            settings,
            transport=transport,
            secrets=DictSecrets({("alibaba", "api_key"): "secret"}),
        )

        result = router.generate(str(_audio(root)), "Analyze the music.", MUSIC_REASONING)

        assert result.text == "music"
        assert not local.calls
        payload = transport.calls[0]["payload"]
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        assert payload["modalities"] == ["text"]
        audio_data = payload["messages"][1]["content"][0]["input_audio"]["data"]
        assert audio_data.startswith("data:audio/wav;base64,")


def test_nvidia_large_audio_is_staged_then_deleted_without_exposing_a_path() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings(allow_external_audio=True)
        providers = dict(settings.providers)
        providers["nvidia"] = providers["nvidia"].model_copy(update={"enabled": True})
        roles = dict(settings.roles)
        roles[ModelRole.MUSIC_ANALYSIS] = RoleAssignment(
            provider_id="nvidia",
            model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        asset_id = "11111111-2222-4333-8444-555555555555"
        transport = SequenceTransport(
            [
                JsonResponse(
                    status=200,
                    data={
                        "assetId": asset_id,
                        "uploadUrl": "https://oida-test.s3.us-west-2.amazonaws.com/upload?signature=opaque",
                    },
                    headers={},
                ),
                JsonResponse(
                    status=200,
                    data={
                        "model": "nemotron",
                        "choices": [{"message": {"content": "staged analysis"}}],
                    },
                    headers={},
                ),
                JsonResponse(status=204, data=None, headers={}),
            ]
        )
        uploads: list[dict[str, Any]] = []

        def upload(url: str, data: bytes, headers: Any, timeout: float) -> None:
            uploads.append({"url": url, "data": data, "headers": dict(headers), "timeout": timeout})

        router, local = _router(
            root,
            settings,
            transport=transport,
            secrets=DictSecrets({("nvidia", "api_key"): "secret"}),
            binary_uploader=upload,
        )
        path = _audio(root, size=200 * 1024)

        result = router.generate(str(path), "Analyze the music.", MUSIC_REASONING)

        assert result.text == "staged analysis"
        assert not local.calls
        assert [call["method"] for call in transport.calls] == ["POST", "POST", "DELETE"]
        assert transport.calls[0]["url"].endswith("/v2/nvcf/assets")
        completion = transport.calls[1]
        assert completion["headers"]["NVCF-INPUT-ASSET-REFERENCES"] == asset_id
        payload = completion["payload"]
        assert payload["messages"][0]["content"].startswith("/no_think\n")
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        asset_url = payload["messages"][1]["content"][0]["audio_url"]["url"]
        assert asset_url == f"data:audio/wav;asset_id,{asset_id}"
        assert str(path) not in str(payload)
        assert transport.calls[2]["url"].endswith(f"/assets/{asset_id}")
        assert len(uploads) == 1
        assert uploads[0]["data"] == path.read_bytes()
        assert "Authorization" not in uploads[0]["headers"]


def test_nvidia_asset_is_deleted_when_binary_upload_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = ReasoningSettings(allow_external_audio=True)
        providers = dict(settings.providers)
        providers["nvidia"] = providers["nvidia"].model_copy(update={"enabled": True})
        roles = dict(settings.roles)
        roles[ModelRole.DEEP_PERCEPTION] = RoleAssignment(
            provider_id="nvidia",
            model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        )
        settings = settings.model_copy(update={"providers": providers, "roles": roles})
        asset_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        transport = SequenceTransport(
            [
                JsonResponse(
                    status=200,
                    data={
                        "assetId": asset_id,
                        "uploadUrl": "https://oida-test.s3.amazonaws.com/upload?signature=opaque",
                    },
                    headers={},
                ),
                JsonResponse(status=204, data=None, headers={}),
            ]
        )

        def fail_upload(url: str, data: bytes, headers: Any, timeout: float) -> None:
            del url, data, headers, timeout
            raise OSError("simulated upload failure")

        router, local = _router(
            root,
            settings,
            transport=transport,
            secrets=DictSecrets({("nvidia", "api_key"): "secret"}),
            binary_uploader=fail_upload,
        )

        result = router.generate(
            str(_audio(root, size=200 * 1024)),
            "Describe deeply.",
            settings=THINKING_REASONING,
        )

        assert result.text == "local observation"
        assert len(local.calls) == 1
        assert [call["method"] for call in transport.calls] == ["POST", "DELETE"]
        assert transport.calls[1]["url"].endswith(f"/assets/{asset_id}")


def test_catalog_and_resource_guard_cover_requested_large_and_small_models() -> None:
    requested = {
        "oida_moss": {
            "OpenMOSS-Team/MOSS-Audio-8B-Thinking",
            "OpenMOSS-Team/MOSS-Audio-8B-Instruct",
            "OpenMOSS-Team/MOSS-Music-8B-Thinking",
            "OpenMOSS-Team/MOSS-Music-8B-Instruct",
        },
        "local_audio": {
            "OpenMOSS-Team/MOSS-Transcribe-Diarize",
            "mispeech/midashenglm-7b-0804-fp32",
            "mispeech/midashenglm-0.6b-fp32",
            "XiaomiMiMo/MiMo-Audio-7B-Base",
            "XiaomiMiMo/MiMo-Audio-Tokenizer",
            "Qwen/Qwen3-Omni-30B-A3B-Instruct",
            "Qwen/Qwen3-Omni-30B-A3B-Thinking",
            "soham97/mellow",
            "google/gemma-3n-E2B-it",
            "google/gemma-3n-E4B-it",
        },
        "google": {"gemini-3.5-flash"},
        "alibaba": {"qwen3.5-omni-plus", "qwen3.5-omni-flash"},
        "nvidia": {"nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"},
        "openrouter": {"nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"},
    }
    for provider_id, model_ids in requested.items():
        for model_id in model_ids:
            assert find_model_spec(provider_id, model_id) is not None

    supported_local = find_model_spec("local_audio", "mispeech/midashenglm-0.6b-fp32")
    assert supported_local is not None
    supported_descriptor = supported_local.descriptor()
    assert supported_descriptor.metadata["catalog"] is True
    assert supported_descriptor.metadata["installed"] is False
    assert supported_descriptor.metadata["available"] is False
    assert supported_descriptor.metadata["source_url"].startswith("https://huggingface.co/")

    settings = ReasoningSettings()
    providers = dict(settings.providers)
    providers["local_audio"] = providers["local_audio"].model_copy(update={"enabled": True})
    roles = dict(settings.roles)
    roles[ModelRole.DEEP_PERCEPTION] = RoleAssignment(
        provider_id="local_audio",
        model_id="Qwen/Qwen3-Omni-30B-A3B-Thinking",
    )
    settings = settings.model_copy(update={"providers": providers, "roles": roles})
    with patch("oida.reasoning.resources.physical_memory_gb", return_value=32.0):
        assessment = resource_assessment(settings)
    assert assessment["level"] == "exceeds"
    assert assessment["estimated_peak_ram_gb"] >= 96
    assert any("above this machine" in warning for warning in assessment["warnings"])


def test_dedicated_recipes_address_transcription_and_music_roles() -> None:
    assert TRANSCRIPTION_EXTRACTION.model_kind == "transcription"
    assert MUSIC_REASONING.model_kind == "music"
