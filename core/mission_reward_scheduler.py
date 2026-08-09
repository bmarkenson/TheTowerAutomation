"""Bound reward probes and Sunday Daily Mission claim policy."""

from __future__ import annotations

from dataclasses import dataclass
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


PROBE_COOLDOWN_SECONDS = 30 * 60
FAILURE_RETRY_SECONDS = 5 * 60
LOCAL_TIMEZONE = ZoneInfo("America/Los_Angeles")


def daily_mission_claims_allowed(now: datetime | None = None) -> bool:
    """Return whether ordinary Daily Mission rewards may be claimed freely.

    Claims are banked on local Sunday until the server's Monday 00:00 UTC
    weekly reset.  This is 17:00 PDT and 16:00 PST.  While this returns false,
    the reward handler may release exactly two ordinary claims from
    authoritatively verified ``8/8`` capacity.  Weekly mission chests, Event
    rewards, and Guild chests are intentionally outside this policy.
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


def weekly_mission_cycle_start(now: datetime | None = None) -> datetime:
    """Return the Monday 00:00 UTC boundary for the active weekly cycle."""

    current = _as_utc(now)
    return current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=current.weekday())


@dataclass
class WeeklyChestReviewState:
    """Remember a complete weekly-chest review within one process and cycle."""

    _cycle_start: datetime | None = None
    _reviewed_unlocked_chests: int | None = None

    def covers(
        self,
        unlocked_chests: int | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        if unlocked_chests is None or unlocked_chests < 0:
            return False
        self._refresh_cycle(now)
        if self._reviewed_unlocked_chests == unlocked_chests:
            return True
        self._reviewed_unlocked_chests = None
        return False

    def mark_reviewed(
        self,
        unlocked_chests: int | None,
        *,
        now: datetime | None = None,
    ) -> None:
        if unlocked_chests is None or unlocked_chests < 0:
            return
        self._refresh_cycle(now)
        self._reviewed_unlocked_chests = unlocked_chests

    def invalidate(self) -> None:
        """Discard retained coverage after a contradictory or successful claim."""

        self._reviewed_unlocked_chests = None

    def _refresh_cycle(self, now: datetime | None) -> None:
        cycle_start = weekly_mission_cycle_start(now)
        if cycle_start != self._cycle_start:
            self._cycle_start = cycle_start
            self._reviewed_unlocked_chests = None


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
    "WeeklyChestReviewState",
    "daily_mission_claims_allowed",
    "seconds_until_daily_mission_release",
    "weekly_mission_cycle_start",
]
