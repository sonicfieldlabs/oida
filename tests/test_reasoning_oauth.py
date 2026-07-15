from __future__ import annotations

import urllib.parse

import pytest

from oida.reasoning.oauth import OpenRouterOAuth
from oida.reasoning.providers.base import JsonResponse
from oida.reasoning.secrets import SecretStore


class _Secrets(SecretStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        return self.values.get((provider_id, name))

    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        self.values[(provider_id, name)] = value

    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        return self.values.pop((provider_id, name), None) is not None


class _Transport:
    def __init__(self) -> None:
        self.payload = None

    def request(self, method, url, *, payload=None, headers=None, timeout=30):
        self.payload = payload
        return JsonResponse(status=200, data={"key": "or-secret-value"}, headers={})


def test_openrouter_pkce_is_loopback_single_use_and_stores_only_result() -> None:
    secrets = _Secrets()
    transport = _Transport()
    oauth = OpenRouterOAuth(secrets, transport=transport)

    started = oauth.start("http://127.0.0.1:8765/reasoning/providers/openrouter/oauth/callback")
    authorization = urllib.parse.urlsplit(started["authorization_url"])
    params = urllib.parse.parse_qs(authorization.query)
    assert authorization.netloc == "openrouter.ai"
    assert params["code_challenge_method"] == ["S256"]
    assert "code_challenge" in params
    callback_params = urllib.parse.parse_qs(urllib.parse.urlsplit(params["callback_url"][0]).query)
    assert callback_params["state"] == [started["state"]]

    result = oauth.exchange(code="authorization-code", state=started["state"])
    assert result["authenticated"] is True
    assert secrets.get("openrouter") == "or-secret-value"
    assert transport.payload["code"] == "authorization-code"
    assert transport.payload["code_challenge_method"] == "S256"
    assert "or-secret-value" not in str(started)

    with pytest.raises(ValueError, match="already used"):
        oauth.exchange(code="again", state=started["state"])


def test_openrouter_oauth_rejects_non_loopback_callback() -> None:
    with pytest.raises(ValueError, match="loopback"):
        OpenRouterOAuth(_Secrets()).start("https://example.com/callback")
