"""Passive, run-scoped observation of automatic survival-ability activations."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

from numpy.typing import NDArray

from core.matcher import MatchResult, get_match_result
from utils.logger import log


Frame = NDArray[Any]

_BUTTON_PATHS = {
    "demon_mode": "floating_buttons.demon_mode",
    "nuke": "floating_buttons.nuke",
}
_PRESENCE_THRESHOLDS = {
    # The disabled Intro Sprint Demon Mode icon scored 0.856-0.875 against
    # the existing enabled template in retained live frames. Its next-best
    # candidate after removing the button scored 0.399.
    "demon_mode": 0.8,
    "nuke": 0.9,
}
_DETECTION_SOURCE = "button_disappearance"


@dataclass
class _ButtonObservation:
    visible_streak: int = 0
    absent_streak: int = 0
    armed: bool = False
    last_presence_confidence: float = 0.0
    last_absence_confidence: float = 0.0

    def reset_streaks(self) -> None:
        self.visible_streak = 0
        self.absent_streak = 0


class BattleActivationTracker:
    """Infer activations from confirmed floating-button disappearance.

    Demon Mode and Nuke remain visible while disabled during Intro Sprint as
    well as when enabled. With automatic activation configured, disappearance
    is the useful transition. Requiring consecutive present and absent frames
    avoids depending on a brief animation.
    """

    def __init__(
        self,
        *,
        presence_confirmation_frames: int = 2,
        absence_confirmation_frames: int = 2,
    ) -> None:
        if presence_confirmation_frames < 1 or absence_confirmation_frames < 1:
            raise ValueError("confirmation frame counts must be positive")
        self._presence_confirmation_frames = int(
            presence_confirmation_frames
        )
        self._absence_confirmation_frames = int(absence_confirmation_frames)
        self._buttons = {
            name: _ButtonObservation() for name in _BUTTON_PATHS
        }
        self._demon_mode_first_activation: Optional[dict[str, Any]] = None
        self._nuke_activations: list[dict[str, Any]] = []
        self._reported_match_errors: set[str] = set()

    def reset(self) -> None:
        """Begin observation for a newly identified battle."""

        self._buttons = {
            name: _ButtonObservation() for name in _BUTTON_PATHS
        }
        self._demon_mode_first_activation = None
        self._nuke_activations = []
        self._reported_match_errors.clear()

    def observe(
        self,
        frame: Frame,
        *,
        ui_state: str,
        wave: Optional[int],
        wave_confidence: float,
        wave_observed_at: Optional[datetime],
        observed_at: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """Observe one frame and return newly confirmed activation events."""

        if str(ui_state or "").upper() != "RUNNING":
            for state in self._buttons.values():
                state.reset_streaks()
            return []

        matches: dict[str, MatchResult] = {}
        for name, dot_path in _BUTTON_PATHS.items():
            if name == "demon_mode" and self._demon_mode_first_activation:
                continue
            try:
                matches[name] = get_match_result(dot_path, screenshot=frame)
            except Exception as exc:
                self._buttons[name].reset_streaks()
                if name not in self._reported_match_errors:
                    log(
                        f"[BATTLE_EVENT] Could not observe {name} button: {exc}",
                        "WARN",
                    )
                    self._reported_match_errors.add(name)

        when = observed_at or datetime.now().astimezone()
        events: list[dict[str, Any]] = []
        for name, match in matches.items():
            event = self._observe_button(
                name,
                match,
                wave=wave,
                wave_confidence=wave_confidence,
                wave_observed_at=wave_observed_at,
                observed_at=when,
            )
            if event is not None:
                events.append(copy.deepcopy(event))
        return events

    def snapshot(self) -> dict[str, Any]:
        """Return serializable completed-run evidence."""

        return {
            "schema_version": 2,
            "source": _DETECTION_SOURCE,
            "demon_mode_first_activation": copy.deepcopy(
                self._demon_mode_first_activation
            ),
            "nuke_activations": copy.deepcopy(self._nuke_activations),
        }

    def _observe_button(
        self,
        name: str,
        match: MatchResult,
        *,
        wave: Optional[int],
        wave_confidence: float,
        wave_observed_at: Optional[datetime],
        observed_at: datetime,
    ) -> Optional[dict[str, Any]]:
        state = self._buttons[name]
        if match.failure_reason is not None:
            state.reset_streaks()
            return None
        present = match.confidence >= _PRESENCE_THRESHOLDS[name]
        if present:
            state.visible_streak += 1
            state.absent_streak = 0
            state.last_presence_confidence = float(match.confidence)
            if state.visible_streak >= self._presence_confirmation_frames:
                state.armed = True
            return None

        state.visible_streak = 0
        if not state.armed:
            return None
        state.absent_streak += 1
        state.last_absence_confidence = float(match.confidence)
        if state.absent_streak < self._absence_confirmation_frames:
            return None

        state.armed = False
        state.absent_streak = 0
        event = {
            "ability": name,
            "sequence": (
                1 if name == "demon_mode" else len(self._nuke_activations) + 1
            ),
            "approximate_wave": int(wave) if wave is not None else None,
            "wave_confidence": round(float(wave_confidence), 1),
            "wave_observed_at": (
                wave_observed_at.isoformat(timespec="seconds")
                if wave_observed_at is not None
                else None
            ),
            "detected_at": observed_at.isoformat(timespec="seconds"),
            "detection_source": _DETECTION_SOURCE,
            "presence_confidence": round(
                state.last_presence_confidence,
                3,
            ),
            "absence_confidence": round(
                state.last_absence_confidence,
                3,
            ),
            "confirmation_frames": self._absence_confirmation_frames,
        }
        if name == "demon_mode":
            self._demon_mode_first_activation = event
        else:
            self._nuke_activations.append(event)
        return event


__all__ = ["BattleActivationTracker"]
