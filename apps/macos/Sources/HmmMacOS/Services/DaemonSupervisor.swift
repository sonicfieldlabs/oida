import Foundation

enum DaemonSupervisorError: LocalizedError {
    case repositoryRootNotFound
    case alreadyRunning
    case launchFailed(String)

    var errorDescription: String? {
        switch self {
        case .repositoryRootNotFound:
            return "Could not locate the hmm repository root."
        case .alreadyRunning:
            return "A managed daemon process is already running."
        case .launchFailed(let detail):
            return "Could not start daemon: \(detail)"
        }
    }
}

final class DaemonSupervisor {
    private var process: Process?
    private var outputPipe: Pipe?
    private var errorPipe: Pipe?

    var onLogLine: ((String) -> Void)?
    var onExit: ((Int32) -> Void)?

    var isManagedRunning: Bool {
        process?.isRunning == true
    }

    func start(profile: String = "mac-mps", host: String = "127.0.0.1", port: Int = 8765) throws {
        guard process?.isRunning != true else {
            throw DaemonSupervisorError.alreadyRunning
        }
        guard let root = findHmmRepositoryRoot() else {
            throw DaemonSupervisorError.repositoryRootNotFound
        }

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = [
            "uv",
            "run",
            "hmm",
            "--profile",
            profile,
            "--host",
            host,
            "--port",
            "\(port)"
        ]
        proc.currentDirectoryURL = root
        proc.standardOutput = outputPipe
        proc.standardError = errorPipe
        proc.environment = mergedEnvironment()

        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.emitLines(from: handle.availableData)
        }
        errorPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.emitLines(from: handle.availableData)
        }
        proc.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                self?.onExit?(process.terminationStatus)
                self?.cleanup()
            }
        }

        do {
            try proc.run()
        } catch {
            cleanup()
            throw DaemonSupervisorError.launchFailed(error.localizedDescription)
        }

        process = proc
        self.outputPipe = outputPipe
        self.errorPipe = errorPipe
        onLogLine?("Started daemon from \(root.path)")
    }

    func stop() {
        guard let process else { return }
        process.terminate()
        cleanup()
        onLogLine?("Stopped managed daemon")
    }

    private func cleanup() {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        errorPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
        errorPipe = nil
        process = nil
    }

    private func emitLines(from data: Data) {
        guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
        let lines = text
            .split(whereSeparator: \.isNewline)
            .map(String.init)
            .filter { !$0.isEmpty }
        guard !lines.isEmpty else { return }
        DispatchQueue.main.async { [weak self] in
            for line in lines {
                self?.onLogLine?(line)
            }
        }
    }

    private func mergedEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let existingPath = environment["PATH"] ?? ""
        let homeLocalBin = "\(NSHomeDirectory())/.local/bin"
        let devPath = "\(homeLocalBin):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PATH"] = existingPath.isEmpty ? devPath : "\(devPath):\(existingPath)"
        let homebrewLibPaths = ["/opt/homebrew/lib", "/usr/local/lib"]
            .filter { FileManager.default.fileExists(atPath: $0) }
        if !homebrewLibPaths.isEmpty {
            let existingDylibPath = environment["DYLD_LIBRARY_PATH"] ?? ""
            environment["DYLD_LIBRARY_PATH"] = existingDylibPath.isEmpty
                ? homebrewLibPaths.joined(separator: ":")
                : "\(homebrewLibPaths.joined(separator: ":")):\(existingDylibPath)"
        }
        if environment["HMM_DATA_DIR"] == nil, environment["AEAR_DATA_DIR"] == nil {
            environment["HMM_DATA_DIR"] = hmmDataDirectory().path
        }
        if environment["HMM_AUDIO_DIR"] == nil, environment["AEAR_AUDIO_DIR"] == nil {
            environment["HMM_AUDIO_DIR"] = hmmAudioDirectory().path
        }
        return environment
    }
}
