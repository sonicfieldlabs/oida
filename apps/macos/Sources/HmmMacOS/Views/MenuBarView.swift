import AppKit
import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject private var store: ShellStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(store.statusLabel, systemImage: statusIcon)

            if let session = store.background?.state.activeLiveSessionId {
                Text(shortLabel(session, maxLength: 24))
                    .foregroundStyle(.secondary)
            } else {
                Text("No live buffer")
                    .foregroundStyle(.secondary)
            }

            Divider()

            Button("Start Daemon") {
                Task { await store.startDaemon() }
            }
            .disabled(store.daemonOnline || store.isStartingDaemon)

            Button("Stop Managed Daemon") {
                Task { await store.stopManagedDaemon() }
            }
            .disabled(!store.managedDaemonRunning)

            Divider()

            Button("Start System Tap") {
                Task { await store.startNativeSystemAudioTap() }
            }
            .disabled(store.nativeSystemAudioActive)

            Button("Stop System Tap") {
                Task { await store.stopNativeSystemAudioTap() }
            }
            .disabled(!store.nativeSystemAudioActive)

            Button("Analyze System Audio") {
                Task { await store.analyzeNativeSystemAudio() }
            }
            .disabled(!store.nativeSystemAudioActive || store.isAnalyzingNativeSystemAudio)

            Button("Clean Temp Audio") {
                Task { await store.cleanupNativeSystemAudioTemp() }
            }
            .disabled(store.nativeSystemAudioTempFileCount == 0 || store.isCleaningNativeSystemAudioTemp)

            Divider()

            Button("Capture Buffer") {
                Task { await store.capture() }
            }
            .disabled(store.isPaused || !store.hasActiveLiveSession || store.isCapturing)

            Button("Run Signal Route") {
                Task { await store.rerunLatestEvent(routePreset: "signal") }
            }
            .disabled(!store.canRerunLatestEvent || store.isRerunningRoute)

            Button("Run Environment") {
                Task { await store.rerunLatestEvent(routePreset: "environment") }
            }
            .disabled(!store.canRerunLatestEvent || store.isRerunningRoute)

            Button(store.isPaused ? "Resume Listening" : "Pause Listening") {
                Task { await store.togglePause() }
            }
            .disabled(!store.daemonOnline)

            Button("Floating Listener") {
                openWindow(id: "floating-agent")
            }

            Button("Open Shell") {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }

            Button("Open Dashboard") {
                store.openDashboard()
            }

            Divider()

            Button("Refresh") {
                Task { await store.refresh() }
            }

            Button("Quit") {
                NSApplication.shared.terminate(nil)
            }
        }
        .padding(.vertical, 4)
        .task {
            await store.refresh()
        }
    }

    private var statusIcon: String {
        if store.isPaused { return "pause.circle" }
        if store.nativeSystemAudioActive { return "speaker.wave.2.circle" }
        if !store.daemonOnline { return "exclamationmark.triangle" }
        if store.hasActiveLiveSession { return "waveform.circle" }
        return "waveform"
    }
}
