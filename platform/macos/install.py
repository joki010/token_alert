#!/usr/bin/env python3
"""
token_alert 설치 스크립트 (macOS)

실행: python3 platform/macos/install.py
"""

import os
import json
import shutil
import sys
import subprocess
import tempfile
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
NOTIFY_SRC_DIR = SCRIPT_DIR / "platform" / "macos"
NOTIFY_INSTALL_DIR = Path.home() / ".local" / "lib" / "token_alert" / "notify"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

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
RUNTIME_MODULE_NAMES = ("watcher.py", "atomic_json.py", "activation.py", "scheduling.py")


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


def runtime_manifest() -> tuple[tuple[Path, Path], ...]:
    """설치할 공통 런타임 모듈의 명시적 매니페스트를 반환합니다."""
    return tuple(
        (SCRIPT_DIR / "src" / name, INSTALL_LIB_DIR / name)
        for name in RUNTIME_MODULE_NAMES
    )


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


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


def install_notify_scripts() -> None:
    """클로드코드 알림 스크립트를 고정 위치에 원자적으로 설치합니다."""
    script_names = ("notify.sh", "detect_terminal_app.sh")
    sources = [NOTIFY_SRC_DIR / name for name in script_names]
    missing = [source for source in sources if not source.is_file()]
    if missing:
        raise FileNotFoundError(f"알림 스크립트가 없습니다: {', '.join(str(path) for path in missing)}")

    for source in sources:
        destination = NOTIFY_INSTALL_DIR / source.name
        _atomic_copy(source, destination)
        destination.chmod(0o755)
        print(f"✅ 알림 스크립트 설치: {destination}")


def _notify_hook_specs() -> dict[str, dict[str, object]]:
    notify_dir = str(NOTIFY_INSTALL_DIR)
    return {
        "SessionStart": {
            "command": f"bash {notify_dir}/detect_terminal_app.sh",
            "timeout": 5,
        },
        "Stop": {
            "command": f"bash {notify_dir}/notify.sh '✅ Claude Code' 'Task completed'",
            "timeout": 10,
        },
    }


def patch_claude_settings_hooks() -> None:
    """Claude Code 전역 설정에 알림 훅을 멱등적으로 추가합니다."""
    try:
        if CLAUDE_SETTINGS_PATH.exists():
            with CLAUDE_SETTINGS_PATH.open("r", encoding="utf-8") as handle:
                settings = json.load(handle)
            if not isinstance(settings, dict):
                raise ValueError("최상위 값이 객체가 아닙니다")
        else:
            settings = {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"⚠️  Claude Code 설정을 읽지 못했습니다. 건너뜁니다: {exc}")
        return

    hooks = settings.get("hooks")
    if hooks is None:
        hooks = {}
        settings["hooks"] = hooks
    elif not isinstance(hooks, dict):
        print("⚠️  Claude Code 설정의 hooks 형식이 올바르지 않습니다. 건너뜁니다.")
        return

    for event, config in _notify_hook_specs().items():
        event_hooks = hooks.get(event)
        if event_hooks is None:
            event_hooks = []
            hooks[event] = event_hooks
        elif not isinstance(event_hooks, list):
            print(f"⚠️  Claude Code 설정의 {event} 훅 형식이 올바르지 않습니다. 건너뜁니다.")
            return

        existing_commands = {
            hook.get("command", "")
            for group in event_hooks
            if isinstance(group, dict) and isinstance(group.get("hooks", []), list)
            for hook in group.get("hooks", [])
            if isinstance(hook, dict)
        }
        command = config["command"]
        if command in existing_commands:
            print(f"⏭️  {event}: 이미 존재 (건너뜀)")
            continue

        event_hooks.append({
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": command,
                "timeout": config["timeout"],
            }],
        })
        print(f"✅ {event}: 훅 추가됨")

    try:
        content = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(CLAUDE_SETTINGS_PATH, content)
    except (OSError, TypeError, ValueError) as exc:
        print(f"⚠️  Claude Code 설정을 저장하지 못했습니다. 계속 진행합니다: {exc}")
        return
    print(f"✅ Claude Code 설정 업데이트 완료: {CLAUDE_SETTINGS_PATH}")


def _config_value(path: Path, key: str) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                current_key, _, value = stripped.partition("=")
                if current_key.strip() == key and value.strip():
                    return value.strip()
    except OSError:
        return None
    return None


def _absolute_path(value: str) -> str:
    return str(Path(value.strip()).expanduser().resolve())


def _persist_cli_path_if_missing() -> None:
    installed_value = _config_value(INSTALLED_CONFIG_ENV, "CLAUDE_CLI_PATH")
    if installed_value:
        print(f"✅ 기존 CLAUDE_CLI_PATH 보존: {installed_value}")
        return

    environment_value = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if environment_value:
        cli_path = _absolute_path(environment_value)
    else:
        detected = shutil.which("claude")
        cli_path = _absolute_path(detected) if detected else ""

    if not cli_path:
        print("⚠️  Claude CLI를 찾지 못했습니다. CLAUDE_CLI_PATH를 직접 설정하세요.")
        return

    current = ""
    if INSTALLED_CONFIG_ENV.exists():
        current = INSTALLED_CONFIG_ENV.read_text(encoding="utf-8")
    if current and not current.endswith("\n"):
        current += "\n"
    current += f"CLAUDE_CLI_PATH={cli_path}\n"
    _atomic_write_text(INSTALLED_CONFIG_ENV, current)
    print(f"✅ Claude CLI 경로 저장: {cli_path}")


def install_watcher_files() -> None:
    """공통 런타임과 사용자 설정을 고정 위치에 안전하게 설치합니다."""
    manifest = runtime_manifest()
    missing = [source for source, _ in manifest if not source.is_file()]
    if missing:
        raise FileNotFoundError(f"필수 런타임 모듈이 없습니다: {', '.join(str(path) for path in missing)}")

    INSTALL_LIB_DIR.mkdir(parents=True, exist_ok=True)
    for source, destination in manifest:
        _atomic_copy(source, destination)
        print(f"✅ 런타임 모듈 설치: {destination}")

    INSTALLED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if INSTALLED_CONFIG_ENV.exists():
        print(f"ℹ️  기존 config.env 보존: {INSTALLED_CONFIG_ENV}")
    elif CONFIG_ENV.exists():
        _atomic_copy(CONFIG_ENV, INSTALLED_CONFIG_ENV)
        INSTALLED_CONFIG_ENV.chmod(0o600)
        print(f"✅ config.env 설치: {INSTALLED_CONFIG_ENV} (권한: 600)")
    else:
        print("ℹ️  config.env 없음 — 환경 변수 전용 설치")

    _persist_cli_path_if_missing()


def verify_smoke() -> None:
    """설치 위치에서 네 런타임 모듈을 불러올 수 있는지 검증합니다."""
    import_line = "import watcher, atomic_json, activation, scheduling;"
    command = (
        "import sys; "
        f"sys.path.insert(0, {str(INSTALL_LIB_DIR)!r}); "
        f"{import_line} "
        "assert watcher and atomic_json and activation and scheduling"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=str(INSTALL_LIB_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"설치된 런타임 연동 검증 실패: {result.stderr.strip()}")
    print("✅ 설치된 런타임 연동 검증 완료")


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

    # ad-hoc 서명 (--deep은 Python.framework 포함 번들에서 실패 → 순서대로 서명)
    py_current = TRAY_APP_DEST / "Contents" / "Frameworks" / "Python.framework" / "Versions" / "Current"
    py_binary = py_current / "Python"
    for target in [py_binary, py_current, TRAY_APP_DEST]:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(target)],
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
    verify_smoke()

    banner("클로드코드 알림 스크립트 설치")
    install_notify_scripts()
    patch_claude_settings_hooks()

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
