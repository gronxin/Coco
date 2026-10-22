"""Packaged backend entry point.

Serves the analyzer HTTP app, and forwards ``service`` / ``overlay`` subcommands to
the launchd management helpers so the menu bar app can drive them via one binary.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402
from analyzer.app import create_app  # noqa: E402
import macos_service  # noqa: E402
import macos_overlay  # noqa: E402


def serve() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8765)


def _dispatch_service(args) -> int:
    action = args.action
    if action == "install":
        macos_service.install(backend_source=args.backend_source)
    elif action == "uninstall":
        macos_service.uninstall()
    elif action == "status":
        print(macos_service.status())
    elif action == "sync":
        macos_service.sync(backend_source=args.backend_source)
    elif action == "restart":
        macos_service.restart()
    elif action == "autostart":
        macos_service.autostart(args.value)
    elif action == "purge-data":
        macos_service.purge_data()
    return 0


def _dispatch_overlay(args) -> int:
    action = args.action
    if action == "install":
        macos_overlay.install(binary_source=args.binary_source)
    elif action == "uninstall":
        macos_overlay.uninstall()
    elif action == "status":
        print(macos_overlay.status())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="CocoBackend")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve")

    svc = sub.add_parser("service")
    svc.add_argument("action", choices=[
        "install", "uninstall", "status", "sync", "restart", "autostart", "purge-data"])
    svc.add_argument("--backend", dest="backend_source", default=None)
    svc.add_argument("value", nargs="?")

    ov = sub.add_parser("overlay")
    ov.add_argument("action", choices=["install", "uninstall", "status"])
    ov.add_argument("--binary", dest="binary_source", default=None)

    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        serve()
        return 0
    if args.command == "service":
        return _dispatch_service(args)
    return _dispatch_overlay(args)


if __name__ == "__main__":
    raise SystemExit(main())
