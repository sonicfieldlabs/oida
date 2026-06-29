from __future__ import annotations

from typing import Any

from harness.akouo.routing import route_for_command, routing_plan
from harness.claim_mapper import map_report_to_claims
from harness.types import LISTENING_MODES, empty_mediations, empty_risks


def build_listening_output(
    object_listened_to: str,
    mode: str,
    claims: dict[str, list[dict[str, str]]],
    *,
    recommended_next_mode: str = "undetermined",
) -> dict[str, Any]:
    risks = empty_risks()
    risks["hallucination"].append("MOSS-Audio can overstate acoustic evidence; retain time anchors and uncertainty notes.")
    risks["over_identification"].append("Source, speaker identity, age, accent, and emotion claims require caution.")
    mediations = empty_mediations()
    mediations["technical"].append("MOSS-Audio receives 16 kHz mono audio; DSP supplies measured signal features.")
    mediations["computational"].append("PerceptionReport evidence is model output plus deterministic DSP, not direct human listening.")
    return {
        "object_listened_to": object_listened_to,
        "input_type": "model_output",
        "listening_mode": mode,
        "listening_claims": claims,
        "what_appears": summarize_visible_claims(claims),
        "what_remains_hidden": [claim["statement"] for claim in claims.get("undetermined", [])[:8]],
        "mediations": mediations,
        "risks": risks,
        "main_reading": f"{mode} reads the PerceptionReport under AKOUO claim permissions.",
        "alternative_reading": "A stricter reading should down-rank any uncorroborated MOSS caption or paralinguistic claim.",
        "recommended_next_mode": recommended_next_mode,
    }


def build_command_output(report: dict[str, Any], command: str = "/listen", question: str | None = None) -> dict[str, Any]:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    object_listened_to = str(source.get("path") or "audio file")
    plan = routing_plan(object_listened_to, command=command, evidence_level="mixed")
    claims = map_report_to_claims(report, claim_permissions=plan["claim_permissions"], question=question)
    route = route_for_command(command)
    outputs = [
        build_listening_output(
            object_listened_to,
            mode,
            claims,
            recommended_next_mode=route.recommended_next_mode,
        )
        for mode in route.modes
    ]
    skills_called = ["akouo-router", *route.modes]
    return {
        "command": command,
        "object_listened_to": object_listened_to,
        "input_type": "audio_file",
        "skills_called": skills_called,
        "execution_order": skills_called,
        "routing_plan": plan,
        "outputs": outputs,
        "synthesis": synthesize_claims(claims, command),
        "claim_summary": claims,
        "risks": [
            "MOSS captions and paralinguistics are machine-heard evidence, not measurements.",
            "Contradictions between DSP and MOSS remain undetermined.",
        ],
        "recommended_next_mode": route.recommended_next_mode,
    }


def build_harness_output(report: dict[str, Any], command: str = "/listen", mode: str | None = None, question: str | None = None) -> dict[str, Any]:
    output = build_command_output(report, command=command, question=question)
    if not mode or mode == "route":
        return output
    if mode not in LISTENING_MODES:
        valid = ", ".join(LISTENING_MODES)
        raise ValueError(f"unknown AKOUO listening mode: {mode}. Valid modes: {valid}")

    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    object_listened_to = str(source.get("path") or "audio file")
    claims = output["claim_summary"]
    selected = [item for item in output["outputs"] if item.get("listening_mode") == mode]
    if not selected:
        selected = [build_listening_output(object_listened_to, mode, claims, recommended_next_mode=output["recommended_next_mode"])]

    output["outputs"] = selected
    output["skills_called"] = ["akouo-router", mode]
    output["execution_order"] = ["akouo-router", mode]
    output["routing_plan"]["mode_chain"] = [
        {"mode": mode, "role": "primary", "reason": f"{mode} selected explicitly from the AEAR harness UI."}
    ]
    output["synthesis"] = f"{mode} selected from {command}; " + output["synthesis"]
    return output


def summarize_visible_claims(claims: dict[str, list[dict[str, str]]]) -> list[str]:
    visible: list[str] = []
    for category in ("heard", "measured", "inferred", "interpreted"):
        visible.extend(claim["statement"] for claim in claims.get(category, [])[:3])
    return visible[:10] or ["No positive perception claims were available."]


def synthesize_claims(claims: dict[str, list[dict[str, str]]], command: str) -> str:
    counts = {category: len(items) for category, items in claims.items()}
    return (
        f"{command} produced a mixed MOSS + DSP listening result with "
        f"{counts.get('heard', 0)} heard, {counts.get('measured', 0)} measured, "
        f"{counts.get('inferred', 0)} inferred, {counts.get('interpreted', 0)} interpreted, "
        f"{counts.get('speculative', 0)} speculative, and {counts.get('undetermined', 0)} undetermined claims."
    )
