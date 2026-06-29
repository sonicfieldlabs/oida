from __future__ import annotations

from dataclasses import dataclass

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
    "/tech": CommandRoute("/tech", ["signal-inspection-listening", "transductive-media-listening"], "transductive-media-listening", "Technical inspection with model-mediation correction."),
    "/voice": CommandRoute("/voice", ["voice-speech-listening", "transductive-media-listening", "accessibility-normative-listening", "critical-political-listening"], "accessibility-normative-listening", "Voice/speech pass with identity and consent cautions."),
    "/field": CommandRoute("/field", ["ecological-posthuman-listening", "transductive-media-listening", "critical-political-listening", "material-event-listening"], "material-event-listening", "Field-recording route with mediation and situated-listening cautions."),
    "/forensic": CommandRoute("/forensic", ["signal-inspection-listening", "forensic-archival-listening", "critical-political-listening"], "critical-political-listening", "Strict evidentiary route; paralinguistic speculation is suppressed."),
    "/one-sound-many-ears": CommandRoute("/one-sound-many-ears", LISTENING_MODES, "undetermined", "Comparative flagship: all thirteen listening modes read one PerceptionReport."),
}


def available_harness_controls() -> dict[str, object]:
    return {
        "commands": [
            {"command": route.command, "modes": route.modes, "summary": route.summary, "recommended_next_mode": route.recommended_next_mode}
            for route in COMMAND_ROUTES.values()
        ],
        "modes": LISTENING_MODES,
        "evidence_level": "mixed",
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
    return {
        "heard_allowed": heard,
        "measured_allowed": measured,
        "inferred_allowed": inferred,
        "interpreted_allowed": interpreted,
        "speculative_allowed": speculative,
        "must_include_undetermined": True,
    }


def routing_plan(object_listened_to: str, command: str = "/listen", evidence_level: str = "mixed") -> dict[str, object]:
    route = route_for_command(command)
    roles = ["primary", "secondary", "corrective"]
    mode_chain = []
    for index, mode in enumerate(route.modes):
        role = roles[index] if index < len(roles) else "optional"
        mode_chain.append({"mode": mode, "role": role, "reason": f"{mode} is part of {command} for mixed MOSS + DSP evidence."})
    return {
        "object_listened_to": object_listened_to,
        "input_type": "audio_file",
        "route_confidence": "medium",
        "evidence_level": evidence_level,
        "mode_chain": mode_chain,
        "claim_permissions": claim_permissions_for(evidence_level, command),
        "agent_handoff": {
            "summary": route.summary,
            "required_inputs": ["PerceptionReport.json", "audio file path"],
            "forbidden_assumptions": [
                "Do not treat MOSS output as measured signal.",
                "Do not make identity claims from voice-caption dimensions.",
                "Do not infer stereo image, absolute level, or >8 kHz content from MOSS.",
            ],
            "recommended_command": command,
        },
        "stop_conditions": [],
    }
