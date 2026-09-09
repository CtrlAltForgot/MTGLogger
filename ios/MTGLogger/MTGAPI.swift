import Foundation

struct MTGAPI {
    let server: URL

    func checkConnection() async throws {
        struct Capabilities: Decodable { let capture_id: Bool?; let full_photo: Bool? }
        let capabilities: Capabilities = try await get("scanner/capabilities")
        guard capabilities.capture_id == true, capabilities.full_photo == true else {
            throw APIError("Update your MTGLogger server before scanning. This app requires safe capture retries and full-photo recognition.")
        }
    }

    func decks() async throws -> [DeckChoice] { try await get("decks") }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var request = URLRequest(url: server.appendingPathComponent("api/" + path))
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(data, response)
        return try JSONDecoder().decode(T.self, from: data)
    }

    func upload(_ scan: PendingScan, jpeg: Data) async throws -> ScanResult {
        let boundary = "MTGLogger-\(scan.id.uuidString)"
        var body = Data()
        func append(_ text: String) { body.append(Data(text.utf8)) }
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"capture_id\"\r\n\r\n\(scan.id.uuidString)\r\n")
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"full_photo\"\r\n\r\ntrue\r\n")
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"defaults_json\"\r\n\r\n")
        body.append(try JSONEncoder().encode(scan.defaults))
        append("\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n")
        body.append(jpeg)
        append("\r\n--\(boundary)--\r\n")
        var request = URLRequest(url: scan.server.appendingPathComponent("api/scanner/recognize"))
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.upload(for: request, from: body)
        try validate(data, response)
        return try JSONDecoder().decode(ScanResult.self, from: data)
    }

    private func validate(_ data: Data, _ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError("No response from your server.") }
        guard (200..<300).contains(http.statusCode) else {
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            throw APIError(json?["detail"] as? String ?? "Server returned HTTP \(http.statusCode).")
        }
    }
}

struct APIError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
