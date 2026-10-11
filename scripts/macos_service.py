"""Install or remove the per-user launchd service for the local analyzer."""
import os
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path


LABEL = 'local.codex-token-analyzer.service'
ROOT = Path(__file__).resolve().parent.parent
RUNTIME = Path.home() / 'Library' / 'Application Support' / 'CodexTokenAnalyzer'
DATA = RUNTIME / 'data'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
TARGET = f'gui/{os.getuid()}/{LABEL}'


def is_loaded():
    return subprocess.run(['launchctl', 'print', TARGET], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def port_is_open():
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', 8765)) == 0


def _replace_tree(source, target):
    staged = RUNTIME / f'.{target.name}.staged'
    previous = RUNTIME / f'.{target.name}.previous'
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    shutil.copytree(source, staged, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if target.exists():
        target.rename(previous)
    staged.rename(target)
    shutil.rmtree(previous, ignore_errors=True)


def sync():
    """Copy executable code out of Documents, which background launchd cannot access."""
    if sys.platform != 'darwin':
        raise RuntimeError('此服务脚本仅支持 macOS')
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.mkdir(exist_ok=True, mode=0o700)
    for name in ('analyzer', '.venv'):
        _replace_tree(ROOT / name, RUNTIME / name)
    (RUNTIME / 'frontend').mkdir(exist_ok=True)
    _replace_tree(ROOT / 'frontend' / 'dist', RUNTIME / 'frontend' / 'dist')
    shutil.copy2(ROOT / 'run.sh', RUNTIME / 'run.sh')
    database = DATA / 'analytics.sqlite'
    if not database.exists() and (ROOT / '.data' / 'analytics.sqlite').exists():
        with sqlite3.connect(ROOT / '.data' / 'analytics.sqlite') as source:
            with sqlite3.connect(database) as destination:
                source.backup(destination)
        database.chmod(0o600)
    print(f'运行文件已同步到 {RUNTIME}')


def install():
    if sys.platform != 'darwin':
        raise RuntimeError('此服务安装脚本仅支持 macOS')
    if not is_loaded() and port_is_open():
        raise RuntimeError('8765 端口已有其他服务；请先停止旧的前台进程')
    sync()
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        'Label': LABEL,
        'ProgramArguments': [str(RUNTIME / 'run.sh')],
        'WorkingDirectory': str(RUNTIME),
        'EnvironmentVariables': {
            'CODEX_ANALYZER_MANAGED': '1',
            'TOKEN_ANALYZER_DB': str(DATA / 'analytics.sqlite'),
            'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        },
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 5,
        'StandardOutPath': str(DATA / 'service.out.log'),
        'StandardErrorPath': str(DATA / 'service.err.log'),
    }
    with PLIST.open('wb') as output:
        plistlib.dump(settings, output)
    PLIST.chmod(0o600)
    if not is_loaded():
        subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(PLIST)], check=True)
    print(f'已安装并启动 {LABEL}')


def uninstall():
    if is_loaded():
        subprocess.run(['launchctl', 'bootout', TARGET], check=True)
    PLIST.unlink(missing_ok=True)
    print(f'已移除 {LABEL}')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('install', 'sync', 'status', 'uninstall'):
        raise SystemExit('用法：python scripts/macos_service.py install|sync|status|uninstall')
    command = sys.argv[1]
    if command == 'install':
        install()
    elif command == 'uninstall':
        uninstall()
    elif command == 'sync':
        sync()
    else:
        print({'installed': PLIST.is_file(), 'loaded': is_loaded(),
               'listening': port_is_open(), 'runtime': str(RUNTIME)})
