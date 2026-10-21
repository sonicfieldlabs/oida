# Akousmata-owned research integration

O18 is included in the Oída 0.14.0 source line and selected local
Akousmata 0.9.1 candidate. This statement identifies source/candidate inclusion,
not registry publication, an installed runtime, or a newly qualified owner.

Oida delegates `/owner/research/proposals`, `/requests/{id}/cancel`,
`/changes/{id}`, `/reconcile` and `/changes/{id}/acknowledge` to the installed
Akousmata research service over the existing shared store. Source selection,
canonical A11 validation, durable identity/recovery, review ancestry and change
queue semantics remain owned by that service. No parallel scoring engine,
all-pairs scan or research scheduler is introduced.

The existing embedded navigator lifecycle already starts the Akousmata watcher.
Its scheduled tick now reconciles bounded changed-record pages. Store triggers
capture new/changed records, including Oida writes; explicit notification is also
available. Reconciliation exposes pending work and exact digests. It does not
invent a proposal or execute a model; a selected comparison policy and explicit
proposal submission remain required. Acknowledgment refuses stale fingerprints.

Use the compatible local AKOUO record-workflow and Akousmata packages. Missing
optional integration packages return 503. Conflict/stale/cancelled work returns
409, unsupported input 400. The shared service supports one active owner process
per store; standalone and embedded owners must not independently commit concurrent
research operations. Existing local owner access control remains the boundary.

Synthetic owner-API tests cover the full mounted route, retained record links,
retry/restart, new/changed notification, stale acknowledgment, scheduled watcher
reconciliation and failure. Shared service tests additionally cover cancellation,
interrupted canonical commit, ancestry deduplication and disclosure/forgetting.

Public Sonic Field references use a separate optional [research reference
wrapper](research-context.md). They remain selected context in an existing
reasoning turn and do not manufacture A11 canonical source IDs. The canonical
proposal/review service above retains ownership and its current validation.

Selected private wiki passages use [local research context](local-research-context.md)
and the existing shared material-use table. Their optional snapshots remain in
reasoning custody, with current-use gates on their retained responses. They do
not create canonical records, independent confirmation, grants or an additional
research scheduler. The source wikis remain read-only.
