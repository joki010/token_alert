# token_alert

Claude Code의 5시간 토큰 초기화 시각을 자동으로 계산하여 컴퓨터가 꺼져 있어도 텔레그램으로 알림을 보내주는 도구.

## 작동 원리

```
로컬 감지 데몬 (launchd / Task Scheduler)
  ↓  선택 시 Codex/Claude 사용량 API 직접 조회 (Codex 다중 프로필 포함)
  ↓  실패하면 ~/.claude/token_alert_usage.json 또는 JSONL 추정값으로 폴백
  ↓  공급자·계정·한도 창별로 GitHub Actions workflow_dispatch 호출

GitHub Actions (클라우드)
  ↓  초기화 시각까지 sleep
  ↓  Telegram Bot API 호출

텔레그램 → 알림 도착
```

---

## 준비사항

- Python 3.8+
- GitHub 계정 + Personal Access Token (scope: `workflow`)
- 텔레그램 봇 토큰 + chat_id
- 이 저장소를 본인 GitHub 계정에 포크 또는 클론

설정 방법:
- [텔레그램 봇 설정](docs/telegram-setup.md)
- [GitHub Actions 설정](docs/github-setup.md)

---

## 설정 파일

```bash
cp config/config.env.example config/config.env
# config/config.env 편집
```

| 키 | 설명 |
|----|------|
| `TELEGRAM_BOT_TOKEN` | BotFather 발급 토큰 |
| `TELEGRAM_CHAT_ID` | 수신자 chat_id |
| `GITHUB_TOKEN` | PAT (scope: workflow) |
| `GITHUB_OWNER` | GitHub 사용자명 |
| `GITHUB_REPO` | 저장소 이름 (기본: `token_alert`) |
| `GITHUB_REF` | workflow_dispatch 대상 브랜치 (기본: `main`) |
| `POLL_INTERVAL` | 감지 주기 초 (기본: 600) |
| `NOTIFY_ADVANCE_SECONDS` | 초기화 시각 몇 초 전에 알림 (기본: 0) |
| `ENABLE_DIRECT_USAGE` | `1`이면 Codex/Claude 사용량 API를 먼저 조회 |
| `CODEX_PROFILES_DIR` | Codex 다중 프로필 경로 (기본: `~/.codex-switch/profiles`) |
| `CODEX_AUTH_JSON` | Codex `auth.json` 경로 (기본: `~/.codex/auth.json`) |
| `CODEX_HOME` | `CODEX_AUTH_JSON` 대신 쓸 Codex 홈 경로 |
| `CLAUDE_USAGE_CREDENTIALS` | Claude usage OAuth 자격 파일 경로 (기본: `~/.config/claude-usage-bar/credentials.json`) |
| `CLAUDE_CLI_PATH` | Claude CLI의 절대 경로 |
| `CLAUDE_ACTIVATION_PROMPT` | 자동 창 시작 시 전달할 프롬프트 (기본: `.`) |
| `CLAUDE_ACTIVATION_TIMEOUT_SECONDS` | 자동 창 시작 각 시도별 타임아웃 (기본: 120) |

직접 조회가 실패하거나 자격 파일이 없으면 기존 캐시와 JSONL 추정 방식으로 계속 동작합니다. Codex는 `CODEX_PROFILES_DIR` 아래의 각 프로필 `auth.json`을 계정별로 조회하고, 유효한 프로필이 없을 때만 단일 `CODEX_AUTH_JSON`/`CODEX_HOME`으로 폴백합니다. 접근 토큰과 새로고침 토큰은 로그, 상태 파일, 텔레그램 응답에 쓰지 않습니다.

---

## macOS 설치

### 필수 조건

- macOS 12 이상
- Python 3.8+
- venv 생성 및 rumps 설치

```bash
python3 -m venv .venv
.venv/bin/pip install rumps pyobjc-framework-Cocoa
```

### 설치

```bash
python3 platform/macos/install.py
```

설치 스크립트가 수행하는 작업:

1. **고정 경로에 파일 복사** — 프로젝트 폴더 이동·삭제와 무관하게 데몬이 동작하도록 고정 위치에 설치
   - `~/.local/lib/token_alert/src/watcher.py` (및 `atomic_json.py`, `activation.py`, `scheduling.py` 등 런타임 매니페스트)
   - `~/.config/token-alert/config.env` (권한 600)

2. **watcher 데몬 등록** — `~/Library/LaunchAgents/com.token-alert.watcher.plist` 생성 및 로드  
   고정 경로를 참조. 로그인 시 자동 시작, 크래시 시 자동 재시작 (`KeepAlive: true`)

3. **트레이 앱 빌드** — py2app으로 `dist/TokenAlertTray.app` 생성  
   (최초 실행 시 py2app 자동 설치, 수십 초 소요)

4. **트레이 앱 설치** — `~/Applications/TokenAlertTray.app`으로 복사 및 ad-hoc 서명

5. **트레이 데몬 등록** — `~/Library/LaunchAgents/com.token-alert.tray.plist` 생성 및 로드  
   메뉴 막대 아이콘으로 watcher 상태 확인 및 제어 가능

### 재설치 (설정 변경 후)

```bash
python3 platform/macos/uninstall.py
python3 platform/macos/install.py
```

### 상태 확인

```bash
# 데몬 상태
launchctl list com.token-alert.watcher
launchctl list com.token-alert.tray

# 실시간 로그
tail -f ~/.claude/token_alert.log

# 한 번 테스트 실행 (실제 dispatch 없이)
python3 src/watcher.py --dry-run --once --verbose
```

### 트레이 앱 메뉴

메뉴 막대 아이콘을 클릭하면:
- **● 감시 중 / ○ 감시 중지됨** — watcher 현재 상태
- **Claude 자동 창 시작** — 토큰 초기화 시 Claude Code를 자동 실행할지 토글 (기본값: 비활성화)
- **감시 중지 / 감시 재시작** — watcher 토글
- **로그 열기** — Console.app으로 로그 확인
- **종료** — 트레이 앱 종료 (watcher는 계속 실행)

---

## Claude 자동 창 시작 (선택)

macOS 트레이 메뉴에서 "Claude 자동 창 시작"을 켜면, watcher가 미리 저장한 Claude 5시간 초기화 시각이 지난 뒤 하나의 `claude -p` 자식 프로세스를 실행합니다.
(GitHub 알림 조건인 300 < remaining <= 21600 제한은 워크플로우 전송에만 해당하며, 로컬 창 시작과는 무관합니다.)

- **실행 조건**: 저장된 대기 건의 초기화 시점보다 `enabled_at`이 앞서야 합니다. 그 시점에 맥이 꺼져 있었다면 다음 watcher 실행에서 해당 대기 건을 한 번 처리합니다.
- **프로세스 관리**: 데몬이 하나의 `claude -p` 자식 프로세스를 동기적으로 시작하고 종료나 타임아웃을 기다립니다. 백그라운드에 세션이 남지 않습니다.
- **타임아웃과 재시도**: 최대 3회 시도하며, 각 시도당 120초 타임아웃을 갖습니다.
- **안전 장치**: 드라이 런 모드에서는 Claude를 실행하거나 활성화 상태를 변경하지 않고 예정 동작만 로그로 보여 줍니다.
- **설정**: 환경 변수나 `config.env`의 `CLAUDE_CLI_PATH`를 읽으며, 없을 시 자동 감지된 경로를 사용합니다.

---

## macOS 언인스톨

```bash
python3 platform/macos/uninstall.py
```

언인스톨 스크립트가 수행하는 작업:

1. **watcher 데몬 중지** — launchctl unload → plist 삭제
2. **트레이 앱 중지** — launchctl unload → plist 삭제 → `~/Applications/TokenAlertTray.app` 삭제
3. **파일 삭제** (확인 후 삭제)
   - 상태 파일: `~/.token_alert_state.json`
   - 로그: `~/.claude/token_alert.log`, `~/.claude/token_alert_error.log`
   - 고정 설치 경로: `~/.local/lib/token_alert/`, `~/.config/token-alert/config.env`

> **보안 주의:** `config/config.env`는 토큰이 담겨 있으므로 직접 삭제하세요.
> ```bash
> rm config/config.env
> ```

---

## Windows 설치

### 필수 조건

```
pip install pystray Pillow
```

### 설치

```
python platform\windows\install.py
```

설치 스크립트가 수행하는 작업:

1. **고정 경로에 파일 복사** — `~\.local\lib\token_alert\src\`에 `watcher.py`, `atomic_json.py`, `activation.py`, `scheduling.py`를 설치하고 `~\.config\token-alert\config.env`를 배치
2. **TokenAlertTray.exe 빌드** — PyInstaller로 단일 실행 파일 생성 (`%LOCALAPPDATA%\TokenAlert\`)
3. **시작 프로그램 등록 여부 선택** — `y` 선택 시 `HKCU\SOFTWARE\...\Run` 레지스트리에 등록 (관리자 권한 불필요)
4. **즉시 시작** — watcher(`pythonw.exe`, 콘솔 창 없음) + 트레이 앱 백그라운드 실행

### 상태 확인

```
# watcher 실행 중 여부 (PID 파일 확인)
type %USERPROFILE%\.token_alert.pid

# 로그 확인
type %USERPROFILE%\.claude\token_alert.log
```

### 트레이 앱 메뉴

시스템 트레이 아이콘을 클릭하면:
- **● 감시 중 / ○ 감시 중지됨** — watcher 현재 상태
- **감시 중지 / 감시 재시작** — watcher 토글 (레지스트리 방식, 관리자 권한 불필요)
- **로그 열기** — 로그 파일 열기
- **종료** — 트레이 앱 종료 (watcher는 계속 실행)

### 언인스톨

```
python platform\windows\uninstall.py
```

---

## 텔레그램 봇 명령

데몬 실행 중 텔레그램에서 봇에게 직접 명령을 보낼 수 있습니다.

| 명령 | 설명 |
|------|------|
| `/status` | Codex/Claude 선택 버튼 표시 |
| `/status codex` | Codex 계정별 다음 토큰 초기화까지 남은 시간 조회 |
| `/status claude` | Claude 다음 토큰 초기화까지 남은 시간 조회 |

예시 응답:
```
⏳ 토큰 한도 현황
──────────────────
Codex work 5시간 한도
• 남은 시간: 1시간 23분
• 남은 비율: 42%
• 초기화 시각: 2026-06-22 21:30 KST
```

---

## 테스트

```bash
python3 -m unittest tests/test_watcher.py
```

---

## 문서

- [텔레그램 봇 설정](docs/telegram-setup.md)
- [GitHub Actions 설정](docs/github-setup.md)

---

## 변경 이력

### v1.1.0 (2026-06-23)

- **공급자별 예약 분리**: GitHub Actions `concurrency` 그룹을 알림 대상과 초기화 시각별로 분리하고, watcher가 같은 대상의 이전 대기 실행을 dispatch 전에 정리합니다.

### v1.0.0 (2026-06-22)

- macOS (launchd + rumps 트레이), Windows (레지스트리 시작 프로그램 + pystray 트레이) 지원
- Claude Code JSONL 파일 모니터링 → GitHub Actions dispatch → 텔레그램 알림
- `/status` 텔레그램 명령으로 남은 시간 즉시 조회
- PID 잠금 + 상태 파일 기반 중복 dispatch 방지
- 이전 워크플로우 실행 자동 취소 (`cancel_previous_workflow_runs`)
