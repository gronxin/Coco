import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import macos_service
import macos_service_support as sup


def test_path_env_covers_codex_locations(tmp_path):
    env = sup.build_path_env(tmp_path)
    for part in ("/opt/homebrew/bin", "/usr/local/bin",
                 str(tmp_path / ".npm-global" / "bin"), "/usr/bin", "/bin"):
        assert part in env.split(":")


def test_classify_conflict():
    assert sup.classify_conflict(False, False, False) == "ok"
    assert sup.classify_conflict(True, False, False) == "stale_plist"
    assert sup.classify_conflict(True, True, True) == "replace_loaded"
    assert sup.classify_conflict(False, False, True) == "foreign_port"


def test_packaged_sync_copies_backend_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_service, "RUNTIME", tmp_path / "rt")
    monkeypatch.setattr(macos_service, "BACKEND_DIR", tmp_path / "rt" / "backend")
    src = tmp_path / "CocoBackend"
    (src / "_internal").mkdir(parents=True)
    (src / "CocoBackend").write_text("bin")
    (src / "_internal" / "prices.json").write_text("[]")
    macos_service.sync(backend_source=str(src))
    assert (tmp_path / "rt" / "backend" / "CocoBackend").read_text() == "bin"
    assert (tmp_path / "rt" / "backend" / "_internal" / "prices.json").read_text() == "[]"


def test_packaged_install_points_plist_at_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_service, "RUNTIME", tmp_path / "rt")
    monkeypatch.setattr(macos_service, "BACKEND_DIR", tmp_path / "rt" / "backend")
    monkeypatch.setattr(macos_service, "PLIST", tmp_path / "x.plist")
    monkeypatch.setattr(macos_service, "sync", lambda **kw: None)
    monkeypatch.setattr(macos_service, "_is_loaded", lambda: False)
    monkeypatch.setattr(macos_service, "_port_is_open", lambda: False)
    monkeypatch.setattr(macos_service.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(macos_service, "launchctl_uid", lambda: 501)
    macos_service.install(backend_source=str(tmp_path / "src"))
    import plistlib
    payload = plistlib.loads((tmp_path / "x.plist").read_bytes())
    assert payload["ProgramArguments"][0].startswith(str(tmp_path / "rt" / "backend"))
    assert payload["ProgramArguments"][-1] == "serve"


def test_install_refuses_foreign_port(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_service, "PLIST", tmp_path / "x.plist")
    monkeypatch.setattr(macos_service, "_is_loaded", lambda: False)
    monkeypatch.setattr(macos_service, "_port_is_open", lambda: True)
    with pytest.raises(RuntimeError):
        macos_service.install(backend_source=str(tmp_path / "src"))


def test_sync_preserves_existing_database(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_service, "RUNTIME", tmp_path / "rt")
    monkeypatch.setattr(macos_service, "DATA", tmp_path / "rt" / "data")
    monkeypatch.setattr(macos_service, "BACKEND_DIR", tmp_path / "rt" / "backend")
    (tmp_path / "rt" / "data").mkdir(parents=True)
    db = tmp_path / "rt" / "data" / "analytics.sqlite"
    db.write_text("keep-me")
    src = tmp_path / "src"; src.mkdir(); (src / "CocoBackend").write_text("b")
    macos_service.sync(backend_source=str(src))
    assert db.read_text() == "keep-me"
