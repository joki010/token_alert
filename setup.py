from setuptools import setup

APP = ['tray.py']
DATA_FILES = [
    'claudecode-tray.png',
    'claudecode-tray-inactive.png',
    'claudecode-color.png',
]
OPTIONS = {
    'argv_emulation': True,
    'plist': {
        'LSUIElement': True,
        'CFBundleIdentifier': 'com.token-alert.tray'
    },
    'packages': ['rumps'],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
