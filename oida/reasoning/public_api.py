from __future__ import annotations

from typing import Any

from oida.reasoning.contracts import (
    ModelRole,
    ProviderKind,
    ProviderLocality,
    ProviderSettings,
    ReasoningProfile,
    ReasoningSettings,
    RoleAssignment,
)
from oida.reasoning.providers.base import endpoint_locality


PUBLIC_SETTINGS_VERSION = "oida/reasoning-settings/v0.2"

_PUBLIC_PROVIDER_FIELDS: dict[str, set[str]] = {
    "ollama": {"base_url", "default_model"},
    "openai_compatible": {"base_url", "default_model", "supports_json_schema", "audio_capable", "audio_transport"},
    "openrouter": {"default_model", "app_url", "app_title", "audio_capable", "audio_transport"},
    "local_audio": {
        "base_url",
        "default_model",
        "supports_json_schema",
        "audio_capable",
        "audio_transport",
        "sglang_thinking_processor",
    },
    "google": {"base_url", "default_model"},
    "alibaba": {"base_url", "default_model", "audio_capable", "audio_transport", "stream"},
    "nvidia": {
        "base_url",
        "default_model",
        "audio_capable",
        "audio_transport",
        "supports_json_schema",
    },
    "opencode": {"base_url", "default_model", "managed", "username"},
    "codex": {"default_model"},
    "claude": {"default_model"},
    "hermes": {"default_model"},
    "openclaw": {"default_model"},
}


def settings_to_public(settings: ReasoningSettings, *, incognito: bool = False) -> dict[str, Any]:
    conversation = settings.roles[ModelRole.CONVERSATION]
    provider_options: dict[str, dict[str, Any]] = {}
    for provider_id, provider in settings.providers.items():
        allowed = _PUBLIC_PROVIDER_FIELDS.get(provider_id, set())
        options = {key: value for key, value in provider.options.items() if key in allowed}
        if provider.base_url and "base_url" in allowed:
            options["base_url"] = provider.base_url
        if provider.default_model and "default_model" in allowed:
            options["default_model"] = provider.default_model
        if options:
            provider_options[provider_id] = options
    return {
        "version": PUBLIC_SETTINGS_VERSION,
        "active_provider_id": conversation.provider_id,
        "active_model_id": conversation.model_id,
        "active_profile_id": settings.active_profile_id,
        "enabled_provider_ids": [
            provider_id for provider_id, provider in settings.providers.items() if provider.enabled
        ],
        "role_assignments": {
            role.value: assignment.model_dump(mode="json")
            for role, assignment in settings.roles.items()
        },
        "profiles": [profile.model_dump(mode="json") for profile in settings.profiles.values()],
        "provider_options": provider_options,
        "include_transcript": settings.include_transcript,
        "include_memory_content": settings.include_memory_content,
        "allow_targeted_relisten": settings.allow_targeted_relisten,
        "allow_external_audio": settings.allow_external_audio,
        "incognito": bool(incognito),
    }


def public_to_settings(payload: dict[str, Any], current: ReasoningSettings) -> ReasoningSettings:
    if not isinstance(payload, dict):
        raise ValueError("reasoning settings must be an object")

    profiles = _profiles(payload.get("profiles"), current)
    active_profile_id = str(payload.get("active_profile_id") or current.active_profile_id)
    if active_profile_id not in profiles:
        raise ValueError("active_profile_id must reference a configured profile")

    roles = _roles(payload, current)
    providers = _providers(payload, current)
    for role, assignment in roles.items():
        if assignment.provider_id not in providers:
            raise ValueError(
                f"role {role.value!r} references unknown provider {assignment.provider_id!r}"
            )

    return ReasoningSettings(
        providers=providers,
        roles=roles,
        profiles=profiles,
        active_profile_id=active_profile_id,
        include_transcript=bool(payload.get("include_transcript", current.include_transcript)),
        include_memory_content=bool(
            payload.get("include_memory_content", current.include_memory_content)
        ),
        allow_targeted_relisten=bool(
            payload.get("allow_targeted_relisten", current.allow_targeted_relisten)
        ),
        allow_external_audio=bool(
            payload.get("allow_external_audio", current.allow_external_audio)
        ),
    )


def _profiles(value: Any, current: ReasoningSettings) -> dict[str, ReasoningProfile]:
    if value is None:
        return dict(current.profiles)
    if isinstance(value, dict):
        raw_profiles = [dict(item or {}, id=profile_id) for profile_id, item in value.items()]
    elif isinstance(value, list):
        raw_profiles = value
    else:
        raise ValueError("profiles must be an array or object")
    profiles: dict[str, ReasoningProfile] = {}
    for raw in raw_profiles:
        profile = ReasoningProfile.model_validate(raw)
        if profile.id in profiles:
            raise ValueError(f"duplicate reasoning profile: {profile.id}")
        profiles[profile.id] = profile
    if not profiles:
        raise ValueError("at least one reasoning profile is required")
    return profiles


def _roles(payload: dict[str, Any], current: ReasoningSettings) -> dict[ModelRole, RoleAssignment]:
    raw = payload.get("role_assignments", payload.get("roles"))
    roles = dict(current.roles)
    if raw is not None:
        if not isinstance(raw, dict):
            raise ValueError("role_assignments must be an object")
        for key, value in raw.items():
            try:
                role = ModelRole(str(key))
            except ValueError as exc:
                raise ValueError(f"unknown model role: {key}") from exc
            if isinstance(value, str):
                value = {"provider_id": value}
            roles[role] = RoleAssignment.model_validate(value)
    conversation = roles[ModelRole.CONVERSATION]
    if payload.get("active_provider_id"):
        conversation = conversation.model_copy(
            update={"provider_id": str(payload["active_provider_id"])}
        )
    if "active_model_id" in payload:
        value = payload.get("active_model_id")
        conversation = conversation.model_copy(
            update={"model_id": str(value) if value else None}
        )
    roles[ModelRole.CONVERSATION] = conversation
    return roles


def _providers(payload: dict[str, Any], current: ReasoningSettings) -> dict[str, ProviderSettings]:
    providers = dict(current.providers)
    enabled_value = payload.get("enabled_provider_ids")
    if enabled_value is not None:
        if not isinstance(enabled_value, list):
            raise ValueError("enabled_provider_ids must be an array")
        enabled = {str(value) for value in enabled_value}
        unknown = enabled - set(providers)
        if unknown:
            raise ValueError("unknown reasoning provider(s): " + ", ".join(sorted(unknown)))
        enabled.update({"local_structured", "oida_moss"})
        providers = {
            provider_id: provider.model_copy(update={"enabled": provider_id in enabled})
            for provider_id, provider in providers.items()
        }

    options = payload.get("provider_options")
    if options is not None:
        if not isinstance(options, dict):
            raise ValueError("provider_options must be an object")
        for provider_id, raw in options.items():
            if provider_id not in providers:
                raise ValueError(f"unknown reasoning provider: {provider_id}")
            if not isinstance(raw, dict):
                raise ValueError(f"provider_options.{provider_id} must be an object")
            existing = providers[provider_id]
            values = dict(raw)
            if any(
                part in str(key).lower().replace("-", "_")
                for key in values
                for part in ("api_key", "apikey", "token", "secret", "password", "authorization", "credential")
            ):
                raise ValueError(
                    f"secret-bearing provider option is not allowed for {provider_id}"
                )
            allowed = _PUBLIC_PROVIDER_FIELDS.get(provider_id, set())
            unknown = set(values) - allowed
            if unknown:
                raise ValueError(
                    f"unsupported public option(s) for {provider_id}: "
                    + ", ".join(sorted(str(value) for value in unknown))
                )
            base_url = values.pop("base_url", existing.base_url)
            default_model = values.pop("default_model", existing.default_model)
            if provider_id == "opencode" and base_url:
                if endpoint_locality(str(base_url)) != "local":
                    raise ValueError("OpenCode attached endpoints must be loopback-local")
                managed = values.get("managed", existing.options.get("managed", False))
                if bool(managed):
                    raise ValueError("OpenCode can be managed or attached, not both")
            locality = _locality(existing.kind, base_url, existing.locality)
            providers[provider_id] = ProviderSettings(
                kind=existing.kind,
                enabled=existing.enabled,
                locality=locality,
                base_url=str(base_url).strip() if base_url else None,
                default_model=str(default_model).strip() if default_model else None,
                credential_ref=existing.credential_ref,
                options={**existing.options, **values},
            )
    return providers


def _locality(
    kind: ProviderKind,
    base_url: Any,
    fallback: ProviderLocality,
) -> ProviderLocality:
    if kind in {ProviderKind.OPENROUTER, ProviderKind.GOOGLE}:
        return ProviderLocality.EXTERNAL
    if kind in {ProviderKind.OLLAMA, ProviderKind.OPENAI_COMPATIBLE} and base_url:
        try:
            return ProviderLocality(endpoint_locality(str(base_url)))
        except ValueError:
            return ProviderLocality.UNKNOWN
    return fallback
