import SwiftUI
import WebKit

/// Holds a weak reference to the embedded dashboard WebView.
final class WebViewHolder: ObservableObject {
    weak var webView: WKWebView?
}

/// The control center IS the daemon's dashboard — the same page a browser
/// shows, embedded, so the mac app and the web dashboard never diverge. The
/// page's own right-rail icons carry everything (Skills / Engine / Path open
/// modals in-page; floating listener / reload / browser post back into the
/// shell), so the window has no native chrome at all. The only native overlay
/// is a Start button when the daemon is offline.
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
                onShellAction: handleShellAction
            )
            .id(store.daemonBaseURL)

            if !store.daemonOnline {
                offlineOverlay
            }
        }
        // Match the dashboard's flat surface so the transparent WebView never flashes.
        .background(Color(red: 0.965, green: 0.965, blue: 0.957))
        .preferredColorScheme(.light)
        .onChange(of: store.daemonOnline) { online in
            if online { reloadToken += 1 }
        }
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

    private func handleShellAction(_ action: String) {
        switch action {
        case "floating":
            store.toggleFloatingListener()
        case "browser":
            store.openDashboard()
        default:
            break // "reload" is handled directly on the WebView
        }
    }
}

struct DashboardWebView: NSViewRepresentable {
    let urlString: String
    let reloadToken: Int
    let holder: WebViewHolder
    let onShellAction: (String) -> Void

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
        webView.allowsBackForwardNavigationGestures = true
        webView.setValue(false, forKey: "drawsBackground")
        holder.webView = webView
        context.coordinator.onShellAction = onShellAction
        if let url = URL(string: urlString) {
            webView.load(URLRequest(url: url))
        }
        context.coordinator.lastReloadToken = reloadToken
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        holder.webView = webView
        context.coordinator.onShellAction = onShellAction
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

    final class Coordinator: NSObject, WKUIDelegate, WKScriptMessageHandler {
        var lastReloadToken = 0
        var onShellAction: ((String) -> Void)?

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let action = message.body as? String else { return }
            if action == "reload" {
                message.webView?.reload()
                return
            }
            onShellAction?(action)
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
