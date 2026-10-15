# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)
sound_asset = project_root / "assets" / "sounds" / "custom_alert.mp3"

analysis = Analysis(
    [str(project_root / "gui" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(sound_asset), str(Path("assets") / "sounds"))],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "diagnostic_runtime_hook.py")],
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
    name="MarketAlertEngine-Diagnostic",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)
