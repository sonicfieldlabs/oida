import Foundation

func hmmDataDirectory() -> URL {
    let environment = ProcessInfo.processInfo.environment
    if let configured = environment["HMM_DATA_DIR"] ?? environment["AEAR_DATA_DIR"], !configured.isEmpty {
        return URL(fileURLWithPath: configured, isDirectory: true)
            .standardizedFileURL
    }
    let applicationSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support", isDirectory: true)
    return applicationSupport.appendingPathComponent("hmm", isDirectory: true)
}

func hmmAudioDirectory() -> URL {
    let environment = ProcessInfo.processInfo.environment
    if let configured = environment["HMM_AUDIO_DIR"] ?? environment["AEAR_AUDIO_DIR"], !configured.isEmpty {
        return URL(fileURLWithPath: (configured as NSString).expandingTildeInPath, isDirectory: true)
            .standardizedFileURL
    }
    return URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Documents/hmm/audio", isDirectory: true)
}

func findHmmRepositoryRoot() -> URL? {
    let fileManager = FileManager.default
    let candidates = [
        Bundle.main.executableURL,
        Bundle.main.bundleURL,
        URL(fileURLWithPath: fileManager.currentDirectoryPath),
        // Known checkout location so a packaged app (run from /Applications)
        // can still supervise the daemon.
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Documents/hmm/aear", isDirectory: true)
    ].compactMap { $0 }

    for candidate in candidates {
        var current = candidate.hasDirectoryPath ? candidate : candidate.deletingLastPathComponent()
        for _ in 0..<12 {
            let pyproject = current.appendingPathComponent("pyproject.toml").path
            let server = current.appendingPathComponent("aear/server.py").path
            if fileManager.fileExists(atPath: pyproject), fileManager.fileExists(atPath: server) {
                return current
            }
            let parent = current.deletingLastPathComponent()
            if parent.path == current.path { break }
            current = parent
        }
    }

    return nil
}
