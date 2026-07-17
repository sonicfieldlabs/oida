"""Provider-neutral Oída gateway contract.

The gateway has two perception paths:

* Oída-owned perception, where an engine such as MOSS-Audio receives the audio.
* Host perception, where an audio-capable agent supplies structured observations
  and Oída contributes routing, epistemic discipline, provenance, and memory.

The second path deliberately does not pretend that every host model has the
same acoustic apparatus.  Its declared apparatus travels with every listening
event and controls which claims can be treated as supported.
"""
from __future__ import annotations

from typing import Any

from harness.akouo.command import build_harness_output
from oida.akouo_skills import akouo_manifest, route_preset
from oida.contracts import (
    AudioDataRef,
    AudioSegment,
    AudioSourceDescriptor,
    PrivacyMode,
    RawAudioPolicy,
    new_id,
    now_iso,
)
from oida import __version__
from oida.listening import listening_event_dict
from oida.listening_identity import (
    LISTENING_IDENTITY_CONTRACT,
    LISTENING_IDENTITY_FILENAME,
    LISTENING_IDENTITY_ROLE,
    ListeningIdentitySnapshot,
)
from oida.memory import earworm_context_for_event

GATEWAY_CONTRACT = "oida/gateway/v0.3"
HOST_PERCEPTION_CONTRACT = "oida/host-perception/v0.2"
SUPPORTED_HOSTS = ("hermes", "codex", "claude", "openclaw", "opencode", "generic")
CLAIM_CATEGORIES = ("heard", "measured", "inferred", "interpreted", "speculative", "undetermined")


def gateway_manifest(*, version: str | None = None) -> dict[str, Any]:
    """Describe the stable surface agents should integrate against."""
    version = version or __version__
    akouo = akouo_manifest()
    return {
        "name": "oida",
        "display_name": "oída",
        "version": version,
        "contract": GATEWAY_CONTRACT,
        "role": ["listening_agent", "agentic_listening_harness", "local_gateway"],
        "components": {
            "akouo": {
                "role": "listening router and claim discipline",
                "contract": f"akouo/{akouo['akouo_contract_version']}",
                "host_profile_version": akouo["version"],
            },
            "earworm": {"role": "event/provenance and context protocol", "contract": "earworm/v0.4"},
            "akousmata": {"role": "local sonic-memory store and navigator", "contract": "akousmata/v0.4"},
        },
        "perception_paths": {
            "oida_owned": {
                "description": "Oída reads a local audio source with its configured engine and DSP.",
                "local_engines": ["moss_audio", "stub_dsp", "openai_compatible_local"],
            },
            "host_supplied": {
                "description": "An audio-capable host submits structured perception; Oída routes, audits, traces, and remembers it.",
                "contract": HOST_PERCEPTION_CONTRACT,
                "supported_hosts": list(SUPPORTED_HOSTS),
            },
        },
        "listening_identity": {
            "contract": LISTENING_IDENTITY_CONTRACT,
            "filename": LISTENING_IDENTITY_FILENAME,
            "role": LISTENING_IDENTITY_ROLE,
            "empty_by_default": True,
            "event_content_included": False,
            "host_declaration_contract": HOST_PERCEPTION_CONTRACT,
        },
        "transports": {
            "rest": "/gateway/*",
            "mcp_stdio": "oida gateway --stdio --ensure-daemon",
            "dashboard": "/",
            "library": "/library/",
            "remote_ear": "/remote",
            "listening_identity": "/listening",
            "covenant": "/covenant",
        },
        "privacy": {
            "local_first": True,
            "memory_is_explicit": True,
            "raw_audio_default": "external_ref",
            "remote_access": "operator-configured private network only",
        },
        "route_presets": [preset["id"] for preset in akouo["route_presets"]],
    }


def normalize_host_perception(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a host report into the report vocabulary consumed by AKOÚŌ.

    Host observations remain visible in ``host_observations``; derived caption,
    transcript, event, speech, and music blocks merely make existing Oída
    surfaces useful without erasing the original report.
    """
    host = _dict(payload.get("host"))
    host_id = str(host.get("id") or "generic").strip().lower()
    if host_id not in SUPPORTED_HOSTS:
        host_id = "generic"
    model = str(host.get("model") or host.get("model_id") or "host-audio-model")
    source = _dict(payload.get("source"))
    apparatus = _normalize_apparatus(_dict(payload.get("apparatus")), host_id=host_id, model=model)
    observations = _normalize_observations(payload.get("observations"), model=model)
    measurements = _dict(payload.get("measurements"))
    dsp = _dsp_block(measurements, source, apparatus)
    transcript = _transcript_block(payload.get("transcript"))
    events = _events_block(payload.get("events"))
    speech = _speech_block(payload.get("speech"))
    caption = _caption_block(
        payload.get("caption"),
        observations,
        transcript_present=bool(transcript.get("present")),
        speech_present=bool(speech.get("present")),
    )
    music = _music_block(payload.get("music"))
    uncertainty = _string_list(payload.get("uncertainty"))
    uncertainty.extend(_string_list(apparatus.get("known_blind_spots")))
    source_uri = str(source.get("uri") or source.get("path") or f"host://{host_id}/{host.get('session_id') or 'session'}")
    return {
        "version": "0.2",
        "contract": HOST_PERCEPTION_CONTRACT,
        "source": {
            "path": source_uri,
            "label": str(source.get("label") or "Host-provided audio"),
            "type": str(source.get("type") or "external_stream"),
            "duration_s": _number(source.get("duration_s") or measurements.get("duration_s")),
            "sr_native": _integer(source.get("sample_rate") or apparatus.get("sample_rate_hz")),
            "channels": _integer(source.get("channels") or apparatus.get("channels")),
            "sha256": source.get("sha256"),
            "audio_available_to_oida": bool(source.get("audio_available_to_oida", False)),
        },
        "engine": {
            "daemon": host_id,
            "model": model,
            "profile": "host-supplied",
            "params": {},
            "thinking_budget": host.get("thinking_budget"),
            "chunks": [],
            "wall_ms": host.get("wall_ms"),
            "unavailable_reason": None,
        },
        "host": {
            "id": host_id,
            "session_id": host.get("session_id"),
            "model": model,
            "provider": host.get("provider"),
            "audio_input_capable": bool(host.get("audio_input_capable", True)),
        },
        "listening_identity": _dict(payload.get("listening_identity")) or None,
        "apparatus": apparatus,
        "host_observations": observations,
        "dsp": dsp,
        "transcript": transcript,
        "events": events,
        "caption": caption,
        "speech": speech,
        "music": music,
        "qa": {"question": None, "answer": "", "answer_segments": [], "confidence": "undetermined", "notes": []},
        "model_uncertainty_notes": _dedupe(uncertainty),
        "forbidden_topics_triggered": [],
        "raw_host_report": payload.get("raw_report"),
    }


def harness_host_perception(
    payload: dict[str, Any],
    *,
    route_preset_id: str = "basic",
    command: str | None = None,
    question: str | None = None,
    remember: bool = False,
    memory: Any | None = None,
    privacy_mode: PrivacyMode = "session",
    raw_audio_policy: RawAudioPolicy = "not_stored",
    enabled_skill_ids: list[str] | None = None,
    disabled_skill_ids: list[str] | None = None,
    covenant_engine: Any | None = None,
    listening_identity_snapshot: ListeningIdentitySnapshot | None = None,
) -> dict[str, Any]:
    """Run host perception through the complete listening harness."""
    preset = route_preset(route_preset_id)
    rules_applied: list[str] = []
    withheld: list[dict[str, Any]] = []
    if covenant_engine is not None:
        source = _dict(payload.get("source"))
        source_type = str(source.get("type") or "external_stream")
        refusal = covenant_engine.refuse_source(source_type) or covenant_engine.refuse_quiet_hours()
        if refusal:
            raise PermissionError(refusal)
        if covenant_engine.forbids_retention("raw-audio"):
            raw_audio_policy = "not_stored"
            rules_applied.append("do_not_retain:raw-audio")
    perception = normalize_host_perception(payload)
    if covenant_engine is not None:
        perception, perception_withheld = covenant_engine.redact_perception(perception)
        withheld.extend(perception_withheld)
    command_output = build_harness_output(perception, command=command or preset.akouo_command, question=question)
    if covenant_engine is not None:
        command_output, command_withheld = covenant_engine.redact_command_output(command_output)
        withheld.extend(command_withheld)
    segment = _host_segment(perception, privacy_mode=privacy_mode, raw_audio_policy=raw_audio_policy)
    identity_snapshot = listening_identity_snapshot or ListeningIdentitySnapshot.empty()
    identity_block = identity_snapshot.host_event_block(payload.get("listening_identity"))
    event = listening_event_dict(
        perception,
        command_output=command_output,
        segment=segment,
        route_preset_id=preset.id,
        enabled_skill_ids=enabled_skill_ids,
        disabled_skill_ids=disabled_skill_ids,
        privacy_mode=privacy_mode,
        raw_audio_policy=raw_audio_policy,
        listening_identity=identity_block,
    )
    if identity_block.get("application") == "revision_mismatch":
        event.setdefault("aggregate", {}).setdefault("warnings", []).append(
            "The host declared a different LISTENING.md revision; identity application is not attributed."
        )
    if covenant_engine is not None:
        event["covenant"] = covenant_engine.event_block(
            rules_applied=rules_applied,
            withheld=withheld,
        )
    trace = None
    if memory is not None:
        event = memory.enrich_event(event)
        memory_refusal = covenant_engine.forbids_retention("memory") if covenant_engine is not None else None
        if memory_refusal:
            rules_applied.append("do_not_retain:memory")
            event["covenant"] = covenant_engine.event_block(
                rules_applied=rules_applied,
                withheld=withheld,
            )
        if remember and privacy_mode != "incognito" and not memory_refusal:
            host_id = str(perception.get("host", {}).get("id") or "generic")
            trace = memory.remember(event, tags=["host-perception", f"host-{host_id}"])
            event.setdefault("memory", {})["saved_trace_id"] = trace["id"]
    earworm = trace.get("earworm") if isinstance(trace, dict) else earworm_context_for_event(event)
    return {
        "contract": GATEWAY_CONTRACT,
        "perception_path": "host_supplied",
        "listening_event": event,
        "perception_report": perception,
        "command_output": command_output,
        "earworm": earworm,
        "trace": trace,
    }


def _host_segment(
    report: dict[str, Any], *, privacy_mode: PrivacyMode, raw_audio_policy: RawAudioPolicy
) -> AudioSegment:
    source = _dict(report.get("source"))
    host = _dict(report.get("host"))
    apparatus = _dict(report.get("apparatus"))
    source_type = str(source.get("type") or "external_stream")
    if source_type not in {"live_input", "system_output", "file", "buffer", "generated", "external_stream"}:
        source_type = "external_stream"
    uri = str(source.get("path") or "")
    keep_ref = raw_audio_policy in {"saved", "external_ref"} and bool(source.get("audio_available_to_oida"))
    data_ref = AudioDataRef(
        kind="external" if keep_ref else "none",
        uri=uri if keep_ref else None,
        sha256=str(source.get("sha256")) if source.get("sha256") else None,
    )
    descriptor = AudioSourceDescriptor(
        type=source_type,  # type: ignore[arg-type]
        label=str(source.get("label") or "Host-provided audio"),
        platform=str(host.get("id") or "generic"),
        details={
            "host": host,
            "apparatus": apparatus,
            "audio_available_to_oida": bool(source.get("audio_available_to_oida")),
        },
    )
    return AudioSegment(
        id=new_id("seg"),
        source=descriptor,
        created_at=now_iso(),
        duration_ms=round(float(source.get("duration_s") or 0) * 1000),
        sample_rate=int(source.get("sr_native") or apparatus.get("sample_rate_hz") or 0),
        channels=int(source.get("channels") or apparatus.get("channels") or 0),
        format="other",
        data_ref=data_ref,
        ephemeral=raw_audio_policy in {"not_stored", "temp"},
        user_initiated=True,
        privacy_mode=privacy_mode,
        metadata={
            "contract": HOST_PERCEPTION_CONTRACT,
            "perception_path": "host_supplied",
            "raw_audio_policy": raw_audio_policy,
        },
    )


def _normalize_apparatus(value: dict[str, Any], *, host_id: str, model: str) -> dict[str, Any]:
    sources = _string_list(value.get("perception_sources")) or [f"{host_id} audio-input model ({model})"]
    blind_spots = _string_list(value.get("known_blind_spots"))
    if not blind_spots:
        blind_spots = [
            "The host did not declare its acoustic preprocessing, bandwidth, channel reduction, or calibration.",
            "Host-model perceptions are machine-heard evidence, not signal measurements.",
        ]
    return {
        "substrate": str(value.get("substrate") or "host_audio_model"),
        "perception_sources": sources,
        "model_ids": _string_list(value.get("model_ids")) or [model],
        "sample_rate_hz": _integer(value.get("sample_rate_hz")),
        "channels": _integer(value.get("channels")),
        "bandwidth_limit_hz": _integer(value.get("bandwidth_limit_hz")),
        "absolute_level_calibrated": bool(value.get("absolute_level_calibrated", False)),
        "known_blind_spots": blind_spots,
    }


def _normalize_observations(value: Any, *, model: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            item = {"statement": item}
        if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
            continue
        category = str(item.get("category") or "heard")
        if category not in CLAIM_CATEGORIES:
            category = "undetermined"
        source = str(item.get("source") or "model")
        if source not in {"audio", "dsp", "metadata", "model", "transcript", "context", "memory", "human"}:
            source = "model"
        result.append(
            {
                "statement": " ".join(str(item["statement"]).split()),
                "category": category,
                "confidence": str(item.get("confidence") or "medium"),
                "basis": str(item.get("basis") or f"{model} host audio perception"),
                "source": source,
                "speech_content": bool(item.get("speech_content")) or source == "transcript",
                "time_range": item.get("time_range") if isinstance(item.get("time_range"), dict) else None,
            }
        )
    return result


def _dsp_block(measurements: dict[str, Any], source: dict[str, Any], apparatus: dict[str, Any]) -> dict[str, Any]:
    features = _dict(measurements.get("features"))
    for key, value in measurements.items():
        if key not in {"features", "duration_s", "sample_rate", "channels"} and isinstance(value, (int, float)):
            features[key] = value
    return {
        "durationSeconds": _number(measurements.get("duration_s") or source.get("duration_s")),
        "sampleRate": _integer(measurements.get("sample_rate") or source.get("sample_rate") or apparatus.get("sample_rate_hz")),
        "channelCount": _integer(measurements.get("channels") or source.get("channels") or apparatus.get("channels")),
        "features": features,
        "measurement_source": str(measurements.get("source") or "host_declared"),
    }


def _transcript_block(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"present": bool(value.strip()), "language": None, "segments": [{"t0": None, "t1": None, "text": value.strip(), "confidence": "medium"}], "notes": []}
    block = _dict(value)
    segments = block.get("segments") if isinstance(block.get("segments"), list) else []
    return {
        "present": bool(block.get("present", segments)),
        "language": block.get("language"),
        "segments": segments,
        "notes": _string_list(block.get("notes")),
    }


def _events_block(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict) or not item.get("label"):
            continue
        result.append(
            {
                "t0": _number(item.get("t0")),
                "t1": _number(item.get("t1")),
                "label": str(item["label"]),
                "description": str(item.get("description") or ""),
                "corroborated_by_dsp": bool(item.get("corroborated_by_dsp", False)),
                "confidence": str(item.get("confidence") or "medium"),
            }
        )
    return result


def _caption_block(
    value: Any,
    observations: list[dict[str, Any]],
    *,
    transcript_present: bool = False,
    speech_present: bool = False,
) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "dense": value.strip() or None,
            "brief": value.strip() or None,
            "speech_content": bool(value.strip()),
        }
    block = _dict(value)
    fallback_item = next(
        (item for item in observations if item["category"] in {"heard", "inferred"}),
        None,
    )
    fallback = fallback_item.get("statement") if isinstance(fallback_item, dict) else None
    # Caption text is free-form and may contain verbatim speech even when the
    # host omitted a transcript marker. Taint all non-empty caption prose.
    speech_content = bool(
        block.get("dense")
        or block.get("brief")
        or fallback
        or block.get("speech_content")
        or transcript_present
        or speech_present
        or (isinstance(fallback_item, dict) and fallback_item.get("speech_content"))
    )
    return {
        "dense": block.get("dense") or fallback,
        "brief": block.get("brief") or fallback,
        "speech_content": speech_content,
    }


def _speech_block(value: Any) -> dict[str, Any]:
    block = _dict(value)
    dimensions = _dict(block.get("dimensions"))
    return {"present": bool(block.get("present", dimensions)), "dimensions": dimensions, "identity_caution": True, "notes": _string_list(block.get("notes"))}


def _music_block(value: Any) -> dict[str, Any]:
    block = _dict(value)
    return {
        "present": bool(block.get("present", block.get("description"))),
        "description": block.get("description"),
        "tempo_feel": block.get("tempo_feel"),
        "dsp_bpm_candidate": _number(block.get("dsp_bpm_candidate")),
        "moss_bpm_candidate": _number(block.get("model_bpm_candidate") or block.get("moss_bpm_candidate")),
        "notes": _string_list(block.get("notes")),
    }


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
