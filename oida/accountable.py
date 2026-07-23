"""Accountable-listening declarations shared by OÍDA producers.

AKOÚŌ owns the semantic vocabulary; OÍDA declares the runtime facts of its
actual hearing. Covenant, position, apparatus, claim, and action authority are
kept separate. A model observation never becomes a measurement merely because
it arrived from an audio-capable model.
"""
from __future__ import annotations

from typing import Any

LISTENING_CONTEXT_CONTRACT = "akouo/listening-context/v1"
LISTENING_EVENT_CONTRACT = "oida/listening-event/v0.2"


def listening_context_for_report(
    report: dict[str, Any],
    *,
    apparatus: dict[str, Any] | None = None,
    segment: Any | None = None,
    raw_audio_policy: str = "not_stored",
    privacy_mode: str = "session",
    action_mode: str = "observe_only",
    action_scopes: list[str] | None = None,
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
                else "The host heard the audio; OÍDA received derived observations only."
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
            "role": "host perceptual listener",
            "report_ref": "#/host_observations",
        })
        participants.append({
            "id": "oida-gateway",
            "type": "agent",
            "role": "routing, evidence audit, and memory boundary",
            "report_ref": None,
        })
    else:
        participants.append({
            "id": "oida-local-listener",
            "type": "agent",
            "role": "local DSP and configured model listener",
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
