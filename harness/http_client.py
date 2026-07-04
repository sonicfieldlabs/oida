from __future__ import annotations

import json
import os
from urllib import request


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    token = os.getenv("HMM_AUTH_TOKEN") or os.getenv("AEAR_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_json(server: str, endpoint: str, timeout: int = 120) -> dict[str, object]:
    url = f"{server.rstrip('/')}/{endpoint.lstrip('/')}"
    req = request.Request(url, headers=_headers(), method="GET")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(server: str, endpoint: str, payload: dict[str, object], timeout: int = 600) -> dict[str, object]:
    url = f"{server.rstrip('/')}/{endpoint.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=_headers({"Content-Type": "application/json"}), method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
