# Changelog

## 0.2.0 - 2026-07-10

- **Oída is now the complete listening distribution and gateway**: installing
  it resolves AKOÚŌ (`akouo-contract`), Earworm's `akousma`, and the Akousmata
  navigator. One FastAPI process exposes the agent/dashboard, REST gateway,
  official streamable HTTP MCP at `/mcp`, and full library at `/library/`.
- **Provider-neutral host perception**: `oida/gateway/v0.2` accepts either
  Oída-owned audio (`/gateway/listen`) or a declared report from an
  audio-capable host (`/gateway/harness`). Host-model observations cannot be
  silently promoted to DSP measurements; apparatus limits and unknowns remain
  explicit, while MOSS keeps its 16 kHz mono restrictions.
- **Gateway lifecycle and local adapters**: `start`, `stop`, `status`, `doctor`,
  `agent`, `gateway`, and `integrate` commands manage a singleton daemon and
  install bundled Hermes, Codex, Claude, and private-network-remote integrations.
  MCP processes ensure or reuse the gateway and pin the active Python runtime.
- **Earworm on every pass, durable memory only by consent**: session-scoped
  Earworm context is emitted for all gateway listens and host harness passes;
  Akousmata writes still require an explicit remember action.

- **Akousmata history embedded in the dashboard**: a new left-side
  "Akousmata" section browses the shared store natively inside oída (search,
  compact cards, and a detail modal with listenings, lineage, kinship, audio
  playback, and the three germ buttons) — the same library the standalone
  akousmata navigator (`github.com/sonicfieldlabs/akousmata`) serves, without
  launching the external app. Backed by new optional `/akousmata/*` routes
  (`oida/akousmata_view.py`) over py-akousma; card shapes stay compatible
  with the navigator. Read-only by design: oída writes through its listen
  flow and the germ bridge; edits belong to the navigator.

- **Preset ids aligned with AKOÚŌ v0.6's portable vocabulary**:
  `environment`→`field`, `speech`→`voice`, `memory`→`recall` (with a
  `LEGACY_PRESET_ALIASES` map so saved configs, sessions, and older clients
  keep resolving; the manifest exposes `preset_aliases`). oída's preset id set
  is now a strict subset of the upstream `presets/presets.json` vocabulary,
  enforced by a test.
- **AKOÚŌ v0.6 contract adopted** (`akouo_contract_version: v0.6`): new
  `/remember` command with a `remember` route preset (memory comparison plus
  registration into the akousmata) alongside the read-only `memory` preset;
  `memory-lineage-listening` added to the harness mode set; `/fiction` now
  grants declared speculative permission and `/forensic` keeps its
  interpreted/speculative suppression, both matching the published
  `command_permission_overrides`. Listening outputs now carry `akouo_version`,
  an `apparatus` declaration (hybrid MOSS+DSP substrate, perception sources,
  known blind spots, model ids), and a `listener` block; claims carry `source`
  (`dsp` / `model` / `context`) and, for events, `time_range` anchors. A new
  `harness/akouo/manifest.py` loads the upstream machine-readable contract
  (`akouo.manifest.json` + `presets/presets.json`) and the test suite includes
  a drift check so the harness fallback tables cannot silently diverge.
- **Earworm v0.2 / akousma spec v1.1 adopted in the bridge**: listen records
  now carry a skimmable `summary`, listening entries are wrapped in the v1.1
  envelope (`contract`/`created_at`/`summary`/`payload`, with `akouo.*` entries
  pinned to `akouo/v0.6`), and registration links recurrences — when the same
  audio content hash already exists in the store, the new record gets a
  `same_source_as` relation to the most recent holder.
- **Floating listener redesigned**: the macOS floating listener is no longer a
  window. It is a borderless, transparent, non-activating panel whose visible
  body is the listening-result box itself — the reading is always shown, with a
  small reactive waveform in its corner that animates only while listening.
  Every control floats just outside the box and fades in on hover: listening
  mode at the top-left, close at the top-right, and source / Listen / control
  center in a row below. The box and its type are larger than the prior card,
  and it drags from the box. The box stays empty until something is actually
  heard, and the mode (preset) control matches the size of the row below it.
  (An earlier take on this rebuild used a Metal shader orb; that was dropped in
  favor of the box-centric layout.)
- **Dashboard restyled flat and grey, then re-laid-out**: one continuous
  surface instead of white cards (sections separated by hairlines, no shadows,
  flush to the width); no green anywhere. The Listen surface is now two lines —
  source + its config (capture / input / file) on one line with the Skills /
  Engine / Path controls as segmented tabs at the top-right (each toggling a
  panel), and the grey Listen button beside the route presets on the next line.
  Presets and the result actions (Ask, Remember, Wiki, JSON, and the germ
  handoff) are borderless icon+word buttons. The claim groups (Hypotheses,
  Heard, Interpreted, Measured, Undetermined, Memory) are tabs below the
  reading. Source descriptions, the preset hint, and the evidence-level chip
  were removed; the header is just the wordmark and the daemon address sits in
  the footer as `host:port`. Skills / Engine / Path open as **modal dialogs**
  (not inline). The source row is one line — source tabs plus their config; in
  System the buffer length reads `Buffer: 10sec` (no route text), and in Mic
  the input list loads by default (no Devices button) with a small icon monitor
  toggle. Selects use the app font, not a mono face.
- **Native top bar**: the mac window is just the embedded dashboard with one
  top-right bar of icon buttons, split into two groups of three — Skills /
  Engine / Path (each opens its modal in the page via `evaluateJavaScript`) and
  the shell actions floating-listener / reload / browser (or Start daemon when
  offline). Icons get a subtle hover wash. The shell injects `window.__oidaNative`
  so the page hides its own browser-only panel icons.
- **Dashboard reorganized into an Obsidian-style three-rail shell**: collapse
  toggles live in the top corners (state persists). The **left rail** carries
  the source tabs (System / Mic / File) and their config at its top, then
  Recent and Memory; the **right rail** carries the tool icons at its top —
  Skills / Engine / Path, plus (inside the native app) floating-listener /
  reload / browser — then the claim Breakdown as a vertical nav. The center is
  the reading, usable with either rail collapsed, and the rails stay rails at
  native-window widths instead of stacking full-width. The foot reads like an
  agent chatbox: Remember / Wiki / JSON / germ as icon+word actions at the
  bottom-left (germ opens a Sound / Prompt / Lineage menu), the listening mode
  as a quiet borderless selector, and **Listen as a round dark send-style
  button** at the bottom-right. The Ask button, page footer, and wordmark are
  gone — the daemon address, audio path, and API/health links moved into the
  Engine modal — and opening any panel modal closes whichever was open.
- **Native shell is chrome-free**: the capsule top bar was removed; the page's
  right rail hosts the shell actions, which post back over a
  `oidaShell` script-message bridge (reload reloads the WebView directly). The
  only native overlay left is a Start-daemon card when the daemon is offline.
- Floating listener: the status dot in the box footer was removed (the status
  word and corner waveform remain), and the listening-mode button is a
  rectangular frost tile with the same rounding and margins as the buttons
  under the box.
- **Shell refinements**: the rails are drag-resizable (widths persist) with
  Obsidian-style panel toggles in the top corners; the sources are icons whose
  config always fits the rail (including the mic meter); memory search is an
  icon. The claim Breakdown is stacked collapsible sections, all expanded by
  default. The result actions condensed into one **export menu** at the box's
  bottom-right — Remember, Expand on Wiki, Export JSON, Generate derived sound,
  Convert listening to prompt (Lineage removed) — next to the Skills icon, the
  quiet mode selector, and Listen. Two corner buttons float over everything:
  a configuration menu bottom-left (Engine / Path / Reload / Open in browser)
  and, inside the native app, the floating-listener toggle bottom-right.
- **App icon**: a minimal abstract listening mark — a single bold two-hump sine
  wave on a charcoal squircle, transparent corners. Sourced from
  `apps/macos/Resources/AppIcon.svg`, shipped as `AppIcon.icns`, and staged into
  the bundle by both packaging scripts.
- **germ handoff fixed and surfaced**: `/germ/handoff` was unusable (with
  `from __future__ import annotations` FastAPI could not resolve the
  function-local request model and degraded the JSON body to a query param);
  the model is now module-scoped. The dashboard gained the three germ buttons
  (Sound / Prompt / Lineage) that persist an akousma to the shared store and
  deep-link germ; origins are normalized to the akousma vocabulary; opt-in
  song identification (`OIDA_SONGID=1`) now enriches handoff records.
- Dashboard hardening: SSE events from other surfaces no longer stomp a local
  mic recording (orphaned hot mic); mic streams are released when MediaRecorder
  fails and on page hide; duplicate render/refresh after each listen removed;
  engine fold no longer rebuilds every 20 s (dropdown-closing); phase guards on
  drop/file/path during a running listen; capture-cancel sends the request id;
  the 15 s "no capture yet" hint verifies with the daemon before flipping to an
  error; SSE reconnects after a permanently closed stream; recording shows
  elapsed time; JSON view gained copy; Ask cannot double-submit via Enter.
- Dashboard accessibility and polish: visible focus rings, AA-passing text
  tones, radio semantics + arrow-key navigation for source/preset groups, a
  live daemon status label, meter semantics, reduced-motion-aware scrolling,
  evidence-level chip colors for all levels, `dsp only` no longer styled as an
  error, dead CSS removed, tags overflow as `+n`.
- Backend correctness: memory list/limit now ordered by `createdAt` (was
  filename-random); `OIDA_MOSS_PREWARM` precedence fixed (legacy `HMM_`/`AEAR_`
  could override it); `OIDA_REQUIRE_MODEL` accepts true/yes/on; per-listen DSP
  inspection memoized (a listen decoded and hashed the same file 2–3×); ffmpeg
  conversion gets a timeout; `/sonicfield/reveal` containment can no longer be
  bypassed by sibling directories; corrupt memory/generation/conversation
  records return 4xx instead of 500; `/health` `legacy_name` reports
  `hmm, aear`; `/api` lists the capture-request, engine-model, status, and germ
  routes; double prewarm race closed.
- macOS shell: native capture filenames had a corrupted date pattern from the
  rename (`HOidassSSS`); the shell now reads `OIDA_AUTH_TOKEN` first; the
  managed daemon is stopped on app quit (its pipes died with the app and the
  orphan would crash on SIGPIPE); transient refresh errors keep recent/pinned
  history; hotkey signature renamed `HMMK` → `OIDA`.
- MCP tools renamed to canonical `oida_*` with `hmm_*`/`aear_*`/`ear_*` aliases
  kept and the previously undeclared `aear_live_*` aliases declared.
- Environment/docs unification: project scripts export `OIDA_*` names (legacy
  still honored), release smoke reads the `*_SERVER_URL` chain and cleans its
  prompt record from the daemon data dir, README/SECURITY/shell docs are
  rewritten OIDA-first and match current behavior, CI/local checks run the
  suite once via pytest, and the server-integration tests isolate ambient
  `OIDA_*`/`HMM_*`/`AEAR_*` variables.

- **Renamed the project `hmm` → `oída`** (part of the Sonic Field "sonic
  evolution"). Python package `aear` → `oida`; primary CLI `oida`
  (`oida-daemon`), with `hmm`/`aear` kept as aliases. Environment variables use
  the `OIDA_*` prefix first, falling back to `HMM_*`/`AEAR_*`. macOS shell target
  renamed `HmmMacOS` → `OidaMacOS` (`oida-macos`). Status route `/hmm/status` →
  `/oida/status`; server identity, journal headers, and UI titles now read
  `oída`. Data directory prefers `~/Library/Application Support/oida`, falling
  back to a pre-rename `hmm` directory so existing listening memory is not
  orphaned. The Sonic Field bridge now resolves `~/workspace/sonicfield`.

- Made `mac-mps` the default engine everywhere (daemon, macOS shell, env
  examples); the daemon prewarms MOSS-Audio-4B-Instruct in the background and
  reports readiness at `/engine/status` (`HMM_MOSS_PREWARM=0` disables).
- Added the deterministic **signal listener** (`oida/signal_listener.py`):
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
- Added Sonic Field bridge (`oida/sonicfield.py`, `/sonicfield/*`): after a
  listen, "Explore in the wiki" searches the wiki/lexicon, topics, journal,
  93k-item library, paths, research, notes, and labs for related concepts,
  with taxonomy alias normalization and Finder reveal.
- Added `/events/stream` (SSE) so every surface mirrors one daemon state, and
  `/background/capture-request` so the web dashboard can ask the native shell
  to capture system audio.
- Default audio directory moved to `~/Documents/oida/audio`
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
  with macOS default `~/Library/Application Support/oida`.
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
