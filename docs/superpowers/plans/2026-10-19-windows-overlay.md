# Windows Quota Overlay Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Windows 10/11 quota overlay matching the macOS interaction and move both platforms' default panel anchor 96 logical units upward.

**Architecture:** Keep Swift/AppKit on macOS. On Windows, separate pure geometry/state, Win32 APIs, network/instance lifecycle, Tk controller/view, stable launcher, and transactional installer so every non-visual boundary can be tested on macOS with fakes.

**Tech Stack:** Python 3.11 standard library, Tkinter, Win32 APIs through `ctypes`, Swift/AppKit, pytest.

---

## File Map

- Modify `scripts/quota_overlay_support.swift`: 96-point default lift.
- Modify `tests/quota_overlay_support_tests.swift`: macOS anchor regression.
- Create `scripts/windows_overlay_support.py`: pure geometry, parsers, hit routing, state validation and atomic JSON.
- Create `scripts/windows_overlay_win32.py`: lazy Win32 adapter only.
- Create `scripts/windows_overlay_worker.py`: bounded HTTP worker and instance/ready lifecycle.
- Create `scripts/windows_quota_overlay.py`: controller plus lazy Tk Canvas view.
- Create `scripts/windows_overlay_launcher.pyw`: stable version-pointer launcher.
- Create `scripts/windows_overlay.py`: transactional install/status/uninstall.
- Create `tests/test_windows_overlay_support.py`.
- Create `tests/test_windows_overlay_win32.py`.
- Create `tests/test_windows_overlay_worker.py`.
- Create `tests/test_windows_overlay_ui.py`.
- Create `tests/test_windows_overlay_installer.py`.
- Modify `README.md`: Windows preview commands and verification boundary.
- Modify `PRD.md`: verified macOS position change only.

Every new function and method receives a concise function-level docstring.

### Task 1: Lift the macOS Default Position

**Files:**
- Modify: `scripts/quota_overlay_support.swift`
- Modify: `tests/quota_overlay_support_tests.swift`

- [ ] **Step 1: Write the failing test**

For a navigation frame `(106, 154, 36, 36)`, expect the default panel origin `(150, 250)`.

- [ ] **Step 2: Verify the red test**

```bash
swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift \
  -o /tmp/quota-overlay-support-tests
/tmp/quota-overlay-support-tests
```

Expected: non-zero exit with the old `(150, 154)` origin in the assertion.

- [ ] **Step 3: Implement the 96-point lift**

Update `defaultOverlayOrigin` to return `navigationFrame.minY + 96` while preserving the 8-point horizontal gap.

- [ ] **Step 4: Verify Swift behavior**

```bash
swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift \
  -o /tmp/quota-overlay-support-tests
/tmp/quota-overlay-support-tests
swiftc -warnings-as-errors \
  -module-cache-path /private/tmp/codex-quota-swift-cache \
  -o /tmp/codex-quota-overlay \
  scripts/quota_overlay_support.swift scripts/quota_overlay.swift
```

Expected: test prints `quota overlay support tests passed`; strict compile exits 0 with no output.

- [ ] **Step 5: Install and verify on the current Mac**

Install with `scripts/macos_overlay.py install`, temporarily set `customPosition=false`, inspect actual button/panel frames, and verify the panel's AppKit `minY` equals button `minY + 96`. Restore all preferences even on failure.

- [ ] **Step 6: Commit**

```bash
git add scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift
git commit -m "fix: lift default quota overlay position"
```

### Task 2: Implement Pure Windows Layout, Parsing and State

**Files:**
- Create: `scripts/windows_overlay_support.py`
- Create: `tests/test_windows_overlay_support.py`

- [ ] **Step 1: Write failing layout and hit tests**

Test:

- half-up scaling at 96/120/144 DPI;
- physical navigation and default panel rectangles;
- 96-unit lift and 8-unit gap;
- negative-coordinate work-area clamping;
- saved `relative_x` and `relative_bottom`;
- 260×270 ↔ 260×194 bottom preservation;
- top-edge temporary clamping without saved-anchor mutation;
- arrow/task/quota/no-op hit priority;
- 4-unit logical drag threshold.

- [ ] **Step 2: Write failing parser and state tests**

Use representative real-shaped quota and queue dictionaries plus malformed variants. Test:

- quota windows by official durations 300 and 10080;
- queue status filtering and five-title limit;
- title cap at 200 characters;
- wrong types and oversized collections rejected;
- strict boolean state fields;
- finite paired coordinates within ±100000;
- 16 KiB file limit;
- invalid state returns defaults without overwriting;
- failed atomic replace preserves the old file.

- [ ] **Step 3: Verify red tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_support.py -q
```

Expected: collection fails with `ModuleNotFoundError: scripts.windows_overlay_support`.

- [ ] **Step 4: Implement minimal pure logic**

Provide documented dataclasses and functions:

```python
Rect
OverlayState
QuotaSnapshot
QueueSnapshot
scale_length
navigation_rect
default_panel_rect
custom_panel_rect
capture_relative_anchor
clamp_rect
overlay_hit_target
exceeds_drag_threshold
parse_quota_payload
parse_queue_payload
load_state
save_state
```

- [ ] **Step 5: Verify green tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_support.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/windows_overlay_support.py tests/test_windows_overlay_support.py
git commit -m "feat: add Windows overlay support logic"
```

### Task 3: Implement the Lazy Win32 Adapter

**Files:**
- Create: `scripts/windows_overlay_win32.py`
- Create: `tests/test_windows_overlay_win32.py`

- [ ] **Step 1: Write failing selection tests**

With a fake native backend, test:

- `Codex.exe` accepted;
- `ChatGPT.exe` requires `Codex` in the title;
- background, hidden, minimized and undersized windows rejected;
- DWM extended bounds preferred, `GetWindowRect` fallback;
- monitor work area and `GetDpiForWindow` returned in physical pixels.

- [ ] **Step 2: Write failing window-style and kernel-object tests**

Test:

- existing extended styles are ORed with `WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`;
- `WS_EX_TRANSPARENT` is never added;
- show uses `SW_SHOWNOACTIVATE`;
- move/topmost uses `SWP_NOACTIVATE`;
- runtime mutex is initially owned;
- manual stop event operations;
- installer mutex ownership;
- process handle wait and liveness.

- [ ] **Step 3: Verify red tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_win32.py -q
```

Expected: import failure for `scripts.windows_overlay_win32`.

- [ ] **Step 4: Implement the adapter**

The module must import on macOS. `Win32Api()` raises `OSError` on non-Windows; no `ctypes.windll`, Tk root, registry or named object is touched at module import.

Expose documented methods for window inspection, DPI, styles, positioning, mutexes, events and process handles. Keep numeric Win32 constants in this module.

- [ ] **Step 5: Verify green tests and syntax**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_win32.py -q
.venv/bin/python -m py_compile scripts/windows_overlay_win32.py
```

Expected: all tests pass; compile exits 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/windows_overlay_win32.py tests/test_windows_overlay_win32.py
git commit -m "feat: add Windows overlay Win32 adapter"
```

### Task 4: Implement HTTP Worker and Instance Lifecycle

**Files:**
- Create: `scripts/windows_overlay_worker.py`
- Create: `tests/test_windows_overlay_worker.py`

- [ ] **Step 1: Write failing bounded-fetch tests**

Inject a fake opener and clock. Test:

- both APIs fetched serially with 3-second timeout;
- response reads at most 1 MiB + 1 byte;
- a local slow-drip HTTP server cannot extend connect plus complete read past a 3-second monotonic deadline;
- oversized and malformed JSON fail the affected snapshot only;
- one request per endpoint is in flight;
- consecutive failures preserve the last successful snapshot and mark stale/unavailable;
- `Queue(maxsize=1)` replaces the previous pending update.

- [ ] **Step 2: Write failing lifecycle tests**

Inject fake kernel/file adapters. Test:

- runtime mutex held for lifetime;
- already-running instance exits before creating UI;
- stop event ends polling and UI callback;
- worker join is bounded to 3.5 seconds;
- ready file includes exact PID/version/nonce;
- persistent instance file includes PID/process-creation-time/pythonw-path/version/nonce/time;
- exit deletes `instance.json` only when PID and creation time still match;
- PID reuse with a different creation time or executable path is rejected.

- [ ] **Step 3: Verify red tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_worker.py -q
```

Expected: import failure for `scripts.windows_overlay_worker`.

- [ ] **Step 4: Implement `BoundedApiWorker`**

Use `http.client` with a monotonic deadline. Before connect, response headers and each bounded read, set the socket timeout to the remaining deadline. Use one daemon thread, a stop event and `Queue(maxsize=1)`. Never call Tk from the worker.

- [ ] **Step 5: Implement `InstanceLifecycle`**

Own runtime mutex/event handles, write atomic ready/instance JSON, expose `should_stop()`, and close all handles in `finally`.

- [ ] **Step 6: Verify green tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_worker.py -q
.venv/bin/python -m py_compile scripts/windows_overlay_worker.py
```

Expected: all tests pass; compile exits 0.

- [ ] **Step 7: Commit**

```bash
git add scripts/windows_overlay_worker.py tests/test_windows_overlay_worker.py
git commit -m "feat: add Windows overlay worker lifecycle"
```

### Task 5: Implement Controller and Tkinter View

**Files:**
- Create: `scripts/windows_quota_overlay.py`
- Create: `tests/test_windows_overlay_ui.py`

- [ ] **Step 1: Write failing controller tests**

Use fake Win32, view, state store and browser adapters. Test:

- foreground Codex shows button; background/minimized hides both;
- navigation click toggles persisted intent without foreground loss;
- default and custom placement;
- movement/resize/DPI change recomputes geometry;
- arrow-only collapse preserves saved bottom anchor;
- quota/task clicks open correct URLs;
- blanks do nothing;
- sub-threshold jitter clicks; threshold drag saves logical anchor;
- edge clamping never rewrites anchor.

- [ ] **Step 2: Write failing view-contract tests**

Test renderer inputs and source contracts without creating a display:

- two view models use 36×36 and 260×270/194 logical sizes;
- Canvas geometry matches support hit geometry;
- all fonts use explicit negative pixel sizes;
- no `focus_force`, `focus_set` or `WS_EX_TRANSPARENT` occurrence;
- all show/move calls route through Win32 adapter methods.

- [ ] **Step 3: Verify red tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_ui.py -q
```

Expected: import failure for `scripts.windows_quota_overlay`.

- [ ] **Step 4: Implement `OverlayController`**

The controller contains no direct Tk or ctypes calls. It consumes adapters, polls at 100 ms, drains latest worker snapshots, computes state and emits view models.

- [ ] **Step 5: Implement lazy `TkOverlayView`**

Import Tkinter only when constructing the production view. Create hidden root plus two borderless `Toplevel` canvases, then pass root HWNDs to Win32 styling.

- [ ] **Step 6: Implement `main()`**

Enable DPI awareness before Tk construction, compose adapters, start lifecycle/worker/controller, and guarantee shutdown in `finally`.

Write candidate ready only after both HWNDs exist, non-activating styles are applied, worker/controller schedules are active, and one Tk idle/event cycle completes. Add a test where a structurally valid ready is followed by immediate process exit; installer verification must reject it during the 500 ms grace period.

For native rollback acceptance only, exit before ready when both `COCO_OVERLAY_TEST_MODE=1` and `COCO_OVERLAY_FAIL_BEFORE_READY=1` are present. The failure hook is ignored unless both values match exactly. Installer rollback must remove both variables from the old-version restart environment so fault injection cannot poison recovery.

- [ ] **Step 7: Verify green tests and syntax**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_ui.py -q
.venv/bin/python -m py_compile scripts/windows_quota_overlay.py
```

Expected: all tests pass; compile exits 0.

- [ ] **Step 8: Commit**

```bash
git add scripts/windows_quota_overlay.py tests/test_windows_overlay_ui.py
git commit -m "feat: add Windows quota overlay UI"
```

### Task 6: Implement Stable Launcher and Transactional Installer

**Files:**
- Create: `scripts/windows_overlay_launcher.pyw`
- Create: `scripts/windows_overlay.py`
- Create: `tests/test_windows_overlay_installer.py`

**Installed version layout:**

```text
%LOCALAPPDATA%\CocoCodexManager\
├── overlay-state.json
└── overlay\
    ├── overlay_launcher.pyw
    ├── current.json
    ├── instance.json
    └── versions\<version>\
        ├── windows_quota_overlay.py
        ├── windows_overlay_support.py
        ├── windows_overlay_win32.py
        └── windows_overlay_worker.py
```

- [ ] **Step 1: Write failing launcher integration tests**

From a temporary installed tree, execute the real launcher in a subprocess with a fake runtime entry point. Verify it:

- bounds and validates `current.json`;
- rejects traversal outside `versions`;
- inserts the selected version directory at `sys.path[0]`;
- sets and restores `sys.argv` and working directory;
- imports all four installed modules;
- ordinary launch stops while install lock is owned;
- nonce launch bypasses only the install-lock check.

The subprocess fixture imports all four real installed modules from the temporary version directory, proving companion imports work.

- [ ] **Step 2: Write failing transaction-state tests**

Define `<version>` as the first 16 hex characters of SHA-256 over fixed filenames plus file bytes. Reuse an existing matching directory, reject a same-name mismatch, and treat identical-source reinstall as an idempotent startup repair rather than a version change.

Represent install as phases:

```text
snapshot → stage → stop-old → switch-files → switch-registry
→ reset-stop → start-candidate → verify-ready → liveness-grace
→ commit → idempotent-cleanup
```

Inject failure after every mutating phase: version staging, launcher replace, pointer replace, HKCU Run write, candidate start and ready timeout.

Before the commit point, for every failure assert:

- candidate is stopped before rollback;
- late nonce ready is removed;
- old registry/launcher/pointer and pre-existing version set are exact;
- stop event is reset;
- old version restarts with a rollback nonce and writes a new valid instance/ready identity;
- first install leaves no code or startup artifacts;
- `overlay-state.json` is preserved.

The commit point is valid ready plus 500 ms continued process liveness. Version pruning happens only after commit and is idempotent; pruning failure does not roll back a running candidate.

- [ ] **Step 3: Write failing status/uninstall tests**

Cover PID + creation time + executable path identity, PID reuse, stale instance cleanup, only-self identity deletion, concurrent installer lock, ordinary launcher blocking, nonce bypass, uninstall exit wait and state preservation.

- [ ] **Step 4: Verify red tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_installer.py -q
```

Expected: imports fail because installer and launcher do not exist.

- [ ] **Step 5: Implement the launcher**

Use bounded JSON reads and `Path.resolve().is_relative_to(versions_root)`. Temporarily set `sys.path`, `sys.argv` and `cwd`, invoke `runpy.run_path(..., run_name="__main__")`, then restore process state in `finally`.

- [ ] **Step 6: Implement adapters and transaction**

Production adapters lazily import `winreg` and instantiate Win32 APIs. `Installer` accepts file, registry and process adapters for tests.

Snapshot static state before writes. Keep installer mutex for the full command. Follow the exact phase order and pre-commit rollback assertions from Step 2. Runtime instance metadata is reconstructed by the restarted old version, not byte-restored as static state.

- [ ] **Step 7: Implement CLI commands**

`install`, `status`, and `uninstall` emit concise JSON-compatible output. Non-Windows rejects before creating directories or touching registry. Installer-lock contention returns exit code 75; all other success paths return 0.

- [ ] **Step 8: Verify green tests and syntax**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_windows_overlay_installer.py -q
.venv/bin/python -m py_compile \
  scripts/windows_overlay.py scripts/windows_overlay_launcher.pyw
```

Expected: all tests pass; compile exits 0.

- [ ] **Step 9: Commit**

```bash
git add scripts/windows_overlay.py scripts/windows_overlay_launcher.pyw \
  tests/test_windows_overlay_installer.py
git commit -m "feat: add Windows overlay installer"
```

### Task 7: Documentation, Regression and Review

**Files:**
- Modify: `README.md`
- Modify after macOS verification: `PRD.md`

- [ ] **Step 1: Update README**

Document Windows 10/11 preview commands, current-user HKCU Run, no-admin behavior, Web service prerequisite and native-verification boundary.

- [ ] **Step 2: Run focused Windows automation**

```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_windows_overlay_support.py \
  tests/test_windows_overlay_win32.py \
  tests/test_windows_overlay_worker.py \
  tests/test_windows_overlay_ui.py \
  tests/test_windows_overlay_installer.py -q
```

Expected: exit 0; all new tests pass.

- [ ] **Step 3: Run full regression**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
npm run build --prefix frontend
git diff --check
```

Expected: pytest exit 0 or only repository-baseline failures proven against `ed6bbfb`; frontend reports a successful Vite build; diff check exits 0.

- [ ] **Step 4: Verify macOS native placement and dragging**

Install the current overlay, back up preferences, force default mode, activate Codex, and verify with actual window frames:

- panel X = button maxX + 8;
- panel Y = button minY + 96;
- real CGEvent drag sets a custom position;
- restart restores custom position;
- cleanup restores the original preferences.

Expected: all assertions pass and `overlay.err.log` remains empty.

- [ ] **Step 5: Update the main PRD**

Only after Step 4 passes, change the macOS default-position fact to “right 8, up 96”. Do not add Windows native behavior to the main PRD.

- [ ] **Step 6: Request final code review**

Review the complete diff against `docs/superpowers/specs/2026-10-19-windows-overlay-design.md`. Fix every Critical and Important issue.

- [ ] **Step 7: Commit documentation**

```bash
git add README.md PRD.md
git commit -m "docs: describe Windows overlay preview"
```

### Task 8: Windows 10/11 Native Acceptance Checklist

Run separately on Windows 10 and Windows 11 with the native Codex desktop app.

- [ ] **Step 1: Install and inspect startup**

```powershell
.\.venv\Scripts\python.exe scripts\windows_overlay.py install
.\.venv\Scripts\python.exe scripts\windows_overlay.py status
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
  -Name CocoCodexManagerOverlay
```

Expected: install exits 0 with no admin prompt; status reports `installed=true`, `running=true`, current version and live PID; Run value invokes `pythonw.exe` and `overlay_launcher.pyw`; no console remains visible.

- [ ] **Step 2: Verify foreground and focus**

Open Codex, then move, resize, maximize, restore and minimize it. Click the navigation button, fold arrow, blank regions and drag the panel.

Expected: button/panel follow within 200 ms; minimize/background hides both; non-URL interactions leave Codex as foreground.

- [ ] **Step 3: Verify position and DPI**

At 100%, 125% and 150% scaling, including mixed-DPI monitors:

- reset state to non-custom;
- verify 8 logical units right and 96 logical units up;
- move Codex between monitors;
- disconnect/reconnect the secondary display.

Expected: physical dimensions rescale once, hit regions align, temporary clamping keeps the panel visible and saved anchor is unchanged.

- [ ] **Step 4: Verify persistence and URLs**

Drag the panel, fold, restart Windows overlay, unfold, click both quota rings and a real task.

Expected: bottom anchor survives fold/restart/unfold; overview and queue URLs open in the default browser.

- [ ] **Step 5: Verify upgrade, rollback and uninstall**

```powershell
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$installer = (Resolve-Path .\scripts\windows_overlay.py).Path

# Exercise the installer lock with two concurrent commands.
$p1 = Start-Process $python -ArgumentList "`"$installer`" install" -PassThru
$p2 = Start-Process $python -ArgumentList "`"$installer`" install" -PassThru
$p1.WaitForExit(); $p2.WaitForExit()
$codes = @($p1.ExitCode, $p2.ExitCode)
if ($codes -notcontains 0 -or @($codes | Where-Object { $_ -notin @(0, 75) }).Count) {
  throw "unexpected concurrent installer exit codes: $codes"
}
$concurrent = & $python $installer status | ConvertFrom-Json
if (-not $concurrent.running) { throw 'overlay not running after concurrent install' }
$instances = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*overlay_launcher.pyw*' }
if (@($instances).Count -ne 1) { throw 'expected exactly one overlay instance' }

# Install an explicitly different v2 fixture.
$fixture = Join-Path $env:TEMP 'coco-overlay-v2'
Remove-Item $fixture -Recurse -Force -ErrorAction SilentlyContinue
New-Item $fixture -ItemType Directory | Out-Null
Copy-Item .\scripts\windows_overlay*.py* $fixture
Copy-Item .\scripts\windows_quota_overlay.py $fixture
Add-Content (Join-Path $fixture 'windows_quota_overlay.py') '# acceptance-v2'
& $python (Join-Path $fixture 'windows_overlay.py') install
if ($LASTEXITCODE -ne 0) { throw 'v2 install failed' }
$v2 = & $python $installer status | ConvertFrom-Json
if (-not $v2.running -or $v2.current_version -eq $concurrent.current_version) {
  throw 'v2 did not commit as a running version'
}

# Install a different v3 fixture that fails before ready and must roll back.
Add-Content (Join-Path $fixture 'windows_quota_overlay.py') '# acceptance-v3'
$env:COCO_OVERLAY_TEST_MODE = '1'
$env:COCO_OVERLAY_FAIL_BEFORE_READY = '1'
& $python (Join-Path $fixture 'windows_overlay.py') install
if ($LASTEXITCODE -eq 0) { throw 'failure injection unexpectedly succeeded' }
Remove-Item Env:COCO_OVERLAY_TEST_MODE, Env:COCO_OVERLAY_FAIL_BEFORE_READY
$rolledBack = & $python $installer status | ConvertFrom-Json
if (-not $rolledBack.running -or $rolledBack.current_version -ne $v2.current_version) {
  throw 'rollback did not restore the v2 runtime'
}

# Uninstall and inspect preserved state.
& $python $installer uninstall
$removed = & $python $installer status | ConvertFrom-Json
if ($removed.installed -or $removed.running) { throw 'uninstall incomplete' }
if (-not (Test-Path "$env:LOCALAPPDATA\CocoCodexManager\overlay-state.json")) {
  throw 'user state was not preserved'
}
```

Expected: script completes without `throw`; concurrent install leaves one instance; v2 changes the content-hash version; injected v3 failure returns non-zero and restores the live v2 version; uninstall removes HKCU Run/code and preserves `overlay-state.json`.

- [ ] **Step 6: Preserve the verification boundary**

Until all steps pass on both Windows versions, final reporting must say “implemented and automatically tested; native Windows acceptance pending,” and the main PRD must not claim Windows overlay support as verified.

- [ ] **Step 7: Sync verified Windows facts after both platforms pass**

Only after Steps 1-5 pass on both Windows 10 and Windows 11, update the main PRD with the Windows overlay, startup, follow, interaction, DPI and persistence facts; commit that PRD update in the native-validation task. If either platform is still pending, do not add Windows capability facts.
