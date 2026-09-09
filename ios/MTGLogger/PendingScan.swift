import Foundation

struct PendingScan: Codable, Identifiable {
    let id: UUID
    let server: URL
    let defaults: ScanDefaults
    let createdAt: Date

    init(server: URL, defaults: ScanDefaults) {
        id = UUID()
        self.server = server
        self.defaults = defaults
        createdAt = Date()
    }
}

struct ScanOutbox {
    let directory: URL

    init(directory: URL? = nil) throws {
        self.directory = try directory ?? FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask,
            appropriateFor: nil, create: true
        ).appendingPathComponent("PendingScans", isDirectory: true)
        try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
    }

    func save(_ scan: PendingScan, jpeg: Data) throws {
        try jpeg.write(to: imageURL(scan), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        try JSONEncoder().encode(scan).write(to: metadataURL(scan), options: .atomic)
    }

    func all() throws -> [PendingScan] {
        try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(PendingScan.self, from: Data(contentsOf: $0)) }
            .sorted { $0.createdAt < $1.createdAt }
    }

    func image(_ scan: PendingScan) throws -> Data { try Data(contentsOf: imageURL(scan)) }
    func remove(_ scan: PendingScan) throws {
        // Remove metadata first: a crash here leaves only an inert orphan JPEG,
        // never a pending entry whose image vanished after a successful upload.
        try FileManager.default.removeItem(at: metadataURL(scan))
        try? FileManager.default.removeItem(at: imageURL(scan))
    }
    private func metadataURL(_ scan: PendingScan) -> URL { directory.appendingPathComponent("\(scan.id).json") }
    private func imageURL(_ scan: PendingScan) -> URL { directory.appendingPathComponent("\(scan.id).jpg") }
}
