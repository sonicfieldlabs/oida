# MOSS-Audio Setup

Oída is usable without a model. Begin with `--profile stub` when validating the
gateway, dashboard, deterministic signal listener, MCP surface, or agent host
integration.

The first model-backed path developed and tested for Oída uses the open-source
MOSS-Audio Instruct and Thinking checkpoints. The gateway remains
model-agnostic, and no hosted provider is enabled by this setup.

## Guided Installation

The Listening Stack assistant checks the host, shows current model sizes,
downloads selected checkpoints, keeps them outside Git, configures Oída, and
can install its agent adapters:

```bash
curl -fsSL https://raw.githubusercontent.com/sonicfieldlabs/listening-stack/main/install.sh | bash
```

Choose the **core stack** and add **GERM** only if you want the optional
cultivation handoff. The recommended Oída selection is
MOSS-Audio 4B Instruct plus 4B Thinking.

## Manual Installation

From the Oída repository:

```bash
git clone https://github.com/OpenMOSS/MOSS-Audio.git MOSS-Audio
uv sync --locked --extra moss

uv run hf download OpenMOSS-Team/MOSS-Audio-4B-Instruct \
  --local-dir weights/MOSS-Audio-4B-Instruct
uv run hf download OpenMOSS-Team/MOSS-Audio-4B-Thinking \
  --local-dir weights/MOSS-Audio-4B-Thinking
```

To bring an existing environment that hears with MOSS up to date, use
`scripts/sync_operator.sh` rather than `uv sync`. The MOSS stack is the `moss` extra, so
`uv sync --locked` alone uninstalls torch from such an environment. The script always passes
`--extra moss`, refuses while an Oída server runs from `.venv`, and prints the stack's versions
afterwards; `--dry-run` shows what would change. With the locked stack (torch 2.13.0, torchcodec
0.17.0), Homebrew's FFmpeg 8 and 9 both decode.

The released MOSS-Audio checkpoints are public Apache-2.0 downloads. A Hugging
Face login is not normally needed for these four repositories. Review the
exact upstream model card before use.

For the larger pair:

```bash
uv run hf download OpenMOSS-Team/MOSS-Audio-8B-Instruct \
  --local-dir weights/MOSS-Audio-8B-Instruct
uv run hf download OpenMOSS-Team/MOSS-Audio-8B-Thinking \
  --local-dir weights/MOSS-Audio-8B-Thinking
```

The Hugging Face API currently reports approximately 10.45 GB for each 4B
repository and 18.11 GB for each 8B repository. Leave additional space for
Python, PyTorch, the MOSS-Audio source, and download metadata.

## Configure Oída

```bash
export OIDA_MOSS_AUDIO_REPO="$PWD/MOSS-Audio"
export OIDA_MOSS_INSTRUCT_MODEL="$PWD/weights/MOSS-Audio-4B-Instruct"
export OIDA_MOSS_THINKING_MODEL="$PWD/weights/MOSS-Audio-4B-Thinking"
export OIDA_MOSS_RESIDENT=single
export OIDA_REQUIRE_MODEL=1
```

For 8B, change the two checkpoint paths. When only one checkpoint is available,
the same path may be assigned to both routes; the split pair gives Oída the
intended direct and deep-listening roles.

`OIDA_MOSS_RESIDENT=single` keeps only one checkpoint resident and hot-swaps
between them. This is the safer default on unified-memory systems.

On Apple silicon, Oída releases the Metal allocator's cached buffers during and
after each generation. Each decoding step frees buffers a little too small for
the next, and PyTorch's MPS allocator keeps freed buffers until its low
watermark, 1.4 times Metal's recommended working set: about 37.5 GB on a 32 GB
computer. Measured with one 10.4 GB checkpoint, a pass of 600 new tokens filled
it, the computer swapped 12 GB, and the passes that followed ran at 1 to 5
tokens a second. With the release, the driver's memory stays within
`OIDA_MPS_POOL_HEADROOM_MB` megabytes (2048 by default) of its level when the
pass began and returns to the model's own size when it ends. Each pass's receipt
records the start, the peak, the end and the number of releases
(`generation.metal_memory`). `OIDA_MPS_POOL_HEADROOM_MB=0` leaves the allocator
alone. The release needs torch 2.13 or later: on torch 2.10.0 it made answers to
long prompts unreadable, so with an earlier torch the allocator is left alone
whatever the setting.

On Apple silicon Oída also draws each sampled token on the CPU. With torch
2.13.0, `torch.multinomial` on the mps device sometimes returns a token whose
probability is zero, which puts one unrelated word into an otherwise readable
account. The temperature, top-k and top-p of the recipe are applied to the
scores on the CPU, the token is drawn there, and the generation takes it as it
is. The pass receipt records it (`generation.sampled_on`: `cpu`, or the device
when the draw stayed there). `OIDA_MPS_SAMPLE_ON_CPU=0` returns the draw to the
GPU.

## Hardware Planning

| Checkpoint family | Minimum RAM | Suggested RAM | Approx. repository size |
| --- | ---: | ---: | ---: |
| MOSS-Audio 4B | 16 GB | 24 GB | 10.45 GB each |
| MOSS-Audio 8B | 24 GB | 48 GB | 18.11 GB each |

These are Oída planning figures, not upstream guarantees. Apple Silicon MPS is
the current embedded release target. The engine can discover CUDA or CPU, but
the documented CUDA deployment uses the separately managed MOSS-Audio SGLang
route and CPU inference can be slow.

## Verify

```bash
uv run oida doctor
uv run oida start --profile mac-mps
curl http://127.0.0.1:8765/engine/status
```

The engine is ready when the status identifies the selected local checkpoint
and a supported device. The dashboard can then run **General** for an Instruct
pass and **Deep** or **Music** for a Thinking pass.

Oída refuses silent Hub lookup by default. `HF_HUB_OFFLINE=1` disables it even
when another setting attempts to enable it. If Hub lookup is explicitly
enabled, built-in 4B IDs use audited commit pins and custom remote model IDs
must use `repository@<40-character-commit>`. Model paths, weights, listening
history, captures, credentials, and local machine configuration are ignored by
Git and must remain outside commits and public issues.
