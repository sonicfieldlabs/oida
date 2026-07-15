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

Choose **Oída only** or **Oída + GERM**. The recommended Oída selection is
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
when another setting attempts to enable it. Model paths, weights, listening
history, captures, credentials, and local machine configuration are ignored by
Git and must remain outside commits and public issues.
