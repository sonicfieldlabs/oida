"""Decision provider resolution over Oída's own reasoning registry."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from oida.routing.providers import RulesDecisionProvider, TextDecisionProvider
from oida.routing.typesafe import TypesafeDecisionProvider

TYPESAFE_BASE_URL = "https://api.typesafe.ai"
JEV_QUALIFICATION_PROTOCOL = "telar/jev-protocol/v1"


def typesafe_qualification(workspace) -> tuple[bool, bool]:
    """Configured and current protocol-qualified are different states."""
    secret_store = getattr(workspace.reasoning, "secret_store", None)
    try:
        key = secret_store.get("typesafe", "api_key") if secret_store else None
    except Exception:
        key = None
    if not key:
        return False, False
    receipt = workspace.journal.get("routing_qualification", "typesafe") or {}
    fingerprint = hashlib.sha256(b"telar-jev-protocol-v1:" + key.encode()).hexdigest()
    try:
        expires = datetime.fromisoformat(receipt.get("expires_at", ""))
        fresh = expires.tzinfo is not None and expires > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        fresh = False
    return True, bool(
        receipt.get("status") == "protocol_qualified"
        and receipt.get("protocol") == JEV_QUALIFICATION_PROTOCOL
        and receipt.get("endpoint") == TYPESAFE_BASE_URL
        and receipt.get("model_id") == "jev-1.13.0"
        and receipt.get("credential_fingerprint") == fingerprint
        and fresh
    )


def build_decision_registry(workspace) -> dict:
    """The decision providers available right now.

    ``rules`` is always available. Every registered text reasoning provider is
    visible with its own availability so the operator can see why a choice is
    disabled; disabled or unreachable providers are never invoked. The
    TypeSafe adapter appears only when a credential is stored, and is
    selectable under Decision only — never for language reasoning.
    """
    providers: dict[str, object] = {"rules": RulesDecisionProvider()}
    settings = workspace.settings.load()
    registry = workspace.reasoning.registry_factory(settings)
    for provider_id in registry.ids():
        adapter = registry.get(provider_id)
        if adapter is None or provider_id == "oida_moss":
            continue
        descriptor = registry.probe(provider_id)
        if not descriptor.enabled or not descriptor.available:
            continue
        provider = TextDecisionProvider(provider_id, registry)
        provider.locality = getattr(descriptor.locality, "value", descriptor.locality)
        providers[provider_id] = provider
    secret_store = getattr(workspace.reasoning, "secret_store", None)
    if secret_store is not None:
        configured, qualified = typesafe_qualification(workspace)
        if configured and qualified:
            providers["typesafe"] = TypesafeDecisionProvider(
                TYPESAFE_BASE_URL,
                lambda: secret_store.get("typesafe", "api_key"),
            )
    return providers


def decision_options(workspace) -> list[dict]:
    """Descriptors for the Routing/Models UI, truthful about availability."""
    options: list[dict] = [
        dict(
            id="rules",
            name="Rules · deterministic policy",
            kind="rules",
            enabled=True,
            available=True,
            locality="local",
            detail="Code-owned rubric over the offered candidates; no model call.",
            credential_configured=None,
        )
    ]
    typesafe_configured, typesafe_qualified = typesafe_qualification(workspace)
    options.append(
        dict(
            id="typesafe",
            name="Jev · TypeSafe SystemOne",
            kind="model",
            enabled=True,
            available=typesafe_configured and typesafe_qualified,
            locality="external",
            detail=(
                "Pinned jev-1.13.0 passed the current credential's synthetic protocol checks; domain performance remains unqualified."
                if typesafe_qualified
                else "Credential configured; run the explicit synthetic Jev protocol check before selecting it."
                if typesafe_configured
                else "No credential stored; enter one under Routing."
            ),
            credential_configured=typesafe_configured,
            qualification="protocol_qualified" if typesafe_qualified else "pending",
        )
    )
    settings = workspace.settings.load()
    try:
        registry = workspace.reasoning.registry_factory(settings)
    except Exception:
        return options
    for descriptor in registry.descriptors():
        if descriptor.id == "oida_moss":
            continue
        options.append(
            dict(
                id=descriptor.id,
                name=descriptor.name,
                kind="model",
                enabled=descriptor.enabled,
                available=descriptor.enabled and descriptor.available,
                locality=str(
                    getattr(descriptor.locality, "value", descriptor.locality)
                ),
                detail=descriptor.detail or "",
                credential_configured=None,
            )
        )
    return options
