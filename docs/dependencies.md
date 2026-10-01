# Runtime dependencies

Oída requires `akousma>=0.8.3` and `akousmata>=0.8.2`. Earworm supplies
retained-policy projection and checked content-addressed object resolution;
Akousmata declares its research contract dependency at runtime.

Local development and CI select canonical wheels from `vendor/` using `uv.lock`.
`SHA256SUMS` and `compatibility.json` identify the actual artifact bytes and
source baselines. Verify those records before installation. A dependency's
minimum version does not prove that its wheel contains subsequent source fixes;
changed owners require new artifacts and renewed compatibility checks.

Qualify dependencies in a separate environment. Do not resync an active owner
or migrate its memory implicitly. Package tests do not qualify live models,
native capture, perceptual quality or hosted providers.

Optional model dependencies are pinned separately from the base application.
`advisory-exceptions.json` records narrowly scoped, expiring reviews, enforced by
`scripts/audit_dependencies.py`. An exception does not fix a vulnerability
or permit loading untrusted model artifacts. Expiry blocks the dependency gate;
changes to the runtime or trust boundary require a new review.
