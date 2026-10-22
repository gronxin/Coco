import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app_entry  # noqa: E402


def test_serve_calls_uvicorn_on_loopback(monkeypatch):
    calls = {}
    monkeypatch.setattr(app_entry, "create_app", lambda: object())
    monkeypatch.setattr(app_entry.uvicorn, "run",
                        lambda app, host, port: calls.update(host=host, port=port))
    app_entry.serve()
    assert calls == {"host": "127.0.0.1", "port": 8765}


def test_service_subcommand_dispatches(monkeypatch):
    seen = []
    monkeypatch.setattr(app_entry.macos_service, "install",
                        lambda **kw: seen.append(("install", kw)))
    assert app_entry.main(["service", "install", "--backend", "/x/backend"]) == 0
    assert seen == [("install", {"backend_source": "/x/backend"})]


def test_overlay_install_passes_binary(monkeypatch):
    seen = []
    monkeypatch.setattr(app_entry.macos_overlay, "install",
                        lambda binary_source=None: seen.append(binary_source))
    assert app_entry.main(["overlay", "install", "--binary", "/r/ov"]) == 0
    assert seen == ["/r/ov"]


def test_unknown_command_is_usage_error():
    import pytest
    with pytest.raises(SystemExit):
        app_entry.main(["nonsense"])
