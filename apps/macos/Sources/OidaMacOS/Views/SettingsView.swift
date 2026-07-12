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
                        .foregroundStyle(store.daemonOnline ? Color.primary : Color.orange)
                    Text(store.engineLabel)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Refresh") {
                        Task { await store.refresh() }
                    }
                }
            }

            Section("Listening") {
                Picker("Direction", selection: $store.selectedDirection) {
                    Text("Past — what was just heard").tag("past")
                    Text("Future — record after the trigger").tag("future")
                }
                .pickerStyle(.radioGroup)
                Text("Past slices the ring buffer that is already listening (up to \(Int(MicTapManager.ringCapacitySeconds)) s); future records the window after you press Listen.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
