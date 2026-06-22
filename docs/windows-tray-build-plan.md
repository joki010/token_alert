# Windows 트레이 앱 빌드 계획

## 현재 상태

| 항목 | 현재 | 문제점 |
|------|------|--------|
| 실행 방식 | `pythonw.exe tray.py` | 대상 PC에 Python + pystray + Pillow 필요 |
| 경로 계산 | `Path(__file__).parent.parent.parent` | `.exe` 빌드 시 경로 깨짐 |
| 아이콘 | 프로젝트 루트 PNG 직접 참조 | 번들링 시 상대 경로 불일치 |
| 배포 | 소스 코드 전체 필요 | 독립 실행 파일 없음 |

macOS는 py2app으로 `TokenAlertTray.app`을 생성하고 autosaveName 등록까지 install.py에서 자동화되어 있음.  
Windows도 동일하게 PyInstaller로 `TokenAlertTray.exe`를 빌드하고 install.py에서 자동 처리하는 구조로 맞춘다.

---

## 작업 목록

### 1. `platform/windows/tray.py` — frozen 경로 처리 추가

PyInstaller `--onefile` 빌드 시 `sys.frozen == True`, 리소스는 `sys._MEIPASS`에 임시 압축 해제됨.  
`__file__` 기반 경로 계산은 동작하지 않으므로 macOS tray.py와 동일한 패턴으로 분기 처리.

```python
# 수정 전
SCRIPT_ROOT = Path(__file__).parent.parent.parent.resolve()
ICON_PATH = SCRIPT_ROOT / "claudecode-tray.png"

# 수정 후
if getattr(sys, 'frozen', False):
    RESOURCES = Path(sys._MEIPASS)
else:
    RESOURCES = Path(__file__).parent.parent.parent.resolve()

ICON_PATH = RESOURCES / "claudecode-tray.png"
ICON_INACTIVE_PATH = RESOURCES / "claudecode-tray-inactive.png"
```

> `config.env`는 tray.py가 직접 읽지 않으므로 frozen 경로 처리 불필요.  
> watcher.py(별도 프로세스)가 읽으며 watcher는 스크립트로 유지.

---

### 2. `platform/windows/setup_tray.spec` (신규) — PyInstaller 스펙

macOS의 `platform/macos/setup_tray.py`에 대응하는 파일.  
프로젝트 루트에서 실행: `.venv/Scripts/pyinstaller platform/windows/setup_tray.spec`

```python
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['platform/windows/tray.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('claudecode-tray.png', '.'),
        ('claudecode-tray-inactive.png', '.'),
    ],
    hiddenimports=['pystray._win32'],
    hookspath=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='TokenAlertTray',
    windowed=True,        # 콘솔 창 없음 (pythonw.exe 상당)
    onefile=True,
    icon='claudecode-tray.ico',  # install.py에서 PNG → ICO 자동 변환
)
```

---

### 3. `platform/windows/install.py` — 빌드 및 설치 단계 추가

다음 함수를 추가하고 `main()`에서 호출.

#### `convert_icon_to_ico()`
Pillow로 PNG → ICO 변환. PyInstaller에 `.ico`가 필요.

```python
def convert_icon_to_ico() -> None:
    from PIL import Image
    src = SCRIPT_ROOT / "claudecode-tray.png"
    dst = SCRIPT_ROOT / "claudecode-tray.ico"
    if not dst.exists():
        img = Image.open(src).resize((256, 256), Image.LANCZOS)
        img.save(str(dst), format="ICO")
    print("✅ ICO 변환 완료")
```

#### `ensure_pyinstaller()`
가상환경에 PyInstaller 미설치 시 자동 설치.

```python
def ensure_pyinstaller() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import PyInstaller"],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    print("✅ PyInstaller 준비 완료")
```

#### `build_tray_exe()`
spec 파일로 빌드. 출력: `dist/TokenAlertTray.exe`

```python
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
```

#### `install_tray_exe()`
exe를 `%LOCALAPPDATA%\TokenAlert\`로 복사하고 Task Scheduler 등록.  
(소스 경로 의존성 없이 독립 실행 가능한 위치로 설치)

```python
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "TokenAlert"
TRAY_EXE_DEST = INSTALL_DIR / "TokenAlertTray.exe"

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
```

#### `main()` 수정
```python
def main() -> None:
    ...
    banner("트레이 앱 빌드 및 설치")
    ensure_pyinstaller()
    convert_icon_to_ico()
    build_tray_exe()
    install_tray_exe()
    ...
```

---

### 4. `platform/windows/uninstall.py` — exe 삭제 추가

```python
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "TokenAlert"

def remove_tray_exe() -> None:
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
        print(f"✅ 트레이 앱 삭제: {INSTALL_DIR}")
    else:
        print(f"ℹ️  트레이 앱 없음: {INSTALL_DIR}")
```

---

## 구조 변경 요약

```
수정 전                          수정 후
─────────────────────────────    ─────────────────────────────────────────
pythonw.exe tray.py              %LOCALAPPDATA%\TokenAlert\TokenAlertTray.exe
  (Python 필요)                    (독립 실행, Python 불필요)

Task Scheduler → tray.py        Task Scheduler → TokenAlertTray.exe
install.py: script만 등록        install.py: 빌드 → 설치 → 등록 자동화
```

## 고려 사항

| 항목 | 내용 |
|------|------|
| onefile vs onedir | onefile이 배포 단순. 단, 시작 시 sys._MEIPASS 압축 해제로 1~2초 지연 |
| Windows Defender | 서명 없는 exe는 SmartScreen 경고 발생 가능. 로컬 빌드 용도이면 무시 가능 |
| macOS와의 대칭성 | `setup_tray.py`(py2app) ↔ `setup_tray.spec`(PyInstaller)로 구조 통일 |
| watcher는 스크립트 유지 | watcher.py는 표준 라이브러리만 사용하므로 빌드 불필요. Python 필요 조건 유지 |
