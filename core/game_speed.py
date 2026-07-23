"""Battle-only measurement and enforcement for the game-speed control."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.input import TapVerification, safe_tap
from core.ss_capture import (
    capture_adb_screenshot,
    is_complete_screenshot,
)
from core.state_detector import detect_state_and_overlays
from utils.logger import log
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
CaptureFn = Callable[[], Optional[Frame]]
DetectorFn = Callable[[Frame], Mapping[str, Any]]
SleepFn = Callable[[float], None]
TapFn = Callable[..., bool]

GAME_SPEED_REGION = (750, 895, 125, 85)
GAME_SPEED_PLUS_REGION = (875, 895, 70, 80)
GAME_SPEED_PLUS_POINT = (910, 935)
GAME_SPEED_CHECK_INTERVAL_S = 30.0
GAME_SPEED_RETRY_INTERVAL_S = 5.0
MAX_GAME_SPEED_TAPS = 24

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "value": self.value,
            "raw_text": self.raw_text,
            "confidence": self.confidence,
            "plus_visible": self.plus_visible,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GameSpeedResult:
    """Outcome from walking the battle speed control to its current ceiling."""

    success: bool
    initial: Optional[float]
    final: Optional[float]
    taps_sent: int
    increases: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "initial": self.initial,
            "final": self.final,
            "taps_sent": self.taps_sent,
            "increases": self.increases,
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
    if match is None:
        return GameSpeedReading(
            False,
            None,
            str(raw_text or ""),
            float(confidence),
            plus_visible,
            "speed_ocr_failed",
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
        )
    if not plus_visible:
        return GameSpeedReading(
            False,
            value,
            str(raw_text or ""),
            float(confidence),
            False,
            "plus_control_not_visible",
        )
    return GameSpeedReading(
        True,
        value,
        str(raw_text or ""),
        float(confidence),
        True,
        "visible",
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
    """Increase battle speed until one verified plus tap produces no change."""

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
        )

    initial = initial_reading.value
    current = initial
    taps_sent = 0
    increases = 0
    for _ in range(max(1, int(max_taps))):
        if not action_guard_fn():
            return GameSpeedResult(
                False, initial, current, taps_sent, increases, "actions_blocked"
            )
        assert frame is not None
        verification = TapVerification(
            screenshot=frame,
            target_region=GAME_SPEED_PLUS_REGION,
            description=f"visible game-speed plus control at x{current:.1f}",
            verifier=lambda candidate: read_game_speed_control(candidate).valid,
        )
        if not tap_fn(
            GAME_SPEED_PLUS_POINT,
            dispatch="now",
            log_label="game_speed:increase",
            verification=verification,
        ):
            return GameSpeedResult(
                False, initial, current, taps_sent, increases, "tap_not_authorized"
            )
        taps_sent += 1
        frame, reading = _wait_for_settled_speed(
            current,
            capture_fn=capture_fn,
            sleep_fn=sleep_fn,
        )
        if not reading.valid or reading.value is None:
            return GameSpeedResult(
                False, initial, current, taps_sent, increases, reading.reason
            )
        if reading.value < current:
            return GameSpeedResult(
                False,
                initial,
                reading.value,
                taps_sent,
                increases,
                "speed_decreased_after_plus",
            )
        if reading.value == current:
            return GameSpeedResult(
                True, initial, current, taps_sent, increases, "maximum_verified"
            )
        current = reading.value
        increases += 1
    return GameSpeedResult(
        False, initial, current, taps_sent, increases, "maximum_not_reached"
    )


class GameSpeedGuard:
    """Periodically restore the maximum visible speed during active battles."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        check_interval_s: float = GAME_SPEED_CHECK_INTERVAL_S,
        retry_interval_s: float = GAME_SPEED_RETRY_INTERVAL_S,
    ) -> None:
        self._clock = clock
        self._check_interval_s = float(check_interval_s)
        self._retry_interval_s = float(retry_interval_s)
        self._next_check_at = 0.0
        self.last_result: Optional[GameSpeedResult] = None

    def handle(
        self,
        screenshot: Frame,
        detection: Mapping[str, Any],
        *,
        action_guard_fn: Callable[[], bool],
        maximize_fn: Callable[..., GameSpeedResult] = maximize_game_speed,
    ) -> bool:
        """Run one bounded check and report whether it dispatched any input."""

        state = str(detection.get("state") or "UNKNOWN")
        if state in {
            "HOME",
            "HOME_SCREEN",
            "GAME_OVER",
            "TOURNAMENT_RESULTS",
        }:
            self._next_check_at = 0.0
            self.last_result = None
            return False
        if state != "RUNNING":
            return False
        now = self._clock()
        if now < self._next_check_at or not action_guard_fn():
            return False

        result = maximize_fn(
            screenshot=screenshot,
            action_guard_fn=action_guard_fn,
        )
        self.last_result = result
        self._next_check_at = now + (
            self._check_interval_s if result.success else self._retry_interval_s
        )
        log(
            "[GAME_SPEED] "
            f"initial={result.initial} final={result.final} "
            f"taps={result.taps_sent} increases={result.increases} "
            f"success={result.success} reason={result.reason}",
            "INFO" if result.success else "WARN",
        )
        return result.taps_sent > 0


__all__ = [
    "GAME_SPEED_PLUS_POINT",
    "GAME_SPEED_PLUS_REGION",
    "GAME_SPEED_REGION",
    "GameSpeedGuard",
    "GameSpeedReading",
    "GameSpeedResult",
    "maximize_game_speed",
    "measure_game_speed",
    "read_game_speed_control",
]
