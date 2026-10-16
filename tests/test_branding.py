from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(relative_path: str) -> str:
    """Read a UTF-8 text file relative to the repository root."""
    return (ROOT / relative_path).read_text(encoding='utf-8')


def test_current_product_branding():
    """Lock the current public product name across Web and product documents."""
    index_html = read_repo_file('frontend/index.html')
    main_source = read_repo_file('frontend/src/main.tsx')
    readme = read_repo_file('README.md')
    prd = read_repo_file('PRD.md')
    license_text = read_repo_file('LICENSE')
    app_source = read_repo_file('analyzer/app.py')
    prompt_history_source = read_repo_file('analyzer/prompt_history.py')
    i18n_source = read_repo_file('frontend/src/i18n.ts')

    assert '<title>Coco Codex管家</title>' in index_html
    assert '<div>Coco<span>Codex管家</span></div>' in main_source
    assert "QUEUE_ATTACHMENT_MARKER = '\\n\\n[Coco Codex管家附件]'" in prompt_history_source
    assert 'QUEUE_ATTACHMENT_MARKER.strip()' in app_source
    assert "document.title = 'Coco Codex管家';" in i18n_source
    assert readme.startswith('# Coco Codex管家\n')
    assert prd.startswith('# Coco Codex管家主 PRD\n')
    assert 'Coco Codex管家是一个本机运行的非官方 Codex 用量分析工具。' in prd
    assert 'Copyright (c) 2026 Coco Codex管家 contributors' in license_text


def test_current_product_surfaces_exclude_old_branding():
    """Reject every obsolete product self-name on current product surfaces."""
    forbidden = (
        'Codex Token 账本',
        'Codex Token 本地账本',
        'Codex Token 用量报告',
        'Codex Token 任务队列',
        'Codex Token 分析器',
        'Codex Token Analyzer contributors',
    )
    current_product_files = (
        'README.md',
        'PRD.md',
        'LICENSE',
        'analyzer/app.py',
        'analyzer/night_queue.py',
        'analyzer/prompt_history.py',
        'frontend/index.html',
        'frontend/src/i18n.ts',
        'frontend/src/main.tsx',
    )

    for relative_path in current_product_files:
        contents = read_repo_file(relative_path)
        for old_name in forbidden:
            assert old_name not in contents, f'{old_name!r} remains in {relative_path}'


def test_internal_identifiers_remain_stable():
    """Keep package, service, storage, protocol, and browser identifiers stable."""
    stable_identifiers = {
        'pyproject.toml': ('name = "codex-token-analyzer"',),
        'frontend/package.json': ('"name":"codex-token-analyzer-ui"',),
        'frontend/package-lock.json': ('"name": "codex-token-analyzer-ui"',),
        'scripts/macos_service.py': (
            'local.codex-token-analyzer.service',
            "Application Support' / 'CodexTokenAnalyzer",
            "'CODEX_ANALYZER_MANAGED'",
            "'TOKEN_ANALYZER_DB'",
        ),
        'scripts/macos_overlay.py': (
            'local.codex-token-analyzer.overlay',
            "Application Support' / 'CodexTokenAnalyzer",
        ),
        'scripts/quota_overlay.swift': (
            '"local.codex-token-analyzer.overlay"',
            '"overlayVisible"',
            '"queueCollapsed"',
            '"customPosition"',
            '"panelRelativeX"',
            '"panelRelativeY"',
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

    for relative_path, identifiers in stable_identifiers.items():
        contents = read_repo_file(relative_path)
        for identifier in identifiers:
            assert identifier in contents, f'{identifier!r} changed in {relative_path}'
