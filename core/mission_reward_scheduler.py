"""Bound reward probes and hold Daily Mission claims until weekly reset."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


PROBE_COOLDOWN_SECONDS = 30 * 60
FAILURE_RETRY_SECONDS = 5 * 60
LOCAL_TIMEZONE = ZoneInfo("America/Los_Angeles")


def daily_mission_claims_allowed(now: datetime | None = None) -> bool:
    """Return whether ordinary Daily Mission rewards may be claimed now.

    Claims are banked on local Sunday until the server's Monday 00:00 UTC
    weekly reset.  This is 17:00 PDT and 16:00 PST.  Weekly mission chests,
    Event rewards, and Guild chests are intentionally outside this policy.
    """

    current_utc = _as_utc(now)
    current_local = current_utc.astimezone(LOCAL_TIMEZONE)
    return not (current_local.weekday() == 6 and current_utc.weekday() == 6)


def seconds_until_daily_mission_release(now: datetime | None = None) -> float | None:
    """Return seconds to the weekly reset while claims are held, else ``None``."""

    current_utc = _as_utc(now)
    if daily_mission_claims_allowed(current_utc):
        return None
    release = (current_utc + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(0.0, (release - current_utc).total_seconds())


def _as_utc(now: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("mission reward wall time must be timezone-aware")
    return current.astimezone(timezone.utc)


class MissionRewardScheduler:
    """In-process cooldown for badge-triggered Daily/Event/Guild inspection."""

    def __init__(self) -> None:
        self._not_before = 0.0

    def should_attempt(
        self,
        *,
        alert_visible: bool,
        now: float | None = None,
    ) -> bool:
        current = time.monotonic() if now is None else float(now)
        return bool(alert_visible and current >= self._not_before)

    def mark_completed(
        self,
        *,
        now: float | None = None,
        wall_now: datetime | None = None,
    ) -> None:
        current = time.monotonic() if now is None else float(now)
        self._set_cooldown(current, PROBE_COOLDOWN_SECONDS, wall_now)

    def mark_failed(
        self,
        *,
        now: float | None = None,
        wall_now: datetime | None = None,
    ) -> None:
        current = time.monotonic() if now is None else float(now)
        self._set_cooldown(current, FAILURE_RETRY_SECONDS, wall_now)

    def _set_cooldown(
        self,
        current: float,
        cooldown: float,
        wall_now: datetime | None,
    ) -> None:
        delay = float(cooldown)
        if wall_now is not None:
            until_release = seconds_until_daily_mission_release(wall_now)
            if until_release is not None:
                delay = min(delay, until_release)
        self._not_before = current + delay


__all__ = [
    "FAILURE_RETRY_SECONDS",
    "LOCAL_TIMEZONE",
    "MissionRewardScheduler",
    "PROBE_COOLDOWN_SECONDS",
    "daily_mission_claims_allowed",
    "seconds_until_daily_mission_release",
]
