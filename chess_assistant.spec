# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

stockfish_datas, stockfish_binaries, stockfish_hiddenimports = collect_all('stockfish')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        ('/opt/homebrew/Cellar/stockfish/18/bin/stockfish', 'bin'),
    ] + stockfish_binaries,
    datas=[
        ('assets/calibration_board.png', 'assets'),
        ('assets/pieces', 'assets/pieces'),
    ] + stockfish_datas,
    hiddenimports=stockfish_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='chess_assistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
