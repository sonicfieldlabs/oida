from __future__ import annotations

import sys
from typing import Any, Mapping

SYSTEM_AUDIO_CAPTURE_SCOPES = {"display_mix", "application_mix", "window_mix", "loopback_device", "unknown"}
SYSTEM_AUDIO_ROUTE_DEFAULT_ADAPTER = "macos-screencapturekit-system-audio"


def normalize_system_audio_source_route(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    incoming = value if isinstance(value, Mapping) else {}
    capture_scope = str(incoming.get("capture_scope") or incoming.get("captureScope") or "display_mix")
    if capture_scope not in SYSTEM_AUDIO_CAPTURE_SCOPES:
        capture_scope = "unknown"

    route = {
        "route_id": str(incoming.get("route_id") or incoming.get("routeId") or _default_route_id(capture_scope)),
        "route_kind": "system_audio_source_route",
        "source_type": "system_output",
        "capture_scope": capture_scope,
        "adapter": str(incoming.get("adapter") or SYSTEM_AUDIO_ROUTE_DEFAULT_ADAPTER),
        "label": str(incoming.get("label") or _default_route_label(capture_scope)),
        "platform": str(incoming.get("platform") or sys.platform),
        "display": _display_info(incoming),
        "application": _application_info(incoming),
        "window": _window_info(incoming),
        "excluded_current_process_audio": bool(
            incoming.get("excluded_current_process_audio", incoming.get("excludedCurrentProcessAudio", True))
        ),
        "excluded_applications": _string_list(
            incoming.get("excluded_applications") or incoming.get("excludedApplications") or []
        ),
        "route_capabilities": {
            "supports_display_mix": True,
            "supports_application_filtering": capture_scope in {"application_mix", "display_mix"},
            "supports_window_filtering": capture_scope in {"window_mix", "display_mix"},
            "supports_loopback_device": capture_scope == "loopback_device",
        },
        "model_input_policy": {
            "moss_audio": "16_khz_mono",
            "native_buffer": "bounded_mono_temp_wav",
            "raw_audio_policy": "temp",
        },
        "claim_limits": [
            "Source route identifies the capture filter, not individual sounding objects.",
            "MOSS-Audio receives 16 kHz mono after capture; stereo image and >8 kHz claims require separate DSP/capture evidence.",
        ],
    }
    return route


def native_system_audio_route_manifest(platform: str | None = None) -> dict[str, Any]:
    current_platform = platform or sys.platform
    supported = current_platform == "darwin"
    route = normalize_system_audio_source_route(
        {
            "route_id": "native-display-mix",
            "capture_scope": "display_mix",
            "label": "Display system mix",
            "platform": current_platform,
            "excluded_current_process_audio": True,
            "excluded_applications": ["oida"],
        }
    )
    return {
        "version": "0.1",
        "platform": current_platform,
        "supported": supported,
        "default_route_id": route["route_id"],
        "routes": [route],
        "notes": [
            "Native ScreenCaptureKit routing currently captures the selected display's system mix.",
            "Application/window-specific routes are represented in the schema for future filters, but the native shell defaults to display_mix.",
        ],
    }


def system_audio_source_label(source_label: str | None, source_route: Mapping[str, Any]) -> str:
    base = (source_label or "").strip()
    route_label = str(source_route.get("label") or "").strip()
    if not base or base == "Native system audio":
        return f"Native system audio / {route_label}" if route_label else "Native system audio"
    return base


def _default_route_id(capture_scope: str) -> str:
    return {
        "display_mix": "native-display-mix",
        "application_mix": "native-application-mix",
        "window_mix": "native-window-mix",
        "loopback_device": "loopback-device",
    }.get(capture_scope, "system-audio-route")


def _default_route_label(capture_scope: str) -> str:
    return {
        "display_mix": "Display system mix",
        "application_mix": "Application system mix",
        "window_mix": "Window system mix",
        "loopback_device": "Loopback device",
    }.get(capture_scope, "System audio route")


def _display_info(value: Mapping[str, Any]) -> dict[str, Any] | None:
    display = value.get("display")
    if isinstance(display, Mapping):
        return {key: display[key] for key in ("id", "width", "height", "frame") if key in display}
    display_id = value.get("display_id", value.get("displayId"))
    if display_id is None:
        return None
    info: dict[str, Any] = {"id": str(display_id)}
    width = value.get("display_width", value.get("displayWidth"))
    height = value.get("display_height", value.get("displayHeight"))
    if width is not None:
        info["width"] = _number(width)
    if height is not None:
        info["height"] = _number(height)
    return info


def _application_info(value: Mapping[str, Any]) -> dict[str, Any] | None:
    app = value.get("application")
    if isinstance(app, Mapping):
        return {
            key: app[key]
            for key in ("name", "bundle_id", "process_id")
            if key in app and app[key] is not None
        }
    app_name = value.get("application_name", value.get("applicationName"))
    bundle_id = value.get("bundle_id", value.get("bundleId"))
    process_id = value.get("process_id", value.get("processId"))
    if app_name is None and bundle_id is None and process_id is None:
        return None
    return {"name": app_name, "bundle_id": bundle_id, "process_id": process_id}


def _window_info(value: Mapping[str, Any]) -> dict[str, Any] | None:
    window = value.get("window")
    if isinstance(window, Mapping):
        return {key: window[key] for key in ("id", "title", "owner_name", "owner_bundle_id") if key in window}
    window_id = value.get("window_id", value.get("windowId"))
    if window_id is None:
        return None
    return {"id": str(window_id)}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _number(value: Any) -> int | float | str:
    if isinstance(value, (int, float)):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return str(value)
    return int(parsed) if parsed.is_integer() else parsed
