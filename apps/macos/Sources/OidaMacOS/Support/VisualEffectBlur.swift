import AppKit
import SwiftUI

/// Frosted glass that blurs what is BEHIND the window (desktop, other apps),
/// which SwiftUI's Material cannot do on macOS. The floating listener's box and
/// chips use it so they read as dilutions of the screen, not opaque cards.
struct VisualEffectBlur: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .popover
    var cornerRadius: CGFloat = 0

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.blendingMode = .behindWindow
        view.state = .active
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.material = material
        view.maskImage = cornerRadius > 0 ? Self.roundedMask(radius: cornerRadius) : nil
    }

    // behindWindow blur ignores layer corner clipping; the documented way to
    // shape it is a stretchable mask image.
    private static func roundedMask(radius: CGFloat) -> NSImage {
        let edge = radius * 2 + 1
        let image = NSImage(size: NSSize(width: edge, height: edge), flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
            return true
        }
        image.capInsets = NSEdgeInsets(top: radius, left: radius, bottom: radius, right: radius)
        image.resizingMode = .stretch
        return image
    }
}

extension View {
    /// The listener's shared surface treatment: behind-window frost, hairline edge.
    func frost(cornerRadius: CGFloat, material: NSVisualEffectView.Material = .popover) -> some View {
        background(VisualEffectBlur(material: material, cornerRadius: cornerRadius))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(Color.primary.opacity(0.07), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }
}
