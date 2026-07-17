# Changelog

## 0.7.0 - 2026-07-17

- **Explicit listening identity**: Oída now creates an empty, local
  `LISTENING.md` in its data directory and exposes it in the shared desktop
  dashboard under Settings → Listening. Its bounded perspective is carried by
  interpretive perception and every model-backed grounded conversation while
  remaining subordinate to evidence, privacy, routes, and Covenants. DSP and
  exact transcription remain literal. The freeform document has an optional
  conceptual scaffold for position, conditions, intentions, attention,
  relations, open questions, voice, and possibilities.
- **Identity provenance across the stack**: normalized events, private
  Earworm context, shared akousma extensions, and conversation audits now
  preserve a content-free `oida/listening-identity/v0.1` revision block. The
  `oida/host-perception/v0.2` contract lets a hosted ear declare the revision
  it applied; missing and changed revisions remain visible without becoming
  evidence. Host integrations preflight the active Covenant before direct
  perception, so perspective cannot be mistaken for permission.
- **Release contract and editing safety**: the gateway advances to
  `oida/gateway/v0.3`, advertises the listening-identity contract explicitly,
  and exposes 14 structured MCP tools. Dashboard edits use revision-aware
  atomic saves, preserving a newer external or agent edit instead of silently
  overwriting it. Capability discovery reports only identity metadata; reading
  the text is an explicit action. The release is covered by 316 tests plus
  package, macOS, dependency, schema, and JavaScript release gates.
- **Covenant coverage closed around secondary hearings**: live/background
  analysis, route reruns, and generation re-listening now apply the active
  Covenant's input, content, output, and memory-retention gates before their
  identity-shaped event is emitted. A new perspective can never become a path
  around an existing refusal.

## 0.6.5 - 2026-07-16

- **Stricter API contracts**: JSON request bodies now reject coercions such as
  integer values for booleans, closing inconsistencies found through generated
  OpenAPI request testing. Malformed GERM handoffs are rejected at the request
  boundary instead of reaching integration processing.
- **Accurate service discovery**: `GET /api` derives its endpoint list from the
  routes actually mounted by the running service, including sessions,
  covenants, remote capture, and optional integrations without a stale manual
  inventory. The OpenAPI document now describes shared error responses and
  HTML surfaces accurately, while the dashboard and embedded Akousmata
  navigator share a working root favicon route.
- **Release and package hardening**: distribution metadata uses current SPDX
  and license-file declarations, source archives exclude property-test state,
  and CI checks lint, both browser entry points, and built-package metadata.
  Obsolete development dependencies and old GitHub Action runtimes were
  removed or updated.
- **Secure optional model runtime**: the embedded MOSS dependency set now uses
  the compatible PyTorch 2.10, TorchCodec 0.10, and Transformers 5.14 releases,
  removing every fixable finding from the all-extras dependency graph. The
  loader now requires Safetensors and preserves the official MOSS-Audio API
  across the patched Transformers runtime.

## 0.6.0 - 2026-07-14

- **Installable Python release chain**: the public distribution is now
  `sonicfield-oida` because the shorter PyPI name belongs to an unrelated
  project; imports and CLIs remain `oida`. A tag-gated Trusted Publishing
  workflow builds and isolates the canonical `akouo-contract`, `akousma`,
  `akousmata`, and Oída distributions without repository tokens. Publishing
  is enabled only after all four PyPI Trusted Publishers are configured.

- **Oída-owned reasoning layer**: event conversation now has a fixed prompt
  hierarchy, a whitelist-built evidence packet, and a strict response contract.
  Listening results remain immutable; answer blocks and hypotheses cite stable
  evidence refs, uncertainty stays explicit, and chain-of-thought is never part
  of the contract or stored audit data. Conversation records move to v0.2 with
  read compatibility for v0.1, one primary event anchor, and up to three
  explicitly selected comparison events.
- **Choose what each model does**: Reasoning settings assign fast perception,
  deep perception, transcription, music analysis, conversation, and targeted
  re-listening independently. The
  shared dashboard manages provider/model selection and conversation profiles
  for tone, depth, initiative, focus, language, and bounded custom instructions.
  Profile text cannot override evidence, covenant, privacy, or output rules.
- **Capability-aware audio model catalog**: embedded/local-host presets cover
  MOSS-Audio 8B, MOSS-Music 8B, MOSS Transcribe + Diarize, MiDashengLM,
  MiMo-Audio, Qwen3-Omni, Gemma 3n, and experimental Mellow. API presets cover
  Gemini 3.5 Flash, Alibaba Qwen Omni, NVIDIA Nemotron, and the OpenRouter free
  Nemotron route. Large Qwen/Nemotron targets are configuration-only and were
  not executed on this machine.
- **RAM-aware routing**: settings report physical RAM, estimated peak local
  model memory, residency mode, platform requirements, gated/experimental
  status, and explicit warnings when a selection exceeds the machine. Models
  are not downloaded or loaded to calculate this assessment.
- **Separate external-audio consent**: conversation packets still never contain
  audio. Cloud perception receives audio bytes only when its provider/model is
  assigned and enabled and the new default-off permission is on; incognito and
  raw-audio covenant rules still block it. Requests never expose a local path;
  larger NVIDIA inputs use a temporary NVCF asset deleted after the call.
  Targeted re-listening remains local.
- **Local, host, and explicit cloud providers**: deterministic local reasoning
  remains the no-model fallback; existing Ollama and OpenAI-compatible endpoints
  can be added without model downloads. Codex, Claude, Hermes, OpenClaw, and
  OpenCode can reason through their existing host authentication, while
  OpenRouter supports BYOK and a localhost PKCE flow. All host and network
  providers are disabled until the operator enables them.
- **Evidence stays smaller than the event**: reasoning packets contain only
  covenant-filtered derived evidence. Raw audio, local paths, source URIs,
  credentials, and arbitrary event fields are excluded. Transcript and memory
  content have separate, default-off permissions. Incognito forces local-only
  reasoning and prevents conversation persistence.
- **One disclosed local re-listen**: a model may request at most one focused
  covenant-compliant MOSS/audio pass per turn. Its observation is added as new
  derived evidence before one final reasoning pass; it never modifies the
  original listening event. Oída never downloads or pulls a local model to
  satisfy the request.
- **Predictable failure behavior**: malformed provider output gets one repair
  attempt with the same provider. A failure then returns a visible deterministic
  local answer; Oída does not silently send the packet to a different external
  service.
- **Host turn handoff**: `/conversation/prepare` and
  `/conversation/commit`, mirrored as `oida_prepare_turn` and
  `oida_commit_turn`, let an active host run the supplied system prompt and
  schema without recursively invoking its own CLI. `oida_ask` remains the
  daemon-managed path. OpenCode and OpenClaw join `oida integrate` and doctor.
- **Credential boundary**: provider secrets use the macOS Keychain or an
  available cross-platform keyring; an environment-only store is the fallback
  when secure persistence is unavailable. Secrets are excluded from settings,
  logs, URLs, and process arguments.

## 0.5.0 - 2026-07-13

- **Listening sessions**: one listening session now holds many results, and
  the session is daemon state — the dashboard, the floating listener, the
  hotkeys, MCP, and agent calls all file into the same place. `GET/POST
  /sessions`, activate/rename/archive/restore/delete, per-result rename and
  delete (raw audio is never touched by history deletion), and `POST
  /sessions/{id}/remember` to remember a whole session. Sessions persist with
  recent history and survive restarts; pre-session history appears as the
  legacy "Earlier listens" group with the same batch actions.
- **The session dashboard**: the center column is now a session feed (every
  result a card with inline rename, copy, and an action menu), the left rail
  groups history by session with Archive below, and tag chips filter the feed
  (`#music-id`, presets, session tags — combinable). "Related" memory entries
  open their trace inline.
- **Measured, drawn**: DSP results now carry a compact normalized log-frequency
  **spectrogram** (`features.spectrogram`, 144×64), rendered as a sonogram with
  a high-definition modal view plus a frequency-energy band chart and a metric
  grid (LUFS, peak, RMS, crest, flatness, centroid). Silence stays visually
  empty instead of normalizing the noise floor into a bright box.
- **Music ID, opt-in per listen**: a `song_id` flag on `/listen-event`,
  `/background/capture`, `/background/capture-request`, live capture, and the
  native analyze path runs ShazamIO recognition only in Music mode; matches
  land as `music_id` on the event (tag `music-id`) and lead the result summary.
  Under a covenant that withholds song identity the answer is attributed
  absence, never a lookup. `/health` reports provider availability.
- **Memory is the shared store**: Remember now writes the compatibility trace
  *and* the shared Akousmata record in one act (`/memory/remember`, session
  remember); the Memory rail lists the shared store with rename and forget
  (`PATCH`/`DELETE /akousmata/records/{id}` — forgetting never deletes audio),
  and each memory card links back to its session and result.
- **Rules in the footer**: the covenant layer is surfaced as a Rules popover —
  a toggle, a document picker, and a plain-text editor; the daemon keeps its
  covenant vocabulary and file format. Still empty by default.
- **Settings, consolidated**: appearance (light/dark, persisted, native shell
  follows), interface reset, capture-permission and open-in-browser actions,
  Engine, Reasoning, and Path — one modal. A collapsible activity console
  under the result box records phases, requests, and errors.
- **Native shell, chromed**: titlebar accessories (sidebar toggles, source
  popovers with buffer/direction/mic level, floating listener, settings) via a
  window-chrome bridge; a real listening lifecycle (capturing → processing →
  result/failed) drives the dashboard, menu bar, and floating listener through
  one state sync; the floating listener gains an editable title (renames the
  session result), share/copy actions, the concentric listening glyph, and
  session-scoped readings (a fresh launch no longer resurrects old history).
  The supervisor launches the daemon through `run_oida_mps.sh` (self-syncing,
  offline-tolerant), terminates its managed daemon on quit, and the mac-mps
  engine monitor no longer reports a stale "ready" after models unload; the
  shell requests a launch prewarm when it attaches to a cold daemon.
  ScreenCaptureKit access is preflighted with an actionable message, and the
  dev build is signed with a stable designated requirement so privacy consent
  survives rebuilds.
- **Directed capture everywhere**: capture requests carry direction, source,
  skills, and Music ID; the native analyze path records `capture` and enforces
  covenant gates (423 refusals, pass filtering, perception/claim redaction,
  memory-retention refusal) like every other listen surface. 176 tests.

## 0.4.0 - 2026-07-12

- **The sovereignty layer (spec v1.3 / AKOÚŌ v0.7)**: oída can now listen
  under a **listening covenant** — a small, human-written declaration of what
  this ear will not listen to, will release after hearing, will not reveal,
  will not retain, will blur, or will refuse at certain hours, and why.
  `oida/covenant.py` parses the easy text format (English and Spanish verbs;
  `## rules`, `## commitments`, `## because`; unknown rule lines become
  commitments — the bridge, not the cage) and enforces the executable subset
  at four gates: **input** (refuse sources, quiet hours, max window — a
  refusal answers 423 with the covenant and rule named), **content**
  (ignored classes have their perception passes never run and their report
  traces dropped), **output** (withhold transcript / speaker-identity /
  affect / location / song-identity / events / spectral-detail, or coarsen
  location to a declared radius), and **retention** (raw audio released,
  memory writes refused — reported, never silent).
- **Honest absence, on the record**: every hearing under a covenant carries
  a `covenant` block (identity, lineage, rules applied, withheld — counted
  and attributed, never described — and commitment count) on the event, in
  memory traces, and in the shared-store akousma; the akousmata navigator
  can filter "everything listened under this covenant".
- **Surfaces**: `GET/PUT /covenant`, `POST /covenant/activate`,
  `GET|DELETE /covenant/{name}`; a Covenant panel on the dashboard (list,
  activate, plain-text editor); the `oida_covenant` MCP tool (agents may
  propose covenants; activation stays the operator's act); a per-listen
  `covenant` pin on `/listen-event`. The remote ear reports "under
  <covenant>" and withholding counts on its result card. **Empty by
  default**: sovereignty is opted into, never imposed.
- Contracts: AKOÚŌ pin moves to `akouo/v0.7` (sovereign-listening mode +
  `/covenant` command mirrored in the harness registries), components to
  `earworm/v0.4` + `akousmata/v0.4`; gateway contract stays
  `oida/gateway/v0.2` (all additive). 163 tests (13 new).

## 0.3.0 - 2026-07-11

- **The remote ear (`/remote`)**: a phone-first capture surface served by the
  daemon itself. Wherever, whenever — the phone records (PCM in the page, WAV
  encoded on-device), attaches its GPS fix when granted, and posts to the new
  `POST /remote/listen`; the server runs the full listening pipeline, keeps
  the WAV, **writes the akousma (the sound + its listening file) into the
  shared store** with `location` and `capture`, and answers with the
  listening event rendered in the remote UI. Network publication remains
  operator-managed and uses the same host/origin guards as the dashboard.
- **Future / past listening (spec v1.2 `capture`)**: every listen surface can
  now declare its temporal direction. *Past* slices the ring buffer that was
  already listening when the trigger fired; *future* records the window after
  it. The floating listener gains a ⟲/⟳ direction pill (persisted), Settings
  gains the default, the native mic ring grows from 30 s to 120 s with real
  `bufferedSeconds` accounting, the remote ear implements both modes with an
  on-phone ring buffer that overwrites itself (nothing accumulates), and
  `/listen-event` accepts `capture_direction` / `capture_seconds` /
  `capture_trigger` — carried on the event, its segment metadata, memory
  traces, and akousmata records.
- **Geolocated listening (spec v1.2 `location`)**: `/listen-event`,
  `/gateway/listen`, the germ handoff, and `oida_listen` (MCP) accept an
  optional consent-scoped `location {lat, lon, accuracy_m, altitude_m, label,
  source}`; it rides the listening event and lands in the akousma, where the
  akousmata navigator's new listening map plots it.
- **MCP**: `oida_live` gains the `capture` action (slice the last N seconds
  from a live ring — the past direction made callable, with optional
  analysis); `oida_listen` gains `location`.
- **Contracts**: gateway manifest components move to `earworm/v0.3` and
  `akousmata/v0.3` (spec v1.2); the gateway contract itself stays
  `oida/gateway/v0.2` — every addition is optional and additive. New
  `remote_ear` transport advertised.
- 150 tests (5 new: capture/location validation and plumbing, the complete
  remote-ear flow into a temp shared store, bridge-level spec v1.2 blocks).

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
  install bundled Hermes, Codex, and Claude integrations. The remote capture
  page is served by the daemon without configuring network access. MCP
  processes ensure or reuse the gateway and pin the active Python runtime.
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
  orphaned. The Sonic Field bridge now resolves a configured or sibling checkout.

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
