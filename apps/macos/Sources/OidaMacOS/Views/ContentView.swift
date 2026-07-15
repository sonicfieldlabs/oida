import SwiftUI
import WebKit

/// Holds a weak reference to the embedded dashboard WebView.
final class WebViewHolder: ObservableObject {
    weak var webView: WKWebView?
}

/// The control center IS the daemon's dashboard — the same page a browser
/// shows, embedded, so the mac app and the web dashboard never diverge. The
/// page's own controls carry listening and result actions. Sidebar collapse is
/// native window chrome so it stays reachable even when a rail is closed. The
/// only native content overlay is a Start button when the daemon is offline.
struct ContentView: View {
    @EnvironmentObject private var store: ShellStore
    @StateObject private var web = WebViewHolder()
    @State private var reloadToken = 0

    var body: some View {
        ZStack {
            DashboardWebView(
                urlString: store.daemonBaseURL,
                reloadToken: reloadToken,
                holder: web,
                nativeState: dashboardNativeState,
                onShellMessage: handleShellMessage
            )
            .id(store.daemonBaseURL)
            // A small continuation of the same shell surface gives the native
            // titlebar the calmer Codex-like vertical margin without adding a
            // second toolbar row or disturbing the centered session title.
            .padding(.top, 4)

            if !store.daemonOnline {
                offlineOverlay
            }
        }
        // Match the selected semantic appearance so the transparent WebView
        // never flashes the opposite theme during navigation.
        .background(chromeBackgroundColor.ignoresSafeArea())
        .preferredColorScheme(store.preferredColorScheme)
        .background(WindowChromeConfigurator(
            title: store.currentSessionName,
            appearance: store.appearanceMode,
            selectedSource: store.selectedSource,
            captureSeconds: store.selectedCaptureSeconds,
            direction: store.selectedDirection,
            micLevel: store.micLevel,
            onToggleLeft: { toggleDashboardSidebar("left") },
            onSelectSource: { source in selectDashboardSource(source) },
            onChangeCaptureSeconds: { store.selectedCaptureSeconds = $0 },
            onChangeDirection: { store.selectedDirection = $0 },
            onChooseFile: {
                store.selectedSource = "file"
                Task { await store.listenNow(source: "file") }
            },
            onOpenSettings: { openDashboardPanel("settings") },
            onToggleRight: { toggleDashboardSidebar("right") }
        ))
        .onChange(of: store.daemonOnline) { online in
            if online { reloadToken += 1 }
        }
    }

    private var chromeBackgroundColor: Color {
        store.appearanceMode == "dark"
            ? Color(red: 0.106, green: 0.106, blue: 0.098)
            : Color(red: 0.965, green: 0.965, blue: 0.957)
    }

    private var offlineOverlay: some View {
        VStack(spacing: 12) {
            Text("daemon offline")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
            Button(store.isStartingDaemon ? "Starting…" : "Start daemon") {
                Task {
                    await store.startDaemon()
                    reloadToken += 1
                }
            }
            .disabled(store.isStartingDaemon)
        }
        .padding(28)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var dashboardNativeState: DashboardNativeState {
        DashboardNativeState(
            phase: store.listeningPhase.rawValue,
            source: store.selectedSource,
            preset: store.selectedPreset,
            direction: store.selectedDirection,
            captureSeconds: store.selectedCaptureSeconds,
            progress: store.listeningProgress,
            secondsRemaining: store.listeningSecondsRemaining,
            status: store.listeningStatusText,
            error: store.errorMessage,
            eventId: store.floatingEvent?.id ?? store.latestEvent?.id,
            logs: store.daemonLogLines,
            customMode: store.customSkillIDs != nil,
            selectedSkillIDs: store.customSkillIDs,
            musicIDEnabled: store.musicIDEnabled,
            appearance: store.appearanceMode
        )
    }

    private func toggleDashboardSidebar(_ side: String) {
        web.webView?.evaluateJavaScript("window.oidaToggleSidebar?.('\(side)');")
    }

    private func selectDashboardSource(_ source: String) {
        store.selectedSource = source
        web.webView?.evaluateJavaScript("window.oidaSelectSource?.('\(source)');")
    }

    private func openDashboardPanel(_ panel: String) {
        web.webView?.evaluateJavaScript("window.oidaOpenPanel?.('\(panel)');")
    }

    private func handleShellMessage(_ message: DashboardShellMessage) {
        if let source = message.source, ["system", "mic", "file"].contains(source) {
            store.selectedSource = source
        }
        if let preset = message.preset, !preset.isEmpty {
            store.selectedPreset = preset
        }
        if let direction = message.direction, ["past", "future"].contains(direction) {
            store.selectedDirection = direction
        }
        if let seconds = message.seconds, seconds >= 0.25, seconds <= 600 {
            store.selectedCaptureSeconds = seconds
        }
        if let customMode = message.customMode {
            store.customSkillIDs = customMode ? message.selectedSkillIDs : nil
        }
        if let musicIDEnabled = message.musicIDEnabled {
            store.musicIDEnabled = musicIDEnabled
        }
        if let appearance = message.appearance {
            store.appearanceMode = appearance == "dark" ? "dark" : "light"
        }
        if let sessionName = message.sessionName {
            store.currentSessionName = sessionName
        }

        switch message.action {
        case "floating":
            store.toggleFloatingListener()
        case "browser":
            store.openDashboard()
        case "capture-permission":
            store.openSystemAudioCaptureSettings()
        case "listen":
            Task {
                await store.listenNow(
                    seconds: message.seconds,
                    preset: message.preset,
                    source: message.source,
                    direction: message.direction,
                    enabledSkillIDs: message.customMode == true ? message.selectedSkillIDs : nil,
                    musicID: message.musicIDEnabled
                )
            }
        case "stop":
            store.stopListening()
        default:
            break // sync-only updates and reload require no further action
        }
    }
}

struct DashboardWebView: NSViewRepresentable {
    let urlString: String
    let reloadToken: Int
    let holder: WebViewHolder
    let nativeState: DashboardNativeState
    let onShellMessage: (DashboardShellMessage) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        // Tell the page it runs inside the native shell (reveals the shell-action
        // icons in its right rail) and receive those actions back.
        let controller = WKUserContentController()
        controller.addUserScript(WKUserScript(
            source: "window.__oidaNative = true;",
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
        controller.add(context.coordinator, name: "oidaShell")
        configuration.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.uiDelegate = context.coordinator
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.setValue(false, forKey: "drawsBackground")
        holder.webView = webView
        context.coordinator.onShellMessage = onShellMessage
        context.coordinator.pendingNativeState = nativeState
        if let url = URL(string: urlString) {
            webView.load(URLRequest(url: url))
        }
        context.coordinator.lastReloadToken = reloadToken
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        holder.webView = webView
        context.coordinator.onShellMessage = onShellMessage
        context.coordinator.push(nativeState, to: webView)
        if context.coordinator.lastReloadToken != reloadToken {
            context.coordinator.lastReloadToken = reloadToken
            if let url = URL(string: urlString) {
                webView.load(URLRequest(url: url))
            }
        }
    }

    static func dismantleNSView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "oidaShell")
    }

    final class Coordinator: NSObject, WKUIDelegate, WKNavigationDelegate, WKScriptMessageHandler {
        var lastReloadToken = 0
        var onShellMessage: ((DashboardShellMessage) -> Void)?
        var pendingNativeState: DashboardNativeState?
        private var lastNativeState: DashboardNativeState?

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let shellMessage = DashboardShellMessage(body: message.body) else { return }
            if shellMessage.action == "reload" {
                message.webView?.reload()
                return
            }
            onShellMessage?(shellMessage)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            if let pendingNativeState {
                push(pendingNativeState, to: webView, force: true)
            }
        }

        func push(_ state: DashboardNativeState, to webView: WKWebView, force: Bool = false) {
            pendingNativeState = state
            guard force || state != lastNativeState else { return }
            guard !webView.isLoading else { return }
            guard let data = try? JSONEncoder().encode(state),
                  let json = String(data: data, encoding: .utf8) else { return }
            webView.evaluateJavaScript("window.oidaNativeState?.(\(json));") { [weak self] _, error in
                guard error == nil else { return }
                self?.lastNativeState = state
            }
        }

        // Allow the dashboard's mic recording without a browser prompt; the
        // OS-level microphone permission still applies.
        func webView(
            _ webView: WKWebView,
            requestMediaCapturePermissionFor origin: WKSecurityOrigin,
            initiatedByFrame frame: WKFrameInfo,
            type: WKMediaCaptureType,
            decisionHandler: @escaping @MainActor @Sendable (WKPermissionDecision) -> Void
        ) {
            let localHosts = Set(["127.0.0.1", "localhost", "::1"])
            let isLocalDashboard = localHosts.contains(origin.host.lowercased())
            decisionHandler(type == .microphone && isLocalDashboard ? .grant : .deny)
        }

        // target=_blank links (API docs, health) open in the default browser
        // so the embedded dashboard never navigates away.
        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if let url = navigationAction.request.url {
                NSWorkspace.shared.open(url)
            }
            return nil
        }
    }
}
