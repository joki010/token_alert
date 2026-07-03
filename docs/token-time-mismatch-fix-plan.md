# 텔레그램 남은 시간 불일치 수정 계획

## 배경

텔레그램 `/status`가 보여주는 남은 시간과 실제 Claude Code 토큰 초기화까지 남은 시간이 일치하지 않는 문제가 있다.

5.5 pro 검토 결과, 가장 큰 원인은 `/status`가 실제 Claude Code 한도 값을 직접 보지 못하고, 없으면 마지막 알림 예약값인 `scheduled_reset_time`을 실제 초기화 시각처럼 쓰는 구조다.

검토 세션:
<https://chatgpt.com/c/6a4753ba-ab78-83e8-b5ea-5e5185aa1161>

## 원인

1. `src/watcher.py`는 `~/.claude/token_alert_usage.json`의 `five_hour_resets_at`을 읽지만, 프로젝트 안에는 이 파일을 쓰는 코드가 없다.

2. `/status` 처리에서 usage 파일을 읽지 못하면 `~/.token_alert_state.json`의 `scheduled_reset_time`을 폴백으로 사용한다. 이 값은 실제 초기화 시각이 아니라 마지막으로 GitHub Actions 알림 예약에 성공한 시각이다.

3. JSONL 폴백은 `~/.claude/projects/**/*.jsonl`에서 최근 5시간 안의 가장 오래된 `timestamp`에 5시간을 더한다. 이 값은 로컬 로그 기반 추정값이라 실제 Claude Code 한도 초기화 시각과 항상 같을 수 없다.

4. 현재 코드는 5시간 한도만 다룬다. 실제 제한 원인이 7일 한도라면 `/status`가 5시간 초기화 시각만 보여줘도 사용자는 아직 풀리지 않은 것처럼 느낄 수 있다.

5. `NOTIFY_ADVANCE_SECONDS`가 0보다 크면 `reset_time - advance`로 만든 `notify_time`을 workflow 입력 `reset_time`에 넘긴다. 이 경우 알림 시각과 실제 초기화 시각이 섞인다.

## 수정 계획

1. Claude Code `statusLine`에서 받은 `rate_limits`를 `~/.claude/token_alert_usage.json`에 저장하는 작은 writer를 추가한다.

2. 저장 필드는 기존 호환을 위해 `five_hour_resets_at`을 유지하고, 가능하면 아래 필드도 함께 저장한다.

   - `seven_day_resets_at`
   - `five_hour_used_percentage`
   - `seven_day_used_percentage`
   - `updated_at`

3. `read_reset_time_from_usage_file()`를 확장한다.

   - 기존 flat `five_hour_resets_at` 읽기 유지
   - `rate_limits.five_hour.resets_at` 형태도 읽기
   - 값이 과거이면 무시
   - `updated_at`이 너무 오래되면 무시

4. `/status`에서 `scheduled_reset_time`을 실제 초기화 시각처럼 쓰지 않는다.

   - usage cache가 있으면 그 값을 표시
   - 7일 한도 값도 있으면 함께 표시
   - cache가 없으면 "아직 Claude Code 한도 값을 받은 적이 없습니다" 류의 안내 표시
   - JSONL 폴백을 남길 경우 "추정값"으로 표시

5. `run_once()`와 `/status`가 같은 helper를 쓰게 한다.

   예: `get_current_limit_status()`가 usage cache를 읽고 5시간/7일 상태를 반환한다.

6. `NOTIFY_ADVANCE_SECONDS` 처리에서 알림 시각과 실제 초기화 시각을 분리한다.

   - workflow 입력을 `reset_time`, `notify_time`으로 나누거나
   - 메시지를 "초기화 완료"가 아니라 "초기화 예정"으로 바꾼다.

## 테스트 계획

1. `read_reset_time_from_usage_file()`가 `rate_limits.five_hour.resets_at` 형태의 cache를 읽는지 테스트한다.

2. 과거 reset 시각이나 오래된 `updated_at`을 가진 cache를 무시하는지 테스트한다.

3. usage cache가 없고 state에 미래 `scheduled_reset_time`이 있어도 `/status`가 실제 남은 시간처럼 표시하지 않는지 테스트한다.

4. usage cache가 있으면 state보다 우선하는 기존 테스트를 실제 임시 파일 기반으로 강화한다.

5. 5시간과 7일 한도 값이 함께 있을 때 `/status`가 둘 다 다루는지 테스트한다.

6. JSONL 폴백을 유지한다면, 응답에 추정값임이 드러나는지 테스트한다.

7. dispatch 실패 뒤에도 `/status`가 낡은 state 값을 실제 초기화 시각처럼 표시하지 않는지 테스트한다.

8. 초기화까지 5분 이하라 dispatch를 건너뛰는 경우에도 stale state가 `/status`에 노출되지 않는지 테스트한다.

9. `NOTIFY_ADVANCE_SECONDS`가 있을 때 workflow에 알림 시각과 실제 초기화 시각이 분리되어 전달되는지 테스트한다.

## 우선순위

1. `/status`에서 `scheduled_reset_time` 실제값 폴백 제거
2. usage cache reader 확장
3. Claude Code `statusLine` writer 추가
4. 테스트 보강
5. workflow 알림 시각 분리

