# GitHub Actions 설정 가이드

token_alert 는 GitHub Actions 를 클라우드 스케줄러로 사용합니다.  
컴퓨터가 꺼져 있어도 GitHub 서버가 정해진 시각에 텔레그램 알림을 전송합니다.

---

## 1. 이 저장소를 본인 GitHub 계정에 포크

1. [https://github.com/YOUR_USERNAME/token_alert](https://github.com) 에서 Fork 버튼 클릭
2. 본인 계정의 저장소가 생성됩니다

또는 직접 저장소를 만들고 이 코드를 올려도 됩니다.

---

## 2. GitHub Secrets 설정

GitHub Actions 에서 텔레그램 봇 토큰과 채팅 ID 를 안전하게 사용하려면  
저장소의 Secrets 에 등록해야 합니다.

1. 본인 저장소 → **Settings** 탭 클릭
2. 왼쪽 메뉴 → **Secrets and variables** → **Actions**
3. **New repository secret** 클릭

아래 두 항목을 각각 추가합니다:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID |

---

## 3. GitHub Personal Access Token (PAT) 발급

로컬 watcher.py 가 GitHub Actions 를 원격으로 실행하려면 PAT 가 필요합니다.

1. [https://github.com/settings/tokens](https://github.com/settings/tokens) 접속
2. **Generate new token (classic)** 클릭
3. Note 입력 (예: `token_alert`)
4. 만료 기간 선택 (권장: 90일 또는 1년)
5. 아래 권한 체크:
   - ✅ `workflow` — GitHub Actions 워크플로우 실행 권한

6. **Generate token** 클릭
7. 발급된 토큰 복사 (한 번만 표시됨)

```
ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> ⚠️ PAT 는 비밀번호와 같습니다. 절대 공유하거나 GitHub 에 올리지 마세요.

---

## 4. config.env 에 입력

```env
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_OWNER=your_github_username     ← 본인 GitHub 사용자명
GITHUB_REPO=token_alert               ← 저장소 이름 (포크한 경우 그대로)
```

---

## 5. GitHub Actions 활성화 확인

포크한 저장소는 기본적으로 Actions 가 비활성화되어 있을 수 있습니다.

1. 저장소 → **Actions** 탭
2. "I understand my workflows..." 버튼이 있으면 클릭하여 활성화

---

## 6. 수동 테스트 (선택)

설치 전에 워크플로우가 정상 작동하는지 확인할 수 있습니다:

1. 저장소 → **Actions** 탭
2. **Claude Code Token Reset Notify** 워크플로우 선택
3. **Run workflow** 클릭
4. `reset_time` 입력: 현재 시각 + 1분 (UTC 기준, 예: `2026-01-01T12:01:00Z`)
5. **Run workflow** 실행
6. 약 1분 후 텔레그램에 알림이 오면 성공

---

## 주의사항

- GitHub Actions 무료 사용 한도: **월 2,000분** (공개 저장소는 무제한)
- 하루 1회 알림 기준으로 약 5분/회 → 월 150분 → 무료 한도 내에 충분히 수용 가능
- 저장소를 **비공개(private)**로 설정해도 무료 2,000분으로 충분합니다

---

## PAT 갱신 방법

PAT 가 만료되면 config.env 의 `GITHUB_TOKEN` 값만 새 토큰으로 교체하면 됩니다.

```bash
# config.env 열기
nano ~/path/to/token_alert/config/config.env
```
