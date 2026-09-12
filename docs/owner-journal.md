# Owner journal and record references (unreleased)

Oída retains acquisition transitions and references to remembered canonical records
in `owner-journal.sqlite3` under its configured data directory. These owner API
routes inherit the daemon's existing loopback/access controls. Journal payloads
can contain consent references and source metadata: they are owner-only data.
The application must apply its disclosure rules before producing public events.

## Identity and replay

`GET /owner/journal?limit=100` returns `oida/owner-journal/v1`, a stable
`producer_id`, ordered `events`, `next_sequence`, `high_water_sequence` and
`has_more`. Each event has that producer ID, its `sequence`, `kind`, `subject_id`,
`created_at`, and the retained `payload`. Supported kinds are `acquisition` and
`record_reference`, plus minimal `operation` receipts. Identical consecutive snapshots do not create another event.
A metadata update within the same status can create an event.

Resume with both `producer_id` and `after_sequence=next_sequence`. Consume pages
while `has_more` is true, then poll from the last processed sequence. Consumers
should deduplicate by `(producer_id, sequence)` and advance their checkpoint only
after applying a page. Missing identity on a nonzero cursor, a different identity,
or a cursor ahead of the journal returns HTTP 409. Page limits are 1–500.
Deleting the database creates a new producer identity; do not silently reuse an
old cursor. Retain the database across owner restarts.

`GET /owner/snapshots` returns `oida/owner-snapshot/v1` and the latest state of each
subject at `high_water_sequence`. For further pages, send that watermark as
`at_sequence`, the returned `producer_id`, and `after_sequence=next_sequence`.
Keep the watermark fixed until `has_more` is false. Then consume journal events
**after the snapshot watermark**, not after the last snapshot row. This prevents
updates during pagination from being lost. Snapshot rows carry their original
owner sequences; nested source producer IDs are preserved unchanged.

Owner sequences do not replace source identities or the application's multiplexed
public resume cursor. The existing `/events/stream` broadcaster remains transient;
this journal does not copy its transcripts or unremembered gateway events.

## Durable acquisition outcomes

The event append and current acquisition snapshot commit in one SQLite transaction
with WAL and full synchronous writes. Readers use a consistent read transaction.
Legacy `source-acquisitions/*.json` receipts are imported if the journal lacks them.
JSON receipt files remain compatibility mirrors; failure to update a mirror does
not roll back a committed transition. The journal takes precedence at restart.

Queued, acquiring, listening, committing and terminal transitions retain their receipt
metadata and available event/record links. Restart marks unfinished work interrupted,
or expires already-stale queued work; it does not replay captures. A retry uses a
new acquisition ID and preserves the failed attempt. Cancellation and start-deadline expiry retain their distinct outcomes.
Cancellation during listening rejects late output before publication; it does not
claim immediate model-kernel termination. See [operation cancellation](operation-cancellation.md).

## Canonical records and reconciliation

Remembered gateway, human-response and observation records add a `record_reference`
with the canonical Akousma ID, an event ID where available, and a SHA-256 digest of
the canonical JSON. The journal stores references, not copies of full records.
`GET /owner/records/{id}` requires a retained reference and reads the current record
from the existing Akousmata store. It returns that record, the historical reference,
and `current_sha256`. A missing reference or unavailable canonical record returns
404; a historical reference is not proof the record still exists or is unchanged.

The canonical store commit and reference append are separate transactions. If a
record was stored but its journal append failed, an owner can repair the link with
`POST /owner/records/{id}/reconcile`. This reads the canonical record and appends
its current digest; repeating it without a change is idempotent. Reconciliation
can also refresh a reference after a canonical revision. No generic transcript
retention is added for unremembered gateway calls.

There is currently no automatic pruning or compaction of this journal. Acquisition
metadata and record-link history remain until an owner retention operation is
implemented. Back up the SQLite database using SQLite's backup facilities or while
the daemon is stopped; copying only the main file during WAL writes is insufficient.

## Local verification

`tests/test_owner_journal.py` checks concurrent sequencing, resume identity,
pinned snapshot pagination, transactional rollback, failed JSON mirrors, legacy
migration and restart, unremembered-call behavior, canonical record lookup and
idempotent reconciliation. Source API tests cover failed/retried, cancelled and
expired jobs. A loopback synthetic WAV fixture uses actual FFmpeg capture and the
stub model to verify receipt/event/record links; it is not hardware microphone or
full-model performance evidence.
