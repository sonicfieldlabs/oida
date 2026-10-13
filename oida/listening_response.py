"""Small dashboard replies; complete listening and Auditum stay with their owners."""


import hashlib
import json

MAX_SUMMARY_BYTES = 128 * 1024
MAX_FIELD_BYTES = 12 * 1024


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def bounded_field(value):
    payload = encoded(value)
    if len(payload) <= MAX_FIELD_BYTES:
        return value
    if isinstance(value, dict):
        return {key: bounded_field(item) for key, item in value.items()}
    return {"representation": "retained_reference", "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def result_page(value, *, offset=0, limit=4096, expected_sha256=None):
    """Bounded JSON text pages of the permission-rechecked current projection."""
    payload = encoded(value)
    if len(payload) > 16 * 1024**2:
        raise ValueError("Retained result exceeds expansion bounds")
    sha = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and sha != expected_sha256:
        raise ValueError("Retained result or disclosure policy changed; start a new expansion")
    text = payload.decode()
    if not 0 <= offset <= len(text) or not 1 <= limit <= 4096:
        raise ValueError("Invalid result page")
    end = min(offset + limit, len(text))
    return dict(contract="oida/result-page/v1", sha256=sha, offset=offset, next_offset=end,
                has_more=end < len(text), encoding="utf-8-json-text", text=text[offset:end])


def compact_listening_result(event, *, remembered=None, remember_requested=False):
    summary = {
        key: bounded_field(event[key])
        for key in (
            "id",
            "created_at",
            "captured_at",
            "duration_ms",
            "location",
            "source",
            "segment",
            "aggregate",
            "apparatus",
            "acoustics",
            "pass_provenance",
            "specialist_evidence",
            "listening_task_status",
            "capture",
            "covenant",
        )
        if key in event
    }
    summary["routes"] = [
        {
            key: bounded_field(route[key])
            for key in ("route_id", "route_name", "summary")
            if key in route
        }
        for route in event.get("routes", [])[:16]
    ]
    # Never include session/background/history siblings in an action acknowledgement.
    saved = remembered or {}
    record_id = saved.get("akousma_id")
    result = dict(
        contract="oida/gateway/v0.6",
        response_mode="summary",
        status="complete",
        outcome="listened",
        listening_event=summary,
        akousma_id=record_id,
        trace_id=(saved.get("trace") or {}).get("id"),
        shared_error=saved.get("shared_error"),
        memory_status="saved"
        if record_id
        else (
            "failed"
            if saved.get("shared_error")
            else "withheld"
            if remember_requested
            else "not_requested"
        ),
    )

    result["result_reference"] = dict(event_id=event.get("id"), trace_id=result["trace_id"],
                                    event_url="/listening/results/" + str(event.get("id") or ""),
                                    retention="owner-rechecked; unavailable for ephemeral or unretained events")
    if len(encoded(result)) > MAX_SUMMARY_BYTES:
        result["listening_event"] = {"id": event.get("id"), "representation": "retained_reference", "sha256": hashlib.sha256(encoded(event)).hexdigest()}
    return result


def bounded_acknowledgement(result):
    """Enforce the wire budget after optional timing/retention envelopes are added."""
    if len(encoded(result)) <= MAX_SUMMARY_BYTES:
        return result
    value = dict(result)
    for key in ("timings", "library_audio"):
        if key in value:
            payload = encoded(value[key])
            value[key] = dict(representation="omitted_from_reply", sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
    if len(encoded(value)) > MAX_SUMMARY_BYTES:
        event = value.get("listening_event") or {}
        value["listening_event"] = dict(id=event.get("id"), representation="retained_reference", sha256=hashlib.sha256(encoded(event)).hexdigest())
    if len(encoded(value)) > MAX_SUMMARY_BYTES:
        raise ValueError("Action envelope exceeds its declared response budget")
    return value
