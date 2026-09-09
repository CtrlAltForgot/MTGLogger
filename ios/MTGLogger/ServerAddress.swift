import Foundation

enum ServerAddress {
    static func parse(_ text: String) -> URL? {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.contains("://") { value = "http://" + value }
        guard var parts = URLComponents(string: value),
              ["http", "https"].contains(parts.scheme?.lowercased() ?? ""),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else { return nil }
        // Plain HTTP is only permitted for the user's local server. The ATS
        // exception is needed for numeric LAN addresses on older iOS versions.
        if parts.scheme == "http" && !isLocal(host) { return nil }
        parts.path = ""
        return parts.url
    }

    static func isLocal(_ host: String) -> Bool {
        if host == "localhost" || host == "::1" || host.hasSuffix(".local") { return true }
        let numbers = host.split(separator: ".").compactMap { Int($0) }
        guard numbers.count == 4, numbers.allSatisfy({ 0...255 ~= $0 }) else { return false }
        return numbers[0] == 10 || numbers[0] == 127
            || (numbers[0] == 192 && numbers[1] == 168)
            || (numbers[0] == 172 && 16...31 ~= numbers[1])
    }
}

struct ScanDefaults: Codable {
    var condition = "near_mint"
    var foil = false
    var language = "en"
    var storage_location = "Unsorted"
    var collection_name = "Main"
    var status = "owned"
    var box_set_code: String?
    var auto_add = true
    var deck_id: String?
}

struct ScanCandidate: Decodable {
    let name: String
    let set_code: String
    let collector_number: String
    let image_url: String?
}

struct ScanInventory: Decodable {
    let card_name: String
    let set_code: String
    let collector_number: String
    let image_url: String?
    let quantity: Int
}

struct ScanResult: Decodable {
    let disposition: String
    let confidence: Double
    let candidates: [ScanCandidate]
    let inventory: ScanInventory?
    let review_id: String?
    let message: String
    let processing_ms: Int

    var name: String { inventory?.card_name ?? candidates.first?.name ?? message }
    var printing: String {
        let set = inventory?.set_code ?? candidates.first?.set_code ?? ""
        let number = inventory?.collector_number ?? candidates.first?.collector_number ?? ""
        return set.isEmpty ? "Saved camera capture" : "\(set.uppercased()) #\(number)"
    }
    var imageURL: URL? {
        (inventory?.image_url ?? candidates.first?.image_url).flatMap(URL.init(string:))
    }
}

struct DeckChoice: Decodable, Identifiable {
    let id: String
    let name: String
}
