import Foundation
import CoreGraphics

/// Automatic mode requires a steady rectangle, then a sustained empty view.
/// A shaking card cannot immediately rearm the shutter as another physical copy.
struct CaptureGate {
    private(set) var latched = false
    private var stableSince: TimeInterval?
    private var emptySince: TimeInterval?
    private var anchor: CGRect?

    mutating func observe(_ bounds: CGRect?, now: TimeInterval) -> Bool {
        guard let bounds else {
            stableSince = nil
            anchor = nil
            if emptySince == nil { emptySince = now }
            if now - (emptySince ?? now) >= 0.9 { latched = false }
            return false
        }
        emptySince = nil
        guard !latched else { return false }
        if let anchor,
           abs(anchor.midX - bounds.midX) < 0.018,
           abs(anchor.midY - bounds.midY) < 0.018,
           abs(anchor.width - bounds.width) < 0.025,
           abs(anchor.height - bounds.height) < 0.025 {
            if now - (stableSince ?? now) >= 0.85 {
                latched = true
                return true
            }
        } else {
            anchor = bounds
            stableSince = now
        }
        return false
    }

    mutating func markCaptured() { latched = true }
    mutating func suspend() {
        stableSince = nil
        emptySince = nil
        anchor = nil
    }
    mutating func reset() { self = CaptureGate() }
}
