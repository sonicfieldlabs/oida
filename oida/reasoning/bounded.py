"""What a provider with a bounded validated context may be sent.

The admitted local planner (local_ecology, Qwen3.5-4B) was evaluated within an 8192-token prompt
and refuses anything longer. On 24 September a five-second listening produced 24 evidence items
(8647 prompt tokens with its choices) and every situated decision it was asked for was refused.
Evidence is bounded by whole items, never rewritten; the receipt says what was withheld.
"""

from __future__ import annotations

import json

BOUNDED_CONTEXT = {
    "local_ecology": {"evidence_chars": 8000, "max_choices": 20, "inquiry_chars": 7000, "routing_chars": 7000}
}

_PRIORITY = {"event_anchor": 0, "summary": 1, "reference": 2}


def _rank(kind) -> int:
    kind = str(getattr(kind, "value", kind) or "")
    if kind in _PRIORITY:
        return _PRIORITY[kind]
    if "anchor" in kind:
        return 0
    if "summary" in kind or "caption" in kind:
        return 1
    return 3


def keep_within(items, chars, *, kind=lambda item: item.get("kind"), size=None):
    """Indices of the items kept: anchors always, then summaries and references, then the rest
    in order, while the budget lasts."""
    size = size or (lambda item: len(json.dumps(item, ensure_ascii=False, default=str)))
    ranked = sorted(enumerate(items), key=lambda pair: (_rank(kind(pair[1])), pair[0]))
    kept, used = set(), 0
    for index, item in ranked:
        n = size(item)
        if _rank(kind(item)) == 0 or used + n <= chars:
            kept.add(index)
            used += n
    return kept, used


def bound_evidence(items, choices, *, evidence_chars, max_choices, **_):
    """Keep the anchor, then summaries and references, then claims in order, within a budget.

    Nothing is summarised or rewritten: whole items are sent or withheld, and the receipt
    says how many of each were withheld, so a decision is read against what was offered.
    """
    kept, used = keep_within(items, evidence_chars)
    sent = [item for index, item in enumerate(items) if index in kept]
    receipt = dict(
        offered=len(items),
        sent=len(sent),
        withheld_refs=[item["ref"] for index, item in enumerate(items) if index not in kept],
        choices_offered=len(choices),
        choices_sent=min(len(choices), max_choices),
        evidence_chars=used,
        basis="the provider's validated context is bounded; whole items were withheld, none rewritten",
    )
    return sent, choices[:max_choices], receipt


def bound_packet(packet, chars):
    """The same packet with only the items that fit, for a bounded provider's prompt."""
    items = list(packet.items)
    kept, _ = keep_within(
        items, chars, kind=lambda item: item.kind,
        size=lambda item: len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False)),
    )
    return packet.model_copy(update={"items": [item for i, item in enumerate(items) if i in kept]}), [
        item.ref for i, item in enumerate(items) if i not in kept
    ]
