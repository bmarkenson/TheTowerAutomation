"""Persisted UTC-midnight scheduling for the Daily Gem handler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from typing import Optional

from utils.logger import log


UTC = timezone.utc
ROLLOVER_GRACE = timedelta(minutes=1)
FAILURE_RETRY_DELAY = timedelta(minutes=5)


class DailyGemScheduler:
    """Run at most one completed Daily Gem probe per UTC game day."""

    def __init__(self, state_path: Path | str) -> None:
        self.state_path = Path(state_path)
        self._completed_utc_day: Optional[str] = None
        self._retry_not_before: Optional[datetime] = None
        self._load()

    @staticmethod
    def _as_utc(now: Optional[datetime] = None) -> datetime:
        current = datetime.now(UTC) if now is None else now
        if current.tzinfo is None:
            raise ValueError("Daily Gem scheduling requires a timezone-aware datetime")
        return current.astimezone(UTC)

    @classmethod
    def game_day(cls, now: Optional[datetime] = None) -> str:
        """Return the game's UTC date identifier for ``now``."""

        return cls._as_utc(now).date().isoformat()

    def should_attempt(
        self,
        *,
        badge_visible: bool = False,
        now: Optional[datetime] = None,
    ) -> bool:
        """Return whether a badge or elapsed UTC rollover warrants a probe."""

        current = self._as_utc(now)
        if self._completed_utc_day == self.game_day(current):
            return False
        if self._retry_not_before is not None and current < self._retry_not_before:
            return False
        if badge_visible:
            return True
        midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return current >= midnight + ROLLOVER_GRACE

    def mark_completed(
        self,
        result: str,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        """Persist a claimed or confirmed-not-ready result for the UTC day."""

        current = self._as_utc(now)
        self._completed_utc_day = self.game_day(current)
        self._retry_not_before = None
        payload = {
            "completed_utc_day": self._completed_utc_day,
            "completed_at_utc": current.isoformat(),
            "result": result,
        }
        try:
            self._write_atomic(payload)
        except OSError as exc:
            # Retain the in-memory completion so one process does not hammer the
            # Store even when persistence is temporarily unavailable.
            log(f"[DAILY_GEM] Failed to persist rollover state: {exc}", "ERROR")

    def mark_failed(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        """Back off a failed attempt without consuming the current UTC day."""

        current = self._as_utc(now)
        self._retry_not_before = current + FAILURE_RETRY_DELAY
        log(
            "[DAILY_GEM] Scheduled attempt failed; retry deferred for 5 minutes",
            "WARN",
        )

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log(f"[DAILY_GEM] Ignoring unreadable rollover state: {exc}", "WARN")
            return
        if not isinstance(payload, dict):
            log("[DAILY_GEM] Ignoring malformed rollover state", "WARN")
            return
        completed = payload.get("completed_utc_day")
        if isinstance(completed, str):
            self._completed_utc_day = completed

    def _write_atomic(self, payload: dict[str, str]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        staged: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{self.state_path.name}.",
                suffix=".tmp",
                dir=self.state_path.parent,
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                staged = Path(handle.name)
            staged.replace(self.state_path)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)


__all__ = [
    "DailyGemScheduler",
    "FAILURE_RETRY_DELAY",
    "ROLLOVER_GRACE",
]
