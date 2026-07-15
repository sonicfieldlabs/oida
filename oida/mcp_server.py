"""Official MCP surface for the unified Oída gateway."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Literal
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP

from harness.http_client import get_json, post_json, put_json
from oida.lifecycle import ensure_gateway, server_url

INSTRUCTIONS = """
Oída gives agents accountable ears. Use oida_listen when Oída should read a
local audio path. If your active model can already receive and hear the audio,
describe its structured observations with oida_harness instead; Oída will add
AKOÚŌ routing and claim discipline, Earworm provenance, and Akousmata memory.
Never label model narrative as measured evidence unless a DSP/metadata/human
measurement source exists. Remembering is explicit and raw audio stays local.
For a follow-up answered by the current host model, use oida_prepare_turn and
oida_commit_turn so Oída still owns evidence, privacy, and persistence. Use
oida_ask for a daemon-managed reasoner.
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
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Listen to a local audio file with Oída's configured engine and complete stack.

    ``location`` (optional, spec v1.2): ``{lat, lon, accuracy_m?, label?, source?}`` —
    where the sound was heard; attach only with the listener's consent."""
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
            "location": location,
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
    event_id: str | None = None,
    event: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    include_memory: bool = True,
    provider_id: str | None = None,
    model_id: str | None = None,
    profile_id: str | None = None,
    comparison_event_ids: list[str] | None = None,
    allow_targeted_relisten: bool | None = None,
    include_transcript: bool | None = None,
    include_memory_content: bool | None = None,
) -> dict[str, Any]:
    """Ask a daemon-managed reasoner a grounded question about one immutable event."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/conversation/ask",
        {
            "question": question,
            "event_id": event_id,
            "event": event,
            "conversation_id": conversation_id,
            "include_memory": include_memory,
            "provider_id": provider_id,
            "model_id": model_id,
            "profile_id": profile_id,
            "comparison_event_ids": comparison_event_ids or [],
            "allow_targeted_relisten": allow_targeted_relisten,
            "include_transcript": include_transcript,
            "include_memory_content": include_memory_content,
        },
    )


@MCP.tool(structured_output=True)
async def oida_prepare_turn(
    question: str,
    event_id: str | None = None,
    event: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    profile_id: str | None = None,
    comparison_event_ids: list[str] | None = None,
    include_memory: bool = True,
    allow_targeted_relisten: bool | None = None,
    include_transcript: bool | None = None,
    include_memory_content: bool | None = None,
) -> dict[str, Any]:
    """Prepare Oída's protected prompt/evidence packet for this active host to answer."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/conversation/prepare",
        {
            "question": question,
            "event_id": event_id,
            "event": event,
            "conversation_id": conversation_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "profile_id": profile_id,
            "comparison_event_ids": comparison_event_ids or [],
            "include_memory": include_memory,
            "allow_targeted_relisten": allow_targeted_relisten,
            "include_transcript": include_transcript,
            "include_memory_content": include_memory_content,
        },
    )


@MCP.tool(structured_output=True)
async def oida_commit_turn(
    prepare_token: str,
    response: dict[str, Any] | str,
) -> dict[str, Any]:
    """Validate and commit a prepared host response; may return one follow-up re-listen packet."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/conversation/commit",
        {"prepare_token": prepare_token, "response": response},
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
async def oida_covenant(
    action: Literal["status", "read", "set", "activate", "deactivate"],
    name: str | None = None,
    text: str | None = None,
    activate: bool = False,
) -> dict[str, Any]:
    """Inspect, write, or switch the listening covenant — Oída's sovereignty layer.

    A covenant is a small human-written declaration (plain text) of what this
    ear will not listen to, will release after hearing, will not reveal, will
    not retain, will blur, or will refuse at certain hours. Executable rules
    are enforced at the daemon's gates; every other line is carried verbatim
    as a commitment. The layer is empty by default; ``status`` shows the
    active covenant and what it enforces. Agents may propose covenants with
    ``set``, but activation is the operator's act — surface it, don't assume it."""
    if action == "status":
        return await asyncio.to_thread(get_json, _server(), "/covenant")
    if action == "read":
        if not name:
            raise ValueError("name is required to read a covenant")
        return await asyncio.to_thread(get_json, _server(), f"/covenant/{name}")
    if action == "set":
        if not name or text is None:
            raise ValueError("name and text are required to set a covenant")
        return await asyncio.to_thread(
            put_json, _server(), "/covenant", {"name": name, "text": text, "activate": activate}
        )
    if action == "deactivate":
        return await asyncio.to_thread(post_json, _server(), "/covenant/activate", {"name": None})
    if not name:
        raise ValueError("name is required to activate a covenant")
    return await asyncio.to_thread(post_json, _server(), "/covenant/activate", {"name": name})


@MCP.tool(structured_output=True)
async def oida_live(
    action: Literal["start", "status", "stop", "capture"],
    session_id: str | None = None,
    ring_seconds: float = 60.0,
    vad_threshold_dbfs: float = -45.0,
    seconds: float = 10.0,
    analyze: bool = False,
    route_preset: str = "basic",
) -> dict[str, Any]:
    """Start, inspect, capture from, or stop an Oída live listening buffer.

    ``capture`` is the past direction made callable: it slices the last
    ``seconds`` already sitting in the ring buffer — the sound from before
    the trigger — and optionally analyzes it (``analyze`` + ``route_preset``)."""
    if action == "start":
        return await asyncio.to_thread(
            post_json,
            _server(),
            "/live/start",
            {"ring_seconds": ring_seconds, "vad_threshold_dbfs": vad_threshold_dbfs},
        )
    if not session_id:
        raise ValueError("session_id is required for live status, capture, or stop")
    if action == "capture":
        return await asyncio.to_thread(
            post_json,
            _server(),
            "/live/capture",
            {"session_id": session_id, "seconds": seconds, "analyze": analyze, "route_preset": route_preset},
        )
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
