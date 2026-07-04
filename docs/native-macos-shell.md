# Native macOS Shell

`apps/macos` contains the first native app-shell target for `oida`. It is a
SwiftPM macOS app that wraps the existing localhost daemon instead of replacing
it.

## Shape

```text
apps/macos/
  Package.swift
  Sources/OidaMacOS/
    App/        SwiftUI entry point and AppKit launch delegate
    Models/     Minimal Codable daemon response models
    Services/   HTTP daemon client, shared shell store, daemon supervisor,
                global hotkey bridge
    Support/    Formatting and narrow NSWindow bridge
    Views/      Main window, menu bar extra, settings, floating listener
  script/build_and_run.sh
```

The app assumes the daemon is already running, usually:

```bash
uv run oida --profile stub --host 127.0.0.1 --port 8765
```

Then build and launch the native shell:

```bash
apps/macos/script/build_and_run.sh
```

The script builds with SwiftPM, stages `apps/macos/dist/oida.app`, and launches
the app bundle with `/usr/bin/open -n`.

Create a local unsigned archive:

```bash
apps/macos/script/package_unsigned.sh
```

This writes `apps/macos/dist/oida-macos-unsigned.zip`. It is a local development
package, not a notarized distribution build.

Release-readiness validation is documented in `docs/release-readiness.md`. With
the daemon running, `scripts/run_local_checks.sh release` builds, verifies,
packages, and smoke-checks the daemon contract plus the unsigned archive.

## Implemented Shell Controls

- Regular macOS main window for daemon state, background runtime state, quick
  actions, latest result, and privacy status.
- Menu bar extra for status, capture buffer, pause/resume, open shell, open
  dashboard, floating listener, refresh, and quit.
- Native floating spectral listener window with floating window level.
- Settings window for daemon URL and optional global hotkey binding.
- Global hotkey registration scaffold using Carbon `RegisterEventHotKey`.
- Daemon supervisor controls that can start a local `uv run oida --profile stub`
  process when the daemon is offline, then stop only that managed process.
- Live signal polling from the daemon's `/live/signal/{session_id}` endpoint.
- User-controlled launch-at-login registration through
  `SMAppService.mainApp`.
- Native system-output signal tap using ScreenCaptureKit. It computes live
  RMS/peak/band meters in memory and does not store raw audio.
- User-initiated native system-output analysis. The shell writes the last
  configured seconds to a temporary local WAV and calls
  `/native/system-audio/analyze`.

No global shortcut is installed by default. A user can enter a modified binding
such as `control+option+h` in Settings and register it. The shortcut triggers
background quick capture through `/background/capture`.

## Daemon Contract

The shell uses these daemon endpoints:

- `GET /health`
- `GET /background/status`
- `GET /background/history`
- `GET /background/history/export`
- `POST /background/history/pin`
- `POST /background/history/batch-pin`
- `POST /background/history/archive`
- `POST /background/history/clear`
- `POST /background/pause`
- `POST /background/resume`
- `POST /background/capture`
- `POST /listen-event/rerun`
- `POST /conversation/ask`
- `POST /generation/prompt`
- `GET /generation/history`
- `GET /live/signal/{session_id}`

The daemon remains the source of truth for live-session state, default capture
duration, route preset, incognito status, memory policy, latest event, and
bounded recent-result history. Recent history is derived event JSON persisted at
the configured data-dir `sessions/recent-results.json`; it does not copy raw
audio and skips incognito events by default. Pinned recent results are kept in a
separate bounded list, and history export returns derived JSON that the shell
copies to the macOS clipboard. History archive writes a timestamped derived JSON
file under the daemon's archive directory and copies that path to the clipboard.
Event conversation uses `/conversation/ask`; the shell sends the current
structured listening event and receives a derived-data answer with known facts,
hypotheses, evidence, uncertainty notes, and optional Akousmata memory context.
The generative bridge uses `/generation/prompt` to derive or save an edited
prompt from the current event, and `/generation/history` to review recent prompt
records. The shell does not generate audio directly.

## Supervision

The shell can start the daemon for local development by locating the repository
root and running:

```bash
uv run oida --profile stub --host 127.0.0.1 --port 8765
```

It appends common development paths to `PATH` so a GUI-launched app can still
find `uv`. The shell only stops a process it started itself. If the daemon is
already running outside the shell, the shell observes and controls it through
HTTP but does not own that process.

## Launch At Login

Launch at login is available in Settings. The shell uses
`SMAppService.mainApp.register()` and `unregister()` for the main app bundle.
Some local or unsigned builds can show `Requires approval`; in that case macOS
expects the user to approve the item in System Settings.

The app never enables launch-at-login automatically.

## Native System Audio Tap

The shell can start an explicit system-output signal tap from the main window,
menu bar, or floating listener. The tap uses ScreenCaptureKit audio capture and
may trigger macOS Screen Recording permission prompts. It excludes the current
process audio where the OS supports that option.

The tap continuously feeds only visual and status meters:

- normalized bands for the floating spectral listener,
- RMS and peak meter values,
- sample rate and channel count,
- capture/error state.

When the user chooses Analyze System Audio, the shell writes a bounded mono WAV
from its in-memory ring to the configured data-dir `uploads/` and sends that
local path to `/native/system-audio/analyze`. The resulting listening event uses:

- source type: `system_output`
- privacy mode: `ephemeral`
- raw audio policy: `temp`
- adapter metadata: `macos-screencapturekit-system-audio`
- source route metadata: `native-display-mix` / `display_mix`

`/native/system-audio/routes` exposes the route schema used by the native shell.
The current route is a display system mix with current-process audio excluded.
That route describes the capture filter only; it does not prove which app or
object produced a sound, and MOSS-Audio still receives 16 kHz mono.

The temporary WAV is not stored in Akousmata memory unless the user explicitly
remembers the listening event.

Native temp captures have an explicit cleanup surface:

- `/native/system-audio/temp` reports matching temp files, total bytes, and the
  active `native_temp_audio_retention` policy.
- `/native/system-audio/cleanup` deletes only files matching the native
  system-output capture pattern.
- The shell exposes cleanup as Clean Temp in the main window, menu bar, and
  floating listener.

The default policy is `delete_after_session` with bounded count pruning. It does
not delete arbitrary uploads and does not copy temp WAVs into Akousmata memory.

Route reruns use `/listen-event/rerun`. The shell sends the latest listening
event back to the daemon with a new route preset, so the same temp segment can be
read through Signal, Environment, Music, Speech, or Memory without recording a
new buffer. The daemon returns a route comparison that the Quick Result panel
summarizes with route changes, summary shift, warning changes, DSP deltas,
change flags, and applied filter metadata. The same panel can restore recent
results from the daemon's bounded durable history and filter that strip by route,
source type, or rerunnable status. It can also pin/unpin current or historical
results, enter a compact review mode for selecting shown results, batch
pin/unpin selected results, archive selected results, clear the rolling recent
list while preserving pinned results, clear all history, export the derived
history payload, ask event-grounded follow-up questions, and derive or edit a
generation prompt from the active listening event.

## Current Limits

- Quick capture requires an active live session started from the dashboard or
  API.
- MOSS/AKOÚŌ analysis of native-tapped system output requires explicit user
  action through Analyze System Audio.
- The generative bridge is prompt-only in the shell. Audio rendering belongs to
  an optional external adapter, and generated files are only re-analyzed when the
  user supplies a local path to the daemon.
- The floating listener visualizes daemon live-chunk DSP snapshots. It does not
  persist raw audio from the native system-output tap outside explicit temp
  analysis captures.

These limits keep the native phase auditable while proving supervision, menu bar
control, quick capture, native system-output signal metering, and a bounded
raw-audio analysis bridge.
