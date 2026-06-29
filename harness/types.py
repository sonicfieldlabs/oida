from __future__ import annotations

CLAIM_CATEGORIES = ["heard", "measured", "inferred", "interpreted", "speculative", "undetermined"]

LISTENING_MODES = [
    "signal-inspection-listening",
    "acoulogical-object-listening",
    "embodied-affective-listening",
    "transductive-media-listening",
    "forensic-archival-listening",
    "ecological-posthuman-listening",
    "critical-political-listening",
    "musical-aesthetic-listening",
    "symbolic-fictional-listening",
    "audiovisual-scenic-listening",
    "voice-speech-listening",
    "accessibility-normative-listening",
    "material-event-listening",
]

def empty_claim_taxonomy() -> dict[str, list[dict[str, str]]]:
    return {category: [] for category in CLAIM_CATEGORIES}


def empty_mediations() -> dict[str, list[str]]:
    return {
        "technical": [],
        "cultural": [],
        "spatial": [],
        "bodily": [],
        "archival": [],
        "computational": [],
    }


def empty_risks() -> dict[str, list[str]]:
    return {
        "hallucination": [],
        "over_identification": [],
        "cultural_flattening": [],
        "forensic_overreach": [],
        "source_confusion": [],
        "aesthetic_overstatement": [],
    }
