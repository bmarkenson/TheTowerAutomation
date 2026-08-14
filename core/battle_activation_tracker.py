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
_INTRO_SPRINT_ACTIVE_PATH = "indicators.intro_sprint_active"
_SECOND_WIND_WING_PATHS = (
    "indicators.second_wind_left_wing",
    "indicators.second_wind_right_wing",
)
_SECOND_WIND_ACTIVE_PATH = "indicators.second_wind_active"
_SECOND_WIND_REARM_WAVES = 400
_INTRO_SPRINT_END_CONFIRMATION_FRAMES = 5
_PRESENCE_THRESHOLDS = {
    # The disabled Intro Sprint Demon Mode icon scored 0.856-0.875 against
    # the existing enabled template in retained live frames. Its next-best
    # candidate after removing the button scored 0.399.
    "demon_mode": 0.8,
    "nuke": 0.9,
}
_DETECTION_SOURCE = "visual_transition_detection"
_BUTTON_DETECTION_SOURCE = "button_disappearance"
_SECOND_WIND_DETECTION_SOURCE = "active_status_icon"


@dataclass
class _ButtonObservation:
    visible_streak: int = 0
    absent_streak: int = 0
    armed: bool = False
    last_presence_confidence: float = 0.0
    last_absence_confidence: float = 0.0
    absence_started_frame: Optional[Frame] = None

    def reset_streaks(self) -> None:
        self.visible_streak = 0
        self.absent_streak = 0
        self.absence_started_frame = None


@dataclass
class _IntroSprintObservation:
    observed_active: bool = False
    ended: bool = False
    absent_streak: int = 0

    @property
    def blocks_demon_mode_activation(self) -> bool:
        return self.observed_active and not self.ended

    def reset_streak(self) -> None:
        self.absent_streak = 0

    def observe(self, match: MatchResult) -> None:
        if match.failure_reason is not None:
            self.reset_streak()
            return
        if match.matched:
            self.observed_active = True
            self.ended = False
            self.reset_streak()
            return
        if not self.observed_active or self.ended:
            return
        self.absent_streak += 1
        if self.absent_streak >= _INTRO_SPRINT_END_CONFIRMATION_FRAMES:
            self.ended = True


@dataclass
class _SecondWindObservation(_ButtonObservation):
    active_streak: int = 0
    active_started_wave: Optional[int] = None
    active_started_wave_confidence: float = -1.0
    active_started_wave_observed_at: Optional[datetime] = None
    active_started_at: Optional[datetime] = None
    last_active_confidence: float = 0.0

    def clear_pending_activation(self) -> None:
        self.last_absence_confidence = 0.0
        self.active_streak = 0
        self.last_active_confidence = 0.0
        self.active_started_wave = None
        self.active_started_wave_confidence = -1.0
        self.active_started_wave_observed_at = None
        self.active_started_at = None
        self.absence_started_frame = None

    def reset_streaks(self) -> None:
        self.visible_streak = 0
        self.clear_pending_activation()


class BattleActivationTracker:
    """Infer survival activations from confirmed visual transitions.

    Demon Mode and Nuke remain visible while disabled during Intro Sprint as
    well as when enabled. With automatic activation configured, disappearance
    is the useful transition, but the top-left Intro Sprint status vetoes Demon
    Mode disappearance until its own sustained exit is confirmed. The small
    wings beside the tower establish that Second Wind is available; its fixed
    active-status glyph above Nuke is the authoritative activation signal.
    This avoids treating transiently obscured tower wings as activations.
    """

    def __init__(
        self,
        *,
        presence_confirmation_frames: int = 2,
        absence_confirmation_frames: int = 2,
        second_wind_active_confirmation_frames: int = 1,
    ) -> None:
        if (
            presence_confirmation_frames < 1
            or absence_confirmation_frames < 1
            or second_wind_active_confirmation_frames < 1
        ):
            raise ValueError("confirmation frame counts must be positive")
        self._presence_confirmation_frames = int(
            presence_confirmation_frames
        )
        self._absence_confirmation_frames = int(absence_confirmation_frames)
        self._second_wind_active_confirmation_frames = int(
            second_wind_active_confirmation_frames
        )
        self._buttons = {
            name: _ButtonObservation() for name in _BUTTON_PATHS
        }
        self._intro_sprint = _IntroSprintObservation()
        self._second_wind = _SecondWindObservation()
        self._demon_mode_first_activation: Optional[dict[str, Any]] = None
        self._second_wind_activations: list[dict[str, Any]] = []
        self._nuke_activations: list[dict[str, Any]] = []
        self._pending_evidence_captures: list[dict[str, Any]] = []
        self._reported_match_errors: set[str] = set()

    def reset(self) -> None:
        """Begin observation for a newly identified battle."""

        self._buttons = {
            name: _ButtonObservation() for name in _BUTTON_PATHS
        }
        self._intro_sprint = _IntroSprintObservation()
        self._second_wind = _SecondWindObservation()
        self._demon_mode_first_activation = None
        self._second_wind_activations = []
        self._nuke_activations = []
        self._pending_evidence_captures = []
        self._reported_match_errors.clear()

    @property
    def intro_sprint_active(self) -> bool:
        """Return confirmed active Intro Sprint evidence for recovery sizing."""

        return self._intro_sprint.blocks_demon_mode_activation

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
            self._intro_sprint.reset_streak()
            self._second_wind.reset_streaks()
            return []

        try:
            intro_sprint_match = get_match_result(
                _INTRO_SPRINT_ACTIVE_PATH,
                screenshot=frame,
            )
            self._intro_sprint.observe(intro_sprint_match)
        except Exception as exc:
            self._intro_sprint.reset_streak()
            if "intro_sprint" not in self._reported_match_errors:
                log(
                    "[BATTLE_EVENT] Could not observe Intro Sprint status: "
                    f"{exc}",
                    "WARN",
                )
                self._reported_match_errors.add("intro_sprint")

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
        wing_matches: list[MatchResult] = []
        active_match: Optional[MatchResult] = None
        try:
            wing_matches = [
                get_match_result(dot_path, screenshot=frame)
                for dot_path in _SECOND_WIND_WING_PATHS
            ]
            active_match = get_match_result(
                _SECOND_WIND_ACTIVE_PATH,
                screenshot=frame,
            )
        except Exception as exc:
            self._second_wind.reset_streaks()
            if "second_wind" not in self._reported_match_errors:
                log(
                    f"[BATTLE_EVENT] Could not observe Second Wind wings: {exc}",
                    "WARN",
                )
                self._reported_match_errors.add("second_wind")
        if (
            len(wing_matches) == len(_SECOND_WIND_WING_PATHS)
            and active_match is not None
        ):
            second_wind_event = self._observe_second_wind(
                wing_matches,
                active_match,
                frame=frame,
                wave=wave,
                wave_confidence=wave_confidence,
                wave_observed_at=wave_observed_at,
                observed_at=when,
            )
            if second_wind_event is not None:
                events.append(copy.deepcopy(second_wind_event))
        for name, match in matches.items():
            event = self._observe_button(
                name,
                match,
                frame=frame,
                wave=wave,
                wave_confidence=wave_confidence,
                wave_observed_at=wave_observed_at,
                observed_at=when,
                disappearance_blocked=(
                    name == "demon_mode"
                    and self._intro_sprint.blocks_demon_mode_activation
                ),
            )
            if event is not None:
                events.append(copy.deepcopy(event))
        return events

    def snapshot(self) -> dict[str, Any]:
        """Return serializable completed-run evidence."""

        return {
            "schema_version": 4,
            "source": _DETECTION_SOURCE,
            "second_wind_activations": copy.deepcopy(
                self._second_wind_activations
            ),
            "demon_mode_first_activation": copy.deepcopy(
                self._demon_mode_first_activation
            ),
            "nuke_activations": copy.deepcopy(self._nuke_activations),
        }

    def drain_evidence_captures(self) -> list[dict[str, Any]]:
        """Return and clear confirmed-event frames awaiting durable storage."""

        captures = self._pending_evidence_captures
        self._pending_evidence_captures = []
        return captures

    def record_evidence_image(
        self,
        ability: str,
        sequence: int,
        path: str,
    ) -> bool:
        """Attach a saved evidence path to the matching completed-run event."""

        if ability == "second_wind":
            events = self._second_wind_activations
        elif ability == "nuke":
            events = self._nuke_activations
        elif ability == "demon_mode":
            events = (
                [self._demon_mode_first_activation]
                if self._demon_mode_first_activation is not None
                else []
            )
        else:
            return False
        for event in events:
            if int(event.get("sequence") or 0) == int(sequence):
                event["evidence_image"] = str(path)
                return True
        return False

    def _observe_button(
        self,
        name: str,
        match: MatchResult,
        *,
        frame: Frame,
        wave: Optional[int],
        wave_confidence: float,
        wave_observed_at: Optional[datetime],
        observed_at: datetime,
        disappearance_blocked: bool = False,
    ) -> Optional[dict[str, Any]]:
        state = self._buttons[name]
        if match.failure_reason is not None:
            state.reset_streaks()
            return None
        present = match.confidence >= _PRESENCE_THRESHOLDS[name]
        if present:
            state.visible_streak += 1
            state.absent_streak = 0
            state.absence_started_frame = None
            state.last_presence_confidence = float(match.confidence)
            if state.visible_streak >= self._presence_confirmation_frames:
                state.armed = True
            return None

        state.visible_streak = 0
        if disappearance_blocked:
            state.absent_streak = 0
            state.absence_started_frame = None
            return None
        if not state.armed:
            return None
        if state.absent_streak == 0:
            state.absence_started_frame = frame.copy()
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
            "detection_source": _BUTTON_DETECTION_SOURCE,
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
        self._queue_evidence_capture(event, state.absence_started_frame)
        state.absence_started_frame = None
        return event

    def _observe_second_wind(
        self,
        matches: list[MatchResult],
        active_match: MatchResult,
        *,
        frame: Frame,
        wave: Optional[int],
        wave_confidence: float,
        wave_observed_at: Optional[datetime],
        observed_at: datetime,
    ) -> Optional[dict[str, Any]]:
        state = self._second_wind
        if (
            any(match.failure_reason is not None for match in matches)
            or active_match.failure_reason is not None
        ):
            state.reset_streaks()
            return None

        matched = tuple(match.matched for match in matches)
        if all(matched):
            state.visible_streak += 1
            state.last_presence_confidence = min(
                float(match.confidence) for match in matches
            )
            state.clear_pending_activation()
            if state.visible_streak >= self._presence_confirmation_frames:
                state.armed = True
            return None

        state.visible_streak = 0
        if state.armed and any(matched):
            # Both wings establish that Second Wind is equipped and available,
            # but either surviving wing disproves activation. Battle effects
            # frequently obscure one side without hiding the other.
            state.clear_pending_activation()
            return None
        if not state.armed:
            state.clear_pending_activation()
            return None

        state.last_absence_confidence = max(
            float(match.confidence) for match in matches
        )
        if not active_match.matched:
            state.clear_pending_activation()
            return None

        if state.active_streak == 0:
            state.active_started_wave = (
                int(wave) if wave is not None else None
            )
            state.active_started_wave_confidence = float(wave_confidence)
            state.active_started_wave_observed_at = wave_observed_at
            state.active_started_at = observed_at
            state.absence_started_frame = frame.copy()
        state.active_streak += 1
        state.last_active_confidence = max(
            state.last_active_confidence,
            float(active_match.confidence),
        )
        if (
            state.active_streak
            < self._second_wind_active_confirmation_frames
        ):
            return None

        approximate_wave = state.active_started_wave
        event = {
            "ability": "second_wind",
            "sequence": len(self._second_wind_activations) + 1,
            "approximate_wave": approximate_wave,
            "estimated_rearm_wave": (
                approximate_wave + _SECOND_WIND_REARM_WAVES
                if approximate_wave is not None
                else None
            ),
            "rearm_wave_offset": _SECOND_WIND_REARM_WAVES,
            "rearm_estimate_is_approximate": True,
            "wave_confidence": round(
                state.active_started_wave_confidence,
                1,
            ),
            "wave_observed_at": (
                state.active_started_wave_observed_at.isoformat(
                    timespec="seconds"
                )
                if state.active_started_wave_observed_at is not None
                else None
            ),
            "detected_at": (
                state.active_started_at.isoformat(timespec="seconds")
                if state.active_started_at is not None
                else observed_at.isoformat(timespec="seconds")
            ),
            "confirmed_at": observed_at.isoformat(timespec="seconds"),
            "detection_source": _SECOND_WIND_DETECTION_SOURCE,
            "presence_confidence": round(
                state.last_presence_confidence,
                3,
            ),
            "absence_confidence": round(
                state.last_absence_confidence,
                3,
            ),
            "active_icon_confidence": round(
                state.last_active_confidence,
                3,
            ),
            "confirmation_frames": (
                self._second_wind_active_confirmation_frames
            ),
        }
        state.armed = False
        self._queue_evidence_capture(event, state.absence_started_frame)
        state.clear_pending_activation()
        self._second_wind_activations.append(event)
        return event

    def _queue_evidence_capture(
        self,
        event: Mapping[str, Any],
        frame: Optional[Frame],
    ) -> None:
        if frame is None:
            return
        self._pending_evidence_captures.append(
            {
                "ability": str(event.get("ability") or "unknown"),
                "sequence": int(event.get("sequence") or 0),
                "detected_at": str(event.get("detected_at") or ""),
                "frame": frame,
            }
        )


__all__ = ["BattleActivationTracker"]
