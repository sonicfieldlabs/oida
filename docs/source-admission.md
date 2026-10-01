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

The v1 contract remains the trusted configured-source format above. For a public
radio directory, use `oida/capture-sources/v2` and add
`"network_policy":"public_radio"` plus `"retention":"temp_only"` to each radio.
Oída then validates and pins every public DNS hop, fetches bounded bytes without a
proxy, refuses playlists/private destinations, and decodes only the temporary local
file. For this v2 policy, `sample_rate` and `channels` are admission maxima; the
capture receipt records the actual native values. A client must confirm the
`bounded-public-radio-v1` capability before supplying such a manifest.

### Research samples (public radio, opt-in)

A public radio is temporary by default: its capture is deleted after listening. A
station may instead carry `"retention":"research_sample"` with a `research` block:

```json
"retention": "research_sample",
"research": {"attestation": "<the operator's own words>", "ttl_seconds": 2592000}
```

Both are required together. `ttl_seconds` is 60 s to 30 days (the default). The
attestation is stored verbatim with every sample. A listening keeps a sample only when
its request also asks, with `"retain_research_sample": true` on
`POST /sources/capture/{id}/listen` or `/jobs`. A station that is not opted in, or an
active covenant forbidding `raw-audio` or `memory` retention, refuses the request
before anything is captured. `retain_library_audio` stays refused for radio.

What is kept, and where:

- **Exactly the listened input.** After the commit fence, Oída copies the file it
  handed to the listening and keeps it only if its SHA-256 equals the listening
  event's `segment.data_ref.sha256`. Otherwise nothing is kept and the acquisition
  receipt says why (`research_sample.status: "not_retained"`).
- **Outside the library.** Samples live in `<data dir>/research-samples/` (files mode
  0600), never under the audio folder GERM and the library scan. If that folder is
  configured inside a scanned folder, nothing is kept. No record, listening event or
  export names a sample; the acquisition receipt names its id, digest, size and expiry,
  and says `raw_audio_deleted: false`.
- **Local only.** `GET /sources/research-samples` (`oida/research-samples/v1`) lists
  unexpired samples as metadata, without paths, with `audience: "local"` and
  `exportable: false`. `GET /sources/research-samples/{id}/audio` serves the file after
  checking its bytes against the recorded digest (409 if they changed). An authorized local
  owner interface may read these samples; they remain excluded from exports.
- **Expiring.** A sweep deletes a sample at `expires_at`. It runs every 30 s, and also
  at startup and before every listing, read and retention. Each deletion is journaled as a
  `research_sample` receipt with `status: "expired"`, `deleted_at` and
  `audio_deleted`.
  `DELETE /sources/research-samples/{id}` deletes one early, journaled with
  `status: "deleted"` and `reason: "operator request"`.

FFmpeg must be installed with the selected input backend. `avfoundation` uses the
macOS device's current native format; set it in the operating system first.
`alsa` passes the configured rate/channels as input negotiation options. `radio`
uses an owner-selected HTTP(S) stream URL in `input`. The API accepts source IDs,
never arbitrary input URLs or FFmpeg arguments. Source URLs are not returned by
the registry or copied into error receipts. An operator-selected stream is a
trusted network source, not a sandboxed public proxy.

`GET /sources/capture` returns `oida/capture-sources/v2`, its capabilities, and
configured sources as `configured_unverified`.
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
use a fresh ID for an intentional retry. Cancellation is accepted during acquisition and listening until the publication
commit fence. Late model output is discarded after an accepted cancellation.
See [operation cancellation](operation-cancellation.md) for receipts and limits. Use a
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
cannot later be dispatched. Cancellation during listening discards late output; once publication commits,
cancellation is no longer accepted.

Restart marks stale queued jobs `expired`; other unfinished jobs are `interrupted`.
Neither is replayed. Graceful shutdown rejects submissions and cancels pending
work, capture and uncommitted listening. A model call may finish computing, but
its output is discarded after accepted cancellation. Existing
completed receipt and canonical-account links survive restart. These receipt
writes are still not a transaction with account persistence.

Executed receipts include observed queue wait, capture/listening/execution wall
seconds and available owner-process peak RSS. RSS is the process lifetime high-
water mark, not a per-job allocation delta; it excludes capture children and GPU
memory. Unsupported platforms report unknown. Expired jobs that never execute
have no execution metrics. These measurements do not select model residency or
raise inference concurrency. The single source executor preserves the previous
acquisition bound; other existing Oída routes retain their own concurrency rules.
The [MOSS runtime guide](moss-runtime.md) retains single-model residency and
one source executor as conservative defaults. Hardware-specific capacity,
longer workloads and higher concurrency require separate measurements.

### Durable owner replay

Acquisition receipt transitions now use the [owner journal](owner-journal.md).
The journal supplies stable owner sequences, pinned snapshots and canonical record
references. Existing JSON receipts remain compatibility mirrors. Owner replay stays
separate from the application's filtered public projection cursor.

### Observation route and operation lifecycle (unreleased)

The observation receiver calls AKOUO's existing agent route planner with
`relation: {"of": "observation", "ref": "selected-observation-id"}`. The retained
`listening["oida.observation-route"]` payload binds the source record, selected
observation, provider source, receiving report/pass, recipient and access declaration.
The canonical auditum decision references that route decision. Only undetermined
source attribution is requested; hearing, measurement, inference and interpretation
permissions are disabled. Deleted values and values with unknown units retain the
mapper's omitted projection without inventing receiving claims.

Send an optional `operation_id` to reuse the owner's durable operation controller.
`GET /operations/{id}` exposes the outcome and canonical account link when retained.
Retries return 409 with the existing receipt, including after restart; use a fresh
ID only for an intentional new receiving pass. Missing references and invalid source
records produce refused receipts; unavailable validation produces a failed receipt.
Unfinished operations become interrupted on restart and are never replayed.

`POST /operations/{id}/cancel` can cancel validation or mapping until the publication
fence. The validator process may finish, but cancelled output is discarded before
canonical retention. Current source/covenant policy is rechecked immediately before
that fence. After sealing, cancellation is refused. Canonical storage, journal links
and terminal operation receipts are separate writes: interrupted committing work
may require owner reconciliation. Without an operation ID, the existing synchronous
request behavior remains available.

`producer_id` is caller-supplied attribution; use the identity from the selected
Cosmoaudition process when transporting its snapshots. It is not authenticated by
the received MASA record, and neither the process identity nor a successful intake
establishes provider freshness, consent truth or public disclosure permission.

### Mac input monitoring before listening

`GET /inputs/devices` enumerates AVFoundation audio devices on the owner Mac.
`POST /inputs/start` accepts an enumerated `device_id` and matching `device_label`;
it rejects a changed device identity and a second concurrent producer.
`POST /inputs/{id}/status` renews the 45-second lease and returns completed WAV
chunk descriptors, actual sample rate/channel count, and DSP input level.
`GET /inputs/{id}/chunks/{sequence}` serves only a chunk in that session's bounded
ring. `POST /inputs/{id}/stop` releases capture and temporary audio. Server shutdown
also closes capture; the existing capture worker kills FFmpeg on owner-pipe EOF.

This route reuses the capture input setup and LiveManager, without selecting a
background listening session or calling a model. Monitoring has approximately a
few seconds of buffering. The device's native rate/channels are reported rather
than resampled into a purported capture capability. Sessions start only through
explicit local-owner controls and expire if the dashboard stops renewing them.


### Dashboard listening selection

`POST /gateway/listen` and `POST /listen-event` accept optional `model_id` and
`listening_mode`. The model resolves against the owner's installed/configured
MOSS catalog; the modality must be in AKOÚŌ's canonical `LISTENING_MODES`.
Invalid selections are refused before audio inspection or inference. Model
selection is request-local across report passes and prepared input bindings,
including exception cleanup; it never changes global model assignments.
Omitting the fields preserves the existing route defaults and modality chain.

Configured-source acquisition and job requests accept the same optional fields
and validate them before capture. `GET /listening/options` exposes owner-derived
models, resident state, routes, and modalities without starting model execution.
Music analysis remains an existing perception pass. Non-acoustic observations
continue through the observation API and never become audio by selecting a model.

Dashboard gateway and configured-source requests can set `response_mode: "summary"`.
The default remains `"full"` for existing clients. Summary replies contain the
current event's source, segment, apparatus, aggregate and route summaries, plus
the canonical memory ID and save status. They omit complete background history
and repeated Earworm context; remembering still saves the full event through the
existing owner path before returning. Refusals also omit background history in
summary mode. No model result is inferred from a successful capture receipt.

`POST /gateway/listen-window` accepts the gateway fields plus `start_seconds`
(0–86400, default 0) and `seconds` (greater than 0, at most 10, default 10). It
admits ordinary uploaded files only. Source/retention preflight runs before
slicing, and normal selection, covenant and input gates still run on the window.
The temporary native-rate, native-channel WAV contains only the available window;
requests past the end fail instead of manufacturing silence. It is removed after
the operation. Canonical `oida.listen` metadata retains the original file hash,
offset, full source duration and actual window duration. The original upload is
unchanged. Declared spectral/source-admission material continues through its
existing dedicated paths rather than this ordinary-file endpoint.
