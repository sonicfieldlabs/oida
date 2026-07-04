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
                .frame(minWidth: 760, minHeight: 560)
                .task {
                    await store.bootstrap()
                }
        }
        .commands {
            CommandMenu("hmm") {
                Button(store.isListening ? "Listening…" : "Listen Now") {
                    Task { await store.listenNow() }
                }
                .keyboardShortcut("l", modifiers: [.command, .option])
                .disabled(store.isListening)

                Button("Show/Hide Floating Listener") {
                    store.toggleFloatingListener()
                }
                .keyboardShortcut("h", modifiers: [.command, .option])

                Button(store.isPaused ? "Resume Listening" : "Pause Listening") {
                    Task { await store.togglePause() }
                }
                .keyboardShortcut("p", modifiers: [.command, .option])

                Button("Open Dashboard in Browser") {
                    store.openDashboard()
                }
                .keyboardShortcut("d", modifiers: [.command, .option])
            }
        }

        Settings {
            SettingsView()
                .environmentObject(store)
                .frame(width: 480)
                .padding()
        }

        MenuBarExtra("hmm", systemImage: "waveform.path") {
            MenuBarView()
                .environmentObject(store)
        }
    }
}
