# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

pyside_datas, pyside_binaries, pyside_hiddenimports = collect_all("PySide6")
shiboken_datas, shiboken_binaries, shiboken_hiddenimports = collect_all("shiboken6")
sounddevice_datas, sounddevice_binaries, sounddevice_hiddenimports = collect_all("sounddevice")

block_cipher = None

a = Analysis(
    ["src/main.py"],
    pathex=[],
    binaries=pyside_binaries + shiboken_binaries + sounddevice_binaries,
    datas=pyside_datas + shiboken_datas + sounddevice_datas,
    hiddenimports=(
        pyside_hiddenimports
        + shiboken_hiddenimports
        + sounddevice_hiddenimports
        + ["numpy", "httpx", "dotenv"]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Screamer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Screamer",
)
