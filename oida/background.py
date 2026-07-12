from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from oida.config import data_dir
from oida.contracts import new_id, now_iso
from oida.akouo_skills import route_preset
from oida.native_temp_audio import default_native_temp_audio_retention, normalize_native_temp_audio_retention
from oida.raw_audio import default_upload_audio_retention, normalize_upload_audio_retention
from oida.storage import write_json_atomic


def _synchronized(method):
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


@dataclass
class BackgroundConfig:
    enabled: bool = True
    paused: bool = False
    launch_at_login: bool = False
    show_floating_agent: bool = True
    floating_agent: dict[str, Any] = field(
        default_factory=lambda: {
            "visible": True,
            "size": "compact",
            "pinned": True,
            "x": None,
            "y": None,
            "reduced_motion": False,
        }
    )
    default_capture_seconds: float = 10.0
    default_capture_direction: str = "past"  # past = ring slice before the trigger; future = window after it
    default_route_preset: str = "basic"
    incognito: bool = False
    save_events_by_default: bool = False
    hotkeys: dict[str, str | None] = field(
        default_factory=lambda: {
            "capture_last_buffer": None,
            "hold_to_listen": None,
            "open_dashboard": None,
        }
    )
    native_temp_audio_retention: dict[str, Any] = field(default_factory=default_native_temp_audio_retention)
    upload_audio_retention: dict[str, Any] = field(default_factory=default_upload_audio_retention)
    recent_history: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": True,
            "persist": True,
            "max_events": 12,
            "max_pinned": 8,
            "include_incognito": False,
        }
    )


@dataclass
class BackgroundState:
    active_live_session_id: str | None = None
    status: str = "idle"
    updated_at: str = field(default_factory=now_iso)
    last_action_id: str | None = None
    last_error: str | None = None
    latest_event: dict[str, Any] | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    pinned_events: list[dict[str, Any]] = field(default_factory=list)
    # A pending system-audio capture request. Any surface (web dashboard) may
    # file one; the native shell polls status, claims it, performs the tap
    # capture, and analyzes. This is how one daemon state drives all surfaces.
    capture_request: dict[str, Any] | None = None


class BackgroundRuntime:
    RECENT_EVENT_LIMIT = 12

    def __init__(
        self,
        config_path: str | Path | None = None,
        history_path: str | Path | None = None,
        archive_dir: str | Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.config_path = Path(config_path) if config_path else data_dir() / "settings" / "background.json"
        self.history_path = Path(history_path) if history_path else self._default_history_path(config_path)
        self.archive_dir = Path(archive_dir) if archive_dir else self._default_history_archive_dir(config_path)
        self.config = self._load_config()
        history = self._load_recent_history()
        recent_events = history["recent_events"]
        pinned_events = history["pinned_events"]
        self.state = BackgroundState(
            status="paused" if self.config.paused else "idle",
            latest_event=(recent_events or pinned_events or [None])[0],
            recent_events=recent_events,
            pinned_events=pinned_events,
        )

    @_synchronized
    def status(self) -> dict[str, Any]:
        state = asdict(self.state)
        live_request = self._live_capture_request()
        state["capture_request"] = (
            {key: value for key, value in live_request.items() if key != "requested_monotonic"} if live_request else None
        )
        return {
            "version": "0.1",
            "mode": "background-runtime",
            "config": asdict(self.config),
            "state": state,
            "capabilities": {
                "daemon_background_runtime": True,
                "quick_capture_api": True,
                "native_tray": False,
                "global_hotkeys": False,
                "launch_at_login": False,
                "desktop_shell_required": True,
                "desktop_shell_target": "apps/macos",
                "native_shell_api": True,
                "live_signal_api": True,
                "route_rerun_api": True,
                "daemon_supervision": "native_shell",
                "native_system_audio_signal_tap": True,
                "native_system_audio_temp_analysis": True,
                "native_temp_audio_cleanup": True,
                "raw_audio_wipe_api": True,
                "recent_result_history": True,
                "durable_recent_history": True,
                "pinned_recent_results": True,
                "recent_history_management": True,
                "recent_history_archive": True,
                "recent_history_batch_review": True,
                "generation_prompt_api": True,
                "generation_relisten_api": True,
            },
            "notes": [
                "The daemon can stay running and perform quick captures from an active live session.",
                "The native macOS shell in apps/macos controls this daemon through the background API.",
                "The daemon does not register OS tray/menu bar controls or global hotkeys by itself.",
            ],
        }

    @_synchronized
    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        data = asdict(self.config)
        for key, value in updates.items():
            if key not in data:
                continue
            if key in {"floating_agent", "hotkeys", "native_temp_audio_retention", "upload_audio_retention", "recent_history"} and isinstance(value, dict):
                if key == "floating_agent":
                    merged = dict(data["floating_agent"])
                    for setting, setting_value in value.items():
                        if setting in merged:
                            merged[setting] = setting_value
                    data[key] = merged
                elif key == "hotkeys":
                    merged = dict(data["hotkeys"])
                    for hotkey, binding in value.items():
                        if hotkey in merged:
                            merged[hotkey] = str(binding) if binding else None
                    data[key] = merged
                else:
                    if key == "native_temp_audio_retention":
                        merged = dict(data["native_temp_audio_retention"])
                        merged.update(value)
                        data[key] = normalize_native_temp_audio_retention(merged)
                    elif key == "upload_audio_retention":
                        merged = dict(data["upload_audio_retention"])
                        merged.update(value)
                        data[key] = normalize_upload_audio_retention(merged)
                    else:
                        merged = dict(data["recent_history"])
                        merged.update(value)
                        data[key] = normalize_recent_history_config(merged)
            else:
                data[key] = value
        data = normalize_background_config_data(data)
        self.config = BackgroundConfig(**data)
        self._write_config()
        self.state.recent_events = self.state.recent_events[: self._recent_limit()]
        self.state.pinned_events = self.state.pinned_events[: self._pinned_limit()]
        self._write_recent_history()
        self.state.status = "paused" if self.config.paused else self.state.status
        self.state.updated_at = now_iso()
        return self.status()

    @_synchronized
    def pause(self) -> dict[str, Any]:
        self.config.paused = True
        self.state.status = "paused"
        self.state.updated_at = now_iso()
        self._write_config()
        return self.status()

    @_synchronized
    def resume(self) -> dict[str, Any]:
        self.config.paused = False
        self.state.status = "idle" if not self.state.active_live_session_id else "listening"
        self.state.updated_at = now_iso()
        self._write_config()
        return self.status()

    @_synchronized
    def set_active_live_session(self, session_id: str | None) -> None:
        self.state.active_live_session_id = session_id
        if self.config.paused:
            self.state.status = "paused"
        elif session_id:
            self.state.status = "listening"
        else:
            self.state.status = "idle"
        self.state.updated_at = now_iso()

    CAPTURE_REQUEST_TTL_SECONDS = 30.0

    @_synchronized
    def request_capture(self, seconds: float | None = None, route_preset: str | None = None) -> dict[str, Any]:
        request = {
            "id": f"capreq-{uuid4().hex[:10]}",
            "seconds": float(seconds) if isinstance(seconds, (int, float)) and seconds and seconds > 0 else self.config.default_capture_seconds,
            "route_preset": route_preset or self.config.default_route_preset,
            "requested_at": now_iso(),
            "requested_monotonic": time.monotonic(),
            "status": "pending",
        }
        self.state.capture_request = request
        self.state.updated_at = now_iso()
        return {key: value for key, value in request.items() if key != "requested_monotonic"}

    @_synchronized
    def claim_capture_request(self, request_id: str | None = None) -> dict[str, Any] | None:
        request = self._live_capture_request()
        if request is None:
            return None
        if request_id and request.get("id") != request_id:
            return None
        self.state.capture_request = None
        self.state.updated_at = now_iso()
        claimed = {key: value for key, value in request.items() if key != "requested_monotonic"}
        claimed["status"] = "claimed"
        return claimed

    @_synchronized
    def cancel_capture_request(self, request_id: str | None = None) -> dict[str, Any] | None:
        request = self._live_capture_request()
        if request is None:
            return None
        if request_id and request.get("id") != request_id:
            return None
        self.state.capture_request = None
        self.state.updated_at = now_iso()
        cancelled = {key: value for key, value in request.items() if key != "requested_monotonic"}
        cancelled["status"] = "cancelled"
        return cancelled

    def _live_capture_request(self) -> dict[str, Any] | None:
        request = self.state.capture_request
        if not isinstance(request, dict):
            return None
        requested = request.get("requested_monotonic")
        if isinstance(requested, (int, float)) and (time.monotonic() - requested) > self.CAPTURE_REQUEST_TTL_SECONDS:
            self.state.capture_request = None
            return None
        return request

    @_synchronized
    def begin_action(self, action: str) -> str:
        if self.config.paused:
            raise RuntimeError("background runtime is paused")
        action_id = f"{action}_{new_id('bg')}"
        self.state.last_action_id = action_id
        self.state.status = "capturing" if action == "capture" else action
        self.state.last_error = None
        self.state.updated_at = now_iso()
        return action_id

    @_synchronized
    def finish_action(self, event: dict[str, Any] | None = None) -> None:
        if event is None:
            self.state.latest_event = None
        elif self._history_can_include_event(event):
            self.state.latest_event = event
            self._remember_recent_event(event)
        else:
            # Incognito events are intentionally NOT retained in latest_event. Otherwise
            # they leak through the unauthenticated GET /background/status and become the
            # silent fallback event for /conversation/ask and /generation/prompt, which
            # would persist incognito-derived data to disk. The capture response still
            # returns the event for immediate UI use.
            pass
        self.state.status = "result_ready" if event else ("listening" if self.state.active_live_session_id else "idle")
        self.state.last_error = None
        self.state.updated_at = now_iso()

    @_synchronized
    def fail_action(self, error: str) -> None:
        self.state.last_error = error
        self.state.status = "error"
        self.state.updated_at = now_iso()

    @_synchronized
    def history(self) -> dict[str, Any]:
        return self.filtered_history()

    @_synchronized
    def filtered_history(
        self,
        *,
        route: str | None = None,
        source_type: str | None = None,
        raw_audio_policy: str | None = None,
        privacy_mode: str | None = None,
        q: str | None = None,
        rerunnable: bool | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        events = [
            event
            for event in self.state.recent_events
            if _history_event_matches(
                event,
                route=route,
                source_type=source_type,
                raw_audio_policy=raw_audio_policy,
                privacy_mode=privacy_mode,
                q=q,
                rerunnable=rerunnable,
            )
        ]
        pinned_events = [
            event
            for event in self.state.pinned_events
            if _history_event_matches(
                event,
                route=route,
                source_type=source_type,
                raw_audio_policy=raw_audio_policy,
                privacy_mode=privacy_mode,
                q=q,
                rerunnable=rerunnable,
            )
        ]
        if limit is not None:
            events = events[: max(1, min(self._recent_limit(), int(limit)))]
        return {
            "version": "0.1",
            "limit": self._recent_limit(),
            "pinned_limit": self._pinned_limit(),
            "persistent": self._recent_persistence_enabled(),
            "history_path": str(self.history_path),
            "raw_audio_policy": "Derived event history only; raw audio is not copied into this file.",
            "filters": {
                "route": route,
                "source_type": source_type,
                "raw_audio_policy": raw_audio_policy,
                "privacy_mode": privacy_mode,
                "q": q,
                "rerunnable": rerunnable,
                "limit": limit,
            },
            "latest_event": self.state.latest_event,
            "pinned_events": pinned_events,
            "recent_events": events,
            "counts": {
                "pinned": len(pinned_events),
                "recent": len(events),
                "total_stored_pinned": len(self.state.pinned_events),
                "total_stored_recent": len(self.state.recent_events),
            },
        }

    @_synchronized
    def export_history(self, event_ids: list[str] | None = None) -> dict[str, Any]:
        payload = self._history_payload_for_event_ids(event_ids) if event_ids else self.history()
        payload["exported_at"] = now_iso()
        payload["export_kind"] = "derived_recent_result_history"
        return payload

    @_synchronized
    def archive_history(self, event_ids: list[str] | None = None, label: str | None = None) -> dict[str, Any]:
        payload = self.export_history(event_ids=event_ids)
        payload["archive_kind"] = "derived_recent_result_history_archive"
        payload["archived_at"] = now_iso()
        payload["archive_label"] = _safe_archive_label(label)

        selected_ids = _unique_event_ids(event_ids or [])
        stem_parts = [payload["archived_at"].replace(":", "").replace(".", "")]
        if payload["archive_label"]:
            stem_parts.append(payload["archive_label"])
        if selected_ids:
            stem_parts.append(f"{len(selected_ids)}-selected")
        path = self.archive_dir / ("-".join(stem_parts) + ".json")
        payload["archive_path"] = str(path)
        write_json_atomic(path, payload)
        return {
            "version": "0.1",
            "archived": True,
            "archive_path": str(path),
            "archive_label": payload["archive_label"],
            "event_count": len(payload["selected_events"]) if "selected_events" in payload else len(payload.get("recent_events") or []) + len(payload.get("pinned_events") or []),
            "selected_event_ids": selected_ids,
            "raw_audio_policy": payload["raw_audio_policy"],
            "history": payload,
        }

    @_synchronized
    def clear_history(self, *, keep_pinned: bool = True) -> dict[str, Any]:
        self.state.recent_events = []
        if not keep_pinned:
            self.state.pinned_events = []
        self.state.latest_event = self.state.pinned_events[0] if self.state.pinned_events else None
        self.state.updated_at = now_iso()
        self._write_recent_history()
        return {
            "version": "0.1",
            "cleared": True,
            "keep_pinned": keep_pinned,
            "background": self.status(),
            "history": self.history(),
        }

    @_synchronized
    def set_pinned_event(self, event_id: str, *, pinned: bool = True) -> dict[str, Any]:
        normalized_event_id = str(event_id or "").strip()
        result = self.set_pinned_events([normalized_event_id], pinned=pinned)
        if not normalized_event_id or normalized_event_id in result["missing_event_ids"]:
            raise KeyError(event_id)
        return {
            "version": "0.1",
            "event_id": normalized_event_id,
            "pinned": pinned,
            "background": result["background"],
            "history": result["history"],
        }

    @_synchronized
    def set_pinned_events(self, event_ids: list[str], *, pinned: bool = True) -> dict[str, Any]:
        requested_ids = _unique_event_ids(event_ids)
        found_events: list[dict[str, Any]] = []
        missing_ids: list[str] = []
        for event_id in requested_ids:
            event = self._find_history_event(event_id)
            if event:
                found_events.append(event)
            else:
                missing_ids.append(event_id)
        for event in found_events:
            if not self._history_can_include_event(event):
                raise ValueError("incognito events cannot be pinned with the current history policy")

        found_ids = [str(event.get("id")) for event in found_events if event.get("id")]
        found_id_set = set(found_ids)
        pinned_events = [item for item in self.state.pinned_events if item.get("id") not in found_id_set]
        if pinned:
            pinned_events = [_pinned_history_event(event) for event in found_events] + pinned_events
        self.state.pinned_events = pinned_events[: self._pinned_limit()]
        self.state.updated_at = now_iso()
        self._write_recent_history()
        return {
            "version": "0.1",
            "event_ids": requested_ids,
            "pinned_event_ids": found_ids,
            "missing_event_ids": missing_ids,
            "pinned": pinned,
            "background": self.status(),
            "history": self.history(),
        }

    def _remember_recent_event(self, event: dict[str, Any]) -> None:
        if not self._recent_history_enabled() or not self._history_can_include_event(event):
            return
        event_id = event.get("id")
        recent = [item for item in self.state.recent_events if not event_id or item.get("id") != event_id]
        recent.insert(0, dict(event))
        self.state.recent_events = recent[: self._recent_limit()]
        if event_id:
            self.state.pinned_events = [
                dict(event) if item.get("id") == event_id else item
                for item in self.state.pinned_events
            ][: self._pinned_limit()]
        self._write_recent_history()

    def _find_history_event(self, event_id: str) -> dict[str, Any] | None:
        for event in [*self.state.pinned_events, *self.state.recent_events]:
            if event.get("id") == event_id:
                return event
        return None

    def _history_payload_for_event_ids(self, event_ids: list[str] | None) -> dict[str, Any]:
        selected_ids = _unique_event_ids(event_ids or [])
        if not selected_ids:
            return self.history()
        selected_events: list[dict[str, Any]] = []
        missing_ids: list[str] = []
        for event_id in selected_ids:
            event = self._find_history_event(event_id)
            if event:
                selected_events.append(event)
            else:
                missing_ids.append(event_id)
        pinned_id_set = {str(event.get("id")) for event in self.state.pinned_events if event.get("id") in selected_ids}
        pinned_events = [event for event in selected_events if event.get("id") in pinned_id_set]
        recent_events = [event for event in selected_events if event.get("id") not in pinned_id_set]
        return {
            "version": "0.1",
            "limit": self._recent_limit(),
            "pinned_limit": self._pinned_limit(),
            "persistent": self._recent_persistence_enabled(),
            "history_path": str(self.history_path),
            "raw_audio_policy": "Derived event history only; raw audio is not copied into this file.",
            "filters": {"event_ids": selected_ids},
            "latest_event": selected_events[0] if selected_events else self.state.latest_event,
            "pinned_events": pinned_events,
            "recent_events": recent_events,
            "selected_events": selected_events,
            "selected_event_ids": selected_ids,
            "missing_event_ids": missing_ids,
            "counts": {
                "pinned": len(pinned_events),
                "recent": len(recent_events),
                "selected": len(selected_events),
                "missing": len(missing_ids),
                "total_stored_pinned": len(self.state.pinned_events),
                "total_stored_recent": len(self.state.recent_events),
            },
        }

    def _load_config(self) -> BackgroundConfig:
        if not self.config_path.exists():
            return BackgroundConfig()
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return BackgroundConfig()
        if not isinstance(data, dict):
            return BackgroundConfig()
        valid = normalize_background_config_data({key: data[key] for key in BackgroundConfig.__dataclass_fields__ if key in data})
        try:
            return BackgroundConfig(**valid)
        except TypeError:
            return BackgroundConfig()

    def _write_config(self) -> None:
        write_json_atomic(self.config_path, asdict(self.config))

    def _load_recent_history(self) -> dict[str, list[dict[str, Any]]]:
        if not self._recent_history_enabled() or not self.history_path.exists():
            return {"recent_events": [], "pinned_events": []}
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"recent_events": [], "pinned_events": []}
        if isinstance(data, dict):
            events = data.get("recent_events")
            pinned = data.get("pinned_events")
        else:
            events = data
            pinned = []
        if not isinstance(events, list):
            events = []
        if not isinstance(pinned, list):
            pinned = []
        return {
            "recent_events": self._normalize_history_events(events, self._recent_limit()),
            "pinned_events": self._normalize_history_events(pinned, self._pinned_limit()),
        }

    def _normalize_history_events(self, events: list[Any], limit: int) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in events:
            if not isinstance(event, dict) or not self._history_can_include_event(event):
                continue
            event_id = str(event.get("id") or "")
            if event_id and event_id in seen:
                continue
            if event_id:
                seen.add(event_id)
            normalized.append(event)
            if len(normalized) >= limit:
                break
        return normalized

    def _write_recent_history(self) -> None:
        if not self._recent_persistence_enabled():
            return
        payload = {
            "version": "0.1",
            "updated_at": now_iso(),
            "limit": self._recent_limit(),
            "pinned_limit": self._pinned_limit(),
            "raw_audio_policy": "Derived event history only; raw audio is not copied into this file.",
            "pinned_events": self.state.pinned_events[: self._pinned_limit()],
            "recent_events": self.state.recent_events[: self._recent_limit()],
        }
        write_json_atomic(self.history_path, payload)

    def _history_can_include_event(self, event: dict[str, Any]) -> bool:
        if self.config.recent_history.get("include_incognito") is True:
            return True
        return event.get("privacy_mode") != "incognito"

    def _recent_history_enabled(self) -> bool:
        return self.config.recent_history.get("enabled") is not False

    def _recent_persistence_enabled(self) -> bool:
        return self._recent_history_enabled() and self.config.recent_history.get("persist") is not False

    def _recent_limit(self) -> int:
        value = self.config.recent_history.get("max_events", self.RECENT_EVENT_LIMIT)
        try:
            return max(1, min(50, int(value)))
        except (TypeError, ValueError):
            return self.RECENT_EVENT_LIMIT

    def _pinned_limit(self) -> int:
        value = self.config.recent_history.get("max_pinned", 8)
        try:
            return max(1, min(25, int(value)))
        except (TypeError, ValueError):
            return 8

    def _default_history_path(self, config_path: str | Path | None) -> Path:
        if config_path:
            return Path(config_path).with_name("recent-results.json")
        return data_dir() / "sessions" / "recent-results.json"

    def _default_history_archive_dir(self, config_path: str | Path | None) -> Path:
        if config_path:
            return Path(config_path).with_name("history-archives")
        return data_dir() / "exports" / "history"


def normalize_recent_history_config(value: Any) -> dict[str, Any]:
    default = {
        "enabled": True,
        "persist": True,
        "max_events": BackgroundRuntime.RECENT_EVENT_LIMIT,
        "max_pinned": 8,
        "include_incognito": False,
    }
    if not isinstance(value, dict):
        return default
    normalized = dict(default)
    for key in normalized:
        if key in value:
            normalized[key] = value[key]
    try:
        normalized["max_events"] = max(1, min(50, int(normalized["max_events"])))
    except (TypeError, ValueError):
        normalized["max_events"] = default["max_events"]
    try:
        normalized["max_pinned"] = max(1, min(25, int(normalized["max_pinned"])))
    except (TypeError, ValueError):
        normalized["max_pinned"] = default["max_pinned"]
    normalized["enabled"] = _coerce_bool(normalized["enabled"], default["enabled"])
    normalized["persist"] = _coerce_bool(normalized["persist"], default["persist"])
    normalized["include_incognito"] = _coerce_bool(
        normalized["include_incognito"], default["include_incognito"]
    )
    return normalized


def normalize_background_config_data(value: dict[str, Any]) -> dict[str, Any]:
    default = asdict(BackgroundConfig())
    data = dict(default)
    for key in default:
        if key in value:
            data[key] = value[key]

    for key in ("enabled", "paused", "launch_at_login", "show_floating_agent", "incognito", "save_events_by_default"):
        data[key] = _coerce_bool(data.get(key), default[key])

    data["floating_agent"] = normalize_floating_agent_config(data.get("floating_agent"))
    data["default_capture_seconds"] = _bounded_float(data.get("default_capture_seconds"), 10.0, 0.25, 600.0)
    data["default_route_preset"] = _valid_route_preset(data.get("default_route_preset"), "basic")
    data["hotkeys"] = normalize_hotkeys_config(data.get("hotkeys"))
    data["native_temp_audio_retention"] = normalize_native_temp_audio_retention(data.get("native_temp_audio_retention"))
    data["upload_audio_retention"] = normalize_upload_audio_retention(data.get("upload_audio_retention"))
    data["recent_history"] = normalize_recent_history_config(data.get("recent_history"))
    return data


def normalize_floating_agent_config(value: Any) -> dict[str, Any]:
    default = {
        "visible": True,
        "size": "compact",
        "pinned": True,
        "x": None,
        "y": None,
        "reduced_motion": False,
    }
    if not isinstance(value, dict):
        return default
    data = dict(default)
    for key in data:
        if key in value:
            data[key] = value[key]
    data["visible"] = _coerce_bool(data["visible"], default["visible"])
    data["size"] = "medium" if data.get("size") == "medium" else "compact"
    data["pinned"] = _coerce_bool(data["pinned"], default["pinned"])
    data["reduced_motion"] = _coerce_bool(data["reduced_motion"], default["reduced_motion"])
    data["x"] = _optional_finite_float(data.get("x"))
    data["y"] = _optional_finite_float(data.get("y"))
    return data


def normalize_hotkeys_config(value: Any) -> dict[str, str | None]:
    default: dict[str, str | None] = {
        "capture_last_buffer": None,
        "hold_to_listen": None,
        "open_dashboard": None,
    }
    if not isinstance(value, dict):
        return default
    normalized = dict(default)
    for key in normalized:
        binding = value.get(key)
        text = str(binding).strip() if binding else ""
        normalized[key] = text[:80] if text else None
    return normalized


def _valid_route_preset(value: Any, fallback: str) -> str:
    candidate = str(value or fallback).strip() or fallback
    try:
        route_preset(candidate)
    except ValueError:
        return fallback
    return candidate


def _bounded_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        return fallback
    return parsed


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return fallback


def _optional_finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _unique_event_ids(event_ids: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for event_id in event_ids:
        normalized = str(event_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _pinned_history_event(event: dict[str, Any]) -> dict[str, Any]:
    pinned_event = dict(event)
    history = pinned_event.get("history") if isinstance(pinned_event.get("history"), dict) else {}
    pinned_event["history"] = {**history, "pinned": True, "pinned_at": now_iso()}
    return pinned_event


def _safe_archive_label(label: str | None) -> str | None:
    if not label:
        return None
    safe = "".join(char.lower() if char.isalnum() else "-" for char in str(label))
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:48] or None


def _history_event_matches(
    event: dict[str, Any],
    *,
    route: str | None,
    source_type: str | None,
    raw_audio_policy: str | None,
    privacy_mode: str | None,
    q: str | None,
    rerunnable: bool | None,
) -> bool:
    if route and route not in _event_route_ids(event):
        return False
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    if source_type and source.get("type") != source_type:
        return False
    if raw_audio_policy and event.get("raw_audio_policy") != raw_audio_policy:
        return False
    if privacy_mode and event.get("privacy_mode") != privacy_mode:
        return False
    if rerunnable is not None and _event_is_rerunnable(event) != rerunnable:
        return False
    if q and q.strip().lower() not in _event_search_text(event):
        return False
    return True


def _event_route_ids(event: dict[str, Any]) -> set[str]:
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    return {
        str(route.get("route_id"))
        for route in routes
        if isinstance(route, dict) and route.get("route_id")
    }


def _event_is_rerunnable(event: dict[str, Any]) -> bool:
    segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
    data_ref = segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
    return bool(data_ref.get("uri"))


def _event_search_text(event: dict[str, Any]) -> str:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    tags = event.get("tags") if isinstance(event.get("tags"), list) else []
    values = [
        event.get("id"),
        aggregate.get("title"),
        aggregate.get("short_summary"),
        aggregate.get("detailed_summary"),
        source.get("label"),
        source.get("type"),
        " ".join(str(tag) for tag in tags),
        " ".join(_event_route_ids(event)),
    ]
    return " ".join(str(value) for value in values if value).lower()
