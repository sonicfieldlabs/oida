from __future__ import annotations

import json
import os
import sys
from typing import Any

from harness.http_client import get_json, post_json

SERVER = os.getenv("AEAR_SERVER_URL", "http://127.0.0.1:8765")

TOOLS = [
    {
        "name": "aear_report",
        "description": "Run AEAR /report on a local audio file.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}, "profile": {"type": "string", "default": "default"}},
        },
    },
    {
        "name": "aear_transcribe",
        "description": "Run AEAR timestamped transcription.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}, "timestamps": {"enum": ["none", "sentence", "word"], "default": "sentence"}},
        },
    },
    {
        "name": "aear_qa",
        "description": "Ask a time-aware question about a local audio file.",
        "inputSchema": {
            "type": "object",
            "required": ["path", "question"],
            "properties": {"path": {"type": "string"}, "question": {"type": "string"}, "thinking_budget": {"type": "integer"}},
        },
    },
    {
        "name": "aear_live_start",
        "description": "Start a local AEAR live ring-buffer/VAD session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ring_seconds": {"type": "number", "default": 60},
                "vad_threshold_dbfs": {"type": "number", "default": -45},
            },
        },
    },
    {
        "name": "aear_live_status",
        "description": "Get local AEAR live session status.",
        "inputSchema": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}}},
    },
    {
        "name": "aear_live_stop",
        "description": "Stop a local AEAR live session and write its manifest.",
        "inputSchema": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}}},
    },
    {
        "name": "ear_report",
        "description": "Legacy alias for aear_report.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}, "profile": {"type": "string", "default": "default"}},
        },
    },
    {
        "name": "ear_transcribe",
        "description": "Legacy alias for aear_transcribe.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}, "timestamps": {"enum": ["none", "sentence", "word"], "default": "sentence"}},
        },
    },
    {
        "name": "ear_qa",
        "description": "Legacy alias for aear_qa.",
        "inputSchema": {
            "type": "object",
            "required": ["path", "question"],
            "properties": {"path": {"type": "string"}, "question": {"type": "string"}, "thinking_budget": {"type": "integer"}},
        },
    },
    {
        "name": "aear_process_metrics",
        "description": "Read process metrics from the local AEAR daemon.",
        "inputSchema": {"type": "object", "properties": {}},
    },
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
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "aear-local", "version": "0.1.0"}, "capabilities": {"tools": {}}}
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
    name = params.get("name")
    arguments = params.get("arguments", {})
    canonical = {"ear_report": "aear_report", "ear_transcribe": "aear_transcribe", "ear_qa": "aear_qa"}.get(str(name), name)
    if canonical == "aear_report":
        return post_json(SERVER, "/report", {"path": _required(arguments, "path"), "profile": arguments.get("profile", "default")})
    if canonical == "aear_transcribe":
        return post_json(SERVER, "/transcribe", {"path": _required(arguments, "path"), "timestamps": arguments.get("timestamps", "sentence")})
    if canonical == "aear_qa":
        return post_json(
            SERVER,
            "/qa",
            {"path": _required(arguments, "path"), "question": _required(arguments, "question"), "thinking_budget": arguments.get("thinking_budget")},
        )
    if canonical == "aear_live_start":
        return post_json(
            SERVER,
            "/live/start",
            {"ring_seconds": arguments.get("ring_seconds", 60), "vad_threshold_dbfs": arguments.get("vad_threshold_dbfs", -45)},
        )
    if canonical == "aear_live_status":
        return post_json(SERVER, "/live/status", {"session_id": _required(arguments, "session_id")})
    if canonical == "aear_live_stop":
        return post_json(SERVER, "/live/stop", {"session_id": _required(arguments, "session_id")})
    if canonical == "aear_process_metrics":
        return get_json(SERVER, "/metrics/process")
    raise ValueError(f"unknown tool: {name}")


def _required(arguments: dict[str, Any], key: str) -> Any:
    value = arguments.get(key)
    if value is None or value == "":
        raise ValueError(f"missing required argument: {key}")
    return value


if __name__ == "__main__":
    main()
