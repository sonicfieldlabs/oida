"""Accountable-listening declarations shared by OÍDA producers.

AKOÚŌ owns the semantic vocabulary; OÍDA declares the runtime facts of its
actual hearing. Covenant, position, apparatus, claim, and action authority are
kept separate. A model observation never becomes a measurement merely because
it arrived from an audio-capable model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

LISTENING_CONTEXT_CONTRACT = "akouo/listening-context/v2"
LISTENING_EVENT_CONTRACT = "oida/listening-event/v0.3"
ROUTE_OUTCOME_CONTRACT = "oida/route-outcome/v0.1"


def accountable_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def accountable_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def route_decision(
    *,
    gate: str,
    outcome: str,
    subject: str,
    reason: str,
    actor: str = "oida-gateway",
    decided_at: str | None = None,
    decision_id: str | None = None,
    covenant_ref: str | None = None,
    granted_by: str | None = None,
    requires_confirmation: bool = False,
    reversible: bool = True,
    receipt_ref: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Build one AKOÚŌ v0.9 gate decision without implying a hearing."""

    authority: dict[str, Any] = {
        "mode": "observe_only",
        "actor": actor,
        "requires_confirmation": bool(requires_confirmation),
        "reversible": bool(reversible),
    }
    if covenant_ref:
        authority["covenant_ref"] = covenant_ref
    if granted_by:
        authority["granted_by"] = granted_by
    decision: dict[str, Any] = {
        "id": decision_id or accountable_id("decision"),
        "gate": gate,
        "outcome": outcome,
        "subject": subject,
        "reason": reason,
        "decided_at": decided_at or accountable_now(),
        "authority": authority,
    }
    if receipt_ref:
        decision["receipt_ref"] = receipt_ref
    if note:
        decision["note"] = note
    return decision


def refusal_outcome(
    *,
    perception_path: str,
    subject: str,
    reason: str,
    gate: str,
    covenant: dict[str, Any] | None,
    remember_requested: bool,
) -> dict[str, Any]:
    """Return a complete pre-listening refusal, not an empty listening event."""

    created_at = accountable_now()
    covenant_id = str((covenant or {}).get("id") or "oida-gateway")
    receipt = {
        "id": accountable_id("receipt"),
        "created_at": created_at,
        "actor": "oida-covenant-gate",
        "result": "listening did not begin",
        "recovery": "Revise or deactivate the governing rule, then make a new listening request.",
    }
    decision = route_decision(
        gate=gate,
        outcome="refuse",
        subject=subject,
        reason=reason,
        actor="oida-covenant-gate",
        decided_at=created_at,
        covenant_ref="#/covenant" if covenant else None,
        granted_by=f"adopted covenant {covenant_id}" if covenant else "gateway input policy",
        receipt_ref="#/receipt",
        note="No listening pass or acoustic claim was created.",
    )
    outcome: dict[str, Any] = {
        "id": accountable_id("outcome"),
        "contract": ROUTE_OUTCOME_CONTRACT,
        "completed_at": created_at,
        "status": "complete",
        "perception_path": perception_path,
        "subject": subject,
        "route_decision": decision,
        "honest_absences": [{
            "kind": "refused",
            "subject": subject,
            "attributed_to": covenant_id,
            "count": 1,
            "note": "The ear closed before listening; no audio content is represented here.",
        }],
        "receipt": receipt,
        "memory": {
            "requested": bool(remember_requested),
            "status": "pending" if remember_requested else "not_requested",
            "akousma_id": None,
        },
    }
    if covenant:
        outcome["covenant"] = dict(covenant)
    return outcome


def listening_context_for_report(
    report: dict[str, Any],
    *,
    apparatus: dict[str, Any] | None = None,
    segment: Any | None = None,
    raw_audio_policy: str = "not_stored",
    privacy_mode: str = "session",
    action_mode: str = "observe_only",
    action_scopes: list[str] | None = None,
    route_ids: list[str] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    route_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe the actual boundary through which this OÍDA report listened."""
    apparatus = dict(apparatus or (report.get("apparatus") if isinstance(report.get("apparatus"), dict) else {}))
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    host = report.get("host") if isinstance(report.get("host"), dict) else {}
    dsp = report.get("dsp") if isinstance(report.get("dsp"), dict) else {}
    transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
    identity = report.get("listening_identity") if isinstance(report.get("listening_identity"), dict) else {}

    host_supplied = bool(host)
    direct_audio_to_oida = not host_supplied or bool(source.get("audio_available_to_oida"))
    # Duration, channel count, and nominal sample rate are capture metadata.
    # They do not open a measurement aperture by themselves.
    has_dsp = bool(dsp.get("features"))
    has_model_observation = bool(report.get("host_observations")) or any(
        _has_content(report.get(key)) for key in ("caption", "events", "speech", "music")
    )

    apertures: list[dict[str, Any]] = [
        {
            "id": "oida-direct-audio",
            "kind": "direct_audio",
            "status": "available" if direct_audio_to_oida else "unavailable",
            "description": (
                "OÍDA could read the local audio source."
                if direct_audio_to_oida
                else "The host model processed the audio; OÍDA received derived observations only."
            ),
            "limits": _string_list(apparatus.get("known_blind_spots"))[:8],
        },
        {
            "id": "oida-signal-measurement",
            "kind": "signal_measurement",
            "status": "available" if has_dsp else "unavailable",
            "description": (
                "The host declared a distinct DSP feature block; OÍDA preserves its provenance."
                if host_supplied
                else "Deterministic local DSP values are the measurement aperture."
            ),
            "limits": [
                "No absolute acoustic level is inferred without calibration.",
                *(["Host-declared measurements were not recomputed by OÍDA."] if host_supplied and has_dsp else []),
            ],
        },
        {
            "id": "oida-model-observation",
            "kind": "model_observation",
            "status": "degraded" if has_model_observation else "unavailable",
            "description": "Audio-model or host-model observations remain machine observations, not measurements.",
            "limits": _string_list(apparatus.get("known_blind_spots"))[:8],
        },
        {
            "id": "oida-source-metadata",
            "kind": "metadata",
            "status": "available" if source else "unavailable",
            "description": "Source and capture metadata supplied at the gateway boundary.",
            "limits": ["Metadata describes the supplied object; it does not prove source identity."],
        },
        {
            "id": "oida-transcript",
            "kind": "transcript",
            "status": "available" if transcript.get("present") else "unavailable",
            "description": "Transcript evidence is kept distinct from acoustic and DSP evidence.",
            "limits": _string_list(transcript.get("notes"))[:8],
        },
    ]

    sources: list[str] = []
    if direct_audio_to_oida:
        sources.append("audio")
    if has_dsp:
        sources.append("dsp")
    if source:
        sources.append("metadata")
    if has_model_observation:
        sources.append("model")
    if transcript.get("present"):
        sources.append("transcript")
    if not sources:
        sources.append("none")

    participants: list[dict[str, Any]] = []
    if host_supplied:
        participants.append({
            "id": str(host.get("id") or "generic-host"),
            "type": "agent",
            "role": "host model observer",
            "standing": "listener",
            "report_ref": "#/host_observations",
        })
        participants.append({
            "id": "oida-gateway",
            "type": "agent",
            "role": "routing, evidence audit, and memory boundary",
            "standing": "operator",
            "report_ref": None,
        })
    else:
        participants.append({
            "id": "oida-local-listener",
            "type": "agent",
            "role": "local DSP and configured model observer",
            "standing": "listener",
            "report_ref": "#/",
        })

    honest_absences: list[dict[str, Any]] = []
    if host_supplied and not direct_audio_to_oida:
        honest_absences.append({
            "kind": "unavailable",
            "subject": "raw audio at the OÍDA gateway",
            "attributed_to": "host-perception boundary",
            "count": 1,
            "note": "OÍDA received derived host observations.",
        })
    if raw_audio_policy in {"not_stored", "temp"} or privacy_mode == "incognito":
        honest_absences.append({
            "kind": "not_retained",
            "subject": "raw audio",
            "attributed_to": "OÍDA raw-audio policy",
            "count": 1,
            "note": f"effective policy: {raw_audio_policy}",
        })

    duration = _duration_seconds(source, dsp, segment)
    scales = ["frame", "event"]
    if duration is None or duration >= 1:
        scales.append("scene")
    if duration is not None and duration >= 300:
        scales.append("session")

    limitations = _string_list(apparatus.get("known_blind_spots"))
    if not limitations:
        limitations = ["The complete capture, playback, and preprocessing chain was not declared."]
    identity_ref = identity.get("sha256") if isinstance(identity.get("sha256"), str) else None
    decision_items = [dict(item) for item in route_decisions or []]
    if not decision_items:
        decision_items.append(route_decision(
            gate="input",
            outcome="proceed",
            subject=f"{str(source.get('type') or 'audio')} listening request",
            reason="The input, covenant, and route gates allowed this hearing to proceed.",
            granted_by="current gateway request",
        ))
    pass_started = started_at or accountable_now()
    pass_completed = completed_at or accountable_now()
    listening_passes = [{
        "id": accountable_id("pass"),
        "listener_id": str(host.get("id") or "oida-local-listener"),
        "route": _dedupe(route_ids or ["basic"]),
        "started_at": pass_started,
        "completed_at": pass_completed,
        "moment": {
            "relation": _moment_relation(source, segment),
            "scales": _dedupe(scales),
            "time_range": _time_range(source, dsp, segment),
        },
        "source_refs": ["#/source", "#/segment"],
        "claim_refs": ["#/routes"],
        "decision_refs": [str(item["id"]) for item in decision_items if item.get("id")],
        "influenced_by": [],
    }]
    return {
        "contract": LISTENING_CONTEXT_CONTRACT,
        "position": {
            "relation_to_object": (
                "host-supplied audio observation audited and routed by OÍDA"
                if host_supplied
                else "local OÍDA inspection of a supplied or captured audio object"
            ),
            "situation": str(source.get("type") or "unknown source"),
            "listening_identity_ref": identity_ref,
            "limitations": limitations,
        },
        "apertures": apertures,
        "auditory_scales": _dedupe(scales),
        "sources_of_listening": _dedupe(sources),
        "participants": participants,
        "action_authority": {
            "mode": action_mode,
            "scopes": action_scopes or ["describe", "measure_local_signal", "recommend_next_listening"],
            "granted_by": "current gateway request",
            "expires_at": None,
            "requires_confirmation": True,
            "reversible": True,
        },
        "honest_absences": honest_absences,
        "listening_passes": listening_passes,
        "route_decisions": decision_items,
    }


def listening_provenance_for_report(
    report: dict[str, Any],
    *,
    apparatus: dict[str, Any] | None = None,
    segment: Any | None = None,
    route_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Name the streams and consequential cuts that conditioned a hearing."""

    apparatus = dict(apparatus or (report.get("apparatus") if isinstance(report.get("apparatus"), dict) else {}))
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    host = report.get("host") if isinstance(report.get("host"), dict) else {}
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    dsp = report.get("dsp") if isinstance(report.get("dsp"), dict) else {}
    transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
    host_supplied = bool(host)
    direct_audio = not host_supplied or bool(source.get("audio_available_to_oida"))
    sources: list[dict[str, Any]] = []

    def add_source(
        source_id: str,
        kind: str,
        label: str,
        *,
        disclosure_status: str,
        provider: str | None = None,
        model_id: str | None = None,
        limitations: list[str] | None = None,
    ) -> None:
        sources.append({
            "id": source_id,
            "kind": kind,
            "label": label,
            "provider": provider,
            "model_id": model_id,
            "revision": None,
            "jurisdiction": None,
            "disclosure_status": disclosure_status,
            "limitations": list(limitations or []),
        })

    blind_spots = _string_list(apparatus.get("known_blind_spots"))[:8]
    if direct_audio:
        add_source(
            "source-audio",
            "audio",
            str(source.get("label") or "supplied audio"),
            disclosure_status="partial",
            limitations=blind_spots,
        )
    if dsp.get("features"):
        add_source(
            "source-dsp",
            "dsp",
            "declared signal measurements",
            disclosure_status="known",
            limitations=["Absolute acoustic level is unsupported without calibration."],
        )
    if source:
        add_source(
            "source-metadata",
            "metadata",
            "gateway source metadata",
            disclosure_status="partial",
            limitations=["Metadata does not prove source identity."],
        )
    has_model = bool(report.get("host_observations")) or any(
        _has_content(report.get(key)) for key in ("caption", "events", "speech", "music")
    )
    if has_model:
        model_id = str(host.get("model") or engine.get("model") or "undeclared-model")
        provider = str(host.get("provider") or host.get("id") or engine.get("daemon") or "oida")
        add_source(
            "source-model",
            "model",
            f"{provider} model observation",
            disclosure_status="partial",
            provider=provider,
            model_id=model_id,
            limitations=blind_spots,
        )
    if transcript.get("present"):
        add_source(
            "source-transcript",
            "transcript",
            "attributed transcript text",
            disclosure_status="partial",
            limitations=_string_list(transcript.get("notes"))[:8],
        )
    if not sources:
        add_source(
            "source-none",
            "none",
            "no evidence stream declared",
            disclosure_status="not_applicable",
        )

    cuts: list[dict[str, Any]] = [{
        "id": accountable_id("cut"),
        "stage": "routing",
        "operation": "select accountable listening route",
        "actor": "oida-gateway",
        "basis": "current request and AKOÚŌ route preset",
        "effect": f"activated routes: {', '.join(_dedupe(route_ids or ['basic']))}",
        "reversible": True,
        "source_ref": None,
    }]
    if host_supplied and not direct_audio:
        cuts.append({
            "id": accountable_id("cut"),
            "stage": "selection",
            "operation": "accept host-derived observations without transferring raw audio",
            "actor": str(host.get("id") or "host"),
            "basis": "host-perception boundary",
            "effect": "Oída can audit the attributed report but cannot inspect the original signal.",
            "reversible": False,
            "source_ref": "source-model" if has_model else None,
        })
    corpus_lineage = [
        {
            "source_ref": item["id"],
            "disclosure_status": "unknown",
            "corpus_refs": [],
            "fine_tune_refs": [],
            "limitations": ["Training and fine-tuning lineage was not disclosed at this listening boundary."],
        }
        for item in sources
        if item["kind"] == "model"
    ]
    return {
        "listening_sources": sources,
        "cuts": cuts,
        "corpus_lineage": corpus_lineage,
    }


def attributable_disagreements(command_output: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only disagreements that already carry participant positions.

    Free-form contradictions are not upgraded into structured disagreement;
    doing so would invent attribution.
    """
    value = command_output.get("disagreements")
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict) and isinstance(item.get("positions"), list)]


def _duration_seconds(source: dict[str, Any], dsp: dict[str, Any], segment: Any | None) -> float | None:
    for value in (source.get("duration_s"), dsp.get("durationSeconds")):
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
    value = getattr(segment, "duration_ms", None)
    if isinstance(value, (int, float)):
        return max(0.0, float(value) / 1000.0)
    return None


def _moment_relation(source: dict[str, Any], segment: Any | None) -> str:
    segment_source = getattr(segment, "source", None)
    source_type = str(source.get("type") or getattr(segment_source, "type", ""))
    if source_type in {"live_input", "system_output", "buffer"}:
        return "live"
    if source_type == "file":
        return "past_capture"
    if source_type == "external_stream":
        return "archive"
    return "unknown"


def _time_range(
    source: dict[str, Any], dsp: dict[str, Any], segment: Any | None
) -> dict[str, float] | None:
    duration = _duration_seconds(source, dsp, segment)
    if duration is None:
        return None
    return {"start_s": 0.0, "end_s": duration}


def _has_content(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, dict):
        return bool(value.get("present") or value.get("dense") or value.get("brief") or value.get("label"))
    return bool(value)


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
