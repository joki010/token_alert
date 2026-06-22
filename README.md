# token_alert

Claude Code의 5시간 토큰 초기화 시각을 자동으로 계산하여 컴퓨터가 꺼져 있어도 텔레그램으로 알림을 보내주는 도구.

## 작동 원리

```
로컬 감지 데몬 (launchd / Task Scheduler)
  ↓  ~/.claude/projects/**/JSONL 파일 모니터링 (10분마다)
  ↓  5시간 창에서 가장 오래된 메시지 + 5h = 초기화 예정 시각 계산
  ↓  GitHub Actions workflow_dispatch 호출 (reset_time 전달)

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
| `POLL_INTERVAL` | 감지 주기 초 (기본: 600) |
| `NOTIFY_ADVANCE_SECONDS` | 초기화 시각 몇 초 전에 알림 (기본: 0) |

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
   - `~/.local/lib/token_alert/src/watcher.py`
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
- **감시 중지 / 감시 재시작** — watcher 토글
- **로그 열기** — Console.app으로 로그 확인
- **종료** — 트레이 앱 종료 (watcher는 계속 실행)

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
- **고정 경로에 파일 복사** — `~\.local\lib\token_alert\src\watcher.py`, `~\.config\token-alert\config.env`
- Task Scheduler에 `TokenAlertWatcher`, `TokenAlertTray` 등록 (로그인 시 자동 시작, 고정 경로 참조)
- `TokenAlertTray`는 `pythonw.exe`로 실행 (콘솔 창 없음)

### 상태 확인

```
schtasks /query /tn TokenAlertWatcher
type %USERPROFILE%\.claude\token_alert.log
```

### 언인스톨

```
python platform\windows\uninstall.py
```

---

## 텔레그램 봇 명령

데몬 실행 중 텔레그램에서 봇에게 직접 명령을 보낼 수 있습니다.

| 명령 | 설명 |
|------|------|
| `/status` | 다음 토큰 초기화까지 남은 시간 조회 |

예시 응답:
```
⏳ 다음 초기화까지 1시간 23분 남았습니다.
예정 시각: 2026-06-22 21:30 KST
```

---

## 테스트

```bash
python3 -m pytest tests/test_watcher.py -v
```

---

## 문서

- [텔레그램 봇 설정](docs/telegram-setup.md)
- [GitHub Actions 설정](docs/github-setup.md)
