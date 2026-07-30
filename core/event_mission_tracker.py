"""Persistent age/progress tracking for incomplete Event Missions."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Optional

from core.event_missions import EventMissionInventory, EventMissionObservation
from utils.logger import log


WARNING_AFTER_SECONDS = 3 * 24 * 60 * 60
STALLED_AFTER_SECONDS = 2 * 24 * 60 * 60
WARNING_REPEAT_SECONDS = 6 * 60 * 60
WARNING_EVIDENCE_MAX_AGE_SECONDS = 60 * 60
_STATE_VERSION = 2


@dataclass(frozen=True)
class EventMissionWarning:
    """One persisted incomplete mission whose reminder is due."""

    name: str
    progress: Optional[str]
    incomplete_seconds: float
    stalled_seconds: float


class EventMissionTracker:
    """Persist Event Mission observations and bound repeated reminders."""

    def __init__(self, state_path: str | Path) -> None:
        self._path = Path(state_path)
        self._state = self._load()

    def record_inventory(
        self,
        inventory: EventMissionInventory,
        *,
        now: float | None = None,
    ) -> bool:
        """Record one authoritative inventory; return whether it was accepted."""

        if not inventory.complete or not inventory.event_name:
            return False
        current = _wall_time(now)
        event_key = _key(inventory.event_name)
        previous_event_key = _key(str(self._state.get("event_name") or ""))
        previous_end = _finite_number(self._state.get("event_ends_at"))
        event_changed = previous_event_key and not _similar(
            previous_event_key,
            event_key,
            0.90,
        )
        event_expired = previous_end is not None and current >= previous_end
        if event_changed or event_expired:
            self._state = self._empty_state()

        previous_missions = self._missions()
        # Preserve rows that an otherwise successful OCR pass happened to miss.
        # A mission is resolved only through explicit completed/claimable row
        # evidence or an event boundary, but an absent row loses warning
        # authority until a later complete inventory observes its progress.
        next_missions = {
            key: {
                **mission,
                "observed_in_latest_inventory": False,
            }
            for key, mission in previous_missions.items()
        }
        for observation in inventory.missions:
            key = _matching_key(_key(observation.name), previous_missions)
            if not observation.incomplete:
                if key is not None:
                    next_missions.pop(key, None)
                continue
            previous = previous_missions.get(key) if key is not None else None
            storage_key = key or _key(observation.name)
            next_missions[storage_key] = _updated_mission(
                previous,
                observation,
                current,
            )

        self._state["version"] = _STATE_VERSION
        self._state["event_name"] = inventory.event_name
        self._state["last_inventory_at"] = current
        if inventory.remaining_seconds is not None:
            # The UI truncates its remaining time to whole hours. Preserve an
            # extra hour so warnings do not expire before the actual boundary.
            self._state["event_ends_at"] = current + inventory.remaining_seconds + 3600
        self._state["missions"] = next_missions
        self._save()
        return True

    def due_warnings(self, *, now: float | None = None) -> tuple[EventMissionWarning, ...]:
        """Return reminders supported by the latest fresh inventory."""

        current = _wall_time(now)
        event_ends_at = _finite_number(self._state.get("event_ends_at"))
        if event_ends_at is not None and current >= event_ends_at:
            return ()
        last_inventory = _finite_number(self._state.get("last_inventory_at"))
        if (
            last_inventory is None
            or current < last_inventory
            or current - last_inventory > WARNING_EVIDENCE_MAX_AGE_SECONDS
        ):
            return ()

        warnings = []
        changed = False
        for mission in self._missions().values():
            first_seen = _finite_number(mission.get("first_seen_at"))
            last_progress = _finite_number(mission.get("last_progress_at"))
            last_seen = _finite_number(mission.get("last_seen_at"))
            last_warned = _finite_number(mission.get("last_warned_at"))
            if (
                first_seen is None
                or last_progress is None
                or last_seen is None
                or last_seen != last_inventory
                or not mission.get("observed_in_latest_inventory")
            ):
                continue
            incomplete_for = max(0.0, last_seen - first_seen)
            stalled_for = max(0.0, last_seen - last_progress)
            if incomplete_for < WARNING_AFTER_SECONDS or stalled_for < STALLED_AFTER_SECONDS:
                continue
            if last_warned is not None and current - last_warned < WARNING_REPEAT_SECONDS:
                continue
            warnings.append(
                EventMissionWarning(
                    name=str(mission.get("name") or "Unknown Event Mission"),
                    progress=str(mission["progress"]) if mission.get("progress") else None,
                    incomplete_seconds=incomplete_for,
                    stalled_seconds=stalled_for,
                )
            )
            mission["last_warned_at"] = current
            changed = True

        if changed:
            self._save()
        return tuple(warnings)

    def _missions(self) -> dict[str, dict[str, Any]]:
        missions = self._state.setdefault("missions", {})
        return missions if isinstance(missions, dict) else {}

    def _load(self) -> dict[str, Any]:
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("version") == _STATE_VERSION:
                return data
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            log(f"[EVENT_MISSIONS] Could not read tracker state: {exc}", "WARN")
        return self._empty_state()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self._state, handle, indent=2, sort_keys=True)
        os.replace(temporary, self._path)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"version": _STATE_VERSION, "missions": {}}


def format_warning(warning: EventMissionWarning) -> str:
    """Render a stable one-line reminder for stdout and actions.log."""

    progress = f" — {warning.progress}" if warning.progress else ""
    return (
        "[EVENT_MISSION_WARNING] "
        f"Observed incomplete across {_duration(warning.incomplete_seconds)}; "
        "progress unchanged across observations spanning "
        f"{_duration(warning.stalled_seconds)}: "
        f"{warning.name}{progress}"
    )


def _updated_mission(
    previous: Optional[dict[str, Any]],
    observation: EventMissionObservation,
    now: float,
) -> dict[str, Any]:
    old = previous or {}
    old_progress = str(old.get("progress")) if old.get("progress") else None
    new_progress = observation.progress
    old_target = _progress_target(old_progress)
    new_target = _progress_target(new_progress)
    tier_changed = (
        old_target is not None
        and new_target is not None
        and old_target != new_target
    )
    progress_changed = new_progress is not None and new_progress != old_progress
    first_seen = _finite_number(old.get("first_seen_at"))
    last_progress = _finite_number(old.get("last_progress_at"))
    return {
        "name": observation.name,
        "progress": new_progress or old_progress,
        "first_seen_at": (
            now
            if tier_changed or first_seen is None
            else first_seen
        ),
        "last_seen_at": now,
        "last_progress_at": (
            now
            if not old or tier_changed or progress_changed
            else (last_progress if last_progress is not None else now)
        ),
        "last_warned_at": (
            None
            if tier_changed
            else _finite_number(old.get("last_warned_at"))
        ),
        "observed_in_latest_inventory": new_progress is not None,
    }


def _matching_key(
    candidate: str,
    missions: dict[str, dict[str, Any]],
) -> Optional[str]:
    if candidate in missions:
        return candidate
    ranked = sorted(
        ((SequenceMatcher(None, candidate, key).ratio(), key) for key in missions),
        reverse=True,
    )
    return ranked[0][1] if ranked and ranked[0][0] >= 0.92 else None


def _similar(first: str, second: str, threshold: float) -> bool:
    return SequenceMatcher(None, first, second).ratio() >= threshold


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _progress_target(progress: Optional[str]) -> Optional[str]:
    if not progress or "/" not in progress:
        return None
    target = progress.rsplit("/", 1)[1]
    normalized = re.sub(r"[\s,]+", "", target).upper()
    return normalized or None


def _wall_time(value: float | None) -> float:
    current = time.time() if value is None else float(value)
    if not math.isfinite(current):
        raise ValueError("event mission wall time must be finite")
    return current


def _finite_number(value: object) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _duration(seconds: float) -> str:
    total_hours = max(0, int(seconds // 3600))
    days, hours = divmod(total_hours, 24)
    return f"{days}d {hours}h" if days else f"{hours}h"


__all__ = [
    "EventMissionTracker",
    "EventMissionWarning",
    "STALLED_AFTER_SECONDS",
    "WARNING_AFTER_SECONDS",
    "WARNING_EVIDENCE_MAX_AGE_SECONDS",
    "WARNING_REPEAT_SECONDS",
    "format_warning",
]
