# Retained ensembles and second reports

Unreleased local owner APIs. Both reuse AKOUO contracts, Earworm canonical records,
the existing operation controller and owner journal. They require compatible local
packages containing `akouo_contract.orchestration` and the current canonical
listening-context adapter; published package version numbers have not changed.

## Independent aggregation

`POST /owner/ensembles` accepts:

```json
{
  "operation_id": "ensemble-example",
  "record_ids": ["first-record", "second-record"],
  "permission_refs": {"first-record": "owner-declaration", "second-record": "owner-declaration"},
  "remember": false
}
```

The 2–16 selected records must already exist in the canonical store. At most 16
distinct retained passes and at least two actual participant identities are
supported. Model, DSP and optional human accounts retain their identities through
the original source snapshots. A human-labelled test fixture is not human-study
evidence. Duplicate passes, conflicting participant types and already-influenced
inputs are refused. A shared actor is not split into invented actors.

The aggregate reuses AKOUO's bounded schedule and ensemble adapter. It stores
validated canonical listening IDs and contexts, source snapshots, permission
references and execution receipts. Disagreements remain under their original
record scopes in `akouo.retained-ensemble`; the aggregate does not relabel them
as locally resolved agreement. Original access, claims, decisions, model identity
and renderings remain in the snapshots. The wrapper context describes access to
retained records. No new audio/model pass runs and `influence_edges` stays empty.

## Second-report execution

`POST /owner/records/{id}/second-report` accepts:

```json
{
  "operation_id": "second-example",
  "question": "Which conclusions can this retained account support?",
  "permission_ref": "owner-declaration",
  "provider_id": "openai_compatible",
  "require_model": true,
  "remember": false
}
```

The route negotiates and validates A2 before invoking the existing reasoning
orchestrator. It projects the retained summary and validated A7 report claims
into the existing bounded evidence packet. Other producer payloads remain in
the source snapshot; their arbitrary fields are not sent to the reasoner.
Inherited claims are marked as memory evidence with undetermined status; their
original categories remain explicit in the text and exact source report.
The source covenant is passed to the existing evidence filter and retained in
the result. Original audio is never opened, and targeted re-listening is disabled.

By default a configured, enabled local text-model provider must execute and pass
response validation. External or unknown-locality providers are refused. A model
failure cannot be recorded as a successful deterministic substitute. For an
explicit deterministic inspection, select `provider_id: "local_structured"` and
`require_model: false`; the execution receipt labels that provider. This is not a
new model pass. Audio-only models are not given invented audio for this route.

Output is an A7 report with interpreted claims, an explicit no-audio limitation,
readable text and a canonical account. The A2 source snapshot and route are
retained. The plan keeps `execution: not_requested`; actual execution is recorded
separately in `oida.second-report-execution`, including provider/model, evidence,
response, execution `pass_id` and its associated A7 `report_pass_ref`. These are
distinct linked execution/report identities. No intermediate conversation is saved.

## Permission, cancellation and retention

Each input requires a nonblank owner-supplied permission reference. This is an
attributable declaration, not an external rights check. A source explicitly marked
`restricted` is refused pending separately resolved permission policy. Existing
owner preflight checks run before work and again before retention. All source
records are re-read before the final operation seal; changed inputs produce a
conflict. Each source is bounded to 2 MiB.

`remember: false` returns the validated result without storing a new canonical
record. `remember: true` writes the canonical record and its owner-journal link.
Those writes are separate commits under the existing recovery design. Original
records are unchanged; lineage points to them. Operation IDs are single-use,
including across restart for durable receipts. Query or cancel through the existing
`/operations/{id}` routes. Cancellation fences late results from canonical retention;
it does not promise to interrupt an already-running provider transport.

Tests use synthetic source records and a synthetic transport through the existing
OpenAI-compatible adapter. They establish local routing, attribution, validation
and failure behavior. Live text-model quality, physical listening, multi-host
exchange, directional commands and measured influence are separate acceptance work.

For retained decision-change attribution and negotiated local handoff, see
[influence and exchange](influence-exchange.md). Neither endpoint infers influence
from dependencies or runs an additional pass during transfer.
