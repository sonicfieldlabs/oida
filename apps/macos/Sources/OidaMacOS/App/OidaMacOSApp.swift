import AppKit
import SwiftUI

@main
struct OidaMacOSApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = ShellStore()

    var body: some Scene {
        WindowGroup("oída", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 760, minHeight: 560)
                .onAppear {
                    appDelegate.onWillTerminate = { store.shutdownManagedDaemon() }
                }
                .task {
                    await store.bootstrap()
                }
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandMenu("oída") {
                Button(store.isListening ? "Listening…" : (store.isProcessing ? "Operating listening…" : "Listen Now")) {
                    store.showFloatingListener()
                    Task { await store.listenNow() }
                }
                .disabled(store.isListenBusy)

                Button("Show Floating Listener") {
                    store.showFloatingListener()
                }

                Button(store.isPaused ? "Resume Listening" : "Pause Listening") {
                    Task { await store.togglePause() }
                }
                .keyboardShortcut("p", modifiers: [.command, .option])

                Button("Open Dashboard in Browser") {
                    store.openDashboard()
                }
                .keyboardShortcut("d", modifiers: [.command, .option])
            }

            CommandGroup(replacing: .appSettings) {
                Button("Settings…") {
                    store.presentSettings()
                }
                .keyboardShortcut(",", modifiers: .command)
            }
        }

        MenuBarExtra {
            MenuBarView()
                .environmentObject(store)
        } label: {
            Image(nsImage: AppLogoSymbol.image())
                .renderingMode(.template)
                .accessibilityLabel("oída")
        }
    }
}
