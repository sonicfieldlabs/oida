# Changelog

## 0.2.0 - Unreleased

- Made `mac-mps` the default engine everywhere (daemon, macOS shell, env
  examples); the daemon prewarms MOSS-Audio-4B-Instruct in the background and
  reports readiness at `/engine/status` (`HMM_MOSS_PREWARM=0` disables).
- Added the deterministic **signal listener** (`aear/signal_listener.py`):
  DSP-only classification (silence / speech-like / music-like / tonal /
  percussive / noise / ambient), honest captions, and inferred hypotheses with
  numeric bases. Perception never falls back to placeholder text; without a
  model the evidence level honestly stays `measured_signal`.
- Route presets now scope MOSS passes (`moss_passes`): Basic runs one caption
  pass (seconds, not minutes), Signal is DSP-only, Speech runs
  transcribe+speech, and the new **Deep** preset runs the full report.
- Chunked mac-mps inference at 45 s per pass (`HMM_MOSS_CHUNK_SECONDS`), with a
  decode-degeneracy guard that discards token-soup output instead of letting it
  become claims, and a tokenizer-safe decode fallback.
- Added Sonic Field bridge (`aear/sonicfield.py`, `/sonicfield/*`): after a
  listen, "Explore in the wiki" searches the wiki/lexicon, topics, journal,
  93k-item library, paths, research, notes, and labs for related concepts,
  with taxonomy alias normalization and Finder reveal.
- Added `/events/stream` (SSE) so every surface mirrors one daemon state, and
  `/background/capture-request` so the web dashboard can ask the native shell
  to capture system audio.
- Default audio directory moved to `~/Documents/hmm/audio`
  (`HMM_AUDIO_DIR`); captures, uploads, and fixtures land there.
- Rebuilt the dashboard as one Listen surface (source + presets + skill
  manager), claims rendered by AKOUO category, memory and history cards, and a
  soft-minimal visual system; removed the floating spectral agent, its
  settings, RMS/peak meters, and the task tabs.
- Rebuilt the macOS shell: the main window embeds the dashboard (WKWebView) so
  web and app never diverge; the floating listener is a minimal always-on-top
  panel with live signal, latest reading, and one Listen action; global
  hotkeys default to ⌃⌥L (listen) and ⌃⌥H (show/hide listener); the shell
  auto-starts the daemon and system tap and claims dashboard capture requests.

## 0.1.0 - Unreleased

- Added platform data directory support through `HMM_DATA_DIR` / `AEAR_DATA_DIR`
  with macOS default `~/Library/Application Support/hmm`.
- Disabled implicit Hugging Face hub model fallback unless explicitly enabled.
- Added bearer-token enforcement for wildcard/LAN daemon binds.
- Added raw-audio status and wipe endpoints for local upload/live-buffer
  retention, including pre-data-dir checkout `uploads/` recordings via the
  `include_legacy` wipe option (the dashboard Wipe raw audio button sets it).
- Bounded in-memory stopped live sessions to the most recent few.
- Added Earworm-compatible Akousmata trace/session/context metadata.
- Updated AKOUO harness routing for the v0.5 public command contract and
  derived evidence levels.
- Reduced long-audio memory pressure by bounded DSP inspection and per-chunk
  source reads.
- Added release/security/contribution/citation metadata.
