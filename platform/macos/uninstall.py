#!/usr/bin/env python3
"""
token_alert 완전 삭제 스크립트 (macOS)

실행: python3 platform/macos/uninstall.py
"""

import json
import os
import shutil
import sys
import subprocess
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve()  # token_alert 루트
PLIST_LABEL = "com.token-alert.watcher"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
STATE_FILE = Path.home() / ".token_alert_state.json"
STDOUT_LOG = Path.home() / ".claude" / "token_alert.log"
STDERR_LOG = Path.home() / ".claude" / "token_alert_error.log"
CONFIG_ENV = SCRIPT_DIR / "config" / "config.env"

# 고정 설치 경로
INSTALL_LIB_DIR = Path.home() / ".local" / "lib" / "token_alert"
INSTALLED_CONFIG_ENV = Path.home() / ".config" / "token-alert" / "config.env"
POLICY_FILE = Path.home() / ".config" / "token-alert" / "activation-policy.json"
NOTIFY_INSTALL_DIR = Path.home() / ".local" / "lib" / "token_alert" / "notify"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
NOTIFY_POLICY_FILE = Path.home() / ".config" / "token-alert" / "notify-policy.json"
NOTIFY_APP_CACHE_FILE = Path.home() / ".config" / "token-alert" / ".notify_app"

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


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _notify_hook_commands() -> set[str]:
    notify_dir = str(NOTIFY_INSTALL_DIR)
    return {
        f"bash {notify_dir}/detect_terminal_app.sh",
        f"bash {notify_dir}/notify.sh '✅ Claude Code' 'Task completed'",
    }


def remove_notify_hooks() -> None:
    """settings.json에서 token_alert가 추가한 알림 훅만 제거합니다."""
    if not CLAUDE_SETTINGS_PATH.exists():
        print(f"ℹ️  Claude Code 설정 파일 없음: {CLAUDE_SETTINGS_PATH}")
        return

    try:
        with CLAUDE_SETTINGS_PATH.open("r", encoding="utf-8") as handle:
            settings = json.load(handle)
        if not isinstance(settings, dict):
            raise ValueError("최상위 값이 객체가 아닙니다")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"⚠️  Claude Code 설정을 읽지 못했습니다. 훅을 건너뜁니다: {exc}")
        return

    hooks = settings.get("hooks")
    if hooks is None:
        print("ℹ️  Claude Code 알림 훅이 없습니다")
        return
    if not isinstance(hooks, dict):
        print("⚠️  Claude Code 설정의 hooks 형식이 올바르지 않습니다. 훅을 건너뜁니다.")
        return

    target_commands = _notify_hook_commands()
    removed = 0
    changed = False
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue

        new_groups = []
        event_changed = False
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                new_groups.append(group)
                continue

            original_hooks = group.get("hooks", [])
            new_hooks = [
                hook for hook in original_hooks
                if not (
                    isinstance(hook, dict)
                    and hook.get("command", "") in target_commands
                )
            ]
            removed_count = len(original_hooks) - len(new_hooks)
            if removed_count == 0:
                new_groups.append(group)
                continue

            changed = True
            event_changed = True
            removed += removed_count
            if new_hooks:
                updated_group = dict(group)
                updated_group["hooks"] = new_hooks
                new_groups.append(updated_group)

        if event_changed:
            hooks[event] = new_groups

    if not changed:
        print("ℹ️  token_alert 알림 훅이 없습니다")
        return

    try:
        content = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(CLAUDE_SETTINGS_PATH, content)
    except (OSError, TypeError, ValueError) as exc:
        print(f"⚠️  Claude Code 설정을 저장하지 못했습니다. 계속 진행합니다: {exc}")
        return
    print(f"✅ Claude Code 알림 훅 {removed}개 제거 완료")


def remove_notify_scripts() -> None:
    """설치된 알림 스크립트와 터미널 앱 캐시를 삭제합니다."""
    targets = [path for path in (NOTIFY_INSTALL_DIR, NOTIFY_APP_CACHE_FILE) if path.exists()]
    if not targets:
        print("ℹ️  클로드코드 알림 스크립트와 캐시가 없습니다")
        return

    target_text = ", ".join(str(path) for path in targets)
    if not confirm(f"클로드코드 알림 스크립트를 삭제할까요? ({target_text})"):
        print("↩️  클로드코드 알림 스크립트 보존")
        return

    if NOTIFY_INSTALL_DIR.exists():
        if NOTIFY_INSTALL_DIR.is_dir() and not NOTIFY_INSTALL_DIR.is_symlink():
            shutil.rmtree(NOTIFY_INSTALL_DIR)
        else:
            NOTIFY_INSTALL_DIR.unlink()
        print(f"✅ 알림 스크립트 디렉터리 삭제: {NOTIFY_INSTALL_DIR}")
    if NOTIFY_APP_CACHE_FILE.exists():
        NOTIFY_APP_CACHE_FILE.unlink()
        print(f"✅ 터미널 앱 캐시 삭제: {NOTIFY_APP_CACHE_FILE}")


def remove_notify_policy() -> None:
    """클로드코드 알림 정책 파일을 사용자 확인 뒤 삭제합니다."""
    if not NOTIFY_POLICY_FILE.exists():
        print(f"ℹ️  클로드코드 알림 정책 파일 없음: {NOTIFY_POLICY_FILE}")
        return

    if confirm(f"클로드코드 알림 정책 파일을 삭제할까요? ({NOTIFY_POLICY_FILE})"):
        NOTIFY_POLICY_FILE.unlink()
        print(f"✅ 클로드코드 알림 정책 파일 삭제: {NOTIFY_POLICY_FILE}")
    else:
        print("↩️  클로드코드 알림 정책 파일 보존")


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

    if POLICY_FILE.exists():
        if confirm(f"자동 창 시작 정책 파일을 삭제할까요? ({POLICY_FILE})"):
            POLICY_FILE.unlink()
            print(f"✅ 정책 파일 삭제: {POLICY_FILE}")
        else:
            print("↩️  정책 파일 보존")
    else:
        print(f"ℹ️  정책 파일 없음: {POLICY_FILE}")


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

    banner("클로드코드 알림 제거")
    remove_notify_hooks()
    remove_notify_scripts()
    remove_notify_policy()

    remove_installed_files()

    remind_config()
    banner("삭제 완료!")


if __name__ == "__main__":
    main()
