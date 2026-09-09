// Convert the existing website mark into the required opaque iOS icon sizes.
import Foundation
import CoreGraphics
import ImageIO

let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
let source = root.appendingPathComponent("frontend/public/mtglogger-card-stack.png")
guard let sourceImage = CGImageSourceCreateWithURL(source as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(sourceImage, 0, nil) else { fatalError("Website icon is missing") }
let assets = root.appendingPathComponent("ios/MTGLogger/Assets.xcassets")
try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
try Data("{\"info\":{\"author\":\"xcode\",\"version\":1}}".utf8).write(to: assets.appendingPathComponent("Contents.json"))
for (name, dimension, icon) in [("AppIcon.appiconset", 1024, true), ("AppMark.imageset", 512, false)] {
    let directory = assets.appendingPathComponent(name)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let context = CGContext(data: nil, width: dimension, height: dimension, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    context.setFillColor(CGColor(red: 0.08, green: 0.035, blue: 0.045, alpha: 1))
    context.fill(CGRect(x: 0, y: 0, width: CGFloat(dimension), height: CGFloat(dimension)))
    context.interpolationQuality = .high
    context.draw(image, in: CGRect(x: Double(dimension) * 0.09, y: Double(dimension) * 0.09, width: Double(dimension) * 0.82, height: Double(dimension) * 0.82))
    guard let output = context.makeImage(), let destination = CGImageDestinationCreateWithURL(directory.appendingPathComponent("mark.png") as CFURL, "public.png" as CFString, 1, nil) else { fatalError("Unable to render icon") }
    CGImageDestinationAddImage(destination, output, nil)
    guard CGImageDestinationFinalize(destination) else { fatalError("Unable to save icon") }
    let item: [String: String] = icon ? ["filename":"mark.png", "idiom":"universal", "platform":"ios", "size":"1024x1024"] : ["filename":"mark.png", "idiom":"universal"]
    let contents: [String: Any] = ["images": [item], "info": ["author":"xcode", "version":1]]
    try JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys]).write(to: directory.appendingPathComponent("Contents.json"))
}
