from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib import request


def build_direct_benchmark_payload(
    *,
    audio_path: str,
    command_output: dict[str, object],
    model_id: str,
    blind_object_name: str | None = None,
    agent_id: str = "oida-local",
) -> dict[str, object]:
    return {
        "agentId": agent_id,
        "agentType": "local-oida",
        "provider": "local",
        "modelId": model_id,
        "modality": "audio",
        "audioMode": "chat-input-audio",
        "blindObjectName": blind_object_name or Path(audio_path).name,
        "inputType": "audio_file",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "result": command_output,
    }


def post_payload(url: str, payload: dict[str, object], timeout: int = 120) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
