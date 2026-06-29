import Foundation

struct HealthResponse: Codable {
    let ok: Bool
    let name: String
    let legacyName: String?
    let profile: String?
    let host: String?
    let port: Int?

    enum CodingKeys: String, CodingKey {
        case ok
        case name
        case legacyName = "legacy_name"
        case profile
        case host
        case port
    }
}

struct BackgroundStatusResponse: Codable {
    let version: String?
    let mode: String?
    let config: BackgroundConfigModel
    let state: BackgroundStateModel
    let capabilities: BackgroundCapabilities?
    let notes: [String]?
}

struct BackgroundConfigModel: Codable {
    let enabled: Bool?
    let paused: Bool?
    let launchAtLogin: Bool?
    let showFloatingAgent: Bool?
    let floatingAgent: FloatingAgentConfig?
    let defaultCaptureSeconds: Double?
    let defaultRoutePreset: String?
    let incognito: Bool?
    let saveEventsByDefault: Bool?
    let hotkeys: [String: String?]?
    let nativeTempAudioRetention: NativeTempAudioRetention?
    let recentHistory: RecentHistoryConfig?

    enum CodingKeys: String, CodingKey {
        case enabled
        case paused
        case launchAtLogin = "launch_at_login"
        case showFloatingAgent = "show_floating_agent"
        case floatingAgent = "floating_agent"
        case defaultCaptureSeconds = "default_capture_seconds"
        case defaultRoutePreset = "default_route_preset"
        case incognito
        case saveEventsByDefault = "save_events_by_default"
        case hotkeys
        case nativeTempAudioRetention = "native_temp_audio_retention"
        case recentHistory = "recent_history"
    }
}

struct RecentHistoryConfig: Codable {
    let enabled: Bool?
    let persist: Bool?
    let maxEvents: Int?
    let maxPinned: Int?
    let includeIncognito: Bool?

    enum CodingKeys: String, CodingKey {
        case enabled
        case persist
        case maxEvents = "max_events"
        case maxPinned = "max_pinned"
        case includeIncognito = "include_incognito"
    }
}

struct NativeTempAudioRetention: Codable {
    let policy: String?
    let deleteAfterDays: Double?
    let maxFiles: Int?
    let deleteAfterAnalysis: Bool?

    enum CodingKeys: String, CodingKey {
        case policy
        case deleteAfterDays = "delete_after_days"
        case maxFiles = "max_files"
        case deleteAfterAnalysis = "delete_after_analysis"
    }
}

struct FloatingAgentConfig: Codable {
    let visible: Bool?
    let size: String?
    let pinned: Bool?
    let x: Double?
    let y: Double?
    let reducedMotion: Bool?

    enum CodingKeys: String, CodingKey {
        case visible
        case size
        case pinned
        case x
        case y
        case reducedMotion = "reduced_motion"
    }
}

struct BackgroundStateModel: Codable {
    let activeLiveSessionId: String?
    let status: String?
    let updatedAt: String?
    let lastActionId: String?
    let lastError: String?
    let latestEvent: ListeningEventSummary?
    let pinnedEvents: [ListeningEventSummary]?
    let recentEvents: [ListeningEventSummary]?

    enum CodingKeys: String, CodingKey {
        case activeLiveSessionId = "active_live_session_id"
        case status
        case updatedAt = "updated_at"
        case lastActionId = "last_action_id"
        case lastError = "last_error"
        case latestEvent = "latest_event"
        case pinnedEvents = "pinned_events"
        case recentEvents = "recent_events"
    }
}

struct BackgroundCapabilities: Codable {
    let daemonBackgroundRuntime: Bool?
    let quickCaptureApi: Bool?
    let nativeTray: Bool?
    let globalHotkeys: Bool?
    let launchAtLogin: Bool?
    let desktopShellRequired: Bool?
    let desktopShellTarget: String?
    let nativeShellApi: Bool?
    let liveSignalApi: Bool?
    let routeRerunApi: Bool?
    let recentResultHistory: Bool?
    let pinnedRecentResults: Bool?
    let recentHistoryManagement: Bool?
    let recentHistoryArchive: Bool?
    let recentHistoryBatchReview: Bool?
    let generationPromptApi: Bool?
    let generationRelistenApi: Bool?
    let daemonSupervision: String?
    let nativeSystemAudioSignalTap: Bool?
    let nativeSystemAudioTempAnalysis: Bool?
    let nativeTempAudioCleanup: Bool?

    enum CodingKeys: String, CodingKey {
        case daemonBackgroundRuntime = "daemon_background_runtime"
        case quickCaptureApi = "quick_capture_api"
        case nativeTray = "native_tray"
        case globalHotkeys = "global_hotkeys"
        case launchAtLogin = "launch_at_login"
        case desktopShellRequired = "desktop_shell_required"
        case desktopShellTarget = "desktop_shell_target"
        case nativeShellApi = "native_shell_api"
        case liveSignalApi = "live_signal_api"
        case routeRerunApi = "route_rerun_api"
        case recentResultHistory = "recent_result_history"
        case pinnedRecentResults = "pinned_recent_results"
        case recentHistoryManagement = "recent_history_management"
        case recentHistoryArchive = "recent_history_archive"
        case recentHistoryBatchReview = "recent_history_batch_review"
        case generationPromptApi = "generation_prompt_api"
        case generationRelistenApi = "generation_relisten_api"
        case daemonSupervision = "daemon_supervision"
        case nativeSystemAudioSignalTap = "native_system_audio_signal_tap"
        case nativeSystemAudioTempAnalysis = "native_system_audio_temp_analysis"
        case nativeTempAudioCleanup = "native_temp_audio_cleanup"
    }
}

struct BackgroundHistoryResponse: Codable {
    let version: String?
    let limit: Int?
    let pinnedLimit: Int?
    let persistent: Bool?
    let historyPath: String?
    let rawAudioPolicy: String?
    let latestEvent: ListeningEventSummary?
    let pinnedEvents: [ListeningEventSummary]?
    let recentEvents: [ListeningEventSummary]?
    let selectedEvents: [ListeningEventSummary]?
    let selectedEventIds: [String]?
    let missingEventIds: [String]?
    let counts: BackgroundHistoryCounts?
    let exportedAt: String?
    let exportKind: String?
    let archiveKind: String?
    let archivedAt: String?
    let archiveLabel: String?
    let archivePath: String?

    enum CodingKeys: String, CodingKey {
        case version
        case limit
        case pinnedLimit = "pinned_limit"
        case persistent
        case historyPath = "history_path"
        case rawAudioPolicy = "raw_audio_policy"
        case latestEvent = "latest_event"
        case pinnedEvents = "pinned_events"
        case recentEvents = "recent_events"
        case selectedEvents = "selected_events"
        case selectedEventIds = "selected_event_ids"
        case missingEventIds = "missing_event_ids"
        case counts
        case exportedAt = "exported_at"
        case exportKind = "export_kind"
        case archiveKind = "archive_kind"
        case archivedAt = "archived_at"
        case archiveLabel = "archive_label"
        case archivePath = "archive_path"
    }
}

struct BackgroundHistoryCounts: Codable {
    let pinned: Int?
    let recent: Int?
    let selected: Int?
    let missing: Int?
    let totalStoredPinned: Int?
    let totalStoredRecent: Int?

    enum CodingKeys: String, CodingKey {
        case pinned
        case recent
        case selected
        case missing
        case totalStoredPinned = "total_stored_pinned"
        case totalStoredRecent = "total_stored_recent"
    }
}

struct BackgroundHistoryMutationResponse: Codable {
    let version: String?
    let eventId: String?
    let eventIds: [String]?
    let pinnedEventIds: [String]?
    let missingEventIds: [String]?
    let pinned: Bool?
    let cleared: Bool?
    let keepPinned: Bool?
    let background: BackgroundStatusResponse?
    let history: BackgroundHistoryResponse?

    enum CodingKeys: String, CodingKey {
        case version
        case eventId = "event_id"
        case eventIds = "event_ids"
        case pinnedEventIds = "pinned_event_ids"
        case missingEventIds = "missing_event_ids"
        case pinned
        case cleared
        case keepPinned = "keep_pinned"
        case background
        case history
    }
}

struct BackgroundHistoryArchiveResponse: Codable {
    let version: String?
    let archived: Bool?
    let archivePath: String?
    let archiveLabel: String?
    let eventCount: Int?
    let selectedEventIds: [String]?
    let rawAudioPolicy: String?
    let history: BackgroundHistoryResponse?

    enum CodingKeys: String, CodingKey {
        case version
        case archived
        case archivePath = "archive_path"
        case archiveLabel = "archive_label"
        case eventCount = "event_count"
        case selectedEventIds = "selected_event_ids"
        case rawAudioPolicy = "raw_audio_policy"
        case history
    }
}

struct CaptureResponse: Codable {
    let actionId: String?
    let listeningEvent: ListeningEventSummary?
    let trace: MemoryTraceSummary?
    let background: BackgroundStatusResponse?

    enum CodingKeys: String, CodingKey {
        case actionId = "action_id"
        case listeningEvent = "listening_event"
        case trace
        case background
    }
}

struct RouteRerunResponse: Codable {
    let routeRerun: RouteRerunSummary?
    let routeComparison: RouteComparisonSummary?
    let trace: MemoryTraceSummary?
    let listeningEvent: ListeningEventSummary?
    let background: BackgroundStatusResponse?

    enum CodingKeys: String, CodingKey {
        case routeRerun = "route_rerun"
        case routeComparison = "route_comparison"
        case trace
        case listeningEvent = "listening_event"
        case background
    }
}

struct ConversationAskResponse: Codable {
    let version: String?
    let mode: String?
    let conversationId: String?
    let eventId: String?
    let rawAudioPolicy: String?
    let forbiddenTopicsTriggered: [String]?
    let turn: ConversationTurn?

    enum CodingKeys: String, CodingKey {
        case version
        case mode
        case conversationId = "conversation_id"
        case eventId = "event_id"
        case rawAudioPolicy = "raw_audio_policy"
        case forbiddenTopicsTriggered = "forbidden_topics_triggered"
        case turn
    }
}

struct ConversationTurn: Codable, Identifiable {
    let id: String?
    let createdAt: String?
    let question: String?
    let answer: String?
    let knownFacts: [String]?
    let hypotheses: [String]?
    let evidence: [ConversationEvidence]?
    let uncertaintyNotes: [String]?
    let memoryContext: [ConversationMemoryContext]?
    let remoteModel: ConversationRemoteModel?

    enum CodingKeys: String, CodingKey {
        case id
        case createdAt = "created_at"
        case question
        case answer
        case knownFacts = "known_facts"
        case hypotheses
        case evidence
        case uncertaintyNotes = "uncertainty_notes"
        case memoryContext = "memory_context"
        case remoteModel = "remote_model"
    }
}

struct ConversationEvidence: Codable, Identifiable {
    let kind: String?
    let label: String?
    let value: String?

    var id: String { "\(kind ?? "event")-\(label ?? "")-\(value ?? "")" }
}

struct ConversationMemoryContext: Codable, Identifiable {
    let traceId: String?
    let title: String?
    let score: Double?
    let basis: String?

    var id: String { traceId ?? title ?? basis ?? "memory-context" }

    enum CodingKeys: String, CodingKey {
        case traceId = "trace_id"
        case title
        case score
        case basis
    }
}

struct ConversationRemoteModel: Codable {
    let enabled: Bool?
    let requested: Bool?
    let provider: String?
    let note: String?
}

struct GenerationRecord: Codable, Identifiable {
    let id: String
    let createdAt: String?
    let updatedAt: String?
    let sourceEventId: String?
    let status: String?
    let adapter: String?
    let intent: String?
    let prompt: String?
    let negativePrompt: String?
    let rawAudioPolicy: String?
    let generatedAudioPolicy: String?
    let notes: [String]?

    enum CodingKeys: String, CodingKey {
        case id
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case sourceEventId = "source_event_id"
        case status
        case adapter
        case intent
        case prompt
        case negativePrompt = "negative_prompt"
        case rawAudioPolicy = "raw_audio_policy"
        case generatedAudioPolicy = "generated_audio_policy"
        case notes
    }
}

struct GenerationHistoryResponse: Codable {
    let version: String?
    let recordCount: Int?
    let adapterDefault: String?
    let rawAudioPolicy: String?
    let records: [GenerationRecord]?

    enum CodingKeys: String, CodingKey {
        case version
        case recordCount = "record_count"
        case adapterDefault = "adapter_default"
        case rawAudioPolicy = "raw_audio_policy"
        case records
    }
}

struct RouteRerunSummary: Codable {
    let fromEventId: String?
    let routePreset: String?
    let path: String?
    let rawAudioPolicy: String?

    enum CodingKeys: String, CodingKey {
        case fromEventId = "from_event_id"
        case routePreset = "route_preset"
        case path
        case rawAudioPolicy = "raw_audio_policy"
    }
}

struct RouteComparisonSummary: Codable {
    let version: String?
    let baseEventId: String?
    let currentEventId: String?
    let sourceLabel: String?
    let sameSegment: Bool?
    let previousRoutes: [String]?
    let currentRoutes: [String]?
    let addedRoutes: [String]?
    let removedRoutes: [String]?
    let sharedRoutes: [String]?
    let summaryShift: RouteSummaryShift?
    let signalDelta: [String: RouteSignalDelta]?
    let warningDelta: RouteWarningDelta?
    let changeFlags: RouteChangeFlags?
    let appliedFilters: RouteAppliedFilters?
    let notes: [String]?

    enum CodingKeys: String, CodingKey {
        case version
        case baseEventId = "base_event_id"
        case currentEventId = "current_event_id"
        case sourceLabel = "source_label"
        case sameSegment = "same_segment"
        case previousRoutes = "previous_routes"
        case currentRoutes = "current_routes"
        case addedRoutes = "added_routes"
        case removedRoutes = "removed_routes"
        case sharedRoutes = "shared_routes"
        case summaryShift = "summary_shift"
        case signalDelta = "signal_delta"
        case warningDelta = "warning_delta"
        case changeFlags = "change_flags"
        case appliedFilters = "applied_filters"
        case notes
    }
}

struct RouteSummaryShift: Codable {
    let from: String?
    let to: String?
    let changed: Bool?
}

struct RouteSignalDelta: Codable {
    let label: String?
    let from: Double?
    let to: Double?
    let delta: Double?
}

struct RouteWarningDelta: Codable {
    let added: [String]?
    let resolved: [String]?
}

struct RouteChangeFlags: Codable {
    let routesChanged: Bool?
    let summaryChanged: Bool?
    let warningsChanged: Bool?
    let signalChanged: Bool?

    enum CodingKeys: String, CodingKey {
        case routesChanged = "routes_changed"
        case summaryChanged = "summary_changed"
        case warningsChanged = "warnings_changed"
        case signalChanged = "signal_changed"
    }
}

struct RouteAppliedFilters: Codable {
    let signalFields: [String]?
    let minAbsSignalDelta: Double?
    let changedOnly: Bool?

    enum CodingKeys: String, CodingKey {
        case signalFields = "signal_fields"
        case minAbsSignalDelta = "min_abs_signal_delta"
        case changedOnly = "changed_only"
    }
}

struct NativeSystemAudioAnalyzeResponse: Codable {
    let path: String?
    let rawAudioPolicy: String?
    let retention: String?
    let retentionPolicy: NativeTempAudioRetention?
    let retentionCleanup: NativeSystemAudioCleanupResponse?
    let retentionStatus: NativeSystemAudioTempStatusResponse?
    let sourceRoute: NativeSystemAudioRoutePayload?
    let trace: MemoryTraceSummary?
    let listeningEvent: ListeningEventSummary?
    let background: BackgroundStatusResponse?

    enum CodingKeys: String, CodingKey {
        case path
        case rawAudioPolicy = "raw_audio_policy"
        case retention
        case retentionPolicy = "retention_policy"
        case retentionCleanup = "retention_cleanup"
        case retentionStatus = "retention_status"
        case sourceRoute = "source_route"
        case trace
        case listeningEvent = "listening_event"
        case background
    }
}

struct NativeSystemAudioTempStatusResponse: Codable {
    let rawAudioPolicy: String?
    let directory: String?
    let pattern: String?
    let retention: NativeTempAudioRetention?
    let fileCount: Int?
    let bytes: Int?
    let files: [NativeSystemAudioTempFile]?

    enum CodingKeys: String, CodingKey {
        case rawAudioPolicy = "raw_audio_policy"
        case directory
        case pattern
        case retention
        case fileCount = "file_count"
        case bytes
        case files
    }
}

struct NativeSystemAudioCleanupResponse: Codable {
    let rawAudioPolicy: String?
    let directory: String?
    let pattern: String?
    let retention: NativeTempAudioRetention?
    let dryRun: Bool?
    let filesBefore: Int?
    let bytesBefore: Int?
    let deletedCount: Int?
    let deletedBytes: Int?
    let deleted: [NativeSystemAudioTempFile]?
    let errors: [NativeSystemAudioCleanupError]?
    let status: NativeSystemAudioTempStatusResponse?
    let background: BackgroundStatusResponse?

    enum CodingKeys: String, CodingKey {
        case rawAudioPolicy = "raw_audio_policy"
        case directory
        case pattern
        case retention
        case dryRun = "dry_run"
        case filesBefore = "files_before"
        case bytesBefore = "bytes_before"
        case deletedCount = "deleted_count"
        case deletedBytes = "deleted_bytes"
        case deleted
        case errors
        case status
        case background
    }
}

struct NativeSystemAudioTempFile: Codable, Identifiable {
    let path: String
    let name: String?
    let bytes: Int?
    let modifiedAt: String?
    let ageHours: Double?

    var id: String { path }

    enum CodingKeys: String, CodingKey {
        case path
        case name
        case bytes
        case modifiedAt = "modified_at"
        case ageHours = "age_hours"
    }
}

struct NativeSystemAudioCleanupError: Codable {
    let path: String?
    let error: String?
}

struct ListeningEventSummary: Codable, Identifiable {
    let id: String
    let createdAt: String?
    let source: AudioSourceSummary?
    let segment: ListeningSegmentSummary?
    let routes: [ListeningRouteSummary]?
    let aggregate: ListeningAggregateSummary?
    let memory: MemoryLinksSummary?
    let privacyMode: String?
    let rawAudioPolicy: String?

    enum CodingKeys: String, CodingKey {
        case id
        case createdAt = "created_at"
        case source
        case segment
        case routes
        case aggregate
        case memory
        case privacyMode = "privacy_mode"
        case rawAudioPolicy = "raw_audio_policy"
    }
}

struct ListeningAggregateSummary: Codable {
    let title: String?
    let shortSummary: String?
    let detailedSummary: String?
    let primaryTags: [String]?
    let warnings: [String]?
    let nextActions: [ListeningNextActionSummary]?

    enum CodingKeys: String, CodingKey {
        case title
        case shortSummary = "short_summary"
        case detailedSummary = "detailed_summary"
        case primaryTags = "primary_tags"
        case warnings
        case nextActions = "next_actions"
    }
}

struct ListeningNextActionSummary: Codable, Identifiable {
    let id: String
    let label: String?
    let routePreset: String?

    enum CodingKeys: String, CodingKey {
        case id
        case label
        case routePreset = "route_preset"
    }
}

struct ListeningRouteSummary: Codable, Identifiable {
    let routeId: String
    let routeName: String?
    let summary: String?
    let suggestedNextRoutes: [String]?

    var id: String { routeId }

    enum CodingKeys: String, CodingKey {
        case routeId = "route_id"
        case routeName = "route_name"
        case summary
        case suggestedNextRoutes = "suggested_next_routes"
    }
}

struct ListeningSegmentSummary: Codable {
    let durationMs: Int?
    let privacyMode: String?
    let ephemeral: Bool?
    let dataRef: AudioDataRefSummary?

    enum CodingKeys: String, CodingKey {
        case durationMs = "duration_ms"
        case privacyMode = "privacy_mode"
        case ephemeral
        case dataRef = "data_ref"
    }
}

struct AudioDataRefSummary: Codable {
    let kind: String?
    let uri: String?
    let sha256: String?
}

struct MemoryLinksSummary: Codable {
    let savedTraceId: String?
    let similarTraceIds: [String]?

    enum CodingKeys: String, CodingKey {
        case savedTraceId = "saved_trace_id"
        case similarTraceIds = "similar_trace_ids"
    }
}

struct MemoryTraceSummary: Codable {
    let id: String?
    let title: String?
}

struct LiveSignalResponse: Codable {
    let version: String?
    let sessionId: String
    let active: Bool
    let updatedAt: String?
    let source: AudioSourceSummary?
    let chunkCount: Int
    let recentChunkCount: Int?
    let ringSeconds: Double?
    let ringDurationSeconds: Double?
    let vadActive: Bool?
    let vadActiveRecentCount: Int?
    let latest: LiveSignalChunk?
    let bands: [Double]
    let peaks: [Double]
    let meter: LiveSignalMeter?

    enum CodingKeys: String, CodingKey {
        case version
        case sessionId = "session_id"
        case active
        case updatedAt = "updated_at"
        case source
        case chunkCount = "chunk_count"
        case recentChunkCount = "recent_chunk_count"
        case ringSeconds = "ring_seconds"
        case ringDurationSeconds = "ring_duration_s"
        case vadActive = "vad_active"
        case vadActiveRecentCount = "vad_active_recent_count"
        case latest
        case bands
        case peaks
        case meter
    }
}

struct AudioSourceSummary: Codable {
    let type: String?
    let label: String?
    let deviceId: String?
    let details: AudioSourceDetailsSummary?

    enum CodingKeys: String, CodingKey {
        case type
        case label
        case deviceId = "device_id"
        case details
    }
}

struct AudioSourceDetailsSummary: Codable {
    let captureScope: String?
    let captureAdapter: String?
    let sourceRoute: NativeSystemAudioRoutePayload?

    enum CodingKeys: String, CodingKey {
        case captureScope = "capture_scope"
        case captureAdapter = "capture_adapter"
        case sourceRoute = "source_route"
    }
}

struct LiveSignalChunk: Codable {
    let receivedAt: String?
    let durationSeconds: Double?
    let rmsDbfs: Double?
    let peakDbfs: Double?
    let vadActive: Bool?

    enum CodingKeys: String, CodingKey {
        case receivedAt = "received_at"
        case durationSeconds = "duration_s"
        case rmsDbfs = "rms_dbfs"
        case peakDbfs = "peak_dbfs"
        case vadActive = "vad_active"
    }
}

struct LiveSignalMeter: Codable {
    let rms: Double?
    let peak: Double?
    let basis: String?
}

struct BackgroundCapturePayload: Codable {
    let sessionId: String?
    let seconds: Double?
    let routePreset: String?
    let remember: Bool

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case seconds
        case routePreset = "route_preset"
        case remember
    }
}

struct RouteRerunPayload: Codable {
    let event: ListeningEventSummary
    let routePreset: String
    let remember: Bool

    enum CodingKeys: String, CodingKey {
        case event
        case routePreset = "route_preset"
        case remember
    }
}

struct ConversationAskPayload: Codable {
    let question: String
    let event: ListeningEventSummary?
    let conversationId: String?
    let includeMemory: Bool
    let allowRemoteModel: Bool
    let provider: String

    enum CodingKeys: String, CodingKey {
        case question
        case event
        case conversationId = "conversation_id"
        case includeMemory = "include_memory"
        case allowRemoteModel = "allow_remote_model"
        case provider
    }
}

struct GenerationPromptPayload: Codable {
    let event: ListeningEventSummary?
    let intent: String
    let prompt: String?
    let negativePrompt: String?
    let adapter: String
    let durationSeconds: Double?
    let generate: Bool

    enum CodingKeys: String, CodingKey {
        case event
        case intent
        case prompt
        case negativePrompt = "negative_prompt"
        case adapter
        case durationSeconds = "duration_s"
        case generate
    }
}

struct NativeSystemAudioAnalyzePayload: Codable {
    let path: String
    let routePreset: String?
    let privacyMode: String
    let sourceLabel: String
    let durationSeconds: Double?
    let remember: Bool
    let sourceRoute: NativeSystemAudioRoutePayload?

    enum CodingKeys: String, CodingKey {
        case path
        case routePreset = "route_preset"
        case privacyMode = "privacy_mode"
        case sourceLabel = "source_label"
        case durationSeconds = "duration_s"
        case remember
        case sourceRoute = "source_route"
    }
}

struct NativeSystemAudioRoutePayload: Codable {
    let routeId: String
    let captureScope: String
    let adapter: String
    let label: String
    let platform: String
    let displayId: UInt32?
    let displayWidth: Int?
    let displayHeight: Int?
    let excludedCurrentProcessAudio: Bool
    let excludedApplications: [String]

    enum CodingKeys: String, CodingKey {
        case routeId = "route_id"
        case captureScope = "capture_scope"
        case adapter
        case label
        case platform
        case displayId = "display_id"
        case displayWidth = "display_width"
        case displayHeight = "display_height"
        case excludedCurrentProcessAudio = "excluded_current_process_audio"
        case excludedApplications = "excluded_applications"
    }
}

struct NativeSystemAudioCleanupPayload: Codable {
    let deleteAll: Bool
    let dryRun: Bool

    enum CodingKeys: String, CodingKey {
        case deleteAll = "delete_all"
        case dryRun = "dry_run"
    }
}

struct BackgroundHistoryPinPayload: Codable {
    let eventId: String
    let pinned: Bool

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case pinned
    }
}

struct BackgroundHistoryBatchPinPayload: Codable {
    let eventIds: [String]
    let pinned: Bool

    enum CodingKeys: String, CodingKey {
        case eventIds = "event_ids"
        case pinned
    }
}

struct BackgroundHistoryClearPayload: Codable {
    let keepPinned: Bool

    enum CodingKeys: String, CodingKey {
        case keepPinned = "keep_pinned"
    }
}

struct BackgroundHistoryArchivePayload: Codable {
    let eventIds: [String]
    let label: String?

    enum CodingKeys: String, CodingKey {
        case eventIds = "event_ids"
        case label
    }
}

struct EmptyPayload: Codable {}

struct DaemonErrorEnvelope: Codable {
    let detail: String
}
