import Foundation
import CoreGraphics
import CoreImage
import Vision

enum CameraOptics {
    /// A card filling 70% of the portrait view should remain beyond the lens's
    /// minimum focus distance. The format's field of view describes landscape
    /// width; portrait width uses its short side. Virtual cameras use their
    /// native field of view and an independently derived 1x wide-camera factor.
    static func recommendedZoom(minimumFocusDistanceMM: Double, horizontalFOVDegrees: Double,
                                formatWidth: Double, formatHeight: Double,
                                baseZoom: Double, maximumZoom: Double) -> Double {
        guard minimumFocusDistanceMM > 0, horizontalFOVDegrees > 0, horizontalFOVDegrees < 180,
              formatWidth > 0, formatHeight > 0 else { return baseZoom }
        let shortSideRatio = min(formatWidth, formatHeight) / max(formatWidth, formatHeight)
        let halfAngleTangent = tan(horizontalFOVDegrees * .pi / 360) * shortSideRatio
        let sceneWidthMM = 63.0 / 0.70
        let zoom = 2 * minimumFocusDistanceMM * 1.15 * halfAngleTangent / sceneWidthMM
        // Round upward to half steps, leaving extra working distance for hands
        // and sleeves. Limit digital magnification to preserve useful detail.
        let rounded = ceil(max(baseZoom, zoom) / baseZoom * 2) / 2 * baseZoom
        return min(maximumZoom, max(baseZoom, rounded))
    }
}

/// A conservative preview heuristic, not a guarantee of OCR readability.
/// Measure the card interior, not the sharp table or plastic holder around it.
final class CardImageQuality {
    private let context = CIContext(options: [.cacheIntermediates: false])
    static let width = 512
    static let height = 716
    static let minimumDetail = 18.0

    func detail(in buffer: CVPixelBuffer, card: VNRectangleObservation) -> Double? {
        let image = CIImage(cvPixelBuffer: buffer)
        let size = image.extent.size
        func point(_ p: CGPoint) -> CIVector { CIVector(x: p.x * size.width, y: p.y * size.height) }
        let rectified = image.applyingFilter("CIPerspectiveCorrection", parameters: [
            "inputTopLeft": point(card.topLeft), "inputTopRight": point(card.topRight),
            "inputBottomLeft": point(card.bottomLeft), "inputBottomRight": point(card.bottomRight),
        ])
        guard rectified.extent.width > 0, rectified.extent.height > 0 else { return nil }
        let normalized = rectified.transformed(by: CGAffineTransform(
            translationX: -rectified.extent.minX, y: -rectified.extent.minY
        )).transformed(by: CGAffineTransform(
            scaleX: CGFloat(Self.width) / rectified.extent.width,
            y: CGFloat(Self.height) / rectified.extent.height
        ))
        guard let cgImage = context.createCGImage(normalized, from: CGRect(x: 0, y: 0, width: Self.width, height: Self.height)) else { return nil }
        var gray = [UInt8](repeating: 0, count: Self.width * Self.height)
        let rendered = gray.withUnsafeMutableBytes { bytes -> Bool in
            guard let canvas = CGContext(data: bytes.baseAddress, width: Self.width, height: Self.height,
                                         bitsPerComponent: 8, bytesPerRow: Self.width,
                                         space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            canvas.draw(cgImage, in: CGRect(x: 0, y: 0, width: Self.width, height: Self.height))
            return true
        }
        return rendered ? Self.detail(gray: gray, width: Self.width, height: Self.height) : nil
    }

    static func detail(gray: [UInt8], width: Int, height: Int) -> Double? {
        guard width >= 16, height >= 16, gray.count == width * height else { return nil }
        // Binomial smoothing suppresses sensor/JPEG noise that could otherwise
        // masquerade as detail. Keep edges out of all three measurement bands.
        var smooth = [Double](repeating: 0, count: gray.count)
        for y in 1..<(height - 1) {
            for x in 1..<(width - 1) {
                let i = y * width + x
                smooth[i] = Double(Int(gray[i - width - 1]) + 2 * Int(gray[i - width]) + Int(gray[i - width + 1])
                    + 2 * Int(gray[i - 1]) + 4 * Int(gray[i]) + 2 * Int(gray[i + 1])
                    + Int(gray[i + width - 1]) + 2 * Int(gray[i + width]) + Int(gray[i + width + 1])) / 16
            }
        }
        let bands = [(0.035, 0.14), (0.35, 0.65), (0.82, 0.95)]
        var scores: [Double] = []
        for (low, high) in bands {
            var sum = 0.0, squared = 0.0, count = 0.0
            for y in max(2, Int(Double(height) * low))..<min(height - 2, Int(Double(height) * high)) {
                for x in Int(Double(width) * 0.10)..<Int(Double(width) * 0.90) {
                    let i = y * width + x
                    let laplacian = 4 * smooth[i] - smooth[i - 1] - smooth[i + 1] - smooth[i - width] - smooth[i + width]
                    sum += laplacian
                    squared += laplacian * laplacian
                    count += 1
                }
            }
            guard count > 0 else { return nil }
            scores.append(max(0, squared / count - pow(sum / count, 2)))
        }
        // One crisp border or isolated glint must not outweigh a soft card.
        return scores.sorted()[1]
    }
}
