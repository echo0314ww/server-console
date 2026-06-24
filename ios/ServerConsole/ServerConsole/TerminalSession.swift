import Foundation
import SwiftUI
import WebKit
import UIKit

struct TerminalWebView: UIViewRepresentable {
    let pageURL: URL
    let hideChrome: Bool

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.userContentController = WKUserContentController()
        installBootstrapScript(in: configuration.userContentController)

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.scrollView.bounces = false
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.isOpaque = true
        webView.backgroundColor = UIColor(red: 0.04, green: 0.05, blue: 0.06, alpha: 1.0)
        let signature = configurationSignature
        context.coordinator.loadedSignature = signature
        webView.load(URLRequest(url: pageURL))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        let signature = configurationSignature
        guard context.coordinator.loadedSignature != signature else { return }
        context.coordinator.loadedSignature = signature
        installBootstrapScript(in: webView.configuration.userContentController)
        webView.load(URLRequest(url: pageURL))
    }

    private func installBootstrapScript(in controller: WKUserContentController) {
        controller.removeAllUserScripts()

        let websocketURL = pageURL.terminalWebSocketURL()?.absoluteString ?? ""
        let payload: [String: Any] = [
            "serverUrl": websocketURL,
            "hideChrome": hideChrome
        ]
        let data = (try? JSONSerialization.data(withJSONObject: payload, options: [])) ?? Data()
        let json = String(data: data, encoding: .utf8) ?? "{}"
        let source = "window.__serverConsoleNativeBootstrap = \(json); window.__serverConsoleNativeBootstrapApplied = true;"
        controller.addUserScript(
            WKUserScript(
                source: source,
                injectionTime: .atDocumentStart,
                forMainFrameOnly: true
            )
        )
    }

    private var configurationSignature: String {
        [
            pageURL.absoluteString,
            hideChrome ? "1" : "0"
        ].joined(separator: "\u{1F}")
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var loadedSignature = ""
    }
}

private extension URL {
    func terminalWebSocketURL() -> URL? {
        guard var components = URLComponents(url: self, resolvingAgainstBaseURL: false) else {
            return nil
        }

        switch components.scheme?.lowercased() {
        case "https":
            components.scheme = "wss"
        case "http":
            components.scheme = "ws"
        case "wss", "ws":
            break
        default:
            components.scheme = "ws"
        }

        components.path = "/terminal"
        components.queryItems = [URLQueryItem(name: "session", value: "phone")]
        components.fragment = nil
        return components.url
    }
}
