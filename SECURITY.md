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

Audio-analysis routes intentionally accept an operator-selected local file path;
these are desktop/localhost file references, not web-root-relative tenant paths.
The daemon verifies that each reference resolves to an existing regular file
before analysis. Writes remain confined to configured Oída data directories,
and names used inside those directories are normalized. The Sonic Field reveal
route separately requires the resolved target to remain inside its configured
Sonic Field root.

MOSS-Audio Hugging Face model lookup is disabled by default. Download weights
into `weights/` or set `OIDA_ALLOW_HF_HUB=1` (legacy `HMM_`/`AEAR_`) explicitly.
`HF_HUB_OFFLINE=1` always disables hub lookup.

## Temporary upstream PyTorch exceptions

The optional local runtime retains Torch and Torchaudio 2.10.0. Updating this
pair requires separate model, dependency and accelerator qualification; this
review does not claim new GPU/MPS or model-inference evidence.

The machine-readable policy is [advisory-exceptions.json](advisory-exceptions.json).
The repository CODEOWNER, **@emeisazam**, owns follow-up. Reviewed **26 September
2026**; exceptions expire **10 October 2026**, or earlier if the affected API,
model trust boundary or runtime version changes. Review again before enabling
an optional runtime. CI refuses an expired policy or a widened advisory set
before invoking pip-audit; it suppresses exactly these two advisory identities.

| Advisory | Current upstream evidence | Scoped repository review |
| --- | --- | --- |
| `PYSEC-2026-139` / `CVE-2026-4538` | [.pt2 deserialization advisory](https://github.com/advisories/GHSA-33x2-ppm4-v46v); no patched version is listed by the current audit feed. | No direct `torch.export.load` call or .pt2 model intake found in owner source. |
| `CVE-2025-3000` / `GHSA-rrmf-rvhw-rf47` | [TorchScript advisory](https://github.com/advisories/GHSA-rrmf-rvhw-rf47); fixed in 2.13.0. | No direct `torch.jit.script` call found in owner source. |

This is a bounded source review, not a transitive execution trace or permission
to load untrusted model artifacts. The reviewed optional runtime remains conditional on its existing operational
qualification requirements.

The September review also found two AnyIO advisories in the locked 4.13.0:
[TLS hostname handling](https://github.com/advisories/GHSA-82r6-8w77-94w6) and
[process-pool stderr handling](https://github.com/advisories/GHSA-5p39-cfhj-2xmp).
Both have fixes in 4.14.2. The dependency floor now excludes older versions;
these findings are not suppressed. New audit findings continue to fail CI.

### Local review, 2026-09-13

Repository call-site inspection found no new use of the excepted APIs. The
all-extras dependency audit passes with exactly the same two exceptions; no
additional advisory was suppressed. HTTPX2/HTTPCore2 were updated to 2.12.0,
and the affected optional dependency bounds were refreshed. These software
checks do not validate GPU/MPS inference with the new resolved dependencies.
Keep optional model execution conditional on that validation and review the
exceptions again before enabling it, or by the deadline above.
