# Security Policy

## Supported Versions

Security fixes are accepted against the current `main` branch until the first
public release tag is cut.

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
