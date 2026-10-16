# Coco Codex管家 Rename Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace every current user-facing “Codex Token 账本” product identity with “Coco Codex管家” while preserving all installed-service, storage, package, and JSON-format identifiers.

**Architecture:** Keep the change at presentation boundaries: Web branding, FastAPI metadata, export labels and filenames, queue-facing titles, and current product documents. Add regression assertions around those surfaces and around the internal identifiers that must remain stable; do not migrate data or rename packages.

**Tech Stack:** Python 3.11+, FastAPI, pytest, React 19, TypeScript, Vite, macOS LaunchAgent, Playwright browser verification.

---

## File Map

- Modify `tests/test_api.py`: lock OpenAPI branding, export titles, filenames, and unchanged JSON schemas.
- Modify `analyzer/app.py`: update FastAPI title, export filenames, and Markdown report heading.
- Modify `tests/test_night_queue.py`: lock queue-facing names and unchanged app-server service identifier.
- Modify `analyzer/night_queue.py`: centralize and apply queue-facing branding constants.
- Create `tests/test_branding.py`: lock Web source branding, current documents, old-name exclusions, and stable internal identifiers.
- Modify `frontend/src/main.tsx`: render `Coco` / `Codex管家` in the existing two-line brand.
- Modify `frontend/index.html`: update the browser title.
- Modify `README.md`: update the current product heading.
- Modify `PRD.md`: synchronize the verified current product name.
- Modify `LICENSE`: update the public contributor identity.

### Task 1: API Metadata and Export Contracts

**Files:**
- Modify: `tests/test_api.py`
- Modify: `analyzer/app.py`

- [x] **Step 1: Write failing API assertions**

Add exact assertions:

```python
assert client.get('/openapi.json').json()['info']['title'] == 'Coco Codex管家'
```

For `/api/export.snapshot.json`, assert:

```python
assert response.headers['content-disposition'] == (
    'attachment; filename="coco-codex-manager-snapshot.json"'
)
assert set(payload) == {
    'format', 'version', 'generated_at', 'timezone', 'filters',
    'metrics', 'daily', 'by_model', 'by_project',
}
assert (payload['format'], payload['version']) == ('codex-token-snapshot', 1)
```

For report JSON and Markdown, assert:

```python
assert set(payload) == {
    'format', 'version', 'generated_at', 'timezone', 'filters', 'metrics',
    'weekly_comparison', 'anomalies', 'model_comparison', 'top_tasks', 'data_notes',
}
assert (payload['format'], payload['version']) == ('codex-token-weekly-report', 1)
assert report.headers['content-disposition'] == (
    'attachment; filename="coco-codex-manager-report.json"'
)
assert markdown.headers['content-disposition'] == (
    'attachment; filename="coco-codex-manager-report.md"'
)
assert markdown.text.startswith('# Coco Codex管家用量报告\n')
```

- [x] **Step 2: Run focused tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_api.py \
  -k 'tools_budgets_snapshot or new_analysis_dimensions_and_reports or endpoints_and_csv_agree' -q
```

Expected: FAIL on the old OpenAPI title, old download filenames, and old Markdown heading.

- [x] **Step 3: Apply the minimal backend rename**

In `analyzer/app.py`:

- set the FastAPI title to `Coco Codex管家`;
- use the three `coco-codex-manager-*` download filenames;
- use `# Coco Codex管家用量报告`;
- leave `codex-token-snapshot`, `codex-token-weekly-report`, version numbers, and payload keys unchanged.

- [x] **Step 4: Run focused API tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add analyzer/app.py tests/test_api.py
git commit -m "feat: rename public API branding"
```

### Task 2: Queue-Facing Branding and Stable Service Identifier

**Files:**
- Modify: `tests/test_night_queue.py`
- Modify: `analyzer/night_queue.py`

- [x] **Step 1: Write failing queue branding assertions**

Add a test using `getattr` so it fails before constants exist:

```python
def test_queue_branding_preserves_machine_identifiers():
    import analyzer.night_queue as module
    assert getattr(module, 'QUEUE_CLIENT_INFO', None) == {
        'name': 'codex-token-analyzer-queue',
        'title': 'Coco Codex管家任务队列',
        'version': '1.0',
    }
    assert getattr(module, 'QUEUE_SERVICE_NAME', None) == 'codex_token_analyzer_queue'
    assert 'Coco Codex管家' in getattr(module, 'DESKTOP_BRIDGE_INSTRUCTION', '')
```

Add a protocol-level test that monkeypatches `subprocess.Popen`,
`_JsonLineReader`, and `_send`, calls `_start_server()`, and captures the
initialize request:

```python
assert sent[0] == {
    'id': 1,
    'method': 'initialize',
    'params': {'clientInfo': {
        'name': 'codex-token-analyzer-queue',
        'title': 'Coco Codex管家任务队列',
        'version': '1.0',
    }},
}
```

Extend an existing fresh-thread runner test, or add a focused runner test, to assert:

```python
assert thread_start['params']['serviceName'] == 'codex_token_analyzer_queue'
```

- [x] **Step 2: Run the focused test and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_night_queue.py \
  -k 'queue_branding_preserves_machine_identifiers or service_name' -q
```

Expected: FAIL because the branding constants are not defined.

- [x] **Step 3: Centralize and apply queue branding**

Add module constants:

```python
QUEUE_CLIENT_INFO = {
    'name': 'codex-token-analyzer-queue',
    'title': 'Coco Codex管家任务队列',
    'version': '1.0',
}
QUEUE_SERVICE_NAME = 'codex_token_analyzer_queue'
DESKTOP_BRIDGE_INSTRUCTION = (
    '用户已授权原对话队列自动派发。现在处理本机 Coco Codex管家的桌面桥接：'
    # Existing operational instructions remain byte-for-byte otherwise.
)
```

Use them in `_start_server`, `AppServerRunner.run`, and `NightQueue._notify_bridge_pending`. Do not change protocol behavior.

- [x] **Step 4: Run focused queue tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add analyzer/night_queue.py tests/test_night_queue.py
git commit -m "feat: rename queue-facing product title"
```

### Task 3: Web Brand and Current Product Documents

**Files:**
- Create: `tests/test_branding.py`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/index.html`
- Modify: `README.md`
- Modify: `PRD.md`
- Modify: `LICENSE`

- [x] **Step 1: Write the failing static branding contract**

Create `tests/test_branding.py` with repository-root reads that assert:

```python
assert '<title>Coco Codex管家</title>' in index_html
assert '<div>Coco<span>Codex管家</span></div>' in main_source
assert readme.startswith('# Coco Codex管家\n')
assert prd.startswith('# Coco Codex管家主 PRD\n')
assert 'Coco Codex管家是一个本机运行的非官方 Codex 用量分析工具。' in prd
assert 'Copyright (c) 2026 Coco Codex管家 contributors' in license_text
```

Scan only current product surfaces:

```python
forbidden = (
    'Codex Token 账本',
    'Codex Token 本地账本',
    'Codex Token 用量报告',
    'Codex Token 任务队列',
    'Codex Token 分析器',
    'Codex Token Analyzer contributors',
)
```

Assert none appears in `README.md`, `PRD.md`, `LICENSE`, `analyzer/app.py`,
`analyzer/night_queue.py`, `frontend/index.html`, or `frontend/src/main.tsx`.
Also lock every approved internal identifier to its owning file:

```python
stable_identifiers = {
    'pyproject.toml': ('name = "codex-token-analyzer"',),
    'frontend/package.json': ('"name":"codex-token-analyzer-ui"',),
    'frontend/package-lock.json': ('"name": "codex-token-analyzer-ui"',),
    'scripts/macos_service.py': (
        "local.codex-token-analyzer.service",
        "Application Support' / 'CodexTokenAnalyzer",
        "'CODEX_ANALYZER_MANAGED'",
        "'TOKEN_ANALYZER_DB'",
    ),
    'scripts/macos_overlay.py': (
        "local.codex-token-analyzer.overlay",
        "Application Support' / 'CodexTokenAnalyzer",
    ),
    'analyzer/app.py': (
        "'TOKEN_ANALYZER_SOURCE'",
        "'TOKEN_ANALYZER_DB'",
        "'TOKEN_ANALYZER_PRIVACY_MODE'",
        "'CODEX_ANALYZER_MANAGED'",
        "BASE/'.data'/'analytics.sqlite'",
        "'codex-token-snapshot'",
        "'codex-token-weekly-report'",
    ),
    'analyzer/night_queue.py': (
        "'codex-token-analyzer-queue'",
        "'codex_token_analyzer_queue'",
    ),
    'analyzer/quota.py': ("'codex-token-analyzer'",),
    'frontend/src/main.tsx': (
        "'codex-token-analyzer-theme'",
        "'task-columns'",
    ),
}
```

Iterate through this mapping and assert every literal remains present.

- [x] **Step 2: Run the branding test and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_branding.py -q
```

Expected: FAIL on Web and document names.

- [x] **Step 3: Update Web and current documents**

- Render `Coco<span>Codex管家</span>` in the existing brand component.
- Set the HTML title to `Coco Codex管家`.
- Replace the README and PRD product headings and product-positioning self-name.
- Replace the LICENSE contributor identity.
- Do not edit historical files under `docs/superpowers/**`, `docs/implementation-plan.md`, or `docs/task-queue-design.md`.

- [x] **Step 4: Run static branding tests and frontend build**

Run:

```bash
.venv/bin/python -m pytest tests/test_branding.py -q
cd frontend && npm run build
```

Expected: pytest PASS and Vite build completes.

- [x] **Step 5: Commit**

```bash
git add tests/test_branding.py frontend/src/main.tsx frontend/index.html README.md PRD.md LICENSE
git commit -m "feat: rename product to Coco Codex manager"
```

### Task 4: Full Verification and Running macOS Service

**Files:**
- Verify only; no planned source edits.

- [x] **Step 0: Preserve the known test baseline**

Before implementation, the repository baseline is `98 passed, 4 failed`. The
known unrelated failures are:

```text
tests/test_night_queue.py::test_manual_retry_requires_unchanged_followup_target
tests/test_night_queue.py::test_discovery_failure_blocks_new_dispatch_and_reports_error
tests/test_night_queue.py::test_old_reserve_and_auto_close_config_is_removed_on_restart
tests/test_night_queue.py::test_exhausted_task_waits_even_when_reset_time_is_unknown
```

This rename does not change those behaviors. Final verification must show every
new or modified branding test passing and no failures beyond these four.

- [x] **Step 1: Run full automated verification**

Run:

```bash
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

Expected: Vite build succeeds; pytest reports `98 + newly added tests` passing
and only the same four named baseline failures. Any additional failure is a
regression and must be fixed before proceeding.

- [x] **Step 2: Scan old public names and stable internal identifiers**

Run `tests/test_branding.py` plus focused `rg` checks over current product files. Expected:

- no forbidden old product self-name remains;
- every stable identifier listed in the Task 3 mapping remains in its exact owning file.

- [x] **Step 3: Sync and restart the installed macOS service**

Read the service state and capture the current process identity:

```bash
.venv/bin/python scripts/macos_service.py status
curl -fsS http://127.0.0.1:8765/api/service/status
```

If `managed` is true, save `instance_id`, then run:

```bash
.venv/bin/python scripts/macos_service.py sync
curl -fsS -X POST http://127.0.0.1:8765/api/service/restart
```

Require the restart response to use HTTP 202. Poll `/api/service/status` until it
returns HTTP 200 with an `instance_id` different from the saved value, then assert:

```bash
curl -fsS http://127.0.0.1:8765/openapi.json
```

Expected: `info.title` is `Coco Codex管家`.

If restart returns 409 because the queue is busy, do not terminate the task or
force-restart the service. Report that condition and start an isolated full app
with background synchronization and queue dispatch disabled:

```bash
ISOLATED_ROOT="$(mktemp -d)"
mkdir -p "$ISOLATED_ROOT/source/sessions"
TOKEN_ANALYZER_SOURCE="$ISOLATED_ROOT/source" \
TOKEN_ANALYZER_DB="$ISOLATED_ROOT/analytics.sqlite" \
.venv/bin/python -c \
  "import uvicorn; from analyzer.app import create_app; uvicorn.run(create_app(background=False), host='127.0.0.1', port=8766)"
```

Use `http://127.0.0.1:8766` for browser verification. Use the same isolated app
when no managed service is installed. Keep the process running through Step 4,
then stop it cleanly and remove `ISOLATED_ROOT`.

- [x] **Step 4: Verify the real Web UI with `@webapp-testing`**

Open the Step 3 URL with Playwright and attach listeners for `pageerror` plus
console messages whose type is `error`. At both `1440 × 900` and `390 × 844`:

1. Navigate to the URL and wait until the network is idle.
2. Assert `document.title === "Coco Codex管家"`.
3. Assert `.brand > div` has direct text `Coco` and its child `span` has exact
   text `Codex管家`.
4. Read rectangles for `.brand-icon`, `.brand > div`, and `.sidebar nav`.
   Assert the icon and text rectangles do not intersect, and the brand bottom is
   above the navigation top.
5. Assert the brand text element has positive width/height and
   `scrollWidth <= clientWidth`.
6. Assert the page has no `pageerror` or console error entries.
7. Capture a screenshot for visual confirmation; keep it outside the repository.

Expected URL: `http://127.0.0.1:8765` for the restarted managed service, otherwise
`http://127.0.0.1:8766` for the isolated full application.

- [x] **Step 5: Check final Git state**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: implementation files are committed; only unrelated pre-existing user changes, if any, remain.
