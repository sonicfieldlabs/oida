# Retained influence and local record exchange

Unreleased owner APIs. These extend the existing ensemble and operation paths;
they do not launch agents, send network messages or re-listen automatically.

## Directional planning

`POST /owner/orchestrate/plan` accepts AKOUO's `orchestration-request` schema and
calls the shared `plan_orchestration` implementation through Oida's structured
planning-command adapter. It resolves ears, participants and direction references
and returns a declared schedule with no execution or influence claim. Perception
report builders refuse `/orchestrate`; they cannot silently turn a plan into a
listening report. The command supports parameter, ensemble, agent, person and no
active director, while keeping each pass's permission unchanged.

## Retained influence

`POST /owner/ensembles/influenced` uses the same `operation_id`, `record_ids`,
`permission_refs` and `remember` fields as independent aggregation, plus:

```json
{"trace_refs": [{"record_id": "target-record", "namespace": "producer.influence"}]}
```

Every reference must resolve to a retained `oida/decision-influence/v1` envelope
inside a selected target record. Its payload contains:

```json
{
  "contract": "oida/decision-influence/v1",
  "trace_id": "trace-identity",
  "source_record_ref": "source-record",
  "from_pass_ref": "source-pass",
  "to_pass_ref": "target-pass",
  "input_report_ref": "target-report",
  "before_decision_ref": "before-decision",
  "after_decision_ref": "after-decision",
  "attributed_by": "target-participant",
  "permission_ref": "target-owner-declaration"
}
```

The source and target passes must belong to their declared records. The target's
validated A7 report must identify that pass and participant and explicitly name
the source in both `input_refs` and `report_of_refs`. Both decisions must belong
to the target pass and its actor, resolve through its `route_decision_refs`, concern
the same gate and subject, and have different outcomes in chronological order.
The source pass must not postdate the attributed decision.
The retained permission reference must match the current target declaration.
Missing references, duplicate traces/edges, unchanged decisions and mismatched
attribution fail before retention.

The effect text is derived from those decision outcomes. The existing canonical
ensemble converter writes matching listening IDs, `influenced_by` entries and
ensemble edges, with required preservation fields. Complete source snapshots and
source-scoped disagreements remain unchanged. Input/decision evidence is retained
separately under `oida.influence-evidence`.

This supports an **attributed recorded decision change**, not independent causal
measurement or evidence of semantic competence. It neither accepts an arbitrary
effect string nor infers an effect from scheduling. It currently accepts independent
source accounts carrying these traces; already-influenced inputs are not re-annotated.
The normal aggregation bounds apply, with at most 32 trace references. Cancellation,
source rechecks, single-use operation IDs and optional retention use the existing
owner controls. `remember: false` still produces a validated result without storage.

## Negotiated record handoff

1. The receiving owner exposes `GET /owner/exchange/capabilities`, including its
   persistent `recipient_id` and required/supported contract lists.
2. The sending owner calls `POST /owner/exchange/offers` with a retained `record_id`,
   that `recipient_id`, the receiver's `supported_contracts`, a nonblank
   `permission_ref`, and an `idempotency_key` of 1–80 letters, digits, `_` or `-`.
3. An explicitly authorized caller transfers the returned packet to
   `POST /owner/exchange/receive` on the receiving owner. The repository supplies
   offer/receive APIs; it does not discover peers or transmit packets itself.

The required contracts are `oida/record-exchange/v1`, `earworm/akousma/v1.7`,
`earworm/listening-context/v1` and `akouo/agent-report/v0.1`. Unsupported requirements,
wrong recipients, invalid canonical/context references, digest mismatches and
records above 2 MiB are refused. Restricted sources and content-withholding covenants
are also refused; this path transfers an exact record, so it cannot silently redact
the record and retain its old identity/digest.

The receiver stores the exact original canonical record under its existing ID,
preserving pass/model identities, recipients, source attribution and companion
payloads. It never overwrites different content at that ID. The handoff receipt
separately records the sending/receiving owner IDs, negotiated contracts, permission
reference, source digest, canonical record link and operation ID. `new_pass` is false.
Transfer does not automatically authorize or run a second-report pass; that remains
an explicit call to the existing A2 endpoint.

Idempotency is scoped to the sending owner and key, and bound to the complete packet,
including permission. An exact completed retry returns the same receipt with
`replayed: true`; changed content or permission conflicts. Concurrent in-progress
retries return either the accepted replay or a conflict asking the caller to inspect
the operation. They cannot create another record or pass.

Canonical storage and the owner journal remain separate commits. A recorded commit
intent plus an exact canonical digest allows a later retry to finish an interrupted
receipt without re-storing or re-running a pass. Cancellation before the seal stores
no record and keeps that key reserved. Changed/missing accepted records produce a
conflict instead of a stale success. Recovery assumes one active owner daemon per
journal, consistent with the existing operation controller.

This is an owner-mediated protocol. The receiving API uses the existing owner access
boundary; the sending identity and permission reference in a transferred packet are
declarations, not cryptographically authenticated peer identity or independently
verified rights. Peer authentication and remote transport remain separate integration
work. Tests use two local owner instances and synthetic accounts, including retries,
concurrency, restart, conflicting content and interruption between the two commits.
