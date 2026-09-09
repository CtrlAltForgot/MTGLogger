import XCTest
import UIKit
import Vision
import CoreVideo
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

    func testFocusHuntingAndSoftFramesRestartTheWholeSteadyInterval() {
        var gate = CaptureGate()
        let card = CGRect(x: 0.2, y: 0.1, width: 0.6, height: 0.8)
        XCTAssertFalse(gate.observe(card, now: 0))
        XCTAssertFalse(gate.observe(card, now: 0.7, eligible: false))
        XCTAssertFalse(gate.observe(card, now: 10))
        XCTAssertFalse(gate.observe(card, now: 10.7))
        XCTAssertTrue(gate.observe(card, now: 10.9))
        XCTAssertFalse(gate.observe(card, now: 11, eligible: false))
        XCTAssertTrue(gate.latched) // Refocusing a captured card is not a new copy.
        XCTAssertFalse(gate.observe(nil, now: 12, eligible: false))
        XCTAssertFalse(gate.observe(nil, now: 13, eligible: false))
        XCTAssertFalse(gate.latched) // Removal still works while upload is busy.
    }

    func testSuggestedZoomRespectsWorkingDistanceAndVirtualCameraScale() {
        let main = CameraOptics.recommendedZoom(minimumFocusDistanceMM: 200, horizontalFOVDegrees: 70,
            formatWidth: 4032, formatHeight: 3024, baseZoom: 1, maximumZoom: 3)
        XCTAssertGreaterThanOrEqual(main, 2.5) // A close guide must not force this lens inside 20 cm.
        XCTAssertLessThanOrEqual(main, 3)
        let virtual = CameraOptics.recommendedZoom(minimumFocusDistanceMM: 20, horizontalFOVDegrees: 120,
            formatWidth: 4032, formatHeight: 3024, baseZoom: 2, maximumZoom: 6)
        XCTAssertEqual(virtual, 2) // Native ultrawide factor 2 means the displayed wide-camera 1x.
        XCTAssertEqual(CameraOptics.recommendedZoom(minimumFocusDistanceMM: -1, horizontalFOVDegrees: 70,
            formatWidth: 4032, formatHeight: 3024, baseZoom: 1, maximumZoom: 3), 1)
        XCTAssertEqual(CameraOptics.recommendedZoom(minimumFocusDistanceMM: 500, horizontalFOVDegrees: 70,
            formatWidth: 4032, formatHeight: 3024, baseZoom: 1, maximumZoom: 1.5), 1.5)
    }

    func testDetailCheckRejectsBlurBlankCardsAndSharpSurroundings() throws {
        let width = CardImageQuality.width, height = CardImageQuality.height
        var clear = [UInt8](repeating: 180, count: width * height)
        // Fine contrasting strokes throughout the card interior, like print.
        for y in 0..<height {
            for x in 0..<width where (x % 17 < 3) && (y % 19 < 12) { clear[y * width + x] = 30 }
        }
        let sharp = try XCTUnwrap(CardImageQuality.detail(gray: clear, width: width, height: height))
        var blurred = clear
        for _ in 0..<32 {
            let previous = blurred
            for y in 1..<(height - 1) {
                for x in 1..<(width - 1) {
                    let i = y * width + x
                    blurred[i] = UInt8((Int(previous[i - 1]) + Int(previous[i + 1])
                        + Int(previous[i - width]) + Int(previous[i + width]) + 4 * Int(previous[i])) / 8)
                }
            }
        }
        let soft = try XCTUnwrap(CardImageQuality.detail(gray: blurred, width: width, height: height))
        XCTAssertGreaterThan(sharp, CardImageQuality.minimumDetail)
        XCTAssertLessThan(soft, sharp / 4)
        XCTAssertLessThan(soft, CardImageQuality.minimumDetail)
        let blank = [UInt8](repeating: 128, count: width * height)
        XCTAssertEqual(CardImageQuality.detail(gray: blank, width: width, height: height), 0)
        var border = blank
        for y in 0..<height {
            for x in 0..<width where x < width / 12 || x > width * 11 / 12 {
                border[y * width + x] = (x + y) % 2 == 0 ? 0 : 255
            }
        }
        XCTAssertEqual(CardImageQuality.detail(gray: border, width: width, height: height), 0)
        XCTAssertNil(CardImageQuality.detail(gray: [1, 2], width: width, height: height))
    }

    func testPhotoEncodingRetainsPixelsAndBoundsLargeUploads() throws {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 2
        let original = UIGraphicsImageRenderer(size: CGSize(width: 1200, height: 1600), format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 1200, height: 1600))
        }
        let encoded = try XCTUnwrap(CardCamera.jpeg(original))
        let decoded = try XCTUnwrap(UIImage(data: encoded)?.cgImage)
        XCTAssertEqual(decoded.width, 2400)
        XCTAssertEqual(decoded.height, 3200) // UIImage's display scale must not discard half the pixels.
        let oversized = UIGraphicsImageRenderer(size: CGSize(width: 1500, height: 2200), format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 1500, height: 2200))
        }
        let bounded = try XCTUnwrap(UIImage(data: XCTUnwrap(CardCamera.jpeg(oversized)))?.cgImage)
        XCTAssertEqual(bounded.height, 3840)
        XCTAssertLessThanOrEqual(bounded.width, 3840)
    }

    func testCameraBufferDetailPipelineMeasuresInsideTheDetectedCard() throws {
        let width = 800, height = 1100
        var optionalBuffer: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
            [kCVPixelBufferCGImageCompatibilityKey: true, kCVPixelBufferCGBitmapContextCompatibilityKey: true] as CFDictionary,
            &optionalBuffer), kCVReturnSuccess)
        let buffer = try XCTUnwrap(optionalBuffer)
        CVPixelBufferLockBaseAddress(buffer, [])
        let canvas = try XCTUnwrap(CGContext(data: CVPixelBufferGetBaseAddress(buffer), width: width, height: height,
            bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue))
        canvas.setFillColor(UIColor.white.cgColor)
        canvas.fill(CGRect(x: 0, y: 0, width: width, height: height))
        canvas.setFillColor(UIColor.black.cgColor)
        // A crisp, distracting surround with a completely blank card interior.
        for x in stride(from: 0, to: width, by: 8) {
            canvas.fill(CGRect(x: x, y: 0, width: 4, height: height))
        }
        canvas.setFillColor(UIColor.lightGray.cgColor)
        canvas.fill(CGRect(x: 160, y: 220, width: 480, height: 660))
        CVPixelBufferUnlockBaseAddress(buffer, [])
        let rectangle = VNRectangleObservation(requestRevision: 1,
            topLeft: CGPoint(x: 0.2, y: 0.8), topRight: CGPoint(x: 0.8, y: 0.8),
            bottomRight: CGPoint(x: 0.8, y: 0.2), bottomLeft: CGPoint(x: 0.2, y: 0.2))
        let quality = CardImageQuality()
        let blankDetail = try XCTUnwrap(quality.detail(in: buffer, card: rectangle))
        XCTAssertLessThan(blankDetail, CardImageQuality.minimumDetail)
        CVPixelBufferLockBaseAddress(buffer, [])
        canvas.setFillColor(UIColor.black.cgColor)
        for y in stride(from: 230, to: 870, by: 20) {
            for x in stride(from: 170, to: 630, by: 17) {
                canvas.fill(CGRect(x: x, y: y, width: 3, height: 12))
            }
        }
        CVPixelBufferUnlockBaseAddress(buffer, [])
        let sharpDetail = try XCTUnwrap(quality.detail(in: buffer, card: rectangle))
        XCTAssertGreaterThan(sharpDetail, CardImageQuality.minimumDetail)
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
