#!/bin/bash
TITLE="$1"
MESSAGE="$2"

# 토글 정책이 켜진 경우에만 알림을 보낸다. 정책 파일이 없거나 손상되면
# 기본값인 비활성으로 처리한다.
POLICY_FILE="$HOME/.config/token-alert/notify-policy.json"
if ! python3 -c '
import json
import sys
from pathlib import Path

try:
    with Path(sys.argv[1]).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    enabled = isinstance(data, dict) and data.get("enabled") is True
except Exception:
    enabled = False

raise SystemExit(0 if enabled else 1)
' "$POLICY_FILE"; then
  exit 0
fi

# detect_terminal_app.sh 가 SessionStart 때 저장한 앱 번들 ID 사용
# 파일이 없으면 detect_terminal_app.sh 를 즉석 실행 후 읽기
APP_FILE="$HOME/.config/token-alert/.notify_app"
DETECT_SCRIPT="$HOME/.local/lib/token_alert/notify/detect_terminal_app.sh"
if [ ! -f "$APP_FILE" ]; then
  bash "$DETECT_SCRIPT" 2>/dev/null || true
fi
APP=$(cat "$APP_FILE" 2>/dev/null || echo "com.apple.Terminal")

# 시각 알림 전송 — 클릭 시 감지된 터미널 앱으로 이동
terminal-notifier -title "$TITLE" -message "$MESSAGE" -activate "$APP"

# 현재 맥 출력 볼륨에 맞춰 소리 재생
SYS_VOL=$(osascript -e "output volume of (get volume settings)" 2>/dev/null)
SYS_MUTED=$(osascript -e "output muted of (get volume settings)" 2>/dev/null)

if [ "$SYS_MUTED" != "true" ] && [ -n "$SYS_VOL" ] && [ "$SYS_VOL" -gt 0 ]; then
  # python3 대신 awk 사용 — 추가 프로세스 없이 부동소수점 계산
  VOL_FLOAT=$(awk "BEGIN {printf \"%.2f\", $SYS_VOL/100}")
  # 시스템에 설정된 경보음 파일 사용, 없으면 Tink 폴백
  ALERT_SOUND=$(defaults read -g "com.apple.sound.beep.sound" 2>/dev/null)
  if [ -z "$ALERT_SOUND" ] || [ ! -f "$ALERT_SOUND" ]; then
    ALERT_SOUND="/System/Library/Sounds/Tink.aiff"
  fi
  afplay "$ALERT_SOUND" -v "$VOL_FLOAT" &
fi
