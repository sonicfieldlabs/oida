# Local MOSS hardware profile

The September 2026 local qualification used a 32 GiB Apple Silicon Mac, full
MOSS-Audio-4B Instruct and Thinking checkpoints, PyTorch 2.10.0, Transformers
5.14.1, TorchAudio 2.10.0 and TorchCodec 0.10.0. All inference ran on MPS with
local weights, offline lookup and no fallback. Inputs were synthetic audio;
these runs measure runtime behavior, not semantic accuracy or microphone bandwidth.

## Measured results

The direct adapter matrix used deterministic tones/noise at 1, 10, 30 and 45
seconds, with a 64-generated-token ceiling. Thinking output can hit that ceiling;
these timings are not estimates for an unrestricted reasoning response.

| Operation | Wall seconds | Sampled MPS driver peak, GiB |
| --- | ---: | ---: |
| Initial Instruct load | 16.86 | 9.74 |
| Instruct, 1 s input | 16.67 | 9.87 |
| Instruct, 10 s input | 7.27 | 11.10 |
| Instruct, 30 s input | 6.95 | 14.84 |
| Instruct, 45 s input | 14.94 | 15.94 |
| Switch to Thinking | 15.65 | 15.75 |
| Thinking, 1 s input | 8.44 | 9.87 |
| Thinking, 10 s input | 4.90 | 11.12 |
| Thinking, 30 s input | 5.92 | 14.86 |
| Thinking, 45 s input | 8.56 | 15.96 |
| Reload Instruct | 15.92 | 15.77 |

Switch/reload peaks include the preceding resident/cache state. Each completed
load exposed exactly one resident checkpoint. Final release left no resident
model and about 7.5 MiB of driver allocations. The first generation includes
initialization effects; timings are individual observations, not statistical
throughput guarantees. MPS driver/tensor memory was sampled every 50 ms, so short
peaks can be missed. Driver and tensor figures overlap; process RSS and MPS
allocations must not be summed as independent unified-memory consumption.

A separate test used real FFmpeg loopback capture, the actual owner source-job
API, its normal `basic` caption recipe and canonical storage. Fallback was disabled.
A three-second cold input took 37.94 s in listening, and a ten-second warm input
took 14.83 s. Acquisition of the finite local fixtures took about 0.21–0.26 s;
this is not the wall time required to capture those durations from a live source.
Both retained records contained `local-moss` pass receipts and actual input
provenance. Sampled driver peak was 11.55 GiB.

While the actual model was loading/listening, queued cancellation returned in
10.5 ms and the stale job expired without acquisition/inference. Expiry was
recorded 131 ms after its deadline. Across 237 status polls, median response time
was 3.4 ms; the largest cold-load outlier was 1.27 s. The control path remains
available, but these measurements do not establish a hard real-time latency SLA.
Cancellation after listening starts remains the separate model-cancellation gate.

## Residency and concurrency decision

Retain `OIDA_MOSS_RESIDENT=single`, one source executor and the existing 45-second
MPS chunk limit. Do not increase inference concurrency or enable simultaneous
Instruct/Thinking residency on the strength of these measurements. The observed
active peak plus another roughly 9.74 GiB checkpoint gives a simple untested
estimate of about 25.7 GiB, above this host's 24.96 GiB recommended MPS budget.
Actual dual residency was not attempted. Switching has a measurable cost, but
this does not justify speculative preloading or a second model worker.

The O16 hardware evidence gate is satisfied for this conservative local profile.
Longer reasoning outputs, different checkpoints/devices, other concurrent
workloads and any higher concurrency require fresh measurements. No production
settings or daemon processes were changed during profiling.

## Reproduce

Use an isolated environment with Oída's pinned `moss` dependencies and the local
MOSS source/checkpoints. The profile tool never downloads weights or alters the
running daemon. Decode compatibility is checked before loading weights.

TorchCodec 0.10.0 could not load this host's FFmpeg 9 libraries. A separate
keg-only FFmpeg 8 installation resolved it; FFmpeg 9 remained the active binary.
Select compatible libraries for the profiling process rather than replacing the
system FFmpeg:

```sh
DYLD_LIBRARY_PATH="$(brew --prefix ffmpeg@8)/lib" python scripts/profile-moss-runtime.py \
  --moss-repo ./MOSS-Audio \
  --instruct ./weights/MOSS-Audio-4B-Instruct \
  --thinking ./weights/MOSS-Audio-4B-Thinking \
  --output /tmp/moss-hardware-profile.json
```

Add `--check-runtime-only` to verify decoding without loading weights. Inputs are
bounded to 45 seconds and decoding to at most 256 tokens; the default is 64. The
JSON includes checkpoint/input provenance and stage-specific timing and memory.
Keep the output with its exact environment and checkpoint inventory when comparing
runs. It may contain operator-selected local paths.

Thinking emitted one unexpected-key load warning for
`audio_encoder.embed_positions.inv_timescales`. The local MOSS implementation
explicitly regenerates this sinusoidal buffer with `persistent=False`; comparison
to its stored float32 values found a maximum absolute difference of 5.96e-8.
There were no reported missing learned weights. The warning and original decoder
failure were preserved with the local verification evidence, not silently removed.
