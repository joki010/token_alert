#!/usr/bin/env python3
"""
token_alert — Claude Code 5시간 토큰 창 초기화 감지 데몬

작동 방식:
  1. ~/.claude/token_alert_usage.json 의 five_hour_resets_at(Unix timestamp)를 우선 읽음
     (Claude Code가 서버 응답 기반으로 기록하는 실제 초기화 시각)
  2. 해당 파일이 없거나 필드가 없으면 ~/.claude/projects/**/*.jsonl 폴백:
     최근 5시간 이내 메시지 중 가장 오래된 타임스탬프 + 5시간으로 계산
  3. 이전에 예약한 시각과 다를 경우 GitHub Actions workflow 를 dispatch 하여 알림 예약
  4. 컴퓨터가 꺼지더라도 GitHub Actions 가 클라우드에서 대기 후 텔레그램 알림 전송
  5. 텔레그램 /status 명령으로 다음 초기화까지 남은 시간 즉시 조회 가능
"""

import json
import os
import sys
import time
import glob
import base64
import logging
import argparse
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass
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
USAGE_FILE = Path.home() / ".claude" / "token_alert_usage.json"
USAGE_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_USER_AGENT = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_USER_AGENT = "ClaudeUsageBar/0.6"
DEFAULT_CLAUDE_SCOPES = ("user:profile", "user:inference")
OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"


@dataclass(frozen=True)
class ProviderWindow:
    provider: str
    label: str
    window: str
    reset: datetime | None
    remaining_percentage: float | None = None
    used_percentage: float | None = None
    estimated: bool = False
    profile: str | None = None
    account: str | None = None


@dataclass(frozen=True)
class LimitStatus:
    five_hour_reset: datetime | None = None
    seven_day_reset: datetime | None = None
    five_hour_used_percentage: float | None = None
    seven_day_used_percentage: float | None = None
    five_hour_remaining_percentage: float | None = None
    seven_day_remaining_percentage: float | None = None
    source: str | None = None
    estimated: bool = False
    provider_windows: tuple[ProviderWindow, ...] = ()


@dataclass(frozen=True)
class CodexAuthSummary:
    email: str | None
    plan: str | None
    organization: str | None
    account_id: str | None
    access_token: str | None
    error: str | None = None

    def __str__(self) -> str:
        token = "null" if self.access_token is None else "[redacted]"
        return (
            "CodexAuthSummary("
            f"email={self.email}, plan={self.plan}, organization={self.organization}, "
            f"account_id={self.account_id}, access_token={token}, error={self.error})"
        )


@dataclass(frozen=True)
class ClaudeCredentials:
    access_token: str
    refresh_token: str | None
    expires_at_epoch: int | None
    scopes: tuple[str, ...]

    def __str__(self) -> str:
        refresh = "null" if self.refresh_token is None else "[redacted]"
        return f"ClaudeCredentials(access_token=[redacted], refresh_token={refresh}, expires_at_epoch={self.expires_at_epoch})"


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
        "GITHUB_REF",
        "CLAUDE_PROJECTS_DIR",
        "GJC_SESSIONS_DIR",
        "POLL_INTERVAL",
        "NOTIFY_ADVANCE_SECONDS",
        "ENABLE_DIRECT_USAGE",
        "CODEX_HOME",
        "CODEX_AUTH_JSON",
        "CODEX_PROFILES_DIR",
        "CLAUDE_USAGE_CREDENTIALS",
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
    stream_handler = logging.StreamHandler(sys.stdout)
    if hasattr(stream_handler.stream, 'reconfigure'):
        try:
            stream_handler.stream.reconfigure(encoding='utf-8')
        except Exception:
            pass
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            stream_handler,
        ],
    )
    return logging.getLogger("token_alert")


# ──────────────────────────────────────────
# Claude Code 초기화 시각 읽기
# ──────────────────────────────────────────
def _parse_reset_datetime(value) -> datetime | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.replace(".", "", 1).isdigit():
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _future_datetime(value, now: datetime) -> datetime | None:
    parsed = _parse_reset_datetime(value)
    if parsed is None or parsed <= now:
        return None
    return parsed


def _usage_limit(data: dict, window: str) -> dict:
    rate_limits = data.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return {}
    limit = rate_limits.get(window)
    if not isinstance(limit, dict):
        return {}
    return limit


def _usage_cache_is_fresh(data: dict, now: datetime) -> bool:
    updated_raw = data.get("updated_at")
    if updated_raw is None:
        return True
    updated_at = _parse_reset_datetime(updated_raw)
    if updated_at is None:
        return False
    return (now - updated_at).total_seconds() <= USAGE_CACHE_MAX_AGE_SECONDS


def _usage_used_percentage(data: dict, window: str) -> float | None:
    flat_key = f"{window}_used_percentage"
    value = data.get(flat_key)
    if value is None:
        value = _usage_limit(data, window).get("used_percentage")
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json_file(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _json_request(
    url: str,
    headers: dict[str, str],
    logger: logging.Logger,
    method: str = "GET",
    body: bytes | None = None,
    timeout: int = 15,
) -> dict | None:
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status < 200 or resp.status >= 300:
                logger.debug(f"사용량 API 응답 코드: {resp.status}")
                return None
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        logger.debug(f"사용량 API 조회 실패: {exc}")
        return None
    return data if isinstance(data, dict) else None


def _decode_jwt_payload(token: str | None) -> dict | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload = payload + "=" * ((4 - len(payload) % 4) % 4)
    try:
        data = json.loads(base64.b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _str_value(data: dict | None, key: str) -> str | None:
    if data is None:
        return None
    value = data.get(key)
    return value if isinstance(value, str) else None


def _num_value(data: dict | None, key: str) -> float | None:
    if data is None:
        return None
    value = data.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _dict_value(data: dict | None, key: str) -> dict | None:
    if data is None:
        return None
    value = data.get(key)
    return value if isinstance(value, dict) else None


def _default_codex_auth_path(cfg: dict) -> Path:
    configured = cfg.get("CODEX_AUTH_JSON")
    if configured:
        return Path(configured).expanduser()
    codex_home = Path(cfg.get("CODEX_HOME", "~/.codex")).expanduser()
    return codex_home / "auth.json"


def _default_codex_profiles_dir(cfg: dict) -> Path:
    configured = cfg.get("CODEX_PROFILES_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex-switch" / "profiles"


def _default_claude_credentials_path(cfg: dict) -> Path:
    configured = cfg.get("CLAUDE_USAGE_CREDENTIALS")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "claude-usage-bar" / "credentials.json"


def read_codex_auth(auth_path: Path) -> CodexAuthSummary | None:
    data = _read_json_file(auth_path)
    if data is None:
        return None
    tokens = _dict_value(data, "tokens")
    if tokens is None:
        return CodexAuthSummary(None, None, None, None, None, "auth.json is missing tokens")

    jwt = _decode_jwt_payload(_str_value(tokens, "id_token"))
    auth = _dict_value(jwt, OPENAI_AUTH_CLAIM)
    orgs = auth.get("organizations") if auth is not None else None
    organization = None
    if isinstance(orgs, list):
        for item in orgs:
            if isinstance(item, dict) and item.get("is_default") is True:
                organization = _str_value(item, "title")
                break
        if organization is None:
            first = next((item for item in orgs if isinstance(item, dict)), None)
            organization = _str_value(first, "title")

    return CodexAuthSummary(
        email=_str_value(jwt, "email"),
        plan=_str_value(auth, "chatgpt_plan_type"),
        organization=organization,
        account_id=_str_value(tokens, "account_id"),
        access_token=_str_value(tokens, "access_token"),
    )


def _codex_window(
    rate: dict | None,
    window: str,
    label: str,
    now: datetime,
    profile: str | None = None,
    account: str | None = None,
) -> ProviderWindow | None:
    key = "primary_window" if window == "five_hour" else "secondary_window"
    camel_key = "primaryWindow" if window == "five_hour" else "secondaryWindow"
    data = _dict_value(rate, key) or _dict_value(rate, camel_key)
    if data is None:
        return None
    used = max(0.0, min(100.0, _num_value(data, "used_percent") or _num_value(data, "usedPercent") or 0.0))
    reset_after = _num_value(data, "reset_after_seconds") or _num_value(data, "resetAfterSeconds")
    reset = now + timedelta(seconds=reset_after) if reset_after is not None and reset_after > 0 else None
    display_label = f"Codex {profile}" if profile else "Codex"
    return ProviderWindow(
        provider="codex",
        label=display_label,
        window=window,
        reset=reset,
        remaining_percentage=round(100 - used),
        used_percentage=used,
        profile=profile,
        account=account,
    )


def _status_from_windows(windows: tuple[ProviderWindow, ...], source: str, estimated: bool = False) -> LimitStatus:
    five_windows = [w for w in windows if w.window == "five_hour" and w.reset is not None]
    seven_windows = [w for w in windows if w.window == "seven_day" and w.reset is not None]
    five_window = min(five_windows, key=lambda w: w.reset) if five_windows else None
    seven_window = min(seven_windows, key=lambda w: w.reset) if seven_windows else None
    return LimitStatus(
        five_hour_reset=five_window.reset if five_window is not None else None,
        seven_day_reset=seven_window.reset if seven_window is not None else None,
        five_hour_used_percentage=five_window.used_percentage if five_window is not None else None,
        seven_day_used_percentage=seven_window.used_percentage if seven_window is not None else None,
        five_hour_remaining_percentage=five_window.remaining_percentage if five_window is not None else None,
        seven_day_remaining_percentage=seven_window.remaining_percentage if seven_window is not None else None,
        source=source,
        estimated=estimated,
        provider_windows=windows,
    )


def fetch_codex_usage_status(
    auth: CodexAuthSummary | None,
    now: datetime,
    logger: logging.Logger,
    profile: str | None = None,
) -> LimitStatus:
    if auth is None or auth.error is not None or not auth.access_token or not auth.account_id:
        return LimitStatus()
    data = _json_request(
        CODEX_USAGE_URL,
        {
            "Authorization": f"Bearer {auth.access_token}",
            "ChatGPT-Account-Id": auth.account_id,
            "User-Agent": CODEX_USER_AGENT,
        },
        logger,
        timeout=12,
    )
    if data is None:
        return LimitStatus()
    rate = _dict_value(data, "rate_limit") or _dict_value(data, "rateLimit")
    windows = tuple(
        window for window in (
            _codex_window(rate, "five_hour", "5시간", now, profile, auth.email or auth.account_id),
            _codex_window(rate, "seven_day", "7일", now, profile, auth.email or auth.account_id),
        )
        if window is not None and window.reset is not None
    )
    return _status_from_windows(windows, "codex")


def _codex_profile_auths(cfg: dict) -> list[tuple[str, CodexAuthSummary]]:
    profiles_dir = _default_codex_profiles_dir(cfg)
    if not profiles_dir.exists():
        return []
    pairs = []
    for profile_dir in sorted((p for p in profiles_dir.iterdir() if p.is_dir()), key=lambda p: p.name):
        auth = read_codex_auth(profile_dir / "auth.json")
        if auth is not None and auth.error is None:
            pairs.append((profile_dir.name, auth))
    return pairs


def load_claude_credentials(path: Path) -> ClaudeCredentials | None:
    data = _read_json_file(path)
    if data is None:
        return None
    access_token = _str_value(data, "accessToken")
    if not access_token:
        return None
    scopes_raw = data.get("scopes")
    scopes = tuple(item for item in scopes_raw if isinstance(item, str)) if isinstance(scopes_raw, list) else DEFAULT_CLAUDE_SCOPES
    expires_at = _parse_reset_datetime(data.get("expiresAt"))
    return ClaudeCredentials(
        access_token=access_token,
        refresh_token=_str_value(data, "refreshToken"),
        expires_at_epoch=int(expires_at.timestamp()) if expires_at is not None else None,
        scopes=scopes or DEFAULT_CLAUDE_SCOPES,
    )


def _save_claude_credentials(path: Path, credentials: ClaudeCredentials) -> None:
    payload = {
        "accessToken": credentials.access_token,
        "refreshToken": credentials.refresh_token,
        "expiresAt": (
            datetime.fromtimestamp(credentials.expires_at_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if credentials.expires_at_epoch is not None else None
        ),
        "scopes": list(credentials.scopes),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    tmp.replace(path)


def _refresh_claude_credentials(
    path: Path,
    credentials: ClaudeCredentials | None,
    now: datetime,
    logger: logging.Logger,
) -> ClaudeCredentials | None:
    if credentials is None:
        return None
    if credentials.expires_at_epoch is None or credentials.expires_at_epoch > int(now.timestamp()) + 120:
        return credentials
    if not credentials.refresh_token:
        return None
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": credentials.refresh_token,
        "client_id": CLAUDE_CLIENT_ID,
        "scope": " ".join(credentials.scopes),
    }).encode("utf-8")
    data = _json_request(
        CLAUDE_TOKEN_URL,
        {"User-Agent": CLAUDE_USER_AGENT, "Content-Type": "application/json"},
        logger,
        method="POST",
        body=body,
    )
    if data is None:
        return None
    access_token = _str_value(data, "access_token")
    if not access_token:
        return None
    expires_in = _num_value(data, "expires_in")
    refreshed = ClaudeCredentials(
        access_token=access_token,
        refresh_token=_str_value(data, "refresh_token") or credentials.refresh_token,
        expires_at_epoch=int(now.timestamp() + expires_in) if expires_in is not None else credentials.expires_at_epoch,
        scopes=tuple(str(item) for item in data.get("scope", "").split()) or credentials.scopes,
    )
    try:
        _save_claude_credentials(path, refreshed)
    except OSError as exc:
        logger.debug(f"Claude 사용량 자격 저장 실패: {exc}")
    return refreshed


def _claude_window(data: dict, window: str) -> ProviderWindow | None:
    bucket = _dict_value(data, window)
    if bucket is None:
        return None
    reset = _future_datetime(bucket.get("resets_at"), datetime.now(timezone.utc))
    used = _num_value(bucket, "utilization")
    if reset is None:
        return None
    return ProviderWindow(
        provider="claude",
        label="Claude",
        window=window,
        reset=reset,
        used_percentage=used,
    )


def fetch_claude_usage_status(credentials: ClaudeCredentials | None, logger: logging.Logger) -> LimitStatus:
    if credentials is None:
        return LimitStatus()
    data = _json_request(
        CLAUDE_USAGE_URL,
        {
            "Authorization": f"Bearer {credentials.access_token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
        logger,
    )
    if data is None:
        return LimitStatus()
    windows = tuple(
        window for window in (
            _claude_window(data, "five_hour"),
            _claude_window(data, "seven_day"),
        )
        if window is not None
    )
    status = _status_from_windows(windows, "claude")
    return status if status.five_hour_reset is not None or status.seven_day_reset is not None else LimitStatus()


def fetch_direct_usage_status(cfg: dict, logger: logging.Logger | None = None) -> LimitStatus:
    direct_configured = any(key in cfg for key in ("CODEX_HOME", "CODEX_AUTH_JSON", "CODEX_PROFILES_DIR", "CLAUDE_USAGE_CREDENTIALS"))
    if not direct_configured and cfg.get("ENABLE_DIRECT_USAGE") != "1":
        return LimitStatus()
    log = logger or logging.getLogger("token_alert")
    now = datetime.now(timezone.utc)
    codex_profiles = _codex_profile_auths(cfg)
    if codex_profiles:
        codex_windows = tuple(
            window
            for profile, auth in codex_profiles
            for window in fetch_codex_usage_status(auth, now, log, profile).provider_windows
        )
        codex = _status_from_windows(codex_windows, "codex")
    else:
        codex = fetch_codex_usage_status(read_codex_auth(_default_codex_auth_path(cfg)), now, log)
    claude_path = _default_claude_credentials_path(cfg)
    claude_credentials = _refresh_claude_credentials(
        claude_path,
        load_claude_credentials(claude_path),
        now,
        log,
    )
    claude = fetch_claude_usage_status(claude_credentials, log)
    windows = codex.provider_windows + claude.provider_windows
    status = _status_from_windows(windows, "direct")
    return status if status.five_hour_reset is not None or status.seven_day_reset is not None else LimitStatus()


def read_limit_status_from_usage_file() -> LimitStatus:
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return LimitStatus()

    now = datetime.now(timezone.utc)
    if not _usage_cache_is_fresh(data, now):
        return LimitStatus()

    five_hour_limit = _usage_limit(data, "five_hour")
    seven_day_limit = _usage_limit(data, "seven_day")
    five_hour_reset = _future_datetime(
        data.get("five_hour_resets_at", five_hour_limit.get("resets_at")),
        now,
    )
    seven_day_reset = _future_datetime(
        data.get("seven_day_resets_at", seven_day_limit.get("resets_at")),
        now,
    )

    if five_hour_reset is None and seven_day_reset is None:
        return LimitStatus()

    return LimitStatus(
        five_hour_reset=five_hour_reset,
        seven_day_reset=seven_day_reset,
        five_hour_used_percentage=_usage_used_percentage(data, "five_hour"),
        seven_day_used_percentage=_usage_used_percentage(data, "seven_day"),
        source="cache",
    )


def read_reset_time_from_usage_file() -> datetime | None:
    """~/.claude/token_alert_usage.json 에서 5시간 한도 초기화 시각을 읽어 반환."""
    return read_limit_status_from_usage_file().five_hour_reset


def get_current_limit_status(cfg: dict) -> LimitStatus:
    direct_status = fetch_direct_usage_status(cfg)
    if direct_status.five_hour_reset is not None or direct_status.seven_day_reset is not None:
        return direct_status

    cache_status = read_limit_status_from_usage_file()
    if cache_status.five_hour_reset is not None or cache_status.seven_day_reset is not None:
        return cache_status

    oldest_ts = find_oldest_message_in_window(get_jsonl_source_dirs(cfg))
    if oldest_ts is None:
        return LimitStatus()

    return LimitStatus(
        five_hour_reset=calculate_reset_time(oldest_ts),
        source="jsonl",
        estimated=True,
    )


def write_usage_cache_from_status_line(status_line: dict, usage_file: Path = USAGE_FILE) -> None:
    rate_limits = status_line.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return

    five_hour = _usage_limit(status_line, "five_hour")
    seven_day = _usage_limit(status_line, "seven_day")
    data = {
        "rate_limits": rate_limits,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if "resets_at" in five_hour:
        data["five_hour_resets_at"] = five_hour["resets_at"]
    if "resets_at" in seven_day:
        data["seven_day_resets_at"] = seven_day["resets_at"]
    if "used_percentage" in five_hour:
        data["five_hour_used_percentage"] = five_hour["used_percentage"]
    if "used_percentage" in seven_day:
        data["seven_day_used_percentage"] = seven_day["used_percentage"]

    usage_file.parent.mkdir(parents=True, exist_ok=True)
    with open(usage_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ──────────────────────────────────────────
# Claude Code jsonl 파싱 (폴백)
# ──────────────────────────────────────────
def get_claude_projects_dir(cfg: dict) -> Path:
    """Claude Code 프로젝트 디렉터리 경로를 반환합니다."""
    raw = cfg.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects")
    return Path(raw).expanduser()


def get_gjc_sessions_dir(cfg: dict) -> Path:
    """GJC(Gajae Code)가 Claude Code를 구동할 때 쓰는 세션 jsonl 디렉터리를 반환합니다.

    GJC는 `~/.claude/statusLine` 훅을 거치지 않고 자체 TUI에서 상태줄을 그리며,
    대화 로그도 `~/.claude/projects` 대신 `~/.gjc/agent/sessions/**/*.jsonl`에 쓴다.
    각 라인은 Claude Code 세션 로그와 동일하게 최상위 `timestamp` 필드(ISO 8601)를
    가지므로 find_oldest_message_in_window()가 그대로 재사용 가능하다.
    """
    raw = cfg.get("GJC_SESSIONS_DIR", "~/.gjc/agent/sessions")
    return Path(raw).expanduser()


def get_jsonl_source_dirs(cfg: dict) -> list[Path]:
    """토큰 윈도우 추정에 쓸 jsonl 디렉터리 목록을 반환합니다(존재하는 것만).

    Claude Code 네이티브 CLI와 GJC 둘 다에서 실행된 세션을 모두 포함해야
    5시간 롤링 윈도우가 어느 클라이언트로 소비됐든 정확히 잡힌다.
    """
    dirs = [get_claude_projects_dir(cfg), get_gjc_sessions_dir(cfg)]
    return [d for d in dirs if d.exists()]


def find_oldest_message_in_window(
    projects_dirs: Path | list[Path], window_hours: int = WINDOW_HOURS
) -> datetime | None:
    """
    현재 시각 기준 최근 `window_hours` 시간 이내 메시지 중
    가장 오래된 메시지의 타임스탬프를 반환합니다.

    `projects_dirs`는 단일 디렉터리(Path) 또는 디렉터리 목록(list[Path])을
    받는다. 목록을 넘기면 모든 디렉터리를 함께 스캔해 전체 중 가장 오래된
    타임스탬프를 반환한다(Claude Code 네이티브 CLI + GJC 세션 통합 스캔용).

    반환값: UTC datetime 또는 None (해당 메시지 없음)
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    oldest: datetime | None = None

    dirs = [projects_dirs] if isinstance(projects_dirs, Path) else list(projects_dirs)
    filepaths = [
        filepath
        for projects_dir in dirs
        for filepath in glob.glob(str(projects_dir / "**" / "*.jsonl"), recursive=True)
    ]
    for filepath in filepaths:
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
def _is_python_process(pid: int) -> bool:
    """PID가 python 계열 프로세스인지 확인. 플랫폼별 방식 사용."""
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_ulong(1024)
                if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return "python" in buf.value.lower()
                return False
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False


def acquire_pid_lock(pid_file: Path, logger: logging.Logger) -> bool:
    """PID 파일을 생성해 단일 인스턴스를 보장한다. 이미 실행 중이면 False를 반환한다."""
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            if _is_python_process(existing_pid):
                logger.error(f"이미 실행 중입니다 (PID: {existing_pid}). 종료합니다.")
                return False
            else:
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

def _get_pending_runs(cfg: dict, logger: logging.Logger) -> list:
    """in_progress 및 queued 상태의 token-reset-notify 워크플로우 실행 목록을 반환한다."""
    token = cfg.get("GITHUB_TOKEN", "")
    owner = cfg.get("GITHUB_OWNER", "")
    repo = cfg.get("GITHUB_REPO", "token_alert")

    if not all([token, owner]):
        return []

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    runs = []
    for status in ("in_progress", "queued"):
        list_url = (
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows"
            f"/token-reset-notify.yml/runs?status={status}&per_page=10"
        )
        try:
            req = urllib.request.Request(list_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            runs.extend(data.get("workflow_runs", []))
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            logger.warning(f"진행 중 워크플로우 목록 조회 실패 (status={status}): {e}")

    return runs


def cancel_previous_workflow_runs(cfg: dict, logger: logging.Logger, runs: list | None = None) -> None:
    """진행 중인 이전 token-reset-notify 워크플로우 실행을 모두 취소한다."""
    token = cfg.get("GITHUB_TOKEN", "")
    owner = cfg.get("GITHUB_OWNER", "")
    repo = cfg.get("GITHUB_REPO", "token_alert")

    if not all([token, owner]):
        return

    if runs is None:
        runs = _get_pending_runs(cfg, logger)

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

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
    target_label: str = "Claude Code 5시간",
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
    advance = int(cfg.get("NOTIFY_ADVANCE_SECONDS", "0"))
    notify_time = reset_time - timedelta(seconds=advance)
    notify_iso = notify_time.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/token-reset-notify.yml/dispatches"

    payload = json.dumps(
        {
            "ref": cfg.get("GITHUB_REF", "main"),
            "inputs": {
                "reset_time": reset_iso,
                "notify_time": notify_iso,
                "target_label": target_label,
            },
        }
    ).encode("utf-8")

    if not dry_run and cfg.get("_SKIP_CANCEL_PENDING") != "1":
        pending_runs = _get_pending_runs(cfg, logger)
        cancel_previous_workflow_runs(cfg, logger, pending_runs)

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
def send_telegram_message(
    cfg: dict,
    text: str,
    logger: logging.Logger,
    dry_run: bool = False,
    reply_markup: dict | None = None,
) -> None:
    """텔레그램 메시지를 전송한다. 실패 시 예외 없이 로그만 남긴다."""
    if dry_run:
        logger.info(f"[DRY-RUN] 텔레그램 전송 건너뜀: {text[:80]}")
        return

    token = cfg.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = cfg.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정 — 전송 건너뜀")
        return

    payload_data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload_data["reply_markup"] = reply_markup
    payload = json.dumps(payload_data).encode("utf-8")
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
        f"?offset={offset}&timeout=30&allowed_updates=%5B%22message%22%2C%22callback_query%22%5D"
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


def _format_remaining(reset_dt: datetime, now: datetime) -> str:
    secs = int((reset_dt - now).total_seconds())
    secs = max(secs, 0)
    if secs >= 86400:
        days, rem = divmod(secs, 86400)
        hours = rem // 3600
        return f"{days}일 {hours}시간" if hours > 0 else f"{days}일"
    elif secs >= 3600:
        hours, rem = divmod(secs, 3600)
        minutes = rem // 60
        return f"{hours}시간 {minutes}분"
    else:
        minutes = secs // 60
        return f"{minutes}분"


def _window_title(window: ProviderWindow) -> str:
    name = "5시간" if window.window == "five_hour" else "7일"
    return f"{window.label} {name} 한도"


def _format_provider_window(window: ProviderWindow, now: datetime) -> str:
    KST = timezone(timedelta(hours=9))
    reset = window.reset
    if reset is None:
        return ""
    reset_kst = reset.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    metric = ""
    if window.remaining_percentage is not None:
        metric = f"\n• 남은 비율: {window.remaining_percentage:g}%"
    elif window.used_percentage is not None:
        metric = f"\n• 사용 비율: {window.used_percentage:g}%"
    estimate = " (추정)" if window.estimated else ""
    return (
        f"<b>{_window_title(window)}</b>{estimate}\n"
        f"• 남은 시간: <b>{_format_remaining(reset, now)}</b>"
        f"{metric}\n"
        f"• 초기화 시각: <code>{reset_kst}</code>"
    )


def format_limit_status_reply(status: LimitStatus) -> str:
    now = datetime.now(timezone.utc)
    KST = timezone(timedelta(hours=9))
    lines = []

    if status.provider_windows:
        lines = [line for line in (_format_provider_window(window, now) for window in status.provider_windows) if line]
        if lines:
            prefix = "⏳ <b>토큰 한도 현황</b>\n──────────────────"
            suffix = "\n\n(JSONL 로그 기반 추정값)" if status.estimated else ""
            return f"{prefix}\n" + "\n\n".join(lines) + "\n──────────────────" + suffix

    if status.five_hour_reset is not None:
        reset_kst = status.five_hour_reset.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
        usage_val = status.five_hour_used_percentage
        usage = "" if usage_val is None else f"\n• 사용 비율: {usage_val:g}%"
        lines.append(
            f"⚡ <b>5시간 단기 한도</b>\n"
            f"• 남은 시간: <b>{_format_remaining(status.five_hour_reset, now)}</b>"
            f"{usage}\n"
            f"• 초기화 시각: <code>{reset_kst}</code>"
        )

    if status.seven_day_reset is not None:
        reset_kst = status.seven_day_reset.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
        usage_val = status.seven_day_used_percentage
        usage = "" if usage_val is None else f"\n• 사용 비율: {usage_val:g}%"
        lines.append(
            f"📅 <b>7일 장기 한도</b>\n"
            f"• 남은 시간: <b>{_format_remaining(status.seven_day_reset, now)}</b>"
            f"{usage}\n"
            f"• 초기화 시각: <code>{reset_kst}</code>"
        )

    if not lines:
        return "아직 Claude Code 한도 값을 받은 적이 없습니다.\nClaude Code statusLine을 한 번 실행하면 정확한 값이 표시됩니다."

    prefix = "⏳ <b>Claude Code 토큰 한도 현황</b>\n──────────────────"
    suffix = "\n\n(JSONL 로그 기반 추정값)" if status.estimated else ""
    return f"{prefix}\n" + "\n\n".join(lines) + "\n──────────────────" + suffix


def _provider_choice_markup() -> dict:
    return {
        "inline_keyboard": [[
            {"text": "Codex", "callback_data": "status:codex"},
            {"text": "Claude", "callback_data": "status:claude"},
        ]]
    }


def _filter_status_by_provider(status: LimitStatus, provider: str) -> LimitStatus:
    if status.provider_windows:
        windows = tuple(window for window in status.provider_windows if window.provider == provider)
        filtered = _status_from_windows(windows, status.source or provider, status.estimated)
        return filtered if filtered.five_hour_reset is not None or filtered.seven_day_reset is not None else LimitStatus()
    return status if provider == "claude" else LimitStatus()


def _empty_provider_reply(provider: str) -> str:
    name = "Codex" if provider == "codex" else "Claude"
    return f"아직 {name} 한도 값을 받은 적이 없습니다."


def _provider_status_reply(cfg: dict, provider: str) -> str:
    status = _filter_status_by_provider(get_current_limit_status(cfg), provider)
    if status.five_hour_reset is None and status.seven_day_reset is None:
        return _empty_provider_reply(provider)
    return format_limit_status_reply(status)


def _window_state_key(window: ProviderWindow) -> str:
    identity = window.profile or window.account or "default"
    return f"{window.provider}:{identity}:{window.window}"


def _window_reset_iso(window: ProviderWindow) -> str:
    KST = timezone(timedelta(hours=9))
    return window.reset.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def _schedule_provider_windows(
    cfg: dict,
    status: LimitStatus,
    logger: logging.Logger,
    dry_run: bool,
) -> None:
    now = datetime.now(timezone.utc)
    state = load_state()
    scheduled = state.get("scheduled_resets")
    if not isinstance(scheduled, dict):
        scheduled = {}
    changed = False
    dispatch_cfg = dict(cfg)
    dispatch_cfg["_SKIP_CANCEL_PENDING"] = "1"

    for window in status.provider_windows:
        if window.reset is None:
            continue
        remaining_secs = (window.reset - now).total_seconds()
        key = _window_state_key(window)
        if remaining_secs <= 300:
            if remaining_secs <= 0 and key in scheduled:
                scheduled.pop(key, None)
                changed = True
            continue

        reset_iso = _window_reset_iso(window)
        previous = scheduled.get(key)
        if isinstance(previous, str):
            try:
                previous_dt = datetime.fromisoformat(previous)
                if abs((window.reset - previous_dt).total_seconds()) < 60:
                    continue
            except ValueError:
                pass
        if previous == reset_iso:
            continue

        window_name = "5시간" if window.window == "five_hour" else "7일"
        target_label = f"{window.label} {window_name}"
        logger.info(f"{target_label} 초기화 예정: {reset_iso}")
        if dispatch_github_workflow(dispatch_cfg, window.reset, logger, dry_run=dry_run, target_label=target_label):
            scheduled[key] = reset_iso
            changed = True

    if changed:
        state["scheduled_resets"] = scheduled
        state["dispatched_at"] = now.isoformat()
        save_state(state)


def handle_telegram_command(cfg: dict, update: dict, logger: logging.Logger, dry_run: bool = False) -> None:
    """텔레그램 update를 처리하고 명령에 맞는 응답을 전송한다."""
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        data = str(callback.get("data", ""))
        message = callback.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        allowed_chat_id = str(cfg.get("TELEGRAM_CHAT_ID", ""))
        if chat_id != allowed_chat_id:
            logger.warning(f"허용되지 않은 chat_id에서 명령 수신: {chat_id}")
            return
        if data in ("status:codex", "status:claude"):
            provider = data.split(":", 1)[1]
            send_telegram_message(cfg, _provider_status_reply(cfg, provider), logger, dry_run=dry_run)
        return

    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    allowed_chat_id = str(cfg.get("TELEGRAM_CHAT_ID", ""))
    if chat_id != allowed_chat_id:
        logger.warning(f"허용되지 않은 chat_id에서 명령 수신: {chat_id}")
        return

    parts = text.split()
    command = parts[0].split("@")[0].lower()

    if command == "/status":
        if len(parts) > 1 and parts[1].lower() in ("codex", "claude"):
            reply = _provider_status_reply(cfg, parts[1].lower())
            logger.info(f"[BOT] /status 응답: {reply[:60]}")
            send_telegram_message(cfg, reply, logger, dry_run=dry_run)
        else:
            reply = "조회할 공급자를 선택하세요.\n텍스트로는 /status codex 또는 /status claude 를 사용할 수 있습니다."
            send_telegram_message(cfg, reply, logger, dry_run=dry_run, reply_markup=_provider_choice_markup())

    elif command.startswith("/"):
        reply = "사용 가능한 명령:\n/status — 공급자 선택\n/status codex — Codex 초기화 조회\n/status claude — Claude 초기화 조회"
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
    status = get_current_limit_status(cfg)
    if status.provider_windows:
        _schedule_provider_windows(cfg, status, logger, dry_run)
        return

    reset_time = status.five_hour_reset
    if reset_time is None:
        logger.debug("초기화 시각 없음 — 알림 예약 불필요")
        return
    logger.debug(f"{status.source}에서 초기화 시각 읽음: {reset_time.isoformat()}")
    now = datetime.now(timezone.utc)

    # 이미 지났거나 너무 임박한 초기화 시각은 무시 (GitHub Actions 지연 고려, 최소 5분)
    MIN_DISPATCH_SECONDS = 300
    remaining_secs = (reset_time - now).total_seconds()
    if remaining_secs <= MIN_DISPATCH_SECONDS:
        logger.debug(f"초기화까지 {int(remaining_secs)}초 미만 — dispatch 건너뜀")
        state = load_state()
        if "scheduled_reset_time" in state and remaining_secs <= 0:
            state.pop("scheduled_reset_time", None)
            save_state(state)
        return

    # 이미 같은 시각으로 예약했으면 중복 dispatch 방지 (1분 이내 차이도 건너뜀)
    state = load_state()
    prev_scheduled = state.get("scheduled_reset_time")

    KST = timezone(timedelta(hours=9))
    reset_iso = reset_time.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    if prev_scheduled:
        try:
            prev_dt = datetime.fromisoformat(prev_scheduled)
            diff_secs = abs((reset_time - prev_dt).total_seconds())
            if diff_secs < 60:
                logger.debug(f"이미 예약됨 (차이 {int(diff_secs)}초): {prev_scheduled} — 중복 dispatch 건너뜀")
                return
        except ValueError:
            pass
    if prev_scheduled == reset_iso:
        logger.debug(f"이미 예약됨: {reset_iso} — 중복 dispatch 건너뜀")
        return

    remaining = reset_time - now

    logger.info(
        f"초기화 예정: {reset_iso} (KST) "
        f"(약 {int(remaining.total_seconds() // 60)}분 후)"
    )

    ok = dispatch_github_workflow(cfg, reset_time, logger, dry_run=dry_run)

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
    parser.add_argument("--write-status-line", action="store_true", help="stdin의 statusLine JSON을 usage cache로 저장")
    args = parser.parse_args()

    logger = setup_logging(verbose=args.verbose)
    cfg = load_config()

    if args.write_status_line:
        try:
            write_usage_cache_from_status_line(json.load(sys.stdin))
        except json.JSONDecodeError as exc:
            logger.error(f"statusLine JSON 파싱 실패: {exc}")
            sys.exit(1)
        return

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
