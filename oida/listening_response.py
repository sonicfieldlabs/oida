"""Small dashboard replies; complete listening and Auditum stay with their owners."""


def compact_listening_result(event, *, remembered=None, remember_requested=False):
    summary = {
        key: event[key]
        for key in (
            "id",
            "created_at",
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
            key: route[key]
            for key in ("route_id", "route_name", "summary")
            if key in route
        }
        for route in event.get("routes", [])
    ]
    saved = remembered or {}
    record_id = saved.get("akousma_id")
    return dict(
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
