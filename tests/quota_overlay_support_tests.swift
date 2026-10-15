import CoreGraphics
import Foundation

private func assertEqual<T: Equatable>(_ actual: T, _ expected: T, _ message: String) {
    guard actual == expected else {
        fatalError("\(message): expected \(expected), got \(actual)")
    }
}

private func assertPoint(_ actual: CGPoint, _ expected: CGPoint, _ message: String) {
    let epsilon = 0.001
    guard abs(actual.x - expected.x) < epsilon,
          abs(actual.y - expected.y) < epsilon else {
        fatalError("\(message): expected \(expected), got \(actual)")
    }
}

private func assertDisjoint(_ first: CGRect, _ second: CGRect, _ message: String) {
    guard !first.intersects(second) else {
        fatalError("\(message): \(first) intersects \(second)")
    }
}

private func item(_ title: String, status: String = "queued", position: Int) -> [String: Any] {
    ["title": title, "status": status, "position": position]
}

@main
private struct OverlaySupportTests {
    static func main() {
        testWindowValidation()
        testCoordinateConversion()
        testAnchorsAndClamping()
        testOverlayHitRouting()
        testScreenSelection()
        testQueueSummary()
        print("quota overlay support tests passed")
    }

    private static func testWindowValidation() {
        assertEqual(isValidTargetWindow(CGRect(x: 0, y: 0, width: 120, height: 120)), true,
                    "minimum target window is valid")
        assertEqual(isValidTargetWindow(CGRect(x: 0, y: 0, width: 119, height: 120)), false,
                    "narrow target window is invalid")
        assertEqual(isValidTargetWindow(CGRect(x: 0, y: 0, width: 120, height: 119)), false,
                    "short target window is invalid")
    }

    private static func testCoordinateConversion() {
        assertEqual(
            appKitWindowFrame(
                cgFrame: CGRect(x: 100, y: 50, width: 1000, height: 700),
                referenceMaxY: 900
            ),
            CGRect(x: 100, y: 150, width: 1000, height: 700),
            "main display conversion"
        )
        assertEqual(
            appKitWindowFrame(
                cgFrame: CGRect(x: -1440, y: -900, width: 1440, height: 900),
                referenceMaxY: 900
            ),
            CGRect(x: -1440, y: 900, width: 1440, height: 900),
            "display above and left conversion"
        )
        assertEqual(
            appKitWindowFrame(
                cgFrame: CGRect(x: 0, y: 900, width: 1440, height: 900),
                referenceMaxY: 900
            ),
            CGRect(x: 0, y: -900, width: 1440, height: 900),
            "display below conversion"
        )
    }

    private static func testAnchorsAndClamping() {
        let window = CGRect(x: 100, y: 100, width: 1000, height: 700)
        let navigation = CGRect(x: 106, y: 154, width: 36, height: 36)
        assertPoint(
            defaultOverlayOrigin(navigationFrame: navigation),
            CGPoint(x: 150, y: 154),
            "default overlay sits beside the navigation button"
        )
        assertPoint(
            navigationOrigin(window: window, buttonSize: CGSize(width: 36, height: 36)),
            CGPoint(x: 106, y: 154),
            "navigation origin"
        )
        assertPoint(
            customOverlayOrigin(window: window, relativeOrigin: CGPoint(x: 500, y: 200)),
            CGPoint(x: 600, y: 300),
            "custom overlay origin"
        )
        assertPoint(
            clampedOrigin(
                CGPoint(x: -1500, y: -10),
                size: CGSize(width: 260, height: 194),
                visibleFrame: CGRect(x: -1440, y: 0, width: 1440, height: 900)
            ),
            CGPoint(x: -1440, y: 0),
            "negative display clamping"
        )
    }

    private static func testOverlayHitRouting() {
        let expandedSize = CGSize(width: 260, height: 270)
        let collapsedSize = CGSize(width: 260, height: 194)
        let expanded = overlayQueueLayout(panelSize: expandedSize, collapsed: false)
        let collapsed = overlayQueueLayout(panelSize: collapsedSize, collapsed: true)

        assertEqual(expanded.arrowRect.size, CGSize(width: 36, height: 36),
                    "expanded arrow hit target")
        assertEqual(collapsed.arrowRect.size, CGSize(width: 36, height: 36),
                    "collapsed arrow hit target")
        for row in expanded.taskRowRects {
            assertDisjoint(expanded.arrowRect, row, "arrow and task row")
        }
        assertDisjoint(expanded.arrowRect, expanded.titleRect, "arrow and task-section title")
        assertDisjoint(collapsed.arrowRect, collapsed.countRect, "arrow and collapsed count")

        assertEqual(
            overlayHitTarget(
                point: CGPoint(x: expanded.arrowRect.midX, y: expanded.arrowRect.midY),
                panelSize: expandedSize,
                queueCollapsed: false,
                visibleTaskCount: 5,
                dragged: false
            ),
            .queueToggle,
            "expanded arrow toggles"
        )
        assertEqual(
            overlayHitTarget(
                point: CGPoint(x: collapsed.arrowRect.midX, y: collapsed.arrowRect.midY),
                panelSize: collapsedSize,
                queueCollapsed: true,
                visibleTaskCount: 0,
                dragged: false
            ),
            .queueToggle,
            "collapsed arrow toggles"
        )
        assertEqual(
            overlayHitTarget(
                point: CGPoint(x: expanded.taskRowRects[0].midX,
                               y: expanded.taskRowRects[0].midY),
                panelSize: expandedSize,
                queueCollapsed: false,
                visibleTaskCount: 1,
                dragged: false
            ),
            .task(0),
            "visible task row opens the queue"
        )
        assertEqual(
            overlayHitTarget(
                point: CGPoint(x: 68, y: expandedSize.height - 59),
                panelSize: expandedSize,
                queueCollapsed: false,
                visibleTaskCount: 1,
                dragged: false
            ),
            .quota,
            "quota ring opens the overview"
        )

        for (point, size, isCollapsed, message) in [
            (CGPoint(x: expanded.titleRect.midX, y: expanded.titleRect.midY),
             expandedSize, false, "task-section title"),
            (CGPoint(x: collapsed.countRect.midX, y: collapsed.countRect.midY),
             collapsedSize, true, "collapsed count"),
            (CGPoint(x: 100, y: 10), expandedSize, false, "timestamp"),
            (CGPoint(x: 8, y: 80), expandedSize, false, "blank area"),
        ] {
            assertEqual(
                overlayHitTarget(
                    point: point,
                    panelSize: size,
                    queueCollapsed: isCollapsed,
                    visibleTaskCount: 1,
                    dragged: false
                ),
                .none,
                "\(message) is a no-op"
            )
        }

        assertEqual(
            resolveOverlayHitTarget(
                dragged: true,
                arrowHit: true,
                taskIndex: 0,
                quotaHit: true
            ),
            .none,
            "completed drag suppresses every click action"
        )
        assertEqual(
            resolveOverlayHitTarget(
                dragged: false,
                arrowHit: true,
                taskIndex: 0,
                quotaHit: true
            ),
            .queueToggle,
            "arrow wins over lower-priority candidates"
        )
        assertEqual(
            resolveOverlayHitTarget(
                dragged: false,
                arrowHit: false,
                taskIndex: 0,
                quotaHit: true
            ),
            .task(0),
            "task wins over quota"
        )
        assertEqual(
            resolveOverlayHitTarget(
                dragged: false,
                arrowHit: false,
                taskIndex: nil,
                quotaHit: true
            ),
            .quota,
            "quota wins over none"
        )
        assertEqual(
            resolveOverlayHitTarget(
                dragged: false,
                arrowHit: false,
                taskIndex: nil,
                quotaHit: false
            ),
            .none,
            "no candidate produces no action"
        )
    }

    private static func testScreenSelection() {
        let left = CGRect(x: -1440, y: 0, width: 1440, height: 900)
        let main = CGRect(x: 0, y: 0, width: 1440, height: 900)
        assertEqual(
            targetScreenFrame(
                window: CGRect(x: -100, y: 100, width: 500, height: 500),
                screens: [left, main],
                mainScreen: main
            ),
            main,
            "largest screen intersection"
        )
        assertEqual(
            targetScreenFrame(
                window: CGRect(x: -200, y: 100, width: 400, height: 500),
                screens: [left, main],
                mainScreen: main
            ),
            main,
            "main display wins an intersection tie"
        )
    }

    private static func testQueueSummary() {
        assertEqual(summarizeQueueItems([]).count, 0, "empty queue count")
        assertEqual(
            summarizeQueueItems([item("唯一任务", position: 1)]).titles,
            ["唯一任务"],
            "single queue title"
        )

        let five = (1...5).map { item("任务 \($0)", position: $0) }
        let fiveSummary = summarizeQueueItems(five)
        assertEqual(fiveSummary.count, 5, "five-item queue count")
        assertEqual(fiveSummary.titles.count, 5, "five-item title limit")

        let mixed = [
            item("第六项", position: 6),
            item("已完成", status: "completed", position: 1),
            item("第二项", position: 2),
            item("失败", status: "failed", position: 3),
            item("第一项", position: 1),
            item("已派发", status: "dispatched", position: 4),
            item("第五项", position: 5),
            item("监控中", status: "watching", position: 5),
            item("第四项", position: 4),
            item("第三项", position: 3),
        ]
        let mixedSummary = summarizeQueueItems(mixed)
        assertEqual(mixedSummary.count, 6, "count is computed before title truncation")
        assertEqual(
            mixedSummary.titles,
            ["第一项", "第二项", "第三项", "第四项", "第五项"],
            "queue filters and sorts before limiting titles"
        )
    }
}
