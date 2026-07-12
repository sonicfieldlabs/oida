from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.types import LISTENING_MODES


@dataclass(frozen=True)
class CommandRoute:
    command: str
    modes: list[str]
    recommended_next_mode: str
    summary: str


COMMAND_ROUTES: dict[str, CommandRoute] = {
    "/listen": CommandRoute("/listen", ["acoulogical-object-listening", "signal-inspection-listening", "transductive-media-listening"], "signal-inspection-listening", "Responsible first pass over the mixed perception report."),
    "/full-ear": CommandRoute("/full-ear", ["signal-inspection-listening", "acoulogical-object-listening", "musical-aesthetic-listening", "embodied-affective-listening", "transductive-media-listening", "critical-political-listening"], "critical-political-listening", "Broad multimodal pass over the same report."),
    "/study": CommandRoute("/study", ["acoulogical-object-listening", "musical-aesthetic-listening", "critical-political-listening", "ecological-posthuman-listening"], "critical-political-listening", "Research-oriented route for sonic study and methodology."),
    "/tech": CommandRoute("/tech", ["signal-inspection-listening", "transductive-media-listening"], "transductive-media-listening", "Technical inspection with model-mediation correction."),
    "/reference": CommandRoute("/reference", ["acoulogical-object-listening", "critical-political-listening"], "none", "Conceptual reference mapping over already grounded listening claims."),
    "/litany": CommandRoute("/litany", ["critical-political-listening", "transductive-media-listening", "acoulogical-object-listening", "musical-aesthetic-listening"], "critical-political-listening", "Critique simplistic sound-versus-vision claims through grounded listening."),
    "/fiction": CommandRoute("/fiction", ["symbolic-fictional-listening", "embodied-affective-listening", "ecological-posthuman-listening"], "embodied-affective-listening", "Declared speculative sonic-world route with evidence boundaries intact."),
    "/transduce": CommandRoute("/transduce", ["transductive-media-listening", "signal-inspection-listening", "critical-political-listening"], "signal-inspection-listening", "Mediation-chain route for sensors, codecs, AI audio, and model outputs."),
    "/voice": CommandRoute("/voice", ["voice-speech-listening", "transductive-media-listening", "accessibility-normative-listening", "critical-political-listening"], "accessibility-normative-listening", "Voice/speech pass with identity and consent cautions."),
    "/audiovision": CommandRoute("/audiovision", ["audiovisual-scenic-listening", "acoulogical-object-listening", "voice-speech-listening", "critical-political-listening"], "acoulogical-object-listening", "Sound-image-text-scene route for audiovisual material."),
    "/access": CommandRoute("/access", ["accessibility-normative-listening", "voice-speech-listening", "embodied-affective-listening", "critical-political-listening"], "accessibility-normative-listening", "Accessibility and hearing-norm review route."),
    "/field": CommandRoute("/field", ["ecological-posthuman-listening", "transductive-media-listening", "critical-political-listening", "material-event-listening"], "material-event-listening", "Field-recording route with mediation and situated-listening cautions."),
    "/method": CommandRoute("/method", ["acoulogical-object-listening", "critical-political-listening", "accessibility-normative-listening"], "none", "Sonic methodology and agent-handoff route."),
    "/route": CommandRoute("/route", ["acoulogical-object-listening"], "none", "Router-only handoff plan for another agent or app."),
    "/forensic": CommandRoute("/forensic", ["signal-inspection-listening", "forensic-archival-listening", "critical-political-listening"], "critical-political-listening", "Strict evidentiary route; paralinguistic speculation is suppressed."),
    "/remember": CommandRoute("/remember", ["memory-lineage-listening", "acoulogical-object-listening", "signal-inspection-listening"], "none", "Memory route: compare against stored akousmata and register the listening."),
    "/covenant": CommandRoute("/covenant", ["sovereign-listening", "acoulogical-object-listening", "signal-inspection-listening"], "none", "Sovereignty route: apply the listening covenant and report enforcement, withholding, and commitments."),
    "/one-sound-many-ears": CommandRoute("/one-sound-many-ears", LISTENING_MODES, "undetermined", "Comparative flagship: all fifteen listening modes read one PerceptionReport."),
}


def available_harness_controls() -> dict[str, object]:
    return {
        "commands": [
            {"command": route.command, "modes": route.modes, "summary": route.summary, "recommended_next_mode": route.recommended_next_mode}
            for route in COMMAND_ROUTES.values()
        ],
        "modes": LISTENING_MODES,
        "evidence_levels": ["none", "prompt_only", "metadata_only", "decoded_audio_metadata", "measured_signal", "transcript_or_caption", "contextual_note", "mixed"],
    }


def route_for_command(command: str) -> CommandRoute:
    try:
        return COMMAND_ROUTES[command]
    except KeyError as exc:
        valid = ", ".join(COMMAND_ROUTES)
        raise ValueError(f"unknown AKOUO command: {command}. Valid commands: {valid}") from exc


def claim_permissions_for(evidence_level: str, command: str = "/listen") -> dict[str, bool]:
    route_for_command(command)
    speculative = False
    table = {
        "none": (False, False, False, False),
        "prompt_only": (True, False, True, True),
        "metadata_only": (False, True, True, True),
        "decoded_audio_metadata": (True, True, True, True),
        "measured_signal": (True, True, True, True),
        "transcript_or_caption": (True, False, True, True),
        "contextual_note": (True, False, True, True),
        "mixed": (True, True, True, True),
    }
    heard, measured, inferred, interpreted = table.get(evidence_level, table["mixed"])
    if command == "/forensic":
        # Strict evidentiary route: suppress paralinguistic / interpretive speculation so
        # emotion/age/gender/identity guesses are demoted to undetermined, matching the
        # route summary ("paralinguistic speculation is suppressed").
        interpreted = False
        speculative = False
    if command == "/fiction":
        # Declared fiction grants speculative permission by user intent (AKOÚŌ v0.6
        # command_permission_overrides); evidence categories keep their ladder limits.
        speculative = True
    return {
        "heard_allowed": heard,
        "measured_allowed": measured,
        "inferred_allowed": inferred,
        "interpreted_allowed": interpreted,
        "speculative_allowed": speculative,
        "must_include_undetermined": True,
    }


def evidence_level_for_path(path: str) -> str:
    if not path or not str(path).strip():
        return "none"
    candidate = Path(str(path)).expanduser()
    if candidate.exists():
        return "decoded_audio_metadata"
    return "metadata_only"


def evidence_level_for_report(report: dict[str, Any]) -> str:
    if not isinstance(report, dict):
        return "none"
    has_measured = _has_measured_signal(report)
    has_model = _has_available_model_perception(report)
    has_transcript_or_caption = _has_transcript_or_caption(report)
    if has_measured and has_model:
        return "mixed"
    if has_measured:
        return "measured_signal"
    if has_transcript_or_caption:
        return "transcript_or_caption"
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    if source.get("path") or source.get("sha256"):
        return "decoded_audio_metadata"
    return "metadata_only"


def routing_plan(object_listened_to: str, command: str = "/listen", evidence_level: str = "mixed") -> dict[str, object]:
    route = route_for_command(command)
    roles = ["primary", "secondary", "corrective"]
    mode_chain = []
    for index, mode in enumerate(route.modes):
        role = roles[index] if index < len(roles) else "optional"
        mode_chain.append({"mode": mode, "role": role, "reason": f"{mode} is part of {command} for {evidence_level} evidence."})
    return {
        "object_listened_to": object_listened_to,
        "input_type": "audio_file",
        "route_confidence": "medium",
        "evidence_level": evidence_level,
        "mode_chain": mode_chain,
        "claim_permissions": claim_permissions_for(evidence_level, command),
        "agent_handoff": {
            "summary": route.summary,
            "required_inputs": _required_inputs_for_evidence(evidence_level),
            "forbidden_assumptions": [
                "Do not treat MOSS output as measured signal.",
                "Do not make identity claims from voice-caption dimensions.",
                "Do not infer stereo image, absolute level, or >8 kHz content from MOSS.",
            ],
            "recommended_command": command,
        },
        "stop_conditions": [],
    }


def _required_inputs_for_evidence(evidence_level: str) -> list[str]:
    if evidence_level == "mixed":
        return ["PerceptionReport.json", "audio file path", "DSP feature block", "MOSS-Audio perception block"]
    if evidence_level == "measured_signal":
        return ["audio file path", "DSP feature block"]
    if evidence_level == "transcript_or_caption":
        return ["transcript or caption"]
    if evidence_level == "decoded_audio_metadata":
        return ["audio file path", "decoded audio metadata"]
    if evidence_level == "metadata_only":
        return ["metadata object or path reference"]
    if evidence_level == "prompt_only":
        return ["sound prompt"]
    return []


def _has_measured_signal(report: dict[str, Any]) -> bool:
    dsp = report.get("dsp") if isinstance(report.get("dsp"), dict) else {}
    features = dsp.get("features") if isinstance(dsp.get("features"), dict) else {}
    if features:
        return True
    return any(key in dsp for key in ("durationSeconds", "sampleRate", "channelCount", "sha256"))


def _has_available_model_perception(report: dict[str, Any]) -> bool:
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    if engine.get("unavailable_reason"):
        return False
    if str(engine.get("profile") or "").lower() == "stub":
        return False
    if _has_transcript_or_caption(report):
        return True
    events = report.get("events")
    if isinstance(events, list) and bool(events):
        return True
    speech = report.get("speech") if isinstance(report.get("speech"), dict) else {}
    music = report.get("music") if isinstance(report.get("music"), dict) else {}
    return bool(speech.get("present") or music.get("present"))


def _has_transcript_or_caption(report: dict[str, Any]) -> bool:
    transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
    if transcript.get("present"):
        return True
    caption = report.get("caption") if isinstance(report.get("caption"), dict) else {}
    return bool(caption.get("brief") or caption.get("dense"))
