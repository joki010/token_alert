# 완전 삭제 가이드

token_alert 를 완전히 제거하는 방법입니다.

---

## 자동 삭제 (권장)

```bash
cd /path/to/token_alert
python3 uninstall.py
```

스크립트가 안내에 따라 선택적으로 삭제합니다.

---

## 수동 삭제 (단계별)

### 1단계 — 데몬 중지

```bash
launchctl unload ~/Library/LaunchAgents/com.token-alert.watcher.plist
```

### 2단계 — launchd plist 삭제

```bash
rm ~/Library/LaunchAgents/com.token-alert.watcher.plist
```

### 3단계 — 상태 파일 삭제

```bash
rm ~/.token_alert_state.json
```

### 4단계 — 로그 파일 삭제 (선택)

```bash
rm ~/.claude/token_alert.log
rm ~/.claude/token_alert_error.log
```

### 5단계 — 프로젝트 디렉터리 삭제

```bash
rm -rf /path/to/token_alert
```

---

## 외부 서비스 정리

token_alert 를 더 이상 사용하지 않는다면 아래 외부 서비스도 정리하는 것을 권장합니다.

### 텔레그램 봇 삭제

봇을 완전히 삭제하면 다른 사람이 같은 이름으로 만들 수 없습니다.

1. 텔레그램에서 **@BotFather** 열기
2. `/deletebot` 명령 입력
3. 삭제할 봇 선택
4. `Yes, I am totally sure.` 입력하여 확인

### GitHub PAT 폐기

1. [https://github.com/settings/tokens](https://github.com/settings/tokens) 접속
2. `token_alert` 용으로 만든 토큰 찾기
3. **Delete** 클릭

### GitHub 저장소 삭제 (선택)

저장소 전체를 삭제하려면:

1. 저장소 → **Settings** 탭
2. 맨 아래 **Danger Zone** → **Delete this repository**
3. 저장소 이름 입력 후 확인

### GitHub Actions Secrets 삭제

저장소를 삭제하지 않고 Secrets 만 삭제하려면:

1. 저장소 → **Settings** → **Secrets and variables** → **Actions**
2. `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 각각 삭제

---

## 삭제 후 확인

모두 완료된 후 아래 명령으로 데몬이 완전히 제거되었는지 확인합니다:

```bash
launchctl list | grep token-alert
```

아무 출력도 없으면 정상적으로 삭제된 것입니다.

```bash
ls ~/Library/LaunchAgents/ | grep token-alert
```

파일이 없으면 완전히 삭제된 것입니다.

---

## 삭제 후 재설치

나중에 다시 사용하고 싶다면 [설치 가이드](install-guide.md) 를 따라 다시 설치할 수 있습니다.
