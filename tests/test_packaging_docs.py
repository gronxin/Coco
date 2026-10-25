from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_has_macos_download_section():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 下载安装（macOS）" in text
    for needle in ("Coco.dmg", "拖进「应用程序」", "首次启动", "完全卸载"):
        assert needle in text


def test_readme_has_no_placeholders_or_signing_creds():
    """The public README is user-facing: no clone placeholders, no maintainer
    signing/notarization credentials or profile names."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for forbidden in (
        "<你的仓库地址>",
        "COCO_SIGN_IDENTITY",
        "NOTARY_PROFILE",
        "notarytool",
        "CocoNotary",
    ):
        assert forbidden not in text, f"README should not contain {forbidden!r}"
    assert "https://github.com/gronxin/codex-token-analyzer.git" in text
