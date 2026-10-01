"""A provider with a bounded validated context gets whole evidence items within that bound.

24 September 2026: a five-second listening gave the local planner 24 items and 8647 prompt
tokens against its validated 8192, and every situated decision was refused.
"""

from oida.situated_listener import BOUNDED_CONTEXT, bound_evidence


def item(ref, kind, size):
    return {"ref": ref, "kind": kind, "value": "x" * size}


def test_the_anchor_and_summaries_go_first_and_whole_items_are_withheld():
    items = [
        item("a:anchor", "event_anchor", 50),
        *(item(f"a:claim:{i}", "claim", 300) for i in range(20)),
        item("a:summary", "summary", 400),
    ]
    sent, choices, receipt = bound_evidence(
        items, list(range(30)), evidence_chars=2000, max_choices=20
    )
    refs = [i["ref"] for i in sent]
    assert refs[0] == "a:anchor" and "a:summary" in refs
    assert refs.index("a:summary") > refs.index("a:anchor"), "original order is kept"
    assert all(i in items for i in sent), "nothing is rewritten"
    assert receipt["offered"] == 22 and receipt["sent"] == len(sent) < 22
    assert set(receipt["withheld_refs"]) == {i["ref"] for i in items} - set(refs)
    assert len(choices) == 20 and receipt["choices_offered"] == 30


def test_the_anchor_is_sent_even_when_it_alone_exceeds_the_budget():
    sent, _, receipt = bound_evidence(
        [item("a:anchor", "event_anchor", 5000)], [], evidence_chars=100, max_choices=5
    )
    assert [i["ref"] for i in sent] == ["a:anchor"] and receipt["withheld_refs"] == []


def test_only_the_bounded_local_planner_is_bounded():
    assert set(BOUNDED_CONTEXT) == {"local_ecology"}
