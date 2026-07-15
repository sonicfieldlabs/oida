from __future__ import annotations

import os

from oida.reasoning.providers.base import UrllibJsonTransport, join_url


MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    token = os.getenv("OIDA_AUTH_TOKEN") or os.getenv("HMM_AUTH_TOKEN") or os.getenv("AEAR_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_json(server: str, endpoint: str, timeout: int = 120) -> dict[str, object]:
    return _request_json("GET", server, endpoint, timeout=timeout)


def post_json(server: str, endpoint: str, payload: dict[str, object], timeout: int = 600) -> dict[str, object]:
    return _request_json("POST", server, endpoint, payload=payload, timeout=timeout)


def put_json(server: str, endpoint: str, payload: dict[str, object], timeout: int = 600) -> dict[str, object]:
    return _request_json("PUT", server, endpoint, payload=payload, timeout=timeout)


def _request_json(
    method: str,
    server: str,
    endpoint: str,
    *,
    payload: dict[str, object] | None = None,
    timeout: int,
) -> dict[str, object]:
    response = UrllibJsonTransport(max_response_bytes=MAX_RESPONSE_BYTES).request(
        method,
        join_url(server, endpoint),
        payload=payload,
        headers=_headers(),
        timeout=timeout,
    )
    if not isinstance(response.data, dict):
        raise ValueError("Oída server returned a non-object JSON response")
    return response.data
