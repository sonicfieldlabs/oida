"""Evaluate receiving-time freshness without modifying the attributed MASA record."""
from datetime import datetime


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def evaluate_observation(record, observation, *, now):
    freshness = observation.get("freshness", {})
    source = next((s for s in record.get("sources", []) if s.get("id") == observation.get("sourceRef")), {})
    declared = freshness.get("status", "unknown")
    result = dict(evaluated_at=now, declared_status=declared, status="unknown", current=False,
                  admission="attributed-account-only", execution="not_requested")
    clock, observed = _time(now), _time(observation.get("observedAt"))
    expiry = _time(freshness.get("expiresAt"))
    reason = freshness.get("reason", "")
    if source.get("sourceKind") == "local-fixture" or reason in {"fixture", "archive"}:
        result["reason"] = reason if reason in {"fixture", "archive"} else "fixture"
    elif clock is None or observed is None:
        result["reason"] = "unusable-observation-clock"
    elif observed > clock:
        result["reason"] = "future-source-time"
    elif declared in {"stale", "expired"}:
        result.update(status=declared, reason="producer-declared-" + declared)
    elif expiry is not None and clock >= expiry:
        result.update(status="expired", reason="expired-at-reception")
    elif declared != "current" or expiry is None:
        result["reason"] = "no-current-validity-window"
    else:
        result.update(status="current", current=True, reason="within-declared-validity-window")
    return result
