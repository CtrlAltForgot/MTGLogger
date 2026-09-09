import AVFoundation
import SwiftUI
import Vision
import UIKit

enum CameraLens: String, CaseIterable, Identifiable {
    case automatic = "Auto"
    case main = "Main"
    case closeUp = "Close-up"
    var id: String { rawValue }
}

final class CardCamera: NSObject, ObservableObject, AVCaptureVideoDataOutputSampleBufferDelegate, AVCapturePhotoCaptureDelegate {
    let session = AVCaptureSession()
    @Published var message = "Allow camera access to start scanning"
    @Published var bounds: CGRect?
    @Published var running = false
    @Published var torch = false
    @Published var error: String?
    @Published var lenses: [CameraLens] = [.automatic]
    @Published var lens: CameraLens = .automatic
    @Published var zoom = 1.0
    @Published var maximumZoom = 3.0
    @Published var cameraHint = "Tap the card to focus. Step back if the text looks soft."
    var onCapture: ((Data) -> Void)?
    private let queue = DispatchQueue(label: "MTGLogger.camera", qos: .userInitiated)
    private let photos = AVCapturePhotoOutput()
    private let quality = CardImageQuality()
    private var device: AVCaptureDevice?
    private var input: AVCaptureDeviceInput?
    private var availableDevices: [CameraLens: AVCaptureDevice] = [:]
    private var selectedLens: CameraLens = .automatic
    private var baseZoom: CGFloat = 1
    private var defaultZoom: CGFloat = 1
    private var maximumDeviceZoom: CGFloat = 3
    private var focusSettlesAfter: TimeInterval = 0
    private var configured = false
    private var active = false
    private var activation = 0
    private var gate = CaptureGate()
    private var lastFrame: TimeInterval = 0
    private var capturing = false
    private var automatic = true
    private var ready = true

    func start() {
        queue.async { [weak self] in
            guard let self else { return }
            guard !self.active else { return }
            self.active = true
            self.activation += 1
            let requestedActivation = self.activation
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                guard let self else { return }
                self.queue.async {
                    // A permission answer can arrive after leaving Scan or
                    // backgrounding the app. A stopped request cannot restart it.
                    guard self.active, self.activation == requestedActivation else { return }
                    guard granted else {
                        self.active = false
                        DispatchQueue.main.async { self.error = "Camera access is off. Enable it in iPhone Settings → MTGLogger." }
                        return
                    }
                    do {
                        if !self.configured { try self.configure() }
                        self.gate.suspend()
                        if !self.session.isRunning { self.session.startRunning() }
                        DispatchQueue.main.async {
                            self.error = nil
                            self.running = true
                            self.message = "Fill the guide with one card"
                        }
                    } catch {
                        self.active = false
                        DispatchQueue.main.async { self.error = error.localizedDescription }
                    }
                }
            }
        }
    }

    func stop() {
        queue.async {
            self.active = false
            self.activation += 1
            if self.session.isRunning { self.session.stopRunning() }
            self.setTorchOnQueue(false)
            self.gate.suspend()
            DispatchQueue.main.async { self.running = false; self.bounds = nil }
        }
    }

    func setAutomatic(_ value: Bool) { queue.async { self.automatic = value; self.gate.suspend() } }
    func setReady(_ value: Bool) { queue.async { self.ready = value; self.gate.suspend() } }
    func toggleTorch() { queue.async { self.setTorchOnQueue(!(self.device?.torchMode == .on)) } }

    func focus(at point: CGPoint) {
        queue.async {
            guard self.active, !self.capturing, let device = self.device else { return }
            do {
                try device.lockForConfiguration()
                defer { device.unlockForConfiguration() }
                if device.isFocusPointOfInterestSupported { device.focusPointOfInterest = point }
                if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
                if device.isExposurePointOfInterestSupported { device.exposurePointOfInterest = point }
                if device.isExposureModeSupported(.continuousAutoExposure) { device.exposureMode = .continuousAutoExposure }
                self.interruptSteadyCapture()
                DispatchQueue.main.async { self.message = "Focusing where you tapped…" }
            } catch { DispatchQueue.main.async { self.error = error.localizedDescription } }
        }
    }

    func setZoom(_ value: Double) {
        queue.async { self.setZoomOnQueue(CGFloat(value) * self.baseZoom) }
    }

    func resetZoom() { queue.async { self.setZoomOnQueue(self.defaultZoom) } }

    private func setZoomOnQueue(_ value: CGFloat) {
        guard !capturing, let device else { return }
        do {
            try device.lockForConfiguration()
            defer { device.unlockForConfiguration() }
            device.videoZoomFactor = min(maximumDeviceZoom, max(baseZoom, value))
            interruptSteadyCapture()
            let displayZoom = Double(device.videoZoomFactor / baseZoom)
            DispatchQueue.main.async { self.zoom = displayZoom }
        } catch { DispatchQueue.main.async { self.error = error.localizedDescription } }
    }

    private func interruptSteadyCapture() {
        gate.suspend()
        focusSettlesAfter = ProcessInfo.processInfo.systemUptime + 0.6
    }

    func selectLens(_ lens: CameraLens) {
        queue.async {
            guard !self.capturing, self.selectedLens != lens,
                  let device = self.availableDevices[lens], let oldInput = self.input else { return }
            do {
                let newInput = try AVCaptureDeviceInput(device: device)
                self.setTorchOnQueue(false)
                self.session.beginConfiguration()
                self.session.removeInput(oldInput)
                guard self.session.canAddInput(newInput) else {
                    self.session.addInput(oldInput)
                    self.session.commitConfiguration()
                    throw APIError("This camera is unavailable. Try Auto or Main.")
                }
                self.session.addInput(newInput)
                self.session.commitConfiguration()
                self.device = device
                self.input = newInput
                self.selectedLens = lens
                try self.configureOptics(device)
                DispatchQueue.main.async { self.lens = lens; self.error = nil }
            } catch { DispatchQueue.main.async { self.error = error.localizedDescription } }
        }
    }

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
        guard let wide = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            throw APIError("No rear camera is available on this device.")
        }
        availableDevices = [.automatic: wide, .main: wide]
        // Fixed-focus ultrawide cameras are not macro cameras. Only offer one
        // when its hardware actually supports autofocus.
        if let close = AVCaptureDevice.default(.builtInUltraWideCamera, for: .video, position: .back),
           close.isFocusModeSupported(.continuousAutoFocus) {
            availableDevices[.closeUp] = close
            for type in [AVCaptureDevice.DeviceType.builtInTripleCamera, .builtInDualWideCamera] {
                if let virtual = AVCaptureDevice.default(type, for: .video, position: .back),
                   virtual.constituentDevices.contains(where: { $0.uniqueID == close.uniqueID }) {
                    availableDevices[.automatic] = virtual
                    break
                }
            }
        }
        let device = availableDevices[.automatic] ?? wide
        let input = try AVCaptureDeviceInput(device: device)
        session.beginConfiguration()
        session.sessionPreset = .photo
        guard session.canAddInput(input), session.canAddOutput(photos) else {
            session.commitConfiguration()
            throw APIError("Unable to configure the camera.")
        }
        session.addInput(input)
        session.addOutput(photos)
        photos.maxPhotoQualityPrioritization = .quality
        let frames = AVCaptureVideoDataOutput()
        frames.alwaysDiscardsLateVideoFrames = true
        frames.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        frames.setSampleBufferDelegate(self, queue: queue)
        guard session.canAddOutput(frames) else {
            session.removeInput(input)
            session.removeOutput(photos)
            session.commitConfiguration()
            throw APIError("Unable to start card detection.")
        }
        session.addOutput(frames)
        for connection in [frames.connection(with: .video), photos.connection(with: .video)].compactMap({ $0 }) {
            if connection.isVideoRotationAngleSupported(90) { connection.videoRotationAngle = 90 }
        }
        session.commitConfiguration()
        self.device = device
        self.input = input
        configured = true
        try configureOptics(device)
        let modes: [CameraLens] = availableDevices[.closeUp] == nil ? [.automatic] : CameraLens.allCases
        DispatchQueue.main.async { self.lenses = modes }
    }

    private func configureOptics(_ device: AVCaptureDevice) throws {
        // Configure photo dimensions after the input/preset has chosen its
        // active format, including after switching to a different physical lens.
        if let largest = device.activeFormat.supportedMaxPhotoDimensions.max(by: {
            Int($0.width) * Int($0.height) < Int($1.width) * Int($1.height)
        }) { photos.maxPhotoDimensions = largest }
        for output in session.outputs {
            if let connection = output.connection(with: .video), connection.isVideoRotationAngleSupported(90) {
                connection.videoRotationAngle = 90
            }
        }
        try device.lockForConfiguration()
        defer { device.unlockForConfiguration() }
        if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
        if device.isExposureModeSupported(.continuousAutoExposure) { device.exposureMode = .continuousAutoExposure }
        if device.isFocusPointOfInterestSupported { device.focusPointOfInterest = CGPoint(x: 0.5, y: 0.5) }
        if device.isExposurePointOfInterestSupported { device.exposurePointOfInterest = CGPoint(x: 0.5, y: 0.5) }
        if device.isAutoFocusRangeRestrictionSupported { device.autoFocusRangeRestriction = .none }
        if device.activePrimaryConstituentDeviceSwitchingBehavior != .unsupported {
            device.setPrimaryConstituentDeviceSwitchingBehavior(.auto, restrictedSwitchingBehaviorConditions: [])
        }
        baseZoom = max(1, device.minAvailableVideoZoomFactor)
        if let wideIndex = device.constituentDevices.firstIndex(where: { $0.deviceType == .builtInWideAngleCamera }),
           wideIndex > 0, wideIndex <= device.virtualDeviceSwitchOverVideoZoomFactors.count {
            baseZoom = CGFloat(device.virtualDeviceSwitchOverVideoZoomFactors[wideIndex - 1].doubleValue)
        }
        maximumDeviceZoom = min(device.maxAvailableVideoZoomFactor, baseZoom * 3)
        baseZoom = min(baseZoom, maximumDeviceZoom)
        let dimensions = CMVideoFormatDescriptionGetDimensions(device.activeFormat.formatDescription)
        defaultZoom = CGFloat(CameraOptics.recommendedZoom(
            minimumFocusDistanceMM: Double(device.minimumFocusDistance),
            horizontalFOVDegrees: Double(device.activeFormat.videoFieldOfView),
            formatWidth: Double(dimensions.width), formatHeight: Double(dimensions.height),
            baseZoom: Double(baseZoom), maximumZoom: Double(maximumDeviceZoom)
        ))
        device.videoZoomFactor = defaultZoom
        interruptSteadyCapture()
        let displayZoom = Double(defaultZoom / baseZoom)
        let displayMaximum = Double(maximumDeviceZoom / baseZoom)
        let hint = selectedLens == .closeUp ? "Close-up lens · tap the card to focus. Use soft, even light."
            : availableDevices[.closeUp] != nil && selectedLens == .automatic
            ? "Auto close-up · tap the card to focus. Tilt away from sleeve glare."
            : "Step back and use zoom to fill the guide. Tap the card to focus."
        DispatchQueue.main.async {
            self.zoom = displayZoom
            self.maximumZoom = displayMaximum
            self.cameraHint = hint
        }
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
        guard active, now - lastFrame >= 0.20, !capturing,
              let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        if now - lastFrame > 0.75 { gate.suspend() }
        lastFrame = now
        let request = VNDetectRectanglesRequest()
        request.maximumObservations = 1
        request.minimumConfidence = 0.8
        request.minimumAspectRatio = 0.55
        request.maximumAspectRatio = 0.85
        request.minimumSize = 0.3
        request.quadratureTolerance = 18
        do { try VNImageRequestHandler(cvPixelBuffer: buffer, orientation: .up).perform([request]) }
        catch { gate.suspend(); return }
        let observation = request.results?.first
        let rectangle = observation?.boundingBox
        let focused = now >= focusSettlesAfter && !(device?.isAdjustingFocus ?? false) && !(device?.isAdjustingExposure ?? false)
        var detailed = false
        if let observation, focused, ready, automatic, !gate.latched {
            detailed = (quality.detail(in: buffer, card: observation) ?? 0) >= CardImageQuality.minimumDetail
        }
        // Observe removal even while the server is busy. Do not consume a
        // steady-card trigger until another capture can actually be accepted.
        let shouldCapture = gate.observe(rectangle, now: now, eligible: focused && detailed && ready && automatic)
        let text = !ready ? "Identifying… keep this card nearby" : gate.latched
            ? "Remove this card before the next scan"
            : rectangle == nil ? "Keep all four card edges in view"
            : !focused ? "Focusing…" : !automatic ? "Ready · tap the shutter"
            : !detailed ? "Image looks soft · step back or tap the card"
            : "Hold steady…"
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
        let pixels = CGSize(width: image.size.width * image.scale, height: image.size.height * image.scale)
        let ratio = min(1, 3840 / max(pixels.width, pixels.height))
        let size = CGSize(width: pixels.width * ratio, height: pixels.height * ratio)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let normalized = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        // Large photo-library images can contain much more noise than a camera
        // scan. Do not save an outbox request the server's 15 MB limit rejects.
        for quality in [0.96, 0.90, 0.80, 0.70] {
            if let data = normalized.jpegData(compressionQuality: quality), data.count < 15_000_000 { return data }
        }
        return nil
    }
}

final class CameraSurface: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    var onFocus: ((CGPoint) -> Void)?
    private let focusRing = CAShapeLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        let tap = UITapGestureRecognizer(target: self, action: #selector(focusTapped(_:)))
        addGestureRecognizer(tap)
        focusRing.strokeColor = UIColor.systemYellow.cgColor
        focusRing.fillColor = UIColor.clear.cgColor
        focusRing.lineWidth = 2
        focusRing.opacity = 0
        layer.addSublayer(focusRing)
        isAccessibilityElement = true
        accessibilityLabel = "Camera preview. Tap a card to focus."
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func focusTapped(_ gesture: UITapGestureRecognizer) {
        let location = gesture.location(in: self)
        // resizeAspect can letterbox; taps in the bars are not camera points.
        let picture = previewLayer.layerRectConverted(fromMetadataOutputRect: CGRect(x: 0, y: 0, width: 1, height: 1))
        guard picture.contains(location) else { return }
        onFocus?(previewLayer.captureDevicePointConverted(fromLayerPoint: location))
        focusRing.path = UIBezierPath(roundedRect: CGRect(x: location.x - 28, y: location.y - 28, width: 56, height: 56), cornerRadius: 8).cgPath
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1
        fade.toValue = 0
        fade.duration = 1.2
        focusRing.add(fade, forKey: "focus")
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    var onFocus: (CGPoint) -> Void
    func makeUIView(context: Context) -> CameraSurface {
        let view = CameraSurface()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspect
        view.onFocus = onFocus
        return view
    }
    func updateUIView(_ uiView: CameraSurface, context: Context) {
        uiView.onFocus = onFocus
        if let connection = uiView.previewLayer.connection, connection.isVideoRotationAngleSupported(90) {
            connection.videoRotationAngle = 90
        }
    }
}
