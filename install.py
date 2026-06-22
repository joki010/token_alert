#!/usr/bin/env python3
"""
token_alert 설치 스크립트

실행: python3 install.py

수행 내용:
  1. config/config.env 유효성 확인
  2. macOS launchd plist 생성 (백그라운드 데몬 등록)
  3. launchctl load 로 즉시 시작
  4. 설치 확인 안내
"""

import os
import sys
import subprocess
from pathlib import Path

# ──────────────────────────────────────────
# 경로 상수
# ──────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
WATCHER_PY = SCRIPT_DIR / "src" / "watcher.py"
CONFIG_ENV = SCRIPT_DIR / "config" / "config.env"
CONFIG_EXAMPLE = SCRIPT_DIR / "config" / "config.env.example"

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_LABEL = "com.token-alert.watcher"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{PLIST_LABEL}.plist"

LOG_DIR = Path.home() / ".claude"
STDOUT_LOG = LOG_DIR / "token_alert.log"
STDERR_LOG = LOG_DIR / "token_alert_error.log"


def banner(msg: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {msg}")
    print(f"{'─' * 50}")


def check_platform() -> None:
    """macOS 여부 확인."""
    if sys.platform != "darwin":
        print("❌ 이 설치 스크립트는 macOS 전용입니다.")
        print("   Windows는 docs/install-guide.md 의 Windows 섹션을 참고하세요.")
        sys.exit(1)


def check_python() -> None:
    """Python 버전 확인."""
    if sys.version_info < (3, 8):
        print(f"❌ Python 3.8 이상이 필요합니다. 현재: {sys.version}")
        sys.exit(1)
    print(f"✅ Python {sys.version.split()[0]}")


def check_config() -> None:
    """config.env 존재 및 필수 키 확인."""
    if not CONFIG_ENV.exists():
        print(f"❌ 설정 파일이 없습니다: {CONFIG_ENV}")
        print(f"   아래 명령으로 템플릿을 복사한 뒤 값을 입력하세요:")
        print(f"   cp {CONFIG_EXAMPLE} {CONFIG_ENV}")
        sys.exit(1)

    required_keys = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN", "GITHUB_OWNER"]
    cfg: dict = {}

    with open(CONFIG_ENV, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip()

    missing = []
    placeholder_values = {
        "TELEGRAM_BOT_TOKEN": "1234567890:AA",
        "TELEGRAM_CHAT_ID": "123456789",
        "GITHUB_TOKEN": "ghp_xxx",
        "GITHUB_OWNER": "your_github_username",
    }

    for key in required_keys:
        val = cfg.get(key, "")
        if not val:
            missing.append(f"  - {key}: 값 없음")
        elif any(val.startswith(ph) for ph in [placeholder_values.get(key, "PLACEHOLDER")]):
            missing.append(f"  - {key}: 예시 값 그대로 (실제 값으로 교체 필요)")

    if missing:
        print("❌ config.env 에 실제 값이 필요한 항목이 있습니다:")
        for m in missing:
            print(m)
        print("\n  docs/telegram-setup.md, docs/github-setup.md 를 참고하세요.")
        sys.exit(1)

    print("✅ config.env 유효성 확인 완료")


def create_plist() -> None:
    """launchd plist 파일을 생성합니다."""
    python3 = sys.executable

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python3}</string>
        <string>{WATCHER_PY}</string>
    </array>

    <!--
        RunAtLoad: 로그인 시 자동 시작
        KeepAlive: 데몬이 죽으면 자동 재시작
        트레이 아이콘 없음 — 완전히 숨겨진 백그라운드 데몬
    -->
    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{STDOUT_LOG}</string>

    <key>StandardErrorPath</key>
    <string>{STDERR_LOG}</string>

    <!--
        ThrottleInterval: 크래시 반복 시 재시작 간격 (초)
    -->
    <key>ThrottleInterval</key>
    <integer>60</integer>

    <!--
        ProcessType: Background — macOS 가 백그라운드 프로세스로 분류
        메뉴 바/독 아이콘 없음
    -->
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
"""

    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "w", encoding="utf-8") as f:
        f.write(plist_content)

    print(f"✅ launchd plist 생성: {PLIST_PATH}")


def load_daemon() -> None:
    """launchctl 로 데몬을 등록하고 시작합니다."""
    # 이미 로드되어 있으면 먼저 언로드
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
    )

    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"❌ launchctl load 실패:")
        print(result.stderr)
        sys.exit(1)

    print("✅ 데몬 등록 및 시작 완료")


def verify_running() -> None:
    """데몬이 실행 중인지 확인합니다."""
    import time
    time.sleep(2)  # 시작 대기

    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"✅ 데몬 실행 중 (launchctl list 확인)")
    else:
        print("⚠️  데몬 상태를 확인할 수 없습니다. 로그를 확인하세요:")
        print(f"   tail -f {STDOUT_LOG}")


def print_summary() -> None:
    banner("설치 완료!")
    print(f"""
token_alert 가 백그라운드에서 실행 중입니다.

📋 유용한 명령어:
  # 데몬 상태 확인
  launchctl list {PLIST_LABEL}

  # 실시간 로그 확인
  tail -f {STDOUT_LOG}

  # 한 번 테스트 실행
  python3 {WATCHER_PY} --dry-run --once --verbose

  # 완전 삭제
  python3 {SCRIPT_DIR}/uninstall.py

📖 자세한 내용: {SCRIPT_DIR}/docs/
""")


def main() -> None:
    banner("token_alert 설치 시작")

    check_platform()
    check_python()
    check_config()

    banner("launchd 데몬 등록")
    create_plist()
    load_daemon()
    verify_running()

    print_summary()


if __name__ == "__main__":
    main()
