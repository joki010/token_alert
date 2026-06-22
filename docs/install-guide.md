# 설치 가이드

---

## macOS 설치

### 전제 조건

- macOS 12 이상
- Python 3.8 이상 (기본 내장)
- GitHub 계정 ([설정 가이드](github-setup.md))
- 텔레그램 계정 ([설정 가이드](telegram-setup.md))

### 단계별 설치

**1단계 — 저장소 클론**

```bash
cd ~/Developer   # 원하는 위치로 변경
git clone https://github.com/YOUR_USERNAME/token_alert.git
cd token_alert
```

**2단계 — 설정 파일 작성**

```bash
cp config/config.env.example config/config.env
```

`config/config.env` 파일을 편집합니다:

```env
TELEGRAM_BOT_TOKEN=1234567890:AAFjkZ3-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_OWNER=your_github_username
GITHUB_REPO=token_alert
```

각 값 발급 방법:
- 텔레그램: [telegram-setup.md](telegram-setup.md)
- GitHub: [github-setup.md](github-setup.md)

**3단계 — 설치 실행**

```bash
python3 install.py
```

설치 스크립트가 자동으로:
- 설정 파일 유효성 검사
- macOS launchd 데몬 등록 (백그라운드 실행)
- 즉시 시작

**4단계 — 동작 확인**

```bash
# 데몬 실행 상태 확인
launchctl list com.token-alert.watcher

# 실시간 로그 확인
tail -f ~/.claude/token_alert.log

# 한 번 테스트 실행 (실제 dispatch 없이)
python3 src/watcher.py --dry-run --once --verbose
```

---

## 데몬 특성 (macOS)

token_alert 데몬은 다음 특성을 가집니다:

| 특성 | 설명 |
|------|------|
| 실행 방식 | macOS launchd 백그라운드 데몬 |
| 메뉴 바 아이콘 | ❌ 없음 |
| 독(Dock) 아이콘 | ❌ 없음 |
| 트레이 아이콘 | ❌ 없음 |
| 로그인 시 자동 시작 | ✅ |
| 크래시 시 자동 재시작 | ✅ |
| CPU/메모리 사용량 | 매우 낮음 (10분마다 잠깐 실행) |

---

## 감지 주기 조정

기본 감지 주기는 10분(600초)입니다. `config.env` 에서 변경 가능합니다:

```env
# 5분마다 확인
POLL_INTERVAL=300

# 30분마다 확인 (배터리 절약)
POLL_INTERVAL=1800
```

변경 후 데몬을 재시작합니다:

```bash
launchctl unload ~/Library/LaunchAgents/com.token-alert.watcher.plist
launchctl load ~/Library/LaunchAgents/com.token-alert.watcher.plist
```

---

## 자주 묻는 질문

**Q: 컴퓨터가 꺼져 있을 때도 알림이 오나요?**  
A: 네. 로컬 데몬이 초기화 시각을 계산한 뒤 GitHub Actions 에 예약합니다.  
   컴퓨터가 꺼지더라도 GitHub 서버에서 텔레그램 알림을 전송합니다.

**Q: 처음 실행 시 바로 알림이 오나요?**  
A: 최근 5시간 내에 Claude Code 사용 기록이 있어야 초기화 시각을 계산합니다.  
   Claude Code 를 사용하면 자동으로 감지됩니다.

**Q: 알림이 중복으로 오나요?**  
A: 같은 초기화 시각으로 이미 예약된 경우 중복 dispatch 를 방지합니다.

**Q: GitHub Actions 실행 비용이 드나요?**  
A: 공개 저장소는 무료, 비공개 저장소는 월 2,000분 무료 한도 이내로 충분합니다.
