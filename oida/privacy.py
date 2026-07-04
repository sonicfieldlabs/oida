from __future__ import annotations

import copy
from typing import Any


def redact_event_audio_for_policy(event: dict[str, Any], raw_audio_policy: str | None = None) -> dict[str, Any]:
    """Return an event safe for durable derived-data stores.

    `temp` and `not_stored` events can be useful as structured evidence, but their
    local raw-audio paths should not become durable references in conversation,
    generation, memory, or export records.
    """
    policy = str(raw_audio_policy or event.get("raw_audio_policy") or "external_ref")
    if policy in {"saved", "external_ref"}:
        return event

    cleaned = copy.deepcopy(event)
    _redact_segment(cleaned.get("segment"))
    _redact_source(cleaned.get("source"))
    return cleaned


def _redact_segment(segment: Any) -> None:
    if not isinstance(segment, dict):
        return
    data_ref = segment.get("data_ref")
    if isinstance(data_ref, dict) and data_ref.get("uri"):
        data_ref["uri"] = None
        data_ref["redacted"] = True
    source = segment.get("source")
    _redact_source(source)


def _redact_source(source: Any) -> None:
    if not isinstance(source, dict):
        return
    details = source.get("details")
    if isinstance(details, dict) and details.get("path"):
        details["path"] = None
        details["redacted"] = True
