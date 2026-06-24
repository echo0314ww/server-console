import SwiftUI

private let defaultPageURL = URL(string: "http://127.0.0.1:8765/")!

struct ContentView: View {
    @State private var draftServerURL = defaultPageURL.absoluteString
    @State private var draftChromeHidden = true

    @State private var loadedPageURL = defaultPageURL
    @State private var loadedChromeHidden = true

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                connectionPanel
                Divider()
                terminalSurface
            }
            .navigationTitle("Server Console")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var connectionPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField("http://server:8765/", text: $draftServerURL)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.system(.footnote, design: .monospaced))
                .textFieldStyle(.roundedBorder)

            Toggle("Hide browser chrome", isOn: $draftChromeHidden)
                .font(.caption)

            Button("Load") {
                applyDraftConfiguration()
            }
            .buttonStyle(.borderedProminent)
            .disabled(draftServerURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(12)
        .background(Color(.secondarySystemBackground))
    }

    private var terminalSurface: some View {
        Group {
            TerminalWebView(pageURL: loadedPageURL, hideChrome: loadedChromeHidden)
        }
        .ignoresSafeArea(.keyboard, edges: .bottom)
        .background(Color.black)
    }

    private func applyDraftConfiguration() {
        loadedPageURL = pageURL(from: draftServerURL)
        loadedChromeHidden = draftChromeHidden
    }

    private func pageURL(from value: String) -> URL {
        let candidate = value.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !candidate.isEmpty else { return defaultPageURL }
        guard var components = URLComponents(string: candidate) else { return defaultPageURL }
        if components.host == nil { return defaultPageURL }

        switch components.scheme?.lowercased() {
        case "ws":
            components.scheme = "http"
        case "wss":
            components.scheme = "https"
        case "http", "https":
            break
        default:
            components.scheme = "http"
        }

        components.path = "/"
        components.query = nil
        components.fragment = nil
        return components.url ?? defaultPageURL
    }
}

#Preview {
    ContentView()
}
