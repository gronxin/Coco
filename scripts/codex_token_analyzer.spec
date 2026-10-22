# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the packaged Coco backend (onedir)."""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / 'scripts' / 'app_entry.py')],
    pathex=[str(ROOT), str(ROOT / 'scripts')],
    datas=[
        (str(ROOT / 'frontend' / 'dist'), 'frontend/dist'),
        (str(ROOT / 'analyzer' / 'prices.json'), 'analyzer'),
    ],
    hiddenimports=[
        'macos_service',
        'macos_overlay',
        'macos_service_support',
        'macos_overlay_support',
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
    ],
    excludes=['pytest'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CocoBackend',
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name='CocoBackend',
)
