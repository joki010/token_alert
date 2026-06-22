#!/usr/bin/env python3
"""
token_alert 설치 스크립트 (Windows)

실행: python platform/windows/install.py
"""

import os
import shutil
import sys
import subprocess
import winreg
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

# 고정 설치 경로
INSTALL_LIB_DIR = Path.home() / ".local" / "lib" / "token_alert" / "src"
INSTALLED_WATCHER_PY = INSTALL_LIB_DIR / "watcher.py"
INSTALLED_CONFIG_DIR = Path.home() / ".config" / "token-alert"
INSTALLED_CONFIG_ENV = INSTALLED_CONFIG_DIR / "config.env"
TRAY_EXE_DEST = INSTALL_DIR / "TokenAlertTray.exe"


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


def install_watcher_files() -> None:
    """watcher.py와 config.env를 고정 위치에 복사합니다."""
    INSTALL_LIB_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(WATCHER_PY), str(INSTALLED_WATCHER_PY))
    print(f"✅ watcher.py 설치: {INSTALLED_WATCHER_PY}")

    if CONFIG_ENV.exists():
        INSTALLED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(CONFIG_ENV), str(INSTALLED_CONFIG_ENV))
        # Windows에는 chmod 없음 — NTFS 권한은 기본적으로 소유자만 접근
        print(f"✅ config.env 설치: {INSTALLED_CONFIG_ENV}")
    else:
        print("ℹ️  config.env 없음 — 설치 건너뜀 (환경 변수로 대체 가능)")


_RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


def _register_startup(name: str, cmd: str) -> None:
    """HKCU 시작 프로그램 레지스트리에 항목을 등록한다. 관리자 권한 불필요."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
    print(f"✅ 시작 프로그램 등록: {name}")


def register_tasks() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    exe = str(pythonw) if pythonw.exists() else sys.executable
    _register_startup(TASK_WATCHER, f'"{exe}" "{INSTALLED_WATCHER_PY}"')
    _register_startup(TASK_TRAY, f'"{TRAY_EXE_DEST}"')


def start_tasks() -> None:
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    exe = str(pythonw) if pythonw.exists() else sys.executable
    _NO_WIN = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen([exe, str(INSTALLED_WATCHER_PY)], creationflags=_NO_WIN, close_fds=True)
        print(f"✅ 즉시 시작: {TASK_WATCHER}")
    except Exception as e:
        print(f"⚠️  시작 실패 ({TASK_WATCHER}): {e}")
    try:
        subprocess.Popen([str(TRAY_EXE_DEST)], creationflags=subprocess.DETACHED_PROCESS, close_fds=True)
        print(f"✅ 즉시 시작: {TASK_TRAY}")
    except Exception as e:
        print(f"⚠️  시작 실패 ({TASK_TRAY}): {e}")


def verify_running() -> None:
    import time
    time.sleep(2)
    pid_file = Path.home() / ".token_alert.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            print(f"✅ {TASK_WATCHER} 실행 중 (PID: {pid})")
            return
        except (OSError, ValueError):
            pass
    print(f"⚠️  {TASK_WATCHER} 상태를 확인하세요:")
    print(f"   로그: {STDOUT_LOG}")


def print_summary(startup_registered: bool = True) -> None:
    banner("설치 완료!")
    if startup_registered:
        print("token_alert 가 백그라운드에서 실행 중입니다.\n")
    else:
        print("token_alert 파일 설치가 완료되었습니다.")
        print("자동 시작 미등록 상태입니다.\n")
        print("수동으로 실행하려면:")
        print(f"  pythonw.exe {INSTALLED_WATCHER_PY}")
        print(f"  {TRAY_EXE_DEST}\n")

    print(f"""📋 유용한 명령어:
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


def _copy_tray_exe() -> None:
    """빌드된 exe를 설치 디렉토리로 복사한다."""
    dist_exe = SCRIPT_ROOT / "dist" / "TokenAlertTray.exe"
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(dist_exe), str(TRAY_EXE_DEST))
    print(f"✅ TokenAlertTray.exe 복사 완료: {TRAY_EXE_DEST}")


def install_tray_exe() -> None:
    """exe 복사 (시작 프로그램 등록은 register_tasks()에서 수행)."""
    _copy_tray_exe()


def main() -> None:
    banner("token_alert 설치 시작 (Windows)")
    check_platform()
    check_python()
    check_pystray()
    check_config()

    banner("트레이 앱 빌드")
    ensure_pyinstaller()
    convert_icon_to_ico()
    build_tray_exe()

    banner("파일 설치 (고정 경로)")
    install_watcher_files()
    _copy_tray_exe()           # exe 복사는 항상 수행

    banner("시작 프로그램 등록")
    registered = ask_startup()
    if registered:
        register_tasks()   # HKCU Run 레지스트리에 watcher + tray 등록
        start_tasks()
        verify_running()
    else:
        print("ℹ️  자동 시작 등록을 건너뜁니다.")
        print(f"   나중에 등록하려면 install.py 를 다시 실행하세요.")

    print_summary(startup_registered=registered)


if __name__ == "__main__":
    main()
