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
