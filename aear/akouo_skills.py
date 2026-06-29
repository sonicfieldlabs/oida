from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from aear.contracts import to_dict

ListeningMode = Literal[
    "basic",
    "speech",
    "music",
    "soundscape",
    "signal",
    "material",
    "ecological",
    "spectral",
    "comparative",
    "generative",
    "experimental",
]


@dataclass(frozen=True)
class ListeningSkillManifest:
    id: str
    name: str
    version: str
    description: str
    listening_mode: ListeningMode
    input_requirements: dict[str, Any]
    model_requirements: list[str]
    memory_policy: Literal["none", "read", "write", "read_write"]
    output_schema: dict[str, Any] | None = None
    ui_card: str | None = None
    enabled_by_default: bool = True


@dataclass(frozen=True)
class RoutePreset:
    id: str
    name: str
    description: str
    skill_ids: list[str]
    akouo_command: str
    direct_moss_modes: list[str] = field(default_factory=list)
    enabled_by_default: bool = True


SKILL_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://sonic-field.local/hmm/akouo-listening-skill.schema.json",
    "title": "AKOUO Listening Skill Manifest",
    "type": "object",
    "required": [
        "id",
        "name",
        "version",
        "description",
        "listening_mode",
        "input_requirements",
        "model_requirements",
        "memory_policy",
        "enabled_by_default",
    ],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "listening_mode": {
            "type": "string",
            "enum": [
                "basic",
                "speech",
                "music",
                "soundscape",
                "signal",
                "material",
                "ecological",
                "spectral",
                "comparative",
                "generative",
                "experimental",
            ],
        },
        "input_requirements": {
            "type": "object",
            "properties": {
                "minDurationMs": {"type": "number", "minimum": 0},
                "maxDurationMs": {"type": "number", "minimum": 0},
                "requiresStereo": {"type": "boolean"},
                "preferredSampleRate": {"type": "number", "minimum": 1},
                "acceptsFile": {"type": "boolean"},
                "acceptsStream": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
        "model_requirements": {"type": "array", "items": {"type": "string"}},
        "memory_policy": {"type": "string", "enum": ["none", "read", "write", "read_write"]},
        "output_schema": {"type": ["object", "null"]},
        "ui_card": {"type": ["string", "null"]},
        "enabled_by_default": {"type": "boolean"},
    },
    "additionalProperties": False,
}


ROUTE_PRESET_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://sonic-field.local/hmm/akouo-route-preset.schema.json",
    "title": "AKOUO Route Preset",
    "type": "object",
    "required": ["id", "name", "description", "skill_ids", "akouo_command", "direct_moss_modes", "enabled_by_default"],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "skill_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "akouo_command": {"type": "string", "pattern": "^/"},
        "direct_moss_modes": {"type": "array", "items": {"type": "string"}},
        "enabled_by_default": {"type": "boolean"},
    },
    "additionalProperties": False,
}


SKILLS: list[ListeningSkillManifest] = [
    ListeningSkillManifest(
        id="basic-listener",
        name="Basic Listener",
        version="0.1",
        description="Default first pass over the sound as sound, preserving uncertainty and suggesting deeper routes.",
        listening_mode="basic",
        input_requirements={"acceptsFile": True, "acceptsStream": True},
        model_requirements=["moss-audio"],
        memory_policy="write",
        ui_card="summary",
    ),
    ListeningSkillManifest(
        id="spectral-cartographer",
        name="Spectral Cartographer",
        version="0.1",
        description="Maps energy bands, tonal/noise balance, centroid, rolloff, flatness, and spectral limits.",
        listening_mode="spectral",
        input_requirements={"acceptsFile": True, "acceptsStream": True},
        model_requirements=["aear-dsp"],
        memory_policy="write",
        ui_card="signal-features",
    ),
    ListeningSkillManifest(
        id="signal-health",
        name="Signal Health Listener",
        version="0.1",
        description="Surfaces clipping, silence, loudness, channel balance, onset density, and routing-health clues.",
        listening_mode="signal",
        input_requirements={"acceptsFile": True, "acceptsStream": True},
        model_requirements=["aear-dsp"],
        memory_policy="write",
        ui_card="diagnostics",
    ),
    ListeningSkillManifest(
        id="material-gesture",
        name="Material Gesture Listener",
        version="0.1",
        description="Frames events as possible material interactions, gestures, resonances, and processes.",
        listening_mode="material",
        input_requirements={"acceptsFile": True, "acceptsStream": True},
        model_requirements=["moss-audio", "aear-dsp"],
        memory_policy="write",
        ui_card="hypotheses",
    ),
    ListeningSkillManifest(
        id="soundscape-ecology",
        name="Soundscape Ecology Listener",
        version="0.1",
        description="Interprets environmental relationships, foreground/background layers, and ecological categories.",
        listening_mode="ecological",
        input_requirements={"acceptsFile": True, "acceptsStream": True},
        model_requirements=["moss-audio", "akouo"],
        memory_policy="write",
        ui_card="soundscape",
    ),
    ListeningSkillManifest(
        id="musicological-listener",
        name="Musicological Listener",
        version="0.1",
        description="Analyzes musical texture, instrumentation hypotheses, tempo feel, structure, and production traits.",
        listening_mode="music",
        input_requirements={"acceptsFile": True, "acceptsStream": True},
        model_requirements=["moss-audio", "aear-dsp"],
        memory_policy="write",
        ui_card="music",
        enabled_by_default=False,
    ),
    ListeningSkillManifest(
        id="speech-route",
        name="Speech Route",
        version="0.1",
        description="Speech transcript and paralinguistic dimensions, kept as one listening route rather than the product center.",
        listening_mode="speech",
        input_requirements={"acceptsFile": True, "acceptsStream": True},
        model_requirements=["moss-audio"],
        memory_policy="write",
        ui_card="speech",
        enabled_by_default=False,
    ),
    ListeningSkillManifest(
        id="comparative-memory",
        name="Comparative Memory Listener",
        version="0.1",
        description="Compares the current event with selected Akousmata traces when memory is enabled.",
        listening_mode="comparative",
        input_requirements={"acceptsFile": True, "acceptsStream": False},
        model_requirements=["akousmata"],
        memory_policy="read",
        ui_card="memory",
        enabled_by_default=False,
    ),
    ListeningSkillManifest(
        id="extended-spectrum-caution",
        name="Extended Spectrum Caution Listener",
        version="0.1",
        description="Checks whether ultrasonic or infrasonic claims are supportable from sample rate, capture chain, and DSP evidence.",
        listening_mode="experimental",
        input_requirements={"acceptsFile": True, "acceptsStream": True, "preferredSampleRate": 96000},
        model_requirements=["aear-dsp"],
        memory_policy="write",
        ui_card="spectrum-limits",
        enabled_by_default=False,
    ),
    ListeningSkillManifest(
        id="generative-bridge",
        name="Generative Bridge Listener",
        version="0.1",
        description="Turns grounded listening observations into future transformation or synthesis prompts without generating audio itself.",
        listening_mode="generative",
        input_requirements={"acceptsFile": True, "acceptsStream": False},
        model_requirements=["akouo"],
        memory_policy="read_write",
        ui_card="generative-brief",
        enabled_by_default=False,
    ),
]


PRESETS: list[RoutePreset] = [
    RoutePreset(
        id="basic",
        name="Basic",
        description="Broad first-pass interpretation with signal facts and AKOÚŌ uncertainty discipline.",
        skill_ids=["basic-listener", "spectral-cartographer", "signal-health"],
        akouo_command="/listen",
        direct_moss_modes=["environment"],
    ),
    RoutePreset(
        id="environment",
        name="Environment",
        description="Soundscape/ecology listening for field recordings, rooms, infrastructure, and ambience.",
        skill_ids=["basic-listener", "soundscape-ecology", "material-gesture", "spectral-cartographer"],
        akouo_command="/field",
        direct_moss_modes=["environment", "soundscape"],
    ),
    RoutePreset(
        id="signal",
        name="Signal",
        description="Technical diagnostics with measured DSP facts first.",
        skill_ids=["signal-health", "spectral-cartographer"],
        akouo_command="/tech",
        direct_moss_modes=["sonic_data"],
    ),
    RoutePreset(
        id="music",
        name="Music",
        description="Musicological and production-oriented listening.",
        skill_ids=["musicological-listener", "spectral-cartographer", "signal-health"],
        akouo_command="/listen",
        direct_moss_modes=["music"],
    ),
    RoutePreset(
        id="speech",
        name="Speech",
        description="Transcript and speech route with identity caution.",
        skill_ids=["speech-route", "signal-health"],
        akouo_command="/voice",
        direct_moss_modes=["transcribe"],
        enabled_by_default=False,
    ),
    RoutePreset(
        id="memory",
        name="Memory",
        description="Basic listening plus local Akousmata comparison.",
        skill_ids=["basic-listener", "comparative-memory", "spectral-cartographer"],
        akouo_command="/listen",
        direct_moss_modes=["environment"],
        enabled_by_default=False,
    ),
    RoutePreset(
        id="extended-spectrum",
        name="Extended Spectrum",
        description="DSP-first route that explains sample-rate and capture-chain limits before any ultrasonic or infrasonic interpretation.",
        skill_ids=["signal-health", "spectral-cartographer", "extended-spectrum-caution"],
        akouo_command="/tech",
        direct_moss_modes=["sonic_data"],
        enabled_by_default=False,
    ),
    RoutePreset(
        id="generative",
        name="Generative",
        description="Grounded prompt-bridge route for future sound-generation or transformation systems.",
        skill_ids=["basic-listener", "material-gesture", "generative-bridge"],
        akouo_command="/listen",
        direct_moss_modes=["environment"],
        enabled_by_default=False,
    ),
]


def skill_manifest(skill_id: str) -> ListeningSkillManifest:
    for skill in SKILLS:
        if skill.id == skill_id:
            return skill
    valid = ", ".join(skill.id for skill in SKILLS)
    raise ValueError(f"unknown listening skill: {skill_id}. Valid skills: {valid}")


def route_preset(preset_id: str) -> RoutePreset:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    valid = ", ".join(preset.id for preset in PRESETS)
    raise ValueError(f"unknown route preset: {preset_id}. Valid presets: {valid}")


def resolve_route_skill_ids(
    preset_id: str,
    *,
    enabled_skill_ids: list[str] | None = None,
    disabled_skill_ids: list[str] | None = None,
) -> list[str]:
    preset = route_preset(preset_id)
    if enabled_skill_ids is None:
        skill_ids = list(preset.skill_ids)
    else:
        skill_ids = [_validate_skill_id(skill_id) for skill_id in enabled_skill_ids]
    disabled = {_validate_skill_id(skill_id) for skill_id in disabled_skill_ids or []}
    resolved = [skill_id for skill_id in _dedupe(skill_ids) if skill_id not in disabled]
    if not resolved:
        raise ValueError("at least one AKOUO listening skill must be enabled")
    return resolved


def validate_akouo_manifest() -> list[str]:
    errors: list[str] = []
    skill_ids = [skill.id for skill in SKILLS]
    duplicate_skills = _duplicates(skill_ids)
    if duplicate_skills:
        errors.append(f"duplicate skill id(s): {', '.join(duplicate_skills)}")
    for skill in SKILLS:
        if not skill.id or not skill.name or not skill.description:
            errors.append(f"skill {skill.id or '<missing>'} is missing required text fields")
        if skill.memory_policy not in {"none", "read", "write", "read_write"}:
            errors.append(f"skill {skill.id} has invalid memory policy: {skill.memory_policy}")
        if not isinstance(skill.input_requirements, dict):
            errors.append(f"skill {skill.id} input_requirements must be an object")

    preset_ids = [preset.id for preset in PRESETS]
    duplicate_presets = _duplicates(preset_ids)
    if duplicate_presets:
        errors.append(f"duplicate preset id(s): {', '.join(duplicate_presets)}")
    known = set(skill_ids)
    for preset in PRESETS:
        missing = [skill_id for skill_id in preset.skill_ids if skill_id not in known]
        if missing:
            errors.append(f"preset {preset.id} references unknown skill(s): {', '.join(missing)}")
        if not preset.akouo_command.startswith("/"):
            errors.append(f"preset {preset.id} has invalid AKOUO command: {preset.akouo_command}")
    return errors


def akouo_manifest() -> dict[str, Any]:
    errors = validate_akouo_manifest()
    return {
        "version": "0.1",
        "schema_version": "0.1",
        "schemas": {
            "skill_manifest": SKILL_MANIFEST_SCHEMA,
            "route_preset": ROUTE_PRESET_SCHEMA,
        },
        "valid": not errors,
        "errors": errors,
        "skills": [to_dict(skill) for skill in SKILLS],
        "route_presets": [to_dict(preset) for preset in PRESETS],
    }


def _validate_skill_id(skill_id: str) -> str:
    skill_manifest(skill_id)
    return skill_id


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _duplicates(values: list[str]) -> list[str]:
    seen = set()
    duplicates = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates
