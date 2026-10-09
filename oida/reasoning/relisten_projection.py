"""Bounded, non-prose provenance for redacted targeted-listening responses.

Hashes refer to private source receipts, never to this reduced projection.
No prompts, reasoning, filenames, checkpoint paths or arbitrary receipt strings
cross this boundary. Unknown metadata remains unknown.
"""

from __future__ import annotations

import hashlib
import json
import math
import re


def _sha(value):
    return (
        value.lower()
        if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value)
        else None
    )


def _positive(value, *, integer=False):
    if type(value) not in (int, float) or (integer and type(value) is not int):
        return None
    try:
        return value if math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def provenance_projection(sidecar):
    source = sidecar.get("source_binding")
    source = source if isinstance(source, dict) else {}
    recorded, observed = (
        _sha(source.get("recorded_sha256")),
        _sha(source.get("observed_sha256")),
    )
    verified = (
        source.get("status") == "verified"
        and recorded is not None
        and recorded == observed == _sha(sidecar.get("segment_hash"))
    )
    rows = sidecar.get("pass_provenance")
    rows = rows if isinstance(rows, list) else []
    receipts = []
    for row in rows[:16]:
        if not isinstance(row, dict):
            continue
        try:
            receipt_hash = hashlib.sha256(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        except (TypeError, ValueError):
            receipt_hash = None
        effective = row.get("effective_input")
        effective = effective if isinstance(effective, dict) else {}
        input_hash = (
            _sha(effective.get("sha256"))
            if effective.get("status") == "known"
            else None
        )
        if input_hash is None:
            effective = {}
        weights = row.get("weights")
        weights = weights if isinstance(weights, dict) else {}
        receipts.append(
            {
                "source_receipt_sha256": receipt_hash,
                "model_kind": row.get("model_kind")
                if row.get("model_kind")
                in (
                    "instruct",
                    "thinking",
                    "music",
                    "transcription",
                    "targeted_relisten",
                )
                else None,
                "weights_sha256": _sha(weights.get("sha256"))
                if weights.get("status") == "known"
                else None,
                "effective_input": {
                    "status": "known" if input_hash is not None else "unknown",
                    "sha256": input_hash,
                    "sample_rate_hz": _positive(
                        effective.get("sample_rate_hz"), integer=True
                    ),
                    "channels": _positive(effective.get("channels"), integer=True),
                    "sample_count": _positive(
                        effective.get("sample_count"), integer=True
                    ),
                    "duration_s": _positive(effective.get("duration_s")),
                },
            }
        )
    return {
        "contract": "oida/relisten-provenance-projection/v1",
        "source_sidecar_sha256": _sha(sidecar.get("sha256")),
        "hash_basis": "Private source sidecar and pass receipts, not this redacted projection",
        "source_binding": {
            "status": "verified" if verified else "unverified_original",
            "recorded_sha256": recorded,
            "observed_sha256": observed,
            "bytes": _positive(source.get("bytes"), integer=True),
        },
        "pass_receipts": receipts,
        "pass_receipts_truncated": len(rows) > 16,
    }
