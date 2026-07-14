"""Guarded, read-only inspection of the in-run Damage adjuster."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable, Final, Optional

import numpy as np

from core.input import safe_tap, tap_if_visible
from core.matcher import get_match
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
CaptureFn = Callable[[], Optional[Frame]]
SleepFn = Callable[[float], None]

DAMAGE_SELECTOR_REGION: Final[tuple[int, int, int, int]] = (60, 1580, 960, 270)
DAMAGE_SELECTOR_MODE: Final[str] = "Percent Of Enemy Health"


@dataclass(frozen=True)
class DamageAdjusterReading:
    visible: bool
    mode: Optional[str]
    percentage: Optional[str]
    ocr_text: str
    ocr_confidence: float
    panel_confidence: float


def _canonical_percentage(text: str) -> Optional[str]:
    compact = re.sub(r"\s+", "", text.upper())
    compact = compact.replace("–", "-").replace("—", "-")
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?(?:E-\d+)?)%", compact)
    return f"{match.group(1)}%" if match else None


def read_damage_adjuster(screenshot: Optional[Frame]) -> DamageAdjusterReading:
    """Read the configurable percentage, excluding the derived damage value."""

    if screenshot is None or not getattr(screenshot, "size", 0):
        return DamageAdjusterReading(False, None, None, "", -1.0, -1.0)

    point, panel_confidence = get_match(
        "indicators.damage_adjuster",
        screenshot=screenshot,
    )
    if point is None:
        return DamageAdjusterReading(
            False,
            None,
            None,
            "",
            -1.0,
            panel_confidence,
        )

    x, y, w, h = DAMAGE_SELECTOR_REGION
    crop = screenshot[y : y + h, x : x + w]
    if crop.size == 0:
        return DamageAdjusterReading(True, None, None, "", -1.0, panel_confidence)

    ocr_text, ocr_confidence = ocr_text_and_conf(crop, psm=6)
    normalized_words = " ".join(re.findall(r"[A-Z]+", ocr_text.upper()))
    mode = (
        DAMAGE_SELECTOR_MODE
        if "PERCENT OF ENEMY HEALTH" in normalized_words
        else None
    )
    return DamageAdjusterReading(
        True,
        mode,
        _canonical_percentage(ocr_text),
        ocr_text,
        ocr_confidence,
        panel_confidence,
    )


def open_damage_adjuster(
    *,
    capture_fn: CaptureFn = capture_adb_screenshot,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    sleep_fn: SleepFn = time.sleep,
    attempts: int = 6,
) -> Optional[DamageAdjusterReading]:
    """Open Damage details from Attack and return a verified settled reading."""

    screenshot = capture_fn()
    existing = read_damage_adjuster(screenshot)
    if existing.visible:
        return existing
    if screenshot is None:
        log("[DAMAGE_ADJUSTER] Screenshot unavailable before open", "WARN")
        return None

    detection = detect_state_and_overlays(screenshot)
    if detection["state"] != "RUNNING" or detection["menu"] != "ATTACK_MENU":
        log(
            "[DAMAGE_ADJUSTER] Refusing open from "
            f"state={detection['state']!r}, menu={detection['menu']!r}",
            "WARN",
        )
        return None
    if not tap_visible_fn(
        "buttons.damage_adjuster:attack",
        screenshot=screenshot,
        retries=1,
    ):
        log("[DAMAGE_ADJUSTER] Damage label did not match", "WARN")
        return None

    for _ in range(max(1, attempts)):
        sleep_fn(0.25)
        reading = read_damage_adjuster(capture_fn())
        if reading.visible:
            return reading
    log("[DAMAGE_ADJUSTER] Panel did not appear after verified label tap", "WARN")
    return None


def dismiss_damage_adjuster(
    *,
    capture_fn: CaptureFn = capture_adb_screenshot,
    tap_fn: Callable[..., bool] = safe_tap,
    sleep_fn: SleepFn = time.sleep,
    attempts: int = 6,
) -> bool:
    """Dismiss the verified panel through its non-interactive dimmed backdrop."""

    screenshot = capture_fn()
    reading = read_damage_adjuster(screenshot)
    if not reading.visible:
        if screenshot is not None:
            detection = detect_state_and_overlays(screenshot)
            if (
                detection["state"] == "RUNNING"
                and detection["menu"] == "ATTACK_MENU"
            ):
                return True
        log("[DAMAGE_ADJUSTER] Refusing dismiss without the panel guard", "WARN")
        return False

    if not tap_fn(
        "gesture_targets.dismiss_damage_adjuster",
        require_visible=False,
        dispatch="now",
    ):
        return False

    for _ in range(max(1, attempts)):
        sleep_fn(0.25)
        frame = capture_fn()
        if frame is None:
            continue
        detection = detect_state_and_overlays(frame)
        if detection["state"] == "RUNNING" and detection["menu"] == "ATTACK_MENU":
            return True
    log("[DAMAGE_ADJUSTER] Attack menu was not restored after dismiss", "WARN")
    return False


__all__ = [
    "DAMAGE_SELECTOR_MODE",
    "DAMAGE_SELECTOR_REGION",
    "DamageAdjusterReading",
    "dismiss_damage_adjuster",
    "open_damage_adjuster",
    "read_damage_adjuster",
]
