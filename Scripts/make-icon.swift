import AppKit
import Foundation

let root = URL(fileURLWithPath: CommandLine.arguments[1])
let iconset = root.appendingPathComponent("Build/AppIcon.iconset", isDirectory: true)
let output = root.appendingPathComponent("Build/SonyTimecodeFixer.icns")

try? FileManager.default.removeItem(at: iconset)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

struct IconVariant {
    let filename: String
    let points: CGFloat
    let scale: CGFloat

    var pixels: Int { Int(points * scale) }
}

let variants = [
    IconVariant(filename: "icon_16x16.png", points: 16, scale: 1),
    IconVariant(filename: "icon_16x16@2x.png", points: 16, scale: 2),
    IconVariant(filename: "icon_32x32.png", points: 32, scale: 1),
    IconVariant(filename: "icon_32x32@2x.png", points: 32, scale: 2),
    IconVariant(filename: "icon_128x128.png", points: 128, scale: 1),
    IconVariant(filename: "icon_128x128@2x.png", points: 128, scale: 2),
    IconVariant(filename: "icon_256x256.png", points: 256, scale: 1),
    IconVariant(filename: "icon_256x256@2x.png", points: 256, scale: 2),
    IconVariant(filename: "icon_512x512.png", points: 512, scale: 1),
    IconVariant(filename: "icon_512x512@2x.png", points: 512, scale: 2),
]

func color(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat) -> NSColor {
    NSColor(calibratedRed: red / 255, green: green / 255, blue: blue / 255, alpha: 1)
}

func drawIcon(size: CGFloat) -> NSImage {
    let image = NSImage(size: NSSize(width: size, height: size))
    image.lockFocus()

    let bounds = NSRect(x: 0, y: 0, width: size, height: size)
    let radius = size * 0.21
    let basePath = NSBezierPath(roundedRect: bounds.insetBy(dx: size * 0.035, dy: size * 0.035), xRadius: radius, yRadius: radius)

    let gradient = NSGradient(colors: [
        color(22, 31, 42),
        color(13, 17, 23),
    ])!
    gradient.draw(in: basePath, angle: -35)

    color(239, 181, 45).withAlphaComponent(0.95).setStroke()
    basePath.lineWidth = max(2, size * 0.018)
    basePath.stroke()

    let slate = color(34, 46, 59)
    slate.withAlphaComponent(0.82).setFill()
    NSBezierPath(roundedRect: bounds.insetBy(dx: size * 0.18, dy: size * 0.28), xRadius: size * 0.055, yRadius: size * 0.055).fill()

    let cyan = color(65, 205, 216)
    cyan.setStroke()
    let timeline = NSBezierPath()
    timeline.lineWidth = max(5, size * 0.035)
    timeline.lineCapStyle = .round
    timeline.move(to: NSPoint(x: size * 0.23, y: size * 0.38))
    timeline.line(to: NSPoint(x: size * 0.77, y: size * 0.38))
    timeline.stroke()

    for x in [0.28, 0.43, 0.58, 0.73] {
        let tick = NSBezierPath()
        tick.lineWidth = max(2, size * 0.017)
        tick.lineCapStyle = .round
        tick.move(to: NSPoint(x: size * x, y: size * 0.34))
        tick.line(to: NSPoint(x: size * x, y: size * 0.43))
        tick.stroke()
    }

    color(246, 248, 250).setStroke()
    let document = NSBezierPath()
    document.lineWidth = max(4, size * 0.028)
    document.lineJoinStyle = .round
    document.move(to: NSPoint(x: size * 0.31, y: size * 0.62))
    document.line(to: NSPoint(x: size * 0.31, y: size * 0.80))
    document.line(to: NSPoint(x: size * 0.55, y: size * 0.80))
    document.line(to: NSPoint(x: size * 0.69, y: size * 0.66))
    document.line(to: NSPoint(x: size * 0.69, y: size * 0.54))
    document.line(to: NSPoint(x: size * 0.31, y: size * 0.54))
    document.close()
    document.stroke()

    let fold = NSBezierPath()
    fold.lineWidth = max(3, size * 0.022)
    fold.move(to: NSPoint(x: size * 0.55, y: size * 0.80))
    fold.line(to: NSPoint(x: size * 0.55, y: size * 0.66))
    fold.line(to: NSPoint(x: size * 0.69, y: size * 0.66))
    fold.stroke()

    color(239, 181, 45).setFill()
    let badge = NSBezierPath(ovalIn: NSRect(x: size * 0.61, y: size * 0.16, width: size * 0.23, height: size * 0.23))
    badge.fill()

    color(13, 17, 23).setStroke()
    let hand = NSBezierPath()
    hand.lineWidth = max(3, size * 0.022)
    hand.lineCapStyle = .round
    hand.move(to: NSPoint(x: size * 0.725, y: size * 0.215))
    hand.line(to: NSPoint(x: size * 0.725, y: size * 0.305))
    hand.move(to: NSPoint(x: size * 0.68, y: size * 0.26))
    hand.line(to: NSPoint(x: size * 0.725, y: size * 0.26))
    hand.stroke()

    image.unlockFocus()
    return image
}

for variant in variants {
    let image = drawIcon(size: CGFloat(variant.pixels))
    guard let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else {
        fatalError("Could not create \(variant.filename)")
    }
    try png.write(to: iconset.appendingPathComponent(variant.filename))
}

let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
process.arguments = ["-c", "icns", iconset.path, "-o", output.path]
try process.run()
process.waitUntilExit()

if process.terminationStatus != 0 {
    fatalError("iconutil failed")
}
