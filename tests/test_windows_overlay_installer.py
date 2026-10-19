import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.windows_overlay import (
    InstallLockError,
    Installer,
    LocalFiles,
    Processes,
    Registry,
    RUNTIME_FILES,
    compute_content_version,
    main,
)


PYTHONW = r"C:\Python311\pythonw.exe"


class FakeMutex:
    """Represent one installer mutex acquisition."""

    def __init__(self, owned=True):
        """Store whether this caller owns the fake mutex."""
        self.owned = owned
        self.closed = False

    def close(self):
        """Record mutex release."""
        self.closed = True

    def __enter__(self):
        """Return this mutex from a context manager."""
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        """Release the fake mutex at context-manager exit."""
        self.close()


class FakeProcessHandle:
    """Expose a controllable process identity and liveness."""

    def __init__(self, pid, creation_time, executable_path):
        """Initialize a live fake process handle."""
        self.pid = pid
        self.creation_time = creation_time
        self.executable_path = executable_path
        self.alive = True
        self.closed = False
        self.waits = []

    def is_alive(self):
        """Return the configured liveness state."""
        return self.alive

    def wait(self, timeout):
        """Record a bounded wait and return whether the process exited."""
        self.waits.append(timeout)
        return not self.alive

    def close(self):
        """Record handle closure without changing process liveness."""
        self.closed = True

    def __enter__(self):
        """Return this handle from a context manager."""
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        """Close this fake process handle."""
        self.close()


class FakeStopEvent:
    """Model the shared manual-reset stop event."""

    def __init__(self, owner):
        """Store the process adapter that owns running fakes."""
        self.owner = owner
        self.signaled = False
        self.closed = False

    def set(self):
        """Signal every currently live process to exit."""
        self.owner.events.append("stop-set")
        self.signaled = True
        if self.owner.stop_wait_succeeds:
            for handle in self.owner.handles.values():
                handle.alive = False

    def reset(self):
        """Reset the shared stop event."""
        self.owner.events.append("stop-reset")
        self.signaled = False

    def close(self):
        """Record event handle closure."""
        self.closed = True

    def __enter__(self):
        """Return this event from a context manager."""
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        """Close this fake event handle."""
        self.close()


class FakeRegistry:
    """Store one current-user Run value without touching winreg."""

    def __init__(self, value=None):
        """Initialize the fake startup value."""
        self.value = value
        self.calls = []

    def get_run_value(self):
        """Return the current fake startup command."""
        self.calls.append(("get", self.value))
        return self.value

    def set_run_value(self, value):
        """Replace the fake startup command."""
        self.calls.append(("set", value))
        self.value = value

    def delete_run_value(self):
        """Delete the fake startup command."""
        self.calls.append(("delete",))
        self.value = None


class FakeProcesses:
    """Provide mutex, event, clock and process operations to the installer."""

    def __init__(self, install_root, pythonw=PYTHONW):
        """Initialize deterministic process state for one install root."""
        self.install_root = Path(install_root)
        self.pythonw = pythonw
        self.lock_owned = True
        self.handles = {}
        self.starts = []
        self.events = []
        self.now = 0.0
        self.next_pid = 1000
        self.suppress_candidate_ready = False
        self.kill_candidate_during_grace = False
        self.fail_start = False
        self.stop_wait_succeeds = True

    @property
    def overlay_root(self):
        """Return the fake installed overlay directory."""
        return self.install_root / "overlay"

    def installer_mutex(self):
        """Return a fake installer mutex."""
        self.events.append("lock")
        return FakeMutex(self.lock_owned)

    def stop_event(self):
        """Return the shared fake stop event."""
        event = FakeStopEvent(self)
        self.events.append("event-open")
        return event

    def monotonic(self):
        """Return the deterministic fake monotonic time."""
        return self.now

    def sleep(self, seconds):
        """Advance fake time and optionally kill the candidate in its grace period."""
        self.now += seconds
        if self.kill_candidate_during_grace and seconds >= 0.5 and self.starts:
            latest = self.starts[-1]
            if not latest["rollback"]:
                latest["handle"].alive = False

    def open_process(self, pid):
        """Open a known fake process or raise for a stale PID."""
        handle = self.handles.get(pid)
        if handle is None:
            raise OSError("stale process")
        return handle

    def start(self, pythonw, launcher, nonce, rollback=False):
        """Start a fake runtime and write its instance and ready identities."""
        if self.fail_start and not rollback:
            raise OSError("candidate start failed")
        assert str(pythonw) == self.pythonw
        assert Path(launcher) == self.overlay_root / "overlay_launcher.pyw"
        pointer = json.loads((self.overlay_root / "current.json").read_text())
        version = pointer["version"]
        self.next_pid += 1
        handle = FakeProcessHandle(
            self.next_pid,
            50_000 + self.next_pid,
            self.pythonw,
        )
        self.handles[handle.pid] = handle
        start = {
            "pid": handle.pid,
            "version": version,
            "nonce": nonce,
            "rollback": rollback,
            "handle": handle,
        }
        self.starts.append(start)
        self.events.append("rollback-start" if rollback else "candidate-start")
        instance = {
            "pid": handle.pid,
            "process_creation_time": handle.creation_time,
            "pythonw_path": self.pythonw,
            "version": version,
            "nonce": nonce,
            "time": 1_700_000_000.0,
        }
        self.overlay_root.mkdir(parents=True, exist_ok=True)
        (self.overlay_root / "instance.json").write_text(json.dumps(instance))
        if rollback or not self.suppress_candidate_ready:
            ready = {
                "pid": handle.pid,
                "version": version,
                "nonce": nonce,
            }
            (self.overlay_root / f"ready-{nonce}.json").write_text(json.dumps(ready))
        return handle

    def seed_running(self, version, nonce="old-nonce"):
        """Create one live pre-existing runtime identity."""
        self.next_pid += 1
        handle = FakeProcessHandle(
            self.next_pid,
            50_000 + self.next_pid,
            self.pythonw,
        )
        self.handles[handle.pid] = handle
        instance = {
            "pid": handle.pid,
            "process_creation_time": handle.creation_time,
            "pythonw_path": self.pythonw,
            "version": version,
            "nonce": nonce,
            "time": 1_700_000_000.0,
        }
        self.overlay_root.mkdir(parents=True, exist_ok=True)
        (self.overlay_root / "instance.json").write_text(json.dumps(instance))
        return handle


class FailureAt:
    """Raise immediately after one named transaction phase."""

    def __init__(self, phase):
        """Store the phase that should fail."""
        self.phase = phase
        self.seen = []

    def __call__(self, phase):
        """Record each phase and fail at the configured boundary."""
        self.seen.append(phase)
        if phase == self.phase:
            raise RuntimeError(f"injected {phase}")


class ReplacingFiles(LocalFiles):
    """Replace stale instance bytes immediately before guarded deletion."""

    def __init__(self, replacement):
        """Store bytes representing a newer runtime identity."""
        self.replacement = replacement
        self.replaced = False

    def remove_if_unchanged(self, path, expected):
        """Simulate a newer instance winning the identity-file race."""
        if Path(path).name == "instance.json" and not self.replaced:
            Path(path).write_bytes(self.replacement)
            self.replaced = True
        return super().remove_if_unchanged(path, expected)


class FailingReadyCleanupFiles(LocalFiles):
    """Fail one post-commit ready-marker deletion."""

    def __init__(self):
        """Initialize a one-shot cleanup failure."""
        self.failed = False

    def remove_file(self, path):
        """Raise once for a ready marker and delegate all other removals."""
        if Path(path).name.startswith("ready-") and not self.failed:
            self.failed = True
            raise OSError("ready cleanup failed")
        return super().remove_file(path)


class LockedInstaller:
    """Raise installer contention from every CLI operation."""

    def install(self):
        """Reject a locked install command."""
        raise InstallLockError("busy")

    def status(self):
        """Reject a locked status command."""
        raise InstallLockError("busy")

    def uninstall(self):
        """Reject a locked uninstall command."""
        raise InstallLockError("busy")


class RecordingPopen:
    """Record production process-launch arguments and emergency cleanup."""

    calls = []
    instances = []

    def __init__(self, arguments, **options):
        """Capture one subprocess request with a deterministic PID."""
        self.arguments = arguments
        self.options = options
        self.pid = 4242
        self.terminated = False
        self.waits = []
        type(self).calls.append((arguments, options))
        type(self).instances.append(self)

    def poll(self):
        """Return None until emergency termination is requested."""
        return 1 if self.terminated else None

    def terminate(self):
        """Record termination of this known child process."""
        self.terminated = True

    def wait(self, timeout):
        """Record the bounded emergency wait."""
        self.waits.append(timeout)
        return 1


class FailingOpenProcesses(Processes):
    """Fail identity-handle acquisition after subprocess creation."""

    def open_process(self, pid):
        """Reject the newly created process identity handle."""
        raise OSError(f"cannot open {pid}")


def write_source(root, marker):
    """Write one complete source runtime with deterministic content."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:
        (root / name).write_bytes(f"{name}:{marker}\n".encode())
    launcher = root / "windows_overlay_launcher.pyw"
    launcher.write_bytes(f"launcher:{marker}\n".encode())
    return root


def expected_version(source):
    """Compute the specified filename-plus-content digest independently."""
    digest = hashlib.sha256()
    for name in RUNTIME_FILES:
        digest.update(name.encode("utf-8"))
        digest.update((Path(source) / name).read_bytes())
    return digest.hexdigest()[:16]


def seed_installed_tree(root, source, registry, processes):
    """Create exact old static state plus one verified live old process."""
    root = Path(root)
    overlay = root / "overlay"
    version = compute_content_version(source)
    target = overlay / "versions" / version
    target.mkdir(parents=True)
    for name in RUNTIME_FILES:
        (target / name).write_bytes((Path(source) / name).read_bytes())
    launcher_bytes = b"old stable launcher bytes\n"
    pointer_bytes = json.dumps({"version": version}, indent=2).encode() + b"\n"
    (overlay / "overlay_launcher.pyw").write_bytes(launcher_bytes)
    (overlay / "current.json").write_bytes(pointer_bytes)
    registry.value = '"C:\\old\\pythonw.exe" "C:\\old\\overlay_launcher.pyw"'
    processes.seed_running(version)
    return {
        "version": version,
        "launcher": launcher_bytes,
        "pointer": pointer_bytes,
        "registry": registry.value,
        "versions": {version},
    }


def make_installer(root, source, registry=None, processes=None, files=None, fault=None):
    """Construct an installer using only fake Windows dependencies."""
    registry = registry or FakeRegistry()
    processes = processes or FakeProcesses(root)
    installer = Installer(
        install_root=root,
        source_root=source,
        pythonw_path=PYTHONW,
        files=files or LocalFiles(),
        registry=registry,
        processes=processes,
        fault=fault,
    )
    return installer, registry, processes


def run_launcher_harness(tmp_path, pointer, args=None, lock_available=True):
    """Execute the real launcher in a child process and return its observations."""
    install_root = tmp_path / "installed" / "overlay"
    versions = install_root / "versions"
    version = "0123456789abcdef"
    selected = versions / version
    selected.mkdir(parents=True)
    probe = tmp_path / "runtime-probe.json"
    result_path = tmp_path / "harness-result.json"
    launcher = Path(__file__).parents[1] / "scripts" / "windows_overlay_launcher.pyw"
    installed_launcher = install_root / "overlay_launcher.pyw"
    installed_launcher.write_bytes(launcher.read_bytes())
    (install_root / "current.json").write_bytes(pointer)
    for name in (
        "windows_overlay_support.py",
        "windows_overlay_worker.py",
        "windows_overlay_win32.py",
    ):
        (selected / name).write_text(f"IDENTITY = {name!r}\n")
    (selected / "windows_quota_overlay.py").write_text(
        "import json, os, pathlib, sys\n"
        "import windows_overlay_support\n"
        "import windows_overlay_worker\n"
        "import windows_overlay_win32\n"
        "pathlib.Path(os.environ['LAUNCHER_PROBE']).write_text(json.dumps({\n"
        "  'cwd': os.getcwd(), 'argv': sys.argv, 'path0': sys.path[0],\n"
        "  'modules': [__file__, windows_overlay_support.__file__,\n"
        "              windows_overlay_worker.__file__, windows_overlay_win32.__file__],\n"
        "}))\n"
    )
    harness = (
        "import json, os, pathlib, runpy, sys\n"
        "ns = runpy.run_path(os.environ['LAUNCHER'], run_name='launcher_test')\n"
        "old_argv, old_path, old_cwd = list(sys.argv), list(sys.path), os.getcwd()\n"
        "calls = []\n"
        "def checker():\n"
        "    calls.append('checked')\n"
        "    return os.environ['LOCK_AVAILABLE'] == '1'\n"
        "error = None\n"
        "try:\n"
        "    launched = ns['launch'](pathlib.Path(os.environ['INSTALL_ROOT']), "
        "json.loads(os.environ['ARGS']), install_lock_checker=checker)\n"
        "except Exception as exc:\n"
        "    launched = False\n"
        "    error = f'{type(exc).__name__}: {exc}'\n"
        "pathlib.Path(os.environ['HARNESS_RESULT']).write_text(json.dumps({\n"
        "  'launched': launched, 'error': error, 'calls': calls,\n"
        "  'argv_restored': sys.argv == old_argv,\n"
        "  'path_restored': sys.path == old_path,\n"
        "  'cwd_restored': os.getcwd() == old_cwd,\n"
        "}))\n"
    )
    environment = {
        **os.environ,
        "LAUNCHER": str(installed_launcher),
        "INSTALL_ROOT": str(install_root),
        "LAUNCHER_PROBE": str(probe),
        "HARNESS_RESULT": str(result_path),
        "ARGS": json.dumps(args or []),
        "LOCK_AVAILABLE": "1" if lock_available else "0",
    }
    completed = subprocess.run(
        [sys.executable, "-c", harness],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text())
    runtime = json.loads(probe.read_text()) if probe.exists() else None
    return result, runtime, install_root, selected


def test_content_version_hashes_fixed_filenames_and_bytes(tmp_path):
    """Derive a stable 16-hex version from every fixed runtime file."""
    source = write_source(tmp_path / "source", "v1")

    actual = compute_content_version(source)

    assert actual == expected_version(source)
    assert len(actual) == 16
    assert set(actual) <= set("0123456789abcdef")


def test_launcher_runs_selected_version_and_restores_process_state(tmp_path):
    """Run all installed companions with isolated argv, cwd and import path."""
    pointer = b'{"version":"0123456789abcdef"}'

    result, runtime, install_root, selected = run_launcher_harness(
        tmp_path,
        pointer,
        args=["--nonce", "a" * 32],
    )

    assert result == {
        "launched": True,
        "error": None,
        "calls": [],
        "argv_restored": True,
        "path_restored": True,
        "cwd_restored": True,
    }
    assert runtime["cwd"] == str(selected)
    assert runtime["path0"] == str(selected)
    assert runtime["argv"] == [
        str(selected / "windows_quota_overlay.py"),
        "--install-root",
        str(install_root),
        "--version",
        "0123456789abcdef",
        "--nonce",
        "a" * 32,
    ]
    assert {Path(path).name for path in runtime["modules"]} == set(RUNTIME_FILES)


def test_launcher_blocks_ordinary_start_but_nonce_bypasses_only_lock(tmp_path):
    """Block ordinary launch during install while allowing its nonce candidate."""
    pointer = b'{"version":"0123456789abcdef"}'

    blocked, runtime, _root, _selected = run_launcher_harness(
        tmp_path / "blocked",
        pointer,
        lock_available=False,
    )
    bypassed, bypass_runtime, _root, _selected = run_launcher_harness(
        tmp_path / "bypassed",
        pointer,
        args=["--nonce", "b" * 32],
        lock_available=False,
    )

    assert blocked["launched"] is False
    assert blocked["calls"] == ["checked"]
    assert runtime is None
    assert bypassed["launched"] is True
    assert bypassed["calls"] == []
    assert bypass_runtime is not None


@pytest.mark.parametrize(
    ("pointer", "error_fragment"),
    [
        (b"x" * 4097, "too large"),
        (b"not-json", "JSON"),
        (b'{"version":"../outside"}', "version"),
        (b'{"version":"0123456789abcdeg"}', "version"),
    ],
)
def test_launcher_bounds_and_validates_pointer_even_with_nonce(
    tmp_path, pointer, error_fragment
):
    """Reject oversized, malformed and traversal-capable version pointers."""
    result, runtime, _root, _selected = run_launcher_harness(
        tmp_path,
        pointer,
        args=["--nonce", "c" * 32],
        lock_available=False,
    )

    assert result["launched"] is False
    assert error_fragment.lower() in result["error"].lower()
    assert runtime is None


def test_existing_matching_version_is_reused_and_reinstall_repairs_startup(tmp_path):
    """Reuse immutable matching code while repairing launcher, Run and runtime."""
    source = write_source(tmp_path / "source", "same")
    installer, registry, processes = make_installer(tmp_path / "app", source)

    first = installer.install()
    version_dir = tmp_path / "app" / "overlay" / "versions" / first["version"]
    original_mtime = version_dir.stat().st_mtime_ns
    registry.value = "broken"
    (tmp_path / "app" / "overlay" / "overlay_launcher.pyw").write_bytes(b"broken")
    second = installer.install()

    assert first["version"] == second["version"]
    assert version_dir.stat().st_mtime_ns == original_mtime
    assert registry.value != "broken"
    assert len(processes.starts) == 2
    assert processes.starts[-1]["version"] == first["version"]
    assert processes.starts[-1]["handle"].alive is True


def test_same_name_version_mismatch_is_rejected_without_overwrite(tmp_path):
    """Refuse to overwrite a corrupt directory named for the source digest."""
    source = write_source(tmp_path / "source", "v1")
    root = tmp_path / "app"
    version = compute_content_version(source)
    corrupt = root / "overlay" / "versions" / version
    corrupt.mkdir(parents=True)
    for name in RUNTIME_FILES:
        (corrupt / name).write_bytes(b"corrupt")
    installer, registry, _processes = make_installer(root, source)

    with pytest.raises(RuntimeError, match="mismatch"):
        installer.install()

    assert registry.value is None
    assert (corrupt / RUNTIME_FILES[0]).read_bytes() == b"corrupt"


@pytest.mark.parametrize(
    "phase",
    [
        "version-staged",
        "launcher-replaced",
        "pointer-replaced",
        "registry-written",
        "candidate-started",
        "ready-timeout",
    ],
)
def test_upgrade_failure_fully_restores_static_state_and_restarts_old(
    tmp_path, phase
):
    """Roll back every pre-commit phase and reconstruct the old live identity."""
    old_source = write_source(tmp_path / "old-source", "old")
    new_source = write_source(tmp_path / "new-source", "new")
    root = tmp_path / "app"
    state_path = root / "overlay-state.json"
    state_path.parent.mkdir(parents=True)
    state_bytes = b'{"overlay_visible":true}\n'
    state_path.write_bytes(state_bytes)
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    snapshot = seed_installed_tree(root, old_source, registry, processes)
    failure = None if phase == "ready-timeout" else FailureAt(phase)
    if phase == "ready-timeout":
        processes.suppress_candidate_ready = True
    installer, _registry, _processes = make_installer(
        root,
        new_source,
        registry=registry,
        processes=processes,
        fault=failure,
    )

    with pytest.raises(Exception):
        installer.install()

    overlay = root / "overlay"
    assert registry.value == snapshot["registry"]
    assert (overlay / "overlay_launcher.pyw").read_bytes() == snapshot["launcher"]
    assert (overlay / "current.json").read_bytes() == snapshot["pointer"]
    assert {path.name for path in (overlay / "versions").iterdir()} == snapshot["versions"]
    assert state_path.read_bytes() == state_bytes
    assert not list(overlay.glob("ready-*.json"))
    assert processes.events[-1] == "stop-reset"
    assert processes.starts[-1]["rollback"] is True
    assert processes.starts[-1]["version"] == snapshot["version"]
    assert processes.starts[-1]["handle"].alive is True
    instance = json.loads((overlay / "instance.json").read_text())
    assert instance["pid"] == processes.starts[-1]["pid"]
    assert instance["nonce"] == processes.starts[-1]["nonce"]
    candidates = [start for start in processes.starts if not start["rollback"]]
    assert all(not start["handle"].alive for start in candidates)


@pytest.mark.parametrize(
    "phase",
    [
        "version-staged",
        "launcher-replaced",
        "pointer-replaced",
        "registry-written",
        "candidate-started",
        "ready-timeout",
    ],
)
def test_first_install_failure_leaves_no_code_or_startup_artifacts(tmp_path, phase):
    """Clean every first-install artifact while preserving user overlay state."""
    source = write_source(tmp_path / "source", "new")
    root = tmp_path / "app"
    state_path = root / "overlay-state.json"
    state_path.parent.mkdir(parents=True)
    state_bytes = b'{"queue_collapsed":true}\n'
    state_path.write_bytes(state_bytes)
    failure = None if phase == "ready-timeout" else FailureAt(phase)
    processes = FakeProcesses(root)
    processes.suppress_candidate_ready = phase == "ready-timeout"
    installer, registry, processes = make_installer(
        root,
        source,
        processes=processes,
        fault=failure,
    )

    with pytest.raises(Exception):
        installer.install()

    overlay = root / "overlay"
    assert registry.value is None
    assert not (overlay / "overlay_launcher.pyw").exists()
    assert not (overlay / "current.json").exists()
    assert not (overlay / "instance.json").exists()
    assert not list(overlay.glob("ready-*.json"))
    versions = overlay / "versions"
    assert not versions.exists() or not list(versions.iterdir())
    assert state_path.read_bytes() == state_bytes
    assert processes.events[-1] == "stop-reset"


def test_ready_requires_matching_nonce_pid_version_and_instance_identity(tmp_path):
    """Reject a ready marker when persistent process identity does not match."""
    source = write_source(tmp_path / "source", "new")
    root = tmp_path / "app"
    installer, _registry, processes = make_installer(root, source)
    original_start = processes.start

    def start_with_wrong_instance(*args, **kwargs):
        """Write ready normally but corrupt the candidate creation time."""
        handle = original_start(*args, **kwargs)
        path = root / "overlay" / "instance.json"
        instance = json.loads(path.read_text())
        instance["process_creation_time"] += 1
        path.write_text(json.dumps(instance))
        return handle

    processes.start = start_with_wrong_instance

    with pytest.raises(RuntimeError, match="identity"):
        installer.install()

    assert processes.starts[0]["handle"].alive is False
    assert not (root / "overlay" / "current.json").exists()


def test_candidate_must_remain_alive_for_500ms_after_ready(tmp_path):
    """Reject a structurally ready candidate that dies in the grace period."""
    old_source = write_source(tmp_path / "old-source", "old")
    new_source = write_source(tmp_path / "new-source", "new")
    root = tmp_path / "app"
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    snapshot = seed_installed_tree(root, old_source, registry, processes)
    processes.kill_candidate_during_grace = True
    installer, _registry, _processes = make_installer(
        root,
        new_source,
        registry=registry,
        processes=processes,
    )

    with pytest.raises(RuntimeError, match="500"):
        installer.install()

    assert json.loads((root / "overlay" / "current.json").read_text()) == {
        "version": snapshot["version"]
    }
    assert processes.starts[-1]["rollback"] is True
    assert processes.starts[-1]["handle"].alive is True


def test_old_stop_timeout_restores_staged_version_and_static_state(tmp_path):
    """Restore all static state even when the old verified process cannot exit."""
    old_source = write_source(tmp_path / "old-source", "old")
    new_source = write_source(tmp_path / "new-source", "new")
    root = tmp_path / "app"
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    snapshot = seed_installed_tree(root, old_source, registry, processes)
    processes.stop_wait_succeeds = False
    installer, _registry, _processes = make_installer(
        root,
        new_source,
        registry=registry,
        processes=processes,
    )

    with pytest.raises(RuntimeError, match="timeout"):
        installer.install()

    overlay = root / "overlay"
    assert registry.value == snapshot["registry"]
    assert (overlay / "overlay_launcher.pyw").read_bytes() == snapshot["launcher"]
    assert (overlay / "current.json").read_bytes() == snapshot["pointer"]
    assert {path.name for path in (overlay / "versions").iterdir()} == snapshot["versions"]
    assert processes.events[-1] == "stop-reset"


def test_cleanup_failure_after_commit_does_not_roll_back_candidate(tmp_path):
    """Keep a verified candidate committed when old-version pruning fails."""
    old_source = write_source(tmp_path / "old-source", "old")
    new_source = write_source(tmp_path / "new-source", "new")
    root = tmp_path / "app"
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    old = seed_installed_tree(root, old_source, registry, processes)
    extra = root / "overlay" / "versions" / "ffffffffffffffff"
    extra.mkdir()
    for name in RUNTIME_FILES:
        (extra / name).write_bytes(b"extra")
    failure = FailureAt("cleanup")
    installer, _registry, _processes = make_installer(
        root,
        new_source,
        registry=registry,
        processes=processes,
        fault=failure,
    )

    result = installer.install()

    assert result["installed"] is True
    assert result["running"] is True
    assert result["version"] != old["version"]
    assert result["cleanup_warning"]
    assert processes.starts[-1]["rollback"] is False
    assert processes.starts[-1]["handle"].alive is True


def test_ready_cleanup_failure_after_commit_does_not_roll_back_candidate(tmp_path):
    """Report ready cleanup failure without undoing a live committed version."""
    source = write_source(tmp_path / "source", "new")
    root = tmp_path / "app"
    files = FailingReadyCleanupFiles()
    installer, _registry, processes = make_installer(root, source, files=files)

    result = installer.install()

    assert result["installed"] is True
    assert result["running"] is True
    assert "ready cleanup failed" in result["cleanup_warning"]
    assert processes.starts[-1]["rollback"] is False
    assert processes.starts[-1]["handle"].alive is True
    assert json.loads((root / "overlay" / "current.json").read_text()) == {
        "version": result["version"]
    }


def test_idempotent_cleanup_keeps_only_current_and_valid_previous_version(tmp_path):
    """Discard stale staging directories instead of retaining one as previous."""
    source = write_source(tmp_path / "source", "same")
    root = tmp_path / "app"
    installer, _registry, _processes = make_installer(root, source)
    first = installer.install()
    versions = root / "overlay" / "versions"
    previous = versions / "1111111111111111"
    previous.mkdir()
    for name in RUNTIME_FILES:
        (previous / name).write_bytes(b"previous")
    stale = versions / ".stale.tmp"
    stale.mkdir()
    for name in RUNTIME_FILES:
        (stale / name).write_bytes(b"stale")

    installer.install()

    assert {path.name for path in versions.iterdir()} == {
        first["version"],
        previous.name,
    }


def test_status_validates_pid_creation_time_and_executable_path(tmp_path):
    """Report running only for the exact persisted process identity."""
    source = write_source(tmp_path / "source", "old")
    root = tmp_path / "app"
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    old = seed_installed_tree(root, source, registry, processes)
    installer, _registry, _processes = make_installer(
        root,
        source,
        registry=registry,
        processes=processes,
    )

    running = installer.status()
    instance = json.loads((root / "overlay" / "instance.json").read_text())
    processes.handles[instance["pid"]].creation_time += 1
    reused = installer.status()

    assert running["installed"] is True
    assert running["running"] is True
    assert running["current_version"] == old["version"]
    assert running["pid"] == instance["pid"]
    assert reused["running"] is False
    assert reused["pid"] is None
    assert not (root / "overlay" / "instance.json").exists()


def test_status_accepts_an_ordinary_startup_instance_without_nonce(tmp_path):
    """Treat a verified login-started runtime with a null nonce as live."""
    source = write_source(tmp_path / "source", "old")
    root = tmp_path / "app"
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    old = seed_installed_tree(root, source, registry, processes)
    instance_path = root / "overlay" / "instance.json"
    instance = json.loads(instance_path.read_text())
    instance["nonce"] = None
    instance_path.write_text(json.dumps(instance))
    installer, _registry, _processes = make_installer(
        root,
        source,
        registry=registry,
        processes=processes,
    )

    status = installer.status()

    assert status["running"] is True
    assert status["current_version"] == old["version"]
    assert instance_path.exists()


def test_status_guarded_cleanup_never_deletes_a_newer_instance(tmp_path):
    """Delete stale metadata only when its exact bytes still identify itself."""
    source = write_source(tmp_path / "source", "old")
    root = tmp_path / "app"
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    old = seed_installed_tree(root, source, registry, processes)
    stale = json.loads((root / "overlay" / "instance.json").read_text())
    processes.handles[stale["pid"]].executable_path = r"C:\Other\pythonw.exe"
    replacement = {
        **stale,
        "pid": stale["pid"] + 100,
        "process_creation_time": stale["process_creation_time"] + 100,
        "nonce": "newer",
    }
    replacement_bytes = json.dumps(replacement).encode()
    files = ReplacingFiles(replacement_bytes)
    installer, _registry, _processes = make_installer(
        root,
        source,
        registry=registry,
        processes=processes,
        files=files,
    )

    status = installer.status()

    assert status["running"] is False
    assert (root / "overlay" / "instance.json").read_bytes() == replacement_bytes
    assert old["version"] == status["current_version"]


def test_uninstall_waits_for_verified_instance_and_preserves_overlay_state(tmp_path):
    """Stop only the verified process and remove code while retaining user state."""
    source = write_source(tmp_path / "source", "old")
    root = tmp_path / "app"
    state_path = root / "overlay-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'{"custom_position":true}\n')
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    seed_installed_tree(root, source, registry, processes)
    instance = json.loads((root / "overlay" / "instance.json").read_text())
    handle = processes.handles[instance["pid"]]
    installer, _registry, _processes = make_installer(
        root,
        source,
        registry=registry,
        processes=processes,
    )

    result = installer.uninstall()

    assert result == {"installed": False, "running": False}
    assert handle.waits
    assert handle.alive is False
    assert registry.value is None
    assert not (root / "overlay").exists()
    assert state_path.read_bytes() == b'{"custom_position":true}\n'
    assert processes.events[-1] == "stop-reset"


def test_uninstall_timeout_preserves_code_startup_and_user_state(tmp_path):
    """Abort removal when a verified runtime does not exit before timeout."""
    source = write_source(tmp_path / "source", "old")
    root = tmp_path / "app"
    state_path = root / "overlay-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'{"overlay_visible":true}\n')
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    seed_installed_tree(root, source, registry, processes)
    original_registry = registry.value
    processes.stop_wait_succeeds = False
    installer, _registry, _processes = make_installer(
        root,
        source,
        registry=registry,
        processes=processes,
    )

    with pytest.raises(RuntimeError, match="timeout"):
        installer.uninstall()

    assert registry.value == original_registry
    assert (root / "overlay" / "current.json").exists()
    assert state_path.read_bytes() == b'{"overlay_visible":true}\n'
    assert processes.events[-1] == "stop-reset"


def test_uninstall_does_not_signal_an_unverified_reused_pid(tmp_path):
    """Avoid stopping an unrelated process when instance identity is stale."""
    source = write_source(tmp_path / "source", "old")
    root = tmp_path / "app"
    registry = FakeRegistry()
    processes = FakeProcesses(root)
    seed_installed_tree(root, source, registry, processes)
    instance = json.loads((root / "overlay" / "instance.json").read_text())
    unrelated = processes.handles[instance["pid"]]
    unrelated.creation_time += 1
    installer, _registry, _processes = make_installer(
        root,
        source,
        registry=registry,
        processes=processes,
    )

    installer.uninstall()

    assert unrelated.alive is True
    assert "stop-set" not in processes.events


@pytest.mark.parametrize("command", ["install", "status", "uninstall"])
def test_every_command_rejects_concurrent_installer_lock(tmp_path, command):
    """Return a dedicated contention error while another installer owns the mutex."""
    source = write_source(tmp_path / "source", "new")
    installer, _registry, processes = make_installer(tmp_path / "app", source)
    processes.lock_owned = False

    with pytest.raises(InstallLockError):
        getattr(installer, command)()


def test_non_windows_cli_rejects_before_constructing_production_adapters(capsys):
    """Reject macOS execution without touching directories, winreg or Win32."""
    calls = []

    def forbidden_factory():
        """Fail if the non-Windows guard constructs production adapters."""
        calls.append("called")
        raise AssertionError("factory must not run")

    code = main(
        ["status"],
        platform_name="posix",
        installer_factory=forbidden_factory,
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["error"]
    assert calls == []


def test_cli_returns_75_for_installer_lock_contention(capsys):
    """Map installer mutex contention to the documented process exit code."""
    code = main(
        ["install"],
        platform_name="nt",
        installer_factory=LockedInstaller,
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 75
    assert output["code"] == "installer_locked"


def test_production_adapters_construct_lazily_without_windows_access():
    """Avoid importing winreg or constructing Win32 APIs on macOS."""
    registry = Registry()
    processes = Processes()

    assert registry.__class__.__name__ == "Registry"
    assert processes._win32 is None


def test_process_start_cleans_known_child_when_identity_open_fails(
    tmp_path, monkeypatch
):
    """Terminate a newly spawned candidate if its identity handle cannot open."""
    RecordingPopen.calls = []
    RecordingPopen.instances = []
    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    processes = FailingOpenProcesses()

    with pytest.raises(OSError, match="cannot open"):
        processes.start(
            PYTHONW,
            tmp_path / "overlay_launcher.pyw",
            "d" * 32,
        )

    child = RecordingPopen.instances[-1]
    assert child.terminated is True
    assert child.waits == [5.0]


def test_rollback_process_start_removes_failure_injection_environment(
    tmp_path, monkeypatch
):
    """Prevent candidate failure hooks from poisoning an old-version restart."""
    RecordingPopen.calls = []
    RecordingPopen.instances = []
    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    monkeypatch.setenv("COCO_OVERLAY_TEST_MODE", "1")
    monkeypatch.setenv("COCO_OVERLAY_FAIL_BEFORE_READY", "1")
    processes = Processes()
    handle = FakeProcessHandle(4242, 54242, PYTHONW)

    def open_process(pid):
        """Return a fake identity handle for the launched process."""
        assert pid == 4242
        return handle

    processes.open_process = open_process

    result = processes.start(
        PYTHONW,
        tmp_path / "overlay_launcher.pyw",
        "e" * 32,
        rollback=True,
    )

    environment = RecordingPopen.calls[-1][1]["env"]
    assert result is handle
    assert "COCO_OVERLAY_TEST_MODE" not in environment
    assert "COCO_OVERLAY_FAIL_BEFORE_READY" not in environment
