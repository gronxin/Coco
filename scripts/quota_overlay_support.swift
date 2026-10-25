import CoreGraphics
import Foundation

private let hiddenQueueStatuses: Set<String> = [
    "completed",
    "failed",
    "dispatched",
    "watching",
]
private let defaultOverlayLift: CGFloat = 96

enum OverlayHitTarget: Equatable {
    case queueToggle
    case task(Int)
    case quota
    case none
}

struct OverlayQueueLayout {
    let arrowRect: CGRect
    let titleRect: CGRect
    let countRect: CGRect
    let taskRowRects: [CGRect]
}

struct QuotaRingLayout {
    let center: CGPoint
    let radius: CGFloat
}

/// Returns whether a CoreGraphics window frame is large enough to host the overlay controls.
func isValidTargetWindow(_ frame: CGRect) -> Bool {
    frame.width >= 120 && frame.height >= 120
}

/// Selects the Codex window that should anchor the overlay controls.
///
/// Codex exposes several layer-zero windows at once (for example the `Computer Use`
/// panels alongside the main editor window), so taking the first match would anchor
/// the overlay to an arbitrary small panel. Choosing the largest valid frame keeps
/// the controls attached to the main window; ties keep the earliest candidate.
func preferredTargetWindowFrame(_ frames: [CGRect]) -> CGRect? {
    let ranked = frames.filter(isValidTargetWindow).map { frame -> (frame: CGRect, area: CGFloat) in
        (frame, frame.width * frame.height)
    }
    guard let largestArea = ranked.map(\.area).max() else { return nil }
    return ranked.first { $0.area == largestArea }?.frame
}

/// Converts a CoreGraphics top-left-origin window frame into AppKit's bottom-left coordinates.
func appKitWindowFrame(cgFrame: CGRect, referenceMaxY: CGFloat) -> CGRect {
    CGRect(
        x: cgFrame.minX,
        y: referenceMaxY - cgFrame.minY - cgFrame.height,
        width: cgFrame.width,
        height: cgFrame.height
    )
}

/// Calculates the lifted default overlay origin beside the visible navigation button.
func defaultOverlayOrigin(navigationFrame: CGRect) -> CGPoint {
    CGPoint(
        x: navigationFrame.maxX + 8,
        y: navigationFrame.minY + defaultOverlayLift
    )
}

/// Calculates the navigation button origin inside the Codex rail.
///
/// Anchored well above the account/update area at the rail's bottom so the mini
/// icon does not cover Codex's update button. Users can still drag it elsewhere.
func navigationOrigin(window: CGRect, buttonSize: CGSize) -> CGPoint {
    CGPoint(x: window.minX + 6, y: window.minY + 160)
}

/// Restores a user-dragged navigation button origin relative to the target window.
func customNavigationOrigin(window: CGRect, relativeOrigin: CGPoint) -> CGPoint {
    CGPoint(
        x: window.minX + relativeOrigin.x,
        y: window.minY + relativeOrigin.y
    )
}

/// Restores a user-selected overlay origin relative to the target window.
func customOverlayOrigin(window: CGRect, relativeOrigin: CGPoint) -> CGPoint {
    CGPoint(
        x: window.minX + relativeOrigin.x,
        y: window.minY + relativeOrigin.y
    )
}

/// Returns shared queue geometry for drawing and pointer hit testing.
func overlayQueueLayout(panelSize: CGSize, collapsed: Bool) -> OverlayQueueLayout {
    let arrowX = max(0, panelSize.width - 48)
    let arrowY = collapsed ? CGFloat(20) : max(0, panelSize.height - 167)
    let arrowRect = CGRect(x: arrowX, y: arrowY, width: 36, height: 36)
    let textWidth = max(0, arrowRect.minX - 25)
    let titleRect = CGRect(
        x: 17,
        y: max(0, panelSize.height - 151),
        width: textWidth,
        height: 18
    )
    let countRect = CGRect(x: 17, y: 32, width: textWidth, height: 18)
    let taskRows = (0..<5).map { index in
        CGRect(
            x: 17,
            y: panelSize.height - 171 - CGFloat(index * 18),
            width: textWidth,
            height: 17
        )
    }
    return OverlayQueueLayout(
        arrowRect: arrowRect,
        titleRect: titleRect,
        countRect: countRect,
        taskRowRects: taskRows
    )
}

/// Returns shared quota ring geometry for drawing and circular hit testing.
func quotaRingLayouts(panelSize: CGSize) -> [QuotaRingLayout] {
    let centerY = panelSize.height - 59
    return [
        QuotaRingLayout(
            center: CGPoint(x: 68, y: centerY),
            radius: 27
        ),
        QuotaRingLayout(
            center: CGPoint(x: 192, y: centerY),
            radius: 27
        ),
    ]
}

/// Returns whether the pointer moved far enough to become a drag.
func exceedsDragThreshold(from start: CGPoint, to current: CGPoint,
                          threshold: CGFloat = 4) -> Bool {
    let deltaX = current.x - start.x
    let deltaY = current.y - start.y
    return deltaX * deltaX + deltaY * deltaY >= threshold * threshold
}

/// Resolves overlapping pointer candidates using the documented interaction priority.
func resolveOverlayHitTarget(dragged: Bool, arrowHit: Bool,
                             taskIndex: Int?, quotaHit: Bool) -> OverlayHitTarget {
    if dragged { return .none }
    if arrowHit { return .queueToggle }
    if let taskIndex { return .task(taskIndex) }
    if quotaHit { return .quota }
    return .none
}

/// Resolves a panel point into its single allowed interaction target.
func overlayHitTarget(point: CGPoint, panelSize: CGSize, queueCollapsed: Bool,
                      visibleTaskCount: Int, dragged: Bool) -> OverlayHitTarget {
    let layout = overlayQueueLayout(panelSize: panelSize, collapsed: queueCollapsed)
    let quotaHit = quotaRingLayouts(panelSize: panelSize).contains { ring in
        let deltaX = point.x - ring.center.x
        let deltaY = point.y - ring.center.y
        return deltaX * deltaX + deltaY * deltaY <= ring.radius * ring.radius
    }
    var taskIndex: Int?
    if !queueCollapsed {
        let visibleCount = min(max(visibleTaskCount, 0), layout.taskRowRects.count)
        for index in 0..<visibleCount where layout.taskRowRects[index].contains(point) {
            taskIndex = index
            break
        }
    }
    return resolveOverlayHitTarget(
        dragged: dragged,
        arrowHit: layout.arrowRect.contains(point),
        taskIndex: taskIndex,
        quotaHit: quotaHit
    )
}

/// Keeps a panel origin inside the supplied visible screen frame.
func clampedOrigin(_ origin: CGPoint, size: CGSize, visibleFrame: CGRect) -> CGPoint {
    let maximumX = max(visibleFrame.minX, visibleFrame.maxX - size.width)
    let maximumY = max(visibleFrame.minY, visibleFrame.maxY - size.height)
    return CGPoint(
        x: min(max(origin.x, visibleFrame.minX), maximumX),
        y: min(max(origin.y, visibleFrame.minY), maximumY)
    )
}

/// Selects the screen with the largest overlap, preferring the main screen on an exact tie.
func targetScreenFrame(window: CGRect, screens: [CGRect], mainScreen: CGRect?) -> CGRect? {
    guard !screens.isEmpty else { return nil }
    let ranked = screens.map { screen -> (frame: CGRect, area: CGFloat) in
        let overlap = screen.intersection(window)
        let area = overlap.isNull ? 0 : overlap.width * overlap.height
        return (screen, area)
    }
    let largestArea = ranked.map(\.area).max() ?? 0
    let candidates = ranked.filter { $0.area == largestArea }.map(\.frame)
    if let mainScreen, candidates.contains(mainScreen) {
        return mainScreen
    }
    return candidates.first
}

/// Filters, sorts, counts, and limits queue entries for the compact overlay.
func summarizeQueueItems(_ items: [[String: Any]]) -> (count: Int, titles: [String]) {
    let actionable = items.enumerated()
        .filter { !hiddenQueueStatuses.contains($0.element["status"] as? String ?? "") }
        .sorted {
            let left = $0.element["position"] as? Int ?? 0
            let right = $1.element["position"] as? Int ?? 0
            return left == right ? $0.offset < $1.offset : left < right
        }
    let titles = actionable.prefix(5).map {
        $0.element["title"] as? String ?? "未命名任务"
    }
    return (actionable.count, titles)
}
