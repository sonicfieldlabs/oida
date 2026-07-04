from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from oida.contracts import to_dict

LOOPBACK_KEYWORDS = (
    "blackhole",
    "loopback",
    "soundflower",
    "audio hijack",
    "rogue amoeba",
    "vb-audio",
    "virtual cable",
    "cable output",
    "stereo mix",
    "what u hear",
    "monitor of",
    "pipewire monitor",
    "pulse monitor",
)


@dataclass(frozen=True)
class SystemAudioStatus:
    platform: str
    status: str
    supported: bool
    capture_strategy: str
    adapter: str | None
    summary: str
    setup_steps: list[str] = field(default_factory=list)
    candidate_keywords: list[str] = field(default_factory=lambda: list(LOOPBACK_KEYWORDS))
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def system_audio_status(platform: str | None = None) -> SystemAudioStatus:
    platform = platform or sys.platform
    if platform == "darwin":
        return SystemAudioStatus(
            platform=platform,
            status="needs_loopback_device",
            supported=True,
            capture_strategy="browser-live-input-with-loopback-device",
            adapter="browser-mediarecorder",
            summary="macOS system output can be routed into oida today by selecting a virtual loopback device as live input.",
            setup_steps=[
                "Install or enable a virtual loopback device such as BlackHole, Loopback, or Soundflower.",
                "Route computer output to that device, or create a Multi-Output Device if you also need to hear it.",
                "Grant browser microphone permission and choose the loopback device in oida's live source selector.",
                "Use Live, then Capture last 10 seconds to analyze the system-output buffer.",
                "For visual metering only, use the native macOS shell's ScreenCaptureKit system-output signal tap.",
            ],
            warnings=[
                "The current web dashboard cannot directly capture macOS output without a loopback input device.",
                "The native macOS signal tap writes raw audio only for explicit user-initiated temp analysis.",
                "Browser live chunks and native analysis captures are temporary local uploads with explicit policy labels.",
            ],
            details={
                "native_signal_tap": "apps/macos ScreenCaptureKit in-memory meter",
                "native_signal_tap_raw_audio_policy": "not_stored_until_explicit_analysis",
                "native_temp_analysis": "/native/system-audio/analyze",
                "native_source_routes": "/native/system-audio/routes",
                "native_temp_status": "/native/system-audio/temp",
                "native_temp_cleanup": "/native/system-audio/cleanup",
                "native_temp_analysis_raw_audio_policy": "temp",
                "analysis_path": "loopback input device or explicit native temp buffer handoff",
            },
        )
    if platform.startswith("win"):
        return SystemAudioStatus(
            platform=platform,
            status="adapter_pending",
            supported=False,
            capture_strategy="wasapi-loopback-native-adapter",
            adapter=None,
            summary="Windows system-output capture should use WASAPI loopback in a future native adapter.",
            setup_steps=[
                "Use a WASAPI loopback-capable native adapter when the desktop shell is added.",
                "Until then, expose a virtual cable as an input device and select it in the live source selector.",
            ],
            warnings=["No WASAPI loopback adapter is implemented in the current Python web daemon."],
        )
    if platform.startswith("linux"):
        return SystemAudioStatus(
            platform=platform,
            status="adapter_pending",
            supported=False,
            capture_strategy="pipewire-pulseaudio-or-jack-monitor",
            adapter=None,
            summary="Linux system-output capture should use PipeWire, PulseAudio monitor devices, or JACK in a future adapter.",
            setup_steps=[
                "Select a PipeWire/PulseAudio monitor source if the browser exposes it as an input.",
                "A native app-shell adapter should enumerate monitor devices directly in a later phase.",
            ],
            warnings=["No PipeWire/PulseAudio/JACK adapter is implemented in the current Python web daemon."],
        )
    return SystemAudioStatus(
        platform=platform,
        status="unsupported",
        supported=False,
        capture_strategy="none",
        adapter=None,
        summary="No system-output capture path is defined for this platform yet.",
        warnings=["Use file input or live input until a platform adapter is added."],
    )


def is_loopback_device_label(label: str) -> bool:
    lowered = label.lower()
    return any(keyword in lowered for keyword in LOOPBACK_KEYWORDS)


def classify_browser_audio_device(label: str, device_id: str | None = None) -> dict[str, Any]:
    is_loopback = is_loopback_device_label(label)
    return {
        "device_id": device_id,
        "label": label,
        "source_type": "system_output" if is_loopback else "live_input",
        "is_loopback_candidate": is_loopback,
        "confidence": "medium" if is_loopback else "undetermined",
        "notes": (
            ["Device label matches a known loopback/system-output pattern."]
            if is_loopback
            else ["Device label does not identify this as a system-output loopback source."]
        ),
    }


def system_audio_status_dict(platform: str | None = None) -> dict[str, Any]:
    return to_dict(system_audio_status(platform))
