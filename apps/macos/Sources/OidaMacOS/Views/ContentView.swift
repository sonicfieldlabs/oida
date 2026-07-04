import SwiftUI
import WebKit

/// The control center IS the daemon's dashboard — the same page a browser
/// shows, embedded, so the mac app and the web dashboard never diverge. A
/// slim native strip on top carries what only the shell can do (supervise the
/// daemon, toggle the floating listener).
struct ContentView: View {
    @EnvironmentObject private var store: ShellStore
    @State private var reloadToken = 0

    var body: some View {
        VStack(spacing: 0) {
            strip
            Divider()
            DashboardWebView(urlString: store.daemonBaseURL, reloadToken: reloadToken)
                .id(store.daemonBaseURL)
        }
        // The dashboard is light-only; keep the titlebar and strip light too.
        .preferredColorScheme(.light)
    }

    private var strip: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(store.daemonOnline ? Color.green : Color.orange)
                .frame(width: 8, height: 8)
            Text(store.daemonOnline ? "daemon \(store.daemonBaseURL)" : "daemon offline")
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Text(store.engineLabel)
                .font(.system(size: 11))
                .foregroundStyle(.tertiary)

            Spacer()

            if store.daemonOnline {
                Button("Floating listener") { store.toggleFloatingListener() }
                    .font(.system(size: 11.5))
                Button("Reload") { reloadToken += 1 }
                    .font(.system(size: 11.5))
                Button("Browser") { store.openDashboard() }
                    .font(.system(size: 11.5))
            } else {
                Button(store.isStartingDaemon ? "Starting…" : "Start daemon") {
                    Task {
                        await store.startDaemon()
                        reloadToken += 1
                    }
                }
                .disabled(store.isStartingDaemon)
                .font(.system(size: 11.5))
            }
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(Color(red: 0.945, green: 0.945, blue: 0.937))
        .onChange(of: store.daemonOnline) { online in
            if online { reloadToken += 1 }
        }
    }
}

struct DashboardWebView: NSViewRepresentable {
    let urlString: String
    let reloadToken: Int

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.setValue(false, forKey: "drawsBackground")
        if let url = URL(string: urlString) {
            webView.load(URLRequest(url: url))
        }
        context.coordinator.lastReloadToken = reloadToken
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        if context.coordinator.lastReloadToken != reloadToken {
            context.coordinator.lastReloadToken = reloadToken
            if let url = URL(string: urlString) {
                webView.load(URLRequest(url: url))
            }
        }
    }

    final class Coordinator: NSObject, WKUIDelegate {
        var lastReloadToken = 0

        // Allow the dashboard's mic recording without a browser prompt; the
        // OS-level microphone permission still applies.
        func webView(
            _ webView: WKWebView,
            requestMediaCapturePermissionFor origin: WKSecurityOrigin,
            initiatedByFrame frame: WKFrameInfo,
            type: WKMediaCaptureType,
            decisionHandler: @escaping (WKPermissionDecision) -> Void
        ) {
            decisionHandler(type == .microphone ? .grant : .deny)
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
