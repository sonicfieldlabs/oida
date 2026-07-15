from __future__ import annotations

import pytest

from oida.reasoning.contracts import ProviderLocality, ReasoningSettings
from oida.reasoning.public_api import public_to_settings, settings_to_public


def test_public_settings_round_trip_matches_dashboard_shape() -> None:
    current = ReasoningSettings()
    payload = settings_to_public(current)
    payload["enabled_provider_ids"].append("codex")
    payload["active_provider_id"] = "codex"
    payload["active_model_id"] = "gpt-5"
    payload["role_assignments"]["conversation"] = {
        "provider_id": "codex",
        "model_id": "gpt-5",
    }
    payload["profiles"][0]["custom_instructions"] = "Be concise."
    payload["include_transcript"] = True

    saved = public_to_settings(payload, current)
    public = settings_to_public(saved, incognito=True)

    assert saved.providers["codex"].enabled is True
    assert saved.roles["conversation"].provider_id == "codex"
    assert saved.roles["conversation"].model_id == "gpt-5"
    assert saved.include_transcript is True
    assert public["incognito"] is True
    assert public["profiles"][0]["custom_instructions"] == "Be concise."


def test_public_settings_recompute_endpoint_locality_and_reject_secrets() -> None:
    current = ReasoningSettings()
    local = public_to_settings(
        {
            "provider_options": {
                "openai_compatible": {"base_url": "http://127.0.0.1:8080/v1"}
            }
        },
        current,
    )
    external = public_to_settings(
        {
            "provider_options": {
                "openai_compatible": {"base_url": "https://models.example/v1"}
            }
        },
        current,
    )
    assert local.providers["openai_compatible"].locality == ProviderLocality.LOCAL
    assert external.providers["openai_compatible"].locality == ProviderLocality.EXTERNAL

    with pytest.raises(ValueError, match="secret-bearing"):
        public_to_settings(
            {"provider_options": {"openai_compatible": {"api_key": "do-not-store"}}},
            current,
        )


def test_public_settings_round_trip_local_sglang_thinking_processor() -> None:
    configured = public_to_settings(
        {
            "provider_options": {
                "local_audio": {
                    "sglang_thinking_processor": "serialized-processor"
                }
            }
        },
        ReasoningSettings(),
    )

    assert (
        configured.providers["local_audio"].options["sglang_thinking_processor"]
        == "serialized-processor"
    )
    assert (
        settings_to_public(configured)["provider_options"]["local_audio"][
            "sglang_thinking_processor"
        ]
        == "serialized-processor"
    )


def test_public_settings_reject_unknown_provider_and_profile() -> None:
    with pytest.raises(ValueError, match="unknown reasoning provider"):
        public_to_settings({"enabled_provider_ids": ["invented"]}, ReasoningSettings())
    with pytest.raises(ValueError, match="active_profile_id"):
        public_to_settings({"active_profile_id": "missing"}, ReasoningSettings())


def test_public_settings_allow_manual_host_model_but_not_executable_or_bad_role() -> None:
    current = ReasoningSettings()
    configured = public_to_settings(
        {"provider_options": {"hermes": {"default_model": "hermes-3-local"}}},
        current,
    )
    assert configured.providers["hermes"].default_model == "hermes-3-local"

    with pytest.raises(ValueError, match="unsupported public option"):
        public_to_settings(
            {"provider_options": {"codex": {"executable": "/tmp/not-allowed"}}},
            current,
        )
    with pytest.raises(ValueError, match="oida_moss"):
        public_to_settings(
            {
                "role_assignments": {
                    "targeted_relisten": {
                        "provider_id": "local_structured",
                        "model_id": None,
                    }
                }
            },
            current,
        )

    with pytest.raises(ValueError, match="loopback"):
        public_to_settings(
            {"provider_options": {"opencode": {"base_url": "https://opencode.example"}}},
            current,
        )
    with pytest.raises(ValueError, match="managed or attached"):
        public_to_settings(
            {
                "provider_options": {
                    "opencode": {
                        "base_url": "http://127.0.0.1:4096",
                        "managed": True,
                    }
                }
            },
            current,
        )
