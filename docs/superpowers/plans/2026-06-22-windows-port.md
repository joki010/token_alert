# Windows 이식 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** token_alert를 Windows에서도 동일하게 동작하도록 macOS 파일을 `platform/macos/`로 이동하고 `platform/windows/`에 Task Scheduler + pystray 기반 구현을 추가한다.

**Architecture:** `src/watcher.py`는 변경 없이 공통으로 사용한다. 플랫폼별 데몬 등록(launchd vs Task Scheduler)과 트레이 앱(rumps vs pystray)을 `platform/macos/`, `platform/windows/`로 격리한다. config/config.env는 양 플랫폼이 공유한다.

**Tech Stack:** Python 3.8+, pystray, Pillow, Windows Task Scheduler (schtasks), subprocess

---

## 파일 구조

| 파일 | 작업 |
|------|------|
| `platform/macos/install.py` | 기존 `install.py` 이동 + SCRIPT_DIR 경로 수정 |
| `platform/macos/uninstall.py` | 기존 `uninstall.py` 이동 + SCRIPT_DIR 경로 수정 |
| `platform/macos/tray.py` | 기존 `tray.py` 이동 + ICON 경로 수정 |
| `platform/windows/install.py` | 신규: Task Scheduler 등록 |
| `platform/windows/uninstall.py` | 신규: Task Scheduler 제거 |
| `platform/windows/tray.py` | 신규: pystray 기반 트레이 앱 |
| `docs/install-windows.md` | 신규: Windows 설치/제거 가이드 |
| `README.md` | macOS/Windows 섹션 분리 |

---

## Task 1: macOS 파일을 platform/macos/로 이동

**Files:**
- Create: `platform/macos/install.py`
- Create: `platform/macos/uninstall.py`
- Create: `platform/macos/tray.py`
- Delete: 루트의 `install.py`, `uninstall.py`, `tray.py`

- [ ] **Step 1: platform/macos/ 디렉토리 생성 및 install.py 이동**

```bash
mkdir -p platform/macos
```

`platform/macos/install.py` 작성 — 기존 `install.py`에서 `SCRIPT_DIR` 한 줄만 변경:

```python
#!/usr/bin/env python3
"""
token_alert 설치 스크립트 (macOS)

실행: python3 platform/macos/install.py
"""

import os
import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve()  # token_alert 루트
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
        <string>{WATCHER_PY}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{STDOUT_LOG}</string>

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


def print_summary() -> None:
    banner("설치 완료!")
    print(f"""
token_alert 가 백그라운드에서 실행 중입니다.

📋 유용한 명령어:
  launchctl list {PLIST_LABEL}
  tail -f {STDOUT_LOG}
  python3 {WATCHER_PY} --dry-run --once --verbose
  python3 {SCRIPT_DIR}/platform/macos/uninstall.py
""")


def main() -> None:
    banner("token_alert 설치 시작 (macOS)")
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
```

- [ ] **Step 2: platform/macos/uninstall.py 작성**

```python
#!/usr/bin/env python3
"""
token_alert 완전 삭제 스크립트 (macOS)

실행: python3 platform/macos/uninstall.py
"""

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

    banner("파일 삭제")
    remove_plist()
    remove_state_file()
    remove_logs()

    remind_config()
    banner("삭제 완료!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: platform/macos/tray.py 작성**

기존 `tray.py`에서 ICON 경로를 루트 기준으로 수정:

```python
#!/usr/bin/env python3
"""
token_alert 메뉴 막대 트레이 앱 (macOS)
rumps 기반
"""

import os
import subprocess
import sys
from pathlib import Path

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

SCRIPT_ROOT = Path(__file__).parent.parent.parent.resolve()  # token_alert 루트
TRAY_LOCK = Path("/tmp/token_alert_tray.pid")
LABEL = "com.token-alert.watcher"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
ICON = SCRIPT_ROOT / "claudecode-tray.png"
ICON_INACTIVE = SCRIPT_ROOT / "claudecode-tray-inactive.png"
LOG_FILE = Path.home() / ".claude" / "token_alert.log"

UPDATE_INTERVAL = 10


def is_watcher_running() -> bool:
    result = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True, text=True, encoding="utf-8"
    )
    return '"PID"' in result.stdout


def watcher_start() -> None:
    subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True)


def watcher_stop() -> None:
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)


class TokenAlertApp(rumps.App):
    def __init__(self):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        icon = str(ICON) if ICON.exists() else None
        super().__init__("token_alert", title="", icon=icon, quit_button=None)

        self.status_item = rumps.MenuItem("확인 중...")
        self.status_item.set_callback(None)
        self.toggle_item = rumps.MenuItem("감시 중지", callback=self.toggle_watcher)

        self.menu = [
            self.status_item,
            None,
            self.toggle_item,
            rumps.MenuItem("로그 열기", callback=self.open_log),
            None,
            rumps.MenuItem("종료", callback=self.quit_app),
        ]
        self._refresh_status()

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

    @rumps.timer(UPDATE_INTERVAL)
    def update_status(self, _):
        self._refresh_status()

    def toggle_watcher(self, _):
        if is_watcher_running():
            watcher_stop()
        else:
            watcher_start()
        self._refresh_status()

    def open_log(self, _):
        subprocess.run(["open", "-a", "Console", str(LOG_FILE)])

    def quit_app(self, _):
        rumps.quit_application()


def already_running() -> bool:
    if TRAY_LOCK.exists():
        try:
            pid = int(TRAY_LOCK.read_text())
            subprocess.run(["kill", "-0", str(pid)], capture_output=True, check=True)
            return True
        except (ValueError, subprocess.CalledProcessError):
            pass
    TRAY_LOCK.write_text(str(os.getpid()))
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
```

- [ ] **Step 4: 루트의 기존 파일 삭제**

```bash
rm install.py uninstall.py tray.py
```

- [ ] **Step 5: 동작 확인 (macOS)**

```bash
python3 platform/macos/install.py --help 2>&1 || python3 -c "
import ast, pathlib
for f in ['platform/macos/install.py', 'platform/macos/uninstall.py', 'platform/macos/tray.py']:
    ast.parse(pathlib.Path(f).read_text())
    print(f'✅ 문법 OK: {f}')
"
```

기대 출력:
```
✅ 문법 OK: platform/macos/install.py
✅ 문법 OK: platform/macos/uninstall.py
✅ 문법 OK: platform/macos/tray.py
```

- [ ] **Step 6: 커밋**

```bash
git add platform/macos/install.py platform/macos/uninstall.py platform/macos/tray.py
git rm install.py uninstall.py tray.py
git commit -m "refactor: macOS 파일을 platform/macos/로 이동"
```

---

## Task 2: platform/windows/install.py 작성

**Files:**
- Create: `platform/windows/install.py`

- [ ] **Step 1: platform/windows/ 디렉토리 생성**

```bash
mkdir -p platform/windows
```

- [ ] **Step 2: platform/windows/install.py 작성**

```python
#!/usr/bin/env python3
"""
token_alert 설치 스크립트 (Windows)

실행: python platform\windows\install.py
"""

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
        print(f"❌ 필수 패키지 미설치: {', '.join(missing)}")
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
    _register_task(TASK_TRAY, TRAY_PY, use_pythonw=True)


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


def main() -> None:
    banner("token_alert 설치 시작 (Windows)")
    check_platform()
    check_python()
    check_pystray()
    check_config()
    banner("Task Scheduler 등록")
    register_tasks()
    start_tasks()
    verify_running()
    print_summary()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 문법 검증**

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('platform/windows/install.py').read_text(encoding='utf-8')
ast.parse(src)
print('✅ 문법 OK: platform/windows/install.py')
"
```

기대 출력:
```
✅ 문법 OK: platform/windows/install.py
```

- [ ] **Step 4: 커밋**

```bash
git add platform/windows/install.py
git commit -m "feat: Windows 설치 스크립트 추가 (Task Scheduler)"
```

---

## Task 3: platform/windows/uninstall.py 작성

**Files:**
- Create: `platform/windows/uninstall.py`

- [ ] **Step 1: platform/windows/uninstall.py 작성**

```python
#!/usr/bin/env python3
"""
token_alert 완전 삭제 스크립트 (Windows)

실행: python platform\windows\uninstall.py
"""

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

    remind_config()
    banner("삭제 완료!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 문법 검증**

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('platform/windows/uninstall.py').read_text(encoding='utf-8')
ast.parse(src)
print('✅ 문법 OK: platform/windows/uninstall.py')
"
```

기대 출력:
```
✅ 문법 OK: platform/windows/uninstall.py
```

- [ ] **Step 3: 커밋**

```bash
git add platform/windows/uninstall.py
git commit -m "feat: Windows 제거 스크립트 추가 (Task Scheduler)"
```

---

## Task 4: platform/windows/tray.py 작성

**Files:**
- Create: `platform/windows/tray.py`

- [ ] **Step 1: platform/windows/tray.py 작성**

```python
#!/usr/bin/env python3
"""
token_alert 시스템 트레이 앱 (Windows)
pystray 기반
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image

SCRIPT_ROOT = Path(__file__).parent.parent.parent.resolve()  # token_alert 루트
ICON_PATH = SCRIPT_ROOT / "claudecode-tray.png"
ICON_INACTIVE_PATH = SCRIPT_ROOT / "claudecode-tray-inactive.png"
LOG_FILE = Path.home() / ".claude" / "token_alert.log"

TASK_WATCHER = "TokenAlertWatcher"
UPDATE_INTERVAL = 10  # 상태 갱신 주기(초)
ICON_SIZE = (22, 22)


def is_watcher_running() -> bool:
    """Task Scheduler 쿼리로 watcher 실행 여부 확인."""
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_WATCHER, "/fo", "LIST"],
        capture_output=True,
        text=True,
    )
    return "Running" in result.stdout


def watcher_start() -> None:
    subprocess.run(["schtasks", "/run", "/tn", TASK_WATCHER], capture_output=True)


def watcher_stop() -> None:
    subprocess.run(["schtasks", "/end", "/tn", TASK_WATCHER], capture_output=True)


def load_icon(path: Path) -> Image.Image:
    """아이콘 이미지를 로드한다. 파일 없으면 회색 폴백 아이콘 반환."""
    if path.exists():
        img = Image.open(path).convert("RGBA").resize(ICON_SIZE, Image.LANCZOS)
        r, g, b, a = img.split()
        a = a.point(lambda v: 255 if v > 30 else 0)
        return Image.merge("RGBA", (r, g, b, a))
    return Image.new("RGBA", ICON_SIZE, (100, 100, 100, 255))


def get_status_title() -> str:
    return "● 감시 중" if is_watcher_running() else "○ 감시 중지됨"


def get_toggle_title() -> str:
    return "감시 중지" if is_watcher_running() else "감시 재시작"


def make_menu(icon_ref: list) -> pystray.Menu:
    """동적 메뉴 생성. icon_ref[0]에 pystray.Icon 인스턴스를 담아 아이콘 갱신에 활용."""

    def toggle(icon, item):
        if is_watcher_running():
            watcher_stop()
        else:
            watcher_start()
        time.sleep(1)
        _update_icon(icon_ref[0])

    def open_log(icon, item):
        if LOG_FILE.exists():
            os.startfile(str(LOG_FILE))
        else:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            LOG_FILE.touch()
            os.startfile(str(LOG_FILE))

    def quit_app(icon, item):
        icon.stop()

    return pystray.Menu(
        pystray.MenuItem(lambda item: get_status_title(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: get_toggle_title(), toggle),
        pystray.MenuItem("로그 열기", open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", quit_app),
    )


def _update_icon(icon: pystray.Icon) -> None:
    """실행 상태에 따라 트레이 아이콘을 교체."""
    running = is_watcher_running()
    path = ICON_PATH if running else ICON_INACTIVE_PATH
    icon.icon = load_icon(path)


def status_updater(icon_ref: list) -> None:
    """백그라운드 스레드: 10초마다 아이콘 갱신."""
    while True:
        time.sleep(UPDATE_INTERVAL)
        if icon_ref[0] is not None:
            _update_icon(icon_ref[0])


def main() -> None:
    running = is_watcher_running()
    initial_icon = load_icon(ICON_PATH if running else ICON_INACTIVE_PATH)

    icon_ref: list = [None]
    icon = pystray.Icon(
        "token_alert",
        initial_icon,
        "token_alert",
    )
    icon_ref[0] = icon
    icon.menu = make_menu(icon_ref)

    updater = threading.Thread(target=status_updater, args=(icon_ref,), daemon=True)
    updater.start()

    icon.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 문법 검증**

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('platform/windows/tray.py').read_text(encoding='utf-8')
ast.parse(src)
print('✅ 문법 OK: platform/windows/tray.py')
"
```

기대 출력:
```
✅ 문법 OK: platform/windows/tray.py
```

- [ ] **Step 3: 커밋**

```bash
git add platform/windows/tray.py
git commit -m "feat: Windows 트레이 앱 추가 (pystray)"
```

---

## Task 5: docs/install-windows.md 작성

**Files:**
- Create: `docs/install-windows.md`

- [ ] **Step 1: docs/install-windows.md 작성**

```markdown
# Windows 설치 가이드

## 사전 요구사항

- Python 3.8 이상 ([python.org](https://www.python.org/downloads/) — 설치 시 "Add Python to PATH" 체크)
- Git
- 필수 패키지:

```
pip install pystray Pillow
```

## 설치

### 1. 저장소 클론

```
git clone https://github.com/YOUR_GITHUB_OWNER/token_alert.git
cd token_alert
```

### 2. config.env 작성

```
copy config\config.env.example config\config.env
```

메모장 등으로 `config\config.env`를 열어 아래 항목을 실제 값으로 교체:

| 항목 | 설명 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급한 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 수신자 chat_id |
| `GITHUB_TOKEN` | PAT (scope: workflow) |
| `GITHUB_OWNER` | GitHub 사용자명 |
| `GITHUB_REPO` | 저장소 이름 (기본: token_alert) |

### 3. 설치 실행

```
python platform\windows\install.py
```

설치가 완료되면 Task Scheduler에 두 개의 작업이 등록되고 즉시 시작됩니다:
- `TokenAlertWatcher` — 감지 데몬
- `TokenAlertTray` — 시스템 트레이 앱

## 상태 확인

```
# watcher 상태
schtasks /query /tn TokenAlertWatcher

# 로그 확인
type %USERPROFILE%\.claude\token_alert.log
```

## 테스트 실행 (dispatch 없이)

```
python src\watcher.py --dry-run --once --verbose
```

## 재시작

```
schtasks /end /tn TokenAlertWatcher
schtasks /run /tn TokenAlertWatcher
```

## 제거

```
python platform\windows\uninstall.py
```

제거 후 선택적으로 삭제:
- `config\config.env` — 토큰 정보 포함, 수동 삭제 필요
- GitHub Secrets (리포지터리 설정 → Secrets and variables → Actions)

## 흔한 문제

**`pystray` 또는 `Pillow` 없음 오류**
```
pip install pystray Pillow
```

**`config.env` 없음 오류**
```
copy config\config.env.example config\config.env
```
이후 실제 값 입력.

**Python 명령을 찾을 수 없음**
Python 설치 시 "Add Python to PATH"를 체크했는지 확인. 또는:
```
py platform\windows\install.py
```

**트레이 아이콘이 보이지 않음**
작업 표시줄 오버플로 영역(숨겨진 아이콘 보기 ^)을 확인하세요.
```

- [ ] **Step 2: 커밋**

```bash
git add docs/install-windows.md
git commit -m "docs: Windows 설치 가이드 추가"
```

---

## Task 6: README.md에 Windows 섹션 추가

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README.md 설치 섹션 확인 후 macOS/Windows 분리**

현재 README.md의 설치 명령어 섹션을 찾아 아래와 같이 교체:

```markdown
## 설치

### macOS

```bash
python3 platform/macos/install.py
```

### Windows

```
python platform\windows\install.py
```

자세한 내용: [docs/install-windows.md](docs/install-windows.md)
```

제거 섹션도 동일하게 분리:

```markdown
## 제거

### macOS

```bash
python3 platform/macos/uninstall.py
```

### Windows

```
python platform\windows\uninstall.py
```
```

- [ ] **Step 2: 커밋**

```bash
git add README.md
git commit -m "docs: README에 Windows 설치/제거 섹션 추가"
```

---

## 전체 완료 확인

- [ ] `platform/macos/` — 3개 파일 존재, 루트에 install.py/uninstall.py/tray.py 없음
- [ ] `platform/windows/` — 3개 파일 존재
- [ ] `docs/install-windows.md` 존재
- [ ] `README.md` macOS/Windows 섹션 분리 확인
- [ ] `src/watcher.py` 변경 없음 (`git diff HEAD src/watcher.py` 출력 없음)
- [ ] `.github/workflows/token-reset-notify.yml` 변경 없음
