import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

struct NativeAudioTapSnapshot {
    let bands: [Double]
    let rms: Double
    let peak: Double
    let sampleRate: Double
    let channelCount: Int
    let updatedAt: Date
}

struct NativeSystemAudioCapture {
    let path: URL
    let durationSeconds: Double
    let sampleRate: Double
    let frameCount: Int
    let sourceRoute: NativeSystemAudioRoutePayload
}

enum SystemAudioTapCaptureError: LocalizedError {
    case notCapturing
    case emptyBuffer
    case dataDirectoryUnavailable
    case unsupportedFormat

    var errorDescription: String? {
        switch self {
        case .notCapturing:
            return "Start the native system audio tap before analyzing system output."
        case .emptyBuffer:
            return "The native system audio buffer is empty."
        case .dataDirectoryUnavailable:
            return "Could not prepare the hmm data directory for temporary audio storage."
        case .unsupportedFormat:
            return "The native system audio buffer format is not writable."
        }
    }
}

enum SystemAudioTapState: Equatable {
    case idle
    case starting
    case capturing
    case stopped
    case unavailable(String)
    case failed(String)

    var label: String {
        switch self {
        case .idle:
            return "Native tap idle"
        case .starting:
            return "Native tap starting"
        case .capturing:
            return "Native tap active"
        case .stopped:
            return "Native tap stopped"
        case .unavailable(let reason):
            return reason
        case .failed(let reason):
            return reason
        }
    }
}

final class SystemAudioTapManager: NSObject {
    var onSnapshot: ((NativeAudioTapSnapshot) -> Void)?
    var onStateChange: ((SystemAudioTapState) -> Void)?
    var onRouteChange: ((NativeSystemAudioRoutePayload?) -> Void)?

    private var stream: SCStream?
    private let captureQueue = DispatchQueue(label: "org.sonicfield.hmm.system-audio-tap")
    private var isCapturing = false
    private var ringSamples: [Float] = []
    private var ringSampleRate: Double = 48_000
    private var ringMaxSeconds: Double = 30
    private var currentSourceRoute: NativeSystemAudioRoutePayload?
    // The current process is the one excluded from capture (excludesCurrentProcessAudio);
    // report it by its real bundle id rather than the previous hardcoded "hmm" guess.
    private static let excludedProcessIdentifiers = [Bundle.main.bundleIdentifier ?? "org.sonicfield.hmm"]

    @MainActor
    func start() async {
        guard !isCapturing else { return }
        guard #available(macOS 13.0, *) else {
            onStateChange?(.unavailable("ScreenCaptureKit audio requires macOS 13+"))
            return
        }

        onStateChange?(.starting)
        do {
            try await startScreenCaptureKit()
            isCapturing = true
            onStateChange?(.capturing)
        } catch {
            isCapturing = false
            onStateChange?(.failed(error.localizedDescription))
        }
    }

    @MainActor
    func stop() async {
        guard let stream else {
            isCapturing = false
            onStateChange?(.stopped)
            return
        }
        do {
            try await stream.stopCapture()
        } catch {
            onStateChange?(.failed(error.localizedDescription))
        }
        self.stream = nil
        isCapturing = false
        currentSourceRoute = nil
        captureQueue.sync {
            ringSamples.removeAll(keepingCapacity: true)
        }
        onRouteChange?(nil)
        onStateChange?(.stopped)
    }

    func writeRecentAudio(seconds: Double, audioDirectory: URL = hmmAudioDirectory()) throws -> NativeSystemAudioCapture {
        guard isCapturing else { throw SystemAudioTapCaptureError.notCapturing }
        let sourceRoute = currentSourceRoute ?? fallbackDisplayMixRoute()
        let requestedSeconds = max(0.25, min(seconds, ringMaxSeconds))
        let snapshot: (samples: [Float], sampleRate: Double) = captureQueue.sync {
            let frameCount = min(ringSamples.count, max(1, Int(round(requestedSeconds * ringSampleRate))))
            guard frameCount > 0 else {
                return ([], ringSampleRate)
            }
            return (Array(ringSamples.suffix(frameCount)), ringSampleRate)
        }
        guard !snapshot.samples.isEmpty else { throw SystemAudioTapCaptureError.emptyBuffer }

        do {
            try FileManager.default.createDirectory(at: audioDirectory, withIntermediateDirectories: true)
        } catch {
            throw SystemAudioTapCaptureError.dataDirectoryUnavailable
        }
        let fileName = "\(timestampForFilename())-hmm-native-system-output-\(Int(round(requestedSeconds)))s.wav"
        let output = audioDirectory.appendingPathComponent(fileName)
        try writeMonoFloatWav(samples: snapshot.samples, sampleRate: snapshot.sampleRate, output: output)
        return NativeSystemAudioCapture(
            path: output,
            durationSeconds: Double(snapshot.samples.count) / snapshot.sampleRate,
            sampleRate: snapshot.sampleRate,
            frameCount: snapshot.samples.count,
            sourceRoute: sourceRoute
        )
    }

    @available(macOS 13.0, *)
    private func startScreenCaptureKit() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "hmm.system-audio-tap", code: 1, userInfo: [NSLocalizedDescriptionKey: "No capturable display is available."])
        }

        let sourceRoute = defaultDisplayMixRoute(display: display)
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true

        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: captureQueue)
        try await stream.startCapture()
        currentSourceRoute = sourceRoute
        onRouteChange?(sourceRoute)
        self.stream = stream
    }

    @available(macOS 13.0, *)
    private func defaultDisplayMixRoute(display: SCDisplay?) -> NativeSystemAudioRoutePayload {
        NativeSystemAudioRoutePayload(
            routeId: "native-display-mix",
            captureScope: "display_mix",
            adapter: "macos-screencapturekit-system-audio",
            label: "Display system mix",
            platform: "darwin",
            displayId: display?.displayID,
            displayWidth: display.map { Int($0.width) },
            displayHeight: display.map { Int($0.height) },
            excludedCurrentProcessAudio: true,
            excludedApplications: Self.excludedProcessIdentifiers
        )
    }

    private func fallbackDisplayMixRoute() -> NativeSystemAudioRoutePayload {
        NativeSystemAudioRoutePayload(
            routeId: "native-display-mix",
            captureScope: "display_mix",
            adapter: "macos-screencapturekit-system-audio",
            label: "Display system mix",
            platform: "darwin",
            displayId: nil,
            displayWidth: nil,
            displayHeight: nil,
            excludedCurrentProcessAudio: true,
            excludedApplications: Self.excludedProcessIdentifiers
        )
    }

    private func handleAudioSampleBuffer(_ sampleBuffer: CMSampleBuffer) {
        guard sampleBuffer.isValid else { return }
        guard let format = CMSampleBufferGetFormatDescription(sampleBuffer),
              let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee else {
            return
        }

        let bufferCount = max(1, Int(streamDescription.mChannelsPerFrame))
        let listSize = MemoryLayout<AudioBufferList>.size + MemoryLayout<AudioBuffer>.size * max(0, bufferCount - 1)
        let listMemory = UnsafeMutableRawPointer.allocate(byteCount: listSize, alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { listMemory.deallocate() }

        let audioBufferList = listMemory.bindMemory(to: AudioBufferList.self, capacity: 1)
        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: audioBufferList,
            bufferListSize: listSize,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr else { return }

        let values = monoSamples(from: audioBufferList, streamDescription: streamDescription)
        guard !values.isEmpty else { return }

        var sumSquares = 0.0
        var peak = 0.0
        for value in values {
            let doubleValue = Double(value)
            let magnitude = abs(doubleValue)
            sumSquares += doubleValue * doubleValue
            peak = max(peak, magnitude)
        }
        let rms = sqrt(sumSquares / Double(values.count))
        appendRingSamples(values, sampleRate: streamDescription.mSampleRate)
        let snapshot = NativeAudioTapSnapshot(
            bands: bandValues(from: values, count: 14),
            rms: min(1.0, rms),
            peak: min(1.0, peak),
            sampleRate: streamDescription.mSampleRate,
            channelCount: bufferCount,
            updatedAt: Date()
        )

        DispatchQueue.main.async { [weak self] in
            self?.onSnapshot?(snapshot)
        }
    }

    private func monoSamples(from audioBufferList: UnsafeMutablePointer<AudioBufferList>, streamDescription: AudioStreamBasicDescription) -> [Float] {
        let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
        let isFloat = (streamDescription.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isNonInterleaved = (streamDescription.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
        let bits = streamDescription.mBitsPerChannel
        let channels = max(1, Int(streamDescription.mChannelsPerFrame))

        if isFloat, bits == 32 {
            return floatMonoSamples(from: buffers, channels: channels, nonInterleaved: isNonInterleaved)
        }
        if !isFloat, bits == 16 {
            return int16MonoSamples(from: buffers, channels: channels, nonInterleaved: isNonInterleaved)
        }

        return []
    }

    private func floatMonoSamples(from buffers: UnsafeMutableAudioBufferListPointer, channels: Int, nonInterleaved: Bool) -> [Float] {
        if nonInterleaved, buffers.count > 1 {
            let usableChannels = min(channels, buffers.count)
            let frameCount = (0..<usableChannels)
                .compactMap { index -> Int? in
                    guard buffers[index].mData != nil else { return nil }
                    return Int(buffers[index].mDataByteSize) / MemoryLayout<Float>.size
                }
                .min() ?? 0
            guard frameCount > 0 else { return [] }
            var samples = Array(repeating: Float(0), count: frameCount)
            for channelIndex in 0..<usableChannels {
                guard let data = buffers[channelIndex].mData else { continue }
                let pointer = data.assumingMemoryBound(to: Float.self)
                for frame in 0..<frameCount {
                    samples[frame] += pointer[frame] / Float(usableChannels)
                }
            }
            return samples
        }

        guard let buffer = buffers.first, let data = buffer.mData else { return [] }
        let count = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
        let pointer = data.assumingMemoryBound(to: Float.self)
        if channels <= 1 {
            return (0..<count).map { pointer[$0] }
        }
        let frameCount = count / channels
        return (0..<frameCount).map { frame in
            var sum = Float(0)
            for channel in 0..<channels {
                sum += pointer[frame * channels + channel]
            }
            return sum / Float(channels)
        }
    }

    private func int16MonoSamples(from buffers: UnsafeMutableAudioBufferListPointer, channels: Int, nonInterleaved: Bool) -> [Float] {
        if nonInterleaved, buffers.count > 1 {
            let usableChannels = min(channels, buffers.count)
            let frameCount = (0..<usableChannels)
                .compactMap { index -> Int? in
                    guard buffers[index].mData != nil else { return nil }
                    return Int(buffers[index].mDataByteSize) / MemoryLayout<Int16>.size
                }
                .min() ?? 0
            guard frameCount > 0 else { return [] }
            var samples = Array(repeating: Float(0), count: frameCount)
            for channelIndex in 0..<usableChannels {
                guard let data = buffers[channelIndex].mData else { continue }
                let pointer = data.assumingMemoryBound(to: Int16.self)
                for frame in 0..<frameCount {
                    samples[frame] += (Float(pointer[frame]) / 32768.0) / Float(usableChannels)
                }
            }
            return samples
        }

        guard let buffer = buffers.first, let data = buffer.mData else { return [] }
        let count = Int(buffer.mDataByteSize) / MemoryLayout<Int16>.size
        let pointer = data.assumingMemoryBound(to: Int16.self)
        if channels <= 1 {
            return (0..<count).map { Float(pointer[$0]) / 32768.0 }
        }
        let frameCount = count / channels
        return (0..<frameCount).map { frame in
            var sum = Float(0)
            for channel in 0..<channels {
                sum += Float(pointer[frame * channels + channel]) / 32768.0
            }
            return sum / Float(channels)
        }
    }

    private func appendRingSamples(_ samples: [Float], sampleRate: Double) {
        ringSampleRate = sampleRate > 0 ? sampleRate : ringSampleRate
        ringSamples.append(contentsOf: samples)
        let maxFrames = max(1, Int(round(ringSampleRate * ringMaxSeconds)))
        if ringSamples.count > maxFrames {
            ringSamples.removeFirst(ringSamples.count - maxFrames)
        }
    }

    private func bandValues(from samples: [Float], count: Int) -> [Double] {
        guard count > 0 else { return [] }
        guard !samples.isEmpty else { return Array(repeating: 0, count: count) }
        var bands: [Double] = []
        for index in 0..<count {
            let start = index * samples.count / count
            let end = max(start + 1, (index + 1) * samples.count / count)
            let slice = samples[start..<min(end, samples.count)]
            let energy = slice.reduce(0.0) { partial, value in
                partial + Double(value * value)
            }
            bands.append(min(1.0, sqrt(energy / Double(slice.count)) * 3.0))
        }
        return bands
    }

    private func writeMonoFloatWav(samples: [Float], sampleRate: Double, output: URL) throws {
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)),
              let channel = buffer.floatChannelData?[0] else {
            throw SystemAudioTapCaptureError.unsupportedFormat
        }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { pointer in
            if let base = pointer.baseAddress {
                channel.update(from: base, count: samples.count)
            }
        }
        let file = try AVAudioFile(forWriting: output, settings: format.settings)
        try file.write(from: buffer)
    }

    private func timestampForFilename() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyyMMdd'T'HHmmssSSS'Z'"
        return formatter.string(from: Date())
    }
}

extension SystemAudioTapManager: SCStreamOutput, SCStreamDelegate {
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        handleAudioSampleBuffer(sampleBuffer)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        DispatchQueue.main.async { [weak self] in
            self?.isCapturing = false
            self?.stream = nil
            self?.onStateChange?(.failed(error.localizedDescription))
        }
    }
}
