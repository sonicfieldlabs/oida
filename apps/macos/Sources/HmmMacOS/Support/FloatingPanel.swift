import AppKit
import SwiftUI

/// Owns the always-on-top floating listener panel. An NSPanel (not a SwiftUI
/// Window scene) so a global hotkey can show/hide it on macOS 13 and it never
/// steals focus from the app the user is listening to.
@MainActor
final class FloatingPanelController {
    private var panel: NSPanel?
    private let makeContent: () -> NSView

    init(makeContent: @escaping () -> NSView) {
        self.makeContent = makeContent
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
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 270),
            styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView, .resizable],
            backing: .buffered,
            defer: false
        )
        panel.appearance = NSAppearance(named: .aqua)
        // Resizable vertically only: the reading area grows, the card stays narrow.
        panel.contentMinSize = NSSize(width: 320, height: 240)
        panel.contentMaxSize = NSSize(width: 320, height: 820)
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true
        panel.isMovableByWindowBackground = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isReleasedWhenClosed = false
        panel.becomesKeyOnly = true
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        let content = makeContent()
        content.autoresizingMask = [.width, .height]
        panel.contentView = content
        panel.setFrameAutosaveName("hmm.floating-listener")
        if !panel.setFrameUsingName("hmm.floating-listener"), let screen = NSScreen.main {
            let visible = screen.visibleFrame
            let origin = NSPoint(
                x: visible.maxX - panel.frame.width - 24,
                y: visible.maxY - panel.frame.height - 24
            )
            panel.setFrameOrigin(origin)
        }
        self.panel = panel
        return panel
    }
}

private extension NSPanel {
    var becomesKeyOnly: Bool {
        get { becomesKeyOnlyIfNeeded }
        set { becomesKeyOnlyIfNeeded = newValue }
    }
}
