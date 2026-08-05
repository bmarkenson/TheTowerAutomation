"""Read and validate the ordered Target Priority list."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import re
import time
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

from core.ss_capture import capture_adb_screenshot
from core.input import TapVerification, safe_tap
from core.run_controls import ensure_menu_open
from core.target_priority_config import (
    TARGET_PRIORITY_TARGETS,
    validate_target_priority_order,
)
from utils.ocr_utils import ocr_text_and_conf
from utils.logger import log, log_action_intent, log_result


TARGETS = TARGET_PRIORITY_TARGETS

_ROW_FIRST_CENTER_Y = 320
_ROW_STEP_Y = 160
_LABEL_X1 = 360
_LABEL_X2 = 800
_LABEL_HALF_HEIGHT = 50
_SCOPE_TARGET = (910, 380)
_CLOSE_TARGET = (950, 100)
_UP_ARROW_X = 910


@dataclass(frozen=True)
class TargetPriorityObservation:
    expected: tuple[str, ...]
    actual: tuple[str, ...]
    observed: bool
    matches: Optional[bool]
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _finish_target_priority_observation(
    observation: TargetPriorityObservation,
) -> TargetPriorityObservation:
    """Emit the terminal result for one read-only Target Priority check."""

    if not observation.observed:
        summary = (
            "Target Priority check failed — "
            f"{observation.reason.replace('_', ' ')}"
        )
    elif observation.matches:
        summary = "Target Priority check complete — order matches the strategy"
    else:
        summary = "Target Priority check complete — order differs from the strategy"
    log_result(
        summary,
        detail=(
            f"[TARGET_PRIORITY] result={observation.reason} "
            f"observed={observation.observed} matches={observation.matches} "
            f"expected={list(observation.expected)} "
            f"actual={list(observation.actual)}"
        ),
    )
    return observation


def _finish_target_priority_enforcement(
    success: bool,
    *,
    expected: Sequence[str],
    actual: Sequence[str],
    reason: str,
) -> bool:
    """Emit the terminal result for one Target Priority enforcement."""

    summary = (
        "Target Priority setup complete — order verified"
        if success
        else f"Target Priority setup failed — {reason.replace('_', ' ')}"
    )
    log_result(
        summary,
        detail=(
            f"[TARGET_PRIORITY] result={'completed' if success else reason} "
            f"expected={list(expected)} actual={list(actual)}"
        ),
    )
    return success


def _normalise(text: str) -> str:
    return " ".join(re.findall(r"[A-Z]+", text.upper()))


def _canonical_target(raw_text: str) -> Optional[str]:
    observed = _normalise(raw_text)
    if not observed:
        return None
    best = max(
        TARGETS,
        key=lambda target: SequenceMatcher(None, observed, _normalise(target)).ratio(),
    )
    score = SequenceMatcher(None, observed, _normalise(best)).ratio()
    return best if score >= 0.60 else None


def detect_target_priority_order(image: np.ndarray) -> list[str]:
    """Return the ten priority labels in their current top-to-bottom order."""
    if image is None or image.shape[0] < 1920 or image.shape[1] < 1080:
        raise ValueError("Target Priority detection requires a 1080x1920 screenshot")

    order: list[str] = []
    for index in range(len(TARGETS)):
        center_y = _ROW_FIRST_CENTER_Y + index * _ROW_STEP_Y
        crop = image[
            center_y - _LABEL_HALF_HEIGHT : center_y + _LABEL_HALF_HEIGHT,
            _LABEL_X1:_LABEL_X2,
        ]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        raw_text, _confidence = ocr_text_and_conf(binary, psm=7)
        target = _canonical_target(raw_text)
        if target is None:
            raise ValueError(
                f"Unable to identify Target Priority row {index + 1}: {raw_text!r}"
            )
        order.append(target)

    if len(set(order)) != len(TARGETS):
        raise ValueError(f"Target Priority OCR returned duplicate labels: {order}")
    return order


def read_target_priority_order(
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
) -> list[str]:
    """Capture the currently open Target Priority panel and read its order."""
    image = capture_fn()
    if image is None:
        raise RuntimeError("Unable to capture Target Priority screen")
    return detect_target_priority_order(image)


def target_priority_matches(actual: Sequence[str], expected: Sequence[str]) -> bool:
    """Return whether two complete priority lists match, case-insensitively."""
    return [_normalise(item) for item in actual] == [
        _normalise(item) for item in expected
    ]


def _tap(
    point: tuple[int, int],
    *,
    verification: Optional[TapVerification] = None,
) -> bool:
    if point == _SCOPE_TARGET:
        return safe_tap("navigation.target_priority", dispatch="now")
    if point == _CLOSE_TARGET:
        return safe_tap("buttons.close:target_priority", dispatch="now")
    return safe_tap(
        point,
        dispatch="now",
        log_label="target_priority",
        verification=verification,
    )


def observe_target_priority_order(
    expected: Sequence[str] = TARGETS,
    *,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    tap_fn: Callable[..., bool] = _tap,
    ensure_menu_fn: Callable[[], bool] = ensure_menu_open,
    sleep_fn: Callable[[float], None] = time.sleep,
    panel_open: bool = False,
) -> TargetPriorityObservation:
    """Read and compare Target Priority without changing its order."""

    expected_list = validate_target_priority_order(expected)
    log_action_intent(
        "Checking Target Priority",
        reason=(
            "compare the current order with the selected strategy without "
            "changing it"
        ),
        detail=f"[TARGET_PRIORITY] mode=observe expected={expected_list}",
    )
    opened_here = panel_open
    observation: TargetPriorityObservation
    try:
        if not panel_open:
            if not ensure_menu_fn():
                raise RuntimeError("unable to open the game menu")
            if not tap_fn(_SCOPE_TARGET):
                raise RuntimeError("unable to open Target Priority")
            opened_here = True
            sleep_fn(0.6)
        actual = read_target_priority_order(capture_fn)
        matches = target_priority_matches(actual, expected_list)
        log(
            f"[TARGET_PRIORITY] Observed order matches_expected={matches}: {actual}",
            "DEBUG",
        )
        observation = TargetPriorityObservation(
            expected=tuple(expected_list),
            actual=tuple(actual),
            observed=True,
            matches=matches,
            reason="observed",
        )
    except Exception as exc:
        log(f"[TARGET_PRIORITY] Observation failed: {exc}", "WARN")
        observation = TargetPriorityObservation(
            expected=tuple(expected_list),
            actual=(),
            observed=False,
            matches=None,
            reason=str(exc),
        )
    finally:
        if opened_here:
            tap_fn(_CLOSE_TARGET)
            sleep_fn(0.2)
    return _finish_target_priority_observation(observation)


def ensure_target_priority_order(
    expected: Sequence[str] = TARGETS,
    *,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    tap_fn: Callable[..., bool] = _tap,
    ensure_menu_fn: Callable[[], bool] = ensure_menu_open,
    sleep_fn: Callable[[float], None] = time.sleep,
    panel_open: bool = False,
    repair_observer_fn: Optional[Callable[[], None]] = None,
) -> bool:
    """Open or consume the panel, enforce with Up arrows, and verify it."""
    expected_list = validate_target_priority_order(expected)
    log_action_intent(
        "Aligning Target Priority",
        reason=(
            "the selected strategy requires a specific target order before "
            "normal run actions continue"
        ),
        detail=f"[TARGET_PRIORITY] mode=enforce expected={expected_list}",
    )
    opened_here = panel_open
    if not panel_open:
        if not ensure_menu_fn():
            log("[TARGET_PRIORITY] Unable to open the game menu", "WARN")
            return _finish_target_priority_enforcement(
                False,
                expected=expected_list,
                actual=(),
                reason="game_menu_not_verified",
            )
        if not tap_fn(_SCOPE_TARGET):
            log("[TARGET_PRIORITY] Unable to open Target Priority", "WARN")
            return _finish_target_priority_enforcement(
                False,
                expected=expected_list,
                actual=(),
                reason="panel_not_verified",
            )
        opened_here = True
        sleep_fn(0.6)
    actual: list[str] = []
    verified: list[str] = []
    success = False
    reason = "not_started"
    repair_observed = False
    try:
        actual = read_target_priority_order(capture_fn)
        log(f"[TARGET_PRIORITY] Current order: {actual}", "DEBUG")
        working = list(actual)
        for desired_index, target in enumerate(expected_list):
            current_index = working.index(target)
            while current_index > desired_index:
                arrow_y = _ROW_FIRST_CENTER_Y + current_index * _ROW_STEP_Y
                fresh = capture_fn()
                if fresh is None:
                    raise RuntimeError(
                        f"Unable to reverify {target!r} before moving it"
                    )
                expected_index = current_index
                verification = TapVerification(
                    screenshot=fresh,
                    target_region=(
                        _UP_ARROW_X - 70,
                        arrow_y - 70,
                        140,
                        140,
                    ),
                    description=f"target_priority_row:{target}",
                    verifier=lambda frame, expected=target, index=expected_index: (
                        detect_target_priority_order(frame)[index] == expected
                    ),
                )
                if not tap_fn(
                    (_UP_ARROW_X, arrow_y),
                    verification=verification,
                ):
                    raise RuntimeError(f"Failed moving {target!r} upward")
                if not repair_observed and repair_observer_fn is not None:
                    repair_observer_fn()
                    repair_observed = True
                working[current_index - 1], working[current_index] = (
                    working[current_index], working[current_index - 1]
                )
                current_index -= 1
                sleep_fn(0.15)
        verified = read_target_priority_order(capture_fn)
        if not target_priority_matches(verified, expected_list):
            log(
                f"[TARGET_PRIORITY] Verification failed: "
                f"expected={expected_list}, actual={verified}",
                "WARN",
            )
            reason = "verification_mismatch"
        else:
            success = True
            reason = "verified"
            log("[TARGET_PRIORITY] Order verified", "DEBUG")
    except Exception as exc:
        log(f"[TARGET_PRIORITY] Enforcement failed: {exc}", "ERROR")
        reason = str(exc)
    finally:
        if opened_here:
            tap_fn(_CLOSE_TARGET)
            sleep_fn(0.2)
    return _finish_target_priority_enforcement(
        success,
        expected=expected_list,
        actual=verified or actual,
        reason=reason,
    )


__all__ = [
    "TARGETS",
    "TargetPriorityObservation",
    "detect_target_priority_order",
    "ensure_target_priority_order",
    "observe_target_priority_order",
    "read_target_priority_order",
    "target_priority_matches",
]
