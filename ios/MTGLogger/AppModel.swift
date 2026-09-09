import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var serverText: String
    @Published var configured: Bool
    @Published var defaults: ScanDefaults {
        didSet { if let data = try? JSONEncoder().encode(defaults) { UserDefaults.standard.set(data, forKey: "scanDefaults") } }
    }
    @Published var pending: [PendingScan] = []
    @Published var result: ScanResult?
    @Published var error: String?
    @Published var checking = false
    @Published var uploading = false
    @Published var decks: [DeckChoice] = []
    @Published var scanned = 0
    @Published var added = 0
    @Published var reviewed = 0
    @Published var refreshID = UUID()
    private var outbox: ScanOutbox?

    var server: URL? { ServerAddress.parse(serverText) }
    var canCapture: Bool { configured && server != nil && !uploading && pending.count < 20 && outbox != nil }

    init() {
        serverText = UserDefaults.standard.string(forKey: "server") ?? "http://192.168.1.254:5173"
        configured = UserDefaults.standard.bool(forKey: "configured")
        defaults = UserDefaults.standard.data(forKey: "scanDefaults")
            .flatMap { try? JSONDecoder().decode(ScanDefaults.self, from: $0) } ?? ScanDefaults()
        do { outbox = try ScanOutbox(); pending = try outbox!.all() }
        catch { self.error = "Unable to open saved scans: \(error.localizedDescription)" }
    }

    func connect(to address: String) async {
        guard let server = ServerAddress.parse(address) else { error = "Enter a server address such as http://192.168.1.254:5173 or your HTTPS address."; return }
        checking = true
        defer { checking = false }
        do {
            try await MTGAPI(server: server).checkConnection()
            decks = (try? await MTGAPI(server: server).decks()) ?? []
            configured = true
            serverText = server.absoluteString
            UserDefaults.standard.set(serverText, forKey: "server")
            UserDefaults.standard.set(true, forKey: "configured")
            error = nil
            refreshID = UUID()
        } catch { self.error = "Could not connect. Join your home Wi-Fi and check the server address. \(error.localizedDescription)" }
    }

    func capture(_ jpeg: Data) async {
        guard let server, let outbox else { return }
        let scan = PendingScan(server: server, defaults: defaults)
        do {
            try outbox.save(scan, jpeg: jpeg)
            pending = try outbox.all()
            await retryPending()
        } catch { self.error = "The capture could not be saved. Keep this card nearby. \(error.localizedDescription)" }
    }

    func retryPending() async {
        guard !uploading, let outbox else { return }
        uploading = true
        error = nil
        defer { uploading = false }
        do {
            // Every queued capture retains its original server and batch
            // defaults, even if Settings changes before the retry.
            for scan in try outbox.all() {
                let api = MTGAPI(server: scan.server)
                try await api.checkConnection()
                let next = try await api.upload(scan, jpeg: outbox.image(scan))
                try outbox.remove(scan)
                pending = try outbox.all()
                result = next
                if next.disposition != "empty" {
                    scanned += 1
                    if next.disposition == "added" { added += 1 } else { reviewed += 1 }
                }
                UIImpactFeedbackGenerator(style: next.disposition == "added" ? .light : .heavy).impactOccurred()
                refreshID = UUID()
            }
        } catch {
            self.error = "Scan saved on this iPhone. Reconnect and tap Retry; keep unconfirmed cards aside. \(error.localizedDescription)"
        }
    }
}
