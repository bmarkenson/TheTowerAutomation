"""Passive, run-scoped observation of automatic survival-ability activations."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
import threading
from typing import Any, Mapping, Optional

from numpy.typing import NDArray

from core.matcher import MatchResult, get_match_result
from core.runtime_save import NormalizedRuntimeSave
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
_SAVE_TIMER_DETECTION_SOURCE = "player_save_refresh_timer"
_SAVE_TIMER_PRECISIONS = frozenset({"exact", "save_timer"})
_VISUAL_SAVE_MERGE_WAVE_TOLERANCE = 50


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


def _is_save_timer_event(event: Mapping[str, Any]) -> bool:
    return str(event.get("wave_precision") or "") in _SAVE_TIMER_PRECISIONS


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
        self._lock = threading.RLock()
        self._buttons = {
            name: _ButtonObservation() for name in _BUTTON_PATHS
        }
        self._intro_sprint = _IntroSprintObservation()
        self._second_wind = _SecondWindObservation()
        self._demon_mode_first_activation: Optional[dict[str, Any]] = None
        self._demon_mode_activations: list[dict[str, Any]] = []
        self._second_wind_activations: list[dict[str, Any]] = []
        self._nuke_activations: list[dict[str, Any]] = []
        self._bound_round_identity_fingerprint: Optional[str] = None
        self._last_save_revision: Optional[int] = None
        self._last_saved_wave: Optional[int] = None
        self._last_save_captured_at: Optional[datetime] = None
        self._last_save_activation_counts: dict[str, int] = {}
        self._pending_evidence_captures: list[dict[str, Any]] = []
        self._reported_match_errors: set[str] = set()

    def reset(self) -> None:
        """Begin observation for a newly identified battle."""

        with self._lock:
            self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self._buttons = {
            name: _ButtonObservation() for name in _BUTTON_PATHS
        }
        self._intro_sprint = _IntroSprintObservation()
        self._second_wind = _SecondWindObservation()
        self._demon_mode_first_activation = None
        self._demon_mode_activations = []
        self._second_wind_activations = []
        self._nuke_activations = []
        self._bound_round_identity_fingerprint = None
        self._last_save_revision = None
        self._last_saved_wave = None
        self._last_save_captured_at = None
        self._last_save_activation_counts = {}
        self._pending_evidence_captures = []
        self._reported_match_errors.clear()

    @property
    def intro_sprint_active(self) -> bool:
        """Return confirmed active Intro Sprint evidence for recovery sizing."""

        with self._lock:
            return self._intro_sprint.blocks_demon_mode_activation

    def bind_round_identity(self, identity_fingerprint: str) -> bool:
        """Bind save evidence to the battle already adopted by App."""

        fingerprint = str(identity_fingerprint or "").strip()
        if not fingerprint:
            return False
        with self._lock:
            if (
                self._bound_round_identity_fingerprint is not None
                and self._bound_round_identity_fingerprint != fingerprint
            ):
                return False
            self._bound_round_identity_fingerprint = fingerprint
            return True

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

        with self._lock:
            return self._observe_unlocked(
                frame,
                ui_state=ui_state,
                wave=wave,
                wave_confidence=wave_confidence,
                wave_observed_at=wave_observed_at,
                observed_at=observed_at,
            )

    def _observe_unlocked(
        self,
        frame: Frame,
        *,
        ui_state: str,
        wave: Optional[int],
        wave_confidence: float,
        wave_observed_at: Optional[datetime],
        observed_at: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
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

        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        events = tuple(
            event
            for ability_events in (
                self._second_wind_activations,
                self._demon_mode_activations,
                self._nuke_activations,
            )
            for event in ability_events
        )
        save_timer_observed = any(
            _is_save_timer_event(event) for event in events
        )
        visual_transition_observed = any(
            str(source) != _SAVE_TIMER_DETECTION_SOURCE
            for event in events
            for source in (
                event.get("evidence_sources")
                or (event.get("detection_source"),)
            )
            if source
        )
        if save_timer_observed and visual_transition_observed:
            source = "visual_transition_and_player_save_refresh_timer"
        elif save_timer_observed:
            source = _SAVE_TIMER_DETECTION_SOURCE
        else:
            source = _DETECTION_SOURCE
        return {
            "schema_version": 5,
            "source": source,
            "second_wind_activations": copy.deepcopy(
                self._second_wind_activations
            ),
            "demon_mode_first_activation": copy.deepcopy(
                self._demon_mode_first_activation
            ),
            "demon_mode_activations": copy.deepcopy(
                self._demon_mode_activations
            ),
            "nuke_activations": copy.deepcopy(self._nuke_activations),
        }

    def observe_save_checkpoint(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        expected_identity_fingerprint: str,
    ) -> list[dict[str, Any]]:
        """Upgrade captured refresh timers to save-derived wave evidence."""

        with self._lock:
            return self._observe_save_checkpoint_unlocked(
                runtime,
                expected_identity_fingerprint=expected_identity_fingerprint,
            )

    def _observe_save_checkpoint_unlocked(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        expected_identity_fingerprint: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(runtime, NormalizedRuntimeSave):
            return []
        identity = runtime.active_round_identity
        expected_identity = str(expected_identity_fingerprint or "").strip()
        if (
            runtime.round_active is not True
            or identity is None
            or not expected_identity
            or identity.fingerprint != expected_identity
            or runtime.save_revision is None
            or runtime.current_wave is None
        ):
            return []
        if self._bound_round_identity_fingerprint != expected_identity:
            return []
        captured_at = str(runtime.capture.get("captured_at") or "")
        if not captured_at:
            return []
        try:
            captured_time = datetime.fromisoformat(captured_at)
        except ValueError:
            return []
        if captured_time.tzinfo is None:
            return []
        save_revision = int(runtime.save_revision)
        saved_wave = int(runtime.current_wave)
        if (
            self._last_save_revision is not None
            and (
                save_revision < self._last_save_revision
                or (
                    self._last_saved_wave is not None
                    and saved_wave < self._last_saved_wave
                )
                or (
                    self._last_save_captured_at is not None
                    and captured_time <= self._last_save_captured_at
                )
            )
        ):
            return []

        activation_snapshot = runtime.survival_ability_activations
        if activation_snapshot is None or activation_snapshot.status not in {
            "observed",
            "partial",
        }:
            return []
        self._last_save_revision = save_revision
        self._last_saved_wave = saved_wave
        self._last_save_captured_at = captured_time

        upgraded: list[dict[str, Any]] = []
        for ability in activation_snapshot.abilities:
            prior_count = self._last_save_activation_counts.get(ability.ability)
            if (
                ability.status == "observed"
                and ability.activation_count is not None
                and ability.activation_count >= 0
            ):
                if (
                    prior_count is not None
                    and ability.activation_count < prior_count
                ):
                    continue
                self._last_save_activation_counts[ability.ability] = (
                    ability.activation_count
                )
            if (
                ability.status != "observed"
                or ability.activation_wave_status != "derived"
                or ability.activation_wave is None
                or ability.activation_count is None
                or ability.activation_count <= 0
            ):
                continue
            event = {
                "ability": ability.ability,
                "sequence": ability.activation_count,
                "activation_wave": ability.activation_wave,
                "activation_wave_min": ability.activation_wave,
                "activation_wave_max": ability.activation_wave,
                # Kept for old report consumers; wave_precision is authoritative.
                "approximate_wave": ability.activation_wave,
                "wave_precision": "save_timer",
                "wave_source": _SAVE_TIMER_DETECTION_SOURCE,
                "detection_source": _SAVE_TIMER_DETECTION_SOURCE,
                "detected_at": captured_at,
                "save_observed_at": captured_at,
                "saved_wave": saved_wave,
                "save_revision": save_revision,
                "waves_until_refresh_at_save": (
                    ability.waves_until_refresh
                ),
                "refresh_wave": ability.refresh_wave,
                "refresh_wave_min": ability.refresh_wave,
                "refresh_wave_max": ability.refresh_wave,
                "recharge_research_level": (
                    ability.recharge_research_level
                ),
                "recharge_waves": ability.recharge_waves,
                "evidence_sources": [_SAVE_TIMER_DETECTION_SOURCE],
                "save_evidence": {
                    "mapping_id": runtime.mapping_id,
                    "audit_id": activation_snapshot.audit_id,
                    "captured_at": captured_at,
                    "saved_wave": saved_wave,
                    "save_revision": save_revision,
                    "refresh_wave": ability.refresh_wave,
                    "waves_until_refresh": ability.waves_until_refresh,
                    "recharge_research_level": (
                        ability.recharge_research_level
                    ),
                    "recharge_waves": ability.recharge_waves,
                    "derivation": (
                        "saved_wave + waves_until_refresh - recharge_waves"
                    ),
                },
            }
            if ability.ability == "second_wind":
                event.update(
                    {
                        "estimated_rearm_wave": ability.refresh_wave,
                        "rearm_wave_min": ability.refresh_wave,
                        "rearm_wave_max": ability.refresh_wave,
                        "rearm_wave_offset": ability.recharge_waves,
                        "rearm_estimate_is_approximate": False,
                        "rearm_wave_precision": "save_timer",
                    }
                )
            upgraded.append(copy.deepcopy(self._merge_save_event(event)))
        return upgraded

    def drain_evidence_captures(self) -> list[dict[str, Any]]:
        """Return and clear confirmed-event frames awaiting durable storage."""

        with self._lock:
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

        with self._lock:
            return self._record_evidence_image_unlocked(
                ability,
                sequence,
                path,
            )

    def _record_evidence_image_unlocked(
        self,
        ability: str,
        sequence: int,
        path: str,
    ) -> bool:
        if ability == "second_wind":
            events = self._second_wind_activations
        elif ability == "nuke":
            events = self._nuke_activations
        elif ability == "demon_mode":
            events = self._demon_mode_activations
        else:
            return False
        for event in events:
            if int(event.get("sequence") or 0) == int(sequence):
                event["evidence_image"] = str(path)
                return True
        return False

    def _events_for_ability(self, ability: str) -> list[dict[str, Any]]:
        if ability == "second_wind":
            return self._second_wind_activations
        if ability == "demon_mode":
            return self._demon_mode_activations
        if ability == "nuke":
            return self._nuke_activations
        raise ValueError(f"unsupported survival ability {ability!r}")

    def _sync_demon_mode_first_activation(self) -> None:
        self._demon_mode_first_activation = next(
            (
                event
                for event in self._demon_mode_activations
                if int(event.get("sequence") or 0) == 1
            ),
            None,
        )

    def _merge_save_event(
        self,
        save_event: Mapping[str, Any],
    ) -> dict[str, Any]:
        ability = str(save_event.get("ability") or "")
        sequence = int(save_event.get("sequence") or 0)
        events = self._events_for_ability(ability)
        match_index = next(
            (
                index
                for index, existing in enumerate(events)
                if int(existing.get("sequence") or 0) == sequence
            ),
            None,
        )
        if (
            match_index is None
            and type(save_event.get("activation_wave")) is int
        ):
            save_wave = int(save_event["activation_wave"])
            candidates = [
                (
                    abs(int(existing["approximate_wave"]) - save_wave),
                    index,
                )
                for index, existing in enumerate(events)
                if not _is_save_timer_event(existing)
                and type(existing.get("approximate_wave")) is int
                and abs(int(existing["approximate_wave"]) - save_wave)
                <= _VISUAL_SAVE_MERGE_WAVE_TOLERANCE
            ]
            if candidates:
                _, match_index = min(candidates)
        if match_index is not None:
            index = match_index
            existing = events[index]
            existing_is_save_timer = _is_save_timer_event(existing)
            existing_activation_min = existing.get(
                "activation_wave_min",
                existing.get("activation_wave"),
            )
            existing_activation_max = existing.get(
                "activation_wave_max",
                existing.get("activation_wave"),
            )
            existing_refresh_min = existing.get(
                "refresh_wave_min",
                existing.get("refresh_wave"),
            )
            existing_refresh_max = existing.get(
                "refresh_wave_max",
                existing.get("refresh_wave"),
            )
            merged = copy.deepcopy(existing)
            visual_source = str(existing.get("detection_source") or "")
            visual_detected_at = existing.get("detected_at")
            visual_wave = existing.get(
                "visual_approximate_wave",
                existing.get("approximate_wave"),
            )
            merged.update(copy.deepcopy(dict(save_event)))
            if (
                existing_is_save_timer
                and type(existing_activation_min) is int
                and type(existing_activation_max) is int
                and type(save_event.get("activation_wave")) is int
            ):
                activation_candidates = (
                    int(existing_activation_min),
                    int(existing_activation_max),
                    int(save_event["activation_wave"]),
                )
                merged["activation_wave_min"] = min(activation_candidates)
                merged["activation_wave_max"] = max(activation_candidates)
                merged["activation_wave"] = min(activation_candidates)
                merged["approximate_wave"] = min(activation_candidates)
            if (
                existing_is_save_timer
                and type(existing_refresh_min) is int
                and type(existing_refresh_max) is int
                and type(save_event.get("refresh_wave")) is int
            ):
                refresh_candidates = (
                    int(existing_refresh_min),
                    int(existing_refresh_max),
                    int(save_event["refresh_wave"]),
                )
                merged["refresh_wave_min"] = min(refresh_candidates)
                merged["refresh_wave_max"] = max(refresh_candidates)
                if ability == "second_wind":
                    merged["estimated_rearm_wave"] = min(refresh_candidates)
                    merged["rearm_wave_min"] = min(refresh_candidates)
                    merged["rearm_wave_max"] = max(refresh_candidates)
            if visual_source and visual_source != _SAVE_TIMER_DETECTION_SOURCE:
                merged["detection_source"] = visual_source
                merged["detected_at"] = visual_detected_at
                merged["visual_approximate_wave"] = visual_wave
                for key in (
                    "confirmed_at",
                    "wave_confidence",
                    "wave_observed_at",
                    "presence_confidence",
                    "absence_confidence",
                    "active_icon_confidence",
                    "confirmation_frames",
                    "evidence_image",
                ):
                    if key in existing:
                        merged[key] = copy.deepcopy(existing[key])
                merged["evidence_sources"] = [
                    visual_source,
                    _SAVE_TIMER_DETECTION_SOURCE,
                ]
            events[index] = merged
            events.sort(key=lambda event: int(event.get("sequence") or 0))
            if ability == "demon_mode":
                self._sync_demon_mode_first_activation()
            return merged
        merged = copy.deepcopy(dict(save_event))
        events.append(merged)
        events.sort(key=lambda event: int(event.get("sequence") or 0))
        if ability == "demon_mode":
            self._sync_demon_mode_first_activation()
        return merged

    def _visual_sequence(self, ability: str, wave: Optional[int]) -> int:
        events = self._events_for_ability(ability)
        if wave is not None:
            for event in events:
                if (
                    _is_save_timer_event(event)
                    and event.get("detection_source")
                    == _SAVE_TIMER_DETECTION_SOURCE
                    and type(event.get("activation_wave")) is int
                    and abs(int(event["activation_wave"]) - int(wave))
                    <= _VISUAL_SAVE_MERGE_WAVE_TOLERANCE
                ):
                    return int(event.get("sequence") or 0)
        return max(
            (int(event.get("sequence") or 0) for event in events),
            default=0,
        ) + 1

    def _record_visual_event(
        self,
        visual_event: Mapping[str, Any],
    ) -> dict[str, Any]:
        ability = str(visual_event.get("ability") or "")
        sequence = int(visual_event.get("sequence") or 0)
        events = self._events_for_ability(ability)
        for index, existing in enumerate(events):
            if (
                int(existing.get("sequence") or 0) != sequence
                or not _is_save_timer_event(existing)
            ):
                continue
            merged = copy.deepcopy(existing)
            merged["visual_approximate_wave"] = visual_event.get(
                "approximate_wave"
            )
            for key, value in visual_event.items():
                if key not in {
                    "sequence",
                    "approximate_wave",
                    "activation_wave",
                    "wave_precision",
                    "wave_source",
                }:
                    merged[key] = copy.deepcopy(value)
            merged["evidence_sources"] = [
                str(visual_event.get("detection_source") or _DETECTION_SOURCE),
                _SAVE_TIMER_DETECTION_SOURCE,
            ]
            events[index] = merged
            if ability == "demon_mode":
                self._sync_demon_mode_first_activation()
            return merged
        event = copy.deepcopy(dict(visual_event))
        event.setdefault("wave_precision", "approximate")
        event.setdefault("wave_source", "visual_wave_ocr")
        event.setdefault(
            "evidence_sources",
            [str(event.get("detection_source") or _DETECTION_SOURCE)],
        )
        events.append(event)
        events.sort(key=lambda item: int(item.get("sequence") or 0))
        if ability == "demon_mode":
            self._sync_demon_mode_first_activation()
        return event

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
            "sequence": self._visual_sequence(name, wave),
            "approximate_wave": int(wave) if wave is not None else None,
            "wave_precision": "approximate",
            "wave_source": "visual_wave_ocr",
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
        recorded_event = self._record_visual_event(event)
        self._queue_evidence_capture(
            recorded_event,
            state.absence_started_frame,
        )
        state.absence_started_frame = None
        return recorded_event

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
            "sequence": self._visual_sequence(
                "second_wind",
                approximate_wave,
            ),
            "approximate_wave": approximate_wave,
            "wave_precision": "approximate",
            "wave_source": "visual_wave_ocr",
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
        recorded_event = self._record_visual_event(event)
        self._queue_evidence_capture(
            recorded_event,
            state.absence_started_frame,
        )
        state.clear_pending_activation()
        return recorded_event

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
