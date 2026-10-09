"""Current claim currency and retention review, using Earworm's independent clocks."""

from datetime import datetime, timezone
from akousma.listening_context import claim_validity_at, claim_retention_at


def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def evaluate(record, now=None):
    now = now or utc_now()
    claims = (
        record.get("extensions", {})
        .get("earworm_listening_context", {})
        .get("claims", [])
    )
    return dict(
        contract="oida/claim-evaluation/v1",
        evaluated_at=now,
        claims=[
            dict(
                claim_ref=c["claim_ref"],
                validity=claim_validity_at(c, now),
                retention=claim_retention_at(c, now),
            )
            for c in claims
        ],
        retention_effect="review only; no deletion or automatic extension of validity",
    )


def apply_declaration(record, validity=None, retention=None):
    claims = (
        record.get("extensions", {})
        .get("earworm_listening_context", {})
        .get("claims", [])
    )
    if (validity is not None or retention is not None) and not claims:
        raise ValueError("no receiving claims exist to bind the declaration")
    for claim in claims:
        if validity is not None:
            claim["validity"] = validity.copy()
        if retention is not None:
            claim["retention"] = retention.copy()
    result = evaluate(record)
    if any(c["validity"] in ("expired", "not_yet_valid") for c in result["claims"]):
        raise ValueError(
            "declared receiving claim is not currently valid; no record written"
        )
    return result
