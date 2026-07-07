from __future__ import annotations

import json
import os
import sys
from typing import Any

from harness.http_client import get_json, post_json

SERVER = os.getenv("OIDA_SERVER_URL") or os.getenv("HMM_SERVER_URL") or os.getenv("AEAR_SERVER_URL", "http://127.0.0.1:8765")

_SCHEMAS: dict[str, dict[str, Any]] = {
    "report": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "profile": {"type": "string", "default": "default"}},
    },
    "transcribe": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "timestamps": {"enum": ["none", "sentence", "word"], "default": "sentence"}},
    },
    "qa": {
        "type": "object",
        "required": ["path", "question"],
        "properties": {"path": {"type": "string"}, "question": {"type": "string"}, "thinking_budget": {"type": "integer"}},
    },
    "live_start": {
        "type": "object",
        "properties": {
            "ring_seconds": {"type": "number", "default": 60},
            "vad_threshold_dbfs": {"type": "number", "default": -45},
        },
    },
    "live_status": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}}},
    "live_stop": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}}},
    "process_metrics": {"type": "object", "properties": {}},
}

_DESCRIPTIONS = {
    "report": "Run oída /report on a local audio file.",
    "transcribe": "Run oída timestamped transcription.",
    "qa": "Ask a time-aware question about a local audio file.",
    "live_start": "Start a local oída live ring-buffer/VAD session.",
    "live_status": "Get local oída live session status.",
    "live_stop": "Stop a local oída live session and write its manifest.",
    "process_metrics": "Read process metrics from the local oída daemon.",
}

_KINDS = ["report", "transcribe", "qa", "live_start", "live_status", "live_stop", "process_metrics"]

# oida_* is canonical; hmm_*/aear_* mirror the previous project names, ear_*
# the original CLI surface. Aliases stay declared so existing MCP configs keep working.
_ALIAS_PREFIXES: dict[str, list[str]] = {
    "hmm": _KINDS,
    "aear": _KINDS,
    "ear": ["report", "transcribe", "qa"],
}


def _tool(name: str, description: str, kind: str) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": _SCHEMAS[kind]}


TOOLS: list[dict[str, Any]] = [
    _tool(f"oida_{kind}", _DESCRIPTIONS[kind], kind) for kind in _KINDS
] + [
    _tool(f"{prefix}_{kind}", f"Legacy alias for oida_{kind}.", kind)
    for prefix, kinds in _ALIAS_PREFIXES.items()
    for kind in kinds
]


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {exc.msg}"}}
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        response = handle(request)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def handle(message: object) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message
    try:
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "oida-local", "version": "0.1.0"}, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params", {})
            try:
                payload = call_tool(params)
                result = {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}
            except Exception as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        else:
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    except Exception as exc:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(exc)}}


def call_tool(params: dict[str, Any]) -> dict[str, object]:
    name = str(params.get("name"))
    arguments = params.get("arguments", {})
    canonical = name
    for prefix in ("hmm_", "aear_", "ear_"):
        if name.startswith(prefix):
            canonical = "oida_" + name[len(prefix):]
            break
    if canonical == "oida_report":
        return post_json(SERVER, "/report", {"path": _required(arguments, "path"), "profile": arguments.get("profile", "default")})
    if canonical == "oida_transcribe":
        return post_json(SERVER, "/transcribe", {"path": _required(arguments, "path"), "timestamps": arguments.get("timestamps", "sentence")})
    if canonical == "oida_qa":
        return post_json(
            SERVER,
            "/qa",
            {"path": _required(arguments, "path"), "question": _required(arguments, "question"), "thinking_budget": arguments.get("thinking_budget")},
        )
    if canonical == "oida_live_start":
        return post_json(
            SERVER,
            "/live/start",
            {"ring_seconds": arguments.get("ring_seconds", 60), "vad_threshold_dbfs": arguments.get("vad_threshold_dbfs", -45)},
        )
    if canonical == "oida_live_status":
        return post_json(SERVER, "/live/status", {"session_id": _required(arguments, "session_id")})
    if canonical == "oida_live_stop":
        return post_json(SERVER, "/live/stop", {"session_id": _required(arguments, "session_id")})
    if canonical == "oida_process_metrics":
        return get_json(SERVER, "/metrics/process")
    raise ValueError(f"unknown tool: {name}")


def _required(arguments: dict[str, Any], key: str) -> Any:
    value = arguments.get(key)
    if value is None or value == "":
        raise ValueError(f"missing required argument: {key}")
    return value


if __name__ == "__main__":
    main()
