# 텔레그램 봇 설정 가이드

token_alert 에서 알림을 받으려면 텔레그램 봇 토큰과 본인의 채팅 ID 가 필요합니다.

---

## 1. 봇 만들기 (BotFather)

1. 텔레그램에서 **@BotFather** 를 검색하거나 [https://t.me/BotFather](https://t.me/BotFather) 접속
2. `/newbot` 명령 입력
3. 봇 이름 입력 (예: `My Claude Alert`)
4. 봇 사용자명 입력 (반드시 `bot` 으로 끝나야 함, 예: `my_claude_alert_bot`)
5. 완료되면 아래와 같은 형식의 **봇 토큰**이 발급됩니다:

```
1234567890:AAFjkZ3-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> ⚠️ 봇 토큰은 비밀번호와 같습니다. 절대 공유하거나 GitHub 에 올리지 마세요.

---

## 2. 본인의 채팅 ID 알아내기

1. 방금 만든 봇과 대화 창 열기 (텔레그램에서 봇 검색)
2. `/start` 메시지 전송
3. 아래 URL 을 브라우저에서 열기 (봇 토큰을 실제 값으로 교체):

```
https://api.telegram.org/bot<봇_토큰>/getUpdates
```

예시:
```
https://api.telegram.org/bot1234567890:AAFjkZ3-xxx.../getUpdates
```

4. JSON 응답에서 `"chat"` → `"id"` 값을 찾습니다:

```json
{
  "result": [
    {
      "message": {
        "chat": {
          "id": 987654321,   ← 이 숫자가 본인의 채팅 ID
          "type": "private"
        }
      }
    }
  ]
}
```

> ℹ️ `/start` 메시지를 보낸 뒤 30초 이내에 URL 을 열어야 `result` 배열에 값이 있습니다.

---

## 3. config.env 에 입력

```env
TELEGRAM_BOT_TOKEN=1234567890:AAFjkZ3-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

---

## 4. 연결 테스트

설정 후 아래 명령으로 텔레그램 메시지 전송을 테스트할 수 있습니다:

```bash
# 터미널에서 실행 (토큰과 ID 를 실제 값으로 교체)
curl -s -X POST \
  "https://api.telegram.org/bot<봇_토큰>/sendMessage" \
  -d "chat_id=<채팅_ID>&text=token_alert+테스트+메시지"
```

`"ok": true` 응답이 오면 정상입니다.

---

## 참고: 그룹 채팅에서 받고 싶은 경우

1. 봇을 그룹에 초대
2. 그룹에서 `/start@봇이름` 입력
3. `getUpdates` 에서 `"chat" → "id"` 확인 (그룹 ID 는 음수)
4. 해당 ID 를 `TELEGRAM_CHAT_ID` 에 입력
