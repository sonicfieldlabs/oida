import AppKit
import SwiftUI

/// The floating listener is deliberately not a window: a borderless,
/// transparent, non-activating panel whose visible body is the listening box
/// and the controls that surround it on hover. It floats over every space,
/// never steals focus from the app being listened to, and is dragged by
/// grabbing the box.
@MainActor
final class FloatingPanelController: NSObject, NSWindowDelegate {
    private var panel: FloatingListenerPanel?
    private let makeContent: () -> NSView
    private static let originDefaultsKey = "oida.floating-listener.origin"

    init(makeContent: @escaping () -> NSView) {
        self.makeContent = makeContent
        super.init()
    }

    var isVisible: Bool {
        panel?.isVisible ?? false
    }

    func toggle() {
        if isVisible {
            hide()
        } else {
            show()
        }
    }

    func show() {
        ensurePanel().orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    private func ensurePanel() -> NSPanel {
        if let panel {
            return panel
        }
        let panel = FloatingListenerPanel(
            contentRect: NSRect(x: 0, y: 0, width: 344, height: 320),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        // The dashboard and listener are light-first; pin the frost to aqua.
        panel.appearance = NSAppearance(named: .aqua)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        // No window shadow: it would trace a rectangle around the transparent
        // frame, including the invisible control gutter around the box.
        panel.hasShadow = false
        panel.isMovableByWindowBackground = true
        panel.isReleasedWhenClosed = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.animationBehavior = .utilityWindow
        panel.hidesOnDeactivate = false
        panel.delegate = self

        let content = makeContent()
        panel.contentView = content
        content.layoutSubtreeIfNeeded()
        let fitting = content.fittingSize
        if fitting.width > 10, fitting.height > 10 {
            panel.setContentSize(fitting)
        }
        place(panel)
        self.panel = panel
        return panel
    }

    /// Restore the last dragged origin, or land top-right; always clamped so
    /// the box cannot be lost off-screen.
    private func place(_ panel: NSPanel) {
        guard let visible = (panel.screen ?? NSScreen.main)?.visibleFrame else { return }
        var origin: NSPoint
        if let stored = UserDefaults.standard.string(forKey: Self.originDefaultsKey) {
            origin = NSPointFromString(stored)
        } else {
            origin = NSPoint(
                x: visible.maxX - panel.frame.width - 24,
                y: visible.maxY - panel.frame.height - 24
            )
        }
        origin.x = min(max(origin.x, visible.minX), visible.maxX - panel.frame.width)
        origin.y = min(max(origin.y, visible.minY), visible.maxY - panel.frame.height)
        panel.setFrameOrigin(origin)
    }

    private func persistOrigin() {
        guard let panel else { return }
        UserDefaults.standard.set(NSStringFromPoint(panel.frame.origin), forKey: Self.originDefaultsKey)
    }

    /// The reading bubble grows the panel upward (SwiftUI content sizing keeps
    /// the origin fixed); if that pushes past the screen edge, slide back in.
    private func keepOnScreen() {
        guard let panel, let visible = (panel.screen ?? NSScreen.main)?.visibleFrame else { return }
        var frame = panel.frame
        if frame.maxY > visible.maxY { frame.origin.y = visible.maxY - frame.height }
        if frame.minY < visible.minY { frame.origin.y = visible.minY }
        if frame.maxX > visible.maxX { frame.origin.x = visible.maxX - frame.width }
        if frame.minX < visible.minX { frame.origin.x = visible.minX }
        if frame != panel.frame {
            panel.setFrame(frame, display: true)
        }
    }

    nonisolated func windowDidMove(_ notification: Notification) {
        MainActor.assumeIsolated {
            persistOrigin()
        }
    }

    nonisolated func windowDidResize(_ notification: Notification) {
        MainActor.assumeIsolated {
            keepOnScreen()
        }
    }
}

/// Borderless panels refuse key status by default; allow it (still
/// non-activating, and only when a control asks) so the listener's menus and
/// buttons stay live while focus remains with the listened-to app.
private final class FloatingListenerPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// Hosting view that sizes the panel to the SwiftUI content and lets every
/// non-control region drag the panel.
final class ListenerHostingView<Content: View>: NSHostingView<Content> {
    override var mouseDownCanMoveWindow: Bool { true }

    required init(rootView: Content) {
        super.init(rootView: rootView)
        sizingOptions = [.preferredContentSize]
    }

    @available(*, unavailable)
    @objc dynamic required init?(coder aDecoder: NSCoder) {
        fatalError("ListenerHostingView does not support NSCoding")
    }
}
