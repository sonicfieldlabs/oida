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

            Button(store.isListening ? "Listening…" : "Listen now") {
                Task { await store.listenNow() }
            }
            .disabled(store.isListening || !store.daemonOnline)
            .keyboardShortcut("l", modifiers: [.command, .option])

            Button("Show/Hide Floating Listener") {
                store.toggleFloatingListener()
            }

            Button("Open Control Center") {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }

            Button("Open in Browser") {
                store.openDashboard()
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
        if store.nativeSystemAudioActive { return "speaker.wave.2.circle" }
        if !store.daemonOnline { return "exclamationmark.triangle" }
        return "waveform"
    }
}
