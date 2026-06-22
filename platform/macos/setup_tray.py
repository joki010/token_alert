"""
py2app 빌드 설정 — TokenAlertTray.app 생성

실행: .venv/bin/python platform/macos/setup_tray.py py2app
출력: dist/tray.app  →  install.py가 ~/Applications/TokenAlertTray.app 으로 이동
"""

from setuptools import setup

APP = ["platform/macos/tray.py"]
DATA_FILES = [
    ("", [
        "claudecode-tray.png",
        "claudecode-tray-inactive.png",
    ]),
]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "TokenAlertTray",
        "CFBundleIdentifier": "com.token-alert.tray",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
    "packages": ["rumps"],
    "excludes": ["tkinter", "unittest"],
}

setup(
    name="TokenAlertTray",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
