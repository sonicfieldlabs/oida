import AppKit
import SwiftUI

struct WindowLevelAccessor: NSViewRepresentable {
    var level: NSWindow.Level
    var canJoinAllSpaces = true

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        DispatchQueue.main.async {
            apply(to: view.window)
        }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {
        DispatchQueue.main.async {
            apply(to: view.window)
        }
    }

    private func apply(to window: NSWindow?) {
        guard let window else { return }
        window.level = level
        window.isMovableByWindowBackground = true
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.styleMask.insert(.fullSizeContentView)
        if canJoinAllSpaces {
            window.collectionBehavior.insert(.canJoinAllSpaces)
            window.collectionBehavior.insert(.fullScreenAuxiliary)
        }
    }
}
