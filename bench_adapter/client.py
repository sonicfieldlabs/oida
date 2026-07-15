from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from oida.reasoning.providers.base import UrllibJsonTransport


MAX_RESPONSE_BYTES = 16 * 1024 * 1024


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
    response = UrllibJsonTransport(max_response_bytes=MAX_RESPONSE_BYTES).request(
        "POST",
        url,
        payload=payload,
        timeout=timeout,
    )
    if not isinstance(response.data, dict):
        raise ValueError("benchmark server returned a non-object JSON response")
    return response.data
