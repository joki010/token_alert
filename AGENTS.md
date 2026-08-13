# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 프로젝트 개요

Codex의 5시간 토큰 사용량 창(rolling window)이 초기화되는 시각을 계산하여, 컴퓨터가 꺼진 상태에서도 텔레그램으로 알림을 보내는 도구.

**흐름:** 로컬 데몬 → GitHub Actions dispatch → GitHub 서버에서 sleep → Telegram Bot API

**지원 플랫폼:** macOS (launchd + rumps), Windows (레지스트리 시작 프로그램 + pystray)

## 디렉토리 구조

```
src/watcher.py              ← 공통 감지 로직 (플랫폼 무관)
platform/macos/             ← macOS 전용: install.py, uninstall.py, tray.py
platform/windows/           ← Windows 전용: install.py, uninstall.py, tray.py
config/config.env           ← 설정 파일 (gitignore됨)
.github/workflows/          ← GitHub Actions 워크플로우
tests/test_watcher.py       ← watcher.py 단위 테스트 (pytest)
docs/                       ← 설치 가이드, 설계 문서, 구현 계획
```

## 주요 명령어

```bash
# 테스트 실행 (실제 dispatch 없이)
python3 src/watcher.py --dry-run --once --verbose

# 한 번 실행 후 종료 (실제 dispatch)
python3 src/watcher.py --once --verbose

# 실시간 로그 확인
tail -f ~/.Codex/token_alert.log
```

### macOS

```bash
# 설치
python3 platform/macos/install.py

# 완전 삭제
python3 platform/macos/uninstall.py

# 데몬 상태 확인
launchctl list com.token-alert.watcher

# 데몬 재시작 (config 변경 후)
launchctl unload ~/Library/LaunchAgents/com.token-alert.watcher.plist
launchctl load ~/Library/LaunchAgents/com.token-alert.watcher.plist
```

### Windows

```
# 설치 (pystray, Pillow 사전 설치 필요: pip install pystray Pillow)
python platform\windows\install.py

# 완전 삭제
python platform\windows\uninstall.py

# 상태 확인 (PID 파일 기반)
type %USERPROFILE%\.token_alert.pid
```

## 아키텍처

### 감지 로직 (`src/watcher.py`)

- `~/.Codex/token_alert_usage.json`의 `five_hour_resets_at`(Unix timestamp) 우선 읽음 — Codex가 서버 응답 기반으로 기록하는 실제 초기화 시각
- 해당 파일 없거나 필드 없으면 폴백: `~/.Codex/projects/**/*.jsonl` **및** `~/.gjc/agent/sessions/**/*.jsonl`(GJC로 Codex를 구동한 세션) 전체를 glob으로 스캔 → 현재 시각 기준 5시간 이내 가장 오래된 타임스탬프 + 5h
- **GJC(Gajae Code) 호환**: GJC는 자체 TUI에서 상태줄을 그려 `~/.Codex`의 statusLine 훅(`~/.Codex/statusline.py` → `watcher.py --write-status-line`)을 거치지 않으므로 GJC 세션에서는 usage cache(`token_alert_usage.json`)가 갱신되지 않는다. 대신 GJC가 `~/.gjc/agent/sessions/**/*.jsonl`에 남기는 세션 로그가 Codex 로그와 동일한 최상위 `timestamp` 필드(ISO 8601)를 쓰므로, jsonl 폴백 스캔에 그대로 합류시켜 감지한다(`get_jsonl_source_dirs()`).
- `/status` 텔레그램 명령도 동일 우선순위로 초기화 시각 조회 (dispatch 상태와 무관하게 실시간 정확한 값 표시)
- 직전 예약 시각과 동일하면 중복 dispatch 방지 (`~/.token_alert_state.json`에 저장)
- dispatch 직전 진행 중인 이전 워크플로우 실행을 모두 취소 (`cancel_previous_workflow_runs`) — 초기화 시각이 바뀔 때 중복 알림 방지
- GitHub Actions `concurrency`는 알림 대상과 초기화 시각별 그룹을 사용하며 `cancel-in-progress: false`; watcher가 같은 대상의 이전 대기 실행을 dispatch 전에 취소
- GitHub API `POST /repos/{owner}/{repo}/actions/workflows/token-reset-notify.yml/dispatches` 로 `reset_time` 전달
- `load_config()`는 `~/.config/token-alert/config.env` → 소스 경로 순으로 탐색 (고정 경로 우선)
- 단일 인스턴스 보장: 시작 시 `~/.token_alert.pid` 파일 생성, 이미 실행 중이면 즉시 종료 (`acquire_pid_lock`)
- 종료 시(`atexit`, `SIGTERM`, `SIGINT`) PID 파일 자동 삭제

### Codex 자동 창 시작

- macOS 트레이가 `~/.config/token-alert/activation-policy.json`을 쓰며, 누락되거나 잘못된 정책은 비활성으로 처리
- watcher가 미래 Codex 5시간 초기화를 대기 상태로 저장하고, 초기화 시각보다 `enabled_at`이 앞선 대기 건만 처리; 컴퓨터가 꺼져 있었다면 다음 실행에서 한 번 처리
- `Codex -p` 자식 프로세스 하나를 동기적으로 실행하고 종료나 타임아웃까지 기다린 뒤 회수; 표준 입출력은 모두 비활성
- `300 < remaining <= 21600` 조건은 GitHub Actions 알림 dispatch에만 적용되며 로컬 자동 창 시작에는 적용되지 않음

### GitHub Actions (`.github/workflows/token-reset-notify.yml`)

- `workflow_dispatch` 트리거, input: `reset_time` (KST ISO 8601, 예: `2026-06-20T12:00:00+09:00`)
- `run-name: ${{ inputs.target_label }} ${{ inputs.reset_time }}` — 워크플로우 실행 이름에 대상과 초기화 시각 표시
- `date` 명령으로 현재 시각과 목표 시각 차이 계산 → `sleep $DIFF`
- 대기 후 `curl`로 Telegram Bot API 호출
- 최대 실행 시간 360분(6시간) — 5시간 창보다 여유 있음
- KST 표시 시 `TZ=Asia/Seoul date -d "$TIME"` 필요 (Actions 서버 기본 UTC)
- `parse_mode: HTML` 사용 — Markdown v1은 언더스코어 이스케이프 오류 발생

### macOS 트레이 앱 (`platform/macos/tray.py`)

- `rumps` 라이브러리 사용, venv에 설치됨
- LaunchAgent: `com.token-alert.tray` (`~/Library/LaunchAgents/com.token-alert.tray.plist`)
- `~/.config/token-alert/activation-policy.json` 정책 파일(atomic write)을 통해 "Codex 자동 창 시작" 옵션 토글 제어
- GUI Python 필수: `/opt/homebrew/Cellar/python@3.13/.../Python.app/.../Python` (rumps가 NSApplication 필요)
- `PYTHONPATH`를 venv site-packages로 지정해야 rumps import 가능
- 독 아이콘 숨기기: `NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)` — `super().__init__` 전에 호출
- 활성/비활성 아이콘 전환: `self.icon = str(path)` 로 런타임에 교체 가능
- 팔레트 PNG(mode=P) → RGBA 변환 후 LANCZOS 리사이즈 시 알파가 1~107로 반투명됨 → 리사이즈 후 `a > 30` 이진화 필요
- 트레이 재시작: `launchctl unload/load ~/Library/LaunchAgents/com.token-alert.tray.plist`
- 디버깅: `osascript -e 'tell application "System Events" to tell process "Python" to tell menu bar 1 to tell menu bar item 1 to return title'`
- **macOS Tahoe(26+) 필수**: `autosaveName` 없는 `NSStatusItem`은 기본값 숨김 → `setAutosaveName_("TokenAlert")`을 `@rumps.timer(0.1)`로 run loop 시작 후 설정 (`_nsapp.nsstatusitem`은 `app.run()` 이후에만 접근 가능)
- 설치 시 `defaults write com.apple.controlcenter "NSStatusItem Visible TokenAlert" -bool true` 필수 — 미실행 시 아이콘이 맥 메뉴바에 나타나지 않음
- py2app으로 번들링: `platform/macos/setup_tray.py` 설정 파일, 출력은 `dist/TokenAlertTray.app`; `CFBundleName`이 바이너리 이름 결정
- 빌드 전 venv에 rumps 설치 필수: `.venv/bin/pip install rumps` (미설치 시 `ImportError: No module named 'rumps'`로 빌드 실패)
- 빌드 명령: `.venv/bin/python platform/macos/setup_tray.py py2app` (소스 디렉터리에서 실행; py2app은 venv에 설치됨)
- 번들 후 애드혹 서명: Python.framework 포함 번들에서 `--deep`은 "bundle format is ambiguous" 오류 발생 → 순서대로 서명 필요:
  ```bash
  codesign --force --sign - ~/Applications/TokenAlertTray.app/Contents/Frameworks/Python.framework/Versions/Current/Python
  codesign --force --sign - ~/Applications/TokenAlertTray.app/Contents/Frameworks/Python.framework/Versions/Current
  codesign --force --sign - ~/Applications/TokenAlertTray.app
  ```
  `codesign --verify`는 Python.framework 번들 구조 특성상 여전히 경고 출력할 수 있음 — 로컬 실행에는 문제 없음
- LaunchAgent plist에 `LimitLoadToSessionType = Aqua` 필요 — GUI/메뉴바 앱은 Aqua 세션에서만 동작

### Windows 트레이 앱 (`platform/windows/tray.py`)

- `pystray` + `Pillow` 사용
- watcher 상태 확인: `~/.token_alert.pid` 읽고 `ctypes.windll.kernel32.QueryFullProcessImageNameW(pid)`로 exe 경로에 "python" 포함 여부 확인
  - `os.kill(pid, 0)` 단독 사용 금지 — PID가 다른 프로세스(예: PowerShell)에 재사용되면 오감지
  - subprocess로 PowerShell 스폰 금지 — ctypes Windows API 직접 호출이 즉시 반환되고 콘솔 깜빡임 없음
- watcher 시작: `subprocess.Popen([pythonw, watcher.py], creationflags=CREATE_NO_WINDOW|DETACHED_PROCESS)` — schtasks 미사용
- watcher 중지: PID 파일 읽어 `os.kill(pid, 9)` 후 PID 파일 삭제 — schtasks 미사용
- TokenAlertTray.exe가 2개 프로세스로 보이는 것은 pystray/PyInstaller 정상 동작 — 창 없음(HasWindow=False) 확인됨
- 로그 열기: `os.startfile(log_path)`
- 상태 갱신 주기: 10초 (백그라운드 스레드)
- 아이콘: `Codex-tray.png`(감시 중) / `Codex-tray-inactive.png`(중지), 알파 `> 30` 이진화 적용
- **콘솔 창 억제 필수**: windowed 앱에서도 subprocess 호출 시 콘솔 깜빡임 발생 → 모든 `subprocess.run/Popen`에 `creationflags=subprocess.CREATE_NO_WINDOW` 추가
- PyInstaller 빌드 스펙: `platform/windows/setup_tray.spec` (6.x 문법) — `a.zipped_data`, `a.zipfiles`, `cipher` 파라미터는 PyInstaller 6.x에서 제거됨
- spec 내 경로는 cwd가 아닌 **spec 파일 위치(`SPECPATH`)** 기준으로 해석됨 — 루트 파일 참조 시 `os.path.abspath(os.path.join(SPECPATH, '..', '..'))` 사용
- 빌드 산출물 설치 위치: `%LOCALAPPDATA%\TokenAlert\TokenAlertTray.exe`; `hiddenimports=['pystray._win32']` 필수

### macOS 데몬 (`platform/macos/install.py`)

- `install.py`가 `watcher.py`, `atomic_json.py`, `activation.py`, `scheduling.py` 매니페스트 파일들을 `~/.local/lib/token_alert/src/`로 설치하고, 기존 설치 설정과 정책 파일은 보존
- 복사 시 `~/.config/token-alert/config.env`에 `CLAUDE_CLI_PATH` 자동 감지 및 추가 로직 수행
- 설치 후 launchd reload 이전에 설치된 위치에서 `import watcher, atomic_json, activation, scheduling` 모듈 연동 검증(`verify_smoke`) 수행
- `~/Library/LaunchAgents/com.token-alert.watcher.plist` 생성 후 `launchctl load` — plist는 소스 경로 대신 고정 경로를 참조
- `ProcessType: Background` — 메뉴 바·독 아이콘 없음
- `KeepAlive: true` — 크래시 시 자동 재시작
- `StandardOutPath` 미설정 — `watcher.py`의 `FileHandler`가 직접 로그 파일에 씀. stdout 리디렉션과 FileHandler가 겹치면 로그가 2번 기록되므로 의도적으로 제외
- 로그: `~/.Codex/token_alert.log`(FileHandler 직접 기록), `~/.Codex/token_alert_error.log`(stderr)

### Windows 데몬 (`platform/windows/install.py`)

- `install.py`가 `watcher.py`, `atomic_json.py`, `activation.py`, `scheduling.py`를 `~/.local/lib/token_alert/src/`로 원자적으로 복사하고 `config.env`를 `~/.config/token-alert/config.env`로 배치
- 시작 프로그램 등록: `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run` 레지스트리 (`winreg` 모듈) — Task Scheduler 불사용
  - `schtasks /create /ru`는 비밀번호 요구, `Register-ScheduledTask`는 샌드박스·비관리자 환경에서 액세스 거부 발생
- watcher는 `pythonw.exe`(콘솔 창 없음)로 등록, tray는 `.exe` 직접 등록
- 즉시 시작: `subprocess.Popen(..., creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS)`
- 위 분리 실행 플래그는 Windows watcher와 트레이 시작에만 사용하며 Codex 자동 창 시작 자식에는 사용하지 않음
- 로그: `%USERPROFILE%\.Codex\token_alert.log`
- **한글 Windows 인코딩**: `print()`에 이모지 포함 시 cp949 오류 → `PYTHONUTF8=1` 환경변수 또는 `-X utf8` 플래그 필요

### 설정 (`config/config.env`)

`load_config()`는 `config/config.env` 파일을 읽은 뒤, 동일 키의 환경 변수가 있으면 덮어씀(환경 변수 우선).

| 키 | 설명 |
|----|------|
| `TELEGRAM_BOT_TOKEN` | BotFather 발급 토큰 |
| `TELEGRAM_CHAT_ID` | 수신자 chat_id |
| `GITHUB_TOKEN` | PAT (scope: workflow) |
| `GITHUB_OWNER` | GitHub 사용자명 |
| `GITHUB_REPO` | 저장소 이름 (기본: `token_alert`) |
| `POLL_INTERVAL` | 감지 주기 초 (기본: 600) |
| `NOTIFY_ADVANCE_SECONDS` | 초기화 시각 몇 초 전에 알림 (기본: 0) |

## 주의사항

- `config/config.env`는 `.gitignore`에 등록됨 — 커밋하지 말 것
- GitHub Secrets(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)는 Actions 워크플로우에서만 사용
- `--dry-run`의 알림 예약 미리보기는 기존처럼 예약 상태를 저장할 수 있지만, Codex 자동 창 시작은 자식 프로세스를 실행하거나 활성화 상태를 변경하지 않음
- `--dry-run` 모드에서는 `cancel_previous_workflow_runs` 호출 없음 — 실제 워크플로우가 취소되지 않음
- `src/watcher.py`는 표준 라이브러리만 사용 — 추가 패키지 불필요
- Windows tray.py는 `pystray`, `Pillow` 필요 (`pip install pystray Pillow`)
- macOS에 `timeout` 명령 없음 — GNU coreutils 설치 필요하거나 백그라운드 프로세스+kill 방식 사용
- 데몬을 재설치할 때(`install.py` 재실행)는 반드시 `uninstall.py` 먼저 실행 — 그렇지 않으면 PID 파일 충돌로 두 번째 인스턴스가 즉시 종료됨
- 테스트 실행: `python3 -m pytest tests/test_watcher.py -v`
