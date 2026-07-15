from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationSettings:
    model_kind: str
    temperature: float
    top_p: float
    top_k: int
    max_new_tokens: int
    thinking_budget: int | None = None


@dataclass(frozen=True)
class Recipe:
    task: str
    prompt: str
    settings: GenerationSettings


INSTRUCT_EXTRACTION = GenerationSettings(
    model_kind="instruct",
    temperature=0.0,
    top_p=1.0,
    top_k=50,
    max_new_tokens=1024,
)

TRANSCRIPTION_EXTRACTION = GenerationSettings(
    model_kind="transcription",
    temperature=0.0,
    top_p=1.0,
    top_k=50,
    max_new_tokens=2048,
)

INSTRUCT_CAPTION = GenerationSettings(
    model_kind="instruct",
    temperature=0.7,
    top_p=1.0,
    top_k=50,
    max_new_tokens=1024,
)

THINKING_REASONING = GenerationSettings(
    model_kind="thinking",
    temperature=1.0,
    top_p=1.0,
    top_k=50,
    max_new_tokens=1536,
)

MUSIC_REASONING = GenerationSettings(
    model_kind="music",
    temperature=1.0,
    top_p=1.0,
    top_k=50,
    max_new_tokens=1536,
)

TARGETED_RELISTEN_REASONING = GenerationSettings(
    model_kind="targeted_relisten",
    temperature=0.4,
    top_p=1.0,
    top_k=50,
    max_new_tokens=1024,
)

STRUCTURED_ANALYSIS = GenerationSettings(
    model_kind="instruct",
    temperature=0.3,
    top_p=1.0,
    top_k=50,
    max_new_tokens=1536,
)


RECIPES: dict[str, Recipe] = {
    "transcribe": Recipe(
        task="transcribe",
        prompt="Transcribe the audio.",
        settings=TRANSCRIPTION_EXTRACTION,
    ),
    "transcribe_sentence": Recipe(
        task="transcribe",
        prompt="Transcribe the audio with sentence-level timestamps in [start]text[end] format.",
        settings=TRANSCRIPTION_EXTRACTION,
    ),
    "transcribe_word": Recipe(
        task="transcribe",
        prompt="Transcribe with word-level timestamps.",
        settings=TRANSCRIPTION_EXTRACTION,
    ),
    "events": Recipe(
        task="events",
        prompt="List every distinct sound event with start and end times, as [start-end] label - description. Return one event per line.",
        settings=INSTRUCT_EXTRACTION,
    ),
    "caption_brief": Recipe(
        task="caption",
        prompt="Briefly describe the audio, focusing only on what can be heard.",
        settings=INSTRUCT_CAPTION,
    ),
    "caption_dense": Recipe(
        task="caption",
        prompt="Describe this audio in detail: every sound source, when it occurs, and how it changes over time.",
        settings=INSTRUCT_CAPTION,
    ),
    "speech": Recipe(
        task="speech",
        prompt=(
            "Describe the speaker(s): gender, age, accent, pitch, volume, speed, texture, clarity, "
            "fluency, emotion, tone, personality, and a summary. Mark uncertain dimensions as uncertain."
        ),
        settings=INSTRUCT_CAPTION,
    ),
    "music": Recipe(
        task="music",
        prompt="Analyze this music: instrumentation, tempo feel, structure over time, production character, and emotional arc.",
        settings=MUSIC_REASONING,
    ),
    "environment": Recipe(
        task="environment",
        prompt=(
            "Analyze this recording as environmental sound, not as a speech transcript. "
            "Extract sound sources, foreground/background layers, event order, texture, density, material cues, "
            "ambience, likely acoustic setting, and uncertainties. Use time anchors whenever possible."
        ),
        settings=STRUCTURED_ANALYSIS,
    ),
    "soundscape": Recipe(
        task="soundscape",
        prompt=(
            "Analyze this recording for soundscape research. Identify keynote sounds, soundmarks if any, "
            "geophony/biophony/anthrophony layers, temporal changes, masking, acoustic ecology clues, "
            "human infrastructure signals, and what remains uncertain. Cite acoustic evidence and time ranges."
        ),
        settings=THINKING_REASONING,
    ),
    "sonic_data": Recipe(
        task="sonic_data",
        prompt=(
            "Extract detailed sonic information from this audio. Return concise structured sections for: "
            "sound_events, source_classes, textures, dynamics, density, rhythm_or_pulse, spectral_impression_below_8khz, "
            "material_resonance_clues, speech_presence, music_presence, environmental_presence, uncertainty_notes. "
            "Do not claim stereo image, absolute loudness, or content above 8 kHz."
        ),
        settings=STRUCTURED_ANALYSIS,
    ),
    "qa": Recipe(
        task="qa",
        prompt="{question}\n{context_block}Cite the time range or acoustic evidence when possible.",
        settings=THINKING_REASONING,
    ),
    "think": Recipe(
        task="think",
        prompt="{instruction}\nThink step by step about the acoustic evidence before answering.",
        settings=THINKING_REASONING,
    ),
}


def get_recipe(task: str, **kwargs: object) -> Recipe:
    try:
        recipe = RECIPES[task]
    except KeyError as exc:
        raise ValueError(f"unknown MOSS recipe: {task}") from exc
    if kwargs:
        return Recipe(
            task=recipe.task,
            prompt=recipe.prompt.format(**kwargs),
            settings=recipe.settings,
        )
    return recipe
