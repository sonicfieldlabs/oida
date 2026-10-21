"""A bounded local reasoner receives an explicitly selected re-listen (P8b, 21 Oct 2026)."""

from oida.reasoning.bounded import BOUNDED_CONTEXT, bound_packet
from oida.reasoning.contracts import EvidenceItem, EvidencePacket


def packet(claims):
    items = [EvidenceItem(ref="event:e:anchor", kind="event_anchor", value={"id": "e"})]
    items.append(EvidenceItem(ref="event:e:summary", kind="summary", value="A long account. " * 40))
    items += [
        EvidenceItem(ref=f"event:e:claim:{i}", kind="claim", value="A measured claim. " * 6)
        for i in range(claims)
    ]
    items.append(
        EvidenceItem(
            ref="relisten:r1:observation:0",
            kind="relisten",
            value={"observation": "A steady tone beating about 1.5 times per second.",
                   "limitations": ["Targeted pass; one model; not a measurement."] * 12},
        )
    )
    return EvidencePacket(primary_event_id="e", question="What does it establish?", items=items)


def test_relisten_appended_after_many_claims_is_kept_within_the_bound():
    bound = BOUNDED_CONTEXT["local_ecology"]["inquiry_chars"]
    kept, withheld = bound_packet(packet(80), bound)
    refs = [item.ref for item in kept.items]
    assert "relisten:r1:observation:0" in refs
    assert withheld and all(ref.startswith("event:e:claim:") for ref in withheld)


def test_relisten_still_obeys_the_bound_when_it_cannot_fit():
    kept, withheld = bound_packet(packet(0), 50)
    assert "relisten:r1:observation:0" in withheld
