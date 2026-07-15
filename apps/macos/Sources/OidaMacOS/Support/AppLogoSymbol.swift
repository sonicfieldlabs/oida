import AppKit

/// A monochrome, template-safe rendering of the app icon's listening point.
/// The Dock icon keeps its tile; compact macOS chrome uses only the dot and
/// incomplete arcs so the mark remains legible at menu-bar scale.
enum AppLogoSymbol {
    static func image(size: CGFloat = 18) -> NSImage {
        let imageSize = NSSize(width: size, height: size)
        let image = NSImage(size: imageSize, flipped: false) { rect in
            let scale = min(rect.width, rect.height) / 18
            let center = NSPoint(x: rect.midX + 0.2 * scale, y: rect.midY - 0.1 * scale)
            let arcs: [(radius: CGFloat, width: CGFloat, opacity: CGFloat)] = [
                (2.8, 1.65, 1),
                (5.2, 1.25, 0.82),
                (7.5, 1.0, 0.62),
            ]

            NSGraphicsContext.current?.shouldAntialias = true
            for arc in arcs {
                NSColor(calibratedWhite: 0, alpha: arc.opacity).setStroke()
                let path = NSBezierPath()
                path.lineCapStyle = .round
                path.lineWidth = arc.width * scale
                path.appendArc(
                    withCenter: center,
                    radius: arc.radius * scale,
                    startAngle: -75,
                    endAngle: 165,
                    clockwise: false
                )
                path.stroke()
            }

            NSColor.black.setFill()
            let dotRadius = 1.15 * scale
            NSBezierPath(
                ovalIn: NSRect(
                    x: center.x - dotRadius,
                    y: center.y - dotRadius,
                    width: dotRadius * 2,
                    height: dotRadius * 2
                )
            ).fill()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "oída"
        return image
    }
}
