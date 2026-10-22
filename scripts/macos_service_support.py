"""Pure helpers for the macOS service installer (testable without launchd)."""
from pathlib import Path


def build_path_env(home: Path) -> str:
    """PATH that covers the usual places a user's ``codex`` CLI can live."""
    parts = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(home / ".npm-global" / "bin"),
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    return ":".join(parts)


def plist_payload(*, label, program_arguments, working_directory, environment,
                  autostart=True):
    data_dir = Path(environment['TOKEN_ANALYZER_DB']).parent
    return {
        "Label": label,
        "ProgramArguments": list(program_arguments),
        "WorkingDirectory": str(working_directory),
        "EnvironmentVariables": dict(environment),
        "RunAtLoad": bool(autostart),
        "KeepAlive": bool(autostart),
        "ThrottleInterval": 5,
        "StandardOutPath": str(data_dir / "service.out.log"),
        "StandardErrorPath": str(data_dir / "service.err.log"),
    }


def classify_conflict(plist_exists: bool, target_loaded: bool, port_open: bool) -> str:
    """Decide how an install should treat pre-existing state.

    ok            - nothing in the way
    replace_loaded- same Label is already loaded; boot it out first
    stale_plist   - plist left on disk but not loaded; delete before writing
    foreign_port  - 8765 held by something we did not install; refuse
    """
    if target_loaded:
        return "replace_loaded"
    if port_open:
        return "foreign_port"
    if plist_exists:
        return "stale_plist"
    return "ok"
