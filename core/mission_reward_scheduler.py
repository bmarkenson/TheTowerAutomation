"""Bound repeated side-menu reward probes while an attention dot persists."""

from __future__ import annotations

import time


PROBE_COOLDOWN_SECONDS = 30 * 60
FAILURE_RETRY_SECONDS = 5 * 60


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

    def mark_completed(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        self._not_before = current + PROBE_COOLDOWN_SECONDS

    def mark_failed(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        self._not_before = current + FAILURE_RETRY_SECONDS


__all__ = [
    "FAILURE_RETRY_SECONDS",
    "MissionRewardScheduler",
    "PROBE_COOLDOWN_SECONDS",
]
