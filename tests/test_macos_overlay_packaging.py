import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import macos_overlay
import macos_overlay_support as sup


def test_resolve_prebuilt(tmp_path):
    b = tmp_path / "ov"; b.write_text("bin")
    assert sup.resolve_binary_source(str(b), tmp_path / "compiled") == ("prebuilt", b)


def test_resolve_falls_back_to_compile(tmp_path):
    kind, path = sup.resolve_binary_source(None, tmp_path / "compiled")
    assert kind == "compile"
    assert path == tmp_path / "compiled"


def test_install_copies_prebuilt(monkeypatch, tmp_path):
    src = tmp_path / "ov"; src.write_text("bin")
    monkeypatch.setattr(macos_overlay, "RUNTIME", tmp_path / "rt")
    monkeypatch.setattr(macos_overlay, "BINARY", tmp_path / "rt" / "codex-quota-overlay")
    monkeypatch.setattr(macos_overlay, "PLIST", tmp_path / "o.plist")
    monkeypatch.setattr(macos_overlay, "loaded", lambda: False)
    monkeypatch.setattr(macos_overlay.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(macos_overlay, "launchctl_uid", lambda: 501)
    macos_overlay.install(binary_source=str(src))
    assert (tmp_path / "rt" / "codex-quota-overlay").read_text() == "bin"


def test_install_source_missing_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_overlay, "RUNTIME", tmp_path)
    with pytest.raises(RuntimeError):
        macos_overlay.install(binary_source=str(tmp_path / "nope"))
