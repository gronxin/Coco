"""Install and manage the current-user Windows quota overlay."""

from dataclasses import dataclass
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


INSTALL_MUTEX_NAME = r"Local\CocoCodexManagerOverlayInstall"
STOP_EVENT_NAME = r"Local\CocoCodexManagerOverlayStop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "CocoCodexManagerOverlay"
RUNTIME_FILES = (
    "windows_quota_overlay.py",
    "windows_overlay_support.py",
    "windows_overlay_win32.py",
    "windows_overlay_worker.py",
)
JSON_MAX_BYTES = 4096
READY_TIMEOUT = 8.0
LIVENESS_GRACE = 0.5
STOP_TIMEOUT = 5.0
VERSION_PATTERN = re.compile(r"^[0-9a-f]{16}$")
NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
FAILURE_ENVIRONMENT = (
    "COCO_OVERLAY_TEST_MODE",
    "COCO_OVERLAY_FAIL_BEFORE_READY",
)


class InstallerError(RuntimeError):
    """Report an installation, status or removal failure."""


class InstallLockError(InstallerError):
    """Report contention on the per-user installer mutex."""


@dataclass(frozen=True)
class StaticSnapshot:
    """Capture all static installation state restored before commit."""

    registry_value: object
    launcher_bytes: object
    pointer_bytes: object
    version_names: tuple
    overlay_existed: bool
    versions_existed: bool


@dataclass
class LiveInstance:
    """Pair persisted runtime metadata with its verified process handle."""

    metadata: dict
    raw: bytes
    handle: object


class StartedProcess:
    """Pair a spawned child with its verified synchronization handle."""

    def __init__(self, child, identity):
        """Store the exact child process and verified kernel identity."""
        self._child = child
        self._identity = identity

    @property
    def pid(self):
        """Return the verified child process identifier."""
        return self._identity.pid

    @property
    def creation_time(self):
        """Return the verified child creation timestamp."""
        return self._identity.creation_time

    @property
    def executable_path(self):
        """Return the verified child executable path."""
        return self._identity.executable_path

    def is_alive(self):
        """Return whether the verified child is still running."""
        return self._identity.is_alive()

    def wait(self, timeout):
        """Wait for the verified child to exit."""
        return self._identity.wait(timeout)

    def terminate_known_child(self, timeout):
        """Force-stop only this directly spawned and identity-verified child."""
        try:
            if self._child.poll() is None:
                self._child.terminate()
            self._child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return self._identity.wait(0)

    def close(self):
        """Close the verified kernel identity handle."""
        self._identity.close()


class LocalFiles:
    """Provide injectable local filesystem operations."""

    def exists(self, path):
        """Return whether a filesystem entry exists."""
        return Path(path).exists()

    def is_directory(self, path):
        """Return whether a path names a directory."""
        return Path(path).is_dir()

    def make_directory(self, path):
        """Create a directory and all missing parents."""
        Path(path).mkdir(parents=True, exist_ok=True)

    def read_bytes(self, path, maximum=None):
        """Read bytes with an optional strict maximum size."""
        with Path(path).open("rb") as handle:
            content = handle.read() if maximum is None else handle.read(maximum + 1)
        if maximum is not None and len(content) > maximum:
            raise InstallerError(f"{Path(path).name} is too large")
        return content

    def write_bytes(self, path, content):
        """Write bytes to an unpublished staging path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    def write_atomic(self, path, content):
        """Atomically replace one file with fully flushed bytes."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def replace(self, source, destination):
        """Atomically rename a staged path into its final location."""
        os.replace(source, destination)

    def remove_file(self, path):
        """Remove one file when present."""
        Path(path).unlink(missing_ok=True)

    def remove_tree(self, path):
        """Remove one directory tree without following absent paths."""
        shutil.rmtree(path, ignore_errors=False) if Path(path).exists() else None

    def remove_if_unchanged(self, path, expected):
        """Remove a file only when its current bytes still match a snapshot."""
        target = Path(path)
        try:
            current = self.read_bytes(target, JSON_MAX_BYTES)
        except FileNotFoundError:
            return False
        if current != expected:
            return False
        target.unlink(missing_ok=True)
        return True

    def directory_names(self, path):
        """Return names of immediate child directories in stable order."""
        root = Path(path)
        if not root.is_dir():
            return ()
        return tuple(sorted(item.name for item in root.iterdir() if item.is_dir()))

    def files_matching(self, path, pattern):
        """Return matching immediate child files in stable order."""
        root = Path(path)
        if not root.is_dir():
            return ()
        return tuple(sorted((item for item in root.glob(pattern) if item.is_file())))

    def directory_mtime(self, path):
        """Return a directory's nanosecond modification time."""
        return Path(path).stat().st_mtime_ns

    def remove_empty_directory(self, path):
        """Remove a directory only when it exists and is empty."""
        target = Path(path)
        try:
            target.rmdir()
        except (FileNotFoundError, OSError):
            return False
        return True


class Registry:
    """Read and write the current user's startup registration lazily."""

    def _winreg(self):
        """Import winreg only when a production registry operation runs."""
        import winreg

        return winreg

    def get_run_value(self):
        """Return the current overlay Run value or None."""
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def set_run_value(self, value):
        """Set the current-user overlay Run value."""
        winreg = self._winreg()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, value)

    def delete_run_value(self):
        """Delete the current-user overlay Run value when present."""
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            return


class Processes:
    """Provide lazy production Win32 process and synchronization operations."""

    def __init__(self):
        """Initialize without importing or constructing Win32 adapters."""
        self._win32 = None

    def _api(self):
        """Construct the Win32 adapter on first production use."""
        if self._win32 is None:
            try:
                from windows_overlay_win32 import Win32Api
            except ImportError:
                from scripts.windows_overlay_win32 import Win32Api

            self._win32 = Win32Api()
        return self._win32

    def installer_mutex(self):
        """Acquire the named installer mutex as an initial owner."""
        return self._api().create_owned_mutex(INSTALL_MUTEX_NAME)

    def stop_event(self):
        """Open the shared manual-reset runtime stop event."""
        return self._api().open_stop_event(STOP_EVENT_NAME)

    def open_process(self, pid):
        """Open a process for identity checks and bounded waits."""
        return self._api().open_process(pid)

    def start(self, pythonw, launcher, nonce, rollback=False):
        """Start the stable launcher and retain a synchronization handle."""
        environment = None
        if rollback:
            environment = dict(os.environ)
            for name in FAILURE_ENVIRONMENT:
                environment.pop(name, None)
        process = subprocess.Popen(
            [str(pythonw), str(launcher), "--nonce", nonce],
            cwd=str(Path(launcher).parent),
            env=environment,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            identity = self.open_process(process.pid)
            return StartedProcess(process, identity)
        except BaseException:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=STOP_TIMEOUT)
            except BaseException:
                pass
            raise

    def monotonic(self):
        """Return monotonic time for readiness deadlines."""
        return time.monotonic()

    def sleep(self, seconds):
        """Sleep for a bounded polling or liveness interval."""
        time.sleep(seconds)


class NoFault:
    """Ignore transaction phase notifications in production."""

    def __call__(self, _phase):
        """Accept one phase without injecting a failure."""
        return None


def _hash_payload(payload):
    """Hash fixed filenames followed by their exact bytes."""
    digest = hashlib.sha256()
    for name in RUNTIME_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(payload[name])
    return digest.hexdigest()[:16]


def compute_content_version(source_root, files=None):
    """Return the first 16 SHA-256 hex characters for fixed runtime files."""
    adapter = files or LocalFiles()
    root = Path(source_root)
    payload = {name: adapter.read_bytes(root / name) for name in RUNTIME_FILES}
    return _hash_payload(payload)


def _normalized_windows_path(path):
    """Normalize a Windows executable path for case-insensitive comparison."""
    return ntpath.normcase(ntpath.normpath(str(path)))


def _optional_bytes(files, path, maximum=None):
    """Read optional bytes and return None only when the path is absent."""
    try:
        return files.read_bytes(path, maximum)
    except FileNotFoundError:
        return None


def _decode_json(raw, label):
    """Decode one UTF-8 JSON object or raise a concise installer error."""
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallerError(f"{label} contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise InstallerError(f"{label} must contain a JSON object")
    return value


def _valid_integer(value):
    """Return whether a value is an integer but not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


class Installer:
    """Perform locked, transactional current-user overlay management."""

    def __init__(
        self,
        install_root,
        source_root,
        pythonw_path,
        files=None,
        registry=None,
        processes=None,
        fault=None,
    ):
        """Store paths and injectable platform adapters without side effects."""
        self.install_root = Path(install_root)
        self.source_root = Path(source_root)
        self.pythonw_path = str(pythonw_path)
        self.files = files or LocalFiles()
        self.registry = registry or Registry()
        self.processes = processes or Processes()
        self.fault = fault or NoFault()
        self.overlay_root = self.install_root / "overlay"
        self.versions_root = self.overlay_root / "versions"
        self.launcher_path = self.overlay_root / "overlay_launcher.pyw"
        self.pointer_path = self.overlay_root / "current.json"
        self.instance_path = self.overlay_root / "instance.json"
        self.launcher_source = self.source_root / "windows_overlay_launcher.pyw"

    def _acquire_mutex(self):
        """Acquire the installer mutex or raise the dedicated contention error."""
        mutex = self.processes.installer_mutex()
        if not mutex.owned:
            mutex.close()
            raise InstallLockError("another overlay installer is running")
        return mutex

    def _source_payload(self):
        """Read all source bytes before changing installed state."""
        payload = {
            name: self.files.read_bytes(self.source_root / name)
            for name in RUNTIME_FILES
        }
        launcher = self.files.read_bytes(self.launcher_source)
        return payload, launcher

    def _snapshot(self):
        """Capture exact static state before the first installation write."""
        return StaticSnapshot(
            registry_value=self.registry.get_run_value(),
            launcher_bytes=_optional_bytes(self.files, self.launcher_path),
            pointer_bytes=_optional_bytes(
                self.files,
                self.pointer_path,
                JSON_MAX_BYTES,
            ),
            version_names=self.files.directory_names(self.versions_root),
            overlay_existed=self.files.is_directory(self.overlay_root),
            versions_existed=self.files.is_directory(self.versions_root),
        )

    def _pointer_version(self, raw):
        """Return a valid version from pointer bytes or None."""
        if raw is None:
            return None
        try:
            payload = _decode_json(raw, "current.json")
        except InstallerError:
            return None
        version = payload.get("version")
        if (
            set(payload) != {"version"}
            or not isinstance(version, str)
            or VERSION_PATTERN.fullmatch(version) is None
        ):
            return None
        return version

    def _read_record(self, path, label):
        """Read an optional bounded JSON object and preserve its exact bytes."""
        raw = _optional_bytes(self.files, path, JSON_MAX_BYTES)
        if raw is None:
            return None, None
        try:
            return _decode_json(raw, label), raw
        except InstallerError:
            return None, raw

    def _valid_instance_metadata(self, metadata):
        """Return whether persisted instance fields have strict basic types."""
        if not isinstance(metadata, dict):
            return False
        pid = metadata.get("pid")
        creation = metadata.get("process_creation_time")
        pythonw = metadata.get("pythonw_path")
        version = metadata.get("version")
        nonce = metadata.get("nonce")
        valid_nonce = nonce is None or (
            isinstance(nonce, str)
            and bool(nonce)
            and len(nonce) <= 128
            and Path(nonce).name == nonce
            and "\\" not in nonce
        )
        return (
            _valid_integer(pid)
            and pid > 0
            and _valid_integer(creation)
            and creation >= 0
            and isinstance(pythonw, str)
            and bool(pythonw)
            and isinstance(version, str)
            and VERSION_PATTERN.fullmatch(version) is not None
            and valid_nonce
        )

    def _remove_instance_raw(self, raw):
        """Delete instance metadata only while its exact identity is unchanged."""
        if raw is not None:
            self.files.remove_if_unchanged(self.instance_path, raw)

    def _inspect_instance(
        self,
        expected_version=None,
        expected_pythonw=None,
        clean_stale=True,
    ):
        """Open and validate PID, creation time, executable and optional version."""
        metadata, raw = self._read_record(self.instance_path, "instance.json")
        if not self._valid_instance_metadata(metadata):
            if clean_stale:
                self._remove_instance_raw(raw)
            return None
        if expected_version is not None and metadata["version"] != expected_version:
            if clean_stale:
                self._remove_instance_raw(raw)
            return None
        if expected_pythonw is not None and _normalized_windows_path(
            metadata["pythonw_path"]
        ) != _normalized_windows_path(expected_pythonw):
            if clean_stale:
                self._remove_instance_raw(raw)
            return None
        try:
            handle = self.processes.open_process(metadata["pid"])
        except OSError:
            if clean_stale:
                self._remove_instance_raw(raw)
            return None
        valid = False
        try:
            valid = (
                handle.pid == metadata["pid"]
                and handle.creation_time == metadata["process_creation_time"]
                and _normalized_windows_path(handle.executable_path)
                == _normalized_windows_path(metadata["pythonw_path"])
                and handle.is_alive()
            )
        except OSError:
            valid = False
        if not valid:
            handle.close()
            if clean_stale:
                self._remove_instance_raw(raw)
            return None
        return LiveInstance(metadata=metadata, raw=raw, handle=handle)

    def _version_matches(self, target, payload, expected_version):
        """Return whether an existing immutable version exactly matches source."""
        if not self.files.is_directory(target):
            return False
        try:
            installed = {
                name: self.files.read_bytes(Path(target) / name)
                for name in RUNTIME_FILES
            }
        except (OSError, InstallerError):
            return False
        return installed == payload and _hash_payload(installed) == expected_version

    def _stage_version(self, version, payload):
        """Reuse a matching version or atomically publish a complete new one."""
        target = self.versions_root / version
        if self.files.exists(target):
            if not self._version_matches(target, payload, version):
                raise InstallerError("existing version directory mismatch")
            return False
        self.files.make_directory(self.versions_root)
        staging = self.versions_root / f".{version}.{uuid.uuid4().hex}.tmp"
        try:
            self.files.make_directory(staging)
            for name in RUNTIME_FILES:
                self.files.write_bytes(staging / name, payload[name])
            if not self._version_matches(staging, payload, version):
                raise InstallerError("staged version verification failed")
            self.files.replace(staging, target)
        except BaseException:
            if self.files.exists(staging):
                self.files.remove_tree(staging)
            raise
        return True

    def _startup_command(self):
        """Build the quoted current-user startup command."""
        return subprocess.list2cmdline(
            [str(self.pythonw_path), str(self.launcher_path.resolve())]
        )

    def _write_pointer(self, version):
        """Atomically select one immutable runtime version."""
        content = (
            json.dumps({"version": version}, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        self.files.write_atomic(self.pointer_path, content)

    def _stop_instance(self, live, stop_event):
        """Signal and wait for one already verified runtime instance."""
        if live is None:
            return
        stop_event.set()
        if not live.handle.wait(STOP_TIMEOUT):
            raise InstallerError("overlay process did not exit before timeout")
        self._remove_instance_raw(live.raw)

    def _ready_path(self, nonce):
        """Return the bounded ready-marker path for a generated nonce."""
        if NONCE_PATTERN.fullmatch(nonce) is None:
            raise InstallerError("invalid ready nonce")
        return self.overlay_root / f"ready-{nonce}.json"

    def _remove_ready(self, nonce):
        """Remove a nonce-owned ready marker after its writer is stable or stopped."""
        self.files.remove_file(self._ready_path(nonce))

    def _cleanup_ready_files(self):
        """Idempotently remove obsolete nonce ready markers after commit."""
        for path in self.files.files_matching(self.overlay_root, "ready-*.json"):
            self.files.remove_file(path)

    def _candidate_identity(self, handle, version, nonce):
        """Return the expected persisted identity for a started candidate."""
        return {
            "pid": handle.pid,
            "process_creation_time": handle.creation_time,
            "pythonw_path": handle.executable_path,
            "version": version,
            "nonce": nonce,
        }

    def _instance_matches_candidate(self, metadata, expected):
        """Return whether instance metadata exactly identifies the candidate."""
        return (
            self._valid_instance_metadata(metadata)
            and metadata["pid"] == expected["pid"]
            and metadata["process_creation_time"]
            == expected["process_creation_time"]
            and _normalized_windows_path(metadata["pythonw_path"])
            == _normalized_windows_path(expected["pythonw_path"])
            and _normalized_windows_path(metadata["pythonw_path"])
            == _normalized_windows_path(self.pythonw_path)
            and metadata["version"] == expected["version"]
            and metadata["nonce"] == expected["nonce"]
        )

    def _verify_candidate(self, handle, version, nonce):
        """Wait for matching ready and instance identities before the commit grace."""
        deadline = self.processes.monotonic() + READY_TIMEOUT
        expected = self._candidate_identity(handle, version, nonce)
        ready_path = self._ready_path(nonce)
        while self.processes.monotonic() <= deadline:
            if not handle.is_alive():
                raise InstallerError("candidate exited before ready")
            ready, _ready_raw = self._read_record(ready_path, "ready marker")
            if ready is not None:
                if (
                    set(ready) != {"pid", "version", "nonce"}
                    or ready.get("pid") != expected["pid"]
                    or ready.get("version") != version
                    or ready.get("nonce") != nonce
                ):
                    raise InstallerError("candidate ready identity mismatch")
                instance, _instance_raw = self._read_record(
                    self.instance_path,
                    "instance.json",
                )
                if not self._instance_matches_candidate(instance, expected):
                    raise InstallerError("candidate instance identity mismatch")
                return
            remaining = deadline - self.processes.monotonic()
            if remaining <= 0:
                break
            self.processes.sleep(min(0.05, remaining))
        self.fault("ready-timeout")
        raise InstallerError("candidate ready timeout")

    def _verify_liveness_grace(self, handle):
        """Require a ready candidate to stay alive for another 500 milliseconds."""
        self.processes.sleep(LIVENESS_GRACE)
        if not handle.is_alive():
            raise InstallerError("candidate exited during 500 ms liveness grace")

    def _remove_candidate_instance(self, handle, version, nonce):
        """Remove persisted metadata only when it still belongs to this candidate."""
        metadata, raw = self._read_record(self.instance_path, "instance.json")
        expected = self._candidate_identity(handle, version, nonce)
        if raw is not None and self._instance_matches_candidate(metadata, expected):
            self._remove_instance_raw(raw)

    def _restore_file(self, path, content):
        """Restore exact snapshotted bytes or restore prior absence."""
        if content is None:
            self.files.remove_file(path)
        else:
            self.files.write_atomic(path, content)

    def _restore_static(self, snapshot, preserve_versions=()):
        """Restore registry, launcher, pointer and pre-existing version set."""
        self._restore_file(self.launcher_path, snapshot.launcher_bytes)
        self._restore_file(self.pointer_path, snapshot.pointer_bytes)
        if snapshot.registry_value is None:
            self.registry.delete_run_value()
        else:
            self.registry.set_run_value(snapshot.registry_value)
        for name in self.files.directory_names(self.versions_root):
            if name not in snapshot.version_names and name not in preserve_versions:
                self.files.remove_tree(self.versions_root / name)
        if not snapshot.versions_existed:
            self.files.remove_empty_directory(self.versions_root)
        if not snapshot.overlay_existed:
            self.files.remove_empty_directory(self.overlay_root)

    def _restart_old(self, snapshot):
        """Restart the restored old version with a clean rollback nonce."""
        version = self._pointer_version(snapshot.pointer_bytes)
        if (
            version is None
            or snapshot.launcher_bytes is None
            or version not in snapshot.version_names
        ):
            return None
        nonce = uuid.uuid4().hex
        handle = self.processes.start(
            self.pythonw_path,
            self.launcher_path,
            nonce,
            rollback=True,
        )
        try:
            self._verify_candidate(handle, version, nonce)
            self._verify_liveness_grace(handle)
            self._remove_ready(nonce)
        except BaseException:
            handle.close()
            raise
        return handle

    def _rollback(self, snapshot, old_live, candidate, version, nonce, stop_event):
        """Stop candidates, restore static state and reconstruct the old instance."""
        errors = []
        candidate_stopped = True
        old_stopped = True
        if candidate is not None:
            try:
                if candidate.is_alive():
                    stop_event.set()
                    if not candidate.wait(STOP_TIMEOUT):
                        terminate = getattr(
                            candidate,
                            "terminate_known_child",
                            None,
                        )
                        if terminate is None or not terminate(STOP_TIMEOUT):
                            candidate_stopped = False
                            errors.append(
                                "candidate did not stop during rollback"
                            )
                if candidate_stopped:
                    self._remove_candidate_instance(candidate, version, nonce)
            except BaseException as exc:
                candidate_stopped = False
                errors.append(str(exc))
        if not candidate_stopped:
            raise InstallerError("; ".join(errors))
        if old_live is not None and old_live.handle.is_alive():
            try:
                stop_event.set()
                if not old_live.handle.wait(STOP_TIMEOUT):
                    old_stopped = False
                    errors.append("old process did not stop during rollback")
                else:
                    self._remove_instance_raw(old_live.raw)
            except BaseException as exc:
                old_stopped = False
                errors.append(str(exc))
        try:
            self._remove_ready(nonce)
        except BaseException as exc:
            errors.append(str(exc))
        preserve_versions = () if candidate_stopped else (version,)
        try:
            self._restore_static(snapshot, preserve_versions=preserve_versions)
        except BaseException as exc:
            errors.append(str(exc))
        try:
            stop_event.reset()
            if candidate_stopped and old_stopped:
                restarted = self._restart_old(snapshot)
                if restarted is not None:
                    restarted.close()
                else:
                    _stale, raw = self._read_record(
                        self.instance_path,
                        "instance.json",
                    )
                    if raw is not None:
                        self._remove_instance_raw(raw)
        except BaseException as exc:
            errors.append(str(exc))
        finally:
            try:
                stop_event.reset()
            except BaseException as exc:
                errors.append(str(exc))
        if errors:
            raise InstallerError("; ".join(errors))

    def _prune_versions(self, current, previous):
        """Keep only the current and one previous immutable runtime version."""
        names = list(self.files.directory_names(self.versions_root))
        keep = {current}
        if previous in names and previous != current:
            keep.add(previous)
        if len(keep) < 2:
            alternatives = [
                name
                for name in names
                if name not in keep and VERSION_PATTERN.fullmatch(name) is not None
            ]

            def version_mtime(name):
                """Return one candidate version's modification time."""
                return self.files.directory_mtime(self.versions_root / name)

            alternatives.sort(key=version_mtime, reverse=True)
            if alternatives:
                keep.add(alternatives[0])
        for name in names:
            if name not in keep:
                self.files.remove_tree(self.versions_root / name)

    def _install_locked(self):
        """Execute the complete install transaction while the mutex is owned."""
        payload, launcher_bytes = self._source_payload()
        version = _hash_payload(payload)
        snapshot = self._snapshot()
        old_version = self._pointer_version(snapshot.pointer_bytes)
        old_live = self._inspect_instance(
            expected_version=old_version,
            clean_stale=True,
        )
        candidate = None
        nonce = uuid.uuid4().hex
        stop_event = self.processes.stop_event()
        try:
            self._stage_version(version, payload)
            self.fault("version-staged")
            stop_event.set()
            if old_live is not None:
                if not old_live.handle.wait(STOP_TIMEOUT):
                    raise InstallerError("old overlay did not exit before timeout")
                self._remove_instance_raw(old_live.raw)
            self.files.write_atomic(self.launcher_path, launcher_bytes)
            self.fault("launcher-replaced")
            self._write_pointer(version)
            self.fault("pointer-replaced")
            self.registry.set_run_value(self._startup_command())
            self.fault("registry-written")
            stop_event.reset()
            candidate = self.processes.start(
                self.pythonw_path,
                self.launcher_path,
                nonce,
                rollback=False,
            )
            self.fault("candidate-started")
            self._verify_candidate(candidate, version, nonce)
            self._verify_liveness_grace(candidate)
        except BaseException as exc:
            try:
                self._rollback(
                    snapshot,
                    old_live,
                    candidate,
                    version,
                    nonce,
                    stop_event,
                )
            except BaseException as rollback_error:
                raise InstallerError(
                    f"{exc}; rollback failed: {rollback_error}"
                ) from exc
            raise
        finally:
            if old_live is not None:
                old_live.handle.close()
            if candidate is not None:
                candidate.close()
            stop_event.close()
        cleanup_errors = []
        try:
            self._remove_ready(nonce)
        except BaseException as exc:
            cleanup_errors.append(str(exc))
        try:
            self._cleanup_ready_files()
            self.fault("cleanup")
            self._prune_versions(version, old_version)
        except BaseException as exc:
            cleanup_errors.append(str(exc))
        return {
            "installed": True,
            "running": True,
            "version": version,
            "cleanup_warning": "; ".join(cleanup_errors) or None,
        }

    def install(self):
        """Install or repair the overlay under the full installer mutex."""
        mutex = self._acquire_mutex()
        try:
            return self._install_locked()
        finally:
            mutex.close()

    def _status_locked(self):
        """Inspect static installation and verified runtime state."""
        registry_value = self.registry.get_run_value()
        pointer_raw = _optional_bytes(self.files, self.pointer_path, JSON_MAX_BYTES)
        current_version = self._pointer_version(pointer_raw)
        version_valid = (
            current_version is not None
            and self.files.is_directory(self.versions_root / current_version)
        )
        installed = (
            registry_value is not None
            and self.files.exists(self.launcher_path)
            and version_valid
        )
        live = self._inspect_instance(
            expected_version=current_version,
            clean_stale=True,
        )
        try:
            return {
                "installed": installed,
                "startup_registered": registry_value is not None,
                "current_version": current_version,
                "running": live is not None,
                "pid": None if live is None else live.metadata["pid"],
            }
        finally:
            if live is not None:
                live.handle.close()

    def status(self):
        """Return installation and verified process status under the mutex."""
        mutex = self._acquire_mutex()
        try:
            return self._status_locked()
        finally:
            mutex.close()

    def _uninstall_locked(self):
        """Stop a verified instance and remove code while preserving user state."""
        live = self._inspect_instance(clean_stale=True)
        if live is not None:
            stop_event = self.processes.stop_event()
            try:
                stop_event.set()
                if not live.handle.wait(STOP_TIMEOUT):
                    raise InstallerError("overlay process did not exit before timeout")
                self._remove_instance_raw(live.raw)
            finally:
                stop_event.reset()
                stop_event.close()
                live.handle.close()
        self.registry.delete_run_value()
        self.files.remove_tree(self.overlay_root)
        return {"installed": False, "running": False}

    def uninstall(self):
        """Uninstall code and startup registration under the mutex."""
        mutex = self._acquire_mutex()
        try:
            return self._uninstall_locked()
        finally:
            mutex.close()


def production_installer():
    """Construct the real current-user installer without mutating its targets."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise InstallerError("LOCALAPPDATA is unavailable")
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        raise InstallerError("pythonw.exe is missing; recreate the Windows environment")
    return Installer(
        install_root=Path(local_app_data) / "CocoCodexManager",
        source_root=Path(__file__).resolve().parent,
        pythonw_path=pythonw.resolve(),
        files=LocalFiles(),
        registry=Registry(),
        processes=Processes(),
    )


def _emit(payload):
    """Print one concise JSON-compatible command result."""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv=None, platform_name=None, installer_factory=None):
    """Run install, status or uninstall with stable JSON and exit codes."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or arguments[0] not in {"install", "status", "uninstall"}:
        _emit({"error": "用法：python scripts/windows_overlay.py install|status|uninstall"})
        return 1
    platform = os.name if platform_name is None else platform_name
    if platform != "nt":
        _emit({"error": "Windows 额度浮窗安装器仅支持原生 Windows"})
        return 1
    factory = installer_factory or production_installer
    try:
        installer = factory()
        result = getattr(installer, arguments[0])()
    except InstallLockError as exc:
        _emit({"error": str(exc), "code": "installer_locked"})
        return 75
    except Exception as exc:
        _emit({"error": str(exc)})
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
