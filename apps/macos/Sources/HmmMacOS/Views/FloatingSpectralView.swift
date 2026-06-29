import SwiftUI

struct FloatingSpectralView: View {
    @EnvironmentObject private var store: ShellStore

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(.regularMaterial)
                .overlay(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(.primary.opacity(0.12))
                )

            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label("hmm", systemImage: "waveform.path")
                        .font(.headline)
                    Spacer()
                    Text(store.statusLabel)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(store.daemonOnline ? .green : .orange)
                }

                spectralField

                Text(store.signalMeterLabel)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                HStack {
                    Button {
                        Task {
                            if store.nativeSystemAudioActive {
                                await store.stopNativeSystemAudioTap()
                            } else {
                                await store.startNativeSystemAudioTap()
                            }
                        }
                    } label: {
                        Image(systemName: store.nativeSystemAudioActive ? "speaker.slash" : "speaker.wave.2")
                    }
                    .help(store.nativeSystemAudioActive ? "Stop native system audio tap" : "Start native system audio tap")

                    Button {
                        Task { await store.analyzeNativeSystemAudio() }
                    } label: {
                        Image(systemName: "waveform.path.ecg.rectangle")
                    }
                    .disabled(!store.nativeSystemAudioActive || store.isAnalyzingNativeSystemAudio)
                    .help("Analyze recent native system audio")

                    Button {
                        Task { await store.cleanupNativeSystemAudioTemp() }
                    } label: {
                        Image(systemName: "trash")
                    }
                    .disabled(store.nativeSystemAudioTempFileCount == 0 || store.isCleaningNativeSystemAudioTemp)
                    .help("Clean native system audio temp files")

                    Button {
                        Task { await store.capture() }
                    } label: {
                        Label("Capture", systemImage: "waveform.path.ecg")
                    }
                    .disabled(store.isPaused || !store.hasActiveLiveSession || store.isCapturing)

                    Button {
                        Task { await store.rerunLatestEvent(routePreset: "signal") }
                    } label: {
                        Image(systemName: "arrow.triangle.2.circlepath")
                    }
                    .disabled(!store.canRerunLatestEvent || store.isRerunningRoute)
                    .help("Run signal route on latest result")

                    Button {
                        Task { await store.togglePause() }
                    } label: {
                        Image(systemName: store.isPaused ? "play.fill" : "pause.fill")
                    }
                    .help(store.isPaused ? "Resume listening" : "Pause listening")

                    Button {
                        store.openDashboard()
                    } label: {
                        Image(systemName: "safari")
                    }
                    .help("Open dashboard")
                }
                .buttonStyle(.borderless)
            }
            .padding(16)
        }
        .background(WindowLevelAccessor(level: .floating))
    }

    private var spectralField: some View {
        TimelineView(.animation) { timeline in
            GeometryReader { proxy in
                let phase = timeline.date.timeIntervalSinceReferenceDate
                let width = proxy.size.width
                let height = proxy.size.height
                let active = store.hasActiveLiveSession && !store.isPaused
                let memoryBoost = min(Double(store.memoryMatchCount), 4.0) * 0.08
                let bands = store.signalBands.isEmpty ? Array(repeating: 0.0, count: 14) : store.signalBands

                ZStack {
                    ForEach(Array(bands.enumerated()), id: \.offset) { index, value in
                        let denominator = max(1, bands.count - 1)
                        let position = CGFloat(index) / CGFloat(denominator)
                        let fallback = active ? 0.24 + memoryBoost + wave(phase, index) : 0.08 + wave(phase * 0.3, index) * 0.25
                        let measured = max(0.06, min(1.0, value + memoryBoost))
                        let amplitude = store.signalBands.isEmpty ? fallback : measured
                        Capsule()
                            .fill(barColor(index: index, active: active))
                            .frame(
                                width: max(3, width / 36),
                                height: max(8, height * CGFloat(amplitude))
                            )
                            .position(
                                x: width * position,
                                y: height / 2 + CGFloat(sin(phase * 0.8 + Double(index))) * 6
                            )
                    }

                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(.primary.opacity(store.memoryMatchCount > 0 ? 0.42 : 0.18), lineWidth: store.memoryMatchCount > 0 ? 2 : 1)
                }
            }
        }
        .frame(height: 72)
        .accessibilityLabel("Signal-derived spectral status")
    }

    private func wave(_ phase: TimeInterval, _ index: Int) -> Double {
        let a = sin(phase * 1.4 + Double(index) * 0.72)
        let b = cos(phase * 0.65 + Double(index) * 0.41)
        return max(0.0, (a + b + 2.0) / 4.0)
    }

    private func barColor(index: Int, active: Bool) -> Color {
        if store.isPaused {
            return .secondary.opacity(0.35)
        }
        if !store.daemonOnline {
            return .orange.opacity(0.55)
        }
        let base = active ? Color.green : Color.teal
        return index.isMultiple(of: 3) ? base.opacity(0.82) : base.opacity(0.52)
    }
}
