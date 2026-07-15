from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from oida.reasoning.providers.base import (
    JsonTransport,
    ProviderTransportError,
    UrllibJsonTransport,
    validate_http_url,
)
from oida.reasoning.secrets import SecretStore


OPENROUTER_AUTHORIZE_URL = "https://openrouter.ai/auth"
OPENROUTER_EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"


@dataclass(frozen=True)
class _PendingAuthorization:
    verifier: str
    created_monotonic: float


class OpenRouterOAuth:
    """Small in-memory PKCE coordinator for OpenRouter's localhost flow.

    Only the resulting key crosses into ``SecretStore``.  State and verifier
    are short-lived, single-use values and are intentionally never persisted.
    """

    def __init__(
        self,
        secret_store: SecretStore,
        *,
        transport: JsonTransport | None = None,
        ttl_seconds: float = 600.0,
    ) -> None:
        self.secret_store = secret_store
        self.transport = transport or UrllibJsonTransport()
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self._pending: dict[str, _PendingAuthorization] = {}
        self._lock = threading.RLock()

    def start(self, callback_url: str) -> dict[str, str]:
        callback = _loopback_callback(callback_url)
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        with self._lock:
            self._expire_locked()
            self._pending[state] = _PendingAuthorization(
                verifier=verifier,
                created_monotonic=time.monotonic(),
            )

        parsed = urllib.parse.urlsplit(callback)
        callback_query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        callback_query.append(("state", state))
        stateful_callback = urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(callback_query),
                parsed.fragment,
            )
        )
        query = urllib.parse.urlencode(
            {
                "callback_url": stateful_callback,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return {
            "provider_id": "openrouter",
            "authorization_url": f"{OPENROUTER_AUTHORIZE_URL}?{query}",
            "state": state,
        }

    def exchange(self, *, code: str, state: str) -> dict[str, Any]:
        normalized_code = str(code or "").strip()
        normalized_state = str(state or "").strip()
        if not normalized_code or not normalized_state:
            raise ValueError("OpenRouter OAuth callback requires code and state")
        with self._lock:
            self._expire_locked()
            pending = self._pending.pop(normalized_state, None)
        if pending is None:
            raise ValueError("OpenRouter OAuth state is invalid, expired, or already used")

        try:
            response = self.transport.request(
                "POST",
                OPENROUTER_EXCHANGE_URL,
                payload={
                    "code": normalized_code,
                    "code_verifier": pending.verifier,
                    "code_challenge_method": "S256",
                },
                timeout=30,
            )
        except ProviderTransportError:
            # Do not restore the state: a callback is deliberately single-use.
            raise
        payload = response.data if isinstance(response.data, dict) else {}
        key = payload.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ProviderTransportError("OpenRouter OAuth exchange returned no API key")
        self.secret_store.set("openrouter", key.strip())
        return {
            "provider_id": "openrouter",
            "authenticated": True,
            "stored_securely": True,
        }

    def _expire_locked(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        self._pending = {
            state: pending
            for state, pending in self._pending.items()
            if pending.created_monotonic >= cutoff
        }


def _loopback_callback(url: str) -> str:
    parsed = validate_http_url(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OAuth callback must use a loopback host")
    return urllib.parse.urlunsplit(parsed)
