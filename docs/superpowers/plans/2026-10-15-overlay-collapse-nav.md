# Codex Overlay Collapse and Navigation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the macOS quota overlay queue collapsible and replace the floating restore control with a Codex-navigation-aligned button whose panels follow the active Codex window.

**Architecture:** Keep the overlay as independent non-activating AppKit panels. Extract pure frame calculations into a small Swift geometry module with executable tests, then let the AppKit delegate own visibility state, persistence, target-window selection, polling, and panel placement.

**Tech Stack:** Swift 6.2, AppKit, CoreGraphics, Foundation, Python 3.11+, launchd.

---

## File Map

- Create `scripts/quota_overlay_support.swift`: pure window/screen placement and queue-summary helpers with no AppKit application state.
- Create `tests/quota_overlay_support_tests.swift`: executable Swift assertions for anchors, clamping, coordinate conversion, negative coordinates, screen selection, and queue totals.
- Modify `scripts/quota_overlay.swift`: collapsible queue rendering, navigation button, state persistence, target-window tracking, and visibility state machine.
- Modify `scripts/macos_overlay.py`: compile both Swift sources and update the install confirmation.
- Modify `README.md`: document installation and interaction.
- Modify `PRD.md`: replace old overlay facts only after implementation and verification.

### Task 1: Add Testable Overlay Helpers

**Files:**
- Create: `scripts/quota_overlay_support.swift`
- Create: `tests/quota_overlay_support_tests.swift`

- [ ] **Step 1: Write the failing support test executable**

Cover:

```swift
@main
struct OverlaySupportTests {
    static func main() {
        assertPoint(defaultOverlayOrigin(
            window: CGRect(x: 100, y: 100, width: 1000, height: 700),
            panelSize: CGSize(width: 260, height: 194)
        ), CGPoint(x: 824, y: 564))
        assertPoint(navigationOrigin(
            window: CGRect(x: 100, y: 100, width: 1000, height: 700),
            buttonSize: CGSize(width: 36, height: 36)
        ), CGPoint(x: 106, y: 154))
    }
}
```

Also test:

- `appKitWindowFrame(cgFrame:referenceMaxY:)` for left/right and above/below displays, including negative coordinates;
- clamping to a negative-coordinate screen;
- largest-intersection screen selection;
- custom relative placement;
- the 120×120 minimum target size;
- queue summaries for 0, 1, 5, and more than 5 actionable items;
- exclusion of `completed`, `failed`, `dispatched`, and `watching` before counting;
- sorting before the first-five title limit.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift \
  -o /tmp/quota-overlay-support-tests
```

Expected: FAIL because the support file and functions do not exist.

- [ ] **Step 3: Implement the pure helpers**

Implement documented functions:

```swift
func isValidTargetWindow(_ frame: CGRect) -> Bool
func appKitWindowFrame(cgFrame: CGRect, referenceMaxY: CGFloat) -> CGRect
func defaultOverlayOrigin(window: CGRect, panelSize: CGSize) -> CGPoint
func navigationOrigin(window: CGRect, buttonSize: CGSize) -> CGPoint
func customOverlayOrigin(window: CGRect, relativeOrigin: CGPoint) -> CGPoint
func clampedOrigin(_ origin: CGPoint, size: CGSize, visibleFrame: CGRect) -> CGPoint
func targetScreenFrame(window: CGRect, screens: [CGRect], mainScreen: CGRect?) -> CGRect?
func summarizeQueueItems(_ items: [[String: Any]]) -> (count: Int, titles: [String])
```

`summarizeQueueItems` must filter and sort the complete array, set `count` before truncation, and return at most five titles.

- [ ] **Step 4: Compile and run the test**

Run:

```bash
swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift \
  -o /tmp/quota-overlay-support-tests
/tmp/quota-overlay-support-tests
```

Expected: `quota overlay support tests passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift
git commit -m "test: cover overlay placement and queue summary"
```

### Task 2: Implement Queue Collapse and Navigation Control

**Files:**
- Modify: `scripts/quota_overlay.swift:5-258`

- [ ] **Step 1: Add persisted state constants and panel sizes**

Add keys `overlayVisible`, `queueCollapsed`, `customPosition`, `panelRelativeX`, and `panelRelativeY`. Define expanded height 270, collapsed height 194, and width 260 points. Empty preferences must produce `false`, `false`, and `false`; preferences containing only old keys must produce the same defaults.

Remove all use of `hiddenByUser`, `windowOriginX`, `windowOriginY`, `onHide`, `onRestore`, `restorePosition()`, and the old one-second preference polling. Leave old preference keys untouched on disk.

- [ ] **Step 2: Replace `RestoreView` with `NavigationButtonView`**

The 36×36 non-activating control must:

- use no panel shadow;
- match the Codex rail's dark neutral surface;
- show two quota rings and queue lines;
- expose a single `onToggle` callback;
- update its accessibility title between “显示…” and “隐藏…”.

- [ ] **Step 3: Make `QuotaView` render both queue states**

Add:

```swift
var queueCount = 0
var queueCollapsed = false
var onToggleQueue: (() -> Void)?
```

Expanded state keeps the first five titles and shows the total count. Collapsed state renders only `N 个任务` in a clickable row. Update hit testing so the queue row toggles collapse, task rows open the queue page, and quota rings open the overview.

Use top-relative layout values:

- quota ring centers: `bounds.maxY - 59`;
- quota labels and reset labels: relative to the same top edge;
- divider: `bounds.maxY - 127`;
- expanded queue header and task rows: below the divider;
- collapsed count row: between the divider and timestamp;
- timestamp: fixed at the bottom.

This keeps quota content visible at both heights. Define explicit `CGRect` hit regions for the queue toggle and each visible task row.

On queue API failure, keep the last successful count and titles, set `queueUnavailable=true`, and expose stale/unavailable detail through the tooltip without replacing the collapsed count.

- [ ] **Step 4: Resize the panel without moving its top edge**

When queue state changes:

1. capture the current panel top edge;
2. select expanded or collapsed height;
3. update content and panel frames;
4. restore the top edge;
5. if custom placement is active, recompute and persist `panelRelativeY`;
6. persist `queueCollapsed`.

- [ ] **Step 5: Build the overlay**

Run:

```bash
swiftc -module-cache-path /private/tmp/codex-quota-swift-cache \
  -o /tmp/codex-quota-overlay \
  scripts/quota_overlay_support.swift scripts/quota_overlay.swift
```

Expected: compilation succeeds with no errors.

`scripts/quota_overlay.swift` must replace top-level expressions with one `@main` application type so multi-file Swift compilation has exactly one entry point.

- [ ] **Step 6: Commit**

```bash
git add scripts/quota_overlay.swift
git commit -m "feat: collapse overlay task queue"
```

### Task 3: Follow the Active Codex Window

**Files:**
- Modify: `scripts/quota_overlay.swift:190-329`

- [ ] **Step 1: Add target-window discovery**

Select the first front-to-back, on-screen, layer-0 Codex window whose converted frame passes `isValidTargetWindow`. Convert CoreGraphics top-left coordinates to AppKit coordinates using the fixed global coordinate reference `NSScreen.screens.first.frame.maxY`, not the dynamically changing `NSScreen.main`.

Parse the bounds into a structured `CGRect`, use the frontmost Codex application's PID, preserve the CoreGraphics array order, and reject invalid or smaller-than-120×120 frames.

- [ ] **Step 2: Add foreground tracking lifecycle**

Implement function-level-documented methods:

```swift
private func startTracking()
private func stopTracking()
private func refreshPlacementAndVisibility()
private func targetCodexWindow() -> CGRect?
private func targetScreen(for window: CGRect) -> NSScreen?
```

Start a 0.1-second timer only while Codex is frontmost. Stop it and hide both panels when another app activates.

- [ ] **Step 3: Implement the visibility matrix**

- Button visible when Codex is frontmost and a target exists.
- Overlay visible only when `overlayVisible` is true.
- Clicking the button toggles and persists `overlayVisible`.
- Backgrounding hides without changing the persisted intent.
- If one Codex window closes or minimizes, the next eligible window becomes the target.

- [ ] **Step 4: Implement anchored and custom placement**

- Default overlay: target window right/top inset.
- Navigation button: target window left/bottom inset above the account avatar.
- Custom overlay: target-window-relative origin.
- Clamp both panels to the chosen screen's `visibleFrame`.
- On invalid custom placement, reset `customPosition` and use the default anchor.
- Add `onDragStarted` and `onDragEnded` callbacks. While dragging, keep positioning the navigation button but skip overlay repositioning. On drag end, clamp the panel, compute its relative origin from the current target window, and persist all custom-position fields.

- [ ] **Step 5: Run automated checks**

Run:

```bash
/tmp/quota-overlay-support-tests
swiftc -module-cache-path /private/tmp/codex-quota-swift-cache \
  -o /tmp/codex-quota-overlay \
  scripts/quota_overlay_support.swift scripts/quota_overlay.swift
```

Expected: geometry tests pass and the AppKit binary compiles.

- [ ] **Step 6: Commit**

```bash
git add scripts/quota_overlay.swift
git commit -m "feat: anchor overlay to Codex window"
```

### Task 4: Update Installer and User Documentation

**Files:**
- Modify: `scripts/macos_overlay.py:24-69`
- Modify: `README.md:56-64`

- [ ] **Step 1: Update the build command**

Compile `scripts/quota_overlay_support.swift` together with `scripts/quota_overlay.swift`.

- [ ] **Step 2: Update install confirmation and README**

Document:

- separate overlay install command;
- navigation button behavior;
- collapsible queue behavior;
- automatic following while Codex is frontmost.

- [ ] **Step 3: Run Python and Swift checks**

Run:

```bash
.venv/bin/python -m py_compile scripts/macos_overlay.py
.venv/bin/python scripts/macos_overlay.py status
/tmp/quota-overlay-support-tests
```

Expected: Python compiles, status returns structured state, geometry tests pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/macos_overlay.py README.md
git commit -m "docs: explain Codex navigation overlay"
```

### Task 5: Install and Verify the Real macOS Experience

**Files:**
- Modify after verification: `PRD.md:41-48`

- [ ] **Step 1: Install the updated LaunchAgent**

Run:

```bash
.venv/bin/python scripts/macos_overlay.py install
```

Expected: binary is rebuilt, LaunchAgent is loaded, and the process remains alive.

- [ ] **Step 2: Verify service dependencies**

Run:

```bash
curl -fsS http://127.0.0.1:8765/api/quota >/dev/null
curl -fsS http://127.0.0.1:8765/api/night-queue >/dev/null
launchctl list | grep local.codex-token-analyzer.overlay
tail -n 50 "$HOME/Library/Application Support/CodexTokenAnalyzer/data/overlay.err.log"
```

Expected: both APIs succeed, LaunchAgent has a PID, and the current run adds no error output.

- [ ] **Step 3: Verify the real UI and record each result**

Using the actual Codex desktop application:

- confirm the button is aligned inside the left rail;
- confirm empty preferences and preferences containing only old keys start with the button visible and overlay hidden;
- click the button, collapse control, and drag the panel, confirming none changes the frontmost bundle ID;
- confirm opening overview or queue pages intentionally activates the browser;
- collapse the queue and confirm only `N 个任务` remains and total count uses the pre-limit filtered set;
- restart the overlay and confirm display intent, collapse state, and custom position persist;
- move and resize Codex and confirm panels settle within 0.2 seconds;
- drag the overlay, move Codex, and confirm the custom relative position follows without snapping during drag or after a collapse/expand cycle;
- minimize/close/switch Codex windows and confirm the target changes or panels hide;
- verify full-screen and Space switching;
- move Codex across available left/right or above/below screens, including negative coordinates and partial off-screen placement, and confirm both panels stay visible;
- shrink the window below the valid threshold and confirm panels hide;
- switch away from Codex and confirm the 0.1-second tracking timer stops.

- [ ] **Step 4: Run the repository regression suite**

Run:

```bash
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

Expected: all Python tests pass and the frontend production build succeeds.

- [ ] **Step 5: Update the main PRD only after every check passes**

If any automated check, UI acceptance item, or regression check fails, stop without changing the PRD. After all pass, replace the old macOS overlay bullets with only the verified new behavior.

- [ ] **Step 6: Review the final diff and commit**

Run:

```bash
git diff --check
git status --short
```

Then:

```bash
git add PRD.md scripts/quota_overlay.swift scripts/quota_overlay_support.swift \
  scripts/macos_overlay.py tests/quota_overlay_support_tests.swift README.md
git commit -m "feat: integrate overlay controls with Codex navigation"
```
