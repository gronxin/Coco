import AppKit
import CoreGraphics
import Foundation

private let serviceURL = URL(string: "http://127.0.0.1:8765/api/quota")!
private let queueURL = URL(string: "http://127.0.0.1:8765/api/night-queue")!
private let overviewURL = URL(string: "http://127.0.0.1:8765/?view=overview")!
private let queuePageURL = URL(string: "http://127.0.0.1:8765/?view=night-queue")!
private let codexBundleID = "com.openai.codex"
private let positionDefaults = UserDefaults(suiteName: "local.codex-token-analyzer.overlay")!
private let savedXKey = "windowOriginX"
private let savedYKey = "windowOriginY"
private let hiddenKey = "hiddenByUser"

private struct QuotaWindow {
    let remaining: Double?
    let reset: Date?
}

private final class QuotaView: NSView {
    var fiveHours = QuotaWindow(remaining: nil, reset: nil) { didSet { needsDisplay = true } }
    var week = QuotaWindow(remaining: nil, reset: nil) { didSet { needsDisplay = true } }
    var updated: Date? { didSet { needsDisplay = true } }
    var stale = true { didSet { needsDisplay = true } }
    var queueTitles: [String] = [] { didSet { needsDisplay = true } }
    var queueUnavailable = false { didSet { needsDisplay = true } }
    var onDragEnded: ((NSPoint) -> Void)?
    var onHide: (() -> Void)?
    private var dragged = false

    override var isOpaque: Bool { false }

    private func label(_ string: String, x: CGFloat, y: CGFloat, width: CGFloat,
                       size: CGFloat, color: NSColor, bold: Bool = false,
                       alignment: NSTextAlignment = .center) {
        let style = NSMutableParagraphStyle()
        style.alignment = alignment
        style.lineBreakMode = .byTruncatingTail
        let font = bold ? NSFont.systemFont(ofSize: size, weight: .semibold) : NSFont.systemFont(ofSize: size)
        (string as NSString).draw(in: NSRect(x: x, y: y, width: width, height: size + 6),
                                  withAttributes: [.font: font, .foregroundColor: color,
                                                   .paragraphStyle: style])
    }

    private func resetLabel(_ date: Date?, weekly: Bool) -> String {
        guard let date, date > Date() else { return "重置时间待更新" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "M月d日 HH:mm"
        return "\(weekly ? "下周" : "下次")重置 \(formatter.string(from: date))"
    }

    private func ring(_ quota: QuotaWindow, centerX: CGFloat, title: String,
                      weekly: Bool, color: NSColor) {
        let center = NSPoint(x: centerX, y: 211)
        let radius: CGFloat = 27
        let background = NSBezierPath()
        background.appendArc(withCenter: center, radius: radius, startAngle: 0, endAngle: 360)
        background.lineWidth = 5
        NSColor(calibratedWhite: 1, alpha: 0.14).setStroke()
        background.stroke()
        if let remaining = quota.remaining {
            let arc = NSBezierPath()
            arc.appendArc(withCenter: center, radius: radius, startAngle: 90,
                          endAngle: 90 - CGFloat(remaining) * 3.6, clockwise: true)
            arc.lineWidth = 5
            arc.lineCapStyle = .round
            (stale ? color.withAlphaComponent(0.45) : color).setStroke()
            arc.stroke()
        }
        let value = quota.remaining.map { "\(Int($0.rounded()))%" } ?? "—"
        label(value, x: centerX - 44, y: 201, width: 88, size: 17, color: .white, bold: true)
        label(title, x: centerX - 50, y: 170, width: 100, size: 11,
              color: NSColor(calibratedWhite: 0.83, alpha: 1))
        label(resetLabel(quota.reset, weekly: weekly), x: centerX - 62, y: 151,
              width: 124, size: 9, color: NSColor(calibratedWhite: 0.72, alpha: 1))
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedRed: 0.07, green: 0.10, blue: 0.16, alpha: 0.94).setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 16, yRadius: 16).fill()
        NSColor(calibratedWhite: 1, alpha: 0.19).setStroke()
        let border = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 16, yRadius: 16)
        border.lineWidth = 1
        border.stroke()
        ring(fiveHours, centerX: 68, title: "5 小时", weekly: false, color: .systemCyan)
        ring(week, centerX: 192, title: "本周", weekly: true, color: .systemGreen)
        let divider = NSBezierPath()
        divider.move(to: NSPoint(x: 14, y: 143))
        divider.line(to: NSPoint(x: bounds.width - 14, y: 143))
        divider.lineWidth = 1
        NSColor(calibratedWhite: 1, alpha: 0.13).setStroke()
        divider.stroke()
        label("任务队列 · 前五条", x: 17, y: 121, width: bounds.width - 34,
              size: 10, color: NSColor(calibratedWhite: 0.82, alpha: 1),
              bold: true, alignment: .left)
        if queueTitles.isEmpty {
            label(queueUnavailable ? "队列暂不可用" : "暂无待执行任务",
                  x: 17, y: 96, width: bounds.width - 34,
                  size: 10, color: NSColor(calibratedWhite: 0.58, alpha: 1), alignment: .left)
        } else {
            for (index, title) in queueTitles.prefix(5).enumerated() {
                let y = CGFloat(101 - index * 18)
                label("\(index + 1). \(title)", x: 17, y: y,
                      width: bounds.width - 34, size: 11, color: .white, alignment: .left)
            }
        }
        let timestamp = updated.map {
            let formatter = DateFormatter()
            formatter.dateFormat = "HH:mm"
            return formatter.string(from: $0)
        } ?? "—"
        label(stale ? "旧数据 · \(timestamp)" : "更新于 \(timestamp)",
              x: 0, y: 6, width: bounds.width, size: 9,
              color: stale ? .systemOrange : NSColor(calibratedWhite: 0.65, alpha: 1))
    }

    override var mouseDownCanMoveWindow: Bool { false }

    override func mouseDown(with event: NSEvent) {
        dragged = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window else { return }
        dragged = true
        window.setFrameOrigin(NSPoint(x: window.frame.origin.x + event.deltaX,
                                      y: window.frame.origin.y - event.deltaY))
    }

    override func mouseUp(with event: NSEvent) {
        if dragged {
            if let window { onDragEnded?(window.frame.origin) }
            return
        }
        let point = convert(event.locationInWindow, from: nil)
        for index in 0..<min(queueTitles.count, 5) {
            let y = CGFloat(101 - index * 18)
            if point.y >= y && point.y < y + 17 {
                NSWorkspace.shared.open(queuePageURL)
                return
            }
        }
        NSWorkspace.shared.open(overviewURL)
    }

    override func rightMouseDown(with event: NSEvent) {
        let menu = NSMenu()
        let hide = NSMenuItem(title: "取消展示", action: #selector(hideOverlay), keyEquivalent: "")
        hide.target = self
        menu.addItem(hide)
        NSMenu.popUpContextMenu(menu, with: event, for: self)
    }

    @objc private func hideOverlay() {
        onHide?()
    }
}

private final class RestoreView: NSView {
    var onRestore: (() -> Void)?
    override var isOpaque: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedRed: 0.17, green: 0.19, blue: 0.21, alpha: 0.98).setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 11, yRadius: 11).fill()
        for (center, color) in [(NSPoint(x: 16, y: 25), NSColor.systemCyan),
                                (NSPoint(x: 30, y: 25), NSColor.systemGreen)] {
            let ring = NSBezierPath()
            ring.appendArc(withCenter: center, radius: 5, startAngle: 0, endAngle: 360)
            ring.lineWidth = 2
            color.setStroke()
            ring.stroke()
        }
        let queue = NSBezierPath()
        for y in [CGFloat(10), 14, 18] {
            queue.move(to: NSPoint(x: 14, y: y))
            queue.line(to: NSPoint(x: 32, y: y))
        }
        queue.lineWidth = 1.5
        NSColor(calibratedWhite: 0.87, alpha: 1).setStroke()
        queue.stroke()
    }

    override func mouseUp(with event: NSEvent) {
        onRestore?()
    }
}

private final class Overlay: NSObject, NSApplicationDelegate {
    private var panel: NSPanel!
    private var restorePanel: NSPanel!
    private let content = QuotaView(frame: NSRect(x: 0, y: 0, width: 260, height: 270))
    private let restoreContent = RestoreView(frame: NSRect(x: 0, y: 0, width: 46, height: 42))
    private var hiddenByUser = positionDefaults.bool(forKey: hiddenKey)
    private var loading = false
    private var loadingQueue = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        panel = NSPanel(contentRect: content.bounds,
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.contentView = content
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.isMovableByWindowBackground = true
        panel.hidesOnDeactivate = false
        panel.setAccessibilityTitle("Codex 剩余额度")
        restorePanel = NSPanel(contentRect: restoreContent.bounds,
                               styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        restorePanel.contentView = restoreContent
        restorePanel.isOpaque = false
        restorePanel.backgroundColor = .clear
        restorePanel.hasShadow = true
        restorePanel.level = .floating
        restorePanel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        restorePanel.hidesOnDeactivate = false
        restorePanel.setAccessibilityTitle("显示 Codex 额度与任务队列")
        restoreContent.toolTip = "显示额度与任务队列"
        restoreContent.onRestore = { [weak self] in
            self?.hiddenByUser = false
            positionDefaults.set(false, forKey: hiddenKey)
            self?.updateVisibility()
        }
        content.onHide = { [weak self] in
            self?.hiddenByUser = true
            positionDefaults.set(true, forKey: hiddenKey)
            self?.updateVisibility()
        }
        content.onDragEnded = { point in
            positionDefaults.set(Double(point.x), forKey: savedXKey)
            positionDefaults.set(Double(point.y), forKey: savedYKey)
        }
        restorePosition()
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(frontAppChanged),
            name: NSWorkspace.didActivateApplicationNotification, object: nil)
        Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.updateVisibility()
            self?.loadQuota()
            self?.loadQueue()
        }
        Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            positionDefaults.synchronize()
            let requested = positionDefaults.bool(forKey: hiddenKey)
            if requested != self.hiddenByUser {
                self.hiddenByUser = requested
                self.updateVisibility()
            }
            if self.restorePanel.isVisible { self.positionRestoreButton() }
        }
        updateVisibility()
        loadQuota()
        loadQueue()
    }

    private func restorePosition() {
        guard let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        // The current 2048×1152 display has the panel at X=1772, Y=42
        // in top-left screen coordinates: 16 points from the right, 42 from the top.
        let fallback = NSPoint(x: screen.frame.maxX - panel.frame.width - 16,
                               y: screen.frame.maxY - panel.frame.height - 42)
        let saved = positionDefaults.object(forKey: savedXKey) != nil &&
                    positionDefaults.object(forKey: savedYKey) != nil
        let requested = saved
            ? NSPoint(x: positionDefaults.double(forKey: savedXKey),
                      y: positionDefaults.double(forKey: savedYKey))
            : fallback
        let target = NSScreen.screens.first(where: {
            $0.frame.intersects(NSRect(origin: requested, size: panel.frame.size))
        }) ?? screen
        let visible = target.visibleFrame
        panel.setFrameOrigin(NSPoint(
            x: min(max(requested.x, visible.minX), visible.maxX - panel.frame.width),
            y: min(max(requested.y, visible.minY), visible.maxY - panel.frame.height)))
    }

    @objc private func frontAppChanged(_ notification: Notification) {
        updateVisibility()
    }

    private func updateVisibility() {
        let front = NSWorkspace.shared.frontmostApplication
        let isCodex = front?.bundleIdentifier == codexBundleID ||
                      front?.processIdentifier == ProcessInfo.processInfo.processIdentifier
        if !isCodex {
            if panel.isVisible { panel.orderOut(nil) }
            if restorePanel.isVisible { restorePanel.orderOut(nil) }
        } else {
            positionRestoreButton()
            if !restorePanel.isVisible { restorePanel.orderFrontRegardless() }
            if hiddenByUser {
                if panel.isVisible { panel.orderOut(nil) }
            } else if !panel.isVisible {
                panel.orderFrontRegardless()
            }
        }
    }

    private func positionRestoreButton() {
        let size = restorePanel.frame.size
        let pid = NSWorkspace.shared.runningApplications.first {
            $0.bundleIdentifier == codexBundleID
        }?.processIdentifier
        if let pid,
           let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]],
           let item = windows.first(where: {
               ($0[kCGWindowOwnerPID as String] as? Int32) == pid &&
               ($0[kCGWindowLayer as String] as? Int) == 0
           }),
           let bounds = item[kCGWindowBounds as String] as? [String: CGFloat],
           let x = bounds["X"], let y = bounds["Y"], let height = bounds["Height"] {
            let top = NSScreen.screens.first?.frame.maxY ?? 0
            let offset = min(max(height * 0.62, CGFloat(64)),
                             max(CGFloat(64), height - size.height - 24))
            restorePanel.setFrameOrigin(NSPoint(x: x + 8, y: top - y - offset - size.height))
            return
        }
        if let screen = NSScreen.main ?? NSScreen.screens.first {
            let visible = screen.visibleFrame
            let offset = min(max(visible.height * 0.62, CGFloat(64)),
                             max(CGFloat(64), visible.height - size.height - 24))
            restorePanel.setFrameOrigin(NSPoint(x: screen.visibleFrame.minX + 8,
                                                y: visible.maxY - offset - size.height))
        }
    }

    private func loadQuota() {
        guard !loading else { return }
        if !content.stale,
           let remaining = content.fiveHours.remaining,
           Int(remaining.rounded()) == 0,
           let reset = content.fiveHours.reset,
           reset > Date() {
            return
        }
        loading = true
        var request = URLRequest(url: serviceURL)
        request.timeoutInterval = 8
        URLSession.shared.dataTask(with: request) { [weak self] data, _, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.loading = false
                guard error == nil, let data,
                      let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
                    self.content.stale = true
                    return
                }
                self.apply(payload)
            }
        }.resume()
    }

    private func loadQueue() {
        guard !loadingQueue else { return }
        loadingQueue = true
        var request = URLRequest(url: queueURL)
        request.timeoutInterval = 8
        URLSession.shared.dataTask(with: request) { [weak self] data, _, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.loadingQueue = false
                guard error == nil, let data,
                      let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                      let items = payload["items"] as? [[String: Any]] else {
                    self.content.queueUnavailable = true
                    self.content.queueTitles = []
                    return
                }
                self.content.queueUnavailable = false
                let hidden: Set<String> = ["completed", "failed", "dispatched", "watching"]
                self.content.queueTitles = items
                    .filter { !hidden.contains($0["status"] as? String ?? "") }
                    .sorted { ($0["position"] as? Int ?? 0) < ($1["position"] as? Int ?? 0) }
                    .prefix(5)
                    .map { $0["title"] as? String ?? "未命名任务" }
            }
        }.resume()
    }

    private func apply(_ payload: [String: Any]) {
        let successful = payload["status"] as? String == "ok"
        let snapshot = successful ? payload : (payload["last_success"] as? [String: Any] ?? [:])
        let limits = snapshot["limits"] as? [[String: Any]] ?? []
        let core = limits.first(where: { $0["limit_id"] as? String == "codex" }) ?? limits.first ?? [:]
        let windows = [core["primary"], core["secondary"]].compactMap { $0 as? [String: Any] }
        func select(_ minutes: Int) -> QuotaWindow {
            guard let value = windows.first(where: { ($0["windowDurationMins"] as? Int) == minutes }),
                  let used = value["usedPercent"] as? Double,
                  let resetSeconds = value["resetsAt"] as? Double else {
                return QuotaWindow(remaining: nil, reset: nil)
            }
            let reset = Date(timeIntervalSince1970: resetSeconds)
            return QuotaWindow(remaining: reset > Date() ? max(0, min(100, 100 - used)) : nil,
                               reset: reset)
        }
        content.fiveHours = select(300)
        content.week = select(10080)
        if let timestamp = snapshot["fetched_at"] as? String {
            let parser = ISO8601DateFormatter()
            parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            content.updated = parser.date(from: timestamp)
            if content.updated == nil {
                parser.formatOptions = [.withInternetDateTime]
                content.updated = parser.date(from: timestamp)
            }
        }
        content.stale = !successful || content.updated.map { Date().timeIntervalSince($0) > 120 } ?? true
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        func detail(_ title: String, _ value: QuotaWindow) -> String {
            let amount = value.remaining.map { "\(Int($0.rounded()))%" } ?? "未知"
            let reset = value.reset.map { formatter.string(from: $0) } ?? "未知"
            return "\(title)剩余 \(amount)，重置于 \(reset)"
        }
        content.toolTip = "\(detail("5 小时", content.fiveHours))\n\(detail("本周", content.week))\n点击额度打开用量总览，点击任务打开队列"
    }
}

let app = NSApplication.shared
private let delegate = Overlay()
app.delegate = delegate
app.run()
