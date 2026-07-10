import Foundation

enum DaemonSupervisorError: LocalizedError {
    case repositoryRootNotFound
    case alreadyRunning
    case launchFailed(String)

    var errorDescription: String? {
        switch self {
        case .repositoryRootNotFound:
            return "Could not locate the oida repository root."
        case .alreadyRunning:
            return "A managed daemon process is already running."
        case .launchFailed(let detail):
            return "Could not start daemon: \(detail)"
        }
    }
}

@MainActor
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
        guard let root = findOidaRepositoryRoot() else {
            throw DaemonSupervisorError.repositoryRootNotFound
        }

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = [
            "uv",
            "run",
            "oida",
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
            let data = handle.availableData
            Task { @MainActor [weak self] in
                self?.emitLines(from: data)
            }
        }
        errorPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            Task { @MainActor [weak self] in
                self?.emitLines(from: data)
            }
        }
        proc.terminationHandler = { [weak self] process in
            let status = process.terminationStatus
            Task { @MainActor [weak self] in
                self?.onExit?(status)
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
        for line in lines {
            onLogLine?(line)
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
        if environment["OIDA_DATA_DIR"] == nil, environment["HMM_DATA_DIR"] == nil, environment["AEAR_DATA_DIR"] == nil {
            environment["OIDA_DATA_DIR"] = oidaDataDirectory().path
        }
        if environment["OIDA_AUDIO_DIR"] == nil, environment["HMM_AUDIO_DIR"] == nil, environment["AEAR_AUDIO_DIR"] == nil {
            environment["OIDA_AUDIO_DIR"] = oidaAudioDirectory().path
        }
        return environment
    }
}
