# 시작 프로그램 등록 선택 기능 구현 계획

## 배경

현재 `install.py`(Windows/macOS 모두)는 시작 프로그램 등록을 자동으로 수행한다.
사용자가 선택할 수 있도록 설치 중 y/N 프롬프트를 추가한다.

---

## 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `platform/windows/install.py` | `ask_startup()` 추가, `install_tray_exe()` 분리, `main()` 수정 |
| `platform/macos/install.py` | `ask_startup()` 추가, `main()` 수정 |

---

## 1. 공통 헬퍼 (두 파일 동일하게 추가)

```python
def ask_startup() -> bool:
    """시작 프로그램 등록 여부를 묻는다. y/Y 이면 True."""
    try:
        ans = input("로그인 시 자동 시작으로 등록할까요? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    return ans == "y"
```

---

## 2. Windows (`platform/windows/install.py`)

### 2-1. `install_tray_exe()` 분리

현재 `install_tray_exe()`는 exe 복사와 Task 등록을 함께 수행한다.
등록 없이 복사만 할 수 있도록 두 함수로 쪼갠다.

```python
def _copy_tray_exe() -> None:
    """빌드된 exe를 설치 디렉토리로 복사한다."""
    dist_exe = SCRIPT_ROOT / "dist" / "TokenAlertTray.exe"
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(dist_exe), str(TRAY_EXE_DEST))
    print(f"✅ TokenAlertTray.exe 복사 완료: {TRAY_EXE_DEST}")


def _register_tray_task() -> None:
    """Task Scheduler에 TokenAlertTray 작업을 등록한다."""
    subprocess.run(["schtasks", "/delete", "/tn", TASK_TRAY, "/f"], capture_output=True)
    result = subprocess.run([
        "schtasks", "/create",
        "/tn", TASK_TRAY,
        "/tr", f'"{TRAY_EXE_DEST}"',
        "/sc", "ONLOGON",
        "/ru", _current_user(),
        "/rl", "LIMITED",
        "/f",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Task 등록 실패: {result.stderr.strip()}")
        sys.exit(1)
    print(f"✅ TokenAlertTray Task 등록 완료")


def install_tray_exe() -> None:
    """exe 복사 + Task 등록 (자동 시작 선택 시 호출)."""
    _copy_tray_exe()
    _register_tray_task()
```

### 2-2. `main()` 수정

```python
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
    if ask_startup():
        register_tasks()       # TASK_WATCHER 등록
        _register_tray_task()  # TASK_TRAY 등록
        start_tasks()
        verify_running()
    else:
        print("ℹ️  자동 시작 등록을 건너뜁니다.")
        print(f"   나중에 등록하려면 install.py 를 다시 실행하세요.")

    print_summary()
```

### 2-3. `print_summary()` 수정

자동 시작 미등록 시에도 수동 실행 방법을 안내하도록 문구를 조정한다.

---

## 3. macOS (`platform/macos/install.py`)

### 3-1. `main()` 수정

```python
def main() -> None:
    banner("token_alert 설치 시작 (macOS)")
    check_platform()
    check_python()
    check_config()

    banner("파일 설치 (고정 경로)")
    install_watcher_files()

    banner("시작 프로그램 등록")
    if ask_startup():
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

    print_summary()
```

> **참고:** 트레이 빌드(`build_tray_app`)는 수십 초 소요되므로 자동 시작 선택 시에만 실행한다.
> plist 파일은 등록 여부와 무관하게 생성해 두어 나중에 `launchctl load`만으로 활성화할 수 있도록 한다.

### 3-2. `print_summary()` 수정

등록 여부를 `main()`에서 인자로 받아 메시지를 분기한다.

```python
def print_summary(startup_registered: bool = True) -> None:
    banner("설치 완료!")
    if startup_registered:
        print("token_alert 가 백그라운드에서 실행 중입니다.\n")
    else:
        print("token_alert 파일 설치가 완료되었습니다.")
        print("자동 시작 미등록 상태입니다.\n")
    ...
```

---

## 구현 순서

1. `platform/windows/install.py`
   - `ask_startup()` 추가
   - `install_tray_exe()` → `_copy_tray_exe()` + `_register_tray_task()` + `install_tray_exe()` 로 분리
   - `main()` 수정
   - `print_summary()` 수정

2. `platform/macos/install.py`
   - `ask_startup()` 추가
   - `main()` 수정
   - `print_summary(startup_registered)` 수정

3. 커밋 및 푸시
