#!/usr/bin/env python3
"""
token_alert 설치 스크립트 (Windows)

실행: python platform/windows/install.py
"""

import os
import shutil
import sys
import subprocess
from pathlib import Path

SCRIPT_ROOT = Path(__file__).parent.parent.parent.resolve()  # token_alert 루트
WATCHER_PY = SCRIPT_ROOT / "src" / "watcher.py"
TRAY_PY = SCRIPT_ROOT / "platform" / "windows" / "tray.py"
CONFIG_ENV = SCRIPT_ROOT / "config" / "config.env"
CONFIG_EXAMPLE = SCRIPT_ROOT / "config" / "config.env.example"
LOG_DIR = Path.home() / ".claude"
STDOUT_LOG = LOG_DIR / "token_alert.log"
STDERR_LOG = LOG_DIR / "token_alert_error.log"

TASK_WATCHER = "TokenAlertWatcher"
TASK_TRAY = "TokenAlertTray"

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "TokenAlert"
TRAY_EXE_DEST = INSTALL_DIR / "TokenAlertTray.exe"


def banner(msg: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {msg}")
    print(f"{'─' * 50}")


def check_platform() -> None:
    if sys.platform != "win32":
        print("❌ 이 설치 스크립트는 Windows 전용입니다.")
        print("   macOS는 platform/macos/install.py 를 사용하세요.")
        sys.exit(1)


def check_python() -> None:
    if sys.version_info < (3, 8):
        print(f"❌ Python 3.8 이상이 필요합니다. 현재: {sys.version}")
        sys.exit(1)
    print(f"✅ Python {sys.version.split()[0]}")


def check_pystray() -> None:
    """pystray, Pillow 설치 여부 확인."""
    missing = []
    try:
        import pystray  # noqa: F401
    except ImportError:
        missing.append("pystray")
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        missing.append("Pillow")

    if missing:
        pkgs = " ".join(missing)
        print(f"❌ 필수 패키 미설치: {', '.join(missing)}")
        print(f"   아래 명령으로 설치 후 다시 실행하세요:")
        print(f"   pip install {pkgs}")
        sys.exit(1)

    print("✅ pystray, Pillow 설치 확인")


def check_config() -> None:
    if not CONFIG_ENV.exists():
        print(f"❌ 설정 파일이 없습니다: {CONFIG_ENV}")
        print(f"   아래 명령으로 템플릿을 복사한 뒤 값을 입력하세요:")
        print(f"   copy {CONFIG_EXAMPLE} {CONFIG_ENV}")
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


def _register_task(task_name: str, script: Path, use_pythonw: bool = False) -> None:
    """Task Scheduler에 로그인 시 자동 실행 작업을 등록한다."""
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    exe = str(pythonw) if (use_pythonw and pythonw.exists()) else sys.executable

    tr = f'"{exe}" "{script}"'
    result = subprocess.run(
        [
            "schtasks", "/create",
            "/tn", task_name,
            "/tr", tr,
            "/sc", "ONLOGON",
            "/rl", "LIMITED",
            "/f",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"❌ Task 등록 실패 ({task_name}):")
        print(result.stderr.strip())
        sys.exit(1)

    print(f"✅ Task 등록: {task_name}")


def register_tasks() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _register_task(TASK_WATCHER, WATCHER_PY, use_pythonw=False)


def start_tasks() -> None:
    for task in [TASK_WATCHER, TASK_TRAY]:
        result = subprocess.run(
            ["schtasks", "/run", "/tn", task],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"✅ 즉시 시작: {task}")
        else:
            print(f"⚠️  시작 실패 ({task}): {result.stderr.strip()}")


def verify_running() -> None:
    import time
    time.sleep(2)
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_WATCHER, "/fo", "LIST"],
        capture_output=True,
        text=True,
    )
    if "Running" in result.stdout:
        print(f"✅ {TASK_WATCHER} 실행 중")
    else:
        print(f"⚠️  {TASK_WATCHER} 상태를 확인하세요:")
        print(f"   schtasks /query /tn {TASK_WATCHER}")
        print(f"   로그: {STDOUT_LOG}")


def print_summary() -> None:
    banner("설치 완료!")
    print(f"""
token_alert 가 백그라운드에서 실행 중입니다.

📋 유용한 명령어:
  # 상태 확인
  schtasks /query /tn {TASK_WATCHER}

  # 로그 확인
  type %USERPROFILE%\\.claude\\token_alert.log

  # 한 번 테스트 실행
  python {WATCHER_PY} --dry-run --once --verbose

  # 완전 삭제
  python {SCRIPT_ROOT}\\platform\\windows\\uninstall.py
""")


def convert_icon_to_ico() -> None:
    from PIL import Image
    src = SCRIPT_ROOT / "claudecode-tray.png"
    dst = SCRIPT_ROOT / "claudecode-tray.ico"
    if not dst.exists():
        img = Image.open(src).resize((256, 256), Image.LANCZOS)
        img.save(str(dst), format="ICO")
    print("✅ ICO 변환 완료")


def ensure_pyinstaller() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import PyInstaller"],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    print("✅ PyInstaller 준비 완료")


def build_tray_exe() -> None:
    spec = SCRIPT_ROOT / "platform" / "windows" / "setup_tray.spec"
    dist_exe = SCRIPT_ROOT / "dist" / "TokenAlertTray.exe"

    print("⏳ TokenAlertTray.exe 빌드 중...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--distpath", str(SCRIPT_ROOT / "dist"),
         "--workpath", str(SCRIPT_ROOT / "build"), str(spec)],
        cwd=str(SCRIPT_ROOT),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("❌ PyInstaller 빌드 실패:")
        print(result.stderr[-2000:])
        sys.exit(1)
    print(f"✅ 빌드 완료: {dist_exe}")


def install_tray_exe() -> None:
    dist_exe = SCRIPT_ROOT / "dist" / "TokenAlertTray.exe"
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(dist_exe), str(TRAY_EXE_DEST))

    # 기존 Task 삭제 후 재등록
    subprocess.run(["schtasks", "/delete", "/tn", TASK_TRAY, "/f"], capture_output=True)
    result = subprocess.run([
        "schtasks", "/create",
        "/tn", TASK_TRAY,
        "/tr", f'"{TRAY_EXE_DEST}"',
        "/sc", "ONLOGON",
        "/rl", "LIMITED",
        "/f",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Task 등록 실패: {result.stderr.strip()}")
        sys.exit(1)
    print(f"✅ TokenAlertTray.exe 설치 완료: {TRAY_EXE_DEST}")


def main() -> None:
    banner("token_alert 설치 시작 (Windows)")
    check_platform()
    check_python()
    check_pystray()
    check_config()
    
    banner("트레이 앱 빌드 및 설치")
    ensure_pyinstaller()
    convert_icon_to_ico()
    build_tray_exe()
    install_tray_exe()
    
    banner("Task Scheduler 등록")
    register_tasks()
    start_tasks()
    verify_running()
    print_summary()


if __name__ == "__main__":
    main()
