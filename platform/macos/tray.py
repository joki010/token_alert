#!/usr/bin/env python3
"""
token_alert 메뉴 막대 트레이 앱 (macOS)
rumps 기반
"""

import os
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

# py2app 번들 여부에 따라 리소스 경로 결정
if getattr(sys, "frozen", False):
    RESOURCES = Path(os.environ.get("RESOURCEPATH", Path(__file__).parent))
else:
    RESOURCES = Path(__file__).parent.parent.parent.resolve()

SCRIPT_ROOT = Path(__file__).parent.parent.parent.resolve()
TRAY_LOCK = Path("/tmp/token_alert_tray.pid")
LABEL = "com.token-alert.watcher"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
TRAY_LABEL = "com.token-alert.tray"
TRAY_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{TRAY_LABEL}.plist"
TRAY_PLIST_DISABLED = TRAY_PLIST.with_suffix(".plist.disabled")
ICON = RESOURCES / "claudecode-tray.png"
ICON_INACTIVE = RESOURCES / "claudecode-tray-inactive.png"
LOG_FILE = Path.home() / ".claude" / "token_alert.log"
POLICY_FILE = Path.home() / ".config" / "token-alert" / "activation-policy.json"

AUTOSAVE_NAME = "TokenAlert"
UPDATE_INTERVAL = 10
DEFAULT_POLICY = {"version": 1, "enabled": False}


def read_policy() -> dict:
    try:
        if not POLICY_FILE.exists():
            return dict(DEFAULT_POLICY)
        with POLICY_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        if not isinstance(data, dict) or set(data) != {"version", "enabled", "enabled_at"}:
            return dict(DEFAULT_POLICY)
        if type(data.get("version")) is not int or data["version"] != 1:
            return dict(DEFAULT_POLICY)
        if type(data.get("enabled")) is not bool:
            return dict(DEFAULT_POLICY)
        enabled_at = data.get("enabled_at")
        if not isinstance(enabled_at, str):
            return dict(DEFAULT_POLICY)
        parsed = datetime.fromisoformat(enabled_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            return dict(DEFAULT_POLICY)

        return {
            "version": 1,
            "enabled": data["enabled"],
            "enabled_at": parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return dict(DEFAULT_POLICY)


def write_policy(enabled: bool) -> bool:
    if type(enabled) is not bool:
        return False
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    data = {"version": 1, "enabled": enabled, "enabled_at": now_iso}
    temporary_path = None
    try:
        POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{POLICY_FILE.name}.",
            suffix=".tmp",
            dir=str(POLICY_FILE.parent),
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, POLICY_FILE)

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            directory_fd = os.open(POLICY_FILE.parent, directory_flags)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    pass
            finally:
                os.close(directory_fd)
        return True
    except OSError:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        return False


def is_watcher_running() -> bool:
    result = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True, text=True, encoding="utf-8"
    )
    return '"PID"' in result.stdout


def watcher_start() -> bool:
    # ponytail: launchctl load/unload는 실패해도 exit code 0을 반환하는 경우가 있어
    # returncode로는 성공 여부를 신뢰할 수 없음 — 실제 상태를 다시 조회해 판정.
    # exec 실패로 즉시 죽는 프로세스의 PID가 launchctl list에 잠깐 남는 걸 피하려 0.5초 대기 후 확인.
    subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True)
    time.sleep(0.5)
    return is_watcher_running()


def watcher_stop() -> bool:
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    time.sleep(0.5)
    return not is_watcher_running()


def current_tray_plist_path() -> Path:
    """quit_app의 self-unload 등에서 쓸, 지금 실제로 존재하는 tray plist 경로.
    로그인 시작이 꺼진 상태(파일이 .disabled로 rename됨)면 그쪽을 반환."""
    return TRAY_PLIST if TRAY_PLIST.exists() else TRAY_PLIST_DISABLED


def is_login_item_enabled() -> bool:
    return TRAY_PLIST.exists()


def set_login_item_enabled(enabled: bool) -> bool:
    # ponytail: 파일 존재 여부로 로그인 시작을 켜고 끔 — launchd는 로그인 시
    # ~/Library/LaunchAgents를 스캔하므로, 파일을 rename해두면 지금 실행 중인
    # 트레이는 그대로 두고 '다음 로그인'부터만 자동시작 여부가 바뀐다.
    try:
        if enabled:
            if TRAY_PLIST_DISABLED.exists():
                TRAY_PLIST_DISABLED.rename(TRAY_PLIST)
        else:
            if TRAY_PLIST.exists():
                TRAY_PLIST.rename(TRAY_PLIST_DISABLED)
        return True
    except OSError:
        return False


class TokenAlertApp(rumps.App):
    def __init__(self):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        icon = str(ICON) if ICON.exists() else None
        super().__init__("token_alert", title=None, icon=icon, quit_button=None)

        self.status_item = rumps.MenuItem("확인 중...")
        self.status_item.set_callback(None)

        self.activation_item = rumps.MenuItem("Claude 자동 창 시작", callback=self.toggle_activation)
        self.activation_item.state = read_policy().get("enabled", False)

        self.toggle_item = rumps.MenuItem("감시 중지", callback=self.toggle_watcher)
        self.login_item = rumps.MenuItem("맥 시작시 실행", callback=self.toggle_login_item)
        self.login_item.state = is_login_item_enabled()

        self.menu = [
            self.status_item,
            self.activation_item,
            rumps.separator,
            self.toggle_item,
            rumps.MenuItem("로그 열기", callback=self.open_log),
            self.login_item,
            None,
            rumps.MenuItem("종료", callback=self.quit_app),
        ]
        self._user_stopped = False
        self._was_running = None
        self._refresh_status()

    def _set_autosave_name(self):
        """앱 시작 후 NSStatusItem autosaveName 설정 — macOS Tahoe에서 메뉴바 표시에 필요."""
        try:
            self._nsapp.nsstatusitem.setAutosaveName_(AUTOSAVE_NAME)
        except AttributeError:
            pass

    @rumps.timer(0.1)
    def _init_autosave(self, sender):
        sender.stop()
        self._set_autosave_name()

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
            if self._was_running and not self._user_stopped:
                rumps.notification(
                    "token_alert", "감시 중지됨",
                    "watcher가 예기치 않게 종료됐습니다. 메뉴에서 재시작하세요."
                )
            self._user_stopped = False
        self._was_running = running

        # update activation state from policy in case it changed externally
        self.activation_item.state = read_policy().get("enabled", False)

    @rumps.timer(UPDATE_INTERVAL)
    def update_status(self, _):
        self._refresh_status()

    def toggle_activation(self, sender):
        new_state = not sender.state
        if write_policy(new_state):
            sender.state = new_state
        else:
            rumps.notification(
                "token_alert", "오류",
                "정책 파일을 저장할 수 없습니다."
            )

    def toggle_watcher(self, _):
        if is_watcher_running():
            self._user_stopped = True
            ok = watcher_stop()
        else:
            ok = watcher_start()
        if not ok:
            rumps.notification("token_alert", "명령 실패", "launchctl 실행에 실패했습니다.")
        self._refresh_status()

    def open_log(self, _):
        subprocess.run(["open", "-a", "Console", str(LOG_FILE)])

    def toggle_login_item(self, _):
        enabled = not is_login_item_enabled()
        if set_login_item_enabled(enabled):
            self.login_item.state = enabled
        else:
            rumps.notification("token_alert", "설정 실패", "로그인 시작 설정을 변경하지 못했습니다.")

    def quit_app(self, _):
        if is_watcher_running():
            if not watcher_stop():
                rumps.notification("token_alert", "watcher 종료 실패", "launchctl unload에 실패했습니다. watcher가 계속 실행 중일 수 있습니다.")
        # KeepAlive=true라 self unload 안 하면 launchd가 즉시 재기동시킴
        subprocess.run(["launchctl", "unload", str(current_tray_plist_path())], capture_output=True)
        rumps.quit_application()


def already_running() -> bool:
    if TRAY_LOCK.exists():
        try:
            pid = int(TRAY_LOCK.read_text(encoding="utf-8"))
            subprocess.run(["kill", "-0", str(pid)], capture_output=True, check=True)
            return True
        except (ValueError, subprocess.CalledProcessError):
            pass
    TRAY_LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return False


if __name__ == "__main__":
    if already_running():
        sys.exit(0)
    try:
        app = TokenAlertApp()
        app.run()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        TRAY_LOCK.unlink(missing_ok=True)
