# oída

`oida` is a local listening agent for machine ears. It listens to audio files,
the microphone, and the computer's own output, routes sound through MOSS-Audio
and AKOÚŌ listening paths, extracts measured signal features, and normalizes
everything into listening events that can be remembered, questioned, handed to
germ as akousmata, or explored in the Sonic Field wiki.

This project was previously named **AEAR**, then **hmm**; it is now **oída**.
The Python package and primary CLI are `oida`; `hmm` and `aear` remain as
backward-compatible command aliases, and every setting reads `OIDA_*` first
with `HMM_*`/`AEAR_*` honored as fallbacks. UI copy uses the accented display
name **oída**.

## What Is Implemented

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
  deep-link germ (`OIDA_GERM_URL`, default `http://127.0.0.1:5178/import`).
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
  and `akoe` helper CLIs, and an MCP server exposing `oida_*` tools (legacy
  `hmm_*`/`aear_*`/`ear_*` aliases kept).
- 113 unit tests that run without model weights.

## Quick Start

Prerequisites: Python 3.12+, `uv`, `ffmpeg` for non-WAV uploads or browser
recordings, and a sibling checkout of `earworm` (the `akousma` dependency is an
editable path source at `../earworm/packages/py-akousma`).

```bash
uv sync --extra dev
```

Run the daemon (MOSS-Audio on Apple Silicon is the default profile):

```bash
uv run oida --host 127.0.0.1 --port 8765   # add --profile stub for a model-free dev run
```

Open the dashboard at `http://127.0.0.1:8765`.

Generate a normalized listening event:

```bash
curl -s http://127.0.0.1:8765/listen-event \
  -H 'content-type: application/json' \
  -d '{"path":"/path/to/clip.wav","route_preset":"basic"}'
```

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
- CI (`.github/workflows/ci.yml`) runs pytest, compileall, a JS syntax check,
  the Swift build (including strict concurrency), packaging, and the stub
  daemon release smoke. The `akousma` path dependency requires `earworm`
  checked out as a sibling.
