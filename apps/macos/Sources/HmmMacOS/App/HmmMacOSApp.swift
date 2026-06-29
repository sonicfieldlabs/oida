import AppKit
import SwiftUI

@main
struct HmmMacOSApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = ShellStore()

    var body: some Scene {
        WindowGroup("hmm", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 720, minHeight: 520)
                .task {
                    store.registerConfiguredHotkeyIfNeeded()
                    store.startPolling()
                    await store.refresh()
                }
        }
        .commands {
            CommandMenu("hmm") {
                Button("Capture Buffer") {
                    Task { await store.capture() }
                }
                .keyboardShortcut("l", modifiers: [.command, .option])

                Button(store.isPaused ? "Resume Listening" : "Pause Listening") {
                    Task { await store.togglePause() }
                }
                .keyboardShortcut("p", modifiers: [.command, .option])

                Button("Open Dashboard") {
                    store.openDashboard()
                }
                .keyboardShortcut("d", modifiers: [.command, .option])
            }
        }

        Window("Spectral Listener", id: "floating-agent") {
            FloatingSpectralView()
                .environmentObject(store)
                .frame(width: 320, height: 180)
        }
        .windowResizability(.contentSize)
        .defaultSize(width: 320, height: 180)

        Settings {
            SettingsView()
                .environmentObject(store)
                .frame(width: 460)
                .padding()
        }

        MenuBarExtra("hmm", systemImage: "waveform.path") {
            MenuBarView()
                .environmentObject(store)
        }
    }
}
