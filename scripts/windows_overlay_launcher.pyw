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


class LauncherError(RuntimeError):
    """Report an invalid or unavailable installed runtime."""


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


def _default_install_lock_available():
    """Return whether the caller can momentarily own the installer mutex."""
    from windows_overlay_win32 import Win32Api

    mutex = Win32Api().create_owned_mutex(INSTALL_MUTEX_NAME)
    try:
        return mutex.owned
    finally:
        mutex.close()


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
    version = _load_current(root)
    version_root, runtime = _selected_runtime(root, version)
    old_path = list(sys.path)
    old_argv = list(sys.argv)
    old_cwd = Path.cwd()
    checker = install_lock_checker or _default_install_lock_available
    try:
        sys.path.insert(0, str(version_root))
        if nonce is None and not checker():
            return False
        sys.argv[:] = _runtime_argv(runtime, root, version, nonce)
        os.chdir(version_root)
        runpy.run_path(str(runtime), run_name="__main__")
        return True
    finally:
        os.chdir(old_cwd)
        sys.argv[:] = old_argv
        sys.path[:] = old_path


def main(argv=None):
    """Run the installed launcher and map validation failures to exit code one."""
    try:
        launch(argv=argv)
    except LauncherError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
