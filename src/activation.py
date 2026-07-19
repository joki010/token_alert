"""Claude five-hour window activation policy and process state machine."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


POLICY_FILE = Path.home() / ".config" / "token-alert" / "activation-policy.json"
ACTIVATION_STATE_VERSION = 1
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (5, 10)
DEFAULT_PROMPT = "."
DEFAULT_TIMEOUT_SECONDS = 120
TERMINATE_GRACE_SECONDS = 5
MAX_TERMINAL_RECORDS = 32
RESET_KEY_PREFIX = "claude:default:five_hour:"
STATE_CORRUPTION_SENTINEL = "__token_alert_state_corrupt__"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reset_epoch(reset_at: datetime) -> int:
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    return int(reset_at.astimezone(timezone.utc).timestamp())


def canonical_reset_key(reset_at: datetime) -> str:
    """Return the stable identity for a Claude five-hour reset."""
    return f"{RESET_KEY_PREFIX}{_reset_epoch(reset_at)}"


def canonical_reset_at(reset_at: datetime) -> str:
    """Return the reset timestamp normalized to UTC whole seconds."""
    return _iso_datetime(datetime.fromtimestamp(_reset_epoch(reset_at), timezone.utc))


def _reset_epoch_from_key(reset_key: Any) -> int | None:
    if not isinstance(reset_key, str) or not reset_key.startswith(RESET_KEY_PREFIX):
        return None
    suffix = reset_key[len(RESET_KEY_PREFIX):]
    try:
        epoch = int(suffix)
    except (TypeError, ValueError):
        return None
    return epoch if str(epoch) == suffix else None


def read_activation_policy(path: Path | None = None) -> dict[str, Any]:
    """Read the tray-owned policy, failing closed for every invalid form."""
    if path is None:
        path = POLICY_FILE
    try:
        import json

        with Path(path).open("r", encoding="utf-8") as handle:
            policy = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {"version": 1, "enabled": False, "enabled_at": None}

    if not isinstance(policy, dict) or policy.get("version") != 1:
        return {"version": 1, "enabled": False, "enabled_at": None}
    if type(policy.get("enabled")) is not bool:
        return {"version": 1, "enabled": False, "enabled_at": None}
    enabled_at = _parse_aware_datetime(policy.get("enabled_at"))
    if enabled_at is None:
        return {"version": 1, "enabled": False, "enabled_at": None}
    return {
        "version": 1,
        "enabled": policy["enabled"],
        "enabled_at": _iso_datetime(enabled_at),
    }


def policy_allows_reset(policy: dict[str, Any], reset_at: datetime) -> bool:
    """Return whether the policy was enabled before this reset."""
    if policy.get("enabled") is not True:
        return False
    enabled_at = _parse_aware_datetime(policy.get("enabled_at"))
    return enabled_at is not None and enabled_at < reset_at


def resolve_cli_path(cfg: dict[str, Any]) -> tuple[Path | None, str | None]:
    """Resolve and validate the configured absolute Claude executable path."""
    raw = cfg.get("CLAUDE_CLI_PATH")
    if not isinstance(raw, str) or not raw.strip():
        return None, "CLAUDE_CLI_PATH is missing"
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        return None, "CLAUDE_CLI_PATH must be absolute"
    if not candidate.is_file():
        return None, "CLAUDE_CLI_PATH is not an executable file"
    if not os.access(candidate, os.X_OK):
        return None, "CLAUDE_CLI_PATH is not executable"
    return candidate, None


def _empty_namespace() -> dict[str, Any]:
    return {
        "version": ACTIVATION_STATE_VERSION,
        "pending": None,
        "records": {},
        "high_watermark_reset_epoch": None,
    }


def _valid_pending(pending: Any) -> bool:
    if pending is None:
        return True
    if not isinstance(pending, dict):
        return False
    required = {"reset_key", "reset_at", "armed_at"}
    if not required.issubset(pending):
        return False
    if not isinstance(pending["reset_key"], str) or not isinstance(pending["reset_at"], str):
        return False
    reset_at = _parse_aware_datetime(pending["reset_at"])
    if reset_at is None or _parse_aware_datetime(pending["armed_at"]) is None:
        return False
    if _reset_epoch_from_key(pending["reset_key"]) != _reset_epoch(reset_at):
        return False
    status = pending.get("status", "pending")
    attempts = pending.get("attempts", 0)
    if status not in {"pending", "started"}:
        return False
    return type(attempts) is int and 0 <= attempts <= MAX_ATTEMPTS


def _valid_record(key: Any, record: Any) -> bool:
    if not isinstance(key, str) or not isinstance(record, dict):
        return False
    if record.get("reset_key") != key:
        return False
    if _reset_epoch_from_key(key) is None:
        return False
    if record.get("status") not in {"succeeded", "final_failed", "unknown"}:
        return False
    if type(record.get("attempts")) is not int or not 0 <= record["attempts"] <= MAX_ATTEMPTS:
        return False
    for field in ("started_at", "finished_at"):
        value = record.get(field)
        if value is not None and _parse_aware_datetime(value) is None:
            return False
    return record.get("error_kind") is None or isinstance(record.get("error_kind"), str)


def _trim_records(records: dict[str, Any]) -> dict[str, Any]:
    ordered = sorted(
        records.items(),
        key=lambda item: (
            _reset_epoch_from_key(item[0])
            if _reset_epoch_from_key(item[0]) is not None
            else -1
        ),
        reverse=True,
    )
    return dict(ordered[:MAX_TERMINAL_RECORDS])


def load_activation_namespace(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return a validated namespace and whether an existing one was valid."""
    raw = state.get("claude_activation")
    if raw is None:
        return _empty_namespace(), True
    if not isinstance(raw, dict) or raw.get("version") != ACTIVATION_STATE_VERSION:
        return _empty_namespace(), False
    if not _valid_pending(raw.get("pending")):
        return _empty_namespace(), False
    records = raw.get("records")
    if not isinstance(records, dict) or any(not _valid_record(k, v) for k, v in records.items()):
        return _empty_namespace(), False
    high_watermark = raw.get("high_watermark_reset_epoch")
    if high_watermark is not None and (type(high_watermark) is not int or high_watermark < 0):
        return _empty_namespace(), False
    pending = raw.get("pending")
    if isinstance(pending, dict):
        pending = dict(pending)
        pending.setdefault("status", "pending")
        pending.setdefault("attempts", 0)
        pending.setdefault("started_at", None)
        pending.setdefault("error_kind", None)
    namespace = {
        "version": ACTIVATION_STATE_VERSION,
        "pending": pending,
        "records": _trim_records(dict(records)),
        "high_watermark_reset_epoch": high_watermark,
    }
    return namespace, True


def _terminal_record(
    reset_key: str,
    status: str,
    attempts: int,
    started_at: str | None,
    finished_at: datetime,
    error_kind: str | None = None,
) -> dict[str, Any]:
    return {
        "reset_key": reset_key,
        "status": status,
        "attempts": attempts,
        "started_at": started_at,
        "finished_at": _iso_datetime(finished_at),
        "error_kind": error_kind,
    }


def _commit_terminal(
    namespace: dict[str, Any],
    record: dict[str, Any],
    reset_epoch: int,
) -> None:
    records = namespace["records"]
    records[record["reset_key"]] = record
    namespace["records"] = _trim_records(records)
    watermark = namespace.get("high_watermark_reset_epoch")
    if watermark is None or reset_epoch > watermark:
        namespace["high_watermark_reset_epoch"] = reset_epoch
    pending = namespace.get("pending")
    if isinstance(pending, dict) and pending.get("reset_key") == record["reset_key"]:
        namespace["pending"] = None


def _save_namespace(state: dict[str, Any], namespace: dict[str, Any], save_state: Callable[[dict[str, Any]], None]) -> None:
    state["claude_activation"] = namespace
    save_state(state)


def _mark_started(namespace: dict[str, Any], now: datetime) -> dict[str, Any]:
    pending = dict(namespace["pending"])
    pending["status"] = "started"
    pending["attempts"] = int(pending.get("attempts", 0)) + 1
    pending["started_at"] = _iso_datetime(now)
    pending["error_kind"] = None
    namespace["pending"] = pending
    return pending


@dataclass
class _ActiveExecution:
    state: dict[str, Any]
    namespace: dict[str, Any]
    save_state: Callable[[dict[str, Any]], None]
    logger: Any
    child: Any = None
    terminated: bool = False
    unknown_committed: bool = False

    def commit_unknown(self, error_kind: str) -> None:
        if self.unknown_committed:
            return
        pending = self.namespace.get("pending")
        if not isinstance(pending, dict):
            return
        reset_key = pending["reset_key"]
        reset_epoch = _reset_epoch_from_key(reset_key)
        if reset_epoch is None:
            return
        record = _terminal_record(
            reset_key,
            "unknown",
            int(pending.get("attempts", 0)),
            pending.get("started_at"),
            _utc_now(),
            error_kind,
        )
        _commit_terminal(self.namespace, record, reset_epoch)
        _save_namespace(self.state, self.namespace, self.save_state)
        self.unknown_committed = True


_ACTIVE_EXECUTION: _ActiveExecution | None = None


def _terminate_child(child: Any, logger: Any) -> None:
    if child is None:
        return
    try:
        child.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        child.wait(timeout=TERMINATE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    except (OSError, ProcessLookupError):
        return
    try:
        child.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        child.wait()
    except (OSError, ProcessLookupError):
        pass


def handle_parent_termination() -> None:
    """Clean the current child and commit unknown for parent termination."""
    active = _ACTIVE_EXECUTION
    if active is None:
        return
    active.terminated = True
    _terminate_child(active.child, active.logger)
    active.commit_unknown("parent_termination")


def _finish(
    active: _ActiveExecution,
    status: str,
    error_kind: str | None,
    now: datetime,
) -> None:
    pending = active.namespace.get("pending")
    if not isinstance(pending, dict):
        return
    reset_key = pending["reset_key"]
    reset_epoch = _reset_epoch_from_key(reset_key)
    if reset_epoch is None:
        return
    record = _terminal_record(
        reset_key,
        status,
        int(pending.get("attempts", 0)),
        pending.get("started_at"),
        now,
        error_kind,
    )
    _commit_terminal(active.namespace, record, reset_epoch)
    _save_namespace(active.state, active.namespace, active.save_state)


def _spawn_and_wait(
    active: _ActiveExecution,
    cli_path: Path,
    prompt: str,
    timeout_seconds: float,
    popen_factory: Callable[..., Any],
) -> tuple[str, str | None]:
    try:
        child = popen_factory(
            [str(cli_path), "-p", prompt],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return "retry", "spawn_error"

    active.child = child
    if active.terminated:
        _terminate_child(child, active.logger)
        return "unknown", "parent_termination"
    try:
        child.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_child(child, active.logger)
        return "retry", "timeout"
    except (OSError, ProcessLookupError):
        return "retry", "wait_error"
    finally:
        active.child = None

    if active.terminated:
        return "unknown", "parent_termination"
    if child.returncode == 0:
        return "success", None
    return "retry", "nonzero_exit"


def _execute_pending(
    cfg: dict[str, Any],
    namespace: dict[str, Any],
    state: dict[str, Any],
    save_state: Callable[[dict[str, Any]], None],
    logger: Any,
    *,
    now: datetime,
    popen_factory: Callable[..., Any],
    sleep_fn: Callable[[float], None],
) -> None:
    cli_path, configuration_error = resolve_cli_path(cfg)
    if cli_path is None:
        logger.warning("Claude activation configuration error: %s", configuration_error)
        _finish(
            _ActiveExecution(state, namespace, save_state, logger),
            "final_failed",
            "configuration_error",
            now,
        )
        return

    prompt = cfg.get("CLAUDE_ACTIVATION_PROMPT", DEFAULT_PROMPT)
    if not isinstance(prompt, str):
        prompt = DEFAULT_PROMPT
    try:
        timeout_seconds = float(cfg.get("CLAUDE_ACTIVATION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        if timeout_seconds <= 0:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning("Claude activation configuration error: invalid timeout")
        _finish(
            _ActiveExecution(state, namespace, save_state, logger),
            "final_failed",
            "configuration_error",
            now,
        )
        return

    active = _ActiveExecution(state, namespace, save_state, logger)
    global _ACTIVE_EXECUTION
    _ACTIVE_EXECUTION = active
    try:
        for attempt_index in range(MAX_ATTEMPTS):
            if active.terminated or active.unknown_committed:
                return
            _mark_started(namespace, _utc_now())
            _save_namespace(state, namespace, save_state)
            outcome, error_kind = _spawn_and_wait(
                active,
                cli_path,
                prompt,
                timeout_seconds,
                popen_factory,
            )
            if outcome == "unknown":
                active.commit_unknown(error_kind or "parent_termination")
                return
            if outcome == "success":
                _finish(active, "succeeded", None, _utc_now())
                return
            if attempt_index >= MAX_ATTEMPTS - 1:
                _finish(active, "final_failed", error_kind, _utc_now())
                return
            pending = namespace.get("pending")
            if isinstance(pending, dict):
                pending["status"] = "pending"
                pending["error_kind"] = error_kind
                namespace["pending"] = pending
                _save_namespace(state, namespace, save_state)
            sleep_fn(RETRY_DELAYS_SECONDS[attempt_index])
    finally:
        _ACTIVE_EXECUTION = None


def _arm_reset(namespace: dict[str, Any], reset_at: datetime, now: datetime) -> bool:
    reset_epoch = _reset_epoch(reset_at)
    reset_key = canonical_reset_key(reset_at)
    high_watermark = namespace.get("high_watermark_reset_epoch")
    if high_watermark is not None and reset_epoch <= high_watermark:
        return False
    if reset_key in namespace["records"]:
        return False
    pending = namespace.get("pending")
    if isinstance(pending, dict):
        pending_epoch = _reset_epoch_from_key(pending["reset_key"])
        if pending_epoch is None:
            return False
        if pending_epoch >= reset_epoch:
            return False
    namespace["pending"] = {
        "reset_key": reset_key,
        "reset_at": canonical_reset_at(reset_at),
        "armed_at": _iso_datetime(now),
        "status": "pending",
        "attempts": 0,
        "started_at": None,
        "error_kind": None,
    }
    return True


def _resolve_restart(namespace: dict[str, Any], state: dict[str, Any], save_state: Callable[[dict[str, Any]], None], now: datetime) -> bool:
    pending = namespace.get("pending")
    if not isinstance(pending, dict) or pending.get("status") != "started":
        return False
    reset_key = pending["reset_key"]
    reset_epoch = _reset_epoch_from_key(reset_key)
    if reset_epoch is None:
        return False
    record = _terminal_record(
        reset_key,
        "unknown",
        int(pending.get("attempts", 0)),
        pending.get("started_at"),
        now,
        "restart_unknown",
    )
    _commit_terminal(namespace, record, reset_epoch)
    _save_namespace(state, namespace, save_state)
    return True


def activate_claude_reset(
    cfg: dict[str, Any],
    reset_at: datetime | None,
    logger: Any,
    state: dict[str, Any],
    save_state: Callable[[dict[str, Any]], None],
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    policy_path: Path | None = None,
    popen_factory: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    """Arm and, when due, execute one Claude reset activation.

    The caller supplies the watcher state and its atomic save seam.  This
    keeps all activation mutations inside the watcher-owned state file while
    allowing focused tests to replace process and clock adapters.
    """
    if now is None:
        now = _utc_now()
    if popen_factory is None:
        popen_factory = subprocess.Popen
    if sleep_fn is None:
        sleep_fn = time.sleep
    if reset_at is not None and reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)

    if state.get(STATE_CORRUPTION_SENTINEL) is True:
        logger.warning("Watcher state is corrupt; Claude activation is disabled until the state is repaired or deleted")
        return

    policy = read_activation_policy(policy_path)
    namespace, valid = load_activation_namespace(state)
    if not valid:
        logger.warning("Claude activation state is invalid; activation is disabled")
        return

    if dry_run:
        if reset_at is not None:
            logger.info(
                "[DRY-RUN] Claude activation preview — reset=%s enabled=%s",
                canonical_reset_at(reset_at),
                policy.get("enabled") is True,
            )
        return

    _resolve_restart(namespace, state, save_state, now)

    pending = namespace.get("pending")
    pending_reset = None
    if isinstance(pending, dict) and pending.get("status") == "pending":
        pending_reset = _parse_aware_datetime(pending.get("reset_at"))
        if pending_reset is not None and pending_reset <= now:
            if not policy_allows_reset(policy, pending_reset):
                _finish(
                    _ActiveExecution(state, namespace, save_state, logger),
                    "final_failed",
                    "policy_stale",
                    now,
                )
                pending = namespace.get("pending")
                pending_reset = None
            else:
                _execute_pending(
                    cfg,
                    namespace,
                    state,
                    save_state,
                    logger,
                    now=now,
                    popen_factory=popen_factory,
                    sleep_fn=sleep_fn,
                )
                pending = namespace.get("pending")
                pending_reset = None

    if reset_at is not None and reset_at > now:
        if _arm_reset(namespace, reset_at, now):
            _save_namespace(state, namespace, save_state)

    pending = namespace.get("pending")
    if not isinstance(pending, dict) or pending.get("status") != "pending":
        return
    pending_reset = _parse_aware_datetime(pending.get("reset_at"))
    if pending_reset is None or pending_reset > now:
        return
    if not policy_allows_reset(policy, pending_reset):
        return

    _execute_pending(
        cfg,
        namespace,
        state,
        save_state,
        logger,
        now=now,
        popen_factory=popen_factory,
        sleep_fn=sleep_fn,
    )


run_activation = activate_claude_reset
