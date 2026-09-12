# Akousmata-owned research integration (unreleased O18)

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
