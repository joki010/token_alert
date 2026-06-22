# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 6.x 문법

a = Analysis(
    ['platform/windows/tray.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('claudecode-tray.png', '.'),
        ('claudecode-tray-inactive.png', '.'),
    ],
    hiddenimports=['pystray._win32'],
    hookspath=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='TokenAlertTray',
    windowed=True,
    onefile=True,
    icon='claudecode-tray.ico',
)
