// Convert the existing website mark into the required opaque iOS icon sizes.
import AppKit
import Foundation

let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
let source = root.appendingPathComponent("frontend/public/mtglogger-card-stack.png")
guard let image = NSImage(contentsOf: source) else { fatalError("Website icon is missing") }
let assets = root.appendingPathComponent("ios/MTGLogger/Assets.xcassets")
try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
try Data("{\"info\":{\"author\":\"xcode\",\"version\":1}}".utf8).write(to: assets.appendingPathComponent("Contents.json"))
for (name, dimension, icon) in [("AppIcon.appiconset", 1024, true), ("AppMark.imageset", 512, false)] {
    let directory = assets.appendingPathComponent(name)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: dimension, pixelsHigh: dimension, bitsPerSample: 8, samplesPerPixel: 3, hasAlpha: false, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    NSColor(calibratedRed: 0.08, green: 0.035, blue: 0.045, alpha: 1).setFill()
    NSRect(x: 0, y: 0, width: CGFloat(dimension), height: CGFloat(dimension)).fill()
    image.draw(in: NSRect(x: Double(dimension) * 0.09, y: Double(dimension) * 0.09, width: Double(dimension) * 0.82, height: Double(dimension) * 0.82))
    NSGraphicsContext.restoreGraphicsState()
    guard let png = bitmap.representation(using: .png, properties: [:]) else { fatalError("Unable to render icon") }
    try png.write(to: directory.appendingPathComponent("mark.png"))
    let item: [String: String] = icon ? ["filename":"mark.png", "idiom":"universal", "platform":"ios", "size":"1024x1024"] : ["filename":"mark.png", "idiom":"universal"]
    let contents: [String: Any] = ["images": [item], "info": ["author":"xcode", "version":1]]
    try JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys]).write(to: directory.appendingPathComponent("Contents.json"))
}
