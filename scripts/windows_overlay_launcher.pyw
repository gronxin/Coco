"""Launch the atomically selected Windows overlay runtime."""

import json
import os
from pathlib import Path
import re
import runpy
import sys


CURRENT_MAX_BYTES = 4096
INSTALL_MUTEX_NAME = r"Local\CocoCodexManagerOverlayInstall"
NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
RUNTIME_NAME = "windows_quota_overlay.py"
VERSION_PATTERN = re.compile(r"^[0-9a-f]{16}$")
ERROR_ALREADY_EXISTS = 183


class LauncherError(RuntimeError):
    """Report an invalid or unavailable installed runtime."""


class _LauncherMutex:
    """Hold the stable launcher's directly acquired installer mutex."""

    def __init__(self, kernel32, handle):
        """Store one owned mutex handle and its kernel provider."""
        self._kernel32 = kernel32
        self._handle = handle
        self._closed = False

    def close(self):
        """Release ownership and close the mutex handle once."""
        if self._closed:
            return
        self._kernel32.ReleaseMutex(self._handle)
        self._kernel32.CloseHandle(self._handle)
        self._closed = True


def _read_bounded(path, maximum):
    """Read at most ``maximum`` bytes and reject a larger file."""
    with Path(path).open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise LauncherError(f"{Path(path).name} is too large")
    return content


def _load_current(install_root):
    """Load and validate the bounded current-version pointer."""
    pointer_path = Path(install_root) / "current.json"
    try:
        payload = json.loads(_read_bounded(pointer_path, CURRENT_MAX_BYTES))
    except json.JSONDecodeError as exc:
        raise LauncherError("current.json contains invalid JSON") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise LauncherError("current.json cannot be read") from exc
    if not isinstance(payload, dict) or set(payload) != {"version"}:
        raise LauncherError("current.json must contain only a version")
    version = payload["version"]
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise LauncherError("current.json version is invalid")
    return version


def _parse_nonce(argv):
    """Return a validated nonce from the launch arguments, if present."""
    arguments = list(argv)
    if not arguments:
        return None
    if len(arguments) != 2 or arguments[0] != "--nonce":
        raise LauncherError("launcher arguments are invalid")
    nonce = arguments[1]
    if NONCE_PATTERN.fullmatch(nonce) is None:
        raise LauncherError("launcher nonce is invalid")
    return nonce


def _selected_runtime(install_root, version):
    """Resolve the selected runtime without permitting path traversal."""
    versions_root = (Path(install_root) / "versions").resolve()
    version_root = (versions_root / version).resolve()
    if not version_root.is_relative_to(versions_root):
        raise LauncherError("selected version escapes the versions directory")
    runtime = (version_root / RUNTIME_NAME).resolve()
    if not runtime.is_relative_to(version_root):
        raise LauncherError("selected runtime escapes its version directory")
    if not version_root.is_dir() or not runtime.is_file():
        raise LauncherError("selected runtime is missing")
    return version_root, runtime


def _default_install_lock():
    """Acquire and return the installer mutex, or None when it is busy."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, True, INSTALL_MUTEX_NAME)
    if not handle:
        raise LauncherError("installer mutex cannot be opened")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return _LauncherMutex(kernel32, handle)


def _runtime_argv(runtime, install_root, version, nonce):
    """Build the runtime arguments derived from trusted installation state."""
    arguments = [
        str(runtime),
        "--install-root",
        str(install_root),
        "--version",
        version,
    ]
    if nonce is not None:
        arguments.extend(["--nonce", nonce])
    return arguments


def launch(install_root=None, argv=None, install_lock_checker=None):
    """Run the selected overlay while restoring all modified process globals."""
    root = (
        Path(install_root).resolve()
        if install_root is not None
        else Path(__file__).resolve().parent
    )
    arguments = list(sys.argv[1:] if argv is None else argv)
    nonce = _parse_nonce(arguments)
    checker = install_lock_checker or _default_install_lock
    install_lock = None
    lock_released = False

    def handoff_install_lock():
        """Release the ordinary-launch lock once runtime identity is durable."""
        nonlocal lock_released
        if lock_released:
            return
        lock_released = True
        closer = getattr(install_lock, "close", None)
        if closer is not None:
            closer()

    if nonce is None:
        install_lock = checker()
        if not install_lock:
            return False
    try:
        version = _load_current(root)
        version_root, runtime = _selected_runtime(root, version)
        old_path = list(sys.path)
        old_argv = list(sys.argv)
        old_cwd = Path.cwd()
        try:
            sys.path.insert(0, str(version_root))
            sys.argv[:] = _runtime_argv(runtime, root, version, nonce)
            os.chdir(version_root)
            initial_globals = (
                {"_COCO_INSTALL_LOCK_HANDOFF": handoff_install_lock}
                if nonce is None
                else None
            )
            runpy.run_path(
                str(runtime),
                init_globals=initial_globals,
                run_name="__main__",
            )
            return True
        finally:
            os.chdir(old_cwd)
            sys.argv[:] = old_argv
            sys.path[:] = old_path
    finally:
        handoff_install_lock()


def main(argv=None):
    """Run the installed launcher and map validation failures to exit code one."""
    try:
        launch(argv=argv)
    except LauncherError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
