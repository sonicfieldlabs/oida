import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: ShellStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        NavigationSplitView {
            List {
                Section("Agent") {
                    Label("Runtime", systemImage: "waveform.path")
                    Label("Quick Result", systemImage: "text.bubble")
                    Label("Privacy", systemImage: "lock")
                }
                Section("Links") {
                    Button("Dashboard") {
                        store.openDashboard()
                    }
                    Button("Floating Listener") {
                        openWindow(id: "floating-agent")
                    }
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(190)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    daemonPanel
                    runtimePanel
                    quickActions
                    nativeSystemAudioPanel
                    signalPanel
                    QuickResultView(
                        event: store.latestEvent,
                        recentEvents: store.recentEvents,
                        pinnedEvents: store.pinnedEvents,
                        historyPersistent: store.recentHistoryPersistent,
                        historyActionMessage: store.historyActionMessage,
                        conversation: store.latestConversation,
                        isAskingConversation: store.isAskingConversation,
                        generation: store.latestGeneration,
                        generationHistory: store.latestGenerationHistory,
                        generationActionMessage: store.generationActionMessage,
                        isCreatingGenerationPrompt: store.isCreatingGenerationPrompt,
                        comparison: store.latestRouteComparison,
                        isRerunning: store.isRerunningRoute,
                        onRerun: { preset in
                            Task { await store.rerunLatestEvent(routePreset: preset) }
                        },
                        onAskConversation: { question in
                            Task { await store.askLatestConversation(question: question) }
                        },
                        onCreateGenerationPrompt: { prompt in
                            Task { await store.createGenerationPrompt(prompt: prompt) }
                        },
                        onRefreshGenerationHistory: {
                            Task { await store.refreshGenerationHistory() }
                        },
                        onSelectRecent: { event in
                            store.selectRecentEvent(event)
                        },
                        onTogglePinned: { event, pinned in
                            Task { await store.setHistoryPinned(event: event, pinned: pinned) }
                        },
                        onBatchPinned: { eventIds, pinned in
                            Task { await store.setHistoryPinned(eventIds: eventIds, pinned: pinned) }
                        },
                        onClearHistory: { keepPinned in
                            Task { await store.clearRecentHistory(keepPinned: keepPinned) }
                        },
                        onExportHistory: {
                            Task { await store.exportRecentHistory() }
                        },
                        onArchiveHistory: { eventIds in
                            Task { await store.archiveRecentHistory(eventIds: eventIds, label: "native-review") }
                        }
                    )
                    privacyPanel
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(.regularMaterial)
        }
        .task {
            store.registerConfiguredHotkeyIfNeeded()
            await store.refresh()
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 6) {
                Text("hmm")
                    .font(.largeTitle.weight(.semibold))
                Text("Native shell for the local listening daemon")
                    .foregroundStyle(.secondary)
            }

            Spacer()

            StatusBadge(
                title: store.statusLabel,
                systemImage: store.daemonOnline ? "checkmark.circle.fill" : "exclamationmark.triangle.fill",
                tint: store.daemonOnline ? .green : .orange
            )
        }
    }

    private var runtimePanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Background Runtime")
                .font(.headline)

            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 8) {
                metricRow("Daemon", store.daemonOnline ? "Online" : "Offline")
                metricRow("Profile", store.health?.profile ?? "-")
                metricRow("Source", store.hasActiveLiveSession ? "Live buffer armed" : "No live session")
                metricRow("Signal", store.signalMeterLabel)
                metricRow("Native tap", store.nativeSystemAudioState.label)
                metricRow("System route", store.nativeSystemAudioRouteLabel)
                metricRow("Capture", "\(Int(store.defaultCaptureSeconds))s / \(store.defaultRoutePreset)")
                metricRow("Launch", store.launchAtLoginStatus)
                metricRow("Memory", store.background?.config.saveEventsByDefault == true ? "Save by default" : "Explicit save")
                metricRow("Incognito", store.background?.config.incognito == true ? "On" : "Off")
                metricRow("Temp audio", store.nativeSystemAudioTempSummary)
            }

            if let error = store.errorMessage {
                Label(error, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
                    .font(.callout)
            }
        }
        .panelStyle()
    }

    private var nativeSystemAudioPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Native System Audio")
                .font(.headline)

            HStack(spacing: 10) {
                Button {
                    Task { await store.startNativeSystemAudioTap() }
                } label: {
                    Label("Start Tap", systemImage: "speaker.wave.2")
                }
                .disabled(store.nativeSystemAudioActive)

                Button {
                    Task { await store.stopNativeSystemAudioTap() }
                } label: {
                    Label("Stop Tap", systemImage: "stop.fill")
                }
                .disabled(!store.nativeSystemAudioActive)

                Button {
                    Task { await store.analyzeNativeSystemAudio() }
                } label: {
                    Label(store.isAnalyzingNativeSystemAudio ? "Analyzing" : "Analyze System", systemImage: "waveform.path.ecg.rectangle")
                }
                .disabled(!store.nativeSystemAudioActive || store.isAnalyzingNativeSystemAudio)

                Button {
                    Task { await store.cleanupNativeSystemAudioTemp() }
                } label: {
                    Label(store.isCleaningNativeSystemAudioTemp ? "Cleaning" : "Clean Temp", systemImage: "trash")
                }
                .disabled(store.nativeSystemAudioTempFileCount == 0 || store.isCleaningNativeSystemAudioTemp)

                Text(store.nativeSystemAudioState.label)
                    .foregroundStyle(.secondary)
            }

            Text(store.nativeSystemAudioTempSummary)
                .font(.caption)
                .foregroundStyle(.secondary)

            Text("Route: \(store.nativeSystemAudioRouteLabel)")
                .font(.caption)
                .foregroundStyle(.secondary)

            if let sampleRate = store.nativeSystemAudioSampleRate, let channels = store.nativeSystemAudioChannelCount {
                Text("\(Int(sampleRate)) Hz / \(channels) channel\(channels == 1 ? "" : "s") / temp analysis on request")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if let path = store.latestNativeSystemAudioTempPath {
                Text("Latest temp capture: \(shortLabel(path, maxLength: 72))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("Uses ScreenCaptureKit. macOS may ask for Screen Recording permission. Raw audio is written only for explicit analysis.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .panelStyle()
    }

    private var daemonPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Daemon Supervisor")
                .font(.headline)

            HStack(spacing: 10) {
                Button {
                    Task { await store.startDaemon() }
                } label: {
                    Label(store.isStartingDaemon ? "Starting" : "Start Daemon", systemImage: "power")
                }
                .disabled(store.daemonOnline || store.isStartingDaemon)

                Button {
                    Task { await store.stopManagedDaemon() }
                } label: {
                    Label("Stop Managed", systemImage: "stop.fill")
                }
                .disabled(!store.managedDaemonRunning)

                Text(store.managedDaemonRunning ? "Managed process running" : "No managed process")
                    .foregroundStyle(.secondary)
            }

            if !store.daemonLogLines.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(store.daemonLogLines.suffix(3), id: \.self) { line in
                        Text(shortLabel(line, maxLength: 88))
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .panelStyle()
    }

    private var quickActions: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Quick Actions")
                .font(.headline)

            HStack(spacing: 10) {
                Button {
                    Task { await store.capture() }
                } label: {
                    Label(store.isCapturing ? "Capturing" : "Capture Buffer", systemImage: "waveform.path.ecg")
                }
                .disabled(store.isPaused || !store.hasActiveLiveSession || store.isCapturing)
                .buttonStyle(.borderedProminent)

                Button {
                    Task { await store.togglePause() }
                } label: {
                    Label(store.isPaused ? "Resume" : "Pause", systemImage: store.isPaused ? "play.fill" : "pause.fill")
                }
                .disabled(!store.daemonOnline)

                Button {
                    store.openDashboard()
                } label: {
                    Label("Dashboard", systemImage: "safari")
                }

                Button {
                    Task { await store.refresh() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(store.isRefreshing)
            }
        }
        .panelStyle()
    }

    private var signalPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Live Signal")
                .font(.headline)

            SignalMeterView(bands: store.signalBands, active: store.hasActiveLiveSession && !store.isPaused)
                .frame(height: 58)

            HStack {
                Text(store.signalSourceLabel)
                Spacer()
                Text(store.signalMeterLabel)
            }
            .font(.callout)
            .foregroundStyle(.secondary)
        }
        .panelStyle()
    }

    private var privacyPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Privacy Surface")
                .font(.headline)

            Label("Quick capture uses the active daemon live buffer.", systemImage: "waveform")
            Label("Akousmata memory remains explicit unless daemon settings say otherwise.", systemImage: "externaldrive")
            Label("Native system-audio captures are temporary files with explicit cleanup controls.", systemImage: "trash")
        }
        .foregroundStyle(.secondary)
        .panelStyle()
    }

    private func metricRow(_ title: String, _ value: String) -> some View {
        GridRow {
            Text(title)
                .foregroundStyle(.secondary)
            Text(value)
                .fontWeight(.medium)
        }
    }
}

private struct SignalMeterView: View {
    let bands: [Double]
    let active: Bool

    var body: some View {
        GeometryReader { proxy in
            let values = normalizedBands
            HStack(alignment: .center, spacing: 4) {
                ForEach(Array(values.enumerated()), id: \.offset) { index, value in
                    Capsule()
                        .fill(active ? Color.green.opacity(index.isMultiple(of: 3) ? 0.9 : 0.6) : Color.secondary.opacity(0.28))
                        .frame(width: max(4, proxy.size.width / CGFloat(values.count * 2)), height: max(6, proxy.size.height * CGFloat(value)))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
        }
        .accessibilityLabel("Live signal meter")
    }

    private var normalizedBands: [Double] {
        if bands.isEmpty {
            return Array(repeating: 0.08, count: 14)
        }
        return bands.map { min(1.0, max(0.04, $0)) }
    }
}

private struct StatusBadge: View {
    let title: String
    let systemImage: String
    let tint: Color

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.callout.weight(.medium))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(tint.opacity(0.14), in: Capsule())
            .foregroundStyle(tint)
    }
}

private extension View {
    func panelStyle() -> some View {
        self
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.background, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}
