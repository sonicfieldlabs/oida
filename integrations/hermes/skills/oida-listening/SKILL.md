---
name: oida-listening
description: Use when the user asks to listen to, analyze, compare, remember, search, or reason about audio, sound, speech, music, field recordings, sonic memory, AKOÚŌ, Earworm, Akousmata, or the Oída gateway.
---

# Oída listening

Oída is the installed listening stack and local gateway:

- AKOÚŌ routes listening modes and keeps heard, measured, inferred, interpreted, speculative, and undetermined claims distinct.
- Earworm records time-indexed context, apparatus, provenance, actions, and retention.
- Akousmata stores and navigates sonic memories.
- Oída may use its configured local audio engine, or it may harness perception supplied by this host's audio-capable model.

## Choose one perception path

1. If this host can directly receive and inspect the user's audio, use `oida_harness`. Submit a structured `oida/host-perception/v0.1` object with host/model/session, source, apparatus, time-anchored observations, and uncertainty.
2. If the audio is available as a local filesystem path and the host cannot directly hear it, use `oida_listen`. Oída will use its configured engine and DSP.
3. If neither direct host audio nor a readable local path exists, ask for the missing audio or path. Never fabricate a listening pass.

Call `oida_capabilities` when engine, route, schema, or memory availability is uncertain. Call `oida_route` before unusual, high-stakes, comparative, forensic, access, fiction, or deep workflows.

## Evidence discipline

- Model observations are machine-heard evidence, not measurements.
- Use `measured` only for DSP, waveform/spectrogram inspection, file metadata, calibrated tools, or an explicitly declared human measurement.
- Declare the actual apparatus: channel count, sample rate/bandwidth, preprocessing or downmix when known, calibration, and blind spots.
- Keep source identity, speaker identity, emotion, location, causality, cultural context, and absolute physical level uncertain unless independently supported.
- Anchor temporal claims when timestamps are available.
- Preserve disagreements between model perception, DSP, memory, and context as disagreements.

## Memory and follow-up

Remembering is explicit. Set `remember=true` or call `oida_remember` only when the user asks, the selected `remember` route requires it, or the workflow already authorizes durable memory. Use `oida_memory_search` and `oida_memory_get` for sonic recurrence or lineage. Use `oida_forget` only on an explicit request.

Use `oida_ask` for grounded follow-up questions about a returned listening event. Do not send raw audio to remote services; Oída integrations are local-first.

## Result

Lead with the useful listening result. Then distinguish measured facts, machine-heard observations, interpretations, and unresolved points in proportion to the task. Include the route/preset and saved trace id when they matter.
