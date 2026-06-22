#!/usr/bin/env python3
"""
token_alert 완전 삭제 스크립트 (Windows)

실행: python platform\\windows\\uninstall.py
"""

import os
import shutil
import sys
import subprocess
from pathlib import Path

SCRIPT_ROOT = Path(__file__).parent.parent.parent.resolve()
CONFIG_ENV = SCRIPT_ROOT / "config" / "config.env"
STATE_FILE = Path.home() / ".token_alert_state.json"
STDOUT_LOG = Path.home() / ".claude" / "token_alert.log"
STDERR_LOG = Path.home() / ".claude" / "token_alert_error.log"

TASK_WATCHER = "TokenAlertWatcher"
TASK_TRAY = "TokenAlertTray"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "TokenAlert"

# 고정 설치 경로
INSTALL_LIB_DIR = Path.home() / ".local" / "lib" / "token_alert"
INSTALLED_CONFIG_ENV = Path.home() / ".config" / "token-alert" / "config.env"


def banner(msg: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {msg}")
    print(f"{'─' * 50}")


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def check_platform() -> None:
    if sys.platform != "win32":
        print("❌ 이 스크립트는 Windows 전용입니다.")
        sys.exit(1)


def stop_and_delete_task(task_name: str) -> None:
    # 실행 중이면 먼저 중지
    subprocess.run(
        ["schtasks", "/end", "/tn", task_name],
        capture_output=True,
    )

    result = subprocess.run(
        ["schtasks", "/delete", "/tn", task_name, "/f"],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"✅ Task 삭제: {task_name}")
    elif "ERROR: The system cannot find the file specified" in result.stderr or \
         "존재하지 않습니다" in result.stderr or \
         "cannot find" in result.stderr.lower():
        print(f"ℹ️  Task 없음 (이미 삭제됨): {task_name}")
    else:
        print(f"⚠️  Task 삭제 중 경고 ({task_name}): {result.stderr.strip()}")


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


def remove_tray_exe() -> None:
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
        print(f"✅ 트레이 앱 삭제: {INSTALL_DIR}")
    else:
        print(f"ℹ️  트레이 앱 없음: {INSTALL_DIR}")


def remove_installed_files() -> None:
    """고정 경로에 설치된 파일을 삭제합니다."""
    if INSTALL_LIB_DIR.exists():
        if confirm(f"설치된 watcher 파일을 삭제할까요? ({INSTALL_LIB_DIR})"):
            shutil.rmtree(INSTALL_LIB_DIR)
            print(f"✅ 설치 디렉터리 삭제: {INSTALL_LIB_DIR}")
        else:
            print("↩️  설치 디렉터리 보존")
    else:
        print(f"ℹ️  설치 디렉터리 없음: {INSTALL_LIB_DIR}")

    if INSTALLED_CONFIG_ENV.exists():
        if confirm(f"설치된 config.env를 삭제할까요? ({INSTALLED_CONFIG_ENV})"):
            INSTALLED_CONFIG_ENV.unlink()
            try:
                INSTALLED_CONFIG_ENV.parent.rmdir()
            except OSError:
                pass
            print(f"✅ 설치된 config.env 삭제: {INSTALLED_CONFIG_ENV}")
        else:
            print("↩️  설치된 config.env 보존")
    else:
        print(f"ℹ️  설치된 config.env 없음: {INSTALLED_CONFIG_ENV}")


def remind_config() -> None:
    if CONFIG_ENV.exists():
        print(f"""
⚠️  보안 주의:
  config.env 에는 텔레그램 봇 토큰과 GitHub 토큰이 있습니다.
  완전히 제거하려면: del {CONFIG_ENV}
""")


def main() -> None:
    check_platform()
    banner("token_alert 완전 삭제 (Windows)")
    print(f"\n삭제 대상:")
    print(f"  • Task Scheduler 작업: {TASK_WATCHER}, {TASK_TRAY}")
    print()

    if not confirm("계속 진행할까요?"):
        print("취소되었습니다.")
        sys.exit(0)

    banner("Task Scheduler 작업 삭제")
    stop_and_delete_task(TASK_WATCHER)
    stop_and_delete_task(TASK_TRAY)

    banner("파일 삭제")
    remove_state_file()
    remove_logs()
    remove_tray_exe()
    remove_installed_files()

    remind_config()
    banner("삭제 완료!")


if __name__ == "__main__":
    main()
