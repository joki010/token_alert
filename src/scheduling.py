"""Pure scheduling policy for GitHub Actions alert workflows."""

from __future__ import annotations

from datetime import datetime, timezone


MIN_SCHEDULE_SECONDS = 300
MAX_SCHEDULE_SECONDS = 21600


def remaining_seconds(reset_at: datetime, now: datetime | None = None) -> float:
    """Return seconds until ``reset_at`` using timezone-aware UTC datetimes."""
    if now is None:
        now = datetime.now(timezone.utc)
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (reset_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()


def is_scheduleable(reset_at: datetime, now: datetime | None = None) -> bool:
    """Return whether an alert may be cancelled, dispatched, and recorded.

    The lower bound is exclusive and the upper bound is inclusive:
    ``300 < remaining <= 21600``.
    """
    remaining = remaining_seconds(reset_at, now)
    return MIN_SCHEDULE_SECONDS < remaining <= MAX_SCHEDULE_SECONDS


def should_schedule(reset_at: datetime, now: datetime | None = None) -> bool:
    """Compatibility spelling for callers that ask the scheduling question."""
    return is_scheduleable(reset_at, now)


def is_within_schedule_horizon(reset_at: datetime, now: datetime | None = None) -> bool:
    """Compatibility spelling for tests and integrations."""
    return is_scheduleable(reset_at, now)

