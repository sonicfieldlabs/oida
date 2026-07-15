from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from oida.config import data_dir
from oida.reasoning.contracts import (
    REASONING_SETTINGS_CONTRACT,
    ReasoningSettings,
    default_provider_settings,
    default_role_assignments,
)
from oida.storage import write_json_atomic


@dataclass(frozen=True)
class ReasoningSettingsStore:
    """Atomic persistence for non-secret reasoning configuration.

    Credentials are intentionally absent from ``ReasoningSettings``. Providers
    may persist only an opaque ``credential_ref`` here; the corresponding value
    belongs in a ``SecretStore``.
    """

    path: Path = field(default_factory=lambda: data_dir() / "settings" / "reasoning.json")

    def load(self) -> ReasoningSettings:
        if not self.path.exists():
            return ReasoningSettings()
        try:
            payload = migrate_reasoning_settings(json.loads(self.path.read_text(encoding="utf-8")))
            return ReasoningSettings.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"invalid reasoning settings: {self.path}") from exc

    def save(self, settings: ReasoningSettings | dict[str, Any]) -> ReasoningSettings:
        payload = (
            settings.model_dump(mode="python")
            if isinstance(settings, ReasoningSettings)
            else migrate_reasoning_settings(settings)
        )
        validated = ReasoningSettings.model_validate(payload)
        write_json_atomic(self.path, validated.model_dump(mode="json"))
        return validated

    def patch(self, updates: dict[str, Any]) -> ReasoningSettings:
        current = self.load().model_dump(mode="python")
        merged = _deep_merge(current, updates)
        return self.save(ReasoningSettings.model_validate(merged))


def _deep_merge(current: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    result = dict(current)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def migrate_reasoning_settings(payload: Any) -> dict[str, Any]:
    """Upgrade persisted v0.1 settings without losing user provider/profile choices."""

    if not isinstance(payload, dict):
        raise ValueError("reasoning settings must be a JSON object")
    result = dict(payload)
    contract = result.get("contract")
    if contract not in {None, "oida/reasoning-settings/v0.1", REASONING_SETTINGS_CONTRACT}:
        raise ValueError(f"unsupported reasoning settings contract: {contract}")

    default_providers = {
        key: value.model_dump(mode="json")
        for key, value in default_provider_settings().items()
    }
    stored_providers = result.get("providers") if isinstance(result.get("providers"), dict) else {}
    result["providers"] = {**default_providers, **stored_providers}

    default_roles = {
        role.value: assignment.model_dump(mode="json")
        for role, assignment in default_role_assignments().items()
    }
    stored_roles = result.get("roles") if isinstance(result.get("roles"), dict) else {}
    result["roles"] = {**default_roles, **stored_roles}
    result["contract"] = REASONING_SETTINGS_CONTRACT
    result.setdefault("allow_external_audio", False)
    return result
