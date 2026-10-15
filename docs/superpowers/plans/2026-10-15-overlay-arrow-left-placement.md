# Overlay Arrow and Left Placement Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit queue disclosure arrow, stop blank clicks from opening Web pages, and place the default overlay beside the navigation button at the lower-left.

**Architecture:** Extend the existing pure Swift support module with default-placement and hit-routing helpers, then make the AppKit view draw and consume those helpers. Keep persisted custom positions unchanged; only the non-custom default anchor moves.

**Tech Stack:** Swift 6.2, AppKit, CoreGraphics, Foundation, Python launchd installer.

---

## File Map

- Modify `scripts/quota_overlay_support.swift`: default origin and hit-routing geometry.
- Modify `tests/quota_overlay_support_tests.swift`: red/green coverage for position, arrow, tasks, quota, and no-op areas.
- Modify `scripts/quota_overlay.swift`: draw arrow, route clicks, preserve bottom-left when resizing, and use the new default anchor.
- Modify `scripts/macos_overlay.py`: update the install confirmation to name the arrow interaction.
- Modify `README.md`: describe the visible arrow and left-bottom default location.
- Modify `PRD.md`: update only after verified real UI behavior.

### Task 1: Test Default Placement and Click Routing

**Files:**
- Modify: `tests/quota_overlay_support_tests.swift`
- Modify: `scripts/quota_overlay_support.swift`

- [ ] **Step 1: Write failing support tests**

Add assertions for:

```swift
let navigation = CGRect(x: 106, y: 154, width: 36, height: 36)
assertPoint(
    defaultOverlayOrigin(navigationFrame: navigation),
    CGPoint(x: 150, y: 154),
    "default overlay sits eight points right of the button"
)
```

Add a pure target enum and route assertions:

```swift
enum OverlayHitTarget: Equatable {
    case queueToggle
    case task(Int)
    case quota
    case none
}
```

Test:

- expanded arrow center → `.queueToggle`;
- collapsed arrow center → `.queueToggle`;
- expanded task row → `.task(0)`;
- either quota ring → `.quota`;
- queue-section title, count, timestamp, and blank area → `.none`;
- `dragged=true` at arrow/task/quota points → `.none`;
- a pure priority resolver with all candidate flags set chooses drag, then arrow, then task, then quota, then none;
- the shared layout's 36×36 arrow rectangle does not intersect the task-section title, collapsed count, or any of five task row rectangles.

- [ ] **Step 2: Run the tests and verify failure**

```bash
swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift \
  -o /tmp/quota-overlay-support-tests
```

Expected: FAIL because the new placement signature and hit-routing helpers do not exist.

- [ ] **Step 3: Implement pure helpers**

Add documented functions:

```swift
func defaultOverlayOrigin(navigationFrame: CGRect) -> CGPoint
func overlayQueueLayout(panelSize: CGSize, collapsed: Bool) -> OverlayQueueLayout
func quotaHitRects(panelSize: CGSize) -> [CGRect]
func resolveOverlayHitTarget(
    dragged: Bool,
    arrowHit: Bool,
    taskIndex: Int?,
    quotaHit: Bool
) -> OverlayHitTarget
func overlayHitTarget(
    point: CGPoint,
    panelSize: CGSize,
    queueCollapsed: Bool,
    visibleTaskCount: Int,
    dragged: Bool
) -> OverlayHitTarget
```

`OverlayQueueLayout` must expose the arrow, task-section title, collapsed count, and five task row rectangles. Both drawing and hit testing consume these rectangles. Routing order must be completed drag → arrow → visible task rows → quota rectangles → none.

Remove `resizedRelativeOrigin`; bottom-left resizing no longer changes persisted relative offsets.

- [ ] **Step 4: Run the tests**

```bash
swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift \
  -o /tmp/quota-overlay-support-tests
/tmp/quota-overlay-support-tests
```

Expected: `quota overlay support tests passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift
git commit -m "test: cover overlay arrow hit routing"
```

### Task 2: Draw the Arrow and Remove the Web Fallback

**Files:**
- Modify: `scripts/quota_overlay.swift:105-224`

- [ ] **Step 1: Draw the 36×36 disclosure control**

Use `overlayQueueLayout(panelSize:collapsed:)` for both rendering and hit testing. Draw a quiet rounded control surface and a two-segment chevron:

- expanded: chevron points up;
- collapsed: chevron points down.

Draw task-section title, collapsed count, and task labels inside their layout rectangles so they cannot visually overlap the arrow.

- [ ] **Step 2: Route clicks through `overlayHitTarget`**

Replace the current fallback:

```swift
NSWorkspace.shared.open(overviewURL)
```

with an exhaustive switch:

```swift
switch overlayHitTarget(...) {
case .queueToggle:
    onToggleQueue?()
case .task:
    NSWorkspace.shared.open(queuePageURL)
case .quota:
    NSWorkspace.shared.open(overviewURL)
case .none:
    break
}
```

A completed drag must still return before hit routing.

Update the overlay tooltip from “点击任务数量折叠或展开” to “点击任务区箭头折叠或展开”.

- [ ] **Step 3: Preserve the lower-left while resizing**

In `toggleQueueCollapsed()`:

- keep `panel.frame.minX` and `panel.frame.minY`;
- set only width and the new 270/194 height;
- do not write custom relative offsets;
- refresh placement after resizing.

- [ ] **Step 4: Anchor the default panel beside the actual button**

After positioning and clamping `navigationPanel`, calculate the non-custom overlay origin from `navigationPanel.frame`:

```swift
requested = defaultOverlayOrigin(navigationFrame: navigationPanel.frame)
```

Then clamp the overlay to the selected screen's `visibleFrame`. Custom placement remains unchanged.

- [ ] **Step 5: Compile the AppKit binary**

```bash
swiftc -module-cache-path /private/tmp/codex-quota-swift-cache \
  -o /tmp/codex-quota-overlay \
  scripts/quota_overlay_support.swift scripts/quota_overlay.swift
```

Expected: compilation succeeds.

- [ ] **Step 6: Commit**

```bash
git add scripts/quota_overlay.swift
git commit -m "feat: add overlay disclosure arrow"
```

### Task 3: Install and Verify the Real Interaction

**Files:**
- Modify: `scripts/macos_overlay.py`
- Modify after verification: `README.md`
- Modify after verification: `PRD.md`

- [ ] **Step 1: Update the install confirmation**

Change the success message from “点击任务数量可折叠” to “点击任务区箭头可折叠或展开”.

- [ ] **Step 2: Preflight permissions and register guaranteed cleanup**

```bash
# Run before any preference, queue, or window mutation.
xcrun swift -e 'import CoreGraphics; import Darwin; exit(CGPreflightPostEventAccess() ? 0 : 1)'

# Run the remaining mutable E2E steps in one bash process (or one /tmp script).
set -euo pipefail
TEST_ID=''
defaults export local.codex-token-analyzer.overlay /tmp/codex-overlay-defaults.plist
curl -fsS http://127.0.0.1:8765/api/night-queue > /tmp/codex-overlay-queue-before.json
curl -fsS http://127.0.0.1:8765/api/night-queue/models > /tmp/codex-overlay-models.json

cleanup_overlay_test() {
  if [ -n "${TEST_ID:-}" ]; then
    curl -fsS -X DELETE "http://127.0.0.1:8765/api/night-queue/items/$TEST_ID" || true
  fi
  if [ -f /tmp/codex-overlay-queue-before.json ]; then
    OLD_ENABLED=$(.venv/bin/python -c 'import json; print(str(json.load(open("/tmp/codex-overlay-queue-before.json"))["config"]["enabled"]).lower())')
    curl -fsS -X PUT -H 'Content-Type: application/json' \
      -d "{\"enabled\":$OLD_ENABLED}" http://127.0.0.1:8765/api/night-queue/config || true
  fi
  if [ -f /tmp/codex-overlay-defaults.plist ]; then
    defaults import local.codex-token-analyzer.overlay /tmp/codex-overlay-defaults.plist || true
  fi
  launchctl kickstart -k "gui/$(id -u)/local.codex-token-analyzer.overlay" || true
  rm -f /tmp/codex-overlay-test-item-id /tmp/codex-overlay-queue-before.json \
    /tmp/codex-overlay-models.json /tmp/codex-overlay-defaults.plist
}
trap cleanup_overlay_test EXIT INT TERM

curl -fsS -X PUT -H 'Content-Type: application/json' \
  -d '{"enabled":false}' http://127.0.0.1:8765/api/night-queue/config

# Create one known visible item while dispatch is disabled.
read MODEL EFFORT <<EOF
$(.venv/bin/python -c 'import json; q=json.load(open("/tmp/codex-overlay-queue-before.json"))["config"]; ms=json.load(open("/tmp/codex-overlay-models.json"))["items"]; mid=q.get("default_model") or ms[0]["id"]; m=next((x for x in ms if x["id"]==mid),ms[0]); print(mid, q.get("default_effort") or m.get("default_effort") or m.get("efforts",["medium"])[0])')
EOF
TEST_ID=$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d "{\"title\":\"__overlay_ui_test__\",\"prompt\":\"UI verification only\",\"cwd\":\"$PWD\",\"model\":\"$MODEL\",\"effort\":\"$EFFORT\"}" \
  http://127.0.0.1:8765/api/night-queue/items | \
  .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
printf '%s' "$TEST_ID" > /tmp/codex-overlay-test-item-id

.venv/bin/python scripts/macos_overlay.py install
launchctl list | grep local.codex-token-analyzer.overlay
curl -fsS http://127.0.0.1:8765/api/quota >/dev/null
curl -fsS http://127.0.0.1:8765/api/night-queue >/dev/null
tail -n 20 "$HOME/Library/Application Support/CodexTokenAnalyzer/data/overlay.err.log"
```

If the permission preflight fails, stop before creating the script or changing any state and request user interaction.

- [ ] **Step 3: Verify default placement**

Set and reload the known default-placement state:

```bash
defaults write local.codex-token-analyzer.overlay customPosition -bool false
defaults delete local.codex-token-analyzer.overlay panelRelativeX || true
defaults delete local.codex-token-analyzer.overlay panelRelativeY || true
defaults write local.codex-token-analyzer.overlay overlayVisible -bool true
launchctl kickstart -k "gui/$(id -u)/local.codex-token-analyzer.overlay"
open -b com.openai.codex
sleep 1
```

Use `CGWindowListCopyWindowInfo` to print the 36×36 and 260×194/270 overlay frames. Verify:

- navigation button remains in the Codex left rail;
- overlay `minX = navigationButton.maxX + 8`;
- overlay `minY = navigationButton.minY`;
- both remain inside the target screen.

- [ ] **Step 4: Verify click behavior with real mouse events**

Derive click points from the printed panel frame and `OverlayQueueLayout`, then send real down/up events with `CGEvent`.

- record the frontmost bundle ID before each click;
- arrow toggles 270 ↔ 194 and reverses direction in screenshots;
- `minX/minY` do not change in an unclamped case;
- clicking task-section title, count, timestamp, and blank space leaves the frontmost bundle ID as `com.openai.codex` and does not create a browser navigation;
- clicking a visible task row activates the default browser on `http://127.0.0.1:8765/?view=night-queue`;
- clicking a quota ring activates the default browser on `http://127.0.0.1:8765/?view=overview`;
- dragging does not trigger any click action.

Use this URL detector after task/quota clicks:

```bash
FRONT=$(osascript -e 'tell application "System Events" to get bundle identifier of first application process whose frontmost is true')
case "$FRONT" in
  com.google.Chrome) osascript -e 'tell application "Google Chrome" to get URL of active tab of front window' ;;
  com.apple.Safari) osascript -e 'tell application "Safari" to get URL of current tab of front window' ;;
  *) echo "unsupported browser: $FRONT"; exit 1 ;;
esac
```

Before no-op clicks, record the active browser URL; after the click, require both unchanged URL and frontmost `com.openai.codex`.

- [ ] **Step 5: Verify custom position preservation and temporary clamping**

Use an intentionally out-of-bounds horizontal offset:

```bash
defaults write local.codex-token-analyzer.overlay customPosition -bool true
defaults write local.codex-token-analyzer.overlay panelRelativeX -float 1400
defaults write local.codex-token-analyzer.overlay panelRelativeY -float 500
launchctl kickstart -k "gui/$(id -u)/local.codex-token-analyzer.overlay"
sleep 1
defaults read local.codex-token-analyzer.overlay
```

Verify the displayed frame is minimally clamped, while stored offsets remain `1400/500` through expand, collapse, restart, and Codex window movement.

- [ ] **Step 6: Restore state on both success and failure**

All mutable verification steps remain inside the shell process that registered `cleanup_overlay_test`. On success, run `cleanup_overlay_test`, then clear the trap with `trap - EXIT INT TERM`. On any error or interruption, the trap runs the same cleanup automatically.

Confirm restored preferences and panel state match the backup.

- [ ] **Step 7: Run regression checks**

```bash
swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift \
  -o /tmp/quota-overlay-support-tests
/tmp/quota-overlay-support-tests
.venv/bin/python -m py_compile scripts/macos_overlay.py
npm run build --prefix frontend
git diff --check
```

The known four baseline `tests/test_night_queue.py` failures remain out of scope unless their behavior changes.

- [ ] **Step 8: Update current product facts**

After all feature checks pass:

- update README usage wording and default location;
- update the macOS overlay bullets in `PRD.md`;
- do not claim unverified behavior.

- [ ] **Step 9: Review and commit**

```bash
git add README.md PRD.md scripts/macos_overlay.py
git commit -m "docs: describe overlay arrow interaction"
```
