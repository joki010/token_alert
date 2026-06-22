#!/usr/bin/env python3
"""
token_alert 설치 스크립트 (macOS)

실행: python3 platform/macos/install.py
"""

import os
import shutil
import stat
import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve()  # token_alert 루트
WATCHER_PY = SCRIPT_DIR / "src" / "watcher.py"
CONFIG_ENV = SCRIPT_DIR / "config" / "config.env"
CONFIG_EXAMPLE = SCRIPT_DIR / "config" / "config.env.example"

# 고정 설치 경로
INSTALL_LIB_DIR = Path.home() / ".local" / "lib" / "token_alert" / "src"
INSTALLED_WATCHER_PY = INSTALL_LIB_DIR / "watcher.py"
INSTALLED_CONFIG_DIR = Path.home() / ".config" / "token-alert"
INSTALLED_CONFIG_ENV = INSTALLED_CONFIG_DIR / "config.env"

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_LABEL = "com.token-alert.watcher"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{PLIST_LABEL}.plist"

TRAY_PLIST_LABEL = "com.token-alert.tray"
TRAY_PLIST_PATH = LAUNCH_AGENTS_DIR / f"{TRAY_PLIST_LABEL}.plist"
TRAY_APP_DEST = Path.home() / "Applications" / "TokenAlertTray.app"
TRAY_BINARY = TRAY_APP_DEST / "Contents" / "MacOS" / "TokenAlertTray"
TRAY_AUTOSAVE_NAME = "TokenAlert"

LOG_DIR = Path.home() / ".claude"
STDOUT_LOG = LOG_DIR / "token_alert.log"
STDERR_LOG = LOG_DIR / "token_alert_error.log"
TRAY_STDOUT_LOG = LOG_DIR / "token_alert_tray.log"
TRAY_STDERR_LOG = LOG_DIR / "token_alert_tray_error.log"


def banner(msg: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {msg}")
    print(f"{'─' * 50}")


def ask_startup() -> bool:
    """시작 프로그램 등록 여부를 묻는다. y/Y 이면 True."""
    try:
        ans = input("로그인 시 자동 시작으로 등록할까요? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    return ans == "y"


def check_platform() -> None:
    if sys.platform != "darwin":
        print("❌ 이 설치 스크립트는 macOS 전용입니다.")
        print("   Windows는 platform/windows/install.py 를 사용하세요.")
        sys.exit(1)


def check_python() -> None:
    if sys.version_info < (3, 8):
        print(f"❌ Python 3.8 이상이 필요합니다. 현재: {sys.version}")
        sys.exit(1)
    print(f"✅ Python {sys.version.split()[0]}")


def check_config() -> None:
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

    placeholder_values = {
        "TELEGRAM_BOT_TOKEN": "1234567890:AA",
        "TELEGRAM_CHAT_ID": "123456789",
        "GITHUB_TOKEN": "ghp_xxx",
        "GITHUB_OWNER": "your_github_username",
    }

    missing = []
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
        sys.exit(1)

    print("✅ config.env 유효성 확인 완료")


def install_watcher_files() -> None:
    """watcher.py와 config.env를 고정 위치에 복사합니다."""
    INSTALL_LIB_DIR.mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    _shutil.copy2(str(WATCHER_PY), str(INSTALLED_WATCHER_PY))
    print(f"✅ watcher.py 설치: {INSTALLED_WATCHER_PY}")

    if CONFIG_ENV.exists():
        INSTALLED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _shutil.copy2(str(CONFIG_ENV), str(INSTALLED_CONFIG_ENV))
        INSTALLED_CONFIG_ENV.chmod(0o600)
        print(f"✅ config.env 설치: {INSTALLED_CONFIG_ENV} (권한: 600)")
    else:
        print("ℹ️  config.env 없음 — 설치 건너뜀 (환경 변수로 대체 가능)")


def create_plist() -> None:
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
        <string>{INSTALLED_WATCHER_PY}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardErrorPath</key>
    <string>{STDERR_LOG}</string>

    <key>ThrottleInterval</key>
    <integer>60</integer>

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
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)

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
    import time
    time.sleep(2)

    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"✅ 데몬 실행 중")
    else:
        print("⚠️  데몬 상태를 확인할 수 없습니다.")
        print(f"   tail -f {STDOUT_LOG}")


def ensure_py2app() -> None:
    """py2app이 venv에 없으면 설치."""
    venv_python = SCRIPT_DIR / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print("❌ .venv 가 없습니다. 먼저 python3 -m venv .venv 를 실행하세요.")
        sys.exit(1)

    result = subprocess.run(
        [str(venv_python), "-c", "import py2app"],
        capture_output=True,
    )
    if result.returncode != 0:
        print("⏳ py2app 설치 중...")
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "py2app"],
            check=True,
        )
    print("✅ py2app 준비 완료")


def build_tray_app() -> None:
    """py2app으로 TokenAlertTray.app 빌드."""
    venv_python = SCRIPT_DIR / ".venv" / "bin" / "python"
    setup_py = SCRIPT_DIR / "platform" / "macos" / "setup_tray.py"
    dist_app = SCRIPT_DIR / "dist" / "TokenAlertTray.app"

    print("⏳ TokenAlertTray.app 빌드 중 (수십 초 소요)...")
    result = subprocess.run(
        [str(venv_python), str(setup_py), "py2app"],
        cwd=str(SCRIPT_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("❌ py2app 빌드 실패:")
        print(result.stderr[-2000:])
        sys.exit(1)

    if not dist_app.exists():
        print(f"❌ 빌드 결과물을 찾을 수 없습니다: {dist_app}")
        sys.exit(1)

    print(f"✅ 빌드 완료: {dist_app}")


def install_tray_app() -> None:
    """빌드된 .app을 ~/Applications으로 이동, 트레이 LaunchAgent 등록."""
    dist_app = SCRIPT_DIR / "dist" / "TokenAlertTray.app"

    # 기존 트레이 중지
    subprocess.run(["launchctl", "unload", str(TRAY_PLIST_PATH)], capture_output=True)

    # ~/Applications 생성 및 .app 복사
    TRAY_APP_DEST.parent.mkdir(parents=True, exist_ok=True)
    if TRAY_APP_DEST.exists():
        shutil.rmtree(TRAY_APP_DEST)
    shutil.copytree(str(dist_app), str(TRAY_APP_DEST))

    # ad-hoc 서명 (Gatekeeper 없이 로컬 실행 가능하도록)
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(TRAY_APP_DEST)],
        capture_output=True,
    )

    # macOS Tahoe: controlcenter에 NSStatusItem 표시 등록
    subprocess.run([
        "defaults", "write", "com.apple.controlcenter",
        f"NSStatusItem Visible {TRAY_AUTOSAVE_NAME}", "-bool", "true",
    ], check=True)

    # 트레이 LaunchAgent plist 생성
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{TRAY_PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{TRAY_BINARY}</string>
    </array>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>StandardOutPath</key>
    <string>{TRAY_STDOUT_LOG}</string>

    <key>StandardErrorPath</key>
    <string>{TRAY_STDERR_LOG}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
"""
    TRAY_PLIST_PATH.write_text(plist_content, encoding="utf-8")

    result = subprocess.run(
        ["launchctl", "load", str(TRAY_PLIST_PATH)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ 트레이 LaunchAgent 로드 실패: {result.stderr}")
        sys.exit(1)

    print(f"✅ TokenAlertTray.app 설치 완료: {TRAY_APP_DEST}")
    print("✅ 트레이 LaunchAgent 등록 완료")


def print_summary(startup_registered: bool = True) -> None:
    banner("설치 완료!")
    if startup_registered:
        print("token_alert 가 백그라운드에서 실행 중입니다.\n")
        print("📋 유용한 명령어:")
        print(f"  launchctl list {PLIST_LABEL}")
        print(f"  launchctl list {TRAY_PLIST_LABEL}")
    else:
        print("token_alert 파일 설치가 완료되었습니다.")
        print("자동 시작 미등록 상태입니다.\n")
        print("📋 수동 실행 및 관리 방법:")
        print(f"  # 수동 데몬 등록 (시작 프로그램 등록)")
        print(f"  launchctl load {PLIST_PATH}")
        print(f"  # 백그라운드 직접 실행")
        print(f"  nohup {sys.executable} {INSTALLED_WATCHER_PY} >/dev/null 2>&1 &")

    print(f"""  # 로그 확인
  tail -f {STDOUT_LOG}

  # 한 번 테스트 실행
  python3 {WATCHER_PY} --dry-run --once --verbose

  # 완전 삭제
  python3 {SCRIPT_DIR}/platform/macos/uninstall.py
""")


def main() -> None:
    banner("token_alert 설치 시작 (macOS)")
    check_platform()
    check_python()
    check_config()
    banner("파일 설치 (고정 경로)")
    install_watcher_files()

    banner("시작 프로그램 등록")
    registered = ask_startup()
    if registered:
        banner("launchd 데몬 등록")
        create_plist()
        load_daemon()
        verify_running()
        banner("트레이 앱 빌드 및 설치")
        ensure_py2app()
        build_tray_app()
        install_tray_app()
    else:
        # plist 파일은 생성해 두되 load 하지 않음
        create_plist()
        print("ℹ️  자동 시작 등록을 건너뜁니다.")
        print(f"   나중에 등록하려면:")
        print(f"   launchctl load {PLIST_PATH}")

    print_summary(startup_registered=registered)


if __name__ == "__main__":
    main()
