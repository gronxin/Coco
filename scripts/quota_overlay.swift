import AppKit
import CoreGraphics
import Foundation

private let serviceURL = URL(string: "http://127.0.0.1:8765/api/quota")!
private let queueURL = URL(string: "http://127.0.0.1:8765/api/night-queue")!
private let overviewURL = URL(string: "http://127.0.0.1:8765/?view=overview")!
private let queuePageURL = URL(string: "http://127.0.0.1:8765/?view=night-queue")!
private let codexBundleID = "com.openai.codex"
private let overlayDefaults = UserDefaults(suiteName: "local.codex-token-analyzer.overlay")!
private let overlayVisibleKey = "overlayVisible"
private let queueCollapsedKey = "queueCollapsed"
private let customPositionKey = "customPosition"
private let panelRelativeXKey = "panelRelativeX"
private let panelRelativeYKey = "panelRelativeY"
private let panelWidth: CGFloat = 260
private let expandedPanelHeight: CGFloat = 270
private let collapsedPanelHeight: CGFloat = 194
private let navigationButtonSize = CGSize(width: 36, height: 36)

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
    var queueCount = 0 { didSet { needsDisplay = true } }
    var queueUnavailable = false { didSet { needsDisplay = true } }
    var queueCollapsed = false { didSet { needsDisplay = true } }
    var onDragStarted: (() -> Void)?
    var onDragEnded: ((NSPoint) -> Void)?
    var onToggleQueue: (() -> Void)?
    private var dragged = false

    override var isOpaque: Bool { false }

    /// Draws one text label using the overlay's compact typography.
    private func label(_ string: String, x: CGFloat, y: CGFloat, width: CGFloat,
                       size: CGFloat, color: NSColor, bold: Bool = false,
                       alignment: NSTextAlignment = .center) {
        let style = NSMutableParagraphStyle()
        style.alignment = alignment
        style.lineBreakMode = .byTruncatingTail
        let font = bold
            ? NSFont.systemFont(ofSize: size, weight: .semibold)
            : NSFont.systemFont(ofSize: size)
        (string as NSString).draw(
            in: NSRect(x: x, y: y, width: width, height: size + 6),
            withAttributes: [
                .font: font,
                .foregroundColor: color,
                .paragraphStyle: style,
            ]
        )
    }

    /// Formats a quota reset date for the compact ring caption.
    private func resetLabel(_ date: Date?, weekly: Bool) -> String {
        guard let date, date > Date() else { return "重置时间待更新" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "M月d日 HH:mm"
        return "\(weekly ? "下周" : "下次")重置 \(formatter.string(from: date))"
    }

    /// Draws one quota ring relative to the panel's top edge.
    private func ring(_ quota: QuotaWindow, centerX: CGFloat, title: String,
                      weekly: Bool, color: NSColor) {
        let center = NSPoint(x: centerX, y: bounds.maxY - 59)
        let radius: CGFloat = 27
        let background = NSBezierPath()
        background.appendArc(withCenter: center, radius: radius, startAngle: 0, endAngle: 360)
        background.lineWidth = 5
        NSColor(calibratedWhite: 1, alpha: 0.14).setStroke()
        background.stroke()
        if let remaining = quota.remaining {
            let arc = NSBezierPath()
            arc.appendArc(
                withCenter: center,
                radius: radius,
                startAngle: 90,
                endAngle: 90 - CGFloat(remaining) * 3.6,
                clockwise: true
            )
            arc.lineWidth = 5
            arc.lineCapStyle = .round
            (stale ? color.withAlphaComponent(0.45) : color).setStroke()
            arc.stroke()
        }
        let value = quota.remaining.map { "\(Int($0.rounded()))%" } ?? "—"
        label(value, x: centerX - 44, y: bounds.maxY - 69, width: 88,
              size: 17, color: .white, bold: true)
        label(title, x: centerX - 50, y: bounds.maxY - 100, width: 100,
              size: 11, color: NSColor(calibratedWhite: 0.83, alpha: 1))
        label(resetLabel(quota.reset, weekly: weekly), x: centerX - 62,
              y: bounds.maxY - 119, width: 124, size: 9,
              color: NSColor(calibratedWhite: 0.72, alpha: 1))
    }

    /// Returns the clickable queue header or collapsed-count region.
    private func queueToggleRect() -> NSRect {
        if queueCollapsed {
            return NSRect(x: 0, y: 20, width: bounds.width, height: max(0, bounds.maxY - 147))
        }
        return NSRect(x: 0, y: bounds.maxY - 151, width: bounds.width, height: 24)
    }

    /// Returns the clickable bounds for one visible queue task.
    private func taskRect(at index: Int) -> NSRect {
        NSRect(x: 12, y: bounds.maxY - 171 - CGFloat(index * 18),
               width: bounds.width - 24, height: 17)
    }

    /// Draws the quota rings and the expanded or collapsed queue area.
    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedRed: 0.07, green: 0.10, blue: 0.16, alpha: 0.94).setFill()
        NSBezierPath(
            roundedRect: bounds.insetBy(dx: 1, dy: 1),
            xRadius: 16,
            yRadius: 16
        ).fill()
        NSColor(calibratedWhite: 1, alpha: 0.19).setStroke()
        let border = NSBezierPath(
            roundedRect: bounds.insetBy(dx: 1, dy: 1),
            xRadius: 16,
            yRadius: 16
        )
        border.lineWidth = 1
        border.stroke()

        ring(fiveHours, centerX: 68, title: "5 小时", weekly: false, color: .systemCyan)
        ring(week, centerX: 192, title: "本周", weekly: true, color: .systemGreen)

        let dividerY = bounds.maxY - 127
        let divider = NSBezierPath()
        divider.move(to: NSPoint(x: 14, y: dividerY))
        divider.line(to: NSPoint(x: bounds.width - 14, y: dividerY))
        divider.lineWidth = 1
        NSColor(calibratedWhite: 1, alpha: 0.13).setStroke()
        divider.stroke()

        let queueColor = queueUnavailable
            ? NSColor.systemOrange
            : NSColor(calibratedWhite: 0.82, alpha: 1)
        if queueCollapsed {
            label("\(queueCount) 个任务", x: 0, y: 34, width: bounds.width,
                  size: 12, color: queueColor, bold: true)
        } else {
            label("任务队列 · \(queueCount) 个", x: 17, y: bounds.maxY - 149,
                  width: bounds.width - 34, size: 10, color: queueColor,
                  bold: true, alignment: .left)
            if queueTitles.isEmpty {
                label(queueUnavailable ? "队列暂不可用" : "暂无待执行任务",
                      x: 17, y: bounds.maxY - 174, width: bounds.width - 34,
                      size: 10, color: NSColor(calibratedWhite: 0.58, alpha: 1),
                      alignment: .left)
            } else {
                for (index, title) in queueTitles.prefix(5).enumerated() {
                    label("\(index + 1). \(title)", x: 17,
                          y: bounds.maxY - 169 - CGFloat(index * 18),
                          width: bounds.width - 34, size: 11, color: .white,
                          alignment: .left)
                }
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

    /// Resets drag detection when a new pointer interaction starts.
    override func mouseDown(with event: NSEvent) {
        dragged = false
    }

    /// Moves the panel directly while pausing automatic overlay placement.
    override func mouseDragged(with event: NSEvent) {
        guard let window else { return }
        if !dragged {
            dragged = true
            onDragStarted?()
        }
        window.setFrameOrigin(
            NSPoint(
                x: window.frame.origin.x + event.deltaX,
                y: window.frame.origin.y - event.deltaY
            )
        )
    }

    /// Completes a drag or routes a click to queue toggle, task, or overview actions.
    override func mouseUp(with event: NSEvent) {
        if dragged {
            if let window {
                onDragEnded?(window.frame.origin)
            }
            return
        }
        let point = convert(event.locationInWindow, from: nil)
        if queueToggleRect().contains(point) {
            onToggleQueue?()
            return
        }
        if !queueCollapsed {
            for index in 0..<min(queueTitles.count, 5) where taskRect(at: index).contains(point) {
                NSWorkspace.shared.open(queuePageURL)
                return
            }
        }
        NSWorkspace.shared.open(overviewURL)
    }

    /// Allows the non-activating panel to receive the first click.
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }
}

private final class NavigationButtonView: NSView {
    var onToggle: (() -> Void)?
    var overlayVisible = false { didSet { needsDisplay = true } }

    override var isOpaque: Bool { false }

    /// Draws a compact quota-and-queue glyph aligned with the Codex navigation rail.
    override func draw(_ dirtyRect: NSRect) {
        let background = overlayVisible
            ? NSColor(calibratedWhite: 1, alpha: 0.14)
            : NSColor(calibratedWhite: 0.14, alpha: 0.96)
        background.setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 8, yRadius: 8).fill()
        if overlayVisible {
            NSColor(calibratedWhite: 1, alpha: 0.22).setStroke()
            let border = NSBezierPath(
                roundedRect: bounds.insetBy(dx: 1, dy: 1),
                xRadius: 8,
                yRadius: 8
            )
            border.lineWidth = 1
            border.stroke()
        }
        for (center, color) in [
            (NSPoint(x: 12, y: 23), NSColor.systemCyan),
            (NSPoint(x: 24, y: 23), NSColor.systemGreen),
        ] {
            let ring = NSBezierPath()
            ring.appendArc(withCenter: center, radius: 4, startAngle: 0, endAngle: 360)
            ring.lineWidth = 1.6
            color.setStroke()
            ring.stroke()
        }
        let queue = NSBezierPath()
        for y in [CGFloat(9), 13, 17] {
            queue.move(to: NSPoint(x: 11, y: y))
            queue.line(to: NSPoint(x: 25, y: y))
        }
        queue.lineWidth = 1.4
        queue.lineCapStyle = .round
        NSColor(calibratedWhite: 0.87, alpha: 1).setStroke()
        queue.stroke()
    }

    /// Toggles the overlay without activating this accessory application.
    override func mouseUp(with event: NSEvent) {
        onToggle?()
    }

    /// Routes accessibility press actions through the same toggle callback as mouse clicks.
    override func accessibilityPerformPress() -> Bool {
        onToggle?()
        return true
    }

    /// Allows the non-activating panel to receive the first click.
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }
}

private final class Overlay: NSObject, NSApplicationDelegate {
    private var panel: NSPanel!
    private var navigationPanel: NSPanel!
    private let content: QuotaView
    private let navigationContent = NavigationButtonView(
        frame: NSRect(origin: .zero, size: navigationButtonSize)
    )
    private var userWantsOverlayVisible = overlayDefaults.bool(forKey: overlayVisibleKey)
    private var queueCollapsed = overlayDefaults.bool(forKey: queueCollapsedKey)
    private var customPosition = overlayDefaults.bool(forKey: customPositionKey)
    private var relativeOrigin: CGPoint?
    private var trackingTimer: Timer?
    private var dataTimer: Timer?
    private var currentTargetWindow: CGRect?
    private var isDraggingOverlay = false
    private var loading = false
    private var loadingQueue = false
    private var quotaTooltip = "额度待更新"

    override init() {
        let height = queueCollapsed ? collapsedPanelHeight : expandedPanelHeight
        content = QuotaView(frame: NSRect(x: 0, y: 0, width: panelWidth, height: height))
        if overlayDefaults.object(forKey: panelRelativeXKey) != nil,
           overlayDefaults.object(forKey: panelRelativeYKey) != nil {
            relativeOrigin = CGPoint(
                x: overlayDefaults.double(forKey: panelRelativeXKey),
                y: overlayDefaults.double(forKey: panelRelativeYKey)
            )
        }
        super.init()
    }

    /// Creates both non-activating panels, binds interactions, and starts data loading.
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        content.queueCollapsed = queueCollapsed
        panel = makePanel(contentView: content, shadow: true)
        panel.setAccessibilityTitle("Codex 剩余额度")

        navigationPanel = makePanel(contentView: navigationContent, shadow: false)
        navigationPanel.setAccessibilityTitle("显示 Codex 额度与任务队列")
        navigationContent.setAccessibilityElement(true)
        navigationContent.setAccessibilityRole(.button)
        navigationContent.overlayVisible = userWantsOverlayVisible
        updateNavigationAccessibility()

        navigationContent.onToggle = { [weak self] in
            self?.toggleOverlayVisibility()
        }
        content.onToggleQueue = { [weak self] in
            self?.toggleQueueCollapsed()
        }
        content.onDragStarted = { [weak self] in
            self?.isDraggingOverlay = true
        }
        content.onDragEnded = { [weak self] origin in
            self?.finishDragging(at: origin)
        }

        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(frontApplicationChanged),
            name: NSWorkspace.didActivateApplicationNotification,
            object: nil
        )
        dataTimer = Timer(timeInterval: 10, repeats: true) { [weak self] _ in
            self?.loadQuota()
            self?.loadQueue()
        }
        if let dataTimer {
            RunLoop.main.add(dataTimer, forMode: .common)
        }
        updateForegroundTracking()
        loadQuota()
        loadQueue()
    }

    /// Stops timers and removes workspace observers during application shutdown.
    func applicationWillTerminate(_ notification: Notification) {
        stopTracking()
        dataTimer?.invalidate()
        NSWorkspace.shared.notificationCenter.removeObserver(self)
    }

    /// Creates one borderless panel with the shared overlay behavior.
    private func makePanel(contentView: NSView, shadow: Bool) -> NSPanel {
        let created = NSPanel(
            contentRect: contentView.bounds,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        created.contentView = contentView
        created.isOpaque = false
        created.backgroundColor = .clear
        created.hasShadow = shadow
        created.level = .floating
        created.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        created.hidesOnDeactivate = false
        created.becomesKeyOnlyIfNeeded = true
        return created
    }

    /// Handles frontmost application changes by starting or stopping window tracking.
    @objc private func frontApplicationChanged(_ notification: Notification) {
        updateForegroundTracking()
    }

    /// Starts tracking only while Codex is the frontmost application.
    private func updateForegroundTracking() {
        if isCodexFrontmost() {
            startTracking()
            refreshPlacementAndVisibility()
        } else {
            stopTracking()
            currentTargetWindow = nil
            hidePanels()
        }
    }

    /// Returns whether the real Codex desktop application currently owns the foreground.
    private func isCodexFrontmost() -> Bool {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier == codexBundleID
    }

    /// Starts the 0.1-second placement timer if it is not already running.
    private func startTracking() {
        guard trackingTimer == nil else { return }
        let timer = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            self?.refreshPlacementAndVisibility()
        }
        RunLoop.main.add(timer, forMode: .common)
        trackingTimer = timer
    }

    /// Stops high-frequency tracking while Codex is not in the foreground.
    private func stopTracking() {
        trackingTimer?.invalidate()
        trackingTimer = nil
    }

    /// Hides both panels without changing the user's persisted visibility intent.
    private func hidePanels() {
        if panel?.isVisible == true { panel.orderOut(nil) }
        if navigationPanel?.isVisible == true { navigationPanel.orderOut(nil) }
    }

    /// Applies the complete foreground, target-window, and user-intent visibility matrix.
    private func refreshPlacementAndVisibility() {
        guard isCodexFrontmost() else {
            updateForegroundTracking()
            return
        }
        guard let target = targetCodexWindow(),
              let screen = targetScreen(for: target) else {
            currentTargetWindow = nil
            hidePanels()
            return
        }
        currentTargetWindow = target
        positionNavigationButton(for: target, on: screen)
        if navigationPanel.isVisible == false {
            navigationPanel.orderFrontRegardless()
        }
        if userWantsOverlayVisible {
            if !isDraggingOverlay {
                positionOverlay(for: target, on: screen)
            }
            if panel.isVisible == false {
                panel.orderFrontRegardless()
            }
        } else if panel.isVisible {
            panel.orderOut(nil)
        }
    }

    /// Returns the frontmost valid, on-screen layer-zero window owned by Codex.
    private func targetCodexWindow() -> CGRect? {
        guard let app = NSWorkspace.shared.frontmostApplication,
              app.bundleIdentifier == codexBundleID,
              let referenceMaxY = NSScreen.screens.first?.frame.maxY,
              let windows = CGWindowListCopyWindowInfo(
                  [.optionOnScreenOnly, .excludeDesktopElements],
                  kCGNullWindowID
              ) as? [[String: Any]] else {
            return nil
        }
        for item in windows {
            guard let owner = item[kCGWindowOwnerPID as String] as? NSNumber,
                  owner.int32Value == app.processIdentifier,
                  let layer = item[kCGWindowLayer as String] as? NSNumber,
                  layer.intValue == 0,
                  let bounds = item[kCGWindowBounds as String] as? [String: Any],
                  let x = bounds["X"] as? NSNumber,
                  let y = bounds["Y"] as? NSNumber,
                  let width = bounds["Width"] as? NSNumber,
                  let height = bounds["Height"] as? NSNumber else {
                continue
            }
            let cgFrame = CGRect(
                x: x.doubleValue,
                y: y.doubleValue,
                width: width.doubleValue,
                height: height.doubleValue
            )
            let frame = appKitWindowFrame(cgFrame: cgFrame, referenceMaxY: referenceMaxY)
            if isValidTargetWindow(frame) {
                return frame
            }
        }
        return nil
    }

    /// Selects the NSScreen that overlaps the target window the most.
    private func targetScreen(for window: CGRect) -> NSScreen? {
        let screens = NSScreen.screens
        guard let selected = targetScreenFrame(
            window: window,
            screens: screens.map(\.frame),
            mainScreen: screens.first?.frame
        ) else {
            return nil
        }
        return screens.first { $0.frame == selected }
    }

    /// Places the 36-point control inside the Codex navigation rail and screen bounds.
    private func positionNavigationButton(for target: CGRect, on screen: NSScreen) {
        let requested = navigationOrigin(window: target, buttonSize: navigationButtonSize)
        let origin = clampedOrigin(
            requested,
            size: navigationButtonSize,
            visibleFrame: screen.visibleFrame
        )
        navigationPanel.setFrameOrigin(origin)
    }

    /// Places the overlay at its default anchor or persisted target-relative origin.
    private func positionOverlay(for target: CGRect, on screen: NSScreen) {
        let size = panel.frame.size
        if size.width > screen.visibleFrame.width || size.height > screen.visibleFrame.height {
            resetCustomPosition()
        }
        let requested: CGPoint
        if customPosition, let relativeOrigin {
            requested = customOverlayOrigin(window: target, relativeOrigin: relativeOrigin)
        } else {
            requested = defaultOverlayOrigin(window: target, panelSize: size)
        }
        let origin = clampedOrigin(requested, size: size, visibleFrame: screen.visibleFrame)
        panel.setFrameOrigin(origin)
    }

    /// Toggles and persists whether the user wants the overlay visible.
    private func toggleOverlayVisibility() {
        userWantsOverlayVisible.toggle()
        overlayDefaults.set(userWantsOverlayVisible, forKey: overlayVisibleKey)
        navigationContent.overlayVisible = userWantsOverlayVisible
        updateNavigationAccessibility()
        refreshPlacementAndVisibility()
    }

    /// Updates accessibility text to match the navigation button's current action.
    private func updateNavigationAccessibility() {
        let title = userWantsOverlayVisible
            ? "隐藏 Codex 额度与任务队列"
            : "显示 Codex 额度与任务队列"
        navigationContent.toolTip = title
        navigationContent.setAccessibilityTitle(title)
        navigationPanel?.setAccessibilityTitle(title)
    }

    /// Toggles the queue section while preserving the panel's top edge and custom origin.
    private func toggleQueueCollapsed() {
        queueCollapsed.toggle()
        overlayDefaults.set(queueCollapsed, forKey: queueCollapsedKey)
        content.queueCollapsed = queueCollapsed
        let top = panel.frame.maxY
        let oldHeight = panel.frame.height
        let height = queueCollapsed ? collapsedPanelHeight : expandedPanelHeight
        let nextFrame = NSRect(
            x: panel.frame.minX,
            y: top - height,
            width: panelWidth,
            height: height
        )
        content.setFrameSize(nextFrame.size)
        panel.setFrame(nextFrame, display: true)
        if customPosition, let relativeOrigin {
            persistRelativeOffset(
                resizedRelativeOrigin(relativeOrigin, oldHeight: oldHeight, newHeight: height)
            )
        }
        refreshPlacementAndVisibility()
    }

    /// Persists a completed drag as a target-window-relative custom position.
    private func finishDragging(at origin: CGPoint) {
        defer { isDraggingOverlay = false }
        guard let target = currentTargetWindow,
              let screen = targetScreen(for: target) else {
            return
        }
        let clamped = clampedOrigin(origin, size: panel.frame.size, visibleFrame: screen.visibleFrame)
        panel.setFrameOrigin(clamped)
        customPosition = true
        overlayDefaults.set(true, forKey: customPositionKey)
        persistRelativeOrigin(clamped, target: target)
    }

    /// Saves a panel origin relative to the active Codex target window.
    private func persistRelativeOrigin(_ origin: CGPoint, target: CGRect) {
        let relative = CGPoint(x: origin.x - target.minX, y: origin.y - target.minY)
        persistRelativeOffset(relative)
    }

    /// Saves a precomputed target-relative origin without consulting the displayed clamped frame.
    private func persistRelativeOffset(_ relative: CGPoint) {
        relativeOrigin = relative
        overlayDefaults.set(Double(relative.x), forKey: panelRelativeXKey)
        overlayDefaults.set(Double(relative.y), forKey: panelRelativeYKey)
    }

    /// Returns placement to the default upper-right anchor without deleting legacy keys.
    private func resetCustomPosition() {
        customPosition = false
        relativeOrigin = nil
        overlayDefaults.set(false, forKey: customPositionKey)
        overlayDefaults.removeObject(forKey: panelRelativeXKey)
        overlayDefaults.removeObject(forKey: panelRelativeYKey)
    }

    /// Loads quota data unless a previous quota request is still in progress.
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
                      let payload = (try? JSONSerialization.jsonObject(with: data))
                        as? [String: Any] else {
                    self.content.stale = true
                    self.refreshContentTooltip()
                    return
                }
                self.applyQuota(payload)
            }
        }.resume()
    }

    /// Loads queue data and preserves the last successful count if the API is unavailable.
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
                      let payload = (try? JSONSerialization.jsonObject(with: data))
                        as? [String: Any],
                      let items = payload["items"] as? [[String: Any]] else {
                    self.content.queueUnavailable = true
                    self.refreshContentTooltip()
                    return
                }
                let summary = summarizeQueueItems(items)
                self.content.queueUnavailable = false
                self.content.queueCount = summary.count
                self.content.queueTitles = summary.titles
                self.refreshContentTooltip()
            }
        }.resume()
    }

    /// Applies a quota response and updates the ring values and timestamps.
    private func applyQuota(_ payload: [String: Any]) {
        let successful = payload["status"] as? String == "ok"
        let snapshot = successful ? payload : (payload["last_success"] as? [String: Any] ?? [:])
        let limits = snapshot["limits"] as? [[String: Any]] ?? []
        let core = limits.first(where: { $0["limit_id"] as? String == "codex" })
            ?? limits.first
            ?? [:]
        let windows = [core["primary"], core["secondary"]]
            .compactMap { $0 as? [String: Any] }
        content.fiveHours = quotaWindow(minutes: 300, windows: windows)
        content.week = quotaWindow(minutes: 10080, windows: windows)
        if let timestamp = snapshot["fetched_at"] as? String {
            let parser = ISO8601DateFormatter()
            parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            content.updated = parser.date(from: timestamp)
            if content.updated == nil {
                parser.formatOptions = [.withInternetDateTime]
                content.updated = parser.date(from: timestamp)
            }
        }
        content.stale = !successful
            || content.updated.map { Date().timeIntervalSince($0) > 120 }
            ?? true
        quotaTooltip = [
            quotaDetail("5 小时", content.fiveHours),
            quotaDetail("本周", content.week),
        ].joined(separator: "\n")
        refreshContentTooltip()
    }

    /// Selects one quota window by its official duration.
    private func quotaWindow(minutes: Int, windows: [[String: Any]]) -> QuotaWindow {
        guard let value = windows.first(where: {
            ($0["windowDurationMins"] as? Int) == minutes
        }),
        let used = value["usedPercent"] as? Double,
        let resetSeconds = value["resetsAt"] as? Double else {
            return QuotaWindow(remaining: nil, reset: nil)
        }
        let reset = Date(timeIntervalSince1970: resetSeconds)
        return QuotaWindow(
            remaining: reset > Date() ? max(0, min(100, 100 - used)) : nil,
            reset: reset
        )
    }

    /// Formats one quota value for the accessibility tooltip.
    private func quotaDetail(_ title: String, _ value: QuotaWindow) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        let amount = value.remaining.map { "\(Int($0.rounded()))%" } ?? "未知"
        let reset = value.reset.map { formatter.string(from: $0) } ?? "未知"
        return "\(title)剩余 \(amount)，重置于 \(reset)"
    }

    /// Combines quota and queue state into one concise overlay tooltip.
    private func refreshContentTooltip() {
        let queueState = content.queueUnavailable
            ? "任务队列暂不可用，显示上次结果 \(content.queueCount) 个"
            : "任务队列 \(content.queueCount) 个"
        content.toolTip = "\(quotaTooltip)\n\(queueState)\n点击任务数量折叠或展开"
    }
}

@main
private struct OverlayApplication {
    /// Starts the accessory AppKit application and retains its delegate for the full run loop.
    static func main() {
        let app = NSApplication.shared
        let delegate = Overlay()
        app.delegate = delegate
        withExtendedLifetime(delegate) {
            app.run()
        }
    }
}
