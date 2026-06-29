from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from aear.contracts import AudioSourceDescriptor, SourceType, to_dict
from aear.source_routes import native_system_audio_route_manifest
from aear.system_audio import system_audio_status


@dataclass(frozen=True)
class SourceRegistryEntry:
    id: str
    type: SourceType
    label: str
    supported: bool
    status: str
    description: str
    capture_adapter: str | None = None
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def descriptor(self) -> AudioSourceDescriptor:
        return AudioSourceDescriptor(
            type=self.type,
            label=self.label,
            supported=self.supported,
            status=self.status,
            platform=sys.platform,
            details={"source_id": self.id, **self.details},
        )


def source_registry() -> list[SourceRegistryEntry]:
    system = system_audio_status()
    return [
        SourceRegistryEntry(
            id="live-input-browser",
            type="live_input",
            label="Live input",
            supported=True,
            status="permission_required",
            description="Browser microphone or audio-interface input captured with getUserMedia and uploaded to the local daemon.",
            capture_adapter="browser-mediarecorder",
            notes=[
                "User permission is required before capture.",
                "The current browser path captures live input, not system output.",
            ],
        ),
        SourceRegistryEntry(
            id="system-output",
            type="system_output",
            label="System audio",
            supported=system.supported,
            status=system.status,
            description=system.summary,
            capture_adapter=system.adapter,
            notes=[*system.setup_steps, *system.warnings],
            details={
                "capture_strategy": system.capture_strategy,
                "candidate_keywords": system.candidate_keywords,
                "native_routes": native_system_audio_route_manifest(),
            },
        ),
        SourceRegistryEntry(
            id="native-system-output",
            type="system_output",
            label="Native system output",
            supported=sys.platform == "darwin",
            status="ready" if sys.platform == "darwin" else "unsupported",
            description="Native ScreenCaptureKit system-output source routes controlled by the macOS shell.",
            capture_adapter="macos-screencapturekit-system-audio",
            notes=[
                "The current native route is display_mix with current-process audio excluded.",
                "Raw audio is written only for explicit temp analysis captures.",
            ],
            details=native_system_audio_route_manifest(),
        ),
        SourceRegistryEntry(
            id="audio-file",
            type="file",
            label="Audio file",
            supported=True,
            status="ready",
            description="Local audio file path or dashboard upload normalized through the existing file pipeline.",
            capture_adapter="file-upload-or-local-path",
            notes=["Supported formats depend on soundfile and FFmpeg availability."],
        ),
        SourceRegistryEntry(
            id="captured-buffer",
            type="buffer",
            label="Captured buffer",
            supported=True,
            status="ready",
            description="A bounded segment extracted from the live local ring buffer.",
            capture_adapter="live-ring-buffer",
            notes=["Current browser-live chunks are temporary local files; they are not uploaded remotely."],
        ),
    ]


def source_registry_dict() -> dict[str, Any]:
    return {
        "version": "0.1",
        "platform": sys.platform,
        "sources": [to_dict(entry) for entry in source_registry()],
    }


def descriptor_for_source(source_id: str) -> AudioSourceDescriptor:
    for entry in source_registry():
        if entry.id == source_id:
            return entry.descriptor()
    valid = ", ".join(entry.id for entry in source_registry())
    raise ValueError(f"unknown source id: {source_id}. Valid sources: {valid}")
