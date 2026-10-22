"""Install or remove the per-user launchd service for the local analyzer."""
import os
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

from macos_service_support import build_path_env, classify_conflict, plist_payload


LABEL = 'local.codex-token-analyzer.service'
ROOT = Path(__file__).resolve().parent.parent
RUNTIME = Path.home() / 'Library' / 'Application Support' / 'CodexTokenAnalyzer'
DATA = RUNTIME / 'data'
BACKEND_DIR = RUNTIME / 'backend'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
TARGET = f'gui/{os.getuid()}/{LABEL}'


def launchctl_uid() -> int:
    return os.getuid()


def _is_loaded():
    return subprocess.run(['launchctl', 'print', TARGET], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def _port_is_open():
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


def _sync_source():
    """Copy executable code out of Documents, which background launchd cannot access."""
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


def sync(backend_source=None):
    """Place runnable code under RUNTIME.

    With ``backend_source`` (packaged .app), copy that onedir tree to BACKEND_DIR.
    Without it, fall back to copying the source checkout (developer path).
    Existing data in DATA is always preserved.
    """
    if sys.platform != 'darwin':
        raise RuntimeError('此服务脚本仅支持 macOS')
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.mkdir(exist_ok=True, mode=0o700)
    if backend_source is None:
        _sync_source()
    else:
        _replace_tree(Path(backend_source), BACKEND_DIR)
    print(f'运行文件已同步到 {RUNTIME}')


def install(backend_source=None):
    if sys.platform != 'darwin':
        raise RuntimeError('此服务安装脚本仅支持 macOS')
    conflict = classify_conflict(PLIST.is_file(), _is_loaded(), _port_is_open())
    if conflict == 'foreign_port':
        raise RuntimeError('8765 端口已被其他进程占用；请先停止该进程后重试')
    if conflict == 'replace_loaded':
        subprocess.run(['launchctl', 'bootout', TARGET], check=False)
    if conflict == 'stale_plist':
        PLIST.unlink(missing_ok=True)

    sync(backend_source=backend_source)

    if backend_source is None:
        program = [str(RUNTIME / 'run.sh')]
        workdir = RUNTIME
    else:
        program = [str(BACKEND_DIR / 'CocoBackend'), 'serve']
        workdir = BACKEND_DIR

    PLIST.parent.mkdir(parents=True, exist_ok=True)
    environment = {
        'CODEX_ANALYZER_MANAGED': '1',
        'TOKEN_ANALYZER_DB': str(DATA / 'analytics.sqlite'),
        'PATH': build_path_env(Path.home()),
    }
    settings = plist_payload(
        label=LABEL,
        program_arguments=program,
        working_directory=workdir,
        environment=environment,
    )
    with PLIST.open('wb') as output:
        plistlib.dump(settings, output)
    PLIST.chmod(0o600)
    if not _is_loaded():
        subprocess.run(['launchctl', 'bootstrap', f'gui/{launchctl_uid()}', str(PLIST)],
                       check=True)
    print(f'已安装并启动 {LABEL}')


def uninstall():
    if _is_loaded():
        subprocess.run(['launchctl', 'bootout', TARGET], check=True)
    PLIST.unlink(missing_ok=True)
    print(f'已移除 {LABEL}')


def restart():
    if _is_loaded():
        subprocess.run(['launchctl', 'bootout', TARGET], check=True)
    if PLIST.is_file():
        subprocess.run(['launchctl', 'bootstrap', f'gui/{launchctl_uid()}', str(PLIST)],
                       check=True)
    print(f'已重启 {LABEL}')


def autostart(value):
    enabled = str(value).strip().lower() in ('on', '1', 'true', 'yes')
    if not PLIST.is_file():
        raise RuntimeError('尚未安装服务，无法设置开机自启')
    with PLIST.open('rb') as source:
        settings = plistlib.load(source)
    settings['RunAtLoad'] = enabled
    settings['KeepAlive'] = enabled
    with PLIST.open('wb') as output:
        plistlib.dump(settings, output)
    PLIST.chmod(0o600)
    if _is_loaded():
        subprocess.run(['launchctl', 'bootout', TARGET], check=False)
        subprocess.run(['launchctl', 'bootstrap', f'gui/{launchctl_uid()}', str(PLIST)],
                       check=True)
    print(f'开机自启已{"开启" if enabled else "关闭"}')


def purge_data():
    shutil.rmtree(DATA, ignore_errors=True)
    print('本地数据已删除')


def status():
    return {'installed': PLIST.is_file(), 'loaded': _is_loaded(),
            'listening': _port_is_open(), 'runtime': str(RUNTIME)}


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
        print(status())
