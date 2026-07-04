# oída

`oida` is a local listening agent for machine ears. It listens to audio files,
browser microphone input, and captured live buffers, routes sound through
MOSS-Audio and AKOÚŌ listening paths, extracts measured signal features, and
normalizes results into listening events that can be saved as Akousmata memory.

This project was previously named **AEAR**, then **hmm**; it is now **oída**.
The Python package and primary CLI are `oida`; the `hmm` and `aear` commands
remain available as backward-compatible aliases, and `OIDA_*` environment
variables are read first with `HMM_*`/`AEAR_*` honored as fallbacks. UI copy
uses the accented display name **oída**.

## What Is Implemented

- FastAPI daemon with `/transcribe`, `/events`, `/caption`, `/speech`, `/music`,
  `/qa`, `/think`, `/report`, and `/listen-event`.
- `mac-mps`, `cuda-server`, and `stub` MOSS engine profiles.
- MOSS prompt recipes and provenance fields.
- Long-audio chunked MOSS inference with timestamp offsets and overlap event
  dedupe for `/report`.
- Python DSP module for peak/RMS/crest, BS.1770-style LUFS/LRA,
  silence/clipping, zero-crossing, centroid/rolloff/flatness, band energy,
  onsets/BPM candidate, and stereo correlation/width/balance.
- Canonical `AudioSegment`, `AudioSourceDescriptor`, and `ListeningEvent`
  contracts for `oida` workflows.
- Source registry for live input, file input, captured buffers, and explicit
  system-output fallback status.
- Live local ring buffer with quick "capture last N seconds" extraction.
- System-audio status and loopback-device workflow for the current browser
  dashboard phase.
- Background runtime status/config, pause/resume, and quick capture from the
  active live session.
- AKOÚŌ command routing plus schema-backed listening skill manifests, route
  presets, and dashboard skill selection.
- Local Akousmata memory store with remember/list/search/similarity/export/forget
  operations and a dashboard memory browser.
- Event-grounded conversation layer over structured listening events, with local
  evidence, hypotheses, uncertainty notes, and Akousmata memory context.
- Prompt-only generative audio bridge that derives editable creation prompts
  from listening events, keeps generator adapters optional, and can attach
  externally generated audio for re-listening comparison.
- Native macOS SwiftPM app shell with menu bar controls, quick capture,
  dashboard opening, local daemon supervision, optional global hotkey
  registration, user-controlled launch-at-login, live signal polling,
  ScreenCaptureKit system-output signal metering, and an OS-level floating
  spectral listener window.
- Static dashboard with upload, record, live capture, quick listening events,
  browser-based floating spectral agent, and JSON inspection.
- CLI and MCP server surfaces for local workflows.
- Unit tests that run without MOSS weights.

## Quick Start

Prerequisites: Python 3.12+, `uv`, and `ffmpeg` for non-WAV uploads or browser
recordings.

Install dependencies:

```bash
uv sync --extra dev
```

Run the daemon (MOSS-Audio on Apple Silicon is the default profile):

```bash
uv run oida --host 127.0.0.1 --port 8765  # add --profile stub for a model-free dev run
```

Open the dashboard:

```text
http://127.0.0.1:8765
```

Generate a normalized listening event:

```bash
uv run python -c "import numpy as np, soundfile as sf; t=np.arange(16000)/16000; sf.write('/tmp/oida-tone.wav',(0.15*np.sin(2*np.pi*440*t)).astype('float32'),16000)"
curl -s http://127.0.0.1:8765/listen-event \
  -H 'content-type: application/json' \
  -d '{"path":"/tmp/oida-tone.wav","route_preset":"basic"}'
```

Run a routed local session and write `sessions/<stamp>-<slug>/`:

```bash
uv run oida listen path/to/clip.wav --command /listen --server http://127.0.0.1:8765
```

Start and inspect a local live ring-buffer session:

```bash
uv run oida live --start
uv run oida live --status <session_id>
uv run oida live --stop <session_id>
```

Control the background runtime:

```bash
uv run oida background status
uv run oida background pause
uv run oida background resume
uv run oida background capture --seconds 10 --route-preset basic
```

The background runtime is daemon-side in this phase. It can keep state, track the
active live session, and quick-capture through the same listening pipeline. The
native macOS shell in `apps/macos` now controls this runtime through the same
API.

Run the native macOS shell:

```bash
apps/macos/script/build_and_run.sh
```

The shell stages `apps/macos/dist/oida.app`, adds a menu bar extra, opens the
dashboard, triggers background quick capture, and exposes an optional global
hotkey in Settings. If the daemon is offline, the shell starts one automatically with the
`mac-mps` profile. Launch at login is
available in Settings and is never enabled automatically.

The shell also includes an explicit native system-output signal tap. It uses
ScreenCaptureKit to compute in-memory RMS/peak/band meters for the floating
listener. Raw system audio is written only when the user chooses Analyze System
Audio, which creates a temporary local WAV and routes it through
`/native/system-audio/analyze` with `raw_audio_policy: temp`. Native temp
captures are listed at `/native/system-audio/temp` and can be removed through
`/native/system-audio/cleanup`; the macOS shell exposes this as Clean Temp.
Native source-route profiles are exposed at `/native/system-audio/routes`; the
current route is `display_mix`, meaning the selected display's system mix with
the oida process excluded.

Raw browser uploads and live chunks are stored under the configured oida data
directory, not the source checkout. Inspect them with `/raw-audio/status` and
delete them with `/raw-audio/wipe`; the dashboard exposes this as Wipe raw
audio. Recordings written by older builds into the checkout's `uploads/` are
reported under the status `legacy_*` fields and deleted when the wipe request
sets `include_legacy` (the dashboard button does).

Package a local unsigned app archive:

```bash
apps/macos/script/package_unsigned.sh
```

Run the local release-readiness check after starting the daemon:

```bash
scripts/run_local_checks.sh release
```

The release check validates tests, dashboard JavaScript syntax, native app
launch, unsigned package structure, daemon endpoints, and the prompt-only
generation bridge. See `docs/release-readiness.md`.

## AKOÚŌ Skills

The AKOÚŌ skill registry is exposed at `/akouo/skills`, with JSON schemas at
`/akouo/schema`. The dashboard skill manager loads that manifest dynamically and
lets a preset route one segment through a custom enabled skill chain.
Existing listening events can be rerun through a different preset with
`/listen-event/rerun`, which reuses the event segment path instead of capturing
new audio. Rerun responses include a conservative route comparison over route
ids, summary changes, warnings, deterministic DSP deltas, and applied comparison
filters. The daemon also keeps a bounded recent-result list at
`/background/history`, persists derived event JSON under the configured oida data
directory, and excludes incognito events from that durable history by default.
Pinned recent results are persisted separately from the rolling recent list, and
`/background/history/export`, `/background/history/pin`,
`/background/history/batch-pin`, `/background/history/archive`, and
`/background/history/clear` expose derived-history export, pin/unpin, batch
pinning, archive-to-file, and explicit clear controls without copying raw audio.
The dashboard and native macOS shell expose route/source, rerunnable,
changed-only, DSP-threshold, pinned, clear, export, archive, and batch review
controls for quick restore and comparison review.

`/conversation/ask` answers questions about the current structured listening
event without running a new audio pass. The default provider is local and
derived-data only: it distinguishes known facts from hypotheses, includes
uncertainty notes, can use similar Akousmata traces as context, and keeps remote
model use opt-in.

`/generation/prompt` turns the current structured listening event into an
editable generation prompt without generating audio itself. `/generation/history`
returns local prompt records, and `/generation/relisten` can analyze an
externally generated audio file and compare it back to the source event.
MOSS-Audio remains the listening side of this bridge; audio rendering is delegated to
optional adapters.

Contributor notes for adding a skill are in
`docs/akouo-skills.md`.

## Akousmata Memory

Akousmata stores selected listening events as local JSON traces. It keeps derived
features, route summaries, tags, user notes, and explicit raw-audio policy. It
can search by text/tag/source/route/time and can compare traces with deterministic
DSP feature similarity when embeddings are not available.

Dashboard controls cover remember, forget, search, open, and export. CLI access:

```bash
uv run oida memory list
uv run oida memory search "machine hum"
uv run oida memory export
uv run oida memory forget <trace_id>
```

See `docs/akousmata-memory.md`.

## Floating Spectral Agent In This Phase

The dashboard now includes a browser-based floating spectral agent. It is a
fixed overlay inside the dashboard, not a native always-on-top desktop window.
It can be shown or hidden, resized, pinned within the page, dragged, opened as a
quick-control popover, double-clicked for background quick capture, and used as
a drop target for audio files. Dropped files are uploaded and immediately routed
through the listening-event pipeline.

Live chunks update its signal bands from measured RMS/peak values. Idle rendering
uses CSS animation only, and reduced-motion can be enabled from the dashboard.

The macOS shell also includes a native floating spectral listener window. It is
an OS-level SwiftUI window with floating window level, quick capture, pause, and
dashboard controls. In this phase it visualizes live-chunk DSP snapshots from
the daemon, native system-output signal meters, and memory-match state.

## System Audio In This Phase

The current dashboard runs in the browser, so it cannot directly capture macOS
system output. `oida` supports system audio now through a visible loopback-device
workflow:

1. Install or enable a virtual loopback input such as BlackHole, Loopback, or
   Soundflower.
2. Route computer output to that device, or use a Multi-Output Device if you
   also need to hear it.
3. Open the dashboard, click `Devices`, choose `System audio`, and select the
   loopback input.
4. Start `Live`, then use capture-last-10-seconds.

The resulting captured buffer is labeled as `system_output` and routed through
the same listening event pipeline. Native WASAPI/PipeWire/CoreAudio adapters are
left for the desktop app-shell phase.

Run tests that do not require downloaded model weights:

```bash
uv run python -m unittest discover -s tests
uv run pytest
```

## Real MOSS-Audio Profile

The `mac-mps` adapter expects the official MOSS-Audio repository classes:

- `MossAudioModel`
- `MossAudioProcessor`
- `load_audio`

The local setup script sets these environment variables before starting `oida`:

```bash
export AEAR_MOSS_AUDIO_REPO="$PWD/MOSS-Audio"
export AEAR_MOSS_INSTRUCT_MODEL="$PWD/weights/MOSS-Audio-4B-Instruct"
export AEAR_MOSS_THINKING_MODEL="$PWD/weights/MOSS-Audio-4B-Thinking"
export AEAR_MOSS_RESIDENT=single
export DYLD_LIBRARY_PATH=/opt/homebrew/lib
```

Then run:

```bash
scripts/run_aear_mps.sh
```

The adapter passes `trust_remote_code=True`, constructs the processor with
`enable_time_marker=True`, loads 16 kHz mono audio through the official loader,
and sets `audio_input_mask = input_ids == processor.audio_token_id`.
`AEAR_MOSS_RESIDENT=single` hot-swaps Instruct and Thinking instead of keeping
both 4B models resident.

`oida` will not silently download model code or weights. If the local `weights/`
paths are absent, the `mac-mps` profile falls back to the stub engine unless
`AEAR_REQUIRE_MODEL=1` is set, and Hugging Face hub lookup is refused unless
`HMM_ALLOW_HF_HUB=1` or `AEAR_ALLOW_HF_HUB=1` is set. `HF_HUB_OFFLINE=1` always
keeps hub lookup disabled.

## CUDA/SGLang Profile

Start the official MOSS-Audio SGLang fork separately, then point `oida` at it:

```bash
export AEAR_SGLANG_BASE_URL=http://127.0.0.1:30000
uv run oida --profile cuda-server
```

Thinking budgets are forwarded through `custom_params.thinking_budget`.

## Privacy Defaults

`oida` is local-first. The daemon binds to `127.0.0.1` by default and does not
upload audio. If you bind to `0.0.0.0` or `::`, the daemon refuses to start
unless `HMM_AUTH_TOKEN` or `AEAR_AUTH_TOKEN` is set; clients then send
`Authorization: Bearer <token>`.

Persistent local data defaults to `~/Library/Application Support/oida` on macOS,
or `$XDG_DATA_HOME/oida` / `~/.local/share/oida` elsewhere. Override it with
`HMM_DATA_DIR` or `AEAR_DATA_DIR`.

Background-style buffers are ephemeral by product policy, but browser live
capture still writes temporary chunks to the data-dir `uploads/` because
`MediaRecorder` sends encoded files to the daemon. Quick live captures are
labeled with `raw_audio_policy: temp`. Native system-output temp captures use a
separate retention policy under `native_temp_audio_retention`, raw uploads use
`upload_audio_retention`, and `/raw-audio/wipe` removes all upload/live-buffer
raw audio on request.

Akousmata memory is explicit: events are saved only when the user calls
`/memory/remember` or uses the dashboard remember action. File-based listening
events keep an external path reference by default rather than copying raw audio
into memory.

## Native macOS Shell

See `docs/native-macos-shell.md` for the SwiftPM package layout, run script,
menu bar behavior, optional hotkey format, and current native-shell limits.
See `docs/macos-signing-notarization.md` for Developer ID signing,
notarization, and release packaging.

## Architecture Notes

See `docs/architecture/current-state.md` for the current architecture map and
phase-by-phase plan. The first native macOS app-shell phase is now present under
`apps/macos`; daemon supervision and live signal polling are implemented, while
MOSS/AKOÚŌ analysis of native-tapped system output is available through explicit
temporary captures.
