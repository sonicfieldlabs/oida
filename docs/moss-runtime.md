# Local MOSS runtime

Use one source executor and `OIDA_MOSS_RESIDENT=single`. The existing MPS chunk
limit is 45 seconds. Higher concurrency, simultaneous Instruct/Thinking residency,
different checkpoints and devices need separate runtime qualification.
The repository publishes no machine-specific latency or memory guarantee.

Profile in an isolated environment with Oída's pinned `moss` dependencies and
operator-supplied MOSS source and checkpoints. The tool does not download weights
or alter an active daemon. Check decoder compatibility before loading models.

```sh
python scripts/profile-moss-runtime.py \
  --moss-repo ./MOSS-Audio \
  --instruct ./weights/MOSS-Audio-4B-Instruct \
  --thinking ./weights/MOSS-Audio-4B-Thinking \
  --output /tmp/moss-runtime-profile.json
```

Add `--check-runtime-only` to check decoding without loading weights. Inputs are
bounded to 45 seconds; decoding defaults to 64 tokens and permits at most 256.
Select FFmpeg libraries compatible with the pinned TorchCodec in the profiling
process rather than changing a system installation implicitly.

Keep the output outside the repository. It contains operator-selected paths,
checkpoint/input provenance and sampled stage timing and memory. Sampled peaks
can miss short allocations; process RSS and accelerator allocations may overlap.
Synthetic inputs establish runtime behavior only. They do not qualify semantic
accuracy, microphone bandwidth, perception or cancellation during inference.
