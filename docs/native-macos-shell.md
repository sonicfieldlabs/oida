# Native macOS Shell

`apps/macos` contains the native app shell for `oida`. It is a SwiftPM macOS
app that wraps the localhost daemon instead of replacing it: the daemon stays
the single source of truth and every surface renders the same state.

## Shape

```text
apps/macos/
  Package.swift
  Sources/OidaMacOS/
    App/        SwiftUI entry point and AppKit launch delegate
    Models/     Codable daemon response models (explicit snake_case keys)
    Services/   HTTP daemon client, shared shell store, daemon supervisor,
                mic + system-audio taps, global hotkey bridge, launch-at-login
    Support/    Floating listener panel, behind-window blur, repo locator, formatting
    Views/      Control center (embedded dashboard), menu bar extra, settings,
                floating listener (result box + small waveform + hover controls)
  script/build_and_run.sh
```

Build and launch:

```bash
apps/macos/script/build_and_run.sh
```

The script builds with SwiftPM, stages `apps/macos/dist/oida.app`, and launches
the bundle. `package_unsigned.sh` writes a local development archive;
`docs/macos-signing-notarization.md` covers Developer ID signing.

## One Daemon, One Dashboard

The control center window embeds the daemon's own dashboard
(`http://127.0.0.1:8765`) in a WKWebView, so the mac app and the browser render
the exact same page and cannot diverge. A slim native strip above it carries
what only the shell can do: supervise the daemon, reload, open in browser, and
toggle the floating listener. The WKWebView grants microphone capture natively
(macOS permission still applies) and routes `target="_blank"` links to the
default browser.

## Supervision

If no daemon is reachable at launch, the shell locates the repository root and
starts one:

```bash
uv run oida --profile mac-mps --host 127.0.0.1 --port 8765
```

Common development paths are appended to `PATH` so a GUI-launched app can find
`uv`, Homebrew dylib paths are exported, and `OIDA_DATA_DIR`/`OIDA_AUDIO_DIR`
are defaulted when unset. The shell stops only processes it started itself —
on explicit Stop Managed Daemon and on app quit (the managed daemon's
stdout/stderr are pipes into the app, so an orphan would die on its next log
write anyway). A daemon already running outside the shell is observed over
HTTP and never owned. If `OIDA_AUTH_TOKEN` (legacy `HMM_`/`AEAR_`) is set, the
daemon client sends it as a bearer token.

## The Floating Listener

The floating listener is deliberately not a window: a borderless, transparent,
non-activating `NSPanel` whose visible body is the listening-result box itself.
It floats over every Space (`.canJoinAllSpaces`, `.fullScreenAuxiliary`), never
steals focus from the app being listened to, remembers its position, keeps
itself on-screen, and is dragged by grabbing the box.

The box (a frosted, behind-window-blur surface) is always shown and holds the
latest reading: title, scrollable short summary, and similar-trace count. A
footer strip carries a state dot, the status word, and a small reactive
waveform tucked into the bottom-right corner. The waveform (a lightweight
SwiftUI `Canvas`, `MiniWaveformView`) follows the live signal — the native
system-output tap's bands, the daemon's live-session signal, or the mic level —
and only animates while something is being heard, freezing quiet at rest.

Every control floats just outside the box and fades in on hover, keeping the
layout stable (opacity only, so the box never jumps):

- top-left: the listening mode (preset) menu;
- top-right: close (×);
- below the box: the source switch (System / Mic / File), the Listen/Stop
  button, and the control-center button.

The box and its type are a step larger than the prior card. Machines need no
Metal support; the visualization is pure SwiftUI.

## Hotkeys

Global hotkeys are registered at startup and editable in Settings:

- `⌃⌥L` — listen now (source-aware: system, mic, or file picker)
- `⌃⌥H` — show/hide the floating listener

Bindings use Carbon `RegisterEventHotKey` with the format
`control+option+l`. The same shortcuts work as app menu commands, alongside
`⌃⌥P` (pause/resume) and `⌃⌥D` (open dashboard in browser).

## Native System Audio

The shell owns system-output listening: the browser cannot hear macOS output,
so the dashboard's `System · Listen` files a capture request that the shell
claims and fulfills. The ScreenCaptureKit tap (route `display_mix`, current
process excluded) continuously feeds only in-memory meters (bands, RMS/peak,
sample rate, state) for the floating listener's waveform. Raw audio is written only when a capture is
analyzed: the last N seconds of the in-memory ring go to a temporary WAV under
the audio dir and through `/native/system-audio/analyze` with
`raw_audio_policy: temp`. `/native/system-audio/temp` reports temp files and
the retention policy (`delete_after_session` by default);
`/native/system-audio/cleanup` deletes them. Screen Recording permission is
required by macOS for the tap.

The mic tap mirrors the same ring-buffer design via AVAudioEngine, and File
listening opens a native picker whose selection goes straight to
`/listen-event`.

## Daemon Contract

The shell calls: `/health`, `/background/status`, `/background/capture`,
`/background/capture-request/claim`, `/background/history/*` (export, pin,
batch-pin, archive, clear), `/background/pause`, `/background/resume`,
`/listen-event`, `/listen-event/rerun`, `/conversation/ask`,
`/generation/prompt`, `/generation/history`, `/live/signal/{session_id}`,
`/akouo/skills`, and `/native/system-audio/*`. The daemon remains the source of
truth for live-session state, defaults, latest event, and bounded recent
history; derived history persists at the data-dir
`sessions/recent-results.json` without copying raw audio and skips incognito
events by default.

## Launch At Login

Settings exposes launch-at-login through `SMAppService.mainApp`. Local or
unsigned builds can show `Requires approval`, in which case macOS expects the
user to approve the item in System Settings. The app never enables it
automatically.

## Current Limits

- MOSS/AKOÚŌ analysis of native-tapped system output happens through explicit
  captures (Listen or capture requests), never continuously.
- The generative bridge is prompt-only in the shell; audio rendering belongs to
  optional external adapters.
- Quick capture through `/background/capture` requires an active live session
  started from the dashboard or API.
