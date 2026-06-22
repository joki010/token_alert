# token_alert

Claude Code의 5시간 토큰 사용량 초기화 시각을 자동으로 계산하여, 컴퓨터가 꺼져 있어도 텔레그램으로 알림을 보내주는 도구입니다.

## 작동 원리

```
로컬 감지 데몬 (launchd)
    ↓ ~/.claude/projects/**/JSONL 파일 모니터링 (10분마다)
    ↓ 5시간 창에서 가장 오래된 메시지 시각 + 5시간 = 초기화 예정 시각 계산
    ↓ GitHub Actions 워크플로우 dispatch (초기화 시각 전달)

GitHub Actions (클라우드)
    ↓ 초기화 시각까지 sleep
    ↓ Telegram Bot API 호출

텔레그램
    → 사용자에게 알림 도착 (컴퓨터 꺼져도 OK)
```

## 빠른 시작

### 1. 필수 준비

- 텔레그램 계정 + 봇 토큰 ([텔레그램 봇 설정 가이드](docs/telegram-setup.md))
- GitHub 계정 + Personal Access Token ([GitHub 설정 가이드](docs/github-setup.md))
- Python 3.8+

### 2. 이 저장소를 GitHub에 포크 또는 클론

```bash
git clone https://github.com/YOUR_USERNAME/token_alert.git
cd token_alert
```

### 3. 설정 파일 작성

```bash
cp config/config.env.example config/config.env
# config/config.env 파일을 편집하여 토큰 입력
```

### 4. 설치

### macOS

```bash
python3 platform/macos/install.py
```

### Windows

```
python platform\windows\install.py
```

자세한 내용: [docs/install-windows.md](docs/install-windows.md)

설치가 완료되면 macOS launchd에 백그라운드 데몬이 등록됩니다.  
트레이 아이콘이나 메뉴 바 표시 없이 완전히 숨겨진 상태로 실행됩니다.

## 설정 파일

`config/config.env` 파일을 작성합니다:

```env
# 텔레그램 봇 토큰 (BotFather에서 발급)
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 텔레그램 채팅 ID (본인 chat_id)
TELEGRAM_CHAT_ID=123456789

# GitHub Personal Access Token (workflow 실행 권한 필요)
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# GitHub 저장소 (포크한 저장소)
GITHUB_OWNER=your_github_username
GITHUB_REPO=token_alert
```

## 문서

- [텔레그램 봇 설정 가이드](docs/telegram-setup.md)
- [GitHub Actions 설정 가이드](docs/github-setup.md)
- [설치 가이드](docs/install-guide.md)
- [완전 삭제 가이드](docs/uninstall-guide.md)

## 완전 삭제

### macOS

```bash
python3 platform/macos/uninstall.py
```

### Windows

```
python platform\windows\uninstall.py
```

자세한 내용은 [완전 삭제 가이드](docs/uninstall-guide.md)를 참고하세요.

## 요구사항

- macOS 12+
- Python 3.8+
- GitHub 계정 (무료)
- 텔레그램 계정 (무료)
