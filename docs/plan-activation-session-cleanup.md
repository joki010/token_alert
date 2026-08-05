# Claude 자동 창 시작 세션 비영속화 계획

## 배경

`token_alert`의 Claude 자동 창 시작은 5시간 사용량 창 초기화 이후 독립 `claude -p` 요청을 한 번 실행해 다음 창을 연다. 현재 구현은 자식 프로세스 종료와 기존 대화형 세션 비간섭까지는 보장하지만, Claude Code가 디스크에 남기는 세션 아티팩트는 정리하지 않는다.

실제 환경에서는 자동 실행 잔여 세션이 `~/.claude/projects/-/<uuid>.jsonl`에 쌓인다.

| 항목 | 관측값 (2026-08-04 기준) |
|------|--------------------------|
| 경로 | `~/.claude/projects/-/` |
| 패턴 | `cwd="/"`, 프롬프트 `"."`, `entrypoint=sdk-cli` |
| 개수 | 58개 |
| 총 용량 | 약 3.0MB |
| 평균 용량 | 약 53KB |

문서 표현도 프로세스 종료와 세션 파일 미잔류를 구분하지 못해, "백그라운드에 세션이 남지 않습니다"라는 설명이 디스크 관점에서는 사실과 어긋난다.

이 계획은 자동 창 시작의 본 목적(사용량 창 트리거)은 유지하면서, 자동 실행으로 생기는 세션 이력·디스크 잔여를 없애거나 안전하게 제거하는 구현 범위를 고정한다.

---

## 목표

1. 자동 창 시작 기능의 본 목적(Claude 5시간 창 트리거)을 유지한다.
2. 자동 실행으로 생긴 Claude Code 세션이 이력/디스크에 남지 않거나, 남아도 같은 실행 흐름 안에서 안전하게 제거된다.
3. 사용자가 직접 만든 대화 세션과 다른 프로젝트 이력은 절대 건드리지 않는다.
4. JSONL 폴백 추정 로직을 자동 세션 잔여물로 오염시키지 않는다.
5. 기존 activation 멱등·재시도·timeout 계약을 깨지 않는다.

---

## 비목표

- 사용자 일반 세션의 자동 청소
- `~/.claude/projects/**` 전체 일괄 삭제
- 휴리스틱 기반 상시 청소 데몬
- watcher 전면 재작성
- 자동 활성화를 위한 새 대화형 세션 유지

---

## 현재 상태

| 항목 | 현재 | 문제점 |
|------|------|--------|
| 실행 명령 | `claude -p <prompt>` | 기본 동작이 세션을 디스크에 저장 |
| 프로세스 관리 | 동기 대기 후 terminate/kill | 프로세스 종료는 되지만 세션 파일은 남음 |
| 작업 디렉터리 | launchd 기본(실측 `cwd="/"`) | 세션이 `projects/-/` 아래로 모임 |
| 문서 | "세션이 남지 않음" 표현 | 프로세스와 디스크 잔여를 혼동 |
| 잔여 정리 | 없음 | 자동 실행마다 jsonl 누적 |
| JSONL 폴백 | `projects/**/*.jsonl` 스캔 | 자동 세션이 초기화 시각 추정에 섞일 수 있음 |

관련 코드:

- `src/activation.py` — `_spawn_and_wait()`, `activate_claude_reset()`
- `platform/macos/tray.py` — 자동 창 시작 토글
- `tests/test_activation.py` — 성공/실패/timeout/재시도/중복 방지
- `src/watcher.py` — JSONL 폴백 스캔

관련 문서/스펙:

- `README.md` Claude 자동 창 시작 절
- `CLAUDE.md` Claude 자동 창 시작 절
- `config/config.env.example`
- `.gjc/.../specs/deep-interview-claude-window-activation.md`

---

## 접근 방식 비교

### A안. 생성 방지 (1순위)

```text
claude -p "." --no-session-persistence
```

- print 모드에서 세션을 디스크에 저장하지 않는 CLI 공식 옵션
- 구현 변경이 가장 작음
- 사후 삭제 레이스·오삭제 위험이 없음
- 기존 잔여 58개는 별도 1회 정리 필요

검증 포인트:

- 플래그 사용 시에도 5시간 창 트리거가 되는지
- hook 오류/세션 파일 생성이 실제로 사라지는지
- 구버전 CLI가 플래그를 모를 때 fallback 가능한지

### B안. 지정 세션 생성 후 삭제 (fallback)

1. activation 전에 `session_id = uuid4()` 생성
2. `--session-id <uuid>`로 실행
3. 종료 후 허용 경로의 해당 파일만 삭제

- 삭제 대상을 정확히 알 수 있음
- 부수 아티팩트 범위(`session-env`, lock, history 등)를 화이트리스트로 제한해야 함
- 실패 시 orphan 가능
- A안보다 복잡하므로 기본안이 아니라 안전망으로 둔다

### C안. 휴리스틱 일괄 정리

- `projects/-` + 프롬프트 `.` 같은 패턴으로 후보 수집
- 기존 잔여 1회 정리에는 유용
- 상시 루틴으로 쓰기엔 오삭제 위험
- 상시 정책으로는 채택하지 않는다

### D안. 작업 디렉터리 고정 + bare 최소화

- 전용 cwd, 필요 시 `--bare`
- 세션 누적 자체를 막지는 못함
- A/B의 보조책으로만 사용

---

## 권장 전략

제목은 "세션 자동 삭제"보다 **자동 창 시작 세션 비영속화(+안전 삭제 fallback)** 로 고정한다.

구현 순서:

1. **A안 생성 방지**를 기본 경로로 적용
2. **B안 지정 삭제**를 fallback/안전망으로 추가
3. **C안 휴리스틱 정리**는 기존 잔여 1회 정리용으로만 사용
4. 문서·테스트를 함께 갱신

성공 정의:

1. 자동 실행 후 새 세션 파일이 생기지 않거나
2. 생기더라도 같은 실행 흐름 안에서 제거된다

---

## 구현 계획

### Phase 0. 범위 고정

삭제/비영속 대상:

- token_alert 자동 활성화가 만든 세션만

금지:

- 사용자 대화 세션 삭제
- 다른 프로젝트 jsonl 삭제
- 임의 패턴 일괄 삭제

판정 원칙:

- activation 성공/실패는 창 트리거 결과로 유지
- cleanup 실패는 soft-fail로 분리해 재시도 낭비를 막는다

### Phase 1. 생성 방지 (Must)

변경 지점: `src/activation.py` `_spawn_and_wait`

현재:

```python
[str(cli_path), "-p", prompt]
```

권장:

```python
[str(cli_path), "-p", prompt, "--no-session-persistence"]
```

추가 권장:

- 전용 cwd: `~/.cache/token-alert/activation-workdir`
- 가능하면 `--output-format text` 명시
- 설정 키로 on/off 가능:
  - `CLAUDE_ACTIVATION_NO_SESSION_PERSISTENCE=1` (기본 on)

호환 정책:

1. 플래그 포함 실행
2. CLI가 옵션 미지원으로 실패하면 1회 fallback(플래그 없이) + warning
3. fallback 시에는 Phase 2 삭제 경로 사용

### Phase 2. 지정 세션 삭제 안전망 (Should)

A안이 불가/실패할 때만 사용:

1. activation 전에 `session_id` 생성
2. spawn 인자에 `--session-id <uuid>` 추가
3. 종료 후 허용 경로에서만 삭제
   - 기본: `~/.claude/projects/-/<uuid>.jsonl`
   - 필요 시 같은 uuid 부수 파일만 화이트리스트 삭제
4. 결과를 activation record에 기록
   - `session_cleanup: succeeded | skipped | failed`
5. cleanup 실패해도 activation 본 결과(`succeeded`/`final_failed`/`unknown`)는 유지

안전 규칙:

- 절대 경로 화이트리스트 밖은 삭제 금지
- `session_id`가 없거나 uuid 형식이 아니면 삭제 금지
- 심볼릭 링크 따라가기 금지 또는 resolve 후 허용 루트 검증

### Phase 3. 기존 잔여물 1회 정리 (Must, 수동/옵트인)

대상 후보 조건(모두 충족):

1. 경로가 `~/.claude/projects/-/*.jsonl`
2. user/queue content가 정확히 activation prompt(기본 `.`)
3. `entrypoint`가 `sdk-cli` 계열
4. 가능하면 생성 시각이 activation 기록 근처

절차:

1. dry-run으로 후보 목록 출력
2. 사용자 확인
3. 삭제 실행
4. 결과 요약(개수, 용량)

기본은 수동/옵트인. watcher 상시 청소 루프에는 넣지 않는다.

구현 형태 후보:

- 일회성 관리 스크립트
- 또는 `watcher.py`/관리 명령의 `--cleanup-activation-sessions --dry-run` 플래그

### Phase 4. 관측·문서·테스트 (Must)

문서 수정:

- "프로세스 미잔류"와 "세션 파일 미잔류"를 분리 기술
- 대상 파일:
  - `README.md`
  - `CLAUDE.md`
  - `config/config.env.example`
  - 필요 시 deep-interview 스펙 후속 메모

설정 문서화:

```env
# 자동 창 시작 시 세션 디스크 저장 방지 (기본: 1)
# CLAUDE_ACTIVATION_NO_SESSION_PERSISTENCE=1

# 세션 저장 방지 실패 시 session-id 기반 삭제 fallback (기본: 1)
# CLAUDE_ACTIVATION_SESSION_CLEANUP=1
```

테스트:

1. spawn 인자에 `--no-session-persistence` 포함
2. 구버전 미지원 시 fallback
3. session-id 지정 시 허용 경로만 삭제
4. 허용 외 경로 삭제 시도 금지
5. cleanup 실패가 activation 성공 판정을 뒤집지 않음
6. 기존 activation 멱등/재시도/timeout 테스트 회귀 없음
7. (선택) 임시 fixture에서 파일 미생성/삭제 통합 확인

---

## 변경 파일 예상

| 파일 | 변경 내용 |
|------|-----------|
| `src/activation.py` | spawn 인자, 선택 cwd, session-id, cleanup 기록 |
| `tests/test_activation.py` | 비영속 플래그, fallback, 안전 삭제 테스트 |
| `README.md` | 프로세스/세션 파일 구분 설명 |
| `CLAUDE.md` | 동일 |
| `config/config.env.example` | 새 설정 키 주석 |
| (선택) `scripts/cleanup_activation_sessions.py` 또는 watcher 관리 플래그 | 기존 잔여 1회 정리 |

Windows 동등 처리는 현재 자동 창 시작이 macOS 중심이므로 Could로 남긴다. 공용 `activation.py`를 고치면 Windows에서도 같은 명령 경로를 타게 된다.

---

## 위험과 대응

| 위험 | 영향 | 대응 |
|------|------|------|
| `--no-session-persistence`가 창 트리거를 약화 | 기능 목적 실패 | 실제 1회 안전 검증 후 기본 on |
| 구버전 CLI 미지원 | 활성화 실패 | capability probe 또는 실패 시 fallback |
| 사후 삭제 오삭제 | 사용자 세션 손실 | uuid 지정 + 경로 화이트리스트 |
| JSONL 폴백 오염 | 초기화 시각 추정 왜곡 | 생성 방지 우선 |
| launchd 환경 hook 오류 로그 과다 | 세션 비대, 잡음 | 전용 cwd, 필요 시 bare 검토 |
| cleanup 실패를 activation 실패로 오판 | 재시도 낭비 | cleanup soft-fail 분리 |
| 기존 잔여 휴리스틱 오탐 | 엉뚱한 세션 삭제 | dry-run 필수, 다중 조건 일치만 삭제 |

---

## 우선순위

### Must

1. spawn에 `--no-session-persistence` 기본 적용
2. 관련 단위 테스트
3. 문서 표현 수정(프로세스 vs 세션 파일)
4. 기존 잔여 세션 1회 정리 절차(최소 dry-run)

### Should

1. session-id 기반 삭제 fallback
2. 전용 cwd
3. cleanup 결과 상태 기록

### Could

1. `--bare` 적용 여부 실험
2. 텔레그램/로그 cleanup 요약
3. Windows 전용 경로/문서 보강
4. 정리 명령 자동화 UX

---

## 수용 기준

- [ ] 자동 창 시작이 켠 상태에서 초기화 이후 `claude -p`가 기존과 같이 한 번 실행된다
- [ ] 기본 경로에서 새 세션 jsonl이 생기지 않는다
- [ ] 세션 저장 방지 실패 시 fallback이 동작하고, 지정 session-id 파일만 삭제한다
- [ ] 허용 경로 밖 파일은 삭제하지 않는다
- [ ] cleanup 실패가 activation 성공/실패 판정을 바꾸지 않는다
- [ ] 기존 activation 단위 테스트와 신규 테스트가 모두 통과한다
- [ ] README/CLAUDE/config 예제가 프로세스 종료와 세션 비영속화를 구분해 설명한다
- [ ] 기존 잔여 세션 dry-run 목록을 만들 수 있고, 확인 후 1회 정리할 수 있다

---

## 검증 계획

### 단위 테스트

- spawn 인자 스냅샷
- policy/state 멱등성 회귀
- cleanup 화이트리스트 검증
- soft-fail 기록

### 안전 실측 (토큰 소비 가능, 별도 단계)

1. 트레이에서 자동 창 시작을 켠 뒤 dry-run 로그 확인
2. 테스트용 최소 요청 1회 실행
3. 실행 전후 `~/.claude/projects/-/` 파일 수 비교
4. 5시간 창 트리거 여부 확인
5. 실패 시 fallback 경로 수동 확인

### 문서 검증

- "백그라운드 세션이 남지 않음" 류 표현이 프로세스/디스크를 혼동하지 않는지 확인
- 새 환경 변수 기본값과 설명이 config 예제와 일치하는지 확인

---

## 권장 구현 순서

1. `activation.py` spawn 인자에 `--no-session-persistence` 추가
2. 단위 테스트 보강
3. 문서/config 예제 수정
4. 기존 잔여 dry-run 정리 도구 추가
5. session-id cleanup fallback 추가
6. 실제 1회 안전 검증

---

## 결정 메모

- 기본 전략은 **삭제보다 생성 방지**
- 삭제는 **uuid 지정 + 경로 화이트리스트**만 허용
- 기존 잔여 정리는 **상시 루프가 아니라 1회/옵트인**
- 기능 성공 판정은 계속 **창 트리거 결과**를 기준으로 두고, 세션 정리는 부가 품질 지표로 취급

---

## 구현 운영안 (가벼운 방식, 확정)

원격 원본이 GitHub에 이미 있으므로, worktree 분리는 쓰지 않는다.  
로컬 feature 브랜치에서 구현하고, 수정본이 완전해질 때까지 푸시하지 않는 방식으로 원본을 보호한다.

### 전제

| 항목 | 결정 |
|------|------|
| 작업 위치 | 현재 저장소 (`/Users/jaewon/Developer/99.유틸/token_alert`) |
| 기반 브랜치 | `codex-claude-usage-alerts` |
| 구현 브랜치 | `feat/activation-session-cleanup` |
| worktree | 사용하지 않음 |
| 원격 푸시 | 구현·검증 완료 전 금지 |
| 커밋 | 로컬 커밋은 가능, 단 사용자 명시 요청 시에만 |
| 설치본 배포 | `install.py` / LaunchAgent reload / 실사용 경로 덮어쓰기 금지 |
| 실세션 삭제 실측 | dry-run 먼저, apply는 별도 확인 후 |

### 왜 이것으로 충분한가

- GitHub의 `origin/codex-claude-usage-alerts`가 복구 기준점 역할
- 로컬에서 망쳐도 원격에서 다시 받을 수 있음
- 실행 중 데몬은 `~/.local/lib/token_alert` 설치본을 쓰므로, 소스만 수정하면 실사용 프로그램은 바로 바뀌지 않음
- worktree는 폴더 분리 이점이 있으나, 현재 목표(원격 원본 보호)에는 과함

### 시작 절차

```bash
cd "/Users/jaewon/Developer/99.유틸/token_alert"
git switch codex-claude-usage-alerts
git switch -c feat/activation-session-cleanup
```

### 구현 중 가드레일

1. `feat/activation-session-cleanup`에서만 코드 수정
2. `git push` 하지 않음
3. 실사용 설치본에 배포하지 않음
4. `~/.claude/projects` 실삭제는 dry-run 검증 후
5. 단위 테스트로 회귀 확인:
   ```bash
   python -m pytest tests/test_activation.py -q
   python -m pytest tests/test_watcher.py -q
   ```

### 실패 시 복구

```bash
# 작업 브랜치 버리고 기반 브랜치로 복귀
git switch codex-claude-usage-alerts
git branch -D feat/activation-session-cleanup

# 또는 워킹트리 변경만 되돌리기
git restore .
git clean -fd   # untracked까지 지울 때만, 실행 전 목록 확인
```

원격 기준점으로 완전히 맞출 때:

```bash
git fetch origin
git switch codex-claude-usage-alerts
git reset --hard origin/codex-claude-usage-alerts
```

### 완료 후

1. 로컬 테스트 통과
2. 문서와 설정 예제 동기화 확인
3. 필요 시 안전 실측
4. 그다음에만 푸시/PR 여부를 사용자에게 확인

### 구현 순서 (이 운영안 기준)

1. feature 브랜치 생성
2. W1 세션 비영속 플래그
3. W2 설정/fallback
4. W5 문서 초안
5. W3 session-id cleanup
6. W4 잔여 정리 도구
7. W5 문서 최종
8. 로컬 검증
9. 푸시 여부는 별도 승인
