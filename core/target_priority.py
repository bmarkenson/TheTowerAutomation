"""Read and validate the ordered Target Priority list."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import time
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

from core.ss_capture import capture_adb_screenshot
from core.input import safe_tap
from core.run_controls import ensure_menu_open
from core.target_priority_config import (
    TARGET_PRIORITY_TARGETS,
    validate_target_priority_order,
)
from utils.ocr_utils import ocr_text_and_conf
from utils.logger import log


TARGETS = TARGET_PRIORITY_TARGETS

_ROW_FIRST_CENTER_Y = 320
_ROW_STEP_Y = 160
_LABEL_X1 = 360
_LABEL_X2 = 800
_LABEL_HALF_HEIGHT = 50
_SCOPE_TARGET = (910, 380)
_CLOSE_TARGET = (950, 100)
_UP_ARROW_X = 910


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


def _tap(point: tuple[int, int]) -> bool:
    return safe_tap(point, require_visible=False, dispatch="now", log_label="target_priority")


def ensure_target_priority_order(
    expected: Sequence[str] = TARGETS,
    *,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    tap_fn: Callable[[tuple[int, int]], bool] = _tap,
    ensure_menu_fn: Callable[[], bool] = ensure_menu_open,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Open the panel, enforce expected using Up arrows, and verify it."""
    expected_list = validate_target_priority_order(expected)
    if not ensure_menu_fn():
        log("[TARGET_PRIORITY] Unable to open the game menu", "WARN")
        return False
    if not tap_fn(_SCOPE_TARGET):
        log("[TARGET_PRIORITY] Unable to open Target Priority", "WARN")
        return False
    sleep_fn(0.6)
    try:
        actual = read_target_priority_order(capture_fn)
        log(f"[TARGET_PRIORITY] Current order: {actual}", "INFO")
        working = list(actual)
        for desired_index, target in enumerate(expected_list):
            current_index = working.index(target)
            while current_index > desired_index:
                arrow_y = _ROW_FIRST_CENTER_Y + current_index * _ROW_STEP_Y
                if not tap_fn((_UP_ARROW_X, arrow_y)):
                    raise RuntimeError(f"Failed moving {target!r} upward")
                working[current_index - 1], working[current_index] = (
                    working[current_index], working[current_index - 1]
                )
                current_index -= 1
                sleep_fn(0.15)
        verified = read_target_priority_order(capture_fn)
        if not target_priority_matches(verified, expected_list):
            log(f"[TARGET_PRIORITY] Verification failed: expected={expected_list}, actual={verified}", "WARN")
            return False
        log("[TARGET_PRIORITY] Order verified", "INFO")
        return True
    except Exception as exc:
        log(f"[TARGET_PRIORITY] Enforcement failed: {exc}", "ERROR")
        return False
    finally:
        tap_fn(_CLOSE_TARGET)
        sleep_fn(0.2)


__all__ = [
    "TARGETS",
    "detect_target_priority_order",
    "ensure_target_priority_order",
    "read_target_priority_order",
    "target_priority_matches",
]
