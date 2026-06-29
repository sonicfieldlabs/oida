from __future__ import annotations

from typing import Any


SIGNAL_FIELDS = {
    "rmsDbfs": "RMS",
    "peakDbfs": "Peak",
    "spectralCentroidHz": "Centroid",
    "spectralRolloffHz": "Rolloff",
    "silenceRatio": "Silence ratio",
    "clippedSampleRatio": "Clipped sample ratio",
}


def compare_route_events(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    signal_fields: list[str] | None = None,
    min_abs_signal_delta: float | None = None,
    changed_only: bool = False,
) -> dict[str, Any]:
    previous_routes = _route_ids(previous)
    current_routes = _route_ids(current)
    added_routes = [route for route in current_routes if route not in previous_routes]
    removed_routes = [route for route in previous_routes if route not in current_routes]
    previous_warnings = _warnings(previous)
    current_warnings = _warnings(current)
    added_warnings = [warning for warning in current_warnings if warning not in previous_warnings]
    resolved_warnings = [warning for warning in previous_warnings if warning not in current_warnings]
    same_segment = _same_segment(previous, current)
    summary_from = _short_summary(previous)
    summary_to = _short_summary(current)
    summary_changed = summary_from != summary_to
    effective_min_delta = _normalized_min_abs_delta(min_abs_signal_delta)
    if changed_only:
        effective_min_delta = max(effective_min_delta, 0.000_001)
    signal_delta = _signal_delta(
        previous,
        current,
        signal_fields=signal_fields,
        min_abs_signal_delta=effective_min_delta,
    )
    return {
        "version": "0.1",
        "base_event_id": previous.get("id"),
        "current_event_id": current.get("id"),
        "source_label": _source_label(current) or _source_label(previous),
        "same_segment": same_segment,
        "previous_routes": previous_routes,
        "current_routes": current_routes,
        "added_routes": added_routes,
        "removed_routes": removed_routes,
        "shared_routes": [route for route in current_routes if route in previous_routes],
        "summary_shift": {
            "from": summary_from,
            "to": summary_to,
            "changed": summary_changed,
        },
        "signal_delta": signal_delta,
        "warning_delta": {
            "added": added_warnings,
            "resolved": resolved_warnings,
        },
        "change_flags": {
            "routes_changed": bool(added_routes or removed_routes),
            "summary_changed": summary_changed,
            "warnings_changed": bool(added_warnings or resolved_warnings),
            "signal_changed": bool(signal_delta),
        },
        "applied_filters": {
            "signal_fields": _normalized_signal_fields(signal_fields),
            "min_abs_signal_delta": effective_min_delta,
            "changed_only": bool(changed_only),
        },
        "notes": _comparison_notes(same_segment),
    }


def _same_segment(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_segment = previous.get("segment") if isinstance(previous.get("segment"), dict) else {}
    current_segment = current.get("segment") if isinstance(current.get("segment"), dict) else {}
    previous_ref = previous_segment.get("data_ref") if isinstance(previous_segment.get("data_ref"), dict) else {}
    current_ref = current_segment.get("data_ref") if isinstance(current_segment.get("data_ref"), dict) else {}
    if previous_ref.get("sha256") and current_ref.get("sha256"):
        return previous_ref.get("sha256") == current_ref.get("sha256")
    if previous_ref.get("uri") and current_ref.get("uri"):
        return str(previous_ref.get("uri")) == str(current_ref.get("uri"))
    return bool(previous.get("id") and previous.get("id") == current.get("id"))


def _route_ids(event: dict[str, Any]) -> list[str]:
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    return [
        str(route.get("route_id"))
        for route in routes
        if isinstance(route, dict) and route.get("route_id")
    ]


def _short_summary(event: dict[str, Any]) -> str:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    value = aggregate.get("short_summary") or aggregate.get("title") or ""
    return str(value)


def _source_label(event: dict[str, Any]) -> str:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    return str(source.get("label") or "")


def _warnings(event: dict[str, Any]) -> list[str]:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    warnings = aggregate.get("warnings") if isinstance(aggregate.get("warnings"), list) else []
    return [str(warning) for warning in warnings if warning]


def _signal_delta(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    signal_fields: list[str] | None = None,
    min_abs_signal_delta: float | None = None,
) -> dict[str, dict[str, float | str]]:
    previous_features = previous.get("features") if isinstance(previous.get("features"), dict) else {}
    current_features = current.get("features") if isinstance(current.get("features"), dict) else {}
    deltas: dict[str, dict[str, float | str]] = {}
    minimum = _normalized_min_abs_delta(min_abs_signal_delta)
    field_keys = _normalized_signal_fields(signal_fields)
    fields = {key: SIGNAL_FIELDS[key] for key in field_keys}
    for key, label in fields.items():
        before = previous_features.get(key)
        after = current_features.get(key)
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            delta = float(after) - float(before)
            if abs(delta) < minimum:
                continue
            deltas[key] = {
                "label": label,
                "from": float(before),
                "to": float(after),
                "delta": delta,
            }
    return deltas


def _normalized_signal_fields(signal_fields: list[str] | None) -> list[str]:
    if not signal_fields:
        return list(SIGNAL_FIELDS.keys())
    selected = [field for field in signal_fields if field in SIGNAL_FIELDS]
    return selected or list(SIGNAL_FIELDS.keys())


def _normalized_min_abs_delta(value: float | None) -> float:
    if value is None:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _comparison_notes(same_segment: bool) -> list[str]:
    notes = [
        "Route comparison uses existing event summaries plus deterministic DSP feature deltas.",
        "Signal deltas do not add new perceptual claims; they compare numeric features already produced for each event.",
    ]
    if same_segment:
        notes.insert(0, "The compared route outputs point at the same audio segment.")
    else:
        notes.insert(0, "The compared route outputs do not prove they used the same audio segment.")
    return notes
