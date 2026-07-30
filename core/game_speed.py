"""Battle-only measurement and enforcement for the game-speed control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import time
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.control_directives import VALID_GAME_SPEED_MODES
from core.input import TapVerification, safe_tap
from core.ss_capture import (
    capture_adb_screenshot,
    is_complete_screenshot,
)
from core.state_detector import detect_state_and_overlays
from utils.logger import log, log_action_intent, log_result
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
CaptureFn = Callable[[], Optional[Frame]]
DetectorFn = Callable[[Frame], Mapping[str, Any]]
SleepFn = Callable[[float], None]
TapFn = Callable[..., bool]

GAME_SPEED_REGION = (750, 895, 125, 85)
GAME_SPEED_MINUS_REGION = (675, 895, 70, 80)
GAME_SPEED_MINUS_POINT = (710, 935)
GAME_SPEED_PLUS_REGION = (875, 895, 70, 80)
GAME_SPEED_PLUS_POINT = (910, 935)
GAME_SPEED_CHECK_INTERVAL_S = 30.0
GAME_SPEED_RETRY_INTERVAL_S = 5.0
GAME_SPEED_WARNING_AFTER_FAILURES = 3
GAME_SPEED_WARNING_REPEAT_S = 5 * 60.0
REDUCED_MODE_REMINDER_INTERVAL_S = 15 * 60.0
MAX_GAME_SPEED_TAPS = 24
NORMAL_MAX_GAME_SPEED = 5.0
REDUCED_GAME_SPEED = 4.0
_TARGET_TOLERANCE = 0.05

_SPEED_PATTERN = re.compile(r"X(\d{1,2}\.\d)")


@dataclass(frozen=True)
class GameSpeedReading:
    """One authoritative reading of the visible battle speed control."""

    valid: bool
    value: Optional[float]
    raw_text: str
    confidence: float
    plus_visible: bool
    reason: str
    minus_visible: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "value": self.value,
            "raw_text": self.raw_text,
            "confidence": self.confidence,
            "plus_visible": self.plus_visible,
            "minus_visible": self.minus_visible,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GameSpeedResult:
    """Outcome from restoring the battle speed to the normal maximum."""

    success: bool
    initial: Optional[float]
    final: Optional[float]
    taps_sent: int
    increases: int
    reason: str
    decreases: int = 0
    mode: str = "AUTO"
    target: float = NORMAL_MAX_GAME_SPEED

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "initial": self.initial,
            "final": self.final,
            "taps_sent": self.taps_sent,
            "increases": self.increases,
            "decreases": self.decreases,
            "mode": self.mode,
            "target": self.target,
            "reason": self.reason,
        }


def _crop(frame: Frame, region: tuple[int, int, int, int]) -> Optional[Frame]:
    x, y, width, height = region
    crop = frame[y : y + height, x : x + width]
    return crop if crop.size else None


def _plus_control_visible(frame: Frame) -> bool:
    """Recognize the fixed white plus glyph without trusting coordinates alone."""

    crop = _crop(frame, GAME_SPEED_PLUS_REGION)
    if crop is None or crop.shape[:2] != (
        GAME_SPEED_PLUS_REGION[3],
        GAME_SPEED_PLUS_REGION[2],
    ):
        return False
    bright = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) > 190
    horizontal = float(np.mean(bright[35:48, 15:58]))
    vertical = float(np.mean(bright[20:65, 29:42]))
    corners = np.concatenate(
        (
            bright[20:32, 15:27].ravel(),
            bright[20:32, 47:59].ravel(),
            bright[52:64, 15:27].ravel(),
            bright[52:64, 47:59].ravel(),
        )
    )
    return (
        horizontal >= 0.30
        and vertical >= 0.30
        and float(np.mean(corners)) <= 0.12
    )


def _minus_control_visible(frame: Frame) -> bool:
    """Recognize the fixed white minus glyph without trusting coordinates alone."""

    crop = _crop(frame, GAME_SPEED_MINUS_REGION)
    if crop is None or crop.shape[:2] != (
        GAME_SPEED_MINUS_REGION[3],
        GAME_SPEED_MINUS_REGION[2],
    ):
        return False
    bright = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) > 190
    horizontal = float(np.mean(bright[35:48, 15:58]))
    above = float(np.mean(bright[18:30, 15:58]))
    below = float(np.mean(bright[53:65, 15:58]))
    return horizontal >= 0.30 and above <= 0.12 and below <= 0.12


def read_game_speed_control(
    screenshot: Optional[Frame],
    *,
    text_fn: Callable[[Frame], tuple[str, float]] = lambda crop: (
        ocr_text_and_conf(
            crop,
            psm=7,
            config_extra="-c tessedit_char_whitelist=xX0123456789.",
        )
    ),
) -> GameSpeedReading:
    """Read the localized control; callers separately establish battle state."""

    if not is_complete_screenshot(screenshot):
        return GameSpeedReading(
            False, None, "", -1.0, False, "incomplete_screenshot"
        )
    assert screenshot is not None
    crop = _crop(screenshot, GAME_SPEED_REGION)
    if crop is None:
        return GameSpeedReading(False, None, "", -1.0, False, "missing_region")
    white_text = cv2.inRange(crop, (170, 170, 170), (255, 255, 255))
    raw_text, confidence = text_fn(white_text)
    normalized = re.sub(r"\s+", "", str(raw_text or "").upper())
    match = _SPEED_PATTERN.search(normalized)
    plus_visible = _plus_control_visible(screenshot)
    minus_visible = _minus_control_visible(screenshot)
    if match is None:
        return GameSpeedReading(
            False,
            None,
            str(raw_text or ""),
            float(confidence),
            plus_visible,
            "speed_ocr_failed",
            minus_visible,
        )
    value = float(match.group(1))
    if not 0.0 < value <= 20.0:
        return GameSpeedReading(
            False,
            value,
            str(raw_text or ""),
            float(confidence),
            plus_visible,
            "speed_out_of_range",
            minus_visible,
        )
    if not plus_visible:
        return GameSpeedReading(
            False,
            value,
            str(raw_text or ""),
            float(confidence),
            False,
            "plus_control_not_visible",
            minus_visible,
        )
    return GameSpeedReading(
        True,
        value,
        str(raw_text or ""),
        float(confidence),
        True,
        "visible",
        minus_visible,
    )


def measure_game_speed(
    screenshot: Optional[Frame],
    *,
    detector: DetectorFn = detect_state_and_overlays,
    text_fn: Callable[[Frame], tuple[str, float]] = lambda crop: (
        ocr_text_and_conf(
            crop,
            psm=7,
            config_extra="-c tessedit_char_whitelist=xX0123456789.",
        )
    ),
) -> GameSpeedReading:
    """Return a valid reading only from an authoritatively active battle."""

    if not is_complete_screenshot(screenshot):
        return GameSpeedReading(
            False, None, "", -1.0, False, "incomplete_screenshot"
        )
    assert screenshot is not None
    if detector(screenshot).get("state") != "RUNNING":
        return GameSpeedReading(False, None, "", -1.0, False, "not_running")
    return read_game_speed_control(screenshot, text_fn=text_fn)


def _wait_for_settled_speed(
    previous: float,
    *,
    capture_fn: CaptureFn,
    sleep_fn: SleepFn,
    attempts: int = 4,
) -> tuple[Optional[Frame], GameSpeedReading]:
    equal_frames = 0
    last_frame: Optional[Frame] = None
    last_reading = GameSpeedReading(
        False, None, "", -1.0, False, "capture_unavailable"
    )
    for _ in range(max(1, attempts)):
        sleep_fn(0.20)
        last_frame = capture_fn()
        last_reading = read_game_speed_control(last_frame)
        if not last_reading.valid or last_reading.value is None:
            equal_frames = 0
            continue
        if last_reading.value > previous:
            return last_frame, last_reading
        if last_reading.value < previous:
            return last_frame, last_reading
        equal_frames += 1
        if equal_frames >= 2:
            return last_frame, last_reading
    return last_frame, last_reading


def game_speed_target(mode: str) -> float:
    """Resolve one validated mode to its visible speed target."""

    normalized = str(mode or "").strip().upper()
    if normalized not in VALID_GAME_SPEED_MODES:
        raise ValueError(
            f"Unsupported game-speed mode {mode!r}; "
            f"expected one of {sorted(VALID_GAME_SPEED_MODES)}"
        )
    return (
        REDUCED_GAME_SPEED
        if normalized == "REDUCED"
        else NORMAL_MAX_GAME_SPEED
    )


def _target_satisfied(value: float, *, mode: str, target: float) -> bool:
    if mode == "AUTO":
        return value >= target
    return abs(value - target) <= _TARGET_TOLERANCE


def enforce_game_speed(
    *,
    mode: str = "AUTO",
    screenshot: Optional[Frame] = None,
    capture_fn: CaptureFn = capture_adb_screenshot,
    detector: DetectorFn = detect_state_and_overlays,
    tap_fn: TapFn = safe_tap,
    sleep_fn: SleepFn = time.sleep,
    action_guard_fn: Callable[[], bool] = lambda: True,
    max_taps: int = MAX_GAME_SPEED_TAPS,
) -> GameSpeedResult:
    """Enforce normal maximum speed or an exact reduced x4.0 target."""

    normalized_mode = str(mode or "").strip().upper()
    target = game_speed_target(normalized_mode)
    frame = screenshot if screenshot is not None else capture_fn()
    initial_reading = measure_game_speed(frame, detector=detector)
    if not initial_reading.valid or initial_reading.value is None:
        return GameSpeedResult(
            False,
            initial_reading.value,
            initial_reading.value,
            0,
            0,
            initial_reading.reason,
            mode=normalized_mode,
            target=target,
        )

    initial = initial_reading.value
    current = initial
    reading = initial_reading
    taps_sent = 0
    increases = 0
    decreases = 0
    action_started = False

    def finish(
        success: bool,
        final: Optional[float],
        reason: str,
    ) -> GameSpeedResult:
        result = GameSpeedResult(
            success,
            initial,
            final,
            taps_sent,
            increases,
            reason,
            decreases=decreases,
            mode=normalized_mode,
            target=target,
        )
        if action_started:
            log_result(
                (
                    f"Game speed adjustment complete — x{final:.1f}"
                    if success and final is not None
                    else "Game speed adjustment failed"
                ),
                detail=(
                    f"[GAME_SPEED] mode={normalized_mode} target={target:.1f} "
                    f"initial={initial:.1f} final={final} taps={taps_sent} "
                    f"increases={increases} decreases={decreases} "
                    f"result={'completed' if success else 'failed'} "
                    f"reason={reason}"
                ),
            )
        return result

    if _target_satisfied(current, mode=normalized_mode, target=target):
        return GameSpeedResult(
            True,
            initial,
            current,
            taps_sent,
            increases,
            "target_satisfied",
            mode=normalized_mode,
            target=target,
        )

    for _ in range(max(1, int(max_taps))):
        if not action_guard_fn():
            return finish(False, current, "actions_blocked")
        direction = "increase" if current < target else "decrease"
        if direction == "decrease" and not reading.minus_visible:
            return finish(False, current, "minus_control_not_visible")
        assert frame is not None
        region = (
            GAME_SPEED_PLUS_REGION
            if direction == "increase"
            else GAME_SPEED_MINUS_REGION
        )
        point = (
            GAME_SPEED_PLUS_POINT
            if direction == "increase"
            else GAME_SPEED_MINUS_POINT
        )

        def control_visible(candidate: Frame) -> bool:
            candidate_reading = read_game_speed_control(candidate)
            return bool(
                candidate_reading.valid
                and (
                    candidate_reading.plus_visible
                    if direction == "increase"
                    else candidate_reading.minus_visible
                )
            )

        verification = TapVerification(
            screenshot=frame,
            target_region=region,
            description=(
                f"visible game-speed {'plus' if direction == 'increase' else 'minus'} "
                f"control at x{current:.1f}"
            ),
            verifier=control_visible,
        )
        if not action_started:
            log_action_intent(
                "Adjusting game speed",
                reason=(
                    f"{normalized_mode} mode requires "
                    + (
                        f"at least x{target:.1f}"
                        if normalized_mode == "AUTO"
                        else f"exactly x{target:.1f}"
                    )
                ),
                detail=(
                    f"[GAME_SPEED] mode={normalized_mode} "
                    f"initial={initial:.1f} target={target:.1f}"
                ),
            )
            action_started = True
        if not tap_fn(
            point,
            dispatch="now",
            log_label=f"game_speed:{direction}",
            verification=verification,
        ):
            return finish(False, current, "tap_not_authorized")
        taps_sent += 1
        frame, reading = _wait_for_settled_speed(
            current,
            capture_fn=capture_fn,
            sleep_fn=sleep_fn,
        )
        if not reading.valid or reading.value is None:
            return finish(False, current, reading.reason)
        if direction == "increase" and reading.value < current:
            return finish(False, reading.value, "speed_decreased_after_plus")
        if direction == "decrease" and reading.value > current:
            return finish(False, reading.value, "speed_increased_after_minus")
        if reading.value == current:
            return finish(
                False,
                current,
                (
                    "speed_did_not_increase"
                    if direction == "increase"
                    else "speed_did_not_decrease"
                ),
            )
        current = reading.value
        if direction == "increase":
            increases += 1
        else:
            decreases += 1
        if _target_satisfied(current, mode=normalized_mode, target=target):
            return finish(True, current, "target_reached")
        if normalized_mode == "REDUCED" and (
            (direction == "increase" and current > target)
            or (direction == "decrease" and current < target)
        ):
            return finish(False, current, "target_crossed")
    return finish(False, current, "target_not_reached")


def maximize_game_speed(
    *,
    screenshot: Optional[Frame] = None,
    capture_fn: CaptureFn = capture_adb_screenshot,
    detector: DetectorFn = detect_state_and_overlays,
    tap_fn: TapFn = safe_tap,
    sleep_fn: SleepFn = time.sleep,
    action_guard_fn: Callable[[], bool] = lambda: True,
    max_taps: int = MAX_GAME_SPEED_TAPS,
) -> GameSpeedResult:
    """Compatibility wrapper for normal automatic maximum enforcement."""

    return enforce_game_speed(
        mode="AUTO",
        screenshot=screenshot,
        capture_fn=capture_fn,
        detector=detector,
        tap_fn=tap_fn,
        sleep_fn=sleep_fn,
        action_guard_fn=action_guard_fn,
        max_taps=max_taps,
    )


class GameSpeedGuard:
    """Periodically enforce the selected battle-speed mode."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        check_interval_s: float = GAME_SPEED_CHECK_INTERVAL_S,
        retry_interval_s: float = GAME_SPEED_RETRY_INTERVAL_S,
        warning_after_failures: int = GAME_SPEED_WARNING_AFTER_FAILURES,
        warning_repeat_s: float = GAME_SPEED_WARNING_REPEAT_S,
        reduced_reminder_interval_s: float = (
            REDUCED_MODE_REMINDER_INTERVAL_S
        ),
    ) -> None:
        self._clock = clock
        self._check_interval_s = float(check_interval_s)
        self._retry_interval_s = float(retry_interval_s)
        self._warning_after_failures = max(1, int(warning_after_failures))
        self._warning_repeat_s = max(0.0, float(warning_repeat_s))
        self._reduced_reminder_interval_s = max(
            1.0,
            float(reduced_reminder_interval_s),
        )
        self._next_check_at = 0.0
        self._consecutive_failures = 0
        self._warning_active = False
        self._last_warning_at: Optional[float] = None
        self._mode = "AUTO"
        self._next_reduced_reminder_at: Optional[float] = None
        self._mode_timeline: list[dict[str, Any]] = []
        self.last_result: Optional[GameSpeedResult] = None

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, *, wave: Optional[int] = None) -> bool:
        """Apply a validated mode, re-arm enforcement, and record the change."""

        normalized = str(mode or "").strip().upper()
        target = game_speed_target(normalized)
        if normalized == self._mode:
            return False
        previous = self._mode
        self._mode = normalized
        self._next_check_at = 0.0
        self._consecutive_failures = 0
        self._warning_active = False
        self._last_warning_at = None
        self.last_result = None
        self._record_mode_event(source="operator_control", wave=wave)
        if normalized == "REDUCED":
            self._next_reduced_reminder_at = (
                self._clock() + self._reduced_reminder_interval_s
            )
            log(
                "[GAME_SPEED] REDUCED mode is active; battle speed is being "
                f"held at x{target:.1f} until AUTO is restored",
                "WARN",
            )
        else:
            self._next_reduced_reminder_at = None
            if previous == "REDUCED":
                log(
                    "[GAME_SPEED] AUTO mode restored; an immediate normal-speed "
                    "check is armed",
                    "INFO",
                )
        return True

    def reset_battle(self, *, wave: Optional[int] = None) -> None:
        """Start a fresh per-battle mode timeline without changing the mode."""

        self._mode_timeline = []
        self._record_mode_event(source="battle_start", wave=wave)
        self._next_check_at = 0.0
        self._consecutive_failures = 0
        self._warning_active = False
        self._last_warning_at = None
        self.last_result = None

    def snapshot(self) -> dict[str, Any]:
        """Return serializable experiment metadata for the completed record."""

        return {
            "mode": self._mode,
            "target": game_speed_target(self._mode),
            "target_semantics": (
                "minimum" if self._mode == "AUTO" else "exact"
            ),
            "timeline": [dict(event) for event in self._mode_timeline],
        }

    def _record_mode_event(
        self,
        *,
        source: str,
        wave: Optional[int],
    ) -> None:
        event = {
            "changed_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "mode": self._mode,
            "target": game_speed_target(self._mode),
            "target_semantics": (
                "minimum" if self._mode == "AUTO" else "exact"
            ),
            "source": source,
        }
        if isinstance(wave, int) and not isinstance(wave, bool) and wave >= 0:
            event["approximate_wave"] = wave
        self._mode_timeline.append(event)

    def _remind_if_reduced(self, now: float) -> None:
        if (
            self._mode != "REDUCED"
            or self._next_reduced_reminder_at is None
            or now < self._next_reduced_reminder_at
        ):
            return
        log(
            "[GAME_SPEED] REDUCED mode remains active; battle speed is still "
            f"being held at x{REDUCED_GAME_SPEED:.1f}",
            "WARN",
        )
        self._next_reduced_reminder_at = (
            now + self._reduced_reminder_interval_s
        )

    def handle(
        self,
        screenshot: Frame,
        detection: Mapping[str, Any],
        *,
        action_guard_fn: Callable[[], bool],
        maximize_fn: Callable[..., GameSpeedResult] = enforce_game_speed,
    ) -> bool:
        """Run one bounded check and report whether it dispatched any input."""

        now = self._clock()
        self._remind_if_reduced(now)
        state = str(detection.get("state") or "UNKNOWN")
        if state in {
            "HOME",
            "HOME_SCREEN",
            "GAME_OVER",
            "TOURNAMENT_RESULTS",
        }:
            self._next_check_at = 0.0
            self._consecutive_failures = 0
            self._warning_active = False
            self._last_warning_at = None
            self.last_result = None
            return False
        if state != "RUNNING":
            return False
        if now < self._next_check_at or not action_guard_fn():
            return False

        previous_result = self.last_result
        result = maximize_fn(
            mode=self._mode,
            screenshot=screenshot,
            action_guard_fn=action_guard_fn,
        )
        self.last_result = result
        self._next_check_at = now + (
            self._check_interval_s if result.success else self._retry_interval_s
        )
        warning_was_active = self._warning_active
        failed_checks = self._consecutive_failures
        if result.success:
            self._consecutive_failures = 0
            self._warning_active = False
            self._last_warning_at = None
            if warning_was_active:
                log(
                    "[GAME_SPEED] Verification recovered after "
                    f"{failed_checks} consecutive failed checks "
                    f"(final={result.final} reason={result.reason})",
                    "INFO",
                )
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._warning_after_failures:
                warning_due = (
                    not self._warning_active
                    or self._last_warning_at is None
                    or now - self._last_warning_at >= self._warning_repeat_s
                )
                self._warning_active = True
                if warning_due:
                    qualifier = (
                        "Still unable"
                        if self._last_warning_at is not None
                        else "Unable"
                    )
                    log(
                        f"[GAME_SPEED] {qualifier} to enforce {self._mode} "
                        f"battle speed target x{game_speed_target(self._mode):.1f} "
                        f"after {self._consecutive_failures} consecutive checks; "
                        f"automation will retry (reason={result.reason})",
                        "WARN",
                    )
                    self._last_warning_at = now
        should_log = (
            result.taps_sent > 0
            or previous_result is None
            or previous_result.success != result.success
            or previous_result.final != result.final
            or previous_result.reason != result.reason
        )
        if should_log:
            log(
                "[GAME_SPEED] "
                f"mode={result.mode} target={result.target} "
                f"initial={result.initial} final={result.final} "
                f"taps={result.taps_sent} increases={result.increases} "
                f"decreases={result.decreases} "
                f"success={result.success} reason={result.reason}",
                "INFO" if result.success else "DEBUG",
            )
        return result.taps_sent > 0


__all__ = [
    "GAME_SPEED_MINUS_POINT",
    "GAME_SPEED_MINUS_REGION",
    "GAME_SPEED_PLUS_POINT",
    "GAME_SPEED_PLUS_REGION",
    "GAME_SPEED_REGION",
    "NORMAL_MAX_GAME_SPEED",
    "REDUCED_GAME_SPEED",
    "VALID_GAME_SPEED_MODES",
    "GameSpeedGuard",
    "GameSpeedReading",
    "GameSpeedResult",
    "enforce_game_speed",
    "game_speed_target",
    "maximize_game_speed",
    "measure_game_speed",
    "read_game_speed_control",
]
