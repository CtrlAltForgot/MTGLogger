import AVFoundation
import SwiftUI
import Vision
import UIKit

final class CardCamera: NSObject, ObservableObject, AVCaptureVideoDataOutputSampleBufferDelegate, AVCapturePhotoCaptureDelegate {
    let session = AVCaptureSession()
    @Published var message = "Allow camera access to start scanning"
    @Published var bounds: CGRect?
    @Published var running = false
    @Published var torch = false
    @Published var error: String?
    var onCapture: ((Data) -> Void)?
    private let queue = DispatchQueue(label: "MTGLogger.camera", qos: .userInitiated)
    private let photos = AVCapturePhotoOutput()
    private var device: AVCaptureDevice?
    private var configured = false
    private var active = false
    private var gate = CaptureGate()
    private var lastFrame: TimeInterval = 0
    private var capturing = false
    private var automatic = true
    private var ready = true

    func start() {
        AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
            guard let self else { return }
            guard granted else {
                DispatchQueue.main.async { self.error = "Camera access is off. Enable it in iPhone Settings → MTGLogger." }
                return
            }
            self.queue.async {
                self.active = true
                do {
                    if !self.configured { try self.configure() }
                    self.gate.reset()
                    if !self.session.isRunning { self.session.startRunning() }
                    DispatchQueue.main.async { self.running = true; self.message = "Fill the guide with one card" }
                } catch { DispatchQueue.main.async { self.error = error.localizedDescription } }
            }
        }
    }

    func stop() {
        queue.async {
            self.active = false
            if self.session.isRunning { self.session.stopRunning() }
            self.setTorchOnQueue(false)
            self.gate.reset()
            DispatchQueue.main.async { self.running = false; self.bounds = nil }
        }
    }

    func setAutomatic(_ value: Bool) { queue.async { self.automatic = value } }
    func setReady(_ value: Bool) { queue.async { self.ready = value } }
    func toggleTorch() { queue.async { self.setTorchOnQueue(!(self.device?.torchMode == .on)) } }

    private func setTorchOnQueue(_ value: Bool) {
        guard let device, device.hasTorch else { return }
        do {
            try device.lockForConfiguration()
            device.torchMode = value ? .on : .off
            device.unlockForConfiguration()
            DispatchQueue.main.async { self.torch = value }
        } catch { DispatchQueue.main.async { self.error = error.localizedDescription } }
    }

    private func configure() throws {
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            throw APIError("No rear camera is available on this device.")
        }
        self.device = device
        let input = try AVCaptureDeviceInput(device: device)
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = .photo
        guard session.canAddInput(input), session.canAddOutput(photos) else {
            throw APIError("Unable to configure the camera.")
        }
        session.addInput(input)
        session.addOutput(photos)
        photos.maxPhotoQualityPrioritization = .quality
        let dimensions = device.activeFormat.supportedMaxPhotoDimensions
        if let largest = dimensions.max(by: { Int($0.width) * Int($0.height) < Int($1.width) * Int($1.height) }) {
            photos.maxPhotoDimensions = largest
        }
        let frames = AVCaptureVideoDataOutput()
        frames.alwaysDiscardsLateVideoFrames = true
        frames.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        frames.setSampleBufferDelegate(self, queue: queue)
        guard session.canAddOutput(frames) else { throw APIError("Unable to start card detection.") }
        session.addOutput(frames)
        for connection in [frames.connection(with: .video), photos.connection(with: .video)].compactMap({ $0 }) {
            if connection.isVideoRotationAngleSupported(90) { connection.videoRotationAngle = 90 }
        }
        try device.lockForConfiguration()
        if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
        if device.isExposureModeSupported(.continuousAutoExposure) { device.exposureMode = .continuousAutoExposure }
        if device.isFocusPointOfInterestSupported { device.focusPointOfInterest = CGPoint(x: 0.5, y: 0.5) }
        device.unlockForConfiguration()
        configured = true
    }

    func capture() { queue.async { self.captureOnQueue() } }

    private func captureOnQueue() {
        guard active, session.isRunning, !capturing, ready else { return }
        capturing = true
        gate.markCaptured()
        let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
        settings.photoQualityPrioritization = .quality
        settings.maxPhotoDimensions = photos.maxPhotoDimensions
        photos.capturePhoto(with: settings, delegate: self)
        DispatchQueue.main.async { self.message = "Capturing…" }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = ProcessInfo.processInfo.systemUptime
        guard active, now - lastFrame >= 0.16, !capturing,
              let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        lastFrame = now
        let request = VNDetectRectanglesRequest()
        request.maximumObservations = 1
        request.minimumConfidence = 0.8
        request.minimumAspectRatio = 0.55
        request.maximumAspectRatio = 0.85
        request.minimumSize = 0.3
        request.quadratureTolerance = 18
        do { try VNImageRequestHandler(cvPixelBuffer: buffer, orientation: .up).perform([request]) }
        catch { return }
        let rectangle = request.results?.first?.boundingBox
        let focused = !(device?.isAdjustingFocus ?? false) && !(device?.isAdjustingExposure ?? false)
        // Observe removal even while the server is busy. Do not consume a
        // steady-card trigger until another capture can actually be accepted.
        let shouldCapture: Bool
        if rectangle == nil || (focused && ready && automatic) {
            shouldCapture = gate.observe(rectangle, now: now)
        } else { shouldCapture = false }
        let text = !ready ? "Identifying… keep this card nearby" : gate.latched
            ? "Remove this card before the next scan"
            : rectangle == nil ? "Keep all four card edges in view"
            : !focused ? "Focusing…" : automatic ? "Hold steady…" : "Ready · tap the shutter"
        DispatchQueue.main.async { self.bounds = rectangle; self.message = text }
        if shouldCapture { captureOnQueue() }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        // Normalize EXIF orientation and bound uploads while retaining footer
        // detail. Still-photo capture avoids the compressed webcam/OBS path.
        queue.async {
            defer { self.capturing = false }
            guard error == nil, let data = photo.fileDataRepresentation(),
                  let image = UIImage(data: data), let jpeg = Self.jpeg(image) else {
                DispatchQueue.main.async { self.error = error?.localizedDescription ?? "Could not capture this card." }
                return
            }
            self.ready = false
            DispatchQueue.main.async { self.onCapture?(jpeg) }
        }
    }

    static func jpeg(_ image: UIImage) -> Data? {
        let ratio = min(1, 2560 / max(image.size.width, image.size.height))
        let size = CGSize(width: image.size.width * ratio, height: image.size.height * ratio)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }.jpegData(compressionQuality: 0.94)
    }
}

final class CameraSurface: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> CameraSurface {
        let view = CameraSurface()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspect
        return view
    }
    func updateUIView(_ uiView: CameraSurface, context: Context) {
        if let connection = uiView.previewLayer.connection, connection.isVideoRotationAngleSupported(90) {
            connection.videoRotationAngle = 90
        }
    }
}
