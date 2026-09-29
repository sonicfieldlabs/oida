# Phase 1 dependency candidate

The current Oída source requires `akousma>=0.8.3` and `akousmata>=0.8.2`.
Earworm supplies retained-policy projection and checked content-addressed object
resolution; Akousmata declares its research contract dependency at runtime.

Local development and CI install the same canonical wheels from `vendor/`.
`SHA256SUMS`, `compatibility.json` and `uv.lock` identify their bytes and source
baselines. These replace the incompatible Earworm 0.8.1 CI checkout and editable
sibling overrides. They are unpublished candidates; the Oída application version
is unchanged and its actual wheel hash identifies each local build.

Use a new environment for qualification. Do not resync an active supervised
owner or migrate memory implicitly. Current package tests do not qualify live
models, native capture, perceptual quality, or hosted providers. Historical
installer compatibility profiles remain unchanged.
