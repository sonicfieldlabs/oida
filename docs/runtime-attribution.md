# Runtime apparatus decisions and pass provenance (unreleased)

The daemon composes AKOÚŌ's existing extended-spectrum gate and Earworm's
listening-access validator. `/listen-event` and `/gateway/listen` accept paired
`listening_access` and `spectral_request` objects. They use
`earworm/listening-access/v1` and `akouo/extended-spectrum-request/v0.1` respectively.
The subject must be `sha256:<actual source file digest>`; declared native rate and
channel count must match the inspected source. Existing covenant checks run first.

A nonpermitted spectral request returns HTTP 409 with
`detail.apparatus_decision`, before a model pass or remembered listening result.
The decision carries support, undetermined claim status, reasons and attributable
observe-only authority. It is also published on the existing transient event
channel. The owner journal also retains the apparatus decision. Invalid declarations return 400;
missing compatible packaged contracts return 503. Supply both fields or neither.
Ordinary requests retain their existing behavior and claim filters.

Client-supplied `resolved_refs` cannot establish resolution. The daemon resolves
source bytes and can consult an operator-selected local registry for capture and
sampled-representation evidence (see below). Model support remains unknown unless exact operator-approved model/input and preprocessing evidence resolves against observable, already-loaded adapters. Reusing a source reference cannot grant that approval. Positive requests use enforced prepared-input bindings; unsupported requests still abstain.
Declaration validity, sampled Nyquist bounds, physical capture support, model
input and human access remain distinct.

Every engine report now carries `engine.pass_provenance`, an ordered list of
`oida/pass-provenance/v1` receipts. Aggregation preserves all returned passes,
including mixed models and fallback. The list also travels as event `pass_provenance`
and is retained in the canonical Akousma listening payload. Legacy top-level engine fields remain for
compatibility; consult the receipt list for per-pass attribution. Stub and DSP-only
receipts explicitly declare no model weights or model input. MOSS records the rate
from the actual processor and the dimensions/duration of the array passed to it,
not a hardcoded rate inferred from the filename. The bundled MOSS audio loader
mixes to mono and resamples when required; this is not capture-bandwidth evidence.

At local MOSS load, a sorted safetensors filename/size/SHA-256 inventory is hashed
as compact, key-sorted JSON (`sha256-manifest-v1`). It is checked before and after
loading and retained with the resident model. This digest identifies the local
file inventory, not the in-memory tensors, a signature, or a remote weight set.
Cold loading reads those files twice; later passes reuse the resident inventory.
Explicit pinned or loaded revisions are recorded separately. Explicitly enabled remote lookup resolves one immutable snapshot directory before model and processor loading; its local safetensors inventory is checked around loading by the same code. No hosted-provider weight inventory is inferred.

SGLang and hosted adapters retain the returned model and provider. Google model
version is labelled provider-reported when supplied. Hidden weight bytes,
unreported immutable revisions and unobservable provider preprocessing remain
unknown. A provider name, requested model alias or source sample rate never fills
those gaps. Unknown fields are limitations, not successful verification.

Use built local Earworm/AKOÚŌ packages containing the new contracts. Their release
numbers alone do not distinguish unreleased working-tree content; record wheel
hashes in local validation. Editable AKOÚŌ installations can omit bundled schemas.
No dependency release bump, provider call, model download or deployment is part
of this change. Tests cover actual stub HTTP handling and mocked MOSS adapter
input; full-weight inference, hardware and hosted-provider claims need separate
evidence. Standalone transcription, event, caption, speech, music, QA, thinking and direct-analysis helpers reuse the same source-bound wrapper. Opaque provider fields remain unknown.

## Source and chunk links

Passes executed by `report` now include a unique `pass_id`, original source
SHA-256, sample-aligned `source.window_s`, and `chunk_index` (`null` for a whole
source). `submitted_audio` identifies the encoded file actually submitted to the
adapter, with its SHA-256 and byte count. It does not identify hidden provider
preprocessing or an in-memory tensor. No private or temporary path enters these
links. The existing cropper still owns crop creation and cleanup.

Overlapping chunks have separate windows and hashes. Speech/music passes that
use only the first chunk carry that first window, even when other passes cover
later chunks. Receipts remain in the report, event and canonical saved payload
after temporary chunk files are removed. They identify what was submitted, not
proof of model comprehension or human access.

The original file is hash-checked when binding and around crop creation; device,
inode, size, modification and change times are checked during passes. Submitted
files are hash-checked before and after each pass. Changes discard the result
with a validation error. This avoids rehashing the entire original once per crop;
submitted-file checks still add streaming file reads per pass. Filesystem checks
do not prove that an external provider read a shared path or processed its bytes.

## Operator-selected evidence registry

Set `OIDA_APPARATUS_EVIDENCE=/absolute/path/to/manifest.json` before starting the
daemon. Requests cannot select a registry path. The configured path is captured
at startup; its current contents are rechecked for each spectral request. No
network fetching, uploads or evidence-writing endpoint is added.

A manifest has exactly `contract: "oida/apparatus-evidence/v1"` and `entries`.
Each entry has `ref`, `kind` (`capture`, `sampled_representation` or `model_input`), a relative
`file`, its lowercase `sha256`, and a timezone-aware `expires_at` timestamp.
Capture/representation evidence JSON has exactly `ref`, `subject_ref`, `kind`, and `declaration`. Model evidence additionally includes exact `binding_ids` (model-kind to prepared binding ID) and the complete `preprocessing` receipt list.
The declaration must equal the corresponding submitted access block in full;
its subject must equal the source SHA-256 reference. A changed subject or claim
cannot reuse approval for a different declaration.

For example, an operator can write an evidence object with
`kind: "sampled_representation"` and the exact approved representation block,
then place its file hash and expiry in the manifest. Each separately resolved
reference needs its own matching entry. Approval here means the operator selected
those bytes, not that Oída independently calibrated a sensor.

Manifest and evidence files are each limited to 128 KiB; a manifest has at most
256 entries. Duplicate keys/references, unknown kinds, nonfinite JSON, malformed
expiry, digest mismatch and paths escaping the configured directory are rejected.
Expired or mismatched declarations remain unresolved with an explicit reason.
Unreadable or malformed configured evidence yields HTTP 503. Registry results
carry the manifest hash and resolved/unresolved references, with no local paths.
Every model, effective-input, competence-evidence and preprocessing reference must resolve through a matching `model_input` evidence entry. Prepared weights must be known; an opaque model descriptor cannot enable this path. Removing or expiring evidence affects the next request; this is not
a durable policy journal or recall of earlier exported decisions.

## Prepared local input and execution binding

Spectral request decisions now include `input_bindings` for the selected report's
model kinds. Preparation uses only already-resident local MOSS models; it never
loads weights, changes residency, generates text or calls a provider. It reuses
the same audio loader and input-receipt constructor as generation, under the
existing model lock. Whole-source preparation is bounded by the configured report
chunk duration. Longer sources remain unknown in this preflight.

The effective-input receipt includes finite mono float32 samples' SHA-256,
`mono-f32le` encoding, rate, channel count, sample count and duration. The
`representation_ref` hashes that complete descriptor, so identical sample bytes
at different rates have different representation identities. `model_ref` hashes
the selected model/provider/kind/revision/weight-inventory descriptor. The binding
ID hashes both descriptors together. These are local descriptor identities, not
signatures, exact hidden tensor hashes or evidence of semantic comprehension.

The gate compares declared model/representation references, rate, channels, window
and Nyquist ceiling with observable prepared input. Contradictions are unsupported.
Missing local models, opaque provider preprocessing and unavailable routing remain
unknown. Matching prepared identity alone does not grant permission; separately approved capture and competence declarations are required.
The source digest is rechecked after preparation, preventing mixed source receipts.

`enforce_input_bindings({model_kind: binding_id})` is a trusted Python execution
context. Inside it, local MOSS recomputes its binding before processor/model
execution. Drift in audio, rate, revision or weight inventory raises
`InputBindingChanged`; no output is accepted. Stub, SGLang and externally selected
routed adapters cannot silently satisfy that binding. Context state resets after
success or failure. Ordinary requests retain their existing routing/fallback rules.
Prepared responses are detached from resident metadata; callers cannot mutate
cached inventory through them.

The REST spectral path obtains binding IDs from the loaded adapter and requires exact operator files; caller-chosen IDs are not authority. Positive requests enforce those bindings around execution and recheck current approval/input identity before accepting the result.
Generation receipts carry their `input_binding_id` even outside that context.
Validation uses an adapter double to exercise the real preparation/generation
boundary at 24 kHz, plus stub HTTP tests. An additional real local MPS Instruct pass verifies actual weight inventory, 16 kHz mono prepared input and matching generation/source receipts. It does not qualify semantic competence, hosted providers or physical devices.

## Chunk leases and supported scope

Each source-bound invocation obtains a lease from its already-loaded observable
adapter for the actual submitted chunk. Generation must honor its descriptor ID;
changed input, weights or adapter selection fails rather than falling back under
that lease. Whole-source positive spectral authorization remains bounded by the
configured chunk duration. Longer spectral requests abstain: callers must submit
individually bounded windows with their own exact approval. Ordinary chunked reports
retain all actual source windows/pass receipts and acquire per-chunk leases when
preparation is observable. Cold preparation does not load a model.

Operator-approved files are authorization records, not independent verification of
calibration or semantic competence. The positive acceptance matrix uses synthetic
fixtures and adapter doubles. The local full-model run establishes execution and
identity only. External provider revisions, preprocessing and weights are reported
only to the extent actually exposed; hidden fields remain unknown.

See [digital sectors and structured reports](digital-sectors.md) for measurement
and rendering paths that do not require a model competence claim.
