from __future__ import annotations

import re
from copy import deepcopy
from functools import partial
from typing import Any

from harness.types import CLAIM_CATEGORIES, empty_claim_taxonomy

FORBIDDEN_QUERY_TERMS = [
    ("above 8 khz", "MOSS-Audio cannot hear above roughly 8 kHz because it receives 16 kHz mono input."),
    (">8 khz", "MOSS-Audio cannot hear above roughly 8 kHz because it receives 16 kHz mono input."),
    ("ultrasonic", "MOSS-Audio cannot hear ultrasonic content; use DSP where the native sample rate permits it."),
    ("stereo image", "MOSS-Audio receives mono audio; stereo-image claims require DSP."),
    ("stereo width", "MOSS-Audio receives mono audio; stereo-width claims require DSP."),
    ("stereo field", "MOSS-Audio receives mono audio; stereo-field claims require DSP."),
    ("spatial width", "MOSS-Audio receives mono audio; spatial-width claims require DSP or spatial metadata."),
    ("absolute level", "MOSS-Audio cannot know absolute physical playback or capture level."),
]


# Output-side forbidden matcher. MOSS hears 16 kHz mono, so any MOSS-*generated* claim
# about stereo image / spatial position, content above ~8 kHz, or absolute physical level
# is unsupported and must be demoted to `undetermined`. This is deliberately broader than
# the question matcher above (it catches paraphrases the 8-term list misses) and is the
# load-bearing suppressor applied to caption / event / speech / music text. Relative
# descriptors ("bright", "high-pitched", "loud") are intentionally NOT matched: they are
# legitimate perceptual captions, not absolute-physical claims.
_SPATIAL_REASON = "MOSS-Audio receives 16 kHz mono audio; stereo-image / spatial-position claims require DSP."
_HIGHFREQ_REASON = "MOSS-Audio receives 16 kHz mono audio and cannot hear content above roughly 8 kHz; verify with DSP."
_LEVEL_REASON = "MOSS-Audio cannot know absolute physical level; absolute-level claims require DSP or capture metadata."

_FORBIDDEN_OUTPUT_PHRASES: list[tuple[str, str]] = [
    ("stereo image", _SPATIAL_REASON),
    ("stereo field", _SPATIAL_REASON),
    ("stereo width", _SPATIAL_REASON),
    ("stereo spread", _SPATIAL_REASON),
    ("spatial width", _SPATIAL_REASON),
    ("spatial image", _SPATIAL_REASON),
    ("soundstage", _SPATIAL_REASON),
    ("sound stage", _SPATIAL_REASON),
    ("left channel", _SPATIAL_REASON),
    ("right channel", _SPATIAL_REASON),
    ("hard left", _SPATIAL_REASON),
    ("hard right", _SPATIAL_REASON),
    ("panned", _SPATIAL_REASON),
    ("panning", _SPATIAL_REASON),
    ("pan left", _SPATIAL_REASON),
    ("pan right", _SPATIAL_REASON),
    ("binaural", _SPATIAL_REASON),
    ("surround sound", _SPATIAL_REASON),
    ("ultrasonic", _HIGHFREQ_REASON),
    ("above 8 khz", _HIGHFREQ_REASON),
    ("above 8khz", _HIGHFREQ_REASON),
    (">8 khz", _HIGHFREQ_REASON),
    ("absolute level", _LEVEL_REASON),
    ("absolute loudness", _LEVEL_REASON),
    ("sound pressure level", _LEVEL_REASON),
    ("playback level", _LEVEL_REASON),
    ("physical level", _LEVEL_REASON),
    ("capture level", _LEVEL_REASON),
]

_STEREO_RE = re.compile(r"\bstereo\b")
_SPL_RE = re.compile(r"\bspl\b")
_KHZ_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*k\s*hz")


DEFAULT_CLAIM_PERMISSIONS = {
    "heard_allowed": True,
    "measured_allowed": True,
    "inferred_allowed": True,
    "interpreted_allowed": True,
    "speculative_allowed": False,
    "must_include_undetermined": True,
}


def map_report_to_claims(
    report: dict[str, Any],
    *,
    claim_permissions: dict[str, bool] | None = None,
    question: str | None = None,
) -> dict[str, list[dict[str, str]]]:
    permissions = {**DEFAULT_CLAIM_PERMISSIONS, **(claim_permissions or {})}
    claims = empty_claim_taxonomy()
    model_name = str(report.get("engine", {}).get("model") or "MOSS-Audio")

    _map_dsp(report, claims)
    _map_host_observations(report, claims)
    _map_signal_interpretation(report, claims)
    _map_transcript(report, claims, model_name)
    _map_events(report, claims, model_name)
    _map_caption(report, claims, model_name)
    _map_speech(report, claims, model_name)
    _map_music(report, claims, model_name)
    _map_uncertainty(report, claims)
    _map_forbidden_query(question, claims, report)
    _filter_permissions(claims, permissions)

    if permissions.get("must_include_undetermined", True) and not claims["undetermined"]:
        claims["undetermined"].append(
            {
                "statement": "Capture chain, listener position, cultural context, and unmeasured provenance remain undetermined.",
                "confidence": "undetermined",
                "basis": "AKOUO Evidence Ladder requires explicit undetermined claims for mixed machine-listening reports.",
            }
        )
    return claims


def _map_host_observations(report: dict[str, Any], claims: dict[str, list[dict[str, str]]]) -> None:
    """Preserve claims supplied by an audio-capable host without laundering them.

    A model-written number is not a measurement. A host may use ``measured``
    only when it identifies DSP, metadata, or human measurement as the source;
    other attempted measurements are demoted and made explicit.
    """
    observations = report.get("host_observations")
    if not isinstance(observations, list):
        return
    for item in observations:
        if not isinstance(item, dict) or not item.get("statement"):
            continue
        category = str(item.get("category") or "heard")
        source = str(item.get("source") or "model")
        basis = str(item.get("basis") or "host audio perception")
        confidence = str(item.get("confidence") or "medium")
        time_range = item.get("time_range") if isinstance(item.get("time_range"), dict) else None
        if category == "measured" and source not in {"dsp", "metadata", "human"}:
            _add(
                claims,
                "inferred",
                str(item["statement"]),
                confidence,
                f"{basis}; demoted because model perception is not measurement",
                source=source,
                time_range=time_range,
            )
            _add(
                claims,
                "undetermined",
                f"Measurement status is unsupported for host claim: {item['statement']}",
                "undetermined",
                "No DSP, metadata, or declared human measurement source was supplied.",
                source="context",
                time_range=time_range,
            )
            continue
        if category not in CLAIM_CATEGORIES:
            category = "undetermined"
        _add(
            claims,
            category,
            str(item["statement"]),
            confidence,
            basis,
            source=source,
            time_range=time_range,
        )


def _map_dsp(report: dict[str, Any], claims: dict[str, list[dict[str, str]]]) -> None:
    add = partial(_add, source="dsp")
    dsp = report.get("dsp") if isinstance(report.get("dsp"), dict) else {}
    features = dsp.get("features") if isinstance(dsp.get("features"), dict) else {}
    basis = "oida DSP module, AKOUO audioAdapter parity port"
    duration = dsp.get("durationSeconds")
    sample_rate = dsp.get("sampleRate")
    channels = dsp.get("channelCount")
    if isinstance(duration, (int, float)):
        add(claims, "measured", f"Duration is {duration:.2f} seconds.", "high", "Decoded audio metadata")
    if isinstance(sample_rate, int):
        add(claims, "measured", f"Sample rate is {sample_rate} Hz.", "high", "Decoded audio metadata")
    if isinstance(channels, int):
        add(claims, "measured", f"Channel count is {channels}.", "high", "Decoded audio metadata")
    numeric_templates = [
        ("peakDbfs", "Peak amplitude is approx {value:.1f} dBFS."),
        ("rmsDbfs", "RMS level is approx {value:.1f} dBFS."),
        ("crestFactorDb", "Crest factor is approx {value:.1f} dB."),
        ("integratedLufs", "Integrated loudness is approx {value:.1f} LUFS."),
        ("loudnessRangeLu", "Loudness range is approx {value:.1f} LU."),
        ("zeroCrossingRate", "Zero-crossing rate on channel 1 is approx {value:.0f} crossings per second."),
        ("spectralCentroidHz", "Spectral centroid is approx {value:.0f} Hz."),
        ("spectralRolloffHz", "85% spectral rolloff is approx {value:.0f} Hz."),
        ("spectralFlatness", "Spectral flatness is approx {value:.3f}."),
        ("onsetDensityPerSec", "Onset density is approx {value:.2f} onsets per second."),
        ("bpmCandidate", "A pulse near {value:.1f} BPM is one possible tempo reading from inter-onset intervals."),
        ("interChannelCorrelation", "Inter-channel correlation is approx {value:.2f}."),
        ("stereoWidth", "Approx {value:.2f} of stereo energy sits in the side signal."),
        ("channelBalanceDb", "Channel balance is approx {value:.1f} dB, positive means channel 1 is louder."),
    ]
    for key, template in numeric_templates:
        value = features.get(key)
        if isinstance(value, (int, float)):
            confidence = "low" if key == "bpmCandidate" else "medium"
            add(claims, "measured", template.format(value=float(value)), confidence, basis)
    silence = features.get("silenceRatio")
    if isinstance(silence, (int, float)):
        add(claims, "measured", f"Approx {silence * 100:.1f}% of frames sit below -60 dBFS.", "medium", basis)
    clipped = features.get("clippedSampleRatio")
    if isinstance(clipped, (int, float)) and clipped > 0:
        add(claims, "measured", f"Approx {clipped * 100:.3f}% of samples sit near full scale.", "medium", basis)
    band_energy = features.get("bandEnergy")
    if isinstance(band_energy, dict):
        parts = ", ".join(f"{key} {float(value) * 100:.0f}%" for key, value in band_energy.items() if isinstance(value, (int, float)))
        if parts:
            add(claims, "measured", f"Band energy distribution is approx {parts}.", "medium", basis)


def _map_signal_interpretation(report: dict[str, Any], claims: dict[str, list[dict[str, str]]]) -> None:
    """Deterministic signal-listener deductions. These are logical inferences from
    measured features (never cultural readings), so they belong in `inferred`."""
    add = partial(_add, source="dsp")
    signal = report.get("signal_interpretation") if isinstance(report.get("signal_interpretation"), dict) else {}
    if not signal:
        return
    for hypothesis in signal.get("hypotheses", []):
        if not isinstance(hypothesis, dict) or not hypothesis.get("statement"):
            continue
        add(
            claims,
            "inferred",
            str(hypothesis["statement"]),
            str(hypothesis.get("confidence") or "low"),
            str(hypothesis.get("basis") or "oida signal listener over measured DSP features"),
        )
    for caution in signal.get("cautions", []):
        if isinstance(caution, str) and caution.strip():
            add(claims, "measured", caution.strip(), "medium", "oida signal listener capture-chain check")


def _map_transcript(report: dict[str, Any], claims: dict[str, list[dict[str, str]]], model_name: str) -> None:
    add = partial(_add, source="model")
    transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
    if not transcript.get("present"):
        return
    for segment in transcript.get("segments", []):
        if not isinstance(segment, dict) or not segment.get("text"):
            continue
        time_range = _range(segment.get("t0"), segment.get("t1"))
        statement = f"Transcript{time_range}: {segment['text']}"
        confidence = str(segment.get("confidence") or "medium")
        add(claims, "heard", statement, confidence, f"{model_name} ASR, temp 0, timestamp anchored when available")


def _map_events(report: dict[str, Any], claims: dict[str, list[dict[str, str]]], model_name: str) -> None:
    add = partial(_add, source="model")
    for event in report.get("events", []):
        if not isinstance(event, dict):
            continue
        label = str(event.get("label") or "").strip()
        if not label:
            continue
        confidence = "high" if event.get("corroborated_by_dsp") else str(event.get("confidence") or "medium")
        basis = f"{model_name} event timeline{_range(event.get('t0'), event.get('t1'))}"
        if event.get("corroborated_by_dsp"):
            basis += " + onset corroboration"
        statement = f"Sound event{_range(event.get('t0'), event.get('t1'))}: {label}"
        description = event.get("description")
        if description:
            statement += f" - {description}"
        time_range = None
        if isinstance(event.get("t0"), (int, float)) and isinstance(event.get("t1"), (int, float)):
            time_range = {"start_s": max(0.0, float(event["t0"])), "end_s": max(0.0, float(event["t1"]))}
        forbidden_reason = _forbidden_output_reason(f"{label} {description or ''}", report)
        if forbidden_reason:
            add(
                claims,
                "undetermined",
                f"Unsupported MOSS event claim remains undetermined: {statement}",
                "undetermined",
                forbidden_reason,
                time_range=time_range,
            )
            continue
        add(claims, "heard", statement, confidence, basis, time_range=time_range)


def _map_caption(report: dict[str, Any], claims: dict[str, list[dict[str, str]]], model_name: str) -> None:
    add = partial(_add, source="model")
    caption = report.get("caption") if isinstance(report.get("caption"), dict) else {}
    dense = caption.get("dense") or caption.get("brief")
    if isinstance(dense, str) and dense.strip():
        forbidden_reason = _forbidden_output_reason(dense, report)
        if forbidden_reason:
            add(
                claims,
                "undetermined",
                f"Unsupported MOSS caption claim remains undetermined: {dense.strip()}",
                "undetermined",
                forbidden_reason,
            )
            return
        add(claims, "inferred", dense.strip(), "medium", f"{model_name} dense audio caption")


def _map_speech(report: dict[str, Any], claims: dict[str, list[dict[str, str]]], model_name: str) -> None:
    add = partial(_add, source="model")
    speech = report.get("speech") if isinstance(report.get("speech"), dict) else {}
    if not speech.get("present"):
        return
    dimensions = speech.get("dimensions") if isinstance(speech.get("dimensions"), dict) else {}
    for key, value in dimensions.items():
        text = str(value).strip()
        if not text:
            continue
        forbidden_reason = _forbidden_output_reason(text, report)
        if forbidden_reason:
            add(
                claims,
                "undetermined",
                f"Unsupported speech-caption dimension {key} remains undetermined: {text}",
                "undetermined",
                forbidden_reason,
            )
            continue
        confidence = "low" if key in {"age", "gender", "accent", "personality", "emotion"} else "medium"
        add(
            claims,
            "interpreted",
            f"Speech-caption dimension {key}: {text}",
            confidence,
            f"{model_name} speech-caption dimension; identity caution applies",
        )


def _map_music(report: dict[str, Any], claims: dict[str, list[dict[str, str]]], model_name: str) -> None:
    add = partial(_add, source="model")
    music = report.get("music") if isinstance(report.get("music"), dict) else {}
    if not music.get("present"):
        return
    description = music.get("description")
    if isinstance(description, str) and description.strip():
        forbidden_reason = _forbidden_output_reason(description, report)
        if forbidden_reason:
            add(
                claims,
                "undetermined",
                f"Unsupported MOSS music-analysis claim remains undetermined: {description.strip()}",
                "undetermined",
                forbidden_reason,
            )
        else:
            add(claims, "interpreted", description.strip(), "medium", f"{model_name} music analysis")
    dsp_bpm = music.get("dsp_bpm_candidate")
    moss_bpm = music.get("moss_bpm_candidate")
    if isinstance(dsp_bpm, (int, float)) and isinstance(moss_bpm, (int, float)):
        delta = abs(float(dsp_bpm) - float(moss_bpm))
        if delta <= 5:
            add(
                claims,
                "inferred",
                f"MOSS and DSP both support a tempo region near {float(dsp_bpm):.1f}-{float(moss_bpm):.1f} BPM.",
                "medium",
                f"{model_name} music analysis + DSP bpmCandidate corroboration",
            )
        else:
            add(
                claims,
                "undetermined",
                f"Tempo is unresolved: DSP suggests {float(dsp_bpm):.1f} BPM while MOSS suggests {float(moss_bpm):.1f} BPM.",
                "undetermined",
                "Mandatory contradiction handling: disagreement remains undetermined.",
            )


def _map_uncertainty(report: dict[str, Any], claims: dict[str, list[dict[str, str]]]) -> None:
    add = partial(_add, source="context")
    for note in report.get("model_uncertainty_notes", []):
        if isinstance(note, str) and note.strip():
            add(claims, "undetermined", note.strip(), "undetermined", "oida model uncertainty note")
    for note in report.get("forbidden_topics_triggered", []):
        if isinstance(note, str) and note.strip():
            add(claims, "undetermined", note.strip(), "undetermined", "oida forbidden topic guard")


def _map_forbidden_query(
    question: str | None,
    claims: dict[str, list[dict[str, str]]],
    report: dict[str, Any],
) -> None:
    add = partial(_add, source="context")
    if not question:
        return
    lowered = question.lower()
    for term, statement in FORBIDDEN_QUERY_TERMS:
        if term in lowered:
            if _term_is_supported(term, report):
                continue
            add(claims, "undetermined", statement, "undetermined", "MOSS-Audio 16 kHz mono input limitation")


def _forbidden_output_reason(text: str, report: dict[str, Any] | None = None) -> str | None:
    lowered = text.lower()
    for term, statement in FORBIDDEN_QUERY_TERMS:
        if term in lowered:
            if report is not None and _term_is_supported(term, report):
                continue
            return statement
    for term, message in _FORBIDDEN_OUTPUT_PHRASES:
        if term in lowered:
            if report is not None and _term_is_supported(term, report):
                continue
            return message
    if _STEREO_RE.search(lowered) and not (report is not None and _term_is_supported("stereo", report)):
        return _SPATIAL_REASON
    if _SPL_RE.search(lowered) and not (report is not None and _term_is_supported("absolute level", report)):
        return _LEVEL_REASON
    khz_match = _KHZ_RE.search(lowered)
    if khz_match:
        try:
            if float(khz_match.group(1)) > 8.0 and not (
                report is not None and _supports_frequency(report, float(khz_match.group(1)) * 1000)
            ):
                return _HIGHFREQ_REASON
        except ValueError:
            pass
    return None


def _term_is_supported(term: str, report: dict[str, Any]) -> bool:
    """Whether the declared perception apparatus supports this claim family."""
    model = str((report.get("engine") if isinstance(report.get("engine"), dict) else {}).get("model") or "")
    # MOSS always receives 16 kHz mono regardless of the native file metadata.
    if "moss" in model.lower():
        return False
    apparatus = report.get("apparatus") if isinstance(report.get("apparatus"), dict) else {}
    lowered = term.lower()
    if any(token in lowered for token in ("stereo", "spatial", "panned", "panning", "channel", "soundstage", "binaural", "surround")):
        return isinstance(apparatus.get("channels"), (int, float)) and int(apparatus["channels"]) >= 2
    if any(token in lowered for token in ("above 8", ">8", "ultrasonic", "khz")):
        return isinstance(apparatus.get("bandwidth_limit_hz"), (int, float)) and float(apparatus["bandwidth_limit_hz"]) > 8000
    if any(token in lowered for token in ("absolute", "sound pressure", "playback level", "physical level", "capture level", "spl")):
        return bool(apparatus.get("absolute_level_calibrated"))
    return False


def _supports_frequency(report: dict[str, Any], frequency_hz: float) -> bool:
    model = str((report.get("engine") if isinstance(report.get("engine"), dict) else {}).get("model") or "")
    if "moss" in model.lower():
        return False
    apparatus = report.get("apparatus") if isinstance(report.get("apparatus"), dict) else {}
    bandwidth = apparatus.get("bandwidth_limit_hz")
    return isinstance(bandwidth, (int, float)) and float(bandwidth) >= frequency_hz


def _filter_permissions(claims: dict[str, list[dict[str, str]]], permissions: dict[str, bool]) -> None:
    mapping = {
        "heard": "heard_allowed",
        "measured": "measured_allowed",
        "inferred": "inferred_allowed",
        "interpreted": "interpreted_allowed",
        "speculative": "speculative_allowed",
    }
    moved: list[dict[str, str]] = []
    for category, permission_key in mapping.items():
        if not permissions.get(permission_key, False):
            for claim in claims[category]:
                blocked = deepcopy(claim)
                blocked["statement"] = f"Blocked {category} claim: {blocked['statement']}"
                blocked["confidence"] = "undetermined"
                blocked["basis"] = f"{blocked.get('basis', '')}; disallowed by routing claim_permissions".strip("; ")
                moved.append(blocked)
            claims[category] = []
    claims["undetermined"].extend(moved)


def _range(t0: object, t1: object) -> str:
    if isinstance(t0, (int, float)) and isinstance(t1, (int, float)):
        return f" [{float(t0):.2f}-{float(t1):.2f}]"
    return ""


def _add(
    claims: dict[str, list[dict[str, str]]],
    category: str,
    statement: str,
    confidence: str,
    basis: str,
    *,
    source: str | None = None,
    time_range: dict[str, float] | None = None,
) -> None:
    if category not in CLAIM_CATEGORIES:
        raise ValueError(f"invalid claim category: {category}")
    statement = " ".join(statement.split())
    if not statement:
        return
    claim: dict[str, object] = {"statement": statement, "confidence": confidence, "basis": basis}
    if source:
        claim["source"] = source
    if time_range:
        claim["time_range"] = time_range
    claims[category].append(claim)
