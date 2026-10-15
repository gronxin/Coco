import CoreGraphics
import Foundation

private let hiddenQueueStatuses: Set<String> = [
    "completed",
    "failed",
    "dispatched",
    "watching",
]

/// Returns whether a CoreGraphics window frame is large enough to host the overlay controls.
func isValidTargetWindow(_ frame: CGRect) -> Bool {
    frame.width >= 120 && frame.height >= 120
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

/// Calculates the default overlay origin inside the target window's upper-right corner.
func defaultOverlayOrigin(window: CGRect, panelSize: CGSize) -> CGPoint {
    CGPoint(
        x: window.maxX - panelSize.width - 16,
        y: window.maxY - panelSize.height - 42
    )
}

/// Calculates the navigation button origin above the account area in the Codex rail.
func navigationOrigin(window: CGRect, buttonSize: CGSize) -> CGPoint {
    CGPoint(x: window.minX + 6, y: window.minY + 54)
}

/// Restores a user-selected overlay origin relative to the target window.
func customOverlayOrigin(window: CGRect, relativeOrigin: CGPoint) -> CGPoint {
    CGPoint(
        x: window.minX + relativeOrigin.x,
        y: window.minY + relativeOrigin.y
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
