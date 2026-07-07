import Foundation

enum DaemonClientError: LocalizedError {
    case invalidBaseURL(String)
    case invalidURL(String)
    case requestFailed(Int, String)
    case emptyResponse

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL(let value):
            return "Invalid daemon URL: \(value)"
        case .invalidURL(let value):
            return "Invalid endpoint URL: \(value)"
        case .requestFailed(let statusCode, let detail):
            return "Daemon request failed (\(statusCode)): \(detail)"
        case .emptyResponse:
            return "Daemon returned an empty response."
        }
    }
}

struct DaemonClient {
    var baseURLString: String
    var session: URLSession = .shared

    private var baseURL: URL {
        get throws {
            guard let url = URL(string: baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                throw DaemonClientError.invalidBaseURL(baseURLString)
            }
            return url
        }
    }

    private var authToken: String? {
        let environment = ProcessInfo.processInfo.environment
        let token = environment["OIDA_AUTH_TOKEN"] ?? environment["HMM_AUTH_TOKEN"] ?? environment["AEAR_AUTH_TOKEN"]
        return token?.isEmpty == false ? token : nil
    }

    func health() async throws -> HealthResponse {
        try await get("/health")
    }

    func backgroundStatus() async throws -> BackgroundStatusResponse {
        try await get("/background/status")
    }

    func pause() async throws -> BackgroundStatusResponse {
        try await post("/background/pause", payload: EmptyPayload())
    }

    func resume() async throws -> BackgroundStatusResponse {
        try await post("/background/resume", payload: EmptyPayload())
    }

    func capture(seconds: Double?, routePreset: String?, remember: Bool) async throws -> CaptureResponse {
        let payload = BackgroundCapturePayload(
            sessionId: nil,
            seconds: seconds,
            routePreset: routePreset,
            remember: remember
        )
        return try await post("/background/capture", payload: payload)
    }

    func rerun(event: ListeningEventSummary, routePreset: String, remember: Bool = false) async throws -> RouteRerunResponse {
        let payload = RouteRerunPayload(event: event, routePreset: routePreset, remember: remember)
        return try await post("/listen-event/rerun", payload: payload)
    }

    func askConversation(event: ListeningEventSummary?, conversationId: String?, question: String) async throws -> ConversationAskResponse {
        let payload = ConversationAskPayload(
            question: question,
            event: event,
            conversationId: conversationId,
            includeMemory: true,
            allowRemoteModel: false,
            provider: "local_structured"
        )
        return try await post("/conversation/ask", payload: payload)
    }

    func createGenerationPrompt(event: ListeningEventSummary?, prompt: String? = nil) async throws -> GenerationRecord {
        let payload = GenerationPromptPayload(
            event: event,
            intent: "transform",
            prompt: prompt,
            negativePrompt: nil,
            adapter: "prompt_only",
            durationSeconds: nil,
            generate: false
        )
        return try await post("/generation/prompt", payload: payload)
    }

    func generationHistory(limit: Int = 10) async throws -> GenerationHistoryResponse {
        try await get("/generation/history?limit=\(limit)")
    }

    func exportHistory() async throws -> BackgroundHistoryResponse {
        try await get("/background/history/export")
    }

    func setHistoryPinned(eventId: String, pinned: Bool) async throws -> BackgroundHistoryMutationResponse {
        let payload = BackgroundHistoryPinPayload(eventId: eventId, pinned: pinned)
        return try await post("/background/history/pin", payload: payload)
    }

    func setHistoryPinned(eventIds: [String], pinned: Bool) async throws -> BackgroundHistoryMutationResponse {
        let payload = BackgroundHistoryBatchPinPayload(eventIds: eventIds, pinned: pinned)
        return try await post("/background/history/batch-pin", payload: payload)
    }

    func archiveHistory(eventIds: [String], label: String? = nil) async throws -> BackgroundHistoryArchiveResponse {
        let payload = BackgroundHistoryArchivePayload(eventIds: eventIds, label: label)
        return try await post("/background/history/archive", payload: payload)
    }

    func clearHistory(keepPinned: Bool) async throws -> BackgroundHistoryMutationResponse {
        let payload = BackgroundHistoryClearPayload(keepPinned: keepPinned)
        return try await post("/background/history/clear", payload: payload)
    }

    func liveSignal(sessionId: String, bands: Int = 14) async throws -> LiveSignalResponse {
        try await get("/live/signal/\(sessionId)?bands=\(bands)")
    }

    func analyzeNativeSystemAudio(
        path: String,
        durationSeconds: Double?,
        routePreset: String?,
        remember: Bool,
        sourceRoute: NativeSystemAudioRoutePayload?
    ) async throws -> NativeSystemAudioAnalyzeResponse {
        let payload = NativeSystemAudioAnalyzePayload(
            path: path,
            routePreset: routePreset,
            privacyMode: "ephemeral",
            sourceLabel: "Native system audio",
            durationSeconds: durationSeconds,
            remember: remember,
            sourceRoute: sourceRoute
        )
        return try await post("/native/system-audio/analyze", payload: payload)
    }

    func nativeSystemAudioTempStatus() async throws -> NativeSystemAudioTempStatusResponse {
        try await get("/native/system-audio/temp")
    }

    func claimCaptureRequest(id: String) async throws -> CaptureRequestClaimResponse {
        try await post("/background/capture-request/claim", payload: CaptureRequestClaimPayload(id: id))
    }

    func listenEvent(path: String, routePreset: String) async throws -> ListenEventResponse {
        try await post("/listen-event", payload: ListenEventPayload(path: path, routePreset: routePreset))
    }

    func akouoSkills() async throws -> AkouoSkillsResponse {
        try await get("/akouo/skills")
    }

    func cleanupNativeSystemAudioTemp(deleteAll: Bool = true, dryRun: Bool = false) async throws -> NativeSystemAudioCleanupResponse {
        let payload = NativeSystemAudioCleanupPayload(deleteAll: deleteAll, dryRun: dryRun)
        return try await post("/native/system-audio/cleanup", payload: payload)
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var request = URLRequest(url: try endpoint(path))
        request.httpMethod = "GET"
        applyAuth(to: &request)
        return try await send(request)
    }

    private func post<T: Decodable, P: Encodable>(_ path: String, payload: P) async throws -> T {
        var request = URLRequest(url: try endpoint(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.httpBody = try JSONEncoder().encode(payload)
        applyAuth(to: &request)
        return try await send(request)
    }

    private func applyAuth(to request: inout URLRequest) {
        guard let authToken else { return }
        request.setValue("Bearer \(authToken)", forHTTPHeaderField: "authorization")
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw DaemonClientError.emptyResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(DaemonErrorEnvelope.self, from: data).detail)
                ?? String(data: data, encoding: .utf8)
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw DaemonClientError.requestFailed(http.statusCode, detail)
        }
        if data.isEmpty {
            throw DaemonClientError.emptyResponse
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func endpoint(_ path: String) throws -> URL {
        let base = try baseURL
        guard let url = URL(string: path, relativeTo: base)?.absoluteURL else {
            throw DaemonClientError.invalidURL(path)
        }
        return url
    }
}
