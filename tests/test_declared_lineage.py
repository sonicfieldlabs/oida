"""Finding P1-02: declared lineage at the owner boundary.

A generated sound reviewed as a new subject was retained as an ordinary capture
with an empty ``parent_akousma_ids``, so the join between a sound and the
listening it was made from lived only in the caller's run receipt, where no
exporter could reach it.

The akousma schema has carried ``parent_akousma_ids`` and typed ``relations`` all
along. Nothing needed extending. What was missing was anyone telling the owner.
"""

from __future__ import annotations

import pytest

from oida.akousma_bridge import (
    DECLARED_RELATION_TYPES,
    apply_declared_lineage,
    build_akousma_from_listen,
)

PARENT = "akm_01M2K0DRKNYB1RBGB2QNC3R4JR"
AUDIO = {"asset_id": "seg_test", "content_hash": "sha256:" + "a" * 64, "duration_seconds": 12.0}


def test_a_listen_without_declared_lineage_is_unchanged():
    """The default stays exactly what it was; this is additive."""
    record = build_akousma_from_listen(audio=AUDIO)
    assert record["lineage"]["parent_akousma_ids"] == []
    assert record["lineage"]["operation"] == "listen"


def test_a_declared_parent_reaches_the_record():
    record = build_akousma_from_listen(
        audio=AUDIO,
        lineage={
            "parent_akousma_ids": [PARENT],
            "operation": "generation_review",
            "relations": [
                {
                    "type": "response_to",
                    "target_akousma_id": PARENT,
                    "note": "Review of a sound generated from this account.",
                }
            ],
        },
    )
    assert record["lineage"]["parent_akousma_ids"] == [PARENT]
    assert record["lineage"]["operation"] == "generation_review"
    relation = record["lineage"]["relations"][0]
    assert relation["type"] == "response_to"
    assert relation["target_akousma_id"] == PARENT
    assert "generated from this account" in relation["note"]


def test_declared_lineage_merges_rather_than_replaces():
    """Kinship oída observed must not be overwritten by kinship a caller asserts."""
    record = {"lineage": {"parent_akousma_ids": ["akm_observed"], "relations": [
        {"type": "same_source_as", "target_akousma_id": "akm_observed", "note": "same hash"}
    ]}}
    apply_declared_lineage(
        record,
        {
            "parent_akousma_ids": [PARENT],
            "relations": [{"type": "response_to", "target_akousma_id": PARENT}],
        },
    )
    assert record["lineage"]["parent_akousma_ids"] == ["akm_observed", PARENT]
    assert {r["type"] for r in record["lineage"]["relations"]} == {
        "same_source_as",
        "response_to",
    }


def test_declaring_the_same_kinship_twice_does_not_duplicate_it():
    record = {"lineage": {}}
    declared = {
        "parent_akousma_ids": [PARENT],
        "relations": [{"type": "response_to", "target_akousma_id": PARENT}],
    }
    apply_declared_lineage(record, declared)
    apply_declared_lineage(record, declared)
    assert record["lineage"]["parent_akousma_ids"] == [PARENT]
    assert len(record["lineage"]["relations"]) == 1


def test_an_unknown_relation_type_is_refused_not_mapped_to_other():
    """Coercion would make every unrecognised kinship look like one deliberate
    choice — the same defect as an unknown relation becoming 'reuse'."""
    with pytest.raises(ValueError, match="not one the akousma schema"):
        apply_declared_lineage(
            {"lineage": {}},
            {"relations": [{"type": "descended_from", "target_akousma_id": PARENT}]},
        )


@pytest.mark.parametrize("kind", DECLARED_RELATION_TYPES)
def test_every_schema_relation_type_is_accepted(kind):
    record = {"lineage": {}}
    apply_declared_lineage(record, {"relations": [{"type": kind, "target_akousma_id": PARENT}]})
    assert record["lineage"]["relations"][0]["type"] == kind


def test_a_relation_without_a_target_is_refused():
    with pytest.raises(ValueError, match="needs a target_akousma_id"):
        apply_declared_lineage({"lineage": {}}, {"relations": [{"type": "response_to"}]})


@pytest.mark.parametrize("parent", ["", "   ", None, 42])
def test_an_unusable_parent_id_is_refused(parent):
    with pytest.raises(ValueError, match="not a usable string"):
        apply_declared_lineage({"lineage": {}}, {"parent_akousma_ids": [parent]})


def test_a_record_cannot_be_its_own_parent():
    with pytest.raises(ValueError, match="its own parent"):
        apply_declared_lineage(
            {"akousma_id": PARENT, "lineage": {}},
            {"parent_akousma_ids": [PARENT]},
        )


def test_a_non_object_declaration_is_refused():
    with pytest.raises(ValueError, match="must be an object"):
        apply_declared_lineage({"lineage": {}}, ["akm_1"])
