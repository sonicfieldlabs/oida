import AppKit
import SwiftUI

/// The floating listener: the listening-result box IS the window. The reading
/// is always shown; a small reactive waveform sits in its corner while
/// listening. Every control floats just outside the box and fades in on hover —
/// listening mode at the top-left, close at the top-right, and the source
/// switch, Listen, and control-center buttons in a row below.
struct FloatingListenerView: View {
    @EnvironmentObject private var store: ShellStore
    @State private var hovering = false
    @State private var isEditingTitle = false
    @State private var titleDraft = ""
    @State private var copied = false
    @FocusState private var titleIsFocused: Bool

    private let boxWidth: CGFloat = 316

    private let sources: [(id: String, label: String, icon: String, hint: String)] = [
        ("system", "System", "speaker.wave.2", "Listen to what the computer is playing"),
        ("mic", "Mic", "mic", "Listen to the microphone"),
        ("file", "File", "folder", "Listen to an audio file"),
    ]

    private let directions: [(id: String, icon: String, hint: String)] = [
        ("past", "gobackward", "Past: capture the seconds the ear already heard before the trigger"),
        ("future", "goforward", "Future: record the seconds after the trigger"),
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
        .preferredColorScheme(store.preferredColorScheme)
    }

    // MARK: - The result box (the main surface)

    private var resultBox: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Empty until something has actually been heard.
            if let aggregate = store.floatingEvent?.aggregate {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    editableTitle(aggregate.title ?? "Untitled reading")
                    Spacer(minLength: 4)
                    if store.floatingMemoryMatchCount > 0 {
                        Label("\(store.floatingMemoryMatchCount)", systemImage: "sparkles")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(.secondary)
                            .help("\(store.floatingMemoryMatchCount) similar Akousmata trace\(store.floatingMemoryMatchCount == 1 ? "" : "s")")
                    }
                }
                ScrollView(.vertical, showsIndicators: false) {
                    Text(floatingSummary(aggregate))
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
        .help(store.floatingEvent == nil ? "Press Listen or \(store.listenHotkey) to hear what is playing." : "The latest reading. \(store.listenHotkey) listens again.")
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
            if store.isProcessing {
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 20, height: 18)
            } else if store.isListening {
                ProgressView(value: store.listeningProgress)
                    .progressViewStyle(.linear)
                    .frame(width: 52)
            }
            if store.floatingEvent != nil, !store.isListenBusy {
                shareButton
                copyButton
            }
            MiniWaveformView(bands: store.signalBands, level: waveLevel, active: waveActive)
                .frame(width: 58, height: 18)
        }
    }

    /// Only speak up when there is something to say; a fresh, idle box is quiet.
    private var footerStatusText: String? {
        if !store.daemonOnline { return "daemon offline" }
        // The progress line and live meter already communicate hearing. Keep
        // the compact footer from repeating a second listening label.
        if store.isListening { return nil }
        if store.isProcessing || store.isAnalyzingNativeSystemAudio { return "operating listening…" }
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
        HStack(spacing: 6) {
            sourcePill
            directionPill
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
            HStack(spacing: 6) {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 11.5, weight: .medium))
                Text(currentPresetName)
                    .font(.system(size: 13, weight: .medium))
                    .lineLimit(1)
            }
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        // Padding sits outside the native Menu label so AppKit cannot collapse
        // the top/bottom breathing room. Horizontal padding stays narrower.
        .padding(.horizontal, 9)
        .padding(.vertical, 7)
        .contentShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
        .foregroundStyle(.secondary)
        .frost(cornerRadius: 13)
        .help(currentPresetDescription)
        .disabled(store.isListenBusy)
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
        .help("Hide the floating listener")
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
                .disabled(store.isListenBusy)
            }
        }
        .padding(3)
        .frost(cornerRadius: 13)
    }

    /// Past ⟲ / future ⟳ — the temporal direction of the next listen.
    private var directionPill: some View {
        HStack(spacing: 3) {
            ForEach(directions, id: \.id) { direction in
                Button {
                    store.selectedDirection = direction.id
                } label: {
                    Image(systemName: direction.icon)
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: 30, height: 24)
                        .background(
                            RoundedRectangle(cornerRadius: 9, style: .continuous)
                                .fill(store.selectedDirection == direction.id ? Color.primary : Color.clear)
                        )
                        .foregroundStyle(store.selectedDirection == direction.id ? Color(nsColor: .windowBackgroundColor) : .secondary)
                }
                .buttonStyle(.plain)
                .help(direction.hint)
                .disabled(store.isListenBusy)
            }
        }
        .padding(3)
        .frost(cornerRadius: 13)
    }

    private var listenButton: some View {
        Button {
            if store.isListening {
                store.stopListening()
            } else if !store.isProcessing {
                Task { await store.listenNow() }
            }
        } label: {
            HStack(spacing: 6) {
                OidaListeningGlyph(motion: glyphMotion)
                    .frame(width: 18, height: 18)
                    .padding(2)
                    .overlay {
                        Circle()
                            .stroke(Color.primary.opacity(0.22), lineWidth: 0.75)
                    }
                Text(store.isListening ? "Hearing" : (store.isProcessing ? "Operating" : "Listen"))
                    .font(.system(size: 13, weight: .semibold))
            }
            .padding(.horizontal, 12)
            .frame(height: 30)
            .background(
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .fill(store.isListening ? Color(red: 0.69, green: 0.29, blue: 0.24) : (store.isProcessing ? Color.secondary : Color.primary))
            )
            .foregroundStyle(Color(nsColor: .windowBackgroundColor))
        }
        .buttonStyle(.plain)
        .disabled(store.isProcessing)
        .help(store.isListening ? "Stop listening" : (store.isProcessing ? "Operating listening on the captured audio" : "Listen \(store.selectedDirection == "future" ? "forward" : "back") on the \(store.selectedSource) source in \(currentPresetName) mode (\(store.listenHotkey))"))
    }

    private var glyphMotion: OidaListeningGlyph.Motion {
        if store.isListening { return .hearing }
        if store.isProcessing { return .operating }
        return .idle
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

    @ViewBuilder
    private func editableTitle(_ title: String) -> some View {
        if isEditingTitle {
            TextField("Listening result title", text: $titleDraft)
                .textFieldStyle(.plain)
                .font(.system(size: 15.5, weight: .semibold))
                .focused($titleIsFocused)
                .onSubmit { commitTitle(original: title) }
                .onExitCommand { cancelTitleEdit(original: title) }
                .onChange(of: titleIsFocused) { focused in
                    if !focused, isEditingTitle { commitTitle(original: title) }
                }
        } else {
            Button {
                titleDraft = title
                isEditingTitle = true
                DispatchQueue.main.async { titleIsFocused = true }
            } label: {
                Text(title)
                    .font(.system(size: 15.5, weight: .semibold))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            .contentShape(Rectangle())
            .help("Click to rename this listening result")
        }
    }

    private func commitTitle(original: String) {
        guard isEditingTitle else { return }
        let requested = titleDraft
        isEditingTitle = false
        titleIsFocused = false
        Task {
            if !(await store.renameFloatingEvent(to: requested)) {
                titleDraft = original
            }
        }
    }

    private func cancelTitleEdit(original: String) {
        titleDraft = original
        isEditingTitle = false
        titleIsFocused = false
    }

    private var shareButton: some View {
        ShareLink(item: floatingShareText, subject: Text(floatingTitle)) {
            Image(systemName: "square.and.arrow.up")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 20, height: 18)
        }
        .buttonStyle(.plain)
        .help("Share or export this listening result")
    }

    private var copyButton: some View {
        Button {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(floatingShareText, forType: .string)
            copied = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.1) { copied = false }
        } label: {
            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 20, height: 18)
        }
        .buttonStyle(.plain)
        .help(copied ? "Copied" : "Copy this listening result")
    }

    private var floatingTitle: String {
        store.floatingEvent?.aggregate?.title ?? "Listening result"
    }

    private var floatingShareText: String {
        guard let aggregate = store.floatingEvent?.aggregate else { return "" }
        return [floatingTitle, floatingSummary(aggregate)]
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .joined(separator: "\n\n")
    }

    private var waveLevel: Double {
        if store.selectedSource == "mic", store.micLevel > 0 { return store.micLevel }
        if store.nativeSystemAudioActive { return store.nativeSystemAudioRMS }
        return store.liveSignal?.meter?.rms ?? 0
    }

    private var waveActive: Bool {
        store.isListenBusy || store.isAnalyzingNativeSystemAudio || waveLevel > 0.02
    }

    private func floatingSummary(_ aggregate: ListeningAggregateSummary) -> String {
        let reading = aggregate.shortSummary ?? aggregate.detailedSummary ?? ""
        guard let musicID = store.floatingEvent?.musicID, musicID.matched == true else {
            return reading
        }
        let song = musicID.title?.trimmingCharacters(in: .whitespacesAndNewlines)
        let artist = musicID.artist?.trimmingCharacters(in: .whitespacesAndNewlines)
        let identity = "\(song?.isEmpty == false ? song! : "Identified song") by \(artist?.isEmpty == false ? artist! : "Unknown artist")."
        return [identity, reading].filter { !$0.isEmpty }.joined(separator: " ")
    }

}

/// The same concentric listening mark used by the dashboard. During hearing
/// each arc turns independently; while the captured sound is being operated
/// on, the arcs rotate around the vertical axis and briefly form a sphere.
private struct OidaListeningGlyph: View {
    enum Motion: Equatable { case idle, hearing, operating }

    let motion: Motion

    var body: some View {
        TimelineView(AnimationTimelineSchedule(minimumInterval: 1.0 / 30.0, paused: motion == .idle)) { timeline in
            let seconds = timeline.date.timeIntervalSinceReferenceDate
            ZStack(alignment: .center) {
                ring(diameter: 17.5, width: 1.45, opacity: 0.62)
                    .rotationEffect(rotation(for: 0, seconds: seconds), anchor: .center)
                    .rotation3DEffect(verticalRotation(for: 0, seconds: seconds), axis: (x: 0, y: 1, z: 0), anchor: .center, anchorZ: 0, perspective: 0)
                ring(diameter: 12.7, width: 1.7, opacity: 0.82)
                    .rotationEffect(rotation(for: 1, seconds: seconds), anchor: .center)
                    .rotation3DEffect(verticalRotation(for: 1, seconds: seconds), axis: (x: 0, y: 1, z: 0), anchor: .center, anchorZ: 0, perspective: 0)
                ring(diameter: 8.1, width: 2, opacity: 1)
                    .rotationEffect(rotation(for: 2, seconds: seconds), anchor: .center)
                    .rotation3DEffect(verticalRotation(for: 2, seconds: seconds), axis: (x: 0, y: 1, z: 0), anchor: .center, anchorZ: 0, perspective: 0)
                Circle()
                    .fill(.primary)
                    .frame(width: 2.8, height: 2.8)
            }
            .frame(width: 18, height: 18, alignment: .center)
            .drawingGroup()
        }
        .accessibilityHidden(true)
    }

    private func ring(diameter: CGFloat, width: CGFloat, opacity: Double) -> some View {
        Circle()
            .trim(from: 0.07, to: 0.76)
            .stroke(style: StrokeStyle(lineWidth: width, lineCap: .round))
            .opacity(opacity)
            .frame(width: diameter, height: diameter)
            .rotationEffect(.degrees(42))
    }

    private func rotation(for index: Int, seconds: Double) -> Angle {
        guard motion == .hearing else { return .zero }
        let speeds = [58.0, -76.0, 104.0]
        return .degrees(seconds * speeds[index])
    }

    private func verticalRotation(for index: Int, seconds: Double) -> Angle {
        guard motion == .operating else { return .zero }
        let speeds = [104.0, -132.0, 164.0]
        return .degrees(seconds * speeds[index] + Double(index) * 38)
    }
}
