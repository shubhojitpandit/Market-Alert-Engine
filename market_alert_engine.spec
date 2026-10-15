# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)
sound_asset = project_root / "assets" / "sounds" / "custom_alert.mp3"

analysis = Analysis(
    [str(project_root / "gui" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(sound_asset), str(Path("assets") / "sounds"))],
    hiddenimports=["websockets"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="MarketAlertEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
