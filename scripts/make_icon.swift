// Renders the Coco app icon (minimal blue "C" gauge) to a macOS .icns.
// Usage: swiftc scripts/make_icon.swift -o /tmp/make_icon && /tmp/make_icon <out_dir>
import AppKit
import Foundation

let S = 512.0

func color(_ r: Int, _ g: Int, _ b: Int, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat(r) / 255, green: CGFloat(g) / 255, blue: CGFloat(b) / 255, alpha: a)
}

func render(size: Int) -> Data {
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx
    let scale = CGFloat(size) / CGFloat(S)
    ctx.cgContext.scaleBy(x: scale, y: scale)

    // Squircle background with a small transparent margin (macOS icon grid).
    let margin = 36.0
    let rect = NSRect(x: margin, y: margin, width: S - 2 * margin, height: S - 2 * margin)
    let squircle = NSBezierPath(roundedRect: rect, xRadius: 99, yRadius: 99)
    let gradient = NSGradient(colors: [color(0x3b, 0x82, 0xf6), color(0x18, 0x59, 0xc2)])!
    gradient.draw(in: squircle, angle: -90)

    // Soft top sheen.
    squircle.addClip()
    color(255, 255, 255, 0.07).setFill()
    NSRect(x: 0, y: S * 0.5, width: S, height: S * 0.5).fill()

    // "C" gauge arc, opening to the right.
    let center = NSPoint(x: S / 2, y: S / 2)
    let radius = 128.0
    let arc = NSBezierPath()
    arc.appendArc(withCenter: center, radius: radius, startAngle: 38.7, endAngle: 321.3, clockwise: false)
    arc.lineWidth = 46
    arc.lineCapStyle = .round
    NSColor.white.setStroke()
    arc.stroke()

    // Center dot.
    let dotR = 27.0
    let dot = NSBezierPath(ovalIn: NSRect(x: center.x - dotR, y: center.y - dotR,
                                          width: dotR * 2, height: dotR * 2))
    NSColor.white.setFill()
    dot.fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "assets"
let fm = FileManager.default
try? fm.createDirectory(atPath: outDir, withIntermediateDirectories: true)

// pixel -> iconset filename
let entries: [(Int, String)] = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]
let iconset = outDir + "/Coco.iconset"
try? fm.removeItem(atPath: iconset)
try! fm.createDirectory(atPath: iconset, withIntermediateDirectories: true)
for (size, name) in entries {
    try! render(size: size).write(to: URL(fileURLWithPath: iconset + "/" + name))
}
// Master 1024 for preview.
try! render(size: 1024).write(to: URL(fileURLWithPath: outDir + "/Coco-1024.png"))
print("wrote \(iconset)")
