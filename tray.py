#!/usr/bin/env python3
"""
token_alert 메뉴 막대 트레이 앱
rumps 기반 — macOS 메뉴 막대에 감시 상태를 표시하고 중지/재시작을 제공한다.
"""

import os
import subprocess
import sys
from pathlib import Path

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

TRAY_LOCK = Path("/tmp/token_alert_tray.pid")
LABEL = "com.token-alert.watcher"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
ICON = Path(__file__).parent / "claudecode-tray.png"
ICON_INACTIVE = Path(__file__).parent / "claudecode-tray-inactive.png"
LOG_FILE = Path.home() / ".claude" / "token_alert.log"

UPDATE_INTERVAL = 10  # 상태 갱신 주기(초)


def is_watcher_running() -> bool:
    result = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True, text=True, encoding='utf-8'
    )
    return '"PID"' in result.stdout


def watcher_start() -> None:
    subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True)


def watcher_stop() -> None:
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)


class TokenAlertApp(rumps.App):
    def __init__(self):
        # 독 아이콘 숨기기 — Python.app이 포그라운드 앱으로 표시되지 않도록
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        icon = str(ICON) if ICON.exists() else None
        super().__init__("token_alert", title="", icon=icon, quit_button=None)

        self.status_item = rumps.MenuItem("확인 중...")
        self.status_item.set_callback(None)

        self.toggle_item = rumps.MenuItem("감시 중지", callback=self.toggle_watcher)

        self.menu = [
            self.status_item,
            None,
            self.toggle_item,
            rumps.MenuItem("로그 열기", callback=self.open_log),
            None,
            rumps.MenuItem("종료", callback=self.quit_app),
        ]

        self._refresh_status()

    def _refresh_status(self):
        running = is_watcher_running()
        if running:
            self.icon = str(ICON) if ICON.exists() else None
            self.status_item.title = "● 감시 중"
            self.toggle_item.title = "감시 중지"
        else:
            self.icon = str(ICON_INACTIVE) if ICON_INACTIVE.exists() else None
            self.status_item.title = "○ 감시 중지됨"
            self.toggle_item.title = "감시 재시작"

    @rumps.timer(UPDATE_INTERVAL)
    def update_status(self, _):
        self._refresh_status()

    def toggle_watcher(self, _):
        if is_watcher_running():
            watcher_stop()
        else:
            watcher_start()
        self._refresh_status()

    def open_log(self, _):
        subprocess.run(["open", "-a", "Console", str(LOG_FILE)])

    def quit_app(self, _):
        rumps.quit_application()


def already_running() -> bool:
    if TRAY_LOCK.exists():
        try:
            pid = int(TRAY_LOCK.read_text())
            subprocess.run(["kill", "-0", str(pid)], capture_output=True, check=True)
            return True
        except (ValueError, subprocess.CalledProcessError):
            pass
    TRAY_LOCK.write_text(str(os.getpid()))
    return False


if __name__ == "__main__":
    if already_running():
        sys.exit(0)
    try:
        app = TokenAlertApp()
        app.run()
    except Exception:
        import traceback; traceback.print_exc()
    finally:
        TRAY_LOCK.unlink(missing_ok=True)
