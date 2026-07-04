import SwiftUI

/// The floating listener: a small, quiet card. Pick a source and a listening
/// mode, listen (or stop), and read the full result — the panel stretches
/// vertically and the reading scrolls.
struct FloatingListenerView: View {
    @EnvironmentObject private var store: ShellStore

    private let sources: [(id: String, label: String, icon: String, hint: String)] = [
        ("system", "System", "speaker.wave.2", "Listen to what the computer is playing"),
        ("mic", "Mic", "mic", "Listen to the microphone"),
        ("file", "File", "folder", "Listen to an audio file"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            signalField
            reading
            actions
        }
        .padding(16)
        .frame(width: 320)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(nsColor: .windowBackgroundColor))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(Color.primary.opacity(0.08), lineWidth: 1)
                )
        )
        .preferredColorScheme(.light)
    }

    private var header: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(statusColor)
                .frame(width: 7, height: 7)
                .help(store.daemonOnline ? "Daemon online" : "Daemon offline")
            Text(store.floatingStatusText)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Spacer(minLength: 6)
            presetMenu
            sourcePicker
            Button {
                store.toggleFloatingListener()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.tertiary)
                    .frame(width: 18, height: 18)
            }
            .buttonStyle(.plain)
            .help("Hide the floating listener (\(store.toggleHotkey))")
        }
    }

    private var presetMenu: some View {
        Menu {
            ForEach(store.presets) { preset in
                Button {
                    store.selectedPreset = preset.id
                } label: {
                    if preset.id == store.selectedPreset {
                        Label(preset.name, systemImage: "checkmark")
                    } else {
                        Text(preset.name)
                    }
                }
                .help(preset.description ?? "")
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 10, weight: .medium))
                Text(currentPresetName)
                    .font(.system(size: 10.5, weight: .medium))
                    .lineLimit(1)
            }
            .padding(.horizontal, 7)
            .frame(height: 18)
            .background(
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .fill(Color.primary.opacity(0.06))
            )
            .foregroundStyle(.secondary)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(currentPresetDescription)
        .disabled(store.isListening)
    }

    private var currentPresetName: String {
        store.presets.first(where: { $0.id == store.selectedPreset })?.name ?? store.selectedPreset.capitalized
    }

    private var currentPresetDescription: String {
        let preset = store.presets.first(where: { $0.id == store.selectedPreset })
        return "Listening mode: \(preset?.name ?? store.selectedPreset). \(preset?.description ?? "")"
    }

    private var sourcePicker: some View {
        HStack(spacing: 2) {
            ForEach(sources, id: \.id) { source in
                Button {
                    store.selectedSource = source.id
                } label: {
                    Image(systemName: source.icon)
                        .font(.system(size: 10, weight: .medium))
                        .frame(width: 24, height: 18)
                        .background(
                            RoundedRectangle(cornerRadius: 5, style: .continuous)
                                .fill(store.selectedSource == source.id ? Color.primary : Color.primary.opacity(0.06))
                        )
                        .foregroundStyle(store.selectedSource == source.id ? Color(nsColor: .windowBackgroundColor) : .secondary)
                }
                .buttonStyle(.plain)
                .help(source.hint)
                .disabled(store.isListening)
            }
        }
    }

    private var signalField: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { timeline in
            Canvas { context, size in
                let phase = timeline.date.timeIntervalSinceReferenceDate
                let bands = store.selectedSource == "mic" && store.micLevel > 0 ? [] : store.signalBands
                let count = 26
                let gap: CGFloat = 3
                let barWidth = (size.width - gap * CGFloat(count - 1)) / CGFloat(count)
                let active = store.isListening || store.nativeSystemAudioActive || store.micLevel > 0.02
                for index in 0..<count {
                    let measured: Double
                    if store.selectedSource == "mic", store.micLevel > 0 {
                        let wobble = 0.5 + 0.5 * sin(phase * 4 + Double(index) * 0.8)
                        measured = max(0.06, min(1.0, store.micLevel * (0.55 + 0.65 * wobble)))
                    } else if bands.isEmpty {
                        let breath = active ? 0.35 : 0.12
                        measured = breath + 0.1 * sin(phase * 1.5 + Double(index) * 0.55)
                    } else {
                        let bandIndex = index * bands.count / count
                        measured = max(0.08, min(1.0, bands[min(bandIndex, bands.count - 1)]))
                    }
                    let height = max(2, size.height * CGFloat(measured))
                    let x = CGFloat(index) * (barWidth + gap)
                    let rect = CGRect(x: x, y: (size.height - height) / 2, width: barWidth, height: height)
                    let opacity = active ? 0.75 : 0.25
                    context.fill(
                        Path(roundedRect: rect, cornerRadius: barWidth / 2),
                        with: .color(.primary.opacity(opacity))
                    )
                }
            }
        }
        .frame(height: 30)
        .accessibilityLabel("Live signal")
    }

    private var reading: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 4) {
                Text(store.latestEvent?.aggregate?.title ?? "Nothing heard yet")
                    .font(.system(size: 13, weight: .semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Text(store.latestEvent?.aggregate?.shortSummary ?? "Press Listen or \(store.listenHotkey) to capture what is playing. Drag the bottom edge to read more.")
                    .font(.system(size: 11.5))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .frame(minHeight: 76, maxHeight: .infinity, alignment: .topLeading)
        .help("The latest reading. Stretch the panel vertically to read everything.")
    }

    private var actions: some View {
        HStack {
            Button {
                if store.isListening {
                    store.stopListening()
                } else {
                    Task { await store.listenNow() }
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: store.isListening ? "stop.fill" : "waveform")
                        .font(.system(size: 11, weight: .semibold))
                    Text(store.isListening ? "Stop" : "Listen")
                        .font(.system(size: 12, weight: .semibold))
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 7)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(store.isListening ? Color(red: 0.69, green: 0.29, blue: 0.24) : Color.primary)
                )
                .foregroundStyle(Color(nsColor: .windowBackgroundColor))
            }
            .buttonStyle(.plain)
            .help(store.isListening ? "Stop listening" : "Listen to the \(store.selectedSource) source in \(currentPresetName) mode (\(store.listenHotkey))")

            Spacer()

            Button {
                store.openControlCenter()
            } label: {
                Image(systemName: "macwindow")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 26, height: 24)
            }
            .buttonStyle(.plain)
            .help("Open the control center")
        }
    }

    private var statusColor: Color {
        if !store.daemonOnline { return .orange }
        if store.isListening { return .green }
        if store.engineState == "warming" { return .orange }
        if store.nativeSystemAudioActive { return .green }
        return Color.primary.opacity(0.25)
    }
}
