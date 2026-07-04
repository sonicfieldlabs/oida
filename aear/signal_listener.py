"""Deterministic signal-level listening over measured DSP features.

This module is the perception floor of hmm: it always has something honest to
say about a sound because it reasons only from measured features (dsp.py).
Under the AKOUO Evidence Ladder its output is `measured_signal` evidence —
classification statements are logical deductions (`inferred`), never cultural
readings, and every statement carries its numeric basis.

MOSS-Audio, when available, layers `transcript_or_caption` evidence on top;
this reading never pretends to replace it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SIGNAL_LISTENER_VERSION = "0.2"

# Classification ids are deliberately plain: they name signal shapes, not genres.
CLASSIFICATIONS = (
    "silence",
    "speech-like",
    "music-like",
    "tonal-sustained",
    "percussive-events",
    "noise-like",
    "ambient-texture",
    "mixed-material",
)


@dataclass
class SignalReading:
    version: str = SIGNAL_LISTENER_VERSION
    classification: str = "mixed-material"
    classification_confidence: str = "low"
    descriptors: list[str] = field(default_factory=list)
    caption: str = ""
    title: str = "Listening event"
    hypotheses: list[dict[str, str]] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


BASIS = "hmm signal listener: deterministic heuristics over measured DSP features"


def interpret_signal(dsp: dict[str, Any] | None) -> SignalReading:
    """Read `inspect_path()` output (or a bare features dict) into a SignalReading."""
    dsp = dsp if isinstance(dsp, dict) else {}
    features = dsp.get("features") if isinstance(dsp.get("features"), dict) else dsp
    reading = SignalReading()

    duration = _num(dsp.get("durationSeconds")) or _num(features.get("analyzedSeconds"))
    rms = _num(features.get("rmsDbfs"))
    peak = _num(features.get("peakDbfs"))
    lufs = _num(features.get("integratedLufs"))
    lra = _num(features.get("loudnessRangeLu"))
    crest = _num(features.get("crestFactorDb"))
    silence_ratio = _num(features.get("silenceRatio"))
    flatness = _num(features.get("spectralFlatness"))
    centroid = _num(features.get("spectralCentroidHz"))
    centroid_std = _num(features.get("spectralCentroidStdHz"))
    rolloff = _num(features.get("spectralRolloffHz"))
    onset_density = _num(features.get("onsetDensityPerSec"))
    bpm = _num(features.get("bpmCandidate"))
    zcr = _num(features.get("zeroCrossingRate"))
    clipped = _num(features.get("clippedSampleRatio"))
    bands = features.get("bandEnergy") if isinstance(features.get("bandEnergy"), dict) else {}
    width = _num(features.get("stereoWidth"))
    correlation = _num(features.get("interChannelCorrelation"))

    if not features or all(value is None for value in (rms, peak, flatness, centroid, onset_density)):
        reading.caption = "No measurable signal features were extracted from this audio."
        reading.classification = "silence" if silence_ratio and silence_ratio > 0.98 else "mixed-material"
        reading.title = "Unreadable or empty signal"
        return reading

    scores = _score_classes(
        rms=rms,
        silence_ratio=silence_ratio,
        flatness=flatness,
        centroid=centroid,
        centroid_std=centroid_std,
        onset_density=onset_density,
        bpm=bpm,
        crest=crest,
        bands=bands,
        zcr=zcr,
        lra=lra,
    )
    reading.scores = scores
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0.0
    reading.classification = best
    margin = best_score - runner_score
    if best_score >= 0.62 and margin >= 0.18:
        reading.classification_confidence = "medium"
    elif best_score >= 0.45:
        reading.classification_confidence = "low"
    else:
        reading.classification = "mixed-material"
        reading.classification_confidence = "low"

    reading.descriptors = _descriptors(
        lufs=lufs,
        rms=rms,
        centroid=centroid,
        flatness=flatness,
        crest=crest,
        lra=lra,
        onset_density=onset_density,
        bpm=bpm,
        bands=bands,
        width=width,
        correlation=correlation,
        silence_ratio=silence_ratio,
    )
    reading.cautions = _cautions(clipped=clipped, silence_ratio=silence_ratio, peak=peak)
    reading.caption = _caption(reading.classification, reading.descriptors, duration)
    reading.title = _title(reading.classification, reading.descriptors)
    reading.hypotheses = _hypotheses(reading, bpm=bpm, onset_density=onset_density, flatness=flatness, centroid=centroid)
    return reading


def signal_reading_dict(dsp: dict[str, Any] | None) -> dict[str, Any]:
    return asdict(interpret_signal(dsp))


def _score_classes(
    *,
    rms: float | None,
    silence_ratio: float | None,
    flatness: float | None,
    centroid: float | None,
    centroid_std: float | None,
    onset_density: float | None,
    bpm: float | None,
    crest: float | None,
    bands: dict[str, Any],
    zcr: float | None,
    lra: float | None,
) -> dict[str, float]:
    """Score each signal shape in [0, 1]. Evidence-additive, feature-guarded."""
    scores = {name: 0.0 for name in CLASSIFICATIONS}

    quiet = _clamp01(_ramp(rms, -40.0, -62.0)) if rms is not None else 0.0
    silent_frames = _clamp01(_ramp(silence_ratio, 0.55, 0.95)) if silence_ratio is not None else 0.0
    scores["silence"] = _clamp01(0.55 * quiet + 0.55 * silent_frames)

    voiceband = 0.0
    if isinstance(bands, dict):
        low_mid = _num(bands.get("lowMid")) or 0.0
        mid = _num(bands.get("mid")) or 0.0
        voiceband = _clamp01(_ramp(low_mid + mid, 0.35, 0.75))
    syllabic = _band_affinity(onset_density, 1.5, 4.0, 8.0) if onset_density is not None else 0.0
    speech_centroid = _band_affinity(centroid, 400.0, 1400.0, 3200.0) if centroid is not None else 0.0
    unstable_spectrum = _clamp01(_ramp(_ratio(centroid_std, centroid), 0.15, 0.5)) if centroid_std and centroid else 0.3
    dynamic = _clamp01(_ramp(lra, 4.0, 14.0)) if lra is not None else 0.3
    no_grid = 0.55 if bpm is None else 0.15
    scores["speech-like"] = _clamp01(
        0.28 * syllabic + 0.22 * speech_centroid + 0.18 * voiceband + 0.12 * unstable_spectrum + 0.1 * dynamic + 0.1 * no_grid
    ) * (1.0 - scores["silence"])

    pulse = 0.65 if bpm is not None else 0.0
    musical_onsets = _band_affinity(onset_density, 0.7, 2.5, 9.0) if onset_density is not None else 0.0
    pitched = _clamp01(_ramp(flatness, 0.35, 0.08)) if flatness is not None else 0.3
    scores["music-like"] = _clamp01(0.45 * pulse + 0.3 * musical_onsets + 0.25 * pitched) * (1.0 - scores["silence"])

    sparse = _clamp01(_ramp(onset_density, 0.6, 0.1)) if onset_density is not None else 0.4
    very_tonal = _clamp01(_ramp(flatness, 0.2, 0.03)) if flatness is not None else 0.0
    steady = _clamp01(_ramp(_ratio(centroid_std, centroid), 0.25, 0.05)) if centroid_std and centroid else 0.35
    scores["tonal-sustained"] = _clamp01(0.4 * very_tonal + 0.35 * sparse + 0.25 * steady) * (1.0 - scores["silence"])

    spiky = _clamp01(_ramp(crest, 14.0, 26.0)) if crest is not None else 0.0
    busy = _clamp01(_ramp(onset_density, 1.5, 6.0)) if onset_density is not None else 0.0
    scores["percussive-events"] = _clamp01(0.55 * spiky + 0.45 * busy) * (1.0 - scores["silence"])

    flat_noise = _clamp01(_ramp(flatness, 0.3, 0.6)) if flatness is not None else 0.0
    hissy = _clamp01(_ramp(zcr, 3000.0, 9000.0)) if zcr is not None else 0.0
    scores["noise-like"] = _clamp01(0.7 * flat_noise + 0.3 * hissy) * (1.0 - scores["silence"])

    mid_flat = _band_affinity(flatness, 0.12, 0.28, 0.5) if flatness is not None else 0.0
    calm = _clamp01(_ramp(onset_density, 1.2, 0.2)) if onset_density is not None else 0.3
    low_dynamics = _clamp01(_ramp(lra, 10.0, 3.0)) if lra is not None else 0.3
    scores["ambient-texture"] = _clamp01(0.4 * mid_flat + 0.35 * calm + 0.25 * low_dynamics) * (1.0 - scores["silence"])

    spread = [value for value in scores.values() if value > 0.3]
    scores["mixed-material"] = 0.42 if len(spread) >= 3 else 0.25
    return scores


def _descriptors(
    *,
    lufs: float | None,
    rms: float | None,
    centroid: float | None,
    flatness: float | None,
    crest: float | None,
    lra: float | None,
    onset_density: float | None,
    bpm: float | None,
    bands: dict[str, Any],
    width: float | None,
    correlation: float | None,
    silence_ratio: float | None,
) -> list[str]:
    words: list[str] = []
    level = lufs if lufs is not None else rms
    if level is not None:
        if level > -14:
            words.append("loud")
        elif level > -26:
            words.append("moderate level")
        elif level > -42:
            words.append("quiet")
        else:
            words.append("very quiet")
    if centroid is not None:
        if centroid < 700:
            words.append("dark")
        elif centroid > 3000:
            words.append("bright")
    if isinstance(bands, dict):
        sub = (_num(bands.get("sub")) or 0.0) + (_num(bands.get("bass")) or 0.0)
        air = _num(bands.get("air")) or 0.0
        if sub > 0.5:
            words.append("bass-heavy")
        if air > 0.25:
            words.append("airy top end")
    if flatness is not None and flatness > 0.45:
        words.append("noisy texture")
    if crest is not None and crest > 20:
        words.append("sharp transients")
    if lra is not None:
        if lra > 12:
            words.append("wide dynamics")
        elif lra < 3:
            words.append("flat dynamics")
    if bpm is not None:
        words.append(f"steady pulse ~{bpm:.0f} BPM")
    elif onset_density is not None and onset_density > 4:
        words.append("dense event activity")
    elif onset_density is not None and onset_density < 0.2:
        words.append("sustained, few onsets")
    if width is not None and width > 0.35:
        words.append("wide stereo energy")
    if correlation is not None and correlation < 0.15:
        words.append("decorrelated channels")
    if silence_ratio is not None and 0.3 < silence_ratio <= 0.55:
        words.append("intermittent")
    return words


def _cautions(*, clipped: float | None, silence_ratio: float | None, peak: float | None) -> list[str]:
    cautions: list[str] = []
    if clipped is not None and clipped > 0:
        cautions.append(f"Approx {clipped * 100:.2f}% of samples sit near full scale; clipping is possible.")
    if peak is not None and peak > -0.3:
        cautions.append("Peaks touch full scale; the capture chain may be limiting.")
    if silence_ratio is not None and silence_ratio > 0.85:
        cautions.append("Most frames are below -60 dBFS; content may be silence or extremely quiet.")
    return cautions


_CLASS_PHRASES = {
    "silence": "Near-silent signal",
    "speech-like": "Speech-like activity",
    "music-like": "Music-like material",
    "tonal-sustained": "Sustained tonal material",
    "percussive-events": "Percussive event activity",
    "noise-like": "Broadband noise-like signal",
    "ambient-texture": "Ambient textural material",
    "mixed-material": "Mixed sonic material",
}


def _caption(classification: str, descriptors: list[str], duration: float | None) -> str:
    lead = _CLASS_PHRASES.get(classification, "Sonic material")
    length = f" across {duration:.1f} s" if isinstance(duration, (int, float)) and duration else ""
    if descriptors:
        return f"{lead}{length}: {', '.join(descriptors[:6])}. Read from measured signal features only."
    return f"{lead}{length}, read from measured signal features only."


def _title(classification: str, descriptors: list[str]) -> str:
    lead = _CLASS_PHRASES.get(classification, "Listening event")
    accent = next((word for word in descriptors if "BPM" in word), None)
    if accent:
        return f"{lead} · {accent}"
    if descriptors:
        return f"{lead} · {descriptors[0]}"
    return lead


def _hypotheses(
    reading: SignalReading,
    *,
    bpm: float | None,
    onset_density: float | None,
    flatness: float | None,
    centroid: float | None,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    phrase = _CLASS_PHRASES.get(reading.classification, "mixed material").lower()
    evidence_bits: list[str] = []
    if flatness is not None:
        evidence_bits.append(f"flatness {flatness:.2f}")
    if centroid is not None:
        evidence_bits.append(f"centroid {centroid:.0f} Hz")
    if onset_density is not None:
        evidence_bits.append(f"{onset_density:.1f} onsets/s")
    if bpm is not None:
        evidence_bits.append(f"pulse ~{bpm:.0f} BPM")
    evidence = "; ".join(evidence_bits) or "measured features"
    items.append(
        {
            "statement": f"The signal most resembles {phrase}.",
            "confidence": reading.classification_confidence,
            "basis": f"{BASIS} ({evidence})",
        }
    )
    if bpm is not None:
        items.append(
            {
                "statement": f"A repeating pulse near {bpm:.0f} BPM organizes the onsets.",
                "confidence": "low",
                "basis": f"{BASIS} (inter-onset interval regularity)",
            }
        )
    runner = sorted(reading.scores.items(), key=lambda item: item[1], reverse=True)
    if len(runner) > 1 and runner[1][1] > 0.4 and runner[1][0] != reading.classification:
        items.append(
            {
                "statement": f"A secondary reading as {_CLASS_PHRASES.get(runner[1][0], runner[1][0]).lower()} also fits the measurements.",
                "confidence": "low",
                "basis": BASIS,
            }
        )
    return items


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ramp(value: float | None, start: float, end: float) -> float:
    """Linear 0→1 ramp from start to end; descending ranges (start > end) work too."""
    if value is None:
        return 0.0
    if start == end:
        return 1.0 if value >= end else 0.0
    position = (value - start) / (end - start)
    return _clamp01(position)


def _band_affinity(value: float | None, low: float, mid: float, high: float) -> float:
    """1.0 at mid, tapering to 0 at low/high bounds."""
    if value is None:
        return 0.0
    if value <= low or value >= high:
        return 0.0
    if value == mid:
        return 1.0
    if value < mid:
        return _clamp01((value - low) / (mid - low))
    return _clamp01((high - value) / (high - mid))


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator
