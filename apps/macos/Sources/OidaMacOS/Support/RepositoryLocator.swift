import Foundation

func oidaDataDirectory() -> URL {
    let environment = ProcessInfo.processInfo.environment
    if let configured = environment["OIDA_DATA_DIR"] ?? environment["HMM_DATA_DIR"] ?? environment["AEAR_DATA_DIR"],
       !configured.isEmpty {
        return URL(fileURLWithPath: configured, isDirectory: true)
            .standardizedFileURL
    }
    let applicationSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support", isDirectory: true)
    return applicationSupport.appendingPathComponent("oida", isDirectory: true)
}

func oidaAudioDirectory() -> URL {
    let environment = ProcessInfo.processInfo.environment
    if let configured = environment["OIDA_AUDIO_DIR"] ?? environment["HMM_AUDIO_DIR"] ?? environment["AEAR_AUDIO_DIR"],
       !configured.isEmpty {
        return URL(fileURLWithPath: (configured as NSString).expandingTildeInPath, isDirectory: true)
            .standardizedFileURL
    }
    return URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Documents/oida/audio", isDirectory: true)
}

func findOidaRepositoryRoot() -> URL? {
    let fileManager = FileManager.default
    let candidates = [
        ProcessInfo.processInfo.environment["OIDA_REPOSITORY_ROOT"].map {
            URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath, isDirectory: true)
        },
        Bundle.main.executableURL,
        Bundle.main.bundleURL,
        URL(fileURLWithPath: fileManager.currentDirectoryPath)
    ].compactMap { $0 }

    for candidate in candidates {
        var current = candidate.hasDirectoryPath ? candidate : candidate.deletingLastPathComponent()
        for _ in 0..<12 {
            let pyproject = current.appendingPathComponent("pyproject.toml").path
            let server = current.appendingPathComponent("oida/server.py").path
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
