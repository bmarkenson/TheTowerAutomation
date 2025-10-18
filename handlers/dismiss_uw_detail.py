"""Handler to dismiss Ultimate Weapon detail popups."""

from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np

from core.input import safe_tap
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.clickmap_access import resolve_dot_path
from utils.logger import log


def _tapped_success(screenshot: np.ndarray) -> bool:
    try:
        detection = detect_state_and_overlays(screenshot)
    except Exception as exc:
        log(f"[UW_DETAIL] Failed to re-evaluate overlay state: {exc}", "WARN")
        return False

    overlays = set(detection.get("overlays") or [])
    return "UW_DETAIL" not in overlays


def handle_uw_detail_popup(
    *,
    screenshot: Optional[np.ndarray] = None,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
) -> Optional[np.ndarray]:
    """Attempt to dismiss the UW detail popup overlay.

    Returns the freshest screenshot available after dismissal (or the original on failure).
    """

    image = screenshot if screenshot is not None else capture_fn()
    if image is None:
        return None

    entry = resolve_dot_path("overlays.uw_detail") or {}
    region = entry.get("match_region") or {}
    region_x = int(region.get("x", 80))
    region_y = int(region.get("y", 120))
    region_w = int(region.get("w", 880))
    region_h = int(region.get("h", 620))

    # Prefer a tap just above the popup; fall back below if needed.
    candidate_taps = [
        (region_x + region_w // 2, max(60, region_y - 40)),
        (region_x + region_w // 2, region_y + region_h + 40),
        (max(60, region_x - 40), region_y + region_h // 2),
        (region_x + region_w + 40, region_y + region_h // 2),
    ]

    for attempt in range(1, max_attempts + 1):
        tap_x, tap_y = candidate_taps[(attempt - 1) % len(candidate_taps)]
        log(
            f"[UW_DETAIL] Dismissing detail popup (attempt {attempt}) with tap at ({tap_x},{tap_y})",
            "ACTION",
        )

        if attempt == 1:
            tapped = safe_tap(
                "gesture_targets.uw_detail_dismiss",
                require_visible=False,
                retries=1,
                retry_delay=0.2,
                dispatch="now",
            )
            if not tapped:
                tapped = safe_tap(
                    (tap_x, tap_y),
                    require_visible=False,
                    dispatch="now",
                    log_label="uw_detail_fallback",
                )
        else:
            tapped = safe_tap(
                (tap_x, tap_y),
                require_visible=False,
                dispatch="now",
                log_label="uw_detail_fallback",
            )

        if not tapped:
            log("[UW_DETAIL] Tap dispatch failed", "WARN")

        sleep_fn(0.3)
        refreshed = capture_fn()
        if refreshed is None:
            continue
        image = refreshed
        if _tapped_success(image):
            log("[UW_DETAIL] Popup dismissed", "INFO")
            return image

    log("[UW_DETAIL] Unable to dismiss popup after multiple attempts", "WARN")
    return image


__all__ = ["handle_uw_detail_popup"]
