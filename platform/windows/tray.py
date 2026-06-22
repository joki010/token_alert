#!/usr/bin/env python3
"""
token_alert 시스템 트레이 앱 (Windows)
pystray 기반
"""

import ctypes
import ctypes.wintypes
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image

# GUI 앱에서 subprocess 호출 시 콘솔 창이 깜빡이지 않도록
_NO_WINDOW = subprocess.CREATE_NO_WINDOW
_NO_WIN_DETACH = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

if getattr(sys, 'frozen', False):
    RESOURCES = Path(sys._MEIPASS)
else:
    RESOURCES = Path(__file__).parent.parent.parent.resolve()

ICON_PATH = RESOURCES / "claudecode-tray.png"
ICON_INACTIVE_PATH = RESOURCES / "claudecode-tray-inactive.png"
LOG_FILE = Path.home() / ".claude" / "token_alert.log"

UPDATE_INTERVAL = 10  # 상태 갱신 주기(초)
ICON_SIZE = (22, 22)
PID_FILE = Path.home() / ".token_alert.pid"
WATCHER_PY = Path.home() / ".local" / "lib" / "token_alert" / "src" / "watcher.py"


def _find_pythonw() -> Path | None:
    """pythonw.exe 경로를 찾는다. PATH → python 형제 경로 순으로 탐색."""
    found = shutil.which("pythonw")
    if found:
        return Path(found)
    python = shutil.which("python")
    if python:
        candidate = Path(python).parent / "pythonw.exe"
        if candidate.exists():
            return candidate
    return None


def _pid_exe_path(pid: int) -> str:
    """Windows API로 PID의 실행 파일 전체 경로를 반환. 외부 프로세스 없이 즉시 반환."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.lower()
        return ""
    finally:
        k32.CloseHandle(handle)


def is_watcher_running() -> bool:
    """PID 파일의 프로세스가 python 계열인지 확인해 watcher 실행 여부를 반환."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        return "python" in _pid_exe_path(pid)
    except (OSError, ValueError):
        return False


def watcher_start() -> None:
    """watcher.py를 pythonw.exe로 백그라운드 실행."""
    pythonw = _find_pythonw()
    if pythonw and WATCHER_PY.exists():
        subprocess.Popen(
            [str(pythonw), str(WATCHER_PY)],
            creationflags=_NO_WIN_DETACH,
            close_fds=True,
        )


def watcher_stop() -> None:
    """PID 파일로 watcher 프로세스를 종료."""
    if not PID_FILE.exists():
        return
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        if "python" in _pid_exe_path(pid):
            os.kill(pid, 9)
        PID_FILE.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def load_icon(path: Path) -> Image.Image:
    """아이콘 이미지를 로드한다. 파일 없으면 회색 폴백 아이콘 반환."""
    if path.exists():
        img = Image.open(path).convert("RGBA").resize(ICON_SIZE, Image.LANCZOS)
        r, g, b, a = img.split()
        a = a.point(lambda v: 255 if v > 30 else 0)
        return Image.merge("RGBA", (r, g, b, a))
    return Image.new("RGBA", ICON_SIZE, (100, 100, 100, 255))


def get_status_title() -> str:
    return "● 감시 중" if is_watcher_running() else "○ 감시 중지됨"


def get_toggle_title() -> str:
    return "감시 중지" if is_watcher_running() else "감시 재시작"


def make_menu(icon_ref: list) -> pystray.Menu:
    """동적 메뉴 생성. icon_ref[0]에 pystray.Icon 인스턴스를 담아 아이콘 갱신에 활용."""

    def toggle(icon, item):
        if is_watcher_running():
            watcher_stop()
        else:
            watcher_start()
        time.sleep(1)
        _update_icon(icon_ref[0])

    def open_log(icon, item):
        if LOG_FILE.exists():
            os.startfile(str(LOG_FILE))
        else:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            LOG_FILE.touch()
            os.startfile(str(LOG_FILE))

    def quit_app(icon, item):
        icon.stop()

    return pystray.Menu(
        pystray.MenuItem(lambda item: get_status_title(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: get_toggle_title(), toggle),
        pystray.MenuItem("로그 열기", open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", quit_app),
    )


def _update_icon(icon: pystray.Icon) -> None:
    """실행 상태에 따라 트레이 아이콘을 교체."""
    running = is_watcher_running()
    path = ICON_PATH if running else ICON_INACTIVE_PATH
    icon.icon = load_icon(path)


def status_updater(icon_ref: list) -> None:
    """백그라운드 스레드: 10초마다 아이콘 갱신."""
    while True:
        time.sleep(UPDATE_INTERVAL)
        if icon_ref[0] is not None:
            _update_icon(icon_ref[0])


def main() -> None:
    running = is_watcher_running()
    initial_icon = load_icon(ICON_PATH if running else ICON_INACTIVE_PATH)

    icon_ref: list = [None]
    icon = pystray.Icon(
        "token_alert",
        initial_icon,
        "token_alert",
    )
    icon_ref[0] = icon
    icon.menu = make_menu(icon_ref)

    updater = threading.Thread(target=status_updater, args=(icon_ref,), daemon=True)
    updater.start()

    icon.run()


if __name__ == "__main__":
    main()
