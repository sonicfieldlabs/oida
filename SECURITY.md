# Security Policy

## Supported Versions

Security fixes target the current `main` branch and the latest tagged minor
release. Older release lines receive fixes only when explicitly announced.

## Reporting A Vulnerability

Please report security issues privately to Sonic Field Labs before public
disclosure. Include the affected commit, local configuration, reproduction steps,
and whether raw audio or private listening traces can be exposed.

## Local-First Boundaries

`oida` binds to `127.0.0.1` by default. Wildcard or LAN binds are refused unless
`OIDA_AUTH_TOKEN` (legacy `HMM_AUTH_TOKEN`/`AEAR_AUTH_TOKEN`) is set, and
token-protected clients must send `Authorization: Bearer <token>`. Loopback
requests are additionally guarded by Host/Origin checks against DNS rebinding
and cross-origin calls.

MOSS-Audio Hugging Face model lookup is disabled by default. Download weights
into `weights/` or set `OIDA_ALLOW_HF_HUB=1` (legacy `HMM_`/`AEAR_`) explicitly.
`HF_HUB_OFFLINE=1` always disables hub lookup.

## Temporary Upstream PyTorch Exceptions

The optional embedded MOSS-Audio runtime uses the locally validated Torch and
Torchaudio 2.10.0 pair. Upstream MOSS-Audio currently pins 2.9.1, while moving
Oída to 2.13 requires a new TorchCodec, Transformers, MPS, and model-inference
validation cycle.

Two findings remain accepted temporarily for this optional local runtime:

| Advisory | Affected API | Oída exposure | Review deadline |
| --- | --- | --- | --- |
| `PYSEC-2026-139` / `CVE-2026-4538` | `torch.export.load` of `.pt2` artifacts | Oída does not call this API or accept `.pt2` model artifacts. No patched PyTorch release is currently published. | 2026-09-02 |
| `GHSA-rrmf-rvhw-rf47` / `CVE-2025-3000` | `torch.jit.script` | Oída does not call this API. PyTorch 2.13 contains the fix, but is not yet validated with the embedded MOSS-Audio dependency set. | 2026-09-02 |

The exception ends immediately if Oída begins calling either API, if its model
trust boundary changes, or when a compatible runtime is validated. The
all-extras CI audit ignores only these identifiers and fails on any new
finding.
