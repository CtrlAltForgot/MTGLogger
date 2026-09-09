import XCTest
@testable import MTGLogger

final class MTGLoggerTests: XCTestCase {
    func testServerAddressAcceptsLANAndHTTPS() {
        XCTAssertEqual(ServerAddress.parse("192.168.1.254:5173")?.absoluteString, "http://192.168.1.254:5173")
        XCTAssertNotNil(ServerAddress.parse("https://cards.example.com"))
        XCTAssertNotNil(ServerAddress.parse("http://mtglogger.local:5173/"))
        for invalid in ["https://user:password@example.com", "http://example.com", "javascript:alert(1)", "http://999.168.1.2", "https://example.com/api", "https://example.com?token=x"] {
            XCTAssertNil(ServerAddress.parse(invalid), invalid)
        }
    }

    func testStationaryCardCapturesOnceAndRequiresSustainedRemoval() {
        var gate = CaptureGate()
        let card = CGRect(x: 0.2, y: 0.1, width: 0.6, height: 0.8)
        XCTAssertFalse(gate.observe(card, now: 0))
        XCTAssertFalse(gate.observe(card, now: 0.5))
        XCTAssertTrue(gate.observe(card, now: 1))
        XCTAssertFalse(gate.observe(card, now: 2))
        XCTAssertFalse(gate.observe(nil, now: 3))
        XCTAssertFalse(gate.observe(card, now: 3.4)) // Brief detection dropout is not removal.
        XCTAssertTrue(gate.latched)
        XCTAssertFalse(gate.observe(nil, now: 4))
        XCTAssertFalse(gate.observe(nil, now: 5))
        XCTAssertFalse(gate.latched)
        XCTAssertFalse(gate.observe(card, now: 5.2))
        XCTAssertTrue(gate.observe(card, now: 6.2)) // A second identical physical card.
    }

    func testSlowDriftDoesNotCountAsSteady() {
        var gate = CaptureGate()
        for i in 0..<15 {
            XCTAssertFalse(gate.observe(CGRect(x: 0.1 + Double(i) * 0.01, y: 0.1, width: 0.6, height: 0.8), now: Double(i) * 0.16))
        }
    }

    func testCameraSuspensionCannotRearmTheSameCardOrAccumulateSteadyTime() {
        var gate = CaptureGate()
        let card = CGRect(x: 0.2, y: 0.1, width: 0.6, height: 0.8)
        XCTAssertFalse(gate.observe(card, now: 0))
        gate.suspend()
        XCTAssertFalse(gate.observe(card, now: 100))
        XCTAssertTrue(gate.observe(card, now: 101))
        XCTAssertFalse(gate.observe(nil, now: 102))
        gate.suspend()
        XCTAssertFalse(gate.observe(nil, now: 200))
        XCTAssertTrue(gate.latched)
        XCTAssertFalse(gate.observe(card, now: 201))
        XCTAssertFalse(gate.observe(card, now: 202))
    }

    func testOutboxSurvivesRestartAndPreservesRequestIdentity() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = try ScanOutbox(directory: directory)
        var defaults = ScanDefaults()
        defaults.foil = true
        defaults.deck_id = "original-deck"
        let scan = PendingScan(server: URL(string: "http://192.168.1.254:5173")!, defaults: defaults)
        let bytes = Data([1, 2, 3, 4])
        try outbox.save(scan, jpeg: bytes)
        let restored = try ScanOutbox(directory: directory)
        let record = try XCTUnwrap(restored.all().first)
        XCTAssertEqual(record.id, scan.id)
        XCTAssertEqual(record.server, scan.server)
        XCTAssertTrue(record.defaults.foil)
        XCTAssertEqual(record.defaults.deck_id, "original-deck")
        XCTAssertEqual(try restored.image(record), bytes)
        try restored.remove(record)
        XCTAssertTrue(try restored.all().isEmpty)
    }
}
