# Bounded source admission

`POST /listen-event` and `POST /gateway/listen` accept optional `source_admission`
metadata alongside their existing local `path`. Existing requests are unchanged.
Use the existing bounded upload endpoint first when transferring audio; the source
admission path does not download streams or capture devices.

```json
{
  "path": "/local/captured-window.wav",
  "source_type": "external_stream",
  "raw_audio_policy": "temp",
  "source_admission": {
    "adapter": "radio-window",
    "producer_id": "local-radio-recorder",
    "source_id": "station-window-001",
    "source_time": "2026-09-06T12:00:00Z",
    "consent": "granted",
    "consent_ref": "local-permission-record",
    "raw_audio_policy": "temp",
    "max_window_s": 30.0,
    "apparatus": {"status": "unknown"}
  }
}
```

Adapter/type pairs are `file`/`file`, `browser-microphone`/`live_input`,
`high-rate-device`/`live_input` and `radio-window`/`external_stream`. These labels
identify caller-supplied captures; they do not certify a device or install a new
capture backend. Source time must have a timezone. Apparatus declarations are
limited to 16 KiB and windows to 300 seconds. The decoded file duration must fit
the declared window. Unknown/denied consent, adapter/type disagreement and
retention disagreement fail before report inference or gateway remembering.
Covenants still apply; if they narrow retention, the declaration must match the
effective policy. This field uses existing retention behavior and does not add
a deletion scheduler or authorization mechanism.

The `oida/source-admission/v1` receipt lives in `source.details.source_admission`
on the event and in the canonical `oida.listen` payload when remembered. It carries
producer/source identity, declared source time, apparatus, consent reference and
retention policy, plus the admitted file hash and inspected rate/channels/duration.
`segment.captured_at` preserves the source time separately from event creation.
Earworm listening event payloads also retain this receipt. Consent and apparatus
remain explicitly caller-declared. A 96 kHz file establishes a sampled
representation, not physical capture bandwidth, model competence or human access.
Do not put credentials or unnecessary personal data in provenance fields.

The owner-configured acquisition and observation APIs below extend this admission
boundary. Requests that omit source admission retain their previous behavior.

## Owner-configured capture

Set `OIDA_CAPTURE_SOURCES` to a local JSON manifest before starting the daemon:

```json
{
  "contract": "oida/capture-sources/v1",
  "sources": [{
    "id": "studio-input",
    "adapter": "avfoundation",
    "input": ":0",
    "sample_rate": 96000,
    "channels": 2,
    "max_seconds": 30.0,
    "producer_id": "studio-capture",
    "consent": "granted",
    "consent_ref": "local-capture-permission",
    "apparatus": {"status": "unknown"}
  }]
}
```

FFmpeg must be installed with the selected input backend. `avfoundation` uses the
macOS device's current native format; set it in the operating system first.
`alsa` passes the configured rate/channels as input negotiation options. `radio`
uses an owner-selected HTTP(S) stream URL in `input`. The API accepts source IDs,
never arbitrary input URLs or FFmpeg arguments. Source URLs are not returned by
the registry or copied into error receipts. An operator-selected stream is a
trusted network source, not a sandboxed public proxy.

`GET /sources/capture` lists configured sources as `configured_unverified`.
Configuration does not prove device presence, permission, calibration or bandwidth.
The capture does not set output resampling options. It rejects the result if its
actual rate/channels differ from the configured expectation. Windows native
capture is not provided by these FFmpeg adapters; the existing browser input
path remains available.

```text
POST /sources/capture/studio-input/listen
{"acquisition_id":"capture-001","seconds":10.0,"remember":false}

GET /sources/acquisitions/capture-001
POST /sources/acquisitions/capture-001/cancel
```

The synchronous listen call runs capture and the existing gateway. It returns a
`receipt` and, on successful listening, the gateway `result`. Receipts have
`complete`, `failed`, `refused`, `cancelled` or recovered `interrupted` status;
callers must inspect the receipt, not just HTTP 200. Event/account IDs and source
admission metadata link successful captures to listening and storage. Raw audio
is temporary and deleted after the attempt, including failures and cancellation.
A supervisor terminates FFmpeg on cancellation or owner-pipe closure. Restart
cleans the adapter's dedicated temporary directory and marks unfinished receipts
interrupted, without replaying them. An interrupted listening attempt may already
have persisted an account; investigate before retrying. This is not a transaction
across acquisition receipts and the account store.

One acquisition executes at a time. The scheduling API below adds a bounded waiting
queue. Duplicate acquisition IDs fail;
use a fresh ID for an intentional retry. Cancellation is accepted only while
acquiring, before gateway listening starts. Model cancellation and a unified journal belong to subsequent runtime phases. Use a
single owner process for a data directory. Acquisition windows are at most 300
seconds, with a 128 MiB PCM budget and a wall-clock deadline. Source consent and
active covenant source/time/window rules are checked before acquisition.

## Cosmoaudition observations

Cosmoaudition already exports a MASA snapshot from
`GET /api/snapshot/masa?mode=fixture` (or its explicitly selected live mode).
Send the returned record as `source_record` to `POST /sources/observations` with:

```json
{
  "source_record": {},
  "observation_ref": "selected-observation-id",
  "producer_id": "cosmoaudition",
  "consent": "granted",
  "consent_ref": "local-observation-permission",
  "remember": false
}
```

Replace `{}` with the complete exported record. Select an ID from its
`observations`; the adapter does not silently select a different observation.
Set `OIDA_MASA_VALIDATOR_MODULE` to the installed
`@sonicfield/masa-validator/dist/index.js` entry before daemon startup. Node must
be available. This reuses Cosmoaudition's pinned MASA validator, including semantic
validation, rather than copying a schema or implementing a second validator.
Missing or failed validation returns 503; invalid records return 400. Snapshots
are limited to 2 MiB and validation has a 15-second timeout.

The receiver reuses AKOÚŌ's observation mapper and Earworm's observation-account
constructor. It retains the entire source snapshot, including apparatus, time,
licences, policies, freshness and unknown/withheld values. Source status is never
upgraded by reception. Canonical source hashing uses sorted compact JSON; it is
not the original HTTP-byte digest. Accounts have no audio asset and make no new
measurement or human-hearing claim. Consent remains a caller declaration.
`remember:false` returns the validated account without storing it;
`remember:true` uses the existing canonical store, subject to the active covenant.
There is no new export or disclosure permission implied by receiving the record.

Acquired audio uses `source_time_basis: "local-acquisition-start"`: this records
when the owner began acquisition, not an independently verified station/device
clock. Supplied-file declarations default to `caller-declared`. Both timestamps
remain distinct from the receiving event time. Failure receipts retain the
configured source descriptor and expected format; only successful inspection
establishes the actual sampled representation.


## Bounded source scheduling

`POST /sources/capture/{source_id}/jobs` queues the same bounded acquisition/listen
request and returns HTTP 202 with its `queued` receipt. Add
`expires_in_seconds` (0.05–3600, default 60). The deadline is calculated at admission
and means **listening must start before it expires**; it does not terminate an
already-started model call. Existing source consent/window checks apply on
submission, and the active covenant is checked again at dispatch.

```json
{"acquisition_id":"queued-001","seconds":10.0,"expires_in_seconds":60.0,"remember":false}
```

The FIFO queue allows eight waiting jobs by default; set
`OIDA_SOURCE_QUEUE_CAPACITY` to an integer from 1 to 32 at owner startup. One
reserved/running job may exist in addition to the waiting capacity. A full queue
returns 429 without creating a job receipt. Existing identifiers return 400; an
intentional retry uses a fresh ID. There is no periodic source polling or automatic
retry. The synchronous capture/listen route shares the execution lock and returns
409 if busy. Scheduled jobs may wait behind a synchronous acquisition, subject to
the same expiry deadline.

`GET /sources/scheduler` exposes capacity, waiting count, executing acquisition ID, reserved scheduled-job ID and whether
new work is accepted. Poll `/sources/acquisitions/{id}` for the durable receipt.
The existing cancellation endpoint now also cancels queued jobs. An independent
monitor expires waiting jobs even while the worker is blocked in listening. It
interrupts stale acquisition through the existing capture cancellation mechanism;
the worker rechecks the deadline before starting listening. Expired/cancelled jobs
cannot later be dispatched. Cancellation after listening starts remains outside
this boundary and is reported as not accepted.

Restart marks stale queued jobs `expired`; other unfinished jobs are `interrupted`.
Neither is replayed. Graceful shutdown rejects submissions and cancels pending
work or capture, while an already-started listening call may finish. Existing
completed receipt and canonical-account links survive restart. These receipt
writes are still not a transaction with account persistence.

Executed receipts include observed queue wait, capture/listening/execution wall
seconds and available owner-process peak RSS. RSS is the process lifetime high-
water mark, not a per-job allocation delta; it excludes capture children and GPU
memory. Unsupported platforms report unknown. Expired jobs that never execute
have no execution metrics. These measurements do not select model residency or
raise inference concurrency. The single source executor preserves the previous
acquisition bound; other existing Oída routes retain their own concurrency rules.
The [local full-model profile](moss-hardware-profile.md) now supports retaining
single-model residency and one source executor on the measured host. Different
hardware, longer workloads or higher concurrency require fresh measurements.
