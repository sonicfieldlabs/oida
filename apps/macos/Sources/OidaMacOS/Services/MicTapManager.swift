import AVFoundation
import Foundation

enum MicTapError: LocalizedError {
    case notCapturing
    case emptyBuffer
    case unsupportedFormat
    case directoryUnavailable

    var errorDescription: String? {
        switch self {
        case .notCapturing: return "The microphone tap is not running."
        case .emptyBuffer: return "No microphone audio is buffered yet."
        case .unsupportedFormat: return "The microphone format is not supported."
        case .directoryUnavailable: return "Could not create the audio directory."
        }
    }
}

/// Ring-buffered microphone capture via AVAudioEngine, mirroring the system
/// tap so the floating listener can listen to the default input natively.
final class MicTapManager: @unchecked Sendable {
    var onLevel: (@Sendable (Double) -> Void)?

    /// How much past the ring can hold. Raised from 30 s so past-direction
    /// listens can reach back up to two minutes (~23 MB of float32 mono).
    static let ringCapacitySeconds: Double = 120

    private let engine = AVAudioEngine()
    private let queue = DispatchQueue(label: "org.sonicfield.oida.mic-tap")
    private var ringSamples: [Float] = []
    private var ringSampleRate: Double = 48_000
    private let ringMaxSeconds: Double = MicTapManager.ringCapacitySeconds
    private(set) var isCapturing = false

    /// Seconds of audio actually sitting in the ring right now.
    var bufferedSeconds: Double {
        queue.sync { ringSampleRate > 0 ? Double(ringSamples.count) / ringSampleRate : 0 }
    }

    func start() throws {
        guard !isCapturing else { return }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else { throw MicTapError.unsupportedFormat }
        queue.sync {
            ringSamples.removeAll(keepingCapacity: true)
            ringSampleRate = format.sampleRate
        }
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            self?.ingest(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            engine.stop()
            queue.sync { ringSamples.removeAll(keepingCapacity: true) }
            throw error
        }
        isCapturing = true
    }

    func stop() {
        guard isCapturing else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        isCapturing = false
        queue.sync { ringSamples.removeAll(keepingCapacity: true) }
    }

    func writeRecentAudio(seconds: Double, audioDirectory: URL = oidaAudioDirectory()) throws -> URL {
        guard isCapturing else { throw MicTapError.notCapturing }
        let requestedSeconds = max(0.25, min(seconds, ringMaxSeconds))
        let snapshot: (samples: [Float], sampleRate: Double) = queue.sync {
            let frameCount = min(ringSamples.count, max(1, Int(round(requestedSeconds * ringSampleRate))))
            guard frameCount > 0 else { return ([], ringSampleRate) }
            return (Array(ringSamples.suffix(frameCount)), ringSampleRate)
        }
        guard !snapshot.samples.isEmpty else { throw MicTapError.emptyBuffer }

        do {
            try FileManager.default.createDirectory(at: audioDirectory, withIntermediateDirectories: true)
        } catch {
            throw MicTapError.directoryUnavailable
        }
        let fileName = "\(Self.timestamp())-oida-mic-input-\(Int(round(requestedSeconds)))s.wav"
        let output = audioDirectory.appendingPathComponent(fileName)

        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: snapshot.sampleRate, channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(snapshot.samples.count)),
              let channel = buffer.floatChannelData?[0] else {
            throw MicTapError.unsupportedFormat
        }
        buffer.frameLength = AVAudioFrameCount(snapshot.samples.count)
        snapshot.samples.withUnsafeBufferPointer { pointer in
            if let base = pointer.baseAddress {
                channel.update(from: base, count: snapshot.samples.count)
            }
        }
        let file = try AVAudioFile(forWriting: output, settings: format.settings)
        try file.write(from: buffer)
        return output
    }

    private func ingest(_ buffer: AVAudioPCMBuffer) {
        guard let channelData = buffer.floatChannelData else { return }
        let frames = Int(buffer.frameLength)
        guard frames > 0 else { return }
        let channels = Int(buffer.format.channelCount)
        var mono = [Float](repeating: 0, count: frames)
        for channel in 0..<channels {
            let data = channelData[channel]
            for frame in 0..<frames {
                mono[frame] += data[frame] / Float(channels)
            }
        }
        var energy: Double = 0
        for value in mono { energy += Double(value * value) }
        let rms = (energy / Double(frames)).squareRoot()
        onLevel?(min(1.0, rms * 3.2))
        let capturedMono = mono
        queue.async { [weak self] in
            guard let self else { return }
            self.ringSamples.append(contentsOf: capturedMono)
            let maxFrames = max(1, Int(round(self.ringSampleRate * self.ringMaxSeconds)))
            if self.ringSamples.count > maxFrames {
                self.ringSamples.removeFirst(self.ringSamples.count - maxFrames)
            }
        }
    }

    private static func timestamp() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyyMMdd'T'HHmmssSSS'Z'"
        return formatter.string(from: Date())
    }
}
