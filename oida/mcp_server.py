"""Official MCP surface for the unified Oída gateway."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Literal
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP

from harness.http_client import get_json, post_json
from oida.lifecycle import ensure_gateway, server_url

INSTRUCTIONS = """
Oída gives agents accountable ears. Use oida_listen when Oída should read a
local audio path. If your active model can already receive and hear the audio,
describe its structured observations with oida_harness instead; Oída will add
AKOÚŌ routing and claim discipline, Earworm provenance, and Akousmata memory.
Never label model narrative as measured evidence unless a DSP/metadata/human
measurement source exists. Remembering is explicit and raw audio stays local.
""".strip()

MCP = FastMCP(
    "oida",
    instructions=INSTRUCTIONS,
    website_url="https://github.com/sonicfieldlabs/oida",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)
_HTTP_APP = None


def _server() -> str:
    return server_url()


@MCP.tool(structured_output=True)
async def oida_capabilities() -> dict[str, Any]:
    """Inspect engines, listening routes, memory, contracts, and host-perception support."""
    return await asyncio.to_thread(get_json, _server(), "/gateway/capabilities")


@MCP.tool(structured_output=True)
async def oida_route(
    object_listened_to: str = "audio input",
    command: str = "/route",
    evidence_level: str = "audio_available",
) -> dict[str, Any]:
    """Choose AKOÚŌ listening modes and claim permissions before listening."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/gateway/route",
        {
            "object_listened_to": object_listened_to,
            "command": command,
            "evidence_level": evidence_level,
        },
    )


@MCP.tool(structured_output=True)
async def oida_listen(
    path: str,
    route_preset: str = "basic",
    remember: bool = False,
    privacy_mode: str = "session",
    raw_audio_policy: str | None = None,
    tags: list[str] | None = None,
    user_notes: str | None = None,
) -> dict[str, Any]:
    """Listen to a local audio file with Oída's configured engine and complete stack."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/gateway/listen",
        {
            "path": path,
            "route_preset": route_preset,
            "remember": remember,
            "privacy_mode": privacy_mode,
            "raw_audio_policy": raw_audio_policy,
            "tags": tags or [],
            "user_notes": user_notes,
        },
    )


@MCP.tool(structured_output=True)
async def oida_harness(
    perception: dict[str, Any],
    route_preset: str = "basic",
    command: str | None = None,
    question: str | None = None,
    remember: bool = False,
    privacy_mode: str = "session",
    raw_audio_policy: str = "not_stored",
) -> dict[str, Any]:
    """Route, audit, trace, and optionally remember perception from an audio-capable host model."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/gateway/harness",
        {
            "perception": perception,
            "route_preset": route_preset,
            "command": command,
            "question": question,
            "remember": remember,
            "privacy_mode": privacy_mode,
            "raw_audio_policy": raw_audio_policy,
        },
    )


@MCP.tool(structured_output=True)
async def oida_ask(
    question: str,
    event: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    include_memory: bool = True,
) -> dict[str, Any]:
    """Ask a grounded follow-up question about a normalized listening event."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/conversation/ask",
        {
            "question": question,
            "event": event,
            "conversation_id": conversation_id,
            "include_memory": include_memory,
            "allow_remote_model": False,
            "provider": "local_structured",
        },
    )


@MCP.tool(structured_output=True)
async def oida_memory_search(
    query: str | None = None,
    tag: str | None = None,
    route: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search recent private Oída listening traces without exposing raw audio."""
    params = {key: value for key, value in {"q": query, "tag": tag, "route": route, "limit": limit}.items() if value is not None}
    return await asyncio.to_thread(get_json, _server(), f"/memory?{urlencode(params)}")


@MCP.tool(structured_output=True)
async def oida_memory_get(trace_id: str) -> dict[str, Any]:
    """Read one Oída listening trace and its nearest sonic memories."""
    return await asyncio.to_thread(get_json, _server(), f"/memory/trace/{trace_id}")


@MCP.tool(structured_output=True)
async def oida_remember(
    event: dict[str, Any],
    user_notes: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Explicitly save a normalized listening event into local Akousmata memory."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/memory/remember",
        {"event": event, "user_notes": user_notes, "tags": tags or []},
    )


@MCP.tool(structured_output=True)
async def oida_forget(trace_id: str) -> dict[str, Any]:
    """Forget one private listening trace and apply its raw-audio cleanup policy."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/memory/forget",
        {"trace_id": trace_id},
    )


@MCP.tool(structured_output=True)
async def oida_live(
    action: Literal["start", "status", "stop"],
    session_id: str | None = None,
    ring_seconds: float = 60.0,
    vad_threshold_dbfs: float = -45.0,
) -> dict[str, Any]:
    """Start, inspect, or stop an Oída live local listening buffer."""
    if action == "start":
        return await asyncio.to_thread(
            post_json,
            _server(),
            "/live/start",
            {"ring_seconds": ring_seconds, "vad_threshold_dbfs": vad_threshold_dbfs},
        )
    if not session_id:
        raise ValueError("session_id is required for live status or stop")
    return await asyncio.to_thread(
        post_json,
        _server(),
        f"/live/{action}",
        {"session_id": session_id},
    )


@MCP.resource("oida://manifest", mime_type="application/json")
async def manifest_resource() -> str:
    """Oída gateway and complete-stack manifest."""
    data = await asyncio.to_thread(get_json, _server(), "/gateway")
    return json.dumps(data, ensure_ascii=False, indent=2)


@MCP.resource("oida://akouo/presets", mime_type="application/json")
async def presets_resource() -> str:
    """Portable AKOÚŌ route presets offered by this gateway."""
    data = await asyncio.to_thread(get_json, _server(), "/akouo/presets")
    return json.dumps(data, ensure_ascii=False, indent=2)


@MCP.resource("oida://schema/host-perception", mime_type="application/schema+json")
async def host_schema_resource() -> str:
    """Schema for observations supplied by an audio-capable host model."""
    data = await asyncio.to_thread(
        get_json,
        _server(),
        "/gateway/schema/host-perception",
    )
    return json.dumps(data, ensure_ascii=False, indent=2)


@MCP.resource("oida://memory/recent", mime_type="application/json")
async def recent_memory_resource() -> str:
    """The 20 most recent private listening traces."""
    data = await asyncio.to_thread(get_json, _server(), "/memory?limit=20")
    return json.dumps(data, ensure_ascii=False, indent=2)


@MCP.prompt(name="listen_with_oida")
def listen_prompt(object_description: str = "the supplied audio", route_preset: str = "basic") -> str:
    """Choose the correct Oída perception path and run a disciplined listen."""
    return f"""Listen to {object_description} through Oída using preset {route_preset}.
If you can directly hear the supplied audio, first describe host, source,
apparatus, time-anchored observations, and uncertainty, then call oida_harness.
Otherwise call oida_listen with a local path. Keep heard, measured, inferred,
interpreted, speculative, and undetermined claims distinct. Do not remember it
unless the user asks or the workflow explicitly requires durable memory."""


@MCP.prompt(name="full_ear")
def full_ear_prompt(object_description: str = "the supplied audio") -> str:
    """Run Oída's deep multi-route listening workflow."""
    return listen_prompt(object_description, "deep")


@MCP.prompt(name="remember_sound")
def remember_prompt(object_description: str = "the supplied audio") -> str:
    """Listen with memory lineage and explicitly save the resulting event."""
    return listen_prompt(object_description, "remember") + "\nSet remember=true and report the saved trace id."


def streamable_http_app():
    """Return one cached ASGI app for mounting at ``/mcp``."""
    global _HTTP_APP
    if _HTTP_APP is None:
        _HTTP_APP = MCP.streamable_http_app()
    return _HTTP_APP


def main() -> None:
    if os.getenv("OIDA_MCP_ENSURE_DAEMON", "1").strip().lower() not in {"0", "false", "no", "off"}:
        try:
            ensure_gateway(profile=os.getenv("OIDA_ENGINE_PROFILE"))
        except Exception as exc:
            print(f"oida MCP could not ensure the local gateway: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    MCP.run(transport="stdio")


if __name__ == "__main__":
    main()
