import SwiftUI

/// The floating listener: the listening-result box IS the window. The reading
/// is always shown; a small reactive waveform sits in its corner while
/// listening. Every control floats just outside the box and fades in on hover —
/// listening mode at the top-left, close at the top-right, and the source
/// switch, Listen, and control-center buttons in a row below.
struct FloatingListenerView: View {
    @EnvironmentObject private var store: ShellStore
    @State private var hovering = false

    private let boxWidth: CGFloat = 316

    private let sources: [(id: String, label: String, icon: String, hint: String)] = [
        ("system", "System", "speaker.wave.2", "Listen to what the computer is playing"),
        ("mic", "Mic", "mic", "Listen to the microphone"),
        ("file", "File", "folder", "Listen to an audio file"),
    ]

    var body: some View {
        VStack(spacing: 9) {
            topControls
            resultBox
            bottomControls
        }
        .padding(14)
        .frame(width: boxWidth + 28)
        .animation(.easeOut(duration: 0.16), value: hovering)
        .onHover { hovering = $0 }
        .preferredColorScheme(.light)
    }

    // MARK: - The result box (the main surface)

    private var resultBox: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Empty until something has actually been heard.
            if let aggregate = store.latestEvent?.aggregate {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(aggregate.title ?? "Untitled reading")
                        .font(.system(size: 15.5, weight: .semibold))
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    if store.memoryMatchCount > 0 {
                        Label("\(store.memoryMatchCount)", systemImage: "sparkles")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(.secondary)
                            .help("\(store.memoryMatchCount) similar Akousmata trace\(store.memoryMatchCount == 1 ? "" : "s")")
                    }
                }
                ScrollView(.vertical, showsIndicators: false) {
                    Text(aggregate.shortSummary ?? "")
                        .font(.system(size: 13.5))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                }
                .frame(height: 152)
            } else {
                Color.clear.frame(height: 152)
            }

            footerStrip
        }
        .padding(18)
        .frame(width: boxWidth, alignment: .topLeading)
        .frost(cornerRadius: 18)
        .help(store.latestEvent == nil ? "Press Listen or \(store.listenHotkey) to hear what is playing." : "The latest reading. \(store.listenHotkey) listens again.")
    }

    /// Status word on the left, the small reactive waveform tucked into the
    /// bottom-right corner of the box.
    private var footerStrip: some View {
        HStack(spacing: 8) {
            if let status = footerStatusText {
                Text(status)
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 6)
            MiniWaveformView(bands: store.signalBands, level: waveLevel, active: waveActive)
                .frame(width: 58, height: 18)
        }
    }

    /// Only speak up when there is something to say; a fresh, idle box is quiet.
    private var footerStatusText: String? {
        if !store.daemonOnline { return "daemon offline" }
        if store.isListening { return "listening…" }
        if store.isAnalyzingNativeSystemAudio { return "reading system audio…" }
        if store.engineState == "warming" { return "warming the ear" }
        return nil
    }

    // MARK: - Controls around the box (hover)

    private var topControls: some View {
        HStack(spacing: 8) {
            presetMenu
            Spacer(minLength: 0)
            closeButton
        }
        .frame(width: boxWidth)
        .opacity(hovering ? 1 : 0)
        .allowsHitTesting(hovering)
    }

    private var bottomControls: some View {
        HStack(spacing: 8) {
            sourcePill
            Spacer(minLength: 0)
            listenButton
            controlCenterButton
        }
        .frame(width: boxWidth)
        .opacity(hovering ? 1 : 0)
        .allowsHitTesting(hovering)
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
            // Same construction as the buttons under the box: a rectangular
            // frost tile with softly rounded corners, not a capsule.
            HStack(spacing: 8) {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 13, weight: .medium))
                Text(currentPresetName)
                    .font(.system(size: 13.5, weight: .medium))
                    .lineLimit(1)
            }
            .padding(.horizontal, 15)
            .frame(height: 32)
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .foregroundStyle(.secondary)
        .frost(cornerRadius: 11)
        .help(currentPresetDescription)
        .disabled(store.isListening)
    }

    private var closeButton: some View {
        Button {
            store.toggleFloatingListener()
        } label: {
            Image(systemName: "xmark")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary)
                .frame(width: 26, height: 26)
        }
        .buttonStyle(.plain)
        .frost(cornerRadius: 13)
        .help("Hide the floating listener (\(store.toggleHotkey))")
    }

    private var sourcePill: some View {
        HStack(spacing: 3) {
            ForEach(sources, id: \.id) { source in
                Button {
                    store.selectedSource = source.id
                } label: {
                    Image(systemName: source.icon)
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: 30, height: 24)
                        .background(
                            RoundedRectangle(cornerRadius: 9, style: .continuous)
                                .fill(store.selectedSource == source.id ? Color.primary : Color.clear)
                        )
                        .foregroundStyle(store.selectedSource == source.id ? Color(nsColor: .windowBackgroundColor) : .secondary)
                }
                .buttonStyle(.plain)
                .help(source.hint)
                .disabled(store.isListening)
            }
        }
        .padding(3)
        .frost(cornerRadius: 13)
    }

    private var listenButton: some View {
        Button {
            if store.isListening {
                store.stopListening()
            } else {
                Task { await store.listenNow() }
            }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: store.isListening ? "stop.fill" : "waveform")
                    .font(.system(size: 12, weight: .semibold))
                Text(store.isListening ? "Stop" : "Listen")
                    .font(.system(size: 13, weight: .semibold))
            }
            .padding(.horizontal, 16)
            .frame(height: 30)
            .background(
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .fill(store.isListening ? Color(red: 0.69, green: 0.29, blue: 0.24) : Color.primary)
            )
            .foregroundStyle(Color(nsColor: .windowBackgroundColor))
        }
        .buttonStyle(.plain)
        .help(store.isListening ? "Stop listening" : "Listen to the \(store.selectedSource) source in \(currentPresetName) mode (\(store.listenHotkey))")
    }

    private var controlCenterButton: some View {
        Button {
            store.openControlCenter()
        } label: {
            Image(systemName: "macwindow")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 30, height: 30)
        }
        .buttonStyle(.plain)
        .frost(cornerRadius: 13)
        .help("Open the control center")
    }

    // MARK: - Derived

    private var currentPresetName: String {
        store.presets.first(where: { $0.id == store.selectedPreset })?.name ?? store.selectedPreset.capitalized
    }

    private var currentPresetDescription: String {
        let preset = store.presets.first(where: { $0.id == store.selectedPreset })
        return "Listening mode: \(preset?.name ?? store.selectedPreset). \(preset?.description ?? "")"
    }

    private var waveLevel: Double {
        if store.selectedSource == "mic", store.micLevel > 0 { return store.micLevel }
        if store.nativeSystemAudioActive { return store.nativeSystemAudioRMS }
        return store.liveSignal?.meter?.rms ?? 0
    }

    private var waveActive: Bool {
        store.isListening || store.isAnalyzingNativeSystemAudio || waveLevel > 0.02
    }

}
