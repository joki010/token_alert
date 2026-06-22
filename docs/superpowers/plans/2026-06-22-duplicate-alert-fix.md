# 중복 알림 방지 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** watcher.py가 두 개 동시 실행될 때 발생하는 중복 dispatch를 PID 파일로 차단하고, 초기화 시각이 바뀔 때 이전 GitHub Actions 실행을 취소하여 알림이 한 번만 오도록 수정한다.

**Architecture:** (1) 시작 시 PID 파일을 생성해 두 번째 인스턴스가 즉시 종료하도록 하고, (2) 새 reset_time을 dispatch하기 직전에 진행 중인 이전 워크플로우 실행을 GitHub API로 취소한다. 두 수정 모두 `src/watcher.py` 단독으로 완결되며 외부 의존성을 추가하지 않는다.

**Tech Stack:** Python 3.11+, 표준 라이브러리(`os`, `signal`, `atexit`, `urllib`), pytest

---

## 파일 변경 지도

| 경로 | 변경 내용 |
|---|---|
| `src/watcher.py` | `PID_FILE` 상수 추가, `acquire_pid_lock()` / `release_pid_lock()` 함수 추가, `cancel_previous_workflow_runs()` 함수 추가, `main()` 및 `dispatch_github_workflow()` 수정 |
| `tests/test_watcher.py` | 새로 생성 — PID 잠금 및 워크플로우 취소 단위 테스트 |

---

## Task 1: 테스트 환경 준비

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_watcher.py`

- [ ] **Step 1: tests 패키지 초기화 파일 생성**

`tests/__init__.py` 를 빈 파일로 생성한다.

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 2: pytest 설치 확인**

```bash
python3 -m pytest --version
```

기댓값: `pytest 7.x.x` 또는 그 이상 출력. 없으면 `pip3 install pytest` 실행.

- [ ] **Step 3: 커밋**

```bash
git add tests/__init__.py
git commit -m "test: tests 패키지 초기화"
```

---

## Task 2: PID 파일 잠금 — 실패 테스트 작성

**Files:**
- Modify: `tests/test_watcher.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_watcher.py` 를 아래 내용으로 작성한다:

```python
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import watcher


class TestPidLock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pid")
        self.tmp.close()
        self.pid_path = Path(self.tmp.name)
        # 빈 파일로 초기화
        self.pid_path.unlink()

    def tearDown(self):
        self.pid_path.unlink(missing_ok=True)

    def _make_logger(self):
        import logging
        return logging.getLogger("test")

    def test_acquire_creates_pid_file(self):
        """잠금 획득 시 PID 파일이 생성되어야 한다."""
        result = watcher.acquire_pid_lock(self.pid_path, self._make_logger())
        self.assertTrue(result)
        self.assertTrue(self.pid_path.exists())
        self.assertEqual(self.pid_path.read_text().strip(), str(os.getpid()))

    def test_acquire_fails_if_process_alive(self):
        """현재 프로세스 PID를 가진 PID 파일이 존재하면 False를 반환해야 한다."""
        self.pid_path.write_text(str(os.getpid()))
        result = watcher.acquire_pid_lock(self.pid_path, self._make_logger())
        self.assertFalse(result)

    def test_acquire_succeeds_if_process_dead(self):
        """죽은 프로세스의 PID 파일이 있으면 덮어쓰고 True를 반환해야 한다."""
        self.pid_path.write_text("99999999")  # 존재하지 않는 PID
        with patch("os.kill", side_effect=ProcessLookupError):
            result = watcher.acquire_pid_lock(self.pid_path, self._make_logger())
        self.assertTrue(result)
        self.assertEqual(self.pid_path.read_text().strip(), str(os.getpid()))

    def test_release_removes_pid_file(self):
        """release_pid_lock 호출 시 PID 파일이 삭제되어야 한다."""
        self.pid_path.write_text(str(os.getpid()))
        watcher.release_pid_lock(self.pid_path)
        self.assertFalse(self.pid_path.exists())

    def test_release_is_idempotent(self):
        """PID 파일이 없어도 release_pid_lock 은 예외 없이 실행되어야 한다."""
        watcher.release_pid_lock(self.pid_path)  # 파일 없음, 예외 없어야 함


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
python3 -m pytest tests/test_watcher.py::TestPidLock -v
```

기댓값: `AttributeError: module 'watcher' has no attribute 'acquire_pid_lock'` 또는 유사한 실패.

---

## Task 3: PID 파일 잠금 — 구현

**Files:**
- Modify: `src/watcher.py:28-31` (상수 섹션)
- Modify: `src/watcher.py` (새 함수 추가 — 상태 저장/복원 섹션 바로 위)
- Modify: `src/watcher.py:295-327` (`main()` 함수)

- [ ] **Step 1: PID_FILE 상수 추가**

`src/watcher.py` 의 상수 섹션(`STATE_FILE` 정의 바로 아래)에 다음을 추가한다:

```python
PID_FILE = Path.home() / ".token_alert.pid"
```

- [ ] **Step 2: acquire_pid_lock / release_pid_lock 함수 추가**

`src/watcher.py` 에서 `# ──────────── 상태 저장/복원` 섹션 **바로 위**에 다음을 삽입한다:

```python
# ──────────────────────────────────────────
# 단일 인스턴스 보장 (PID 파일)
# ──────────────────────────────────────────
def acquire_pid_lock(pid_file: Path, logger: logging.Logger) -> bool:
    """PID 파일을 생성해 단일 인스턴스를 보장한다. 이미 실행 중이면 False를 반환한다."""
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            os.kill(existing_pid, 0)  # 프로세스 존재 여부만 확인 (신호 없음)
            logger.error(f"이미 실행 중입니다 (PID: {existing_pid}). 종료합니다.")
            return False
        except (ProcessLookupError, OSError):
            logger.warning("오래된 PID 파일 발견, 덮어씁니다.")
        except ValueError:
            logger.warning("PID 파일 형식 오류, 덮어씁니다.")

    pid_file.write_text(str(os.getpid()))
    return True


def release_pid_lock(pid_file: Path = PID_FILE) -> None:
    """PID 파일을 제거한다. 파일이 없어도 예외 없이 종료한다."""
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass
```

- [ ] **Step 3: main() 에 PID 잠금 통합**

`src/watcher.py` 의 `main()` 함수를 아래와 같이 수정한다. `logger = setup_logging(...)` 호출 직후에 잠금 획득 코드와 `atexit`/`signal` 등록을 추가한다:

```python
def main() -> None:
    import atexit
    import signal

    parser = argparse.ArgumentParser(description="Claude Code 토큰 초기화 감지 데몬")
    parser.add_argument("--dry-run", action="store_true", help="실제 dispatch 없이 테스트 실행")
    parser.add_argument("--once", action="store_true", help="한 번만 실행 후 종료 (데몬 없이)")
    parser.add_argument("--verbose", action="store_true", help="상세 로그 출력")
    args = parser.parse_args()

    logger = setup_logging(verbose=args.verbose)
    cfg = load_config()

    if not acquire_pid_lock(PID_FILE, logger):
        sys.exit(1)

    atexit.register(release_pid_lock)

    def _handle_signal(signum, frame):
        release_pid_lock()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("token_alert 시작")

    if args.dry_run:
        logger.info("[DRY-RUN 모드] GitHub Actions dispatch 는 실제로 전송되지 않습니다.")

    poll_interval = int(cfg.get("POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)))

    if args.once:
        run_once(cfg, logger, dry_run=args.dry_run)
        return

    while True:
        try:
            run_once(cfg, logger, dry_run=args.dry_run)
        except Exception as exc:
            logger.exception(f"감지 주기 중 오류 발생: {exc}")

        logger.debug(f"{poll_interval}초 후 다시 확인합니다...")
        time.sleep(poll_interval)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
python3 -m pytest tests/test_watcher.py::TestPidLock -v
```

기댓값: 5개 테스트 모두 `PASSED`.

- [ ] **Step 5: --once 정상 작동 확인**

```bash
python3 src/watcher.py --dry-run --once --verbose
```

기댓값: 정상 실행 후 종료. `~/.token_alert.pid` 파일이 실행 후 삭제되었는지 확인:

```bash
ls -la ~/.token_alert.pid 2>&1
```

기댓값: `No such file or directory`

- [ ] **Step 6: 커밋**

```bash
git add src/watcher.py tests/test_watcher.py
git commit -m "feat: PID 파일로 중복 실행 방지"
```

---

## Task 4: 이전 워크플로우 취소 — 실패 테스트 작성

**Files:**
- Modify: `tests/test_watcher.py` (TestCancelWorkflow 클래스 추가)

- [ ] **Step 1: 테스트 클래스 추가**

`tests/test_watcher.py` 의 `TestPidLock` 클래스 **아래**에 다음을 추가한다:

```python
class TestCancelWorkflow(unittest.TestCase):

    def _cfg(self):
        return {
            "GITHUB_TOKEN": "fake-token",
            "GITHUB_OWNER": "testowner",
            "GITHUB_REPO": "token_alert",
        }

    def _make_logger(self):
        import logging
        return logging.getLogger("test")

    def _make_response(self, body: dict, status: int = 200):
        import io
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        resp.status = status
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_cancels_all_in_progress_runs(self):
        """진행 중인 워크플로우 실행이 2개면 취소 요청을 2번 보내야 한다."""
        list_resp = self._make_response({"workflow_runs": [{"id": 111}, {"id": 222}]})
        cancel_resp = self._make_response({}, status=202)

        with patch("urllib.request.urlopen", side_effect=[list_resp, cancel_resp, cancel_resp]) as mock_open:
            watcher.cancel_previous_workflow_runs(self._cfg(), self._make_logger())

        # 첫 번째 호출 = 목록 조회, 두 번째·세 번째 = 취소
        self.assertEqual(mock_open.call_count, 3)
        cancel_urls = [
            mock_open.call_args_list[1][0][0].full_url,
            mock_open.call_args_list[2][0][0].full_url,
        ]
        self.assertIn("runs/111/cancel", cancel_urls[0])
        self.assertIn("runs/222/cancel", cancel_urls[1])

    def test_skips_cancel_when_no_runs(self):
        """진행 중인 워크플로우가 없으면 취소 요청을 보내지 않아야 한다."""
        list_resp = self._make_response({"workflow_runs": []})

        with patch("urllib.request.urlopen", return_value=list_resp) as mock_open:
            watcher.cancel_previous_workflow_runs(self._cfg(), self._make_logger())

        self.assertEqual(mock_open.call_count, 1)  # 목록 조회 1번만

    def test_skips_cancel_when_no_credentials(self):
        """GITHUB_TOKEN 또는 GITHUB_OWNER 가 없으면 API 호출 없이 종료해야 한다."""
        with patch("urllib.request.urlopen") as mock_open:
            watcher.cancel_previous_workflow_runs({}, self._make_logger())

        mock_open.assert_not_called()

    def test_cancel_continues_on_http_error(self):
        """개별 취소 실패(HTTPError)가 나머지 취소를 막지 않아야 한다."""
        import urllib.error
        list_resp = self._make_response({"workflow_runs": [{"id": 111}, {"id": 222}]})
        cancel_resp = self._make_response({}, status=202)
        http_err = urllib.error.HTTPError(url="", code=409, msg="", hdrs=None, fp=None)

        with patch("urllib.request.urlopen", side_effect=[list_resp, http_err, cancel_resp]) as mock_open:
            watcher.cancel_previous_workflow_runs(self._cfg(), self._make_logger())

        self.assertEqual(mock_open.call_count, 3)
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
python3 -m pytest tests/test_watcher.py::TestCancelWorkflow -v
```

기댓값: `AttributeError: module 'watcher' has no attribute 'cancel_previous_workflow_runs'`

---

## Task 5: 이전 워크플로우 취소 — 구현

**Files:**
- Modify: `src/watcher.py` (`cancel_previous_workflow_runs` 함수 추가, `dispatch_github_workflow` 수정)

- [ ] **Step 1: cancel_previous_workflow_runs 함수 추가**

`src/watcher.py` 에서 `# ──────────── GitHub Actions dispatch` 섹션 **바로 위**에 다음을 삽입한다:

```python
# ──────────────────────────────────────────
# 이전 워크플로우 취소
# ──────────────────────────────────────────
def cancel_previous_workflow_runs(cfg: dict, logger: logging.Logger) -> None:
    """진행 중인 이전 token-reset-notify 워크플로우 실행을 모두 취소한다."""
    token = cfg.get("GITHUB_TOKEN", "")
    owner = cfg.get("GITHUB_OWNER", "")
    repo = cfg.get("GITHUB_REPO", "token_alert")

    if not all([token, owner]):
        return

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    list_url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows"
        f"/token-reset-notify.yml/runs?status=in_progress&per_page=10"
    )

    try:
        req = urllib.request.Request(list_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        logger.warning(f"진행 중 워크플로우 목록 조회 실패: {e}")
        return

    runs = data.get("workflow_runs", [])
    for run in runs:
        run_id = run["id"]
        cancel_url = (
            f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/cancel"
        )
        try:
            cancel_req = urllib.request.Request(
                cancel_url, data=b"", headers=headers, method="POST"
            )
            with urllib.request.urlopen(cancel_req, timeout=15):
                pass
            logger.info(f"이전 워크플로우 취소 완료 (run_id: {run_id})")
        except urllib.error.HTTPError as e:
            logger.warning(f"워크플로우 취소 실패 (run_id: {run_id}, HTTP {e.code})")
        except urllib.error.URLError as e:
            logger.warning(f"워크플로우 취소 네트워크 오류 (run_id: {run_id}): {e.reason}")
```

- [ ] **Step 2: dispatch_github_workflow 에 취소 호출 추가**

`src/watcher.py` 의 `dispatch_github_workflow` 함수에서 `if dry_run:` 블록 **바로 앞**에 다음을 추가한다:

```python
    if not dry_run:
        cancel_previous_workflow_runs(cfg, logger)
```

수정 후 해당 함수의 흐름 (관련 부분만):

```python
def dispatch_github_workflow(
    cfg: dict,
    reset_time: datetime,
    logger: logging.Logger,
    dry_run: bool = False,
) -> bool:
    token = cfg.get("GITHUB_TOKEN", "")
    owner = cfg.get("GITHUB_OWNER", "")
    repo = cfg.get("GITHUB_REPO", "token_alert")

    KST = timezone(timedelta(hours=9))
    reset_iso = reset_time.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/token-reset-notify.yml/dispatches"

    payload = json.dumps(
        {"ref": "main", "inputs": {"reset_time": reset_iso}}
    ).encode("utf-8")

    if not dry_run:
        cancel_previous_workflow_runs(cfg, logger)   # ← 추가된 줄

    if dry_run:
        logger.info(f"[DRY-RUN] GitHub Actions dispatch — URL: {url}")
        logger.info(f"[DRY-RUN] payload: {payload.decode()}")
        return True

    # ... 이후 기존 코드 그대로 ...
```

- [ ] **Step 3: 테스트 통과 확인**

```bash
python3 -m pytest tests/test_watcher.py -v
```

기댓값: 9개 테스트 모두 `PASSED`.

- [ ] **Step 4: dry-run 으로 전체 흐름 확인**

```bash
python3 src/watcher.py --dry-run --once --verbose
```

기댓값: 정상 실행 후 `[DRY-RUN]` 로그 출력. 취소 API는 dry-run 에서 호출되지 않으므로 취소 관련 로그는 없어야 정상.

- [ ] **Step 5: 커밋**

```bash
git add src/watcher.py tests/test_watcher.py
git commit -m "feat: 새 dispatch 시 이전 워크플로우 취소"
```

---

## Task 6: 통합 확인 및 재배포

- [ ] **Step 1: 현재 LaunchAgent 상태 확인**

```bash
launchctl list | grep token-alert
```

기댓값: `com.token-alert.watcher` 와 `com.token-alert.tray` 각 1개씩만 보여야 한다. 2개 이상이면 아래 재설치 단계를 반드시 실행한다.

- [ ] **Step 2: LaunchAgent 재설치 (중복 제거)**

```bash
python3 platform/macos/uninstall.py
python3 platform/macos/install.py
```

- [ ] **Step 3: 재설치 후 단일 인스턴스 확인**

```bash
launchctl list | grep token-alert
ps aux | grep watcher.py | grep -v grep
```

기댓값: watcher.py 프로세스가 1개만 보여야 한다.

- [ ] **Step 4: 로그에서 중복 줄 없음 확인**

```bash
tail -20 ~/.claude/token_alert.log
```

기댓값: 동일 타임스탬프로 중복된 줄이 없어야 한다.

- [ ] **Step 5: 최종 커밋**

```bash
git add -u
git commit -m "chore: 중복 알림 방지 구현 완료"
```

---

## 자기 검토

**요구사항 대비:**
- [x] PID 파일로 두 번째 인스턴스 즉시 종료 → Task 2-3
- [x] 죽은 프로세스의 오래된 PID 파일 처리 → Task 3 Step 2
- [x] 프로그램 종료 시(정상/SIGTERM/SIGINT) PID 파일 자동 삭제 → Task 3 Step 3
- [x] 새 dispatch 전 이전 워크플로우 취소 → Task 4-5
- [x] dry-run 에서 취소 API 미호출 → Task 5 Step 2
- [x] 취소 실패가 dispatch 자체를 막지 않음 → Task 4 Step 1 (`test_cancel_continues_on_http_error`)
- [x] 재설치로 현재 중복 인스턴스 제거 → Task 6

**타입/함수명 일관성:**
- `acquire_pid_lock(pid_file, logger)` — Task 2 테스트와 Task 3 구현 동일
- `release_pid_lock(pid_file)` — 기본값 `PID_FILE` 포함, Task 2 테스트와 Task 3 구현 동일
- `cancel_previous_workflow_runs(cfg, logger)` — Task 4 테스트와 Task 5 구현 동일
