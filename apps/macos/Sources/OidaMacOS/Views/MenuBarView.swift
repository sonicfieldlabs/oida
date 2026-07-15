import AppKit
import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject private var store: ShellStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(store.statusLabel, systemImage: statusIcon)
            Text(store.engineLabel)
                .foregroundStyle(.secondary)

            Divider()

            Button(store.isListening ? "Listening…" : (store.isProcessing ? "Operating listening…" : "Listen now")) {
                store.showFloatingListener()
                Task { await store.listenNow() }
            }
            .disabled(store.isListenBusy || !store.daemonOnline)

            Button("Show Floating Listener") {
                store.showFloatingListener()
            }

            Button("Open Control Center") {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }

            Button("Open in Browser") {
                store.openDashboard()
            }

            Button("Settings…") {
                openWindow(id: "main")
                store.presentSettings()
                NSApp.activate(ignoringOtherApps: true)
            }

            Divider()

            Button(store.isPaused ? "Resume Listening" : "Pause Listening") {
                Task { await store.togglePause() }
            }
            .disabled(!store.daemonOnline)

            Button("Start Daemon") {
                Task { await store.startDaemon() }
            }
            .disabled(store.daemonOnline || store.isStartingDaemon)

            Button("Stop Managed Daemon") {
                Task { await store.stopManagedDaemon() }
            }
            .disabled(!store.managedDaemonRunning)

            Divider()

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
        if store.isListening { return "waveform.circle.fill" }
        if store.isProcessing { return "hourglass.circle" }
        if store.nativeSystemAudioActive { return "speaker.wave.2.circle" }
        if !store.daemonOnline { return "exclamationmark.triangle" }
        return "waveform"
    }
}
