# Local transposition runtime (unreleased)

`POST /sources/transpositions` accepts an owner operation ID, local WAV `path`,
`source_sha256`, `permission: granted`, a `permission_ref`, `kind`, `lower_hz`,
`upper_hz`, and optional `remember`. The existing owner access and covenant gates
apply. Sources are bounded to 32 MiB, 0.1–10 seconds, two channels and 192 kHz.
The owner must select the input; the adapter does not fetch URLs or capture devices.

| Kind | Additional parameter | Result |
| --- | --- | --- |
| `filtered_resample` | `target_rate` | Polyphase/Kaiser resampling; same duration, new sampling rate |
| `playback_rate` | `rate_ratio` | Filtered samples interpreted at a new integer rate; frequency and duration change |
| `frequency_translation` | `offset_hz` | Filtered analytic-signal modulation; additive Hz translation, preserved duration |
| `pitch_shift` | `cents` | Polyphase resampling at the same output rate; pitch scales and duration changes |

Only the selected parameter is accepted. Input/output bands must remain strictly
inside their respective digital Nyquist limits and above zero. Folding across zero
is refused. Exact duration alignment is required for filtered resampling. Pitch
ratios use a bounded rational approximation; this is not a duration-preserving
phase-vocoder implementation. Every path first applies an eighth-order Butterworth
bandpass in forward/backward SOS form. Finite transition bands and edge transients
remain; the declared band is not an ideal brick-wall guarantee. Channels are
processed independently; FLOAT WAV output is not level-calibrated or normalized.

The result carries a fully validated MASA receipt and Earworm's typed transposition
recipe, actual input/output hashes, sample rates, durations, engine/version,
parameters, permission reference and directed representation lineage. With
`remember`, existing Earworm graph construction and protected graph-record APIs
retain the result in Akousmata. The operation receipt links its canonical record,
hashes and MASA derivation. Local asset locations remain owner-only. The selected
MASA validator and its matching core lineage registry must be installed; unavailable
contracts fail closed rather than inventing a receipt.

Cancellation uses `/operations/{operation_id}/cancel` until the existing commit
fence. DSP may finish computing, but cancelled output is not published or stored.
Duplicate IDs do not execute twice; restart marks unfinished operations interrupted.
After the commit fence, the output file and canonical store are separate writes:
a store failure may leave a permitted local derivative requiring owner inspection.
A retry must use a fresh ID; existing output paths are never overwritten.

Tests measure known synthetic-tone output frequency/duration, channel separation,
stop-band rejection, actual byte hashes, canonical graph links, refusal and
cancellation. These are DSP/software checks. See [capability limits](capability-limits.md)
for the separate capture, model-input and human-access boundaries.
