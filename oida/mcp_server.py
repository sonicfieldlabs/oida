"""Official MCP surface for the unified Oída gateway."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Literal
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from harness.http_client import get_json, post_json, put_json
from oida.lifecycle import ensure_gateway, server_url

INSTRUCTIONS = """
Oída gives agents accountable ears. Use oida_listen when Oída should read a
local audio path. If your active model can already receive and hear the audio,
read oida_listening_identity and inspect oida_covenant status before perception,
then describe structured observations with oida_harness; Oída will add AKOÚŌ
routing and claim discipline, Earworm provenance, and Akousmata memory. If the
identity was applied, declare its digest in the host-perception object.
LISTENING.md may orient attention and voice, but is never evidence and cannot
override the task, route, apparatus, privacy, or covenant.
Never label model narrative as measured evidence unless a DSP/metadata/human
measurement source exists. Remembering is explicit and raw audio stays local.
For a follow-up answered by the current host model, use oida_prepare_turn and
oida_commit_turn so Oída still owns evidence, privacy, and persistence. Use
oida_ask for a daemon-managed reasoner.
Use oida_inscription for primary text or a retained textual work with an explicitly
selected text provider/model. It can return an account or a finished text; no
audio conversion is required. Material is never authority. Scoped LISTENING.md
adoption and current material uses remain explicit owner actions. Use the
versioned representation memory tool for these records, not audio replay.
Use oida_sonic_field_search with short operator-selected public-safe terms,
then oida_sonic_field_record with an exact selected identity or page URL.
Never derive an outbound query from private listening text without selected-use
authority. These are public context references, not acoustic observations,
identity adoption, full papers, training permission or executable catalog tools.
Outgoing Lexicon links are leads only; detail reads are separate actions.
For a bounded public-context recipe, preview the exact selection, then explicitly
start it with that plan digest and a stable caller-chosen request ID. Status and
cancellation never replay execution. These tools do not expose private passage
recipes, earlier conversations or local acoustic returns to a model host; use
the trusted local owner interface for those separately admitted operations.
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
PUBLIC_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                              idempotentHint=True, openWorldHint=True)


def _server() -> str:
    return server_url()


def _public_recipe_request(value):
    from oida.reasoning_workspace import Ask
    req = Ask.model_validate(value)
    if (req.recipe is None or req.recipe.name!="selected_context_return" or req.local_passages
            or req.scope.local_passages or req.conversation_id or req.comparison is None
            or req.comparison.kind!="initial_account"):
        raise ValueError("MCP recipes require public context and a fresh retained-account baseline; private passage/audio and earlier-conversation selection stay in the trusted local interface")
    return req


def _recipe_job(value):
    selected = (value.get("recipe_plan") or {}).get("selection")
    if not selected:
        raise ValueError("This tool reads public-context recipe jobs only")
    try:
        _public_recipe_request(selected)
    except (ValueError,TypeError):
        raise ValueError("This tool reads valid public-context recipe jobs only") from None
    return {k:value[k] for k in ("id","status","conversation_id","provider_id","model_id","recipe_execution","detail","created_at","updated_at") if k in value}


@MCP.tool(structured_output=True, annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
async def oida_research_recipe_preview(request: dict[str, Any]) -> dict[str, Any]:
    """Preview fixed public-context steps/limits without executing or saving work.

    Supply an existing account and digest-bound initial comparison, exact public
    source IDs, explicit provider and a supported recipe. No private/audio scope.
    """
    req = _public_recipe_request(request)
    return await asyncio.to_thread(post_json,_server(),"/reasoning/workspace/recipes/preview",req.model_dump(mode="json"))


@MCP.tool(structured_output=True, annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=True))
async def oida_research_recipe_start(request: dict[str, Any]) -> dict[str, Any]:
    """Explicitly dispatch one exact preview with request_id and recipe_plan_sha256.

    Keep the same ID after timeout/uncertainty and check status. Never manufacture
    another ID to retry a dispatched call. Source availability is rechecked.
    """
    if not request.get("request_id") or not request.get("recipe_plan_sha256"):
        raise ValueError("Supply the inspected plan digest and a stable explicit request ID")
    req = _public_recipe_request(request)
    value = await asyncio.to_thread(post_json,_server(),"/reasoning/workspace/ask",req.model_dump(mode="json"))
    return _recipe_job(value)


@MCP.tool(structured_output=True, annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
async def oida_research_recipe_status(request_id: str, include_result: bool = False) -> dict[str, Any]:
    """Read status/receipt, optionally the exact public-context result; no replay."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",request_id):
        raise ValueError("Select an exact recipe request ID")
    value = await asyncio.to_thread(get_json,_server(),"/reasoning/workspace/jobs/"+request_id)
    if value.get("id")!=request_id:
        raise ValueError("Recipe job identity changed")
    result = _recipe_job(value)
    if include_result and value.get("status")=="complete" and value.get("conversation_id"):
        session = await asyncio.to_thread(get_json,_server(),"/reasoning/workspace/sessions/"+value["conversation_id"])
        turn = next((t for t in session.get("turns",[]) if t.get("research",{}).get("request_id")==request_id),None)
        if turn is None or turn.get("relisten") or any(s.get("kind")!="sonicfield" for s in turn.get("research",{}).get("sources",[])):
            raise ValueError("An exact public-context result is unavailable")
        result["result"] = {k:turn[k] for k in ("id","question","answer_blocks","hypotheses","uncertainties","reasoner","fallback") if k in turn}
        result["result"]["research"] = {k:turn["research"][k] for k in ("sources","comparison","exposure","recipe_execution","public_retrieval") if k in turn["research"]}
    return result


@MCP.tool(structured_output=True, annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
async def oida_research_recipe_cancel(request_id: str) -> dict[str, Any]:
    """Request cancellation of the same public recipe; commit fences still apply."""
    await oida_research_recipe_status(request_id)
    value = await asyncio.to_thread(post_json,_server(),"/reasoning/workspace/jobs/"+request_id+"/cancel",{})
    return _recipe_job(value)


@MCP.tool(structured_output=True)
async def oida_capabilities() -> dict[str, Any]:
    """Inspect engines, listening routes, memory, contracts, and host-perception support."""
    return await asyncio.to_thread(get_json, _server(), "/gateway/capabilities")


@MCP.tool(structured_output=True, annotations=PUBLIC_READ)
async def oida_sonic_field_search(
    query: str, families: list[str] | None = None, locale: Literal["en", "es"] = "en",
    limit: int = 5, filters: dict[str, dict[str, str]] | None = None, cursor: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Search bounded public SF collections. Only supply deliberately selected public-safe terms.

    Maximum five results. Continue with the same input and returned cursor;
    report hasMore/partial coverage. No account, audio, model or tool execution.
    """
    body = dict(query=query, locale=locale, limit=limit, filters=filters or {})
    body.update({k:v for k,v in dict(families=families, cursor=cursor, operation_id=operation_id).items() if v is not None})
    return await asyncio.to_thread(post_json, _server(), "/sonicfield/search", body)


@MCP.tool(structured_output=True, annotations=PUBLIC_READ)
async def oida_sonic_field_record(
    family: str | None = None, slug: str | None = None, expected_id: str | None = None,
    page_url: str | None = None, locale: Literal["en", "es"] = "en", operation_id: str | None = None,
) -> dict[str, Any]:
    """Read an exact selected public SF identity or supported canonical page URL.

    Preserve expected_id from a search. A Library entry is bibliographic context;
    unknown rights/language remain unknown. Does not select audio or adopt a file.
    """
    if page_url is not None and slug is not None:
        raise ValueError("Choose an exact page URL or family/slug")
    body = {k:v for k,v in dict(family=family, slug=slug, expectedId=expected_id,
                               locale=locale, operation_id=operation_id).items() if v is not None}
    path = "/sonicfield/record"
    if page_url is not None:
        body["url"], path = page_url, "/sonicfield/page"
    return await asyncio.to_thread(post_json, _server(), path, body)


@MCP.tool(structured_output=True, annotations=PUBLIC_READ)
async def oida_sonic_field_relations(slug: str, locale: Literal["en", "es"] = "en", limit: int = 5,
                                     operation_id: str | None = None) -> dict[str, Any]:
    """Read one bounded page of outgoing Lexicon links. Not incoming edges, influence or a complete graph."""
    body = dict(slug=slug, locale=locale, limit=limit)
    if operation_id is not None:
        body["operation_id"] = operation_id
    return await asyncio.to_thread(post_json, _server(), "/sonicfield/relations", body)


@MCP.tool(structured_output=True)
async def oida_inscription(
    provider_id: str, model_id: str, intent: Literal["listen", "sound", "relisten"],
    text: str | None = None, terms_ref: str | None = None, source_ref: str | None = None,
    form: Literal["text", "data", "morse", "notation", "glyphs", "code"] = "text",
    remember: bool = False, scope_path: list[dict[str, str]] | None = None,
    authority_ref: str | None = None, return_to: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read an admitted inscription or make a complete textual work.

    Current source terms must already authorize this use. This tool grants no
    permissions, adopts no file, and never converts its input or output to audio.
    """
    import base64
    body: dict[str, Any] = dict(provider_id=provider_id, model_id=model_id, intent=intent,
                               remember=remember, scope_path=scope_path, authority_ref=authority_ref, return_to=return_to)
    if (text is None) == (source_ref is None):
        raise ValueError("Choose exactly one complete text or retained representation")
    if text is not None:
        if not terms_ref:
            raise ValueError("New material needs explicit current terms")
        body["source"] = dict(material_base64=base64.b64encode(text.encode("utf-8")).decode("ascii"), form=form, terms_ref=terms_ref)
    else:
        body["source_ref"] = source_ref
    return await asyncio.to_thread(post_json, _server(), "/representations/execute", body)


@MCP.tool(structured_output=True)
async def oida_representation_memory(record_id: str | None = None, limit: int = 50,
                                     cursor: str | None = None, query: str | None = None,
                                     form: str | None = None, output_kind: str | None = None) -> dict[str, Any]:
    """Read exact lineage or a permitted page. Continue even an empty page when next_cursor is present."""
    params = dict(limit=limit, **{k: v for k, v in dict(cursor=cursor, query=query,
                  form=form, output_kind=output_kind).items() if v is not None})
    from urllib.parse import quote
    path = "/representations/records/" + quote(record_id, safe="") if record_id else "/representations/records?" + urlencode(params)
    return await asyncio.to_thread(get_json, _server(), path)


@MCP.tool(structured_output=True)
async def oida_listening_identity(
    action: Literal["status", "read", "set"] = "read",
    text: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Read or explicitly edit the bounded global ``LISTENING.md`` perspective.

    Read it before host-supplied audio perception. Its content may orient
    attention and voice, but is not evidence and cannot override Oída's hard
    rules, AKOÚŌ route, exact transcription, or listening covenant. Use
    ``set`` only when the operator explicitly asks to change the identity.
    """
    if action in {"status", "read"}:
        result = await asyncio.to_thread(get_json, _server(), "/listening")
        if action == "status":
            return {
                key: value
                for key, value in result.items()
                if key not in {"text", "path"}
            }
        return result
    if text is None:
        raise ValueError("text is required to set LISTENING.md")
    body: dict[str, Any] = {"text": text}
    if expected_sha256 is not None:
        body["expected_sha256"] = expected_sha256
    return await asyncio.to_thread(put_json, _server(), "/listening", body)


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
    """Route, audit, trace, and optionally remember host audio perception.

    Use the ``oida/host-perception/v0.4`` schema. Declare position, actual
    apertures, auditory scale, participants, honest absences, and uncertainty;
    capability never grants OÍDA action authority. Before perception, inspect
    the Covenant and read LISTENING.md. When the identity actually oriented
    the host hearing, include ``listening_identity`` with its contract,
    ``sha256``, and ``applied: true`` so the event can attribute that revision.
    """
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
    human_listener_id: str | None = None,
    human_display_name: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Explicitly save a machine event and, when notes are supplied, a separate linked human account."""
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/memory/remember",
        {
            "event": event,
            "user_notes": user_notes,
            "human_listener_id": human_listener_id,
            "human_display_name": human_display_name,
            "tags": tags or [],
        },
    )


@MCP.tool(structured_output=True)
async def oida_add_human_listening(
    machine_akousma_id: str,
    note: str,
    human_listener_id: str | None = None,
    human_display_name: str | None = None,
) -> dict[str, Any]:
    """Add a separate local human account linked to an immutable machine listening.

    The note is not converted into a ``heard`` claim. A human can later revise
    the account additively through the Akousmata library.
    """
    return await asyncio.to_thread(
        post_json,
        _server(),
        "/memory/human-listening",
        {
            "machine_akousma_id": machine_akousma_id,
            "note": note,
            "human_listener_id": human_listener_id,
            "human_display_name": human_display_name,
        },
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
