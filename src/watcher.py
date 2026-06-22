#!/usr/bin/env python3
"""
token_alert — Claude Code 5시간 토큰 창 초기화 감지 데몬

작동 방식:
  1. ~/.claude/projects/**/*.jsonl 파일을 주기적으로 스캔
  2. 현재 시각 기준으로 최근 5시간 이내 메시지 중 가장 오래된 것의 타임스탬프를 찾음
  3. 그 타임스탬프 + 5시간 = 다음 초기화 시각 계산
  4. 이전에 예약한 시각과 다를 경우 GitHub Actions workflow 를 dispatch 하여 알림 예약
  5. 컴퓨터가 꺼지더라도 GitHub Actions 가 클라우드에서 대기 후 텔레그램 알림 전송
  6. 텔레그램 /status 명령으로 다음 초기화까지 남은 시간 즉시 조회 가능
"""

import json
import os
import sys
import time
import glob
import logging
import argparse
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ──────────────────────────────────────────
# 설정
# ──────────────────────────────────────────
WINDOW_HOURS = 5                          # Claude Code 롤링 윈도우 (시간)
DEFAULT_POLL_INTERVAL = 600               # 감지 주기 (초, 기본 10분)
STATE_FILE = Path.home() / ".token_alert_state.json"
PID_FILE = Path.home() / ".token_alert.pid"
LOG_FILE = Path.home() / ".claude" / "token_alert.log"


def load_config() -> dict:
    """config/config.env 또는 환경 변수에서 설정을 읽습니다.

    탐색 순서:
    1. ~/.config/token-alert/config.env (설치된 경우)
    2. 스크립트 위치 기준 config/config.env (개발 환경, 심볼릭 링크 포함)
    """
    candidate_paths = [
        Path.home() / ".config" / "token-alert" / "config.env",
        Path(__file__).resolve().parent.parent / "config" / "config.env",
    ]

    config_path = None
    for p in candidate_paths:
        if p.exists():
            config_path = p
            break

    cfg: dict = {}

    if config_path is not None:
        with open(config_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip()] = v.strip()

    # 환경 변수가 파일보다 우선
    for key in [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GITHUB_TOKEN",
        "GITHUB_OWNER",
        "GITHUB_REPO",
        "CLAUDE_PROJECTS_DIR",
        "POLL_INTERVAL",
        "NOTIFY_ADVANCE_SECONDS",
    ]:
        env_val = os.environ.get(key)
        if env_val:
            cfg[key] = env_val

    return cfg


def setup_logging(verbose: bool = False) -> logging.Logger:
    """로거를 설정합니다 (파일 + 콘솔)."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("token_alert")


# ──────────────────────────────────────────
# Claude Code jsonl 파싱
# ──────────────────────────────────────────
def get_claude_projects_dir(cfg: dict) -> Path:
    """Claude Code 프로젝트 디렉터리 경로를 반환합니다."""
    raw = cfg.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects")
    return Path(raw).expanduser()


def find_oldest_message_in_window(projects_dir: Path, window_hours: int = WINDOW_HOURS) -> datetime | None:
    """
    현재 시각 기준 최근 `window_hours` 시간 이내 메시지 중
    가장 오래된 메시지의 타임스탬프를 반환합니다.

    반환값: UTC datetime 또는 None (해당 메시지 없음)
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    oldest: datetime | None = None

    pattern = str(projects_dir / "**" / "*.jsonl")
    for filepath in glob.glob(pattern, recursive=True):
        try:
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # timestamp 필드 추출 (ISO 8601 형식)
                    ts_raw = entry.get("timestamp")
                    if not ts_raw:
                        continue

                    try:
                        # Python 3.11+ 는 fromisoformat 이 Z 처리 가능
                        # 하위 버전 호환을 위해 수동 처리
                        ts_raw = ts_raw.replace("Z", "+00:00")
                        ts = datetime.fromisoformat(ts_raw)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue

                    # 윈도우 내 메시지인지 확인
                    if cutoff <= ts <= now:
                        if oldest is None or ts < oldest:
                            oldest = ts

        except (OSError, PermissionError):
            continue

    return oldest


def calculate_reset_time(oldest_ts: datetime, window_hours: int = WINDOW_HOURS) -> datetime:
    """가장 오래된 메시지 시각 + 5시간 = 초기화 예정 시각."""
    return oldest_ts + timedelta(hours=window_hours)


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


# ──────────────────────────────────────────
# 상태 저장/복원
# ──────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


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


# ──────────────────────────────────────────
# GitHub Actions dispatch
# ──────────────────────────────────────────
def dispatch_github_workflow(
    cfg: dict,
    reset_time: datetime,
    logger: logging.Logger,
    dry_run: bool = False,
) -> bool:
    """
    GitHub Actions workflow_dispatch 이벤트를 전송합니다.

    workflow 파일: .github/workflows/token-reset-notify.yml
    input: reset_time (ISO 8601 UTC 문자열)
    """
    token = cfg.get("GITHUB_TOKEN", "")
    owner = cfg.get("GITHUB_OWNER", "")
    repo = cfg.get("GITHUB_REPO", "token_alert")

    KST = timezone(timedelta(hours=9))
    reset_iso = reset_time.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/token-reset-notify.yml/dispatches"

    payload = json.dumps(
        {
            "ref": "main",
            "inputs": {
                "reset_time": reset_iso,
            },
        }
    ).encode("utf-8")

    if not dry_run:
        cancel_previous_workflow_runs(cfg, logger)

    if dry_run:
        logger.info(f"[DRY-RUN] GitHub Actions dispatch — URL: {url}")
        logger.info(f"[DRY-RUN] payload: {payload.decode()}")
        return True

    if not all([token, owner]):
        logger.error("GITHUB_TOKEN, GITHUB_OWNER 설정이 필요합니다.")
        return False

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            if status == 204:
                logger.info(f"GitHub Actions dispatch 성공 — 초기화 시각: {reset_iso}")
                return True
            else:
                logger.warning(f"GitHub Actions dispatch 응답 코드: {status}")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        logger.error(f"GitHub Actions dispatch 실패 ({e.code}): {body[:200]}")
        return False
    except urllib.error.URLError as e:
        logger.error(f"GitHub Actions dispatch 네트워크 오류: {e.reason}")
        return False


# ──────────────────────────────────────────
# 텔레그램 봇 명령어 (Long Polling)
# ──────────────────────────────────────────
def send_telegram_message(cfg: dict, text: str, logger: logging.Logger, dry_run: bool = False) -> None:
    """텔레그램 메시지를 전송한다. 실패 시 예외 없이 로그만 남긴다."""
    if dry_run:
        logger.info(f"[DRY-RUN] 텔레그램 전송 건너뜀: {text[:80]}")
        return

    token = cfg.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = cfg.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정 — 전송 건너뜀")
        return

    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                logger.warning(f"텔레그램 전송 실패: {result}")
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        logger.warning(f"텔레그램 전송 오류: {e}")


def get_telegram_updates(cfg: dict, offset: int, logger: logging.Logger) -> list:
    """getUpdates long polling으로 텔레그램 업데이트 목록을 가져온다."""
    token = cfg.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return []

    url = (
        f"https://api.telegram.org/bot{token}/getUpdates"
        f"?offset={offset}&timeout=30&allowed_updates=%5B%22message%22%5D"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return data.get("result", [])
    except (urllib.error.URLError, json.JSONDecodeError):
        pass
    return []


def handle_telegram_command(cfg: dict, update: dict, logger: logging.Logger, dry_run: bool = False) -> None:
    """텔레그램 update를 처리하고 명령에 맞는 응답을 전송한다."""
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    allowed_chat_id = str(cfg.get("TELEGRAM_CHAT_ID", ""))
    if chat_id != allowed_chat_id:
        logger.warning(f"허용되지 않은 chat_id에서 명령 수신: {chat_id}")
        return

    command = text.split()[0].split("@")[0].lower()

    if command == "/status":
        KST = timezone(timedelta(hours=9))
        now = datetime.now(timezone.utc)
        state = load_state()
        scheduled = state.get("scheduled_reset_time")

        if scheduled:
            try:
                reset_dt = datetime.fromisoformat(scheduled)
                remaining = reset_dt - now
                secs = int(remaining.total_seconds())
                if secs > 0:
                    hours, rem = divmod(secs, 3600)
                    minutes = rem // 60
                    reset_kst = reset_dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
                    if hours > 0:
                        time_str = f"{hours}시간 {minutes}분"
                    else:
                        time_str = f"{minutes}분"
                    reply = f"⏳ 다음 초기화까지 {time_str} 남았습니다.\n예정 시각: {reset_kst}"
                else:
                    reply = "✅ 현재 예약된 초기화 알림이 없습니다.\n(Claude Code를 사용하면 자동으로 감지됩니다)"
            except ValueError:
                reply = "✅ 현재 예약된 초기화 알림이 없습니다.\n(Claude Code를 사용하면 자동으로 감지됩니다)"
        else:
            reply = "✅ 현재 예약된 초기화 알림이 없습니다.\n(Claude Code를 사용하면 자동으로 감지됩니다)"

        logger.info(f"[BOT] /status 응답: {reply[:60]}")
        send_telegram_message(cfg, reply, logger, dry_run=dry_run)

    elif command.startswith("/"):
        reply = "사용 가능한 명령:\n/status — 다음 토큰 초기화까지 남은 시간 조회"
        send_telegram_message(cfg, reply, logger, dry_run=dry_run)


def run_telegram_polling(cfg: dict, logger: logging.Logger, dry_run: bool = False) -> None:
    """텔레그램 업데이트를 long polling으로 수신하는 루프 (백그라운드 스레드용)."""
    if not cfg.get("TELEGRAM_BOT_TOKEN"):
        logger.warning("TELEGRAM_BOT_TOKEN 미설정 — 텔레그램 polling 비활성화")
        return

    logger.info("텔레그램 봇 polling 시작 (/status 명령 대기 중)")
    offset = 0
    while True:
        try:
            updates = get_telegram_updates(cfg, offset, logger)
            for update in updates:
                handle_telegram_command(cfg, update, logger, dry_run=dry_run)
                offset = update["update_id"] + 1
        except Exception as e:
            logger.warning(f"텔레그램 polling 오류: {e}")
            time.sleep(5)


# ──────────────────────────────────────────
# 메인 루프
# ──────────────────────────────────────────
def run_once(cfg: dict, logger: logging.Logger, dry_run: bool = False) -> None:
    """한 번의 감지 주기를 실행합니다."""
    projects_dir = get_claude_projects_dir(cfg)

    if not projects_dir.exists():
        logger.warning(f"Claude Code 프로젝트 디렉터리를 찾을 수 없습니다: {projects_dir}")
        return

    oldest_ts = find_oldest_message_in_window(projects_dir)

    if oldest_ts is None:
        logger.debug("최근 5시간 내 메시지 없음 — 알림 예약 불필요")
        return

    reset_time = calculate_reset_time(oldest_ts)
    now = datetime.now(timezone.utc)

    # 이미 지난 초기화 시각은 무시
    if reset_time <= now:
        logger.debug(f"초기화 시각({reset_time.isoformat()})이 이미 지났음 — 건너뜀")
        state = load_state()
        if "scheduled_reset_time" in state:
            state.pop("scheduled_reset_time", None)
            save_state(state)
        return

    # 이미 같은 시각으로 예약했으면 중복 dispatch 방지
    state = load_state()
    prev_scheduled = state.get("scheduled_reset_time")

    KST = timezone(timedelta(hours=9))
    reset_iso = reset_time.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    if prev_scheduled == reset_iso:
        logger.debug(f"이미 예약됨: {reset_iso} — 중복 dispatch 건너뜀")
        return

    advance = int(cfg.get("NOTIFY_ADVANCE_SECONDS", "0"))
    notify_time = reset_time - timedelta(seconds=advance)
    remaining = reset_time - now

    logger.info(
        f"초기화 예정: {reset_iso} (KST) "
        f"(약 {int(remaining.total_seconds() // 60)}분 후)"
    )

    ok = dispatch_github_workflow(cfg, notify_time, logger, dry_run=dry_run)

    if ok:
        state["scheduled_reset_time"] = reset_iso
        state["dispatched_at"] = now.isoformat()
        save_state(state)


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

    # 텔레그램 봇 polling 스레드 시작 (데몬 모드에서만)
    t = threading.Thread(
        target=run_telegram_polling,
        args=(cfg, logger, args.dry_run),
        daemon=True,
    )
    t.start()

    # 데몬 루프
    while True:
        try:
            run_once(cfg, logger, dry_run=args.dry_run)
        except Exception as exc:
            logger.exception(f"감지 주기 중 오류 발생: {exc}")

        logger.debug(f"{poll_interval}초 후 다시 확인합니다...")
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
