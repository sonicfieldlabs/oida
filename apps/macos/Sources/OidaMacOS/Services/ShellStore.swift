import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

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
    /// Session-scoped reading shown by the floating listener. Persisted
    /// background history still hydrates `latestEvent` for the control center,
    /// but must not fill a newly launched floating panel with an old result.
    @Published private(set) var floatingEvent: ListeningEventSummary?
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
    @Published var listenHotkey: String {
        didSet {
            UserDefaults.standard.set(listenHotkey, forKey: Defaults.captureHotkey)
        }
    }
    @Published var toggleHotkey: String {
        didSet {
            UserDefaults.standard.set(toggleHotkey, forKey: Defaults.toggleHotkey)
        }
    }
    @Published var hotkeyStatus = "No global hotkey"
    @Published private(set) var listeningPhase = ListeningPhase.idle
    @Published private(set) var listeningProgress = 0.0
    @Published private(set) var listeningSecondsRemaining: Double?
    @Published private(set) var listeningStatusText = "Ready"
    @Published var micLevel: Double = 0
    @Published var presets: [RoutePresetModel] = []
    @Published var customSkillIDs: [String]?
    @Published var musicIDEnabled: Bool {
        didSet {
            UserDefaults.standard.set(musicIDEnabled, forKey: Defaults.musicIDEnabled)
        }
    }
    @Published var appearanceMode: String {
        didSet {
            let normalized = appearanceMode == "dark" ? "dark" : "light"
            UserDefaults.standard.set(normalized, forKey: Defaults.appearanceMode)
            floatingPanel?.setAppearance(
                NSAppearance(named: normalized == "dark" ? .darkAqua : .aqua)
            )
        }
    }
    @Published var currentSessionName = ""
    @Published var selectedSource: String {
        didSet {
            UserDefaults.standard.set(selectedSource, forKey: Defaults.listenSource)
        }
    }
    @Published var selectedPreset: String {
        didSet {
            UserDefaults.standard.set(selectedPreset, forKey: Defaults.routePreset)
            if oldValue != selectedPreset {
                customSkillIDs = nil
            }
        }
    }
    /// Temporal direction of the listen gesture (spec v1.2 capture):
    /// "past" slices what the ring buffer already heard before the trigger;
    /// "future" records the window after it.
    @Published var selectedDirection: String {
        didSet {
            UserDefaults.standard.set(selectedDirection, forKey: Defaults.listenDirection)
        }
    }
    @Published var selectedCaptureSeconds: Double {
        didSet {
            UserDefaults.standard.set(selectedCaptureSeconds, forKey: Defaults.captureSeconds)
        }
    }

    private var client: DaemonClient
    private let supervisor = DaemonSupervisor()
    private let systemAudioTap = SystemAudioTapManager()
    private let micTap = MicTapManager()
    private var pollingTask: Task<Void, Never>?
    private var listenTask: Task<Void, Never>?
    private var micTapStartedAt: Date?
    private var floatingPanel: FloatingPanelController?
    private var hasBootstrapped = false
    private var hasEstablishedFloatingEventBaseline = false
    private var floatingEventBaselineId: String?
    private var hasRequestedLaunchPrewarm = false
    private var claimedCaptureRequestIds: Set<String> = []

    init() {
        let url = UserDefaults.standard.string(forKey: Defaults.daemonBaseURL) ?? "http://127.0.0.1:8765"
        daemonBaseURL = url
        listenHotkey = UserDefaults.standard.string(forKey: Defaults.captureHotkey) ?? "control+option+l"
        toggleHotkey = UserDefaults.standard.string(forKey: Defaults.toggleHotkey) ?? "control+option+h"
        selectedSource = UserDefaults.standard.string(forKey: Defaults.listenSource) ?? "system"
        selectedPreset = UserDefaults.standard.string(forKey: Defaults.routePreset) ?? "basic"
        customSkillIDs = nil
        musicIDEnabled = UserDefaults.standard.bool(forKey: Defaults.musicIDEnabled)
        appearanceMode = UserDefaults.standard.string(forKey: Defaults.appearanceMode) == "dark" ? "dark" : "light"
        selectedDirection = UserDefaults.standard.string(forKey: Defaults.listenDirection) ?? "past"
        let savedSeconds = UserDefaults.standard.double(forKey: Defaults.captureSeconds)
        selectedCaptureSeconds = savedSeconds > 0 ? savedSeconds : 10
        client = DaemonClient(baseURLString: url)
        micTap.onLevel = { [weak self] level in
            Task { @MainActor in
                self?.micLevel = level
            }
        }
        supervisor.onLogLine = { [weak self] line in
            self?.appendDaemonLog(line)
        }
        supervisor.onExit = { [weak self] status in
            self?.managedDaemonRunning = false
            self?.appendDaemonLog("Managed daemon exited with status \(status)")
        }
        systemAudioTap.onStateChange = { [weak self] state in
            self?.nativeSystemAudioState = state
            switch state {
            case .failed(let reason):
                self?.appendDaemonLog("System audio tap failed: \(reason)")
            case .unavailable(let reason):
                self?.appendDaemonLog("System audio tap unavailable: \(reason)")
            case .capturing:
                self?.appendDaemonLog("System audio tap ready")
            default:
                break
            }
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
        // The managed daemon's stdout/stderr are pipes into this app; if the app
        // quits without stopping it, the orphan dies on its next log write
        // (SIGPIPE). Stop it cleanly instead. Externally-started daemons are
        // never touched.
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.supervisor.stop()
            }
        }
    }

    var daemonOnline: Bool {
        health?.ok == true
    }

    var isListening: Bool {
        listeningPhase == .capturing
    }

    var isProcessing: Bool {
        listeningPhase == .processing
    }

    var isListenBusy: Bool {
        isListening || isProcessing
    }

    var engineState: String {
        health?.engine?.state ?? "unknown"
    }

    var engineLabel: String {
        switch engineState {
        case "ready": return "MOSS ready"
        case "warming": return "MOSS warming…"
        case "cold": return "MOSS cold"
        case "degraded": return "MOSS unavailable"
        case "stub": return "DSP only"
        default: return health?.engine?.profile ?? "engine"
        }
    }

    var preferredColorScheme: ColorScheme {
        appearanceMode == "dark" ? .dark : .light
    }

    var floatingStatusText: String {
        if !daemonOnline { return "daemon offline" }
        if isListening { return listeningStatusText }
        if isProcessing { return "operating listening…" }
        if engineState == "warming" { return "warming the ear" }
        if nativeSystemAudioActive { return "hearing the system" }
        return engineState == "ready" ? "idle · ready" : "idle · \(engineLabel.lowercased())"
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

    var floatingMemoryMatchCount: Int {
        floatingEvent?.memory?.similarTraceIds?.count ?? 0
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

    /// One-time app startup: hotkeys, polling, daemon supervision, system tap,
    /// and the floating listener. Everything converges on one daemon instance.
    func bootstrap() async {
        guard !hasBootstrapped else { return }
        hasBootstrapped = true
        registerHotkeys()
        startPolling()
        await refresh()
        if !daemonOnline {
            await startDaemon()
        }
        Task { @MainActor [weak self] in
            await self?.ensureEngineWarmOnLaunch()
        }
        if !nativeSystemAudioActive {
            await startNativeSystemAudioTap()
        }
        await loadPresets()
        showFloatingListener()
    }

    func shutdownManagedDaemon() {
        supervisor.stop()
    }

    /// The daemon normally prewarms from its lifespan hook. The shell also
    /// asks once at launch so attaching to an existing cold or previously
    /// degraded mac-mps daemon recovers without requiring a dashboard click.
    private func ensureEngineWarmOnLaunch() async {
        guard !hasRequestedLaunchPrewarm else { return }

        // `uv run --extra moss` may still be syncing on a first launch. Give
        // the managed daemon a short window to become reachable before the
        // one launch-time warm request.
        for _ in 0..<20 {
            if daemonOnline, let engine = health?.engine {
                guard engine.profile == "mac-mps" else {
                    hasRequestedLaunchPrewarm = true
                    return
                }
                let hasResidentModel = !(engine.loadedModels ?? []).isEmpty
                if engine.state == "warming" || (engine.state == "ready" && hasResidentModel) {
                    hasRequestedLaunchPrewarm = true
                    return
                }
                hasRequestedLaunchPrewarm = true
                do {
                    _ = try await client.warmEngine()
                    appendDaemonLog("Requested MOSS prewarm at app launch")
                    await refresh()
                } catch {
                    appendDaemonLog("Launch prewarm request failed: \(error.localizedDescription)")
                }
                return
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
            await refresh()
        }
        appendDaemonLog("Daemon did not become reachable for launch prewarm")
    }

    func loadPresets() async {
        guard let manifest = try? await client.akouoSkills() else { return }
        let visiblePresetIDs = Set(["basic", "field", "signal", "music", "voice", "deep"])
        let loaded = (manifest.routePresets ?? []).filter {
            $0.enabledByDefault != false && visiblePresetIDs.contains($0.id)
        }
        if !loaded.isEmpty {
            presets = loaded
            // Pre-v0.6 preset ids saved in UserDefaults follow the rename instead of
            // resetting to the first preset (mirrors the daemon's LEGACY_PRESET_ALIASES).
            let legacyAliases = ["environment": "field", "speech": "voice"]
            if let renamed = legacyAliases[selectedPreset], loaded.contains(where: { $0.id == renamed }) {
                selectedPreset = renamed
            }
            if !loaded.contains(where: { $0.id == selectedPreset }) {
                selectedPreset = loaded.first?.id ?? "basic"
            }
        }
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
            await claimPendingCaptureRequestIfAny(status.state.captureRequest)
        } catch {
            health = nil
            background = nil
            liveSignal = nil
            nativeSystemAudioTempStatus = nil
            // keep recentEvents/pinnedEvents: stale history beats an empty
            // panel during a transient refresh failure
            errorMessage = error.localizedDescription
            managedDaemonRunning = supervisor.isManagedRunning
            launchAtLoginStatus = LaunchAtLoginManager.statusLabel
        }
    }

    /// The web dashboard (or any surface) can file a system-capture request;
    /// this native shell is the only process that can actually hear the
    /// system output, so it claims and performs the capture.
    private func claimPendingCaptureRequestIfAny(_ request: CaptureRequestModel?) async {
        guard let request, !isListenBusy, !claimedCaptureRequestIds.contains(request.id) else { return }
        claimedCaptureRequestIds.insert(request.id)
        if claimedCaptureRequestIds.count > 32 {
            claimedCaptureRequestIds.removeAll()
            claimedCaptureRequestIds.insert(request.id)
        }
        guard let claim = try? await client.claimCaptureRequest(id: request.id), claim.claimed else {
            // A transient daemon/network error must not poison this request ID;
            // the next poll should be allowed to retry the atomic claim.
            claimedCaptureRequestIds.remove(request.id)
            return
        }
        let seconds = request.seconds
        let preset = request.routePreset
        let direction = request.direction
        let source = request.source ?? "system"
        customSkillIDs = request.enabledSkillIDs
        if let songID = request.songID {
            musicIDEnabled = songID
        }
        Task { @MainActor [weak self] in
            await self?.listenNow(
                seconds: seconds,
                preset: preset,
                source: source,
                direction: direction,
                enabledSkillIDs: request.enabledSkillIDs,
                musicID: request.songID
            )
        }
    }

    /// The one listen gesture, source-aware and cancellable. Used by the
    /// floating listener, the global hotkey, and dashboard capture requests.
    /// System/mic wait for the ring buffer to fill; Stop cancels the wait.
    func listenNow(
        seconds: Double? = nil,
        preset: String? = nil,
        source: String? = nil,
        direction: String? = nil,
        enabledSkillIDs: [String]? = nil,
        musicID: Bool? = nil
    ) async {
        guard !isListenBusy else { return }
        let captureSeconds = max(0.25, seconds ?? selectedCaptureSeconds)
        let routePreset = preset ?? selectedPreset
        let listenSource = source ?? selectedSource
        let requestedDirection = direction ?? selectedDirection
        let captureDirection = requestedDirection == "future" ? "future" : "past"
        let listenSkillIDs = enabledSkillIDs ?? customSkillIDs
        let useMusicID = (musicID ?? musicIDEnabled) && routePreset == "music"
        selectedCaptureSeconds = captureSeconds
        selectedPreset = routePreset
        selectedSource = listenSource
        selectedDirection = captureDirection
        beginListening(captureSeconds: captureSeconds, direction: captureDirection, source: listenSource)
        listenTask = Task { @MainActor [weak self] in
            await self?.performListen(
                seconds: captureSeconds,
                preset: routePreset,
                source: listenSource,
                direction: captureDirection,
                enabledSkillIDs: listenSkillIDs,
                musicID: useMusicID
            )
        }
        let task = listenTask
        await task?.value
        listenTask = nil
        if task?.isCancelled == true {
            finishListeningAsIdle(status: "Stopped")
            return
        }
        if listeningPhase == .result {
            try? await Task.sleep(nanoseconds: 800_000_000)
        } else if listeningPhase == .failed {
            try? await Task.sleep(nanoseconds: 1_200_000_000)
        }
        if !isListenBusy {
            finishListeningAsIdle(status: listeningPhase == .result ? "Result ready" : listeningStatusText)
        }
    }

    func stopListening() {
        guard isListening else { return }
        listenTask?.cancel()
        appendDaemonLog("Listening capture stopped by the user")
        finishListeningAsIdle(status: "Stopped")
    }

    private func beginListening(captureSeconds: Double, direction: String, source: String) {
        listeningPhase = .capturing
        listeningProgress = 0
        listeningSecondsRemaining = captureSeconds
        listeningStatusText = "Hearing · \(Int(ceil(captureSeconds)))s"
        errorMessage = nil
        appendDaemonLog("Listening started: \(source), \(direction), \(captureSeconds.formatted()) s, preset \(selectedPreset)")
    }

    private func beginProcessing(source: String) {
        listeningPhase = .processing
        listeningProgress = 1
        listeningSecondsRemaining = nil
        listeningStatusText = "Operating listening…"
        appendDaemonLog("Capture complete; operating listening on \(source) with \(selectedPreset)")
    }

    private func completeListening(_ event: ListeningEventSummary?) {
        listeningPhase = .result
        listeningProgress = 1
        listeningSecondsRemaining = nil
        listeningStatusText = "Result ready"
        if let event {
            appendDaemonLog("Listening result ready: \(event.id)")
        } else {
            appendDaemonLog("Listening completed without a result payload")
        }
    }

    private func failListening(_ message: String) {
        listeningPhase = .failed
        listeningProgress = 0
        listeningSecondsRemaining = nil
        listeningStatusText = "Listen failed"
        errorMessage = message
        appendDaemonLog("Listening failed: \(message)")
    }

    private func finishListeningAsIdle(status: String) {
        listeningPhase = .idle
        listeningProgress = 0
        listeningSecondsRemaining = nil
        listeningStatusText = status
    }

    private func performListen(
        seconds: Double,
        preset: String,
        source: String,
        direction: String,
        enabledSkillIDs: [String]?,
        musicID: Bool
    ) async {
        switch source {
        case "mic":
            await listenFromMic(captureSeconds: seconds, preset: preset, direction: direction, enabledSkillIDs: enabledSkillIDs, musicID: musicID)
        case "file":
            await listenFromFile(preset: preset, enabledSkillIDs: enabledSkillIDs, musicID: musicID)
        default:
            await listenFromSystem(captureSeconds: seconds, preset: preset, direction: direction, enabledSkillIDs: enabledSkillIDs, musicID: musicID)
        }
    }

    /// Direction-aware wait before slicing the ring (spec v1.2 capture):
    /// - past: take what the buffer already heard — wait only a short grace
    ///   when the tap just opened and holds nothing yet (an ear that just
    ///   opened has no past).
    /// - future: the window starts at the trigger — wait the full length,
    ///   then the last N seconds are exactly [trigger, trigger + N].
    private func waitForDirection(_ direction: String, captureSeconds: Double, buffered: Double) async -> Bool {
        let target = max(0.25, captureSeconds)
        let alreadyBuffered = direction == "past" ? min(target, max(0, buffered)) : 0
        let missing = direction == "future" ? target : max(0, target - alreadyBuffered)
        if missing <= 0 {
            listeningProgress = 1
            listeningSecondsRemaining = 0
            return !Task.isCancelled
        }
        let startedAt = Date()
        while !Task.isCancelled {
            let elapsed = Date().timeIntervalSince(startedAt)
            if elapsed >= missing { break }
            let captured = min(target, alreadyBuffered + elapsed)
            listeningProgress = min(1, captured / target)
            listeningSecondsRemaining = max(0, missing - elapsed)
            listeningStatusText = "Hearing · \(Int(ceil(max(0, missing - elapsed))))s"
            do {
                try await Task.sleep(nanoseconds: 200_000_000)
            } catch {
                return false
            }
        }
        guard !Task.isCancelled else { return false }
        listeningProgress = 1
        listeningSecondsRemaining = 0
        return true
    }

    private func listenFromSystem(captureSeconds: Double, preset: String, direction: String, enabledSkillIDs: [String]?, musicID: Bool) async {
        if !nativeSystemAudioActive {
            await startNativeSystemAudioTap()
            guard nativeSystemAudioActive else {
                failListening(nativeSystemAudioState.label)
                return
            }
        }
        guard await waitForDirection(direction, captureSeconds: captureSeconds, buffered: bufferedSeconds()) else { return }
        beginProcessing(source: "system audio")
        await analyzeNativeSystemAudio(
            seconds: captureSeconds,
            preset: preset,
            direction: direction,
            enabledSkillIDs: enabledSkillIDs,
            musicID: musicID
        )
    }

    private func listenFromMic(captureSeconds: Double, preset: String, direction: String, enabledSkillIDs: [String]?, musicID: Bool) async {
        if !micTap.isCapturing {
            do {
                try micTap.start()
                micTapStartedAt = Date()
            } catch {
                failListening(error.localizedDescription)
                return
            }
        }
        guard await waitForDirection(direction, captureSeconds: captureSeconds, buffered: micTap.bufferedSeconds) else { return }
        beginProcessing(source: "microphone audio")
        do {
            let output = try micTap.writeRecentAudio(seconds: captureSeconds)
            let response = try await client.listenEvent(
                path: output.path,
                routePreset: preset,
                sourceType: "live_input",
                sourceLabel: "Native microphone",
                privacyMode: "ephemeral",
                rawAudioPolicy: "temp",
                captureDirection: direction,
                captureSeconds: captureSeconds,
                captureTrigger: "floating-listener",
                enabledSkillIDs: enabledSkillIDs,
                songID: musicID
            )
            latestRouteComparison = nil
            presentFloatingEvent(response.listeningEvent)
            completeListening(response.listeningEvent)
            resetConversation()
            applyBackgroundStatus(response.background)
            errorMessage = nil
            await refresh()
        } catch is CancellationError {
            return
        } catch {
            failListening(error.localizedDescription)
        }
    }

    private func listenFromFile(preset: String, enabledSkillIDs: [String]?, musicID: Bool) async {
        let panel = NSOpenPanel()
        panel.title = "Choose audio to listen to"
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if #available(macOS 13.0, *) {
            panel.allowedContentTypes = [.audio, .movie]
        }
        NSApp.activate(ignoringOtherApps: true)
        listeningStatusText = "Choose an audio file"
        guard panel.runModal() == .OK, let url = panel.url else {
            finishListeningAsIdle(status: "Cancelled")
            return
        }
        beginProcessing(source: url.lastPathComponent)
        do {
            let response = try await client.listenEvent(
                path: url.path,
                routePreset: preset,
                enabledSkillIDs: enabledSkillIDs,
                songID: musicID
            )
            latestRouteComparison = nil
            presentFloatingEvent(response.listeningEvent)
            completeListening(response.listeningEvent)
            resetConversation()
            applyBackgroundStatus(response.background)
            errorMessage = nil
            await refresh()
        } catch is CancellationError {
            return
        } catch {
            failListening(error.localizedDescription)
        }
    }

    func stopMicTap() {
        micTap.stop()
        micTapStartedAt = nil
        micLevel = 0
    }

    private func bufferedSeconds() -> Double {
        nativeSystemAudioUpdatedAt == nil ? 0 : ringBufferedSecondsEstimate
    }

    // The tap keeps up to MicTapManager.ringCapacitySeconds; after the first
    // snapshot we assume the ring is filling in real time from tap start.
    private var tapStartedAt: Date?
    private var ringBufferedSecondsEstimate: Double {
        guard let tapStartedAt else { return 0 }
        return min(MicTapManager.ringCapacitySeconds, Date().timeIntervalSince(tapStartedAt))
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

    func openSystemAudioCaptureSettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") else {
            errorMessage = SystemAudioTapManager.capturePermissionMessage
            return
        }
        NSWorkspace.shared.open(url)
        appendDaemonLog("Opened Screen & System Audio Recording privacy settings")
    }

    func startNativeSystemAudioTap() async {
        await systemAudioTap.start()
        if nativeSystemAudioState == .capturing, tapStartedAt == nil {
            tapStartedAt = Date()
        }
    }

    func stopNativeSystemAudioTap() async {
        await systemAudioTap.stop()
        tapStartedAt = nil
        nativeSystemAudioBands = []
        nativeSystemAudioRMS = 0
        nativeSystemAudioPeak = 0
        nativeSystemAudioSampleRate = nil
        nativeSystemAudioChannelCount = nil
        nativeSystemAudioUpdatedAt = nil
        nativeSystemAudioRoute = nil
    }

    func analyzeNativeSystemAudio(
        remember: Bool = false,
        seconds: Double? = nil,
        preset: String? = nil,
        direction: String? = nil,
        enabledSkillIDs: [String]? = nil,
        musicID: Bool? = nil
    ) async {
        guard !isAnalyzingNativeSystemAudio else { return }
        guard nativeSystemAudioActive else {
            failListening("Start the native system audio tap before analyzing system output.")
            return
        }
        isAnalyzingNativeSystemAudio = true
        defer { isAnalyzingNativeSystemAudio = false }
        do {
            let capture = try systemAudioTap.writeRecentAudio(seconds: seconds ?? defaultCaptureSeconds)
            latestNativeSystemAudioTempPath = capture.path.path
            let response = try await client.analyzeNativeSystemAudio(
                path: capture.path.path,
                durationSeconds: capture.durationSeconds,
                routePreset: preset ?? defaultRoutePreset,
                remember: remember,
                sourceRoute: capture.sourceRoute,
                captureDirection: direction ?? selectedDirection,
                captureTrigger: "oida-listener",
                enabledSkillIDs: enabledSkillIDs ?? customSkillIDs,
                songID: (musicID ?? musicIDEnabled) && (preset ?? defaultRoutePreset) == "music"
            )
            latestRouteComparison = nil
            presentFloatingEvent(response.listeningEvent)
            completeListening(response.listeningEvent)
            resetConversation()
            applyBackgroundStatus(response.background)
            nativeSystemAudioRoute = response.sourceRoute ?? nativeSystemAudioRoute
            nativeSystemAudioTempStatus = response.retentionStatus ?? response.retentionCleanup?.status ?? nativeSystemAudioTempStatus
            historyActionMessage = nil
            errorMessage = nil
            await refresh()
        } catch is CancellationError {
            return
        } catch {
            failListening(error.localizedDescription)
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
            presentFloatingEvent(response.listeningEvent ?? response.background?.state.latestEvent)
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
            presentFloatingEvent(response.listeningEvent)
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

    // MARK: - Floating listener panel

    func toggleFloatingListener() {
        ensureFloatingPanel().toggle()
    }

    func showFloatingListener() {
        ensureFloatingPanel().show()
    }

    @discardableResult
    func renameFloatingEvent(to requestedTitle: String) async -> Bool {
        let title = requestedTitle
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return false }
        guard let event = floatingEvent else { return false }
        guard title != event.aggregate?.title else { return true }
        guard let sessionId = background?.state.activeSession?.id ?? background?.state.activeLiveSessionId,
              !sessionId.isEmpty else {
            errorMessage = "No active listening session is available for renaming this result."
            appendDaemonLog("Listening result rename failed: no active session")
            return false
        }
        do {
            let response = try await client.renameListeningEvent(
                sessionId: sessionId,
                eventId: event.id,
                title: title
            )
            presentFloatingEvent(response.listeningEvent)
            recentEvents = recentEvents.map { $0.id == event.id ? response.listeningEvent : $0 }
            pinnedEvents = pinnedEvents.map { $0.id == event.id ? response.listeningEvent : $0 }
            errorMessage = nil
            appendDaemonLog("Listening result renamed: \(title)")
            return true
        } catch {
            errorMessage = error.localizedDescription
            appendDaemonLog("Listening result rename failed: \(error.localizedDescription)")
            return false
        }
    }

    private func ensureFloatingPanel() -> FloatingPanelController {
        if let floatingPanel {
            return floatingPanel
        }
        let controller = FloatingPanelController { [weak self] in
            guard let self else { return NSView() }
            return ListenerHostingView(rootView: FloatingListenerView().environmentObject(self))
        }
        controller.setAppearance(
            NSAppearance(named: appearanceMode == "dark" ? .darkAqua : .aqua)
        )
        floatingPanel = controller
        return controller
    }

    func openControlCenter() {
        NSApp.activate(ignoringOtherApps: true)
        for window in NSApp.windows where window.identifier?.rawValue.contains("main") == true {
            window.makeKeyAndOrderFront(nil)
            return
        }
        NSApp.windows.first(where: { $0.canBecomeMain })?.makeKeyAndOrderFront(nil)
    }

    // MARK: - Hotkeys

    private enum HotkeyID {
        static let listen: UInt32 = 1
        static let toggle: UInt32 = 2
    }

    func registerHotkeys() {
        var parts: [String] = []
        switch GlobalHotkeyManager.shared.register(id: HotkeyID.listen, bindingText: listenHotkey, action: { [weak self] in
            Task { @MainActor in
                await self?.listenNow()
            }
        }) {
        case .registered(let display):
            parts.append("listen \(display)")
        case .invalid(let reason):
            parts.append("listen: \(reason)")
        case .failed(let status):
            parts.append("listen failed (\(status))")
        }
        switch GlobalHotkeyManager.shared.register(id: HotkeyID.toggle, bindingText: toggleHotkey, action: { [weak self] in
            self?.toggleFloatingListener()
        }) {
        case .registered(let display):
            parts.append("panel \(display)")
        case .invalid(let reason):
            parts.append("panel: \(reason)")
        case .failed(let status):
            parts.append("panel failed (\(status))")
        }
        hotkeyStatus = parts.joined(separator: " · ")
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
        if !hasEstablishedFloatingEventBaseline {
            // The first status refresh establishes where persisted history
            // ended before this app session. It deliberately does not present
            // that historical reading in the floating listener.
            hasEstablishedFloatingEventBaseline = true
            floatingEventBaselineId = nextEvent?.id
        } else if nextEvent?.id != floatingEventBaselineId {
            // A genuinely new event may arrive from the dashboard, MCP, or a
            // second Oida surface. Keep cross-surface sync after the blank
            // launch state without resurrecting the startup history item.
            floatingEventBaselineId = nextEvent?.id
            if let nextEvent {
                floatingEvent = nextEvent
            }
        }
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

    private func presentFloatingEvent(_ event: ListeningEventSummary?) {
        guard let event else { return }
        latestEvent = event
        floatingEvent = event
        floatingEventBaselineId = event.id
        hasEstablishedFloatingEventBaseline = true
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
    static let daemonBaseURL = "oida.daemonBaseURL"
    static let captureHotkey = "oida.captureHotkey"
    static let toggleHotkey = "oida.toggleHotkey"
    static let listenSource = "oida.listenSource"
    static let routePreset = "oida.routePreset"
    static let listenDirection = "oida.listenDirection"
    static let captureSeconds = "oida.captureSeconds"
    static let musicIDEnabled = "oida.musicIDEnabled"
    static let appearanceMode = "oida.appearanceMode"
}
