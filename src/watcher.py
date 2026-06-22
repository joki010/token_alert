#!/usr/bin/env python3
"""
token_alert — Claude Code 5시간 토큰 창 초기화 감지 데몬

작동 방식:
  1. ~/.claude/projects/**/*.jsonl 파일을 주기적으로 스캔
  2. 현재 시각 기준으로 최근 5시간 이내 메시지 중 가장 오래된 것의 타임스탬프를 찾음
  3. 그 타임스탬프 + 5시간 = 다음 초기화 시각 계산
  4. 이전에 예약한 시각과 다를 경우 GitHub Actions workflow 를 dispatch 하여 알림 예약
  5. 컴퓨터가 꺼지더라도 GitHub Actions 가 클라우드에서 대기 후 텔레그램 알림 전송
"""

import json
import os
import sys
import time
import glob
import logging
import argparse
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
LOG_FILE = Path.home() / ".claude" / "token_alert.log"


def load_config() -> dict:
    """config/config.env 또는 환경 변수에서 설정을 읽습니다."""
    # 스크립트 위치 기준으로 config.env 탐색
    script_dir = Path(__file__).parent.parent
    config_path = script_dir / "config" / "config.env"

    cfg: dict = {}

    if config_path.exists():
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
    parser = argparse.ArgumentParser(description="Claude Code 토큰 초기화 감지 데몬")
    parser.add_argument("--dry-run", action="store_true", help="실제 dispatch 없이 테스트 실행")
    parser.add_argument("--once", action="store_true", help="한 번만 실행 후 종료 (데몬 없이)")
    parser.add_argument("--verbose", action="store_true", help="상세 로그 출력")
    args = parser.parse_args()

    logger = setup_logging(verbose=args.verbose)
    cfg = load_config()

    logger.info("token_alert 시작")

    if args.dry_run:
        logger.info("[DRY-RUN 모드] GitHub Actions dispatch 는 실제로 전송되지 않습니다.")

    poll_interval = int(cfg.get("POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)))

    if args.once:
        run_once(cfg, logger, dry_run=args.dry_run)
        return

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
