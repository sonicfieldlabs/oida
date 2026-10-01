from copy import deepcopy
import json

import pytest

from oida.reasoning.orchestrator import _public_relisten
from oida.reasoning.relisten_projection import provenance_projection


def sidecar():
    return {
        "id": "relisten-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "segment_hash": "a" * 64,
        "sha256": "b" * 64,
        "observation": "PRIVATE OBSERVATION",
        "observation_shared_to_reasoner": False,
        "source_binding": {
            "status": "verified",
            "recorded_sha256": "a" * 64,
            "observed_sha256": "a" * 64,
            "bytes": 32044,
        },
        "pass_provenance": [
            {
                "model_kind": "thinking",
                "model": "/PRIVATE/checkpoint",
                "prompt": "PRIVATE PROMPT",
                "reasoning_trace": "PRIVATE REASONING",
                "weights": {
                    "status": "known",
                    "sha256": "c" * 64,
                    "files": ["PRIVATE"],
                },
                "effective_input": {
                    "status": "known",
                    "sha256": "d" * 64,
                    "sample_rate_hz": 16000,
                    "channels": 1,
                    "sample_count": 16000,
                    "duration_s": 1.0,
                },
            }
        ],
    }


def test_public_relisten_keeps_bounded_identity_without_revealing_observation():
    source = sidecar()
    before = deepcopy(source)
    projected = _public_relisten(source)
    assert projected["observation"] is None and projected["observation_withheld"]
    assert (
        projected["conversation_id"] == "conversation-1"
        and projected["turn_id"] == "turn-1"
    )
    provenance = projected["provenance"]
    assert provenance["source_binding"]["status"] == "verified"
    assert provenance["source_sidecar_sha256"] == "b" * 64
    receipt = provenance["pass_receipts"][0]
    assert receipt["weights_sha256"] == "c" * 64
    assert receipt["effective_input"]["sha256"] == "d" * 64
    assert len(receipt["source_receipt_sha256"]) == 64
    assert "PRIVATE" not in json.dumps(projected)
    assert source == before


@pytest.mark.parametrize("damage", ["missing", "mismatch", "malformed", "unknown"])
def test_source_identity_is_not_upgraded(damage):
    source = sidecar()
    if damage == "missing":
        source.pop("source_binding")
    elif damage == "mismatch":
        source["segment_hash"] = "e" * 64
    elif damage == "malformed":
        source["source_binding"]["recorded_sha256"] = "bad"
    else:
        source["source_binding"]["status"] = "unverified_original"
    assert (
        provenance_projection(source)["source_binding"]["status"]
        == "unverified_original"
    )


def test_unknown_input_and_extra_receipts_are_bounded():
    source = sidecar()
    source["pass_provenance"] *= 17
    source["pass_provenance"][0]["effective_input"]["status"] = "unknown"
    projected = provenance_projection(source)
    assert (
        len(projected["pass_receipts"]) == 16 and projected["pass_receipts_truncated"]
    )
    assert projected["pass_receipts"][0]["effective_input"]["sample_rate_hz"] is None


@pytest.mark.parametrize("value", [True, -1, 1.5, float("nan"), 10**1000, "16000"])
def test_invalid_integer_metadata_cannot_become_measurement(value):
    source = sidecar()
    source["pass_provenance"][0]["effective_input"]["sample_rate_hz"] = value
    assert (
        provenance_projection(source)["pass_receipts"][0]["effective_input"][
            "sample_rate_hz"
        ]
        is None
    )
