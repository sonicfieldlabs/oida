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
`HMM_AUTH_TOKEN` or `AEAR_AUTH_TOKEN` is set, and token-protected clients must
send `Authorization: Bearer <token>`.

MOSS-Audio Hugging Face model lookup is disabled by default. Download weights
into `weights/` or set `HMM_ALLOW_HF_HUB=1` / `AEAR_ALLOW_HF_HUB=1` explicitly.
`HF_HUB_OFFLINE=1` always disables hub lookup.
