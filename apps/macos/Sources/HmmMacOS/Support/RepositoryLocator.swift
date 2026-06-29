import Foundation

func findHmmRepositoryRoot() -> URL? {
    let fileManager = FileManager.default
    let candidates = [
        Bundle.main.executableURL,
        Bundle.main.bundleURL,
        URL(fileURLWithPath: fileManager.currentDirectoryPath)
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
