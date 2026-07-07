import SwiftUI

/// A small reactive waveform for the corner of the listening box. Bars follow
/// the live spectral bands (system tap) or the mic level; it only animates
/// while something is actually being heard, and freezes low and quiet at rest.
struct MiniWaveformView: View {
    var bands: [Double]
    var level: Double
    var active: Bool

    private let count = 15

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: !active)) { timeline in
            Canvas { context, size in
                let phase = timeline.date.timeIntervalSinceReferenceDate
                let gap: CGFloat = 2
                let barWidth = (size.width - gap * CGFloat(count - 1)) / CGFloat(count)
                for index in 0..<count {
                    let measured: Double
                    if !bands.isEmpty {
                        let bandIndex = index * bands.count / count
                        measured = max(0.05, min(1.0, bands[min(bandIndex, bands.count - 1)]))
                    } else if active {
                        // No spectral bands (mic metering): let the level ripple.
                        let wobble = 0.5 + 0.5 * sin(phase * 5 + Double(index) * 0.7)
                        measured = max(0.06, min(1.0, level * (0.5 + 0.7 * wobble)))
                    } else {
                        measured = 0.06
                    }
                    let height = max(1.5, size.height * CGFloat(measured))
                    let x = CGFloat(index) * (barWidth + gap)
                    let rect = CGRect(x: x, y: (size.height - height) / 2, width: barWidth, height: height)
                    context.fill(
                        Path(roundedRect: rect, cornerRadius: barWidth / 2),
                        with: .color(.primary.opacity(active ? 0.5 : 0.18))
                    )
                }
            }
        }
        .accessibilityHidden(true)
    }
}
