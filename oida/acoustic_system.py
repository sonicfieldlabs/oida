from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AcousticMode:
    id: str
    label: str
    direct_moss_mode: str
    akouo_command: str
    evidence_role: str
    status: str


ACOUSTIC_MODES = [
    AcousticMode(
        id="environmental_sound",
        label="Environmental Sound",
        direct_moss_mode="environment",
        akouo_command="/field",
        evidence_role="event timeline, ambience, source classes, material cues",
        status="direct_moss_ready",
    ),
    AcousticMode(
        id="music",
        label="Music",
        direct_moss_mode="music",
        akouo_command="/listen",
        evidence_role="instrumentation, structure, tempo feel, production, arc",
        status="direct_moss_ready",
    ),
    AcousticMode(
        id="soundscape_research",
        label="Soundscape Research",
        direct_moss_mode="soundscape",
        akouo_command="/field",
        evidence_role="keynotes, soundmarks, geophony, biophony, anthrophony, uncertainty",
        status="direct_moss_ready",
    ),
    AcousticMode(
        id="speech_voice",
        label="Speech And Voice",
        direct_moss_mode="transcribe",
        akouo_command="/voice",
        evidence_role="transcript, speech caption, paralinguistic cautions",
        status="direct_moss_ready",
    ),
]


def acoustic_system_manifest() -> dict[str, object]:
    return {
        "version": "0.1",
        "status": "operational",
        "direct_layer": "moss_audio",
        "claim_layer": "akouo_mapping_ready",
        "modes": [asdict(mode) for mode in ACOUSTIC_MODES],
        "guardrails": [
            "MOSS output remains evidence until claim mapping assigns category, confidence, and basis.",
            "DSP owns measured loudness, stereo, clipping, spectrum, and onset claims.",
            "Contradictions between MOSS and DSP remain undetermined.",
        ],
    }
