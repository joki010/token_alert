#!/usr/bin/env python3
"""
token_alert 완전 삭제 스크립트

실행: python3 uninstall.py

수행 내용:
  1. launchctl unload 로 데몬 중지
  2. launchd plist 파일 삭제
  3. 상태 파일 삭제 (선택)
  4. 로그 파일 삭제 (선택)

※ 이 스크립트는 config.env (토큰 정보) 는 삭제하지 않습니다.
  완전히 지우려면 'config/config.env' 파일을 직접 삭제하세요.
"""

import sys
import subprocess
from pathlib import Path

# ──────────────────────────────────────────
# 경로 상수
# ──────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
PLIST_LABEL = "com.token-alert.watcher"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
STATE_FILE = Path.home() / ".token_alert_state.json"
STDOUT_LOG = Path.home() / ".claude" / "token_alert.log"
STDERR_LOG = Path.home() / ".claude" / "token_alert_error.log"
CONFIG_ENV = SCRIPT_DIR / "config" / "config.env"


def banner(msg: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {msg}")
    print(f"{'─' * 50}")


def confirm(prompt: str) -> bool:
    """사용자 확인을 받습니다."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def stop_daemon() -> None:
    """launchctl 로 데몬을 중지합니다."""
    # 실행 중인지 확인
    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("ℹ️  데몬이 실행 중이 아닙니다 (이미 중지됨)")
        return

    # unload
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
    """launchd plist 파일을 삭제합니다."""
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        print(f"✅ plist 삭제: {PLIST_PATH}")
    else:
        print(f"ℹ️  plist 파일 없음 (이미 삭제됨): {PLIST_PATH}")


def remove_state_file() -> None:
    """상태 파일을 삭제합니다."""
    if STATE_FILE.exists():
        if confirm(f"상태 파일을 삭제할까요? ({STATE_FILE})"):
            STATE_FILE.unlink()
            print(f"✅ 상태 파일 삭제: {STATE_FILE}")
        else:
            print("↩️  상태 파일 보존")
    else:
        print(f"ℹ️  상태 파일 없음: {STATE_FILE}")


def remove_logs() -> None:
    """로그 파일을 삭제합니다."""
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


def remind_config() -> None:
    """config.env 수동 삭제 안내."""
    if CONFIG_ENV.exists():
        print(f"""
⚠️  보안 주의:
  config.env 파일에는 텔레그램 봇 토큰과 GitHub 토큰이 저장되어 있습니다.
  완전히 제거하려면 아래 명령을 실행하세요:

    rm {CONFIG_ENV}

  또는 GitHub 설정에서 Secrets 도 삭제하세요:
  https://github.com/YOUR_USERNAME/token_alert/settings/secrets/actions
""")


def print_summary() -> None:
    banner("삭제 완료!")
    print(f"""
token_alert 가 제거되었습니다.

남은 작업 (선택):
  1. config.env 삭제:
     rm {CONFIG_ENV}

  2. GitHub repository 삭제 (필요 시):
     https://github.com/YOUR_USERNAME/token_alert/settings

  3. Telegram Bot 삭제 (BotFather에서 /deletebot):
     https://t.me/BotFather

  4. GitHub PAT 폐기:
     https://github.com/settings/tokens

자세한 내용: {SCRIPT_DIR}/docs/uninstall-guide.md
""")


def main() -> None:
    banner("token_alert 완전 삭제")

    print("\n다음 항목을 삭제합니다:")
    print(f"  • launchd 데몬 중지")
    print(f"  • plist 파일: {PLIST_PATH}")
    print()

    if not confirm("계속 진행할까요?"):
        print("취소되었습니다.")
        sys.exit(0)

    banner("데몬 중지")
    stop_daemon()

    banner("파일 삭제")
    remove_plist()
    remove_state_file()
    remove_logs()

    remind_config()
    print_summary()


if __name__ == "__main__":
    main()
