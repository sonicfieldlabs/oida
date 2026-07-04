from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SEGMENT_PREFIX_RE = re.compile(r"^Segment \d+ \[[0-9.]+-[0-9.]+s\]:\s*")

from oida.akouo_skills import RoutePreset, resolve_route_skill_ids, route_preset, skill_manifest
from oida.contracts import (
    AkousmataLinks,
    AudioSegment,
    AudioSourceDescriptor,
    AudioTimeRange,
    ListeningAggregate,
    ListeningArtifact,
    ListeningEvent,
    ListeningHypothesis,
    ListeningNextAction,
    ListeningRouteResult,
    PrivacyMode,
    RawAudioPolicy,
    audio_segment_from_path,
    new_id,
    now_iso,
    to_dict,
)


def listening_event_from_report(
    report: dict[str, Any],
    *,
    command_output: dict[str, Any] | None = None,
    segment: AudioSegment | dict[str, Any] | None = None,
    route_preset_id: str = "basic",
    enabled_skill_ids: list[str] | None = None,
    disabled_skill_ids: list[str] | None = None,
    privacy_mode: PrivacyMode = "session",
    raw_audio_policy: RawAudioPolicy = "external_ref",
) -> ListeningEvent:
    preset = route_preset(route_preset_id)
    selected_skill_ids = resolve_route_skill_ids(
        preset.id,
        enabled_skill_ids=enabled_skill_ids,
        disabled_skill_ids=disabled_skill_ids,
    )
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    path = str(source.get("path") or "")
    if isinstance(segment, AudioSegment):
        audio_segment = segment
    elif isinstance(segment, dict):
        audio_segment = _segment_from_dict(segment)
    elif path:
        audio_segment = audio_segment_from_path(path, privacy_mode=privacy_mode, ephemeral=raw_audio_policy != "external_ref")
    else:
        raise ValueError("cannot build a listening event without a source path or segment")

    routes = _routes_from_command_output(command_output or {}, preset, selected_skill_ids, report)
    if not routes:
        routes = [
            ListeningRouteResult(
                route_id=preset.id,
                route_name=preset.name,
                skill_ids=selected_skill_ids,
                summary=_summary_from_report(report),
                structured={"perception_report": report, "route_preset": preset.id},
                uncertainty=_uncertainty(report),
                suggested_next_routes=_suggested_next_routes(route_preset_id),
            )
        ]

    aggregate = _aggregate(report, command_output or {}, route_preset_id)
    features = _features(report)
    tags = _tags(report, aggregate.primary_tags)
    artifacts = []
    if path:
        artifacts.append(ListeningArtifact(kind="perception_report", label="PerceptionReport source", ref=path))
    return ListeningEvent(
        id=new_id("evt"),
        created_at=now_iso(),
        source=audio_segment.source,
        segment=audio_segment,
        routes=routes,
        aggregate=aggregate,
        features=features,
        memory=AkousmataLinks(),
        artifacts=artifacts,
        tags=tags,
        privacy_mode=privacy_mode,
        raw_audio_policy=raw_audio_policy,
    )


def listening_event_dict(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return to_dict(listening_event_from_report(*args, **kwargs))


def _routes_from_command_output(
    command_output: dict[str, Any],
    preset: RoutePreset,
    selected_skill_ids: list[str],
    report: dict[str, Any],
) -> list[ListeningRouteResult]:
    outputs = command_output.get("outputs") if isinstance(command_output.get("outputs"), list) else []
    if not selected_skill_ids:
        return []
    claim_summary = command_output.get("claim_summary") if isinstance(command_output.get("claim_summary"), dict) else {}
    uncertainty = []
    for claim in claim_summary.get("undetermined", []):
        if isinstance(claim, dict) and claim.get("statement"):
            uncertainty.append(str(claim["statement"]))
    routes = []
    for index, skill_id in enumerate(selected_skill_ids):
        skill = skill_manifest(skill_id)
        output = _output_for_skill(skill.listening_mode, outputs, index)
        routes.append(
            ListeningRouteResult(
                route_id=skill.id,
                route_name=skill.name,
                skill_ids=[skill.id],
                model_observations=[],
                summary=_skill_summary(skill.id, report, command_output, output),
                structured=_skill_structured_output(skill.id, preset, report, command_output, output),
                uncertainty=uncertainty[:8],
                suggested_next_routes=_suggested_next_routes(preset.id),
            )
        )
    return routes


def _output_for_skill(listening_mode: str, outputs: list[Any], index: int) -> dict[str, Any]:
    for output in outputs:
        if isinstance(output, dict) and str(output.get("listening_mode") or "").startswith(listening_mode):
            return output
    dict_outputs = [output for output in outputs if isinstance(output, dict)]
    if dict_outputs:
        return dict_outputs[index % len(dict_outputs)]
    return {}


def _skill_summary(skill_id: str, report: dict[str, Any], command_output: dict[str, Any], output: dict[str, Any]) -> str:
    features = _features(report)
    if skill_id == "spectral-cartographer":
        return _spectral_summary(features)
    if skill_id == "signal-health":
        return _signal_health_summary(features)
    if skill_id == "speech-route":
        transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
        return "Speech route has transcript content." if transcript.get("present") else "Speech route did not find confident transcript content."
    if skill_id == "musicological-listener":
        music = report.get("music") if isinstance(report.get("music"), dict) else {}
        return "Music route found musical content." if music.get("present") else "Music route did not find confident musical structure."
    if skill_id == "extended-spectrum-caution":
        sample_rate = features.get("sample_rate")
        if isinstance(sample_rate, (int, float)) and sample_rate >= 96_000:
            return "Capture sample rate may support extended-spectrum DSP checks, subject to microphone/interface limits."
        return "Extended-spectrum claims are not supported by this capture chain unless higher sample-rate source evidence is supplied."
    if skill_id == "generative-bridge":
        return "Grounded listening observations are ready to be translated into future transformation prompts; no audio is generated here."
    if output.get("main_reading"):
        return str(output["main_reading"])
    return str(command_output.get("synthesis") or _summary_from_report(report))


def _skill_structured_output(
    skill_id: str,
    preset: RoutePreset,
    report: dict[str, Any],
    command_output: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    skill = skill_manifest(skill_id)
    routing = command_output.get("routing_plan") if isinstance(command_output.get("routing_plan"), dict) else {}
    return {
        "skill_id": skill.id,
        "listening_mode": skill.listening_mode,
        "ui_card": skill.ui_card,
        "route_preset": preset.id,
        "akouo_command": preset.akouo_command,
        "akouo_output": output,
        "evidence_level": routing.get("evidence_level"),
        "features": _features(report),
        "claim_summary": command_output.get("claim_summary") if isinstance(command_output.get("claim_summary"), dict) else {},
    }


def _spectral_summary(features: dict[str, Any]) -> str:
    centroid = features.get("spectralCentroidHz")
    rolloff = features.get("spectralRolloffHz")
    if isinstance(centroid, (int, float)) and isinstance(rolloff, (int, float)):
        return f"Spectral centroid is approx {float(centroid):.1f} Hz with rolloff near {float(rolloff):.1f} Hz."
    return "Spectral route has DSP features but no stable centroid/rolloff pair."


def _signal_health_summary(features: dict[str, Any]) -> str:
    clipped = features.get("clippedSampleRatio")
    silence = features.get("silenceRatio")
    issues = []
    if isinstance(clipped, (int, float)) and clipped > 0:
        issues.append("clipping risk")
    if isinstance(silence, (int, float)) and silence > 0.8:
        issues.append("mostly silence")
    if issues:
        return "Signal health: " + ", ".join(issues) + "."
    return "Signal health route found no obvious clipping or silence failure from DSP features."


def _aggregate(report: dict[str, Any], command_output: dict[str, Any], route_preset_id: str) -> ListeningAggregate:
    claim_summary = command_output.get("claim_summary") if isinstance(command_output.get("claim_summary"), dict) else {}
    inferred = _claim_hypotheses(claim_summary, "inferred") + _claim_hypotheses(claim_summary, "interpreted")
    signal_facts = _claim_statements(claim_summary, "measured")[:8] or _signal_facts_from_features(_features(report))
    warnings = _uncertainty(report) + _claim_statements(claim_summary, "undetermined")[:6]
    short_summary = _short_summary(report, command_output)
    return ListeningAggregate(
        title=_event_title(report, short_summary),
        short_summary=short_summary,
        detailed_summary=str(command_output.get("synthesis") or _summary_from_report(report)),
        primary_tags=_primary_tags(report),
        hypotheses=inferred[:8],
        signal_facts=signal_facts,
        warnings=_dedupe(warnings),
        next_actions=[
            ListeningNextAction(id="run_environment", label="Run environment route", route_preset="environment"),
            ListeningNextAction(id="run_signal", label="Run signal route", route_preset="signal"),
            ListeningNextAction(id="remember", label="Remember this sound", route_preset=route_preset_id),
        ],
    )


def _short_summary(report: dict[str, Any], command_output: dict[str, Any]) -> str:
    caption = report.get("caption") if isinstance(report.get("caption"), dict) else {}
    for key in ("brief", "dense"):
        value = caption.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    events = report.get("events") if isinstance(report.get("events"), list) else []
    if events:
        labels = [str(event.get("label")) for event in events[:3] if isinstance(event, dict) and event.get("label")]
        if labels:
            return "Heard events: " + ", ".join(labels)
    signal_caption = _signal_interpretation(report).get("caption")
    if isinstance(signal_caption, str) and signal_caption.strip():
        return signal_caption.strip()
    outputs = command_output.get("outputs") if isinstance(command_output.get("outputs"), list) else []
    for output in outputs:
        if isinstance(output, dict):
            what_appears = output.get("what_appears")
            if isinstance(what_appears, list) and what_appears:
                return str(what_appears[0])
    return "No confident listening summary was produced."


def _summary_from_report(report: dict[str, Any]) -> str:
    parts = []
    caption = report.get("caption") if isinstance(report.get("caption"), dict) else {}
    if caption.get("dense"):
        parts.append(str(caption["dense"]))
    events = report.get("events") if isinstance(report.get("events"), list) else []
    if events:
        parts.append(f"{len(events)} event(s) detected or described.")
    transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
    if transcript.get("present"):
        parts.append("Speech route produced transcript content.")
    if not parts:
        signal_caption = _signal_interpretation(report).get("caption")
        if isinstance(signal_caption, str) and signal_caption.strip():
            parts.append(signal_caption.strip())
    return " ".join(parts) or "Perception report contains DSP metadata and model uncertainty notes."


def _signal_interpretation(report: dict[str, Any]) -> dict[str, Any]:
    signal = report.get("signal_interpretation")
    return signal if isinstance(signal, dict) else {}


def _event_title(report: dict[str, Any], fallback: str) -> str:
    events = report.get("events") if isinstance(report.get("events"), list) else []
    for event in events:
        if isinstance(event, dict) and event.get("label"):
            return _title(str(event["label"]))[:90]
    caption = report.get("caption") if isinstance(report.get("caption"), dict) else {}
    text = caption.get("brief") or caption.get("dense")
    if not text:
        signal_title = _signal_interpretation(report).get("title")
        if isinstance(signal_title, str) and signal_title.strip():
            return signal_title.strip()[:90]
    text = _SEGMENT_PREFIX_RE.sub("", str(text or fallback))
    return _truncate_words(" ".join(text.split()), 88) or "Listening event"


def _truncate_words(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",.;:") + "…"


def _features(report: dict[str, Any]) -> dict[str, Any]:
    dsp = report.get("dsp") if isinstance(report.get("dsp"), dict) else {}
    features = dsp.get("features") if isinstance(dsp.get("features"), dict) else {}
    return {
        "duration_s": dsp.get("durationSeconds"),
        "sample_rate": dsp.get("sampleRate"),
        "channels": dsp.get("channelCount"),
        **features,
    }


def _primary_tags(report: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if (report.get("speech") if isinstance(report.get("speech"), dict) else {}).get("present"):
        tags.append("speech")
    if (report.get("music") if isinstance(report.get("music"), dict) else {}).get("present"):
        tags.append("music")
    events = report.get("events") if isinstance(report.get("events"), list) else []
    for event in events[:4]:
        if isinstance(event, dict) and event.get("label"):
            tags.append(_slug(str(event["label"])))
    classification = _signal_interpretation(report).get("classification")
    if isinstance(classification, str) and classification and classification != "mixed-material":
        tags.append(classification)
    if not tags:
        tags.append("listening-event")
    return _dedupe(tags)


def _tags(report: dict[str, Any], aggregate_tags: list[str]) -> list[str]:
    features = _features(report)
    tags = list(aggregate_tags)
    clipped = features.get("clippedSampleRatio")
    silence = features.get("silenceRatio")
    if isinstance(clipped, (int, float)) and clipped > 0:
        tags.append("clipping")
    if isinstance(silence, (int, float)) and silence > 0.8:
        tags.append("quiet")
    return _dedupe(tags)


def _signal_facts_from_features(features: dict[str, Any]) -> list[str]:
    facts = []
    if isinstance(features.get("duration_s"), (int, float)):
        facts.append(f"Duration is {float(features['duration_s']):.2f} seconds.")
    if isinstance(features.get("sample_rate"), int):
        facts.append(f"Sample rate is {features['sample_rate']} Hz.")
    if isinstance(features.get("peakDbfs"), (int, float)):
        facts.append(f"Peak amplitude is approx {float(features['peakDbfs']):.1f} dBFS.")
    if isinstance(features.get("rmsDbfs"), (int, float)):
        facts.append(f"RMS level is approx {float(features['rmsDbfs']):.1f} dBFS.")
    return facts


def _claim_statements(claims: dict[str, Any], category: str) -> list[str]:
    values = claims.get(category) if isinstance(claims, dict) else []
    return [str(item.get("statement")) for item in values if isinstance(item, dict) and item.get("statement")]


def _claim_hypotheses(claims: dict[str, Any], category: str) -> list[ListeningHypothesis]:
    values = claims.get(category) if isinstance(claims, dict) else []
    return [
        ListeningHypothesis(
            statement=str(item.get("statement")),
            confidence=str(item.get("confidence") or "undetermined"),
            basis=str(item.get("basis")) if item.get("basis") else None,
        )
        for item in values
        if isinstance(item, dict) and item.get("statement")
    ]


def _uncertainty(report: dict[str, Any]) -> list[str]:
    notes = []
    for key in ("model_uncertainty_notes", "forbidden_topics_triggered"):
        values = report.get(key) if isinstance(report.get(key), list) else []
        notes.extend(str(value) for value in values if value)
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    if engine.get("unavailable_reason"):
        notes.append(str(engine["unavailable_reason"]))
    return _dedupe(notes)


def _suggested_next_routes(current: str | None) -> list[str]:
    routes = ["environment", "signal", "music", "speech", "memory"]
    return [route for route in routes if route != current][:4]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = " ".join(str(value).split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _title(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def _slug(value: str) -> str:
    return "-".join("".join(ch.lower() if ch.isalnum() else "-" for ch in value).split("-")).strip("-") or "sound"


def _segment_from_dict(segment: dict[str, Any]) -> AudioSegment:
    data_ref = segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
    path = data_ref.get("uri")
    if not path:
        raise ValueError("segment dict must include data_ref.uri")
    audio_path = Path(str(path))
    source = _source_from_dict(segment.get("source"), audio_path)
    time_range = _time_range_from_dict(segment.get("time_range"))
    captured_at = segment.get("captured_at")
    raw_sha256 = data_ref.get("sha256")
    return audio_segment_from_path(
        audio_path,
        source=source,
        privacy_mode=str(segment.get("privacy_mode") or "session"),  # type: ignore[arg-type]
        raw_sha256=str(raw_sha256) if raw_sha256 else None,
        ephemeral=bool(segment.get("ephemeral", False)),
        user_initiated=bool(segment.get("user_initiated", True)),
        captured_at=str(captured_at) if captured_at else None,
        time_range=time_range,
        metadata=dict(segment.get("metadata")) if isinstance(segment.get("metadata"), dict) else None,
    )


def _source_from_dict(value: Any, path: Path) -> AudioSourceDescriptor | None:
    if not isinstance(value, dict):
        return None
    source_type = str(value.get("type") or "file")
    if source_type not in {"live_input", "system_output", "file", "buffer", "generated", "external_stream"}:
        source_type = "file"
    details = value.get("details") if isinstance(value.get("details"), dict) else {}
    return AudioSourceDescriptor(
        type=source_type,  # type: ignore[arg-type]
        label=str(value.get("label") or path.name or "Audio file"),
        device_id=str(value.get("device_id")) if value.get("device_id") else None,
        platform=str(value.get("platform")) if value.get("platform") else None,
        supported=bool(value.get("supported", True)),
        status=str(value.get("status") or "ready"),
        details={**details, "path": str(path.expanduser().resolve())},
    )


def _time_range_from_dict(value: Any) -> AudioTimeRange | None:
    if not isinstance(value, dict):
        return None
    start = value.get("start_ms")
    end = value.get("end_ms")
    if isinstance(start, int) and isinstance(end, int):
        return AudioTimeRange(start_ms=start, end_ms=end)
    return None
