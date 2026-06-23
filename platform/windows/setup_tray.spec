# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 6.x 문법
# SPECPATH: 이 spec 파일이 있는 디렉토리 (platform/windows/)
# 프로젝트 루트는 SPECPATH 기준 두 단계 위

import os

project_root = os.path.abspath(os.path.join(SPECPATH, '..', '..'))

a = Analysis(
    [os.path.join(SPECPATH, 'tray.py')],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(project_root, 'claudecode-tray.png'), '.'),
        (os.path.join(project_root, 'claudecode-tray-inactive.png'), '.'),
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
    console=False,      # 콘솔 창 완전 억제 (windowed=True 와 동일, PyInstaller 6.x 권장 표기)
    windowed=True,      # 호환성을 위해 병기
    onefile=True,
    icon=os.path.join(project_root, 'claudecode-tray.ico'),
)
