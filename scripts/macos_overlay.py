"""Build and manage the per-user Codex quota overlay on macOS."""
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path


LABEL = 'local.codex-token-analyzer.overlay'
ROOT = Path(__file__).resolve().parent.parent
RUNTIME = Path.home() / 'Library' / 'Application Support' / 'CodexTokenAnalyzer'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
TARGET = f'gui/{os.getuid()}/{LABEL}'
BINARY = RUNTIME / 'codex-quota-overlay'


def loaded():
    """Return whether launchd currently has the overlay agent loaded."""
    return subprocess.run(['launchctl', 'print', TARGET], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def build():
    """Compile the native overlay and copy the binary into its runtime directory."""
    if sys.platform != 'darwin':
        raise RuntimeError('额度悬浮窗仅支持 macOS')
    compiler = shutil.which('swiftc')
    if not compiler:
        raise RuntimeError('未找到 swiftc，请先安装 Xcode Command Line Tools')
    ROOT.joinpath('.build').mkdir(exist_ok=True)
    subprocess.run([compiler, '-module-cache-path', '/private/tmp/codex-quota-swift-cache',
                    '-o', str(ROOT / '.build' / 'codex-quota-overlay'),
                    str(ROOT / 'scripts' / 'quota_overlay_support.swift'),
                    str(ROOT / 'scripts' / 'quota_overlay.swift')], check=True)
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    staged = RUNTIME / '.codex-quota-overlay.staged'
    shutil.copy2(ROOT / '.build' / 'codex-quota-overlay', staged)
    staged.chmod(0o700)
    staged.replace(BINARY)
    print(f'悬浮窗已更新：{BINARY}')


def install():
    """Build and register the per-user navigation overlay LaunchAgent."""
    build()
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        'Label': LABEL,
        'ProgramArguments': [str(BINARY)],
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 5,
        'StandardOutPath': str(RUNTIME / 'data' / 'overlay.out.log'),
        'StandardErrorPath': str(RUNTIME / 'data' / 'overlay.err.log'),
    }
    (RUNTIME / 'data').mkdir(exist_ok=True)
    with PLIST.open('wb') as output:
        plistlib.dump(settings, output)
    PLIST.chmod(0o600)
    if loaded():
        subprocess.run(['launchctl', 'bootout', TARGET], check=True)
    for attempt in range(3):
        try:
            subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(PLIST)],
                           check=True, capture_output=True, text=True)
            break
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    print('额度导航按钮已启动。切换到 Codex 后点击左侧按钮显示浮窗；点击任务数量可折叠。')


def uninstall():
    """Unload the overlay LaunchAgent and remove its plist."""
    if loaded():
        subprocess.run(['launchctl', 'bootout', TARGET], check=True)
    PLIST.unlink(missing_ok=True)
    print('额度悬浮窗已关闭。')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('install', 'status', 'uninstall'):
        raise SystemExit('用法：python scripts/macos_overlay.py install|status|uninstall')
    command = sys.argv[1]
    if command == 'install':
        install()
    elif command == 'uninstall':
        uninstall()
    else:
        print({'installed': PLIST.is_file(), 'loaded': loaded(), 'binary': str(BINARY)})
