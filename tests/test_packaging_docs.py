from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_has_macos_download_section():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 下载安装（macOS）" in text
    for needle in ("Coco.dmg", "拖进「应用程序」", "首次启动", "完全卸载"):
        assert needle in text
