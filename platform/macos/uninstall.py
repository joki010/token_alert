#!/usr/bin/env python3
"""
token_alert 완전 삭제 스크립트 (macOS)

실행: python3 platform/macos/uninstall.py
"""

import shutil
import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve()  # token_alert 루트
PLIST_LABEL = "com.token-alert.watcher"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
STATE_FILE = Path.home() / ".token_alert_state.json"
STDOUT_LOG = Path.home() / ".claude" / "token_alert.log"
STDERR_LOG = Path.home() / ".claude" / "token_alert_error.log"
CONFIG_ENV = SCRIPT_DIR / "config" / "config.env"

TRAY_PLIST_LABEL = "com.token-alert.tray"
TRAY_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{TRAY_PLIST_LABEL}.plist"
TRAY_APP_DEST = Path.home() / "Applications" / "TokenAlertTray.app"
TRAY_STDOUT_LOG = Path.home() / ".claude" / "token_alert_tray.log"
TRAY_STDERR_LOG = Path.home() / ".claude" / "token_alert_tray_error.log"


def banner(msg: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {msg}")
    print(f"{'─' * 50}")


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def stop_daemon() -> None:
    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("ℹ️  데몬이 실행 중이 아닙니다")
        return

    result = subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print("✅ 데몬 중지 완료")
    else:
        print(f"⚠️  데몬 중지 중 경고: {result.stderr.strip()}")


def remove_plist() -> None:
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        print(f"✅ plist 삭제: {PLIST_PATH}")
    else:
        print(f"ℹ️  plist 파일 없음: {PLIST_PATH}")


def remove_state_file() -> None:
    if STATE_FILE.exists():
        if confirm(f"상태 파일을 삭제할까요? ({STATE_FILE})"):
            STATE_FILE.unlink()
            print(f"✅ 상태 파일 삭제: {STATE_FILE}")
        else:
            print("↩️  상태 파일 보존")
    else:
        print(f"ℹ️  상태 파일 없음: {STATE_FILE}")


def remove_logs() -> None:
    logs = [STDOUT_LOG, STDERR_LOG]
    existing = [p for p in logs if p.exists()]

    if not existing:
        print("ℹ️  로그 파일 없음")
        return

    if confirm(f"로그 파일을 삭제할까요? ({', '.join(str(p) for p in existing)})"):
        for log in existing:
            log.unlink()
            print(f"✅ 로그 삭제: {log}")
    else:
        print("↩️  로그 파일 보존")


def stop_tray() -> None:
    result = subprocess.run(
        ["launchctl", "list", TRAY_PLIST_LABEL],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("ℹ️  트레이가 실행 중이 아닙니다")
        return

    subprocess.run(["launchctl", "unload", str(TRAY_PLIST_PATH)], capture_output=True)
    print("✅ 트레이 중지 완료")


def remove_tray_plist() -> None:
    if TRAY_PLIST_PATH.exists():
        TRAY_PLIST_PATH.unlink()
        print(f"✅ 트레이 plist 삭제: {TRAY_PLIST_PATH}")
    else:
        print(f"ℹ️  트레이 plist 없음: {TRAY_PLIST_PATH}")


def remove_tray_app() -> None:
    if TRAY_APP_DEST.exists():
        shutil.rmtree(TRAY_APP_DEST)
        print(f"✅ 트레이 앱 삭제: {TRAY_APP_DEST}")
    else:
        print(f"ℹ️  트레이 앱 없음: {TRAY_APP_DEST}")

    # controlcenter 표시 설정 초기화
    subprocess.run([
        "defaults", "delete", "com.apple.controlcenter", "NSStatusItem Visible TokenAlert",
    ], capture_output=True)

    # 트레이 로그
    for log in [TRAY_STDOUT_LOG, TRAY_STDERR_LOG]:
        if log.exists():
            log.unlink()
            print(f"✅ 트레이 로그 삭제: {log}")


def remind_config() -> None:
    if CONFIG_ENV.exists():
        print(f"""
⚠️  보안 주의:
  config.env 에는 텔레그램 봇 토큰과 GitHub 토큰이 있습니다.
  완전히 제거하려면: rm {CONFIG_ENV}
""")


def main() -> None:
    banner("token_alert 완전 삭제 (macOS)")
    if not confirm("계속 진행할까요?"):
        print("취소되었습니다.")
        sys.exit(0)

    banner("데몬 중지")
    stop_daemon()

    banner("트레이 앱 중지 및 제거")
    stop_tray()
    remove_tray_plist()
    remove_tray_app()

    banner("파일 삭제")
    remove_plist()
    remove_state_file()
    remove_logs()

    remind_config()
    banner("삭제 완료!")


if __name__ == "__main__":
    main()
