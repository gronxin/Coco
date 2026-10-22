import sys
from pathlib import Path
import analyzer.paths as paths


def test_non_frozen_root_is_repo_root():
    assert paths.is_frozen() is False
    assert paths.resource_root() == Path(paths.__file__).resolve().parent.parent


def test_frozen_root_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.is_frozen() is True
    assert paths.resource_root() == tmp_path


def test_app_base_follows_resource_root():
    import analyzer.app as appmod
    assert appmod.BASE == paths.resource_root()
