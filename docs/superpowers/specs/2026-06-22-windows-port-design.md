# Windows 이식 설계 문서

**날짜:** 2026-06-22  
**대상:** token_alert Windows 지원 추가

---

## 목표

token_alert를 Windows에서도 동일하게 작동하도록 확장한다. 핵심 감지 로직(`watcher.py`)은 변경 없이 재사용하고, 플랫폼별 차이(데몬 등록, 트레이 앱)를 `platform/` 폴더로 격리한다.

---

## 아키텍처

### 접근 방식

`src/watcher.py`는 이미 크로스 플랫폼이므로 변경하지 않는다. 플랫폼 의존성(launchd, Task Scheduler, rumps, pystray)은 `platform/macos/`와 `platform/windows/`에만 존재한다.

### 디렉토리 구조

```
token_alert/
├── src/
│   └── watcher.py              ← 공통, 변경 없음
├── platform/
│   ├── macos/
│   │   ├── install.py          ← 기존 루트 install.py 이동
│   │   ├── uninstall.py        ← 기존 루트 uninstall.py 이동
│   │   └── tray.py             ← 기존 루트 tray.py 이동
│   └── windows/
│       ├── install.py          ← 신규
│       ├── uninstall.py        ← 신규
│       └── tray.py             ← 신규
├── config/
│   ├── config.env              ← 공통, 변경 없음
│   └── config.env.example
├── .github/workflows/
│   └── token-reset-notify.yml  ← 변경 없음
├── docs/
│   └── install-windows.md      ← 신규
└── README.md                   ← Windows 섹션 추가
```

---

## 컴포넌트 상세

### 공통: `src/watcher.py`

변경 없음. `Path.home() / ".claude/projects"` 기본값이 Windows에서도 `C:\Users\<이름>\.claude\projects`로 자동 확장된다. 경로가 다를 경우 `CLAUDE_PROJECTS_DIR` 환경 변수로 재정의 가능.

### macOS 이동: `platform/macos/`

기존 루트의 `install.py`, `uninstall.py`, `tray.py`를 이동한다.

**변경 사항:**
- `SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve()` — 루트 기준 유지
- `WATCHER_PY`, `CONFIG_ENV` 등 루트 상대 경로는 변경 없음
- 기존 설치된 LaunchAgent plist는 `watcher.py` 절대 경로를 직접 참조하므로 영향 없음

### Windows 신규: `platform/windows/install.py`

1. Python 버전 확인 (3.8+)
2. `config/config.env` 유효성 확인 (필수 키 존재 및 예시값 여부)
3. `pystray`, `pillow` 설치 여부 확인 — 미설치 시 `pip install pystray pillow` 안내 후 종료
4. Task Scheduler에 두 작업 등록 (`schtasks /create`, 관리자 권한 불필요):
   - `TokenAlertWatcher` — `python src\watcher.py`, 로그인 시 자동 시작, 숨김 실행
   - `TokenAlertTray` — `python platform\windows\tray.py`, 로그인 시 자동 시작
5. 즉시 시작 (`schtasks /run`)
6. 등록 확인 출력

### Windows 신규: `platform/windows/uninstall.py`

1. `schtasks /delete /tn TokenAlertWatcher /f`
2. `schtasks /delete /tn TokenAlertTray /f`
3. 상태 파일(`~/.token_alert_state.json`) 삭제 여부 사용자에게 확인 후 처리
4. 삭제 완료 안내

### Windows 신규: `platform/windows/tray.py`

- `pystray` + `PIL` 기반 시스템 트레이 아이콘
- 기능: macOS tray.py와 동일
  - 감시 상태 표시 (실행 중 / 중지됨)
  - 감시 중지 / 재시작 토글
  - 로그 열기 (`os.startfile(log_path)`)
  - 종료
- watcher 상태 확인: `schtasks /query /tn TokenAlertWatcher /fo LIST` 파싱 → `Status: Running` 여부
- 아이콘: 기존 `claudecode-tray.png`, `claudecode-tray-inactive.png` 재사용
- 상태 갱신 주기: 10초 (macOS와 동일)

---

## 설정

`config/config.env`는 macOS와 Windows가 공유한다. 추가 키 없음.

로그 경로:
- Windows: `%USERPROFILE%\.claude\token_alert.log`
- macOS: `~/.claude/token_alert.log`

`watcher.py`의 `LOG_FILE = Path.home() / ".claude" / "token_alert.log"`가 양 플랫폼에서 동일하게 동작한다.

---

## 문서

### `docs/install-windows.md` (신규)

- 사전 요구사항: Python 3.8+, `pip install pystray pillow`
- 설치: `git clone` → `config.env` 작성 → `python platform\windows\install.py`
- 제거: `python platform\windows\uninstall.py`
- 상태 확인: `schtasks /query /tn TokenAlertWatcher`
- 로그 위치: `%USERPROFILE%\.claude\token_alert.log`
- 흔한 문제: pystray 미설치, config.env 누락, Python PATH 미등록

### `README.md` 업데이트

설치 섹션에 macOS / Windows 구분 추가:
- macOS: `python platform/macos/install.py`
- Windows: `python platform\windows\install.py`

---

## 제약 사항

- Python 표준 라이브러리만 사용 (watcher.py)
- Windows tray.py는 `pystray`, `pillow` 필요
- EXE 패키징 없음 — Python 설치 가정
- BAT 래퍼 없음 — 명령줄 직접 실행
- GitHub Actions 워크플로우 변경 없음
