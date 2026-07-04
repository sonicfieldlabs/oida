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
                    Text(store.engineLabel)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Refresh") {
                        Task { await store.refresh() }
                    }
                }
            }

            Section("Global Hotkeys") {
                LabeledContent("Listen (capture system audio)") {
                    TextField("control+option+l", text: $store.listenHotkey)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 180)
                }
                LabeledContent("Show/hide floating listener") {
                    TextField("control+option+h", text: $store.toggleHotkey)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 180)
                }
                HStack {
                    Text(store.hotkeyStatus)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Apply") {
                        store.registerHotkeys()
                    }
                }
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
            }

            Section {
                Label("System-output capture uses ScreenCaptureKit and needs Screen Recording permission.", systemImage: "speaker.wave.2")
                    .font(.caption)
            }
            .foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
    }
}
