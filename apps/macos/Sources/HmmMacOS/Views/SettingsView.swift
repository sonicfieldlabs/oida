import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: ShellStore

    var body: some View {
        Form {
            Section("Daemon") {
                TextField("Base URL", text: $store.daemonBaseURL)
                    .textFieldStyle(.roundedBorder)

                HStack {
                    Text(store.daemonOnline ? "Online" : "Offline")
                        .foregroundStyle(store.daemonOnline ? .green : .orange)
                    Spacer()
                    Button("Refresh") {
                        Task { await store.refresh() }
                    }
                }
            }

            Section("Global Hotkey") {
                TextField("control+option+h", text: $store.shellHotkey)
                    .textFieldStyle(.roundedBorder)

                HStack {
                    Text(store.hotkeyStatus)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Register") {
                        store.registerCaptureHotkey()
                    }
                }

                Text("No default global shortcut is installed. Use a modified key such as control+option+h, then register it.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Launch") {
                HStack {
                    Text("Launch at login")
                    Spacer()
                    Text(store.launchAtLoginStatus)
                        .foregroundStyle(.secondary)
                }

                HStack {
                    Button("Enable") {
                        store.setLaunchAtLogin(true)
                    }
                    Button("Disable") {
                        store.setLaunchAtLogin(false)
                    }
                }

                Text("Registration uses macOS ServiceManagement for the main app bundle. Some builds may require System Settings approval.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Shell Limits") {
                Label("Native system-output capture uses ScreenCaptureKit and needs Screen Recording permission.", systemImage: "speaker.wave.2")
                Label("Quick capture requires an active daemon live session.", systemImage: "waveform")
            }
            .foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
    }
}
