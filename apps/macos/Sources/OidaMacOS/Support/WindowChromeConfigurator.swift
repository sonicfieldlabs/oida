import AppKit
import SwiftUI

/// Narrow AppKit bridge for controls that must sit beside the macOS traffic
/// lights. SwiftUI remains the source of truth; this view only mirrors state
/// into titlebar accessories, anchors source popovers, and forwards actions.
struct WindowChromeConfigurator: NSViewRepresentable {
    let title: String
    let appearance: String
    let selectedSource: String
    let captureSeconds: Double
    let direction: String
    let micLevel: Double
    let onToggleLeft: () -> Void
    let onSelectSource: (String) -> Void
    let onChangeCaptureSeconds: (Double) -> Void
    let onChangeDirection: (String) -> Void
    let onChooseFile: () -> Void
    let onToggleFloating: () -> Void
    let onOpenSettings: () -> Void
    let onToggleRight: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(
            title: title,
            appearance: appearance,
            selectedSource: selectedSource,
            captureSeconds: captureSeconds,
            direction: direction,
            micLevel: micLevel,
            onToggleLeft: onToggleLeft,
            onSelectSource: onSelectSource,
            onChangeCaptureSeconds: onChangeCaptureSeconds,
            onChangeDirection: onChangeDirection,
            onChooseFile: onChooseFile,
            onToggleFloating: onToggleFloating,
            onOpenSettings: onOpenSettings,
            onToggleRight: onToggleRight
        )
    }

    func makeNSView(context: Context) -> NSView {
        let view = WindowChromeView()
        view.coordinator = context.coordinator
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        let coordinator = context.coordinator
        coordinator.title = title
        coordinator.appearance = appearance
        coordinator.selectedSource = selectedSource
        coordinator.captureSeconds = captureSeconds
        coordinator.direction = direction
        coordinator.micLevel = micLevel
        coordinator.onToggleLeft = onToggleLeft
        coordinator.onSelectSource = onSelectSource
        coordinator.onChangeCaptureSeconds = onChangeCaptureSeconds
        coordinator.onChangeDirection = onChangeDirection
        coordinator.onChooseFile = onChooseFile
        coordinator.onToggleFloating = onToggleFloating
        coordinator.onOpenSettings = onOpenSettings
        coordinator.onToggleRight = onToggleRight
        coordinator.refreshOpenSourcePopover()
        (nsView as? WindowChromeView)?.configureWindow()
    }

    final class Coordinator: NSObject, NSPopoverDelegate {
        private static let leftAccessoryIdentifier = NSUserInterfaceItemIdentifier("org.sonicfield.oida.left-titlebar-tools")
        private static let rightAccessoryIdentifier = NSUserInterfaceItemIdentifier("org.sonicfield.oida.right-titlebar-tools")
        private static let sourceIdentifiers = [
            "system": NSUserInterfaceItemIdentifier("org.sonicfield.oida.source-system"),
            "mic": NSUserInterfaceItemIdentifier("org.sonicfield.oida.source-mic"),
            "file": NSUserInterfaceItemIdentifier("org.sonicfield.oida.source-file"),
        ]

        var title: String
        var appearance: String
        var selectedSource: String
        var captureSeconds: Double
        var direction: String
        var micLevel: Double
        var onToggleLeft: () -> Void
        var onSelectSource: (String) -> Void
        var onChangeCaptureSeconds: (Double) -> Void
        var onChangeDirection: (String) -> Void
        var onChooseFile: () -> Void
        var onToggleFloating: () -> Void
        var onOpenSettings: () -> Void
        var onToggleRight: () -> Void

        private weak var window: NSWindow?
        private var sourcePopover: NSPopover?
        private var popoverSource: String?

        init(
            title: String,
            appearance: String,
            selectedSource: String,
            captureSeconds: Double,
            direction: String,
            micLevel: Double,
            onToggleLeft: @escaping () -> Void,
            onSelectSource: @escaping (String) -> Void,
            onChangeCaptureSeconds: @escaping (Double) -> Void,
            onChangeDirection: @escaping (String) -> Void,
            onChooseFile: @escaping () -> Void,
            onToggleFloating: @escaping () -> Void,
            onOpenSettings: @escaping () -> Void,
            onToggleRight: @escaping () -> Void
        ) {
            self.title = title
            self.appearance = appearance
            self.selectedSource = selectedSource
            self.captureSeconds = captureSeconds
            self.direction = direction
            self.micLevel = micLevel
            self.onToggleLeft = onToggleLeft
            self.onSelectSource = onSelectSource
            self.onChangeCaptureSeconds = onChangeCaptureSeconds
            self.onChangeDirection = onChangeDirection
            self.onChooseFile = onChooseFile
            self.onToggleFloating = onToggleFloating
            self.onOpenSettings = onOpenSettings
            self.onToggleRight = onToggleRight
        }

        func configure(_ window: NSWindow) {
            self.window = window
            let normalizedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
            window.title = normalizedTitle
            window.titleVisibility = normalizedTitle.isEmpty ? .hidden : .visible
            // Extend the dashboard surface through the titlebar. Without the
            // full-size content mask, AppKit leaves a separate vibrancy strip
            // above titlebar accessories that picks up the desktop tint.
            window.styleMask.insert(.fullSizeContentView)
            window.toolbarStyle = .unified
            window.titlebarAppearsTransparent = true
            window.titlebarSeparatorStyle = .none
            window.isOpaque = true
            let background = Self.chromeBackgroundColor(for: appearance)
            window.backgroundColor = background
            window.contentView?.wantsLayer = true
            window.contentView?.layer?.backgroundColor = background.cgColor
            installLeftAccessory(in: window)
            installRightAccessory(in: window)
            updateSourceSelection(in: window)
        }

        private static func chromeBackgroundColor(for appearance: String) -> NSColor {
            if appearance == "dark" {
                return NSColor(srgbRed: 0.106, green: 0.106, blue: 0.098, alpha: 1)
            }
            return NSColor(srgbRed: 0.965, green: 0.965, blue: 0.957, alpha: 1)
        }

        private func installLeftAccessory(in window: NSWindow) {
            guard accessory(in: window, identifier: Self.leftAccessoryIdentifier) == nil else {
                updateTargets(in: window, accessoryIdentifier: Self.leftAccessoryIdentifier)
                return
            }
            let toggle = makeSymbolButton(
                identifier: NSUserInterfaceItemIdentifier("org.sonicfield.oida.toggle-left"),
                symbol: "sidebar.left",
                label: "Toggle left sidebar",
                help: "Show or hide the left sidebar",
                action: #selector(toggleLeftSidebar)
            )
            let system = sourceButton(
                identifier: Self.sourceIdentifiers["system"]!,
                symbol: "speaker.wave.1",
                label: "System audio",
                help: "Configure system audio listening",
                action: #selector(selectSystemSource(_:))
            )
            let microphone = sourceButton(
                identifier: Self.sourceIdentifiers["mic"]!,
                symbol: "mic",
                label: "Microphone",
                help: "Configure microphone listening",
                action: #selector(selectMicrophoneSource(_:))
            )
            let file = sourceButton(
                identifier: Self.sourceIdentifiers["file"]!,
                symbol: "folder",
                label: "Audio file",
                help: "Choose an audio file",
                action: #selector(selectFileSource(_:))
            )
            installAccessory(
                in: window,
                identifier: Self.leftAccessoryIdentifier,
                buttons: [toggle, system, microphone, file],
                placement: .left
            )
        }

        private func installRightAccessory(in window: NSWindow) {
            guard accessory(in: window, identifier: Self.rightAccessoryIdentifier) == nil else {
                updateTargets(in: window, accessoryIdentifier: Self.rightAccessoryIdentifier)
                return
            }
            let buttons = [
                makeSymbolButton(
                    identifier: NSUserInterfaceItemIdentifier("org.sonicfield.oida.floating-listener"),
                    symbol: "wave.3.right",
                    label: "Floating listener",
                    help: "Show or hide the floating listener",
                    action: #selector(toggleFloatingListener)
                ),
                makeSymbolButton(
                    identifier: NSUserInterfaceItemIdentifier("org.sonicfield.oida.settings"),
                    symbol: "slider.horizontal.3",
                    label: "Settings",
                    help: "Open Oída settings",
                    action: #selector(openSettings)
                ),
                makeSymbolButton(
                    identifier: NSUserInterfaceItemIdentifier("org.sonicfield.oida.toggle-right"),
                    symbol: "sidebar.right",
                    label: "Toggle right sidebar",
                    help: "Show or hide the right sidebar",
                    action: #selector(toggleRightSidebar)
                ),
            ]
            installAccessory(
                in: window,
                identifier: Self.rightAccessoryIdentifier,
                buttons: buttons,
                placement: .right
            )
        }

        private func installAccessory(
            in window: NSWindow,
            identifier: NSUserInterfaceItemIdentifier,
            buttons: [TitlebarButton],
            placement: NSLayoutConstraint.Attribute
        ) {
            let stack = NSStackView(views: buttons)
            stack.orientation = .horizontal
            stack.alignment = .centerY
            stack.spacing = 7
            stack.translatesAutoresizingMaskIntoConstraints = false

            let leadingInset: CGFloat = placement == .left ? 6 : 0
            let trailingInset: CGFloat = placement == .right ? 16 : 0
            let buttonLength: CGFloat = 30
            let gapWidth = CGFloat(max(0, buttons.count - 1)) * stack.spacing
            let width = CGFloat(buttons.count) * buttonLength + gapWidth + leadingInset + trailingInset
            let accessoryView = NSView(frame: NSRect(x: 0, y: 0, width: width, height: 40))
            accessoryView.identifier = identifier
            accessoryView.addSubview(stack)
            NSLayoutConstraint.activate([
                accessoryView.widthAnchor.constraint(equalToConstant: width),
                accessoryView.heightAnchor.constraint(equalToConstant: 40),
                stack.leadingAnchor.constraint(equalTo: accessoryView.leadingAnchor, constant: leadingInset),
                stack.trailingAnchor.constraint(equalTo: accessoryView.trailingAnchor, constant: -trailingInset),
                stack.centerYAnchor.constraint(equalTo: accessoryView.centerYAnchor),
                stack.heightAnchor.constraint(equalToConstant: buttonLength),
            ])

            let accessory = NSTitlebarAccessoryViewController()
            accessory.view = accessoryView
            accessory.layoutAttribute = placement
            window.addTitlebarAccessoryViewController(accessory)
        }

        private func sourceButton(
            identifier: NSUserInterfaceItemIdentifier,
            symbol: String,
            label: String,
            help: String,
            action: Selector
        ) -> TitlebarButton {
            let button = makeSymbolButton(
                identifier: identifier,
                symbol: symbol,
                label: label,
                help: help,
                action: action
            )
            button.usesContrastSelection = true
            return button
        }

        private func makeSymbolButton(
            identifier: NSUserInterfaceItemIdentifier,
            symbol: String,
            label: String,
            help: String,
            action: Selector
        ) -> TitlebarButton {
            let baseImage = NSImage(systemSymbolName: symbol, accessibilityDescription: label) ?? NSImage()
            let image = baseImage.withSymbolConfiguration(
                NSImage.SymbolConfiguration(pointSize: 13, weight: .regular)
            ) ?? baseImage
            image.isTemplate = true
            let button = TitlebarButton(frame: .zero)
            button.image = image
            button.target = self
            button.action = action
            button.identifier = identifier
            button.imagePosition = .imageOnly
            button.imageScaling = .scaleProportionallyDown
            button.contentTintColor = .secondaryLabelColor
            button.toolTip = help
            button.setAccessibilityLabel(label)
            button.translatesAutoresizingMaskIntoConstraints = false
            NSLayoutConstraint.activate([
                button.widthAnchor.constraint(equalToConstant: 30),
                button.heightAnchor.constraint(equalToConstant: 30),
            ])
            return button
        }

        private func accessory(in window: NSWindow, identifier: NSUserInterfaceItemIdentifier) -> NSTitlebarAccessoryViewController? {
            window.titlebarAccessoryViewControllers.first(where: { $0.view.identifier == identifier })
        }

        private func buttons(in view: NSView) -> [TitlebarButton] {
            view.subviews.flatMap { child -> [TitlebarButton] in
                let current = (child as? TitlebarButton).map { [$0] } ?? []
                return current + buttons(in: child)
            }
        }

        private func updateTargets(in window: NSWindow, accessoryIdentifier: NSUserInterfaceItemIdentifier) {
            guard let view = accessory(in: window, identifier: accessoryIdentifier)?.view else { return }
            for button in buttons(in: view) {
                button.target = self
            }
        }

        private func updateSourceSelection(in window: NSWindow) {
            guard let view = accessory(in: window, identifier: Self.leftAccessoryIdentifier)?.view else { return }
            for button in buttons(in: view) {
                let source = Self.sourceIdentifiers.first(where: { $0.value == button.identifier })?.key
                button.isSelected = source == selectedSource
            }
        }

        private func menuView(for source: String) -> TitlebarSourceMenu {
            TitlebarSourceMenu(
                source: source,
                captureSeconds: captureSeconds,
                direction: direction,
                micLevel: micLevel,
                onCaptureSeconds: { [weak self] seconds in self?.onChangeCaptureSeconds(seconds) },
                onDirection: { [weak self] direction in self?.onChangeDirection(direction) },
                onChooseFile: { [weak self] in
                    self?.sourcePopover?.performClose(nil)
                    self?.onChooseFile()
                }
            )
        }

        private func presentSourcePopover(_ source: String, from sender: NSButton) {
            if sourcePopover?.isShown == true, popoverSource == source {
                sourcePopover?.performClose(nil)
                return
            }
            sourcePopover?.performClose(nil)
            onSelectSource(source)
            popoverSource = source

            let popover = NSPopover()
            popover.behavior = .transient
            popover.animates = true
            popover.delegate = self
            popover.contentSize = NSSize(width: 276, height: source == "file" ? 116 : (source == "mic" ? 174 : 142))
            popover.contentViewController = NSHostingController(rootView: menuView(for: source))
            sourcePopover = popover
            popover.show(relativeTo: sender.bounds, of: sender, preferredEdge: .minY)
        }

        func refreshOpenSourcePopover() {
            guard let source = popoverSource,
                  let host = sourcePopover?.contentViewController as? NSHostingController<TitlebarSourceMenu>
            else { return }
            host.rootView = menuView(for: source)
        }

        func popoverDidClose(_ notification: Notification) {
            guard let closed = notification.object as? NSPopover, closed === sourcePopover else { return }
            sourcePopover = nil
            popoverSource = nil
        }

        @objc private func toggleLeftSidebar() { onToggleLeft() }
        @objc private func selectSystemSource(_ sender: NSButton) { presentSourcePopover("system", from: sender) }
        @objc private func selectMicrophoneSource(_ sender: NSButton) { presentSourcePopover("mic", from: sender) }
        @objc private func selectFileSource(_ sender: NSButton) { presentSourcePopover("file", from: sender) }
        @objc private func toggleFloatingListener() { onToggleFloating() }
        @objc private func openSettings() { onOpenSettings() }
        @objc private func toggleRightSidebar() { onToggleRight() }
    }

    private final class WindowChromeView: NSView {
        weak var coordinator: Coordinator?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            configureWindow()
        }

        func configureWindow() {
            guard let window, let coordinator else { return }
            coordinator.configure(window)
        }
    }
}

private struct TitlebarSourceMenu: View {
    let source: String
    let captureSeconds: Double
    let direction: String
    let micLevel: Double
    let onCaptureSeconds: (Double) -> Void
    let onDirection: (String) -> Void
    let onChooseFile: () -> Void

    private var title: String {
        switch source {
        case "mic": "Microphone"
        case "file": "Audio file"
        default: "System audio"
        }
    }

    private var symbol: String {
        switch source {
        case "mic": "mic"
        case "file": "folder"
        default: "speaker.wave.2"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            Label(title, systemImage: symbol)
                .font(.system(size: 13, weight: .semibold))

            if source == "file" {
                Text("Add an audio or video file to the current listening session.")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Choose file…", action: onChooseFile)
                    .buttonStyle(.borderless)
            } else {
                HStack(spacing: 8) {
                    Text("Buffer")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                    Spacer()
                    Picker("Buffer", selection: Binding(get: { captureSeconds }, set: onCaptureSeconds)) {
                        Text("5 sec").tag(5.0)
                        Text("10 sec").tag(10.0)
                        Text("30 sec").tag(30.0)
                        Text("60 sec").tag(60.0)
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .frame(width: 84)
                    directionControl
                }

                if source == "mic" {
                    HStack(spacing: 9) {
                        Text("Default input")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                        GeometryReader { proxy in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Color.secondary.opacity(0.13))
                                Capsule()
                                    .fill(Color.primary.opacity(0.65))
                                    .frame(width: proxy.size.width * max(0, min(1, micLevel)))
                            }
                        }
                        .frame(height: 3)
                    }
                }
            }
        }
        .padding(14)
        .frame(width: 276, alignment: .leading)
    }

    private var directionControl: some View {
        HStack(spacing: 3) {
            directionButton("past", symbol: "arrow.counterclockwise", help: "Use the preceding buffer")
            directionButton("future", symbol: "arrow.clockwise", help: "Record after Listen")
        }
    }

    private func directionButton(_ value: String, symbol: String, help: String) -> some View {
        let selected = direction == value
        return Button {
            onDirection(value)
        } label: {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: selected ? .semibold : .regular))
                .foregroundStyle(selected ? Color.primary : Color.secondary.opacity(0.58))
                .frame(width: 24, height: 22)
        }
        .buttonStyle(.plain)
        .help(help)
        .accessibilityLabel(value == "past" ? "Past" : "Future")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// A borderless titlebar button. Selection and hover are communicated only
/// through icon contrast so the chrome remains quiet and surface-free.
private final class TitlebarButton: NSButton {
    private var tracking: NSTrackingArea?
    private var hovering = false
    var usesContrastSelection = false { didSet { updateSurface() } }
    var isSelected = false { didSet { updateSurface() } }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        configure()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        configure()
    }

    private func configure() {
        isBordered = false
        bezelStyle = .inline
        focusRingType = .none
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.activeInActiveApp, .mouseEnteredAndExited, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        tracking = area
    }

    override func mouseEntered(with event: NSEvent) {
        hovering = true
        updateSurface()
        super.mouseEntered(with: event)
    }

    override func mouseExited(with event: NSEvent) {
        hovering = false
        updateSurface()
        super.mouseExited(with: event)
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateSurface()
    }

    private func updateSurface() {
        layer?.backgroundColor = nil
        if usesContrastSelection {
            contentTintColor = isSelected
                ? .labelColor
                : (hovering ? .labelColor : .tertiaryLabelColor)
            return
        }
        contentTintColor = hovering ? .labelColor : .tertiaryLabelColor
    }
}
