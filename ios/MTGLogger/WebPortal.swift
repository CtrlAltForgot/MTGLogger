import SwiftUI
import WebKit

struct SharedFile: Identifiable { let id = UUID(); let url: URL }

struct WebPortal: View {
    let server: URL
    let page: String
    let refreshID: UUID
    @State private var failure: String?
    @State private var sharedFile: SharedFile?
    @State private var retryID = UUID()

    var body: some View {
        ZStack {
            WebSurface(server: server, page: page, refreshID: refreshID, failure: $failure, sharedFile: $sharedFile)
                .id(retryID)
            if let failure {
                ContentUnavailableView {
                    Label("Server unavailable", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(failure)
                } actions: {
                    Button("Retry") { self.failure = nil; retryID = UUID() }.buttonStyle(.borderedProminent)
                }.background(Color(.systemBackground))
            }
        }
        .sheet(item: $sharedFile) { file in ActivitySheet(url: file.url) }
    }
}

struct ActivitySheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

struct WebSurface: UIViewRepresentable {
    let server: URL
    let page: String
    let refreshID: UUID
    @Binding var failure: String?
    @Binding var sharedFile: SharedFile?

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        let web = WKWebView(frame: .zero, configuration: configuration)
        web.isOpaque = false
        web.backgroundColor = UIColor(red: 0.07, green: 0.035, blue: 0.045, alpha: 1)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.allowsBackForwardNavigationGestures = true
        return web
    }
    func updateUIView(_ web: WKWebView, context: Context) {
        context.coordinator.parent = self
        let key = "\(server)|\(page)|\(refreshID)"
        if context.coordinator.key != key {
            context.coordinator.key = key
            var url = URLComponents(url: server, resolvingAgainstBaseURL: false)!
            url.queryItems = [URLQueryItem(name: "page", value: page)]
            web.load(URLRequest(url: url.url!))
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        var parent: WebSurface
        var key = ""
        private var downloads: [ObjectIdentifier: URL] = [:]
        init(_ parent: WebSurface) { self.parent = parent }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { parent.failure = nil }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if (error as NSError).code != NSURLErrorCancelled { parent.failure = "Connect to your MTGLogger network and try again. \(error.localizedDescription)" }
        }
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { decisionHandler(.cancel); return }
            let sameOrigin = url.host == parent.server.host && url.scheme == parent.server.scheme && url.port == parent.server.port
            if sameOrigin || url.scheme == "blob" {
                decisionHandler(action.shouldPerformDownload ? .download : .allow)
            } else {
                if url.scheme == "https" { UIApplication.shared.open(url) }
                decisionHandler(.cancel)
            }
        }
        func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().contains("attachment") ?? false
            decisionHandler(attachment || !response.canShowMIMEType ? .download : .allow)
        }
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if action.targetFrame == nil, let url = action.request.url, url.scheme == "https" { UIApplication.shared.open(url) }
            return nil
        }
        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            do {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let destination = folder.appendingPathComponent(URL(fileURLWithPath: suggestedFilename).lastPathComponent)
                downloads[ObjectIdentifier(download)] = destination
                completionHandler(destination)
            } catch { completionHandler(nil); parent.failure = error.localizedDescription }
        }
        func downloadDidFinish(_ download: WKDownload) {
            if let url = downloads.removeValue(forKey: ObjectIdentifier(download)) { parent.sharedFile = SharedFile(url: url) }
        }
        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            downloads.removeValue(forKey: ObjectIdentifier(download))
            parent.failure = "Export failed: \(error.localizedDescription)"
        }
    }
}
