from __future__ import annotations

from typing import Any

from harness.akouo.routing import evidence_level_for_report, route_for_command, routing_plan
from harness.claim_mapper import map_report_to_claims
from harness.types import LISTENING_MODES, empty_mediations, empty_risks
from oida.accountable import listening_context_for_report


AKOUO_OUTPUT_VERSION = "0.9"


def build_apparatus(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """AKOÚŌ apparatus declaration for Oída or a host audio model."""
    report = report if isinstance(report, dict) else {}
    declared = report.get("apparatus") if isinstance(report.get("apparatus"), dict) else None
    if declared:
        apparatus = dict(declared)
        apparatus.setdefault("substrate", "host_audio_model")
        apparatus.setdefault("perception_sources", ["host-supplied audio perception"])
        apparatus.setdefault(
            "known_blind_spots",
            ["The host did not declare complete acoustic preprocessing and calibration details."],
        )
        return apparatus
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    perception_sources = ["oida DSP feature block (deterministic measurement)"]
    for key, label in (
        ("caption", "MOSS-Audio caption pass"),
        ("events", "MOSS-Audio event timeline"),
        ("transcript", "MOSS-Audio transcript pass"),
        ("speech", "MOSS-Audio speech dimensions"),
        ("music", "MOSS-Audio music pass"),
    ):
        value = report.get(key)
        if isinstance(value, dict) and (value.get("present") or value.get("dense") or value.get("brief")):
            perception_sources.append(label)
        elif isinstance(value, list) and value:
            perception_sources.append(label)
    apparatus: dict[str, Any] = {
        "substrate": "hybrid_agent_stack",
        "perception_sources": perception_sources,
        "sample_rate_hz": 16000,
        "channels": 1,
        "bandwidth_limit_hz": 8000,
        "known_blind_spots": [
            "MOSS-Audio receives 16 kHz mono audio: no stereo-image claims and no content claims above roughly 8 kHz.",
            "No absolute playback or capture level is known to the model.",
            "Model perception is attributable inference, not embodied hearing or measurement; measured claims come from DSP only.",
        ],
    }
    model = engine.get("model")
    if model:
        apparatus["model_ids"] = [str(model)]
    return apparatus


def build_listening_output(
    object_listened_to: str,
    mode: str,
    claims: dict[str, list[dict[str, str]]],
    *,
    recommended_next_mode: str = "undetermined",
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    apparatus = build_apparatus(report)
    sources = ", ".join(str(source) for source in apparatus.get("perception_sources", [])[:2]) or "machine perception"
    risks = empty_risks()
    risks["hallucination"].append("Audio models can overstate acoustic evidence; retain time anchors and uncertainty notes.")
    risks["over_identification"].append("Source, speaker identity, age, accent, and emotion claims require caution.")
    mediations = empty_mediations()
    mediations["technical"].append(f"Declared perception apparatus: {sources}.")
    mediations["computational"].append("PerceptionReport evidence is model output plus any declared measurements, not direct human listening.")
    return {
        "object_listened_to": object_listened_to,
        "input_type": "model_output",
        "listening_mode": mode,
        "akouo_version": AKOUO_OUTPUT_VERSION,
        "apparatus": apparatus,
        "listener": {"type": "agent", "process": "agent_automated"},
        "listening_context": listening_context_for_report(report or {}, apparatus=apparatus),
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
    evidence_level = evidence_level_for_report(report)
    plan = routing_plan(object_listened_to, command=command, evidence_level=evidence_level)
    claims = map_report_to_claims(report, claim_permissions=plan["claim_permissions"], question=question)
    route = route_for_command(command)
    outputs = [
        build_listening_output(
            object_listened_to,
            mode,
            claims,
            recommended_next_mode=route.recommended_next_mode,
            report=report,
        )
        for mode in route.modes
    ]
    skills_called = _skills_called_for_command(command, route.modes)
    return {
        "command": command,
        "object_listened_to": object_listened_to,
        "input_type": "model_output" if report.get("host") else "audio_file",
        "akouo_version": AKOUO_OUTPUT_VERSION,
        "skills_called": skills_called,
        "execution_order": skills_called,
        "routing_plan": plan,
        "outputs": outputs,
        "synthesis": synthesize_claims(claims, command),
        "claim_summary": claims,
        "risks": [
            "Model captions and paralinguistics are attributable inferences or interpretations, not embodied hearings or measurements.",
            "Contradictions between declared measurements and model perception remain undetermined.",
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
        selected = [build_listening_output(object_listened_to, mode, claims, recommended_next_mode=output["recommended_next_mode"], report=report)]

    output["outputs"] = selected
    output["skills_called"] = _skills_called_for_command(command, [mode])
    output["execution_order"] = output["skills_called"]
    output["routing_plan"]["mode_chain"] = [
        {"mode": mode, "role": "primary", "reason": f"{mode} selected explicitly from the oida harness UI."}
    ]
    output["synthesis"] = f"{mode} selected from {command}; " + output["synthesis"]
    return output


def _skills_called_for_command(command: str, modes: list[str]) -> list[str]:
    skills = ["akouo-router", *modes]
    if command in {"/reference", "/method"}:
        skills.append("reference-layer")
    return _dedupe(skills)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def summarize_visible_claims(claims: dict[str, list[dict[str, str]]]) -> list[str]:
    visible: list[str] = []
    for category in ("heard", "measured", "inferred", "interpreted"):
        visible.extend(claim["statement"] for claim in claims.get(category, [])[:3])
    return visible[:10] or ["No positive perception claims were available."]


def synthesize_claims(claims: dict[str, list[dict[str, str]]], command: str) -> str:
    counts = {category: len(items) for category, items in claims.items()}
    return (
        f"{command} produced an AKOUO-disciplined listening result with "
        f"{counts.get('heard', 0)} heard, {counts.get('measured', 0)} measured, "
        f"{counts.get('inferred', 0)} inferred, {counts.get('interpreted', 0)} interpreted, "
        f"{counts.get('speculative', 0)} speculative, and {counts.get('undetermined', 0)} undetermined claims."
    )
