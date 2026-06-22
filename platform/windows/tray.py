#!/usr/bin/env python3
"""
token_alert 시스템 트레이 앱 (Windows)
pystray 기반
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image

# GUI 앱에서 subprocess 호출 시 콘솔 창이 깜빡이지 않도록
_NO_WINDOW = subprocess.CREATE_NO_WINDOW

if getattr(sys, 'frozen', False):
    RESOURCES = Path(sys._MEIPASS)
else:
    RESOURCES = Path(__file__).parent.parent.parent.resolve()

ICON_PATH = RESOURCES / "claudecode-tray.png"
ICON_INACTIVE_PATH = RESOURCES / "claudecode-tray-inactive.png"
LOG_FILE = Path.home() / ".claude" / "token_alert.log"

TASK_WATCHER = "TokenAlertWatcher"
UPDATE_INTERVAL = 10  # 상태 갱신 주기(초)
ICON_SIZE = (22, 22)
PID_FILE = Path.home() / ".token_alert.pid"


def is_watcher_running() -> bool:
    """watcher 실행 여부 확인. PID 파일 우선, 없으면 schtasks로 재확인."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            pass
    # PID 파일 없거나 스테일: schtasks로 재확인 (한글/영문 Windows 모두 대응)
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_WATCHER, "/fo", "LIST"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    return "Running" in result.stdout or "실행 중" in result.stdout


def watcher_start() -> None:
    subprocess.run(["schtasks", "/run", "/tn", TASK_WATCHER],
                   capture_output=True, creationflags=_NO_WINDOW)


def watcher_stop() -> None:
    subprocess.run(["schtasks", "/end", "/tn", TASK_WATCHER],
                   capture_output=True, creationflags=_NO_WINDOW)


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
