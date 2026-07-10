# oída

`oida` is the unified local agentic listening stack: the AKOÚŌ listening
harness, Earworm provenance and memory protocol, and Akousmata listening
library behind one agent, CLI, gateway, and install. It can listen through its
own optional local engine (including MOSS-Audio plus deterministic DSP), or it
can harness the audio perception already produced by Hermes, Codex, Claude, or
another audio-input-capable host. Both paths produce the same accountable
AKOÚŌ claims, Earworm session context, and optional durable Akousmata memory.

This project was previously named **AEAR**, then **hmm**; it is now **oída**.
The Python package and primary CLI are `oida`; `hmm` and `aear` remain as
backward-compatible command aliases, and every setting reads `OIDA_*` first
with `HMM_*`/`AEAR_*` honored as fallbacks. UI copy uses the accented display
name **oída**.

## What Is Implemented

- **Unified gateway contract** (`oida/gateway/v0.2`) with two honest perception
  paths: Oída-owned audio through `POST /gateway/listen`, and host-owned model
  perception through `POST /gateway/harness`. `GET /gateway` advertises the
  installed AKOÚŌ, Earworm, and Akousmata contracts; `GET
  /gateway/schema/host-perception` publishes the host envelope.
- **One lifecycle**: `oida start` ensures a singleton background gateway,
  `oida agent` starts it and opens the listening agent, and every stdio MCP
  adapter can ensure/reuse that gateway itself. The same process serves the
  dashboard, REST API, streamable HTTP MCP at `/mcp`, and the complete
  Akousmata navigator at `/library/`.
- **Local host integrations** installed by `oida integrate`: a native Hermes
  plugin, Codex and Claude plugins, and a mobile-responsive private private-network
  surface. Their generated MCP configs are pinned to the active Oída runtime,
  so they do not depend on shell `PATH` or require a second app. Host adapters
  start the gateway without prewarming MOSS; the host-perception path stays
  lightweight until Oída-owned listening actually needs the local model.
- FastAPI daemon (default `127.0.0.1:8765`) with task endpoints
  (`/transcribe`, `/events`, `/caption`, `/speech`, `/music`, `/qa`, `/think`,
  `/report`), the listening-event pipeline (`/listen-event`,
  `/listen-event/rerun`), background runtime, SSE state stream, memory,
  conversation, generation, and bridge routes. `GET /api` lists everything.
- Engine profiles: **`mac-mps`** (default; MOSS-Audio-4B on Apple Silicon,
  background prewarm, hot-swappable Instruct/Thinking), **`cuda-server`**
  (SGLang endpoint), and **`stub`** (no model; DSP still listens). Long audio
  is chunked at 45 s per mac-mps pass with overlap dedupe and a
  decode-degeneracy guard that discards token-soup output.
- Deterministic **signal listener**: DSP-only classification (silence /
  speech-like / music-like / tonal / percussive / noise / ambient) with honest
  captions — without a model the evidence level stays `measured_signal`.
- Python DSP: peak/RMS/crest, BS.1770-style LUFS/LRA, silence/clipping,
  zero-crossing, centroid/rolloff/flatness, band energy, onsets/BPM candidate,
  stereo correlation/width/balance. One inspection per file per listen (results
  are memoized on path+mtime).
- **Route presets** scope the MOSS passes: Basic (one caption pass), Signal
  (DSP-only, instant), Field, Music, Voice, Recall (read-only memory
  comparison), Remember (memory comparison + registration into the shared
  akousmata, AKOÚŌ `/remember`), and Deep (the full report). Preset ids follow
  AKOÚŌ v0.6's portable preset vocabulary (pre-v0.6 ids `environment`/`speech`/
  `memory` still resolve as aliases). Presets come from the AKOÚŌ skill
  registry (`/akouo/skills`) and the dashboard skill manager can deviate per
  listen.
- **Akousmata memory** (private traces): remember/list/search/similar/export/
  forget over local JSON traces with deterministic DSP similarity. This is
  oída's private store — distinct from the shared akousmata below.
- **germ handoff** (shared akousmata store): after a listen, three actions —
  *Sound*, *Prompt*, *Lineage* — persist the listen as an **akousma** in the
  shared store (`~/workspace/akousmata`, via earworm's `py-akousma`) and
  deep-link germ's `/import` route (`OIDA_GERM_URL`, default `http://127.0.0.1:5178`).
  Opt-in song identification (`OIDA_SONGID=1`, ShazamIO) enriches the record's
  `extensions.songid`.
- **Sonic Field bridge**: "Explore in the wiki" searches the wiki, topics,
  journal, 93k-item library, paths, research, notes, and labs for related
  concepts, with taxonomy alias normalization and Finder reveal.
- Event-grounded **conversation** (`/conversation/ask`): local, derived-data
  answers that separate known facts from hypotheses; remote models stay opt-in.
- Prompt-only **generation bridge** (`/generation/*`): derives editable
  creation prompts from listening events; audio rendering is delegated to
  optional adapters, and `relisten` compares generated audio back to source.
- **Web dashboard** served by the daemon at `/`: one Listen surface (System /
  Mic / File), presets and skill manager, claims rendered by AKOÚŌ category,
  Ask / Remember / Explore / JSON / germ actions, recent history, and memory
  search. Every surface syncs over `/events/stream` (SSE).
- **Native macOS shell** (`apps/macos`, SwiftPM): menu bar extra, a control
  center that embeds the same daemon-served dashboard (WKWebView — web and app
  cannot diverge), daemon supervision, ScreenCaptureKit system-output tap, and
  the **floating listener** — a borderless, transparent, always-on-top
  listening-result box with a small reactive waveform in its corner; its
  controls float just outside the box and appear on hover. Global hotkeys
  default to ⌃⌥L (listen) and ⌃⌥H (show/hide it).
- CLI (`oida listen/live/background/memory/chat/sweep/corpus-qa/bench`), `ear`
  and `akoe` helper CLIs, and an official MCP server exposing compact
  `oida_*` listening, harness, memory, and live tools (legacy aliases kept on
  the HTTP compatibility surface).
- A dependency-light test suite that runs without model weights.

## Quick Start

Prerequisites: Python 3.12+, `uv`, and `ffmpeg` for non-WAV uploads or browser
recordings. From this source workspace, one sync installs Oída together with
the canonical AKOÚŌ, Earworm/akousma, and Akousmata packages:

```bash
uv sync --extra dev --extra moss
```

Start the singleton gateway, then open the agent or library:

```bash
uv run oida start                         # add --profile stub for model-free use
uv run oida agent
uv run oida agent --library
```

`oida` or `oida serve` still runs the same system in the foreground. The local
dashboard is `http://127.0.0.1:8765`, the library is `/library/`, REST gateway
discovery is `/gateway`, and streamable HTTP MCP is `/mcp`.

Install the local adapters (each host can then start/reuse Oída automatically):

```bash
uv run oida integrate hermes
uv run oida integrate codex
uv run oida integrate claude
uv run oida integrate remote --serve     # private responsive UI via private-network
uv run oida doctor
```

No native iOS or cloud service is required: open the reported private-network URL on
the phone to use it as the microphone, speaker, screen, and remote control.

Generate a normalized listening event:

```bash
curl -s http://127.0.0.1:8765/listen-event \
  -H 'content-type: application/json' \
  -d '{"path":"/path/to/clip.wav","route_preset":"basic"}'
```

An audio-capable host can keep perception in its own model and send a declared
report to `/gateway/harness`; see [the gateway contract](docs/gateway-contract.md)
for the schema and examples. MOSS is therefore an optimized local backend, not
a requirement for the harness path.

Routed local session, live ring buffer, and background runtime:

```bash
uv run oida listen path/to/clip.wav --command /listen --server http://127.0.0.1:8765
uv run oida live --start && uv run oida live --status <session_id>
uv run oida background status|pause|resume|capture --seconds 10
uv run oida memory list|search "machine hum"|export|forget <trace_id>
```

Run the native macOS shell (builds, stages `apps/macos/dist/oida.app`, launches):

```bash
apps/macos/script/build_and_run.sh
```

If the daemon is offline the shell starts one automatically with the `mac-mps`
profile and stops that managed process again when the app quits (externally
started daemons are never touched). Launch-at-login is available in Settings
and never enabled automatically.

Run the tests and the local release check:

```bash
uv run pytest -q                     # pytest collects the unittest suite
scripts/run_local_checks.sh          # tests + compileall + JS syntax
scripts/run_local_checks.sh release  # + app build, packaging, daemon smoke
```

## The Floating Listener

The macOS shell's floating listener is deliberately **not a window**: a
borderless, transparent, non-activating panel whose visible body is the
listening-result box itself. The latest reading (title and summary) is always
shown; a small reactive waveform sits in the box's corner, driven by the live
signal (system tap or mic) and animating only while something is being heard.

Every control floats just outside the box and fades in on hover: the listening
mode (preset) at the top-left, close at the top-right, and the source switch
(System / Mic / File), Listen/Stop, and control-center buttons in a row below.
Grab the box to drag it. It floats over every Space, never steals focus, and
its controls stay out of the way until you reach for them.

The dashboard's system capture asks the shell for help: the browser cannot hear
macOS output, so `System · Listen` files a capture request that the native
shell claims and fulfills through ScreenCaptureKit (route `display_mix`, the
current process excluded). Raw system audio is written only when a capture is
analyzed, as a temporary WAV under the audio dir, and is cleaned by the
`native_temp_audio_retention` policy (`delete_after_session` by default).

## Engine Profiles

The `mac-mps` adapter expects the official MOSS-Audio repository and local
weights. `scripts/run_oida_mps.sh` sets the environment and starts the daemon:

```bash
export OIDA_MOSS_AUDIO_REPO="$PWD/MOSS-Audio"
export OIDA_MOSS_INSTRUCT_MODEL="$PWD/weights/MOSS-Audio-4B-Instruct"
export OIDA_MOSS_THINKING_MODEL="$PWD/weights/MOSS-Audio-4B-Thinking"
export OIDA_MOSS_RESIDENT=single      # hot-swap Instruct/Thinking instead of keeping both resident
export DYLD_LIBRARY_PATH=/opt/homebrew/lib
scripts/run_oida_mps.sh
```

`oida` never silently downloads model code or weights. If the local `weights/`
paths are absent, `mac-mps` falls back to the stub engine unless
`OIDA_REQUIRE_MODEL=1`; Hugging Face hub lookup is refused unless
`OIDA_ALLOW_HF_HUB=1`, and `HF_HUB_OFFLINE=1` always wins. The daemon prewarms
the Instruct model in the background (`OIDA_MOSS_PREWARM=0` disables);
`/engine/status` reports readiness, and the dashboard's Engine fold can
reassign models and warm on demand.

For CUDA, start the official MOSS-Audio SGLang fork separately:

```bash
export OIDA_SGLANG_BASE_URL=http://127.0.0.1:30000
uv run oida --profile cuda-server
```

## Privacy Defaults

`oida` is local-first. The daemon binds to `127.0.0.1` by default, enforces
Host/Origin loopback guards, and does not upload audio. Wildcard binds refuse
to start without `OIDA_AUTH_TOKEN`; clients then send
`Authorization: Bearer <token>`.

Persistent data lives in `~/Library/Application Support/oida` on macOS
(`$XDG_DATA_HOME/oida` elsewhere; override with `OIDA_DATA_DIR`); a pre-rename
`hmm` directory is honored so existing memory is not orphaned. Audio captures
and uploads live in `~/Documents/oida/audio` (`OIDA_AUDIO_DIR`). Uploads are
capped at 1 GiB and normalized through ffmpeg. `/raw-audio/status` and
`/raw-audio/wipe` inspect and delete raw upload/live-buffer audio, including
pre-data-dir recordings in the checkout's `uploads/` via `include_legacy`.

Memory is explicit: events are saved only through `/memory/remember` or the
dashboard's Remember. Incognito events stay out of durable history. The shared
akousmata store is written only by the explicit germ handoff actions.

## Repository Notes

- `docs/native-macos-shell.md` — shell layout, supervision, hotkeys, listener.
- `docs/macos-signing-notarization.md` — Developer ID signing and packaging.
- `docs/akouo-skills.md` / `docs/akousmata-memory.md` — skills and memory.
- `docs/architecture/current-state.md` — architecture map and phase log
  (`PLAN.md` keeps the historical OIDA phases).
- `docs/release-readiness.md` — what `scripts/run_local_checks.sh release`
  validates.
- `docs/gateway-contract.md` — lifecycle, host-perception envelope, and local
  integration boundaries.
- `integrations/` — the bundled Hermes, Codex, Claude, and remote adapters.
- CI (`.github/workflows/ci.yml`) runs pytest, compileall, a JS syntax check,
  the Swift build (including strict concurrency), packaging, and the stub
  daemon release smoke. Development uses canonical sibling sources; the Oída
  distribution declares them as versioned dependencies so they are installed
  as one stack rather than copied into divergent forks.
