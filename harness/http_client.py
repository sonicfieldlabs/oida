from __future__ import annotations

import json
from urllib import request


def get_json(server: str, endpoint: str, timeout: int = 120) -> dict[str, object]:
    url = f"{server.rstrip('/')}/{endpoint.lstrip('/')}"
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(server: str, endpoint: str, payload: dict[str, object], timeout: int = 600) -> dict[str, object]:
    url = f"{server.rstrip('/')}/{endpoint.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
