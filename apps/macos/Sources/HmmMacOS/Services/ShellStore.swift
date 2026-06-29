import AppKit
import Foundation

@MainActor
final class ShellStore: ObservableObject {
    @Published var daemonBaseURL: String {
        didSet {
            let trimmed = daemonBaseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            UserDefaults.standard.set(trimmed, forKey: Defaults.daemonBaseURL)
            client = DaemonClient(baseURLString: trimmed)
        }
    }
    @Published var health: HealthResponse?
    @Published var background: BackgroundStatusResponse?
    @Published var liveSignal: LiveSignalResponse?
    @Published var latestEvent: ListeningEventSummary?
    @Published var recentEvents: [ListeningEventSummary] = []
    @Published var pinnedEvents: [ListeningEventSummary] = []
    @Published var latestHistoryExport: BackgroundHistoryResponse?
    @Published var latestHistoryArchive: BackgroundHistoryArchiveResponse?
    @Published var historyActionMessage: String?
    @Published var latestConversation: ConversationAskResponse?
    @Published var isAskingConversation = false
    @Published var latestGeneration: GenerationRecord?
    @Published var latestGenerationHistory: GenerationHistoryResponse?
    @Published var generationActionMessage: String?
    @Published var isCreatingGenerationPrompt = false
    @Published var latestRouteComparison: RouteComparisonSummary?
    @Published var errorMessage: String?
    @Published var isRefreshing = false
    @Published var isCapturing = false
    @Published var isRerunningRoute = false
    @Published var isAnalyzingNativeSystemAudio = false
    @Published var latestNativeSystemAudioTempPath: String?
    @Published var nativeSystemAudioTempStatus: NativeSystemAudioTempStatusResponse?
    @Published var isCleaningNativeSystemAudioTemp = false
    @Published var isStartingDaemon = false
    @Published var managedDaemonRunning = false
    @Published var daemonLogLines: [String] = []
    @Published var launchAtLoginStatus = LaunchAtLoginManager.statusLabel
    @Published var nativeSystemAudioState = SystemAudioTapState.idle
    @Published var nativeSystemAudioBands: [Double] = []
    @Published var nativeSystemAudioRMS = 0.0
    @Published var nativeSystemAudioPeak = 0.0
    @Published var nativeSystemAudioSampleRate: Double?
    @Published var nativeSystemAudioChannelCount: Int?
    @Published var nativeSystemAudioUpdatedAt: Date?
    @Published var nativeSystemAudioRoute: NativeSystemAudioRoutePayload?
    @Published var shellHotkey: String {
        didSet {
            UserDefaults.standard.set(shellHotkey, forKey: Defaults.captureHotkey)
        }
    }
    @Published var hotkeyStatus = "No global hotkey"

    private var client: DaemonClient
    private let supervisor = DaemonSupervisor()
    private let systemAudioTap = SystemAudioTapManager()
    private var hasRegisteredConfiguredHotkey = false
    private var pollingTask: Task<Void, Never>?

    init() {
        let url = UserDefaults.standard.string(forKey: Defaults.daemonBaseURL) ?? "http://127.0.0.1:8765"
        daemonBaseURL = url
        shellHotkey = UserDefaults.standard.string(forKey: Defaults.captureHotkey) ?? ""
        client = DaemonClient(baseURLString: url)
        supervisor.onLogLine = { [weak self] line in
            self?.appendDaemonLog(line)
        }
        supervisor.onExit = { [weak self] status in
            self?.managedDaemonRunning = false
            self?.appendDaemonLog("Managed daemon exited with status \(status)")
        }
        systemAudioTap.onStateChange = { [weak self] state in
            self?.nativeSystemAudioState = state
        }
        systemAudioTap.onSnapshot = { [weak self] snapshot in
            self?.nativeSystemAudioBands = snapshot.bands
            self?.nativeSystemAudioRMS = snapshot.rms
            self?.nativeSystemAudioPeak = snapshot.peak
            self?.nativeSystemAudioSampleRate = snapshot.sampleRate
            self?.nativeSystemAudioChannelCount = snapshot.channelCount
            self?.nativeSystemAudioUpdatedAt = snapshot.updatedAt
        }
        systemAudioTap.onRouteChange = { [weak self] route in
            self?.nativeSystemAudioRoute = route
        }
    }

    var daemonOnline: Bool {
        health?.ok == true
    }

    var statusLabel: String {
        if let errorMessage {
            return shortLabel(errorMessage, maxLength: 42)
        }
        if background?.config.paused == true {
            return "Paused"
        }
        if let status = background?.state.status, !status.isEmpty {
            return displayStatus(status)
        }
        return daemonOnline ? "Idle" : "Offline"
    }

    var hasActiveLiveSession: Bool {
        background?.state.activeLiveSessionId?.isEmpty == false
    }

    var defaultCaptureSeconds: Double {
        background?.config.defaultCaptureSeconds ?? 10
    }

    var defaultRoutePreset: String {
        background?.config.defaultRoutePreset ?? "basic"
    }

    var isPaused: Bool {
        background?.config.paused == true
    }

    var memoryMatchCount: Int {
        latestEvent?.memory?.similarTraceIds?.count ?? 0
    }

    var recentHistoryPersistent: Bool {
        background?.config.recentHistory?.persist != false
    }

    var canRerunLatestEvent: Bool {
        latestEvent?.segment?.dataRef?.uri?.isEmpty == false
    }

    var signalBands: [Double] {
        if nativeSystemAudioState == .capturing, !nativeSystemAudioBands.isEmpty {
            return nativeSystemAudioBands
        }
        return liveSignal?.bands ?? []
    }

    var signalMeterLabel: String {
        if nativeSystemAudioState == .capturing {
            let rms = Int(round(nativeSystemAudioRMS * 100))
            let peak = Int(round(nativeSystemAudioPeak * 100))
            return "System RMS \(rms)% / peak \(peak)%"
        }
        guard let meter = liveSignal?.meter else { return "No live signal" }
        let rms = Int(round((meter.rms ?? 0) * 100))
        let peak = Int(round((meter.peak ?? 0) * 100))
        return "RMS \(rms)% / peak \(peak)%"
    }

    var nativeSystemAudioActive: Bool {
        nativeSystemAudioState == .capturing
    }

    var nativeSystemAudioTempFileCount: Int {
        nativeSystemAudioTempStatus?.fileCount ?? 0
    }

    var nativeSystemAudioTempSummary: String {
        let count = nativeSystemAudioTempFileCount
        let noun = count == 1 ? "file" : "files"
        return "\(count) temp \(noun) / \(byteCountLabel(nativeSystemAudioTempStatus?.bytes)) / \(nativeSystemAudioRetentionLabel)"
    }

    var nativeSystemAudioRetentionLabel: String {
        let retention = background?.config.nativeTempAudioRetention ?? nativeSystemAudioTempStatus?.retention
        switch retention?.policy {
        case "keep":
            return "keep until cleaned"
        case "delete_after_days":
            let days = retention?.deleteAfterDays ?? 1
            return "delete after \(formatDays(days))"
        case "delete_after_session":
            return "delete after session"
        default:
            return "temp policy"
        }
    }

    var nativeSystemAudioRouteLabel: String {
        guard let route = nativeSystemAudioRoute else { return "Display system mix" }
        if let displayId = route.displayId {
            return "\(route.label) / display \(displayId)"
        }
        return route.label
    }

    var signalSourceLabel: String {
        if nativeSystemAudioActive {
            return "Native system output tap / \(nativeSystemAudioRouteLabel)"
        }
        return liveSignal?.source?.label ?? "No live source"
    }

    func startPolling() {
        guard pollingTask == nil else { return }
        pollingTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    func refresh() async {
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            async let healthTask = client.health()
            async let statusTask = client.backgroundStatus()
            health = try await healthTask
            let status = try await statusTask
            applyBackgroundStatus(status)
            nativeSystemAudioTempStatus = try? await client.nativeSystemAudioTempStatus()
            if let sessionId = status.state.activeLiveSessionId, !sessionId.isEmpty {
                liveSignal = try? await client.liveSignal(sessionId: sessionId)
            } else {
                liveSignal = nil
            }
            managedDaemonRunning = supervisor.isManagedRunning
            launchAtLoginStatus = LaunchAtLoginManager.statusLabel
            errorMessage = nil
        } catch {
            health = nil
            background = nil
            liveSignal = nil
            nativeSystemAudioTempStatus = nil
            recentEvents = []
            pinnedEvents = []
            errorMessage = error.localizedDescription
            managedDaemonRunning = supervisor.isManagedRunning
            launchAtLoginStatus = LaunchAtLoginManager.statusLabel
        }
    }

    func startDaemon() async {
        guard !daemonOnline else {
            appendDaemonLog("Daemon is already reachable at \(daemonBaseURL)")
            return
        }
        guard !isStartingDaemon else { return }
        isStartingDaemon = true
        defer { isStartingDaemon = false }
        do {
            try supervisor.start()
            managedDaemonRunning = true
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
            appendDaemonLog(error.localizedDescription)
        }
    }

    func stopManagedDaemon() async {
        supervisor.stop()
        managedDaemonRunning = false
        try? await Task.sleep(nanoseconds: 500_000_000)
        await refresh()
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        do {
            try LaunchAtLoginManager.setEnabled(enabled)
            launchAtLoginStatus = LaunchAtLoginManager.statusLabel
            errorMessage = nil
        } catch {
            launchAtLoginStatus = LaunchAtLoginManager.statusLabel
            errorMessage = error.localizedDescription
        }
    }

    func startNativeSystemAudioTap() async {
        await systemAudioTap.start()
    }

    func stopNativeSystemAudioTap() async {
        await systemAudioTap.stop()
        nativeSystemAudioBands = []
        nativeSystemAudioRMS = 0
        nativeSystemAudioPeak = 0
        nativeSystemAudioSampleRate = nil
        nativeSystemAudioChannelCount = nil
        nativeSystemAudioUpdatedAt = nil
        nativeSystemAudioRoute = nil
    }

    func analyzeNativeSystemAudio(remember: Bool = false) async {
        guard !isAnalyzingNativeSystemAudio else { return }
        guard nativeSystemAudioActive else {
            errorMessage = "Start the native system audio tap before analyzing system output."
            return
        }
        guard let root = findHmmRepositoryRoot() else {
            errorMessage = "Could not locate the hmm repository root for temporary audio storage."
            return
        }

        isAnalyzingNativeSystemAudio = true
        defer { isAnalyzingNativeSystemAudio = false }
        do {
            let capture = try systemAudioTap.writeRecentAudio(seconds: defaultCaptureSeconds, repositoryRoot: root)
            latestNativeSystemAudioTempPath = capture.path.path
            let response = try await client.analyzeNativeSystemAudio(
                path: capture.path.path,
                durationSeconds: capture.durationSeconds,
                routePreset: defaultRoutePreset,
                remember: remember,
                sourceRoute: capture.sourceRoute
            )
            latestRouteComparison = nil
            latestEvent = response.listeningEvent ?? latestEvent
            resetConversation()
            applyBackgroundStatus(response.background)
            nativeSystemAudioRoute = response.sourceRoute ?? nativeSystemAudioRoute
            nativeSystemAudioTempStatus = response.retentionStatus ?? response.retentionCleanup?.status ?? nativeSystemAudioTempStatus
            historyActionMessage = nil
            errorMessage = nil
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func cleanupNativeSystemAudioTemp() async {
        guard !isCleaningNativeSystemAudioTemp else { return }
        isCleaningNativeSystemAudioTemp = true
        defer { isCleaningNativeSystemAudioTemp = false }
        do {
            let response = try await client.cleanupNativeSystemAudioTemp(deleteAll: true)
            nativeSystemAudioTempStatus = response.status
            applyBackgroundStatus(response.background)
            if let latest = latestNativeSystemAudioTempPath,
               response.deleted?.contains(where: { $0.path == latest }) == true {
                latestNativeSystemAudioTempPath = nil
            } else if response.status?.fileCount == 0 {
                latestNativeSystemAudioTempPath = nil
            }
            errorMessage = nil
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func pause() async {
        await mutateBackground {
            try await client.pause()
        }
    }

    func resume() async {
        await mutateBackground {
            try await client.resume()
        }
    }

    func togglePause() async {
        if isPaused {
            await resume()
        } else {
            await pause()
        }
    }

    func capture(remember: Bool = false) async {
        guard !isCapturing else { return }
        // The UI disables the capture button without a live session, but the global hotkey
        // bypasses that gate. Guard here so the hotkey can't fire a request the daemon
        // rejects with HTTP 400 ("no active live session" / "paused").
        guard hasActiveLiveSession else {
            errorMessage = "Start a live session before quick-capturing."
            return
        }
        guard !isPaused else {
            errorMessage = "Background capture is paused; resume before capturing."
            return
        }
        isCapturing = true
        defer { isCapturing = false }
        do {
            let response = try await client.capture(
                seconds: defaultCaptureSeconds,
                routePreset: defaultRoutePreset,
                remember: remember
            )
            latestRouteComparison = nil
            latestEvent = response.listeningEvent ?? response.background?.state.latestEvent
            resetConversation()
            applyBackgroundStatus(response.background)
            historyActionMessage = nil
            errorMessage = nil
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
            await refresh()
        }
    }

    func rerunLatestEvent(routePreset: String, remember: Bool = false) async {
        guard !isRerunningRoute else { return }
        guard let event = latestEvent, event.segment?.dataRef?.uri?.isEmpty == false else {
            errorMessage = "No routed listening event is ready to rerun."
            return
        }
        isRerunningRoute = true
        defer { isRerunningRoute = false }
        do {
            let response = try await client.rerun(event: event, routePreset: routePreset, remember: remember)
            latestRouteComparison = response.routeComparison
            latestEvent = response.listeningEvent ?? latestEvent
            resetConversation()
            applyBackgroundStatus(response.background)
            historyActionMessage = nil
            errorMessage = nil
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
            await refresh()
        }
    }

    func openDashboard() {
        guard let url = URL(string: daemonBaseURL) else {
            errorMessage = "Invalid daemon URL."
            return
        }
        NSWorkspace.shared.open(url)
    }

    func selectRecentEvent(_ event: ListeningEventSummary) {
        latestEvent = event
        latestRouteComparison = nil
        resetConversation()
        historyActionMessage = nil
        errorMessage = nil
    }

    func isPinned(_ event: ListeningEventSummary?) -> Bool {
        guard let id = event?.id else { return false }
        return pinnedEvents.contains { $0.id == id }
    }

    func togglePinned(_ event: ListeningEventSummary) async {
        await setHistoryPinned(event: event, pinned: !isPinned(event))
    }

    func setHistoryPinned(event: ListeningEventSummary, pinned: Bool) async {
        do {
            let response = try await client.setHistoryPinned(eventId: event.id, pinned: pinned)
            applyBackgroundStatus(response.background)
            applyHistory(response.history)
            historyActionMessage = pinned ? "Pinned recent result." : "Unpinned recent result."
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setHistoryPinned(eventIds: [String], pinned: Bool) async {
        let ids = uniqueEventIds(eventIds)
        guard !ids.isEmpty else {
            historyActionMessage = "Select history results first."
            return
        }
        do {
            let response = try await client.setHistoryPinned(eventIds: ids, pinned: pinned)
            applyBackgroundStatus(response.background)
            applyHistory(response.history)
            let count = response.pinnedEventIds?.count ?? ids.count
            let verb = pinned ? "Pinned" : "Unpinned"
            historyActionMessage = "\(verb) \(count) selected result\(count == 1 ? "" : "s")."
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func clearRecentHistory(keepPinned: Bool) async {
        do {
            let response = try await client.clearHistory(keepPinned: keepPinned)
            applyBackgroundStatus(response.background)
            applyHistory(response.history)
            latestRouteComparison = nil
            historyActionMessage = keepPinned ? "Cleared recent results; pinned results kept." : "Cleared all recent history."
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func exportRecentHistory() async {
        do {
            let response = try await client.exportHistory()
            latestHistoryExport = response
            applyHistory(response)

            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(response)
            let payload = String(data: data, encoding: .utf8) ?? "{}"
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(payload, forType: .string)

            historyActionMessage = "History export copied to clipboard."
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func askLatestConversation(question: String) async {
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            errorMessage = "Question is required."
            return
        }
        guard let latestEvent else {
            errorMessage = "No listening event is ready for conversation."
            return
        }
        guard !isAskingConversation else { return }
        isAskingConversation = true
        defer { isAskingConversation = false }
        do {
            latestConversation = try await client.askConversation(
                event: latestEvent,
                conversationId: latestConversation?.conversationId,
                question: trimmed
            )
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createGenerationPrompt(prompt: String? = nil) async {
        let trimmed = prompt?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let latestEvent else {
            errorMessage = "No listening event is ready for generation prompting."
            return
        }
        guard !isCreatingGenerationPrompt else { return }
        isCreatingGenerationPrompt = true
        defer { isCreatingGenerationPrompt = false }
        do {
            latestGeneration = try await client.createGenerationPrompt(
                event: latestEvent,
                prompt: trimmed?.isEmpty == false ? trimmed : nil
            )
            generationActionMessage = "Prompt ready."
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshGenerationHistory() async {
        do {
            latestGenerationHistory = try await client.generationHistory(limit: 5)
            let count = latestGenerationHistory?.recordCount ?? latestGenerationHistory?.records?.count ?? 0
            generationActionMessage = "\(count) prompt record\(count == 1 ? "" : "s")."
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func archiveRecentHistory(eventIds: [String], label: String? = nil) async {
        let ids = uniqueEventIds(eventIds)
        do {
            let response = try await client.archiveHistory(eventIds: ids, label: label)
            latestHistoryArchive = response
            if let path = response.archivePath {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(path, forType: .string)
            }
            let count = response.eventCount ?? ids.count
            historyActionMessage = "Archived \(count) result\(count == 1 ? "" : "s"); path copied."
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func registerConfiguredHotkeyIfNeeded() {
        guard !hasRegisteredConfiguredHotkey else { return }
        hasRegisteredConfiguredHotkey = true
        guard !shellHotkey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            hotkeyStatus = "No global hotkey"
            return
        }
        registerCaptureHotkey()
    }

    func registerCaptureHotkey() {
        let bindingText = shellHotkey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !bindingText.isEmpty else {
            GlobalHotkeyManager.shared.unregister()
            hotkeyStatus = "No global hotkey"
            return
        }
        switch GlobalHotkeyManager.shared.register(bindingText: bindingText, action: { [weak self] in
            Task { @MainActor in
                await self?.capture()
            }
        }) {
        case .registered(let display):
            hotkeyStatus = "Registered \(display)"
        case .invalid(let reason):
            hotkeyStatus = reason
        case .failed(let status):
            hotkeyStatus = "Hotkey failed: \(status)"
        }
    }

    private func mutateBackground(_ operation: () async throws -> BackgroundStatusResponse) async {
        do {
            applyBackgroundStatus(try await operation())
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func applyBackgroundStatus(_ status: BackgroundStatusResponse?) {
        guard let status else { return }
        let previousEventId = latestEvent?.id
        let nextEvent = status.state.latestEvent ?? latestEvent
        background = status
        latestEvent = nextEvent
        if nextEvent?.id != previousEventId {
            resetConversation()
        }
        pinnedEvents = status.state.pinnedEvents ?? pinnedEvents
        recentEvents = status.state.recentEvents ?? recentEvents
    }

    private func applyHistory(_ history: BackgroundHistoryResponse?) {
        guard let history else { return }
        latestEvent = history.latestEvent
        resetConversation()
        pinnedEvents = history.pinnedEvents ?? []
        recentEvents = history.recentEvents ?? []
    }

    private func resetConversation() {
        latestConversation = nil
        latestGeneration = nil
        latestGenerationHistory = nil
        generationActionMessage = nil
    }

    private func uniqueEventIds(_ ids: [String]) -> [String] {
        var unique: [String] = []
        var seen: Set<String> = []
        for id in ids {
            let trimmed = id.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty, !seen.contains(trimmed) else { continue }
            seen.insert(trimmed)
            unique.append(trimmed)
        }
        return unique
    }

    private func appendDaemonLog(_ line: String) {
        daemonLogLines.append(line)
        if daemonLogLines.count > 40 {
            daemonLogLines.removeFirst(daemonLogLines.count - 40)
        }
    }

    private func byteCountLabel(_ bytes: Int?) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes ?? 0), countStyle: .file)
    }

    private func formatDays(_ value: Double) -> String {
        if value.rounded() == value {
            let days = Int(value)
            return "\(days)d"
        }
        return String(format: "%.1fd", value)
    }
}

private enum Defaults {
    static let daemonBaseURL = "hmm.daemonBaseURL"
    static let captureHotkey = "hmm.captureHotkey"
}
