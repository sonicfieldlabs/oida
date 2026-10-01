# Digital sectors and structured reports (unreleased)

`POST /sources/spectrum` measures one channel of an owner-selected local WAV.
Required fields are `operation_id`, `path`, `source_sha256`, `lower_hz`, `upper_hz`,
`end_s`, `permission: "granted"` and `permission_ref`. Optional fields are
`start_s` (default 0), `channel` (default 0), `remember`, `recipients`, and
`evidence_class` (`capture_unverified` or caller-declared `synthetic_declared`).

The source is limited to 64 MiB, eight channels and 192 kHz. Analysis ends at or
before 60 seconds; the selected interval must fit the file and align with samples.
Band edges must lie strictly inside digital Nyquist. At least five cycles at the
lower edge and two frequency bins across the band are required. A short low-frequency
window is refused, with no fabricated measurement or canonical result.

The measurement is mean-square digital energy in `sample^2`, computed from the
selected channel using a symmetric Hann window and normalized one-sided periodogram.
Interior bins are doubled, DC/Nyquist are not; the selected lower edge is inclusive
and upper edge exclusive. Frequency spacing is `1 / duration`. Finite-window leakage,
Hann smoothing and bin-edge quantization remain. There is no physical SPL,
calibrated capture response, semantic interpretation or human-hearing claim.
Channels are not mixed. Synthetic declaration is attribution, not detection of a
file's origin; the default remains unverified capture.

The returned account retains a validated MASA measurement, its source hash/method/
window/uncertainty, Earworm measurement descriptors and an E8 sector. In the access
contract, `model_input` names the deterministic DSP adapter and actual digital
representation; it does not imply a neural model. Capture and perceptual access
remain unknown. The raw WAV is read locally and not copied into the account.

The A7 report and `text` rendering are both returned. Rendering text is retained
with the account when requested, while structured encoding, intended recipients
and human audio access remain distinct. A human-readable textual account is not
proof that its recipient heard the measured signal.

`POST /owner/records/{id}/agent-report` accepts `operation_id`, optional
`recipients` and `remember`. It uses canonical DSP/observation A7 reports or retained
actual model-pass receipts, preserving the original account in its new account.
Inherited statements have category/confidence `undetermined` and source `memory`;
the endpoint does not recompute measurements, run a model or renew claim validity.
Unsupported records return 409. It checks that the source record did not change
while the new account was constructed. Current owner source/retention gates apply;
this is an owner-only operation and grants no public disclosure permission.

Both routes reuse owner operation receipts, retry refusal, cancellation and the
publication fence. Policy is checked before work and again before retention.
Cancelled work may finish calculation but cannot retain a late result. Raw records,
owner-journal references and final operation receipts remain separate writes;
interrupted committing operations require inspection rather than automatic replay.
A completed receipt links to the new canonical account when `remember` is true.
