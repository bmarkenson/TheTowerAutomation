"""Fast, exclusive EHLS -> EALS initialization for new GC runs."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from core.adb_utils import adb_shell
from core.clickmap_access import resolve_dot_path
from core.input import safe_tap
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import UpgradeBox, detect_visible_boxes
from core.upgrade_buy_quantity import detect_current_buy_quantity, ensure_buy_quantity
from utils.logger import log, log_mission
from utils.wave_detector import detect_wave_number_from_image


Frame = np.ndarray
Point = Tuple[int, int]
EHLS = "Enemy Health Level Skip"
EALS = "Enemy Attack Level Skip"
TARGET_COLUMN = {EHLS: "left", EALS: "right"}


@dataclass(frozen=True)
class LevelSkipInitializationResult:
    success: bool
    ehls_maxed: bool
    eals_maxed: bool
    elapsed_s: float
    ehls_wave: Optional[int]
    eals_wave: Optional[int]
    taps_sent: int
    reason: str
    eals_first_tap_wave: Optional[int] = None
    eals_first_tap_elapsed_s: Optional[float] = None


def _default_tap(point: Point, *, label: str) -> bool:
    return safe_tap(
        point,
        require_visible=False,
        dispatch="now",
        log_label=label,
    )


def _default_scroll_to_bottom() -> bool:
    entry = resolve_dot_path("_shared_match_regions.upgrade_menu_area")
    region = entry.get("match_region") if isinstance(entry, dict) else None
    if not isinstance(region, dict):
        return False
    x = int(region["x"]) + int(region["w"]) // 2
    y = int(region["y"])
    h = int(region["h"])
    result = adb_shell(
        [
            "input",
            "swipe",
            str(x),
            str(y + int(h * 0.82)),
            str(x),
            str(y + int(h * 0.22)),
            "220",
        ],
        capture_output=False,
        check=False,
    )
    return bool(result and result.returncode == 0)


def _is_verified_utility(frame: Frame) -> bool:
    detection = detect_state_and_overlays(frame)
    return detection.get("state") == "RUNNING" and detection.get("menu") == "UTILITY_MENU"


def _target_boxes(frame: Frame) -> Dict[str, UpgradeBox]:
    boxes = detect_visible_boxes(frame, menu="utility")
    found: Dict[str, UpgradeBox] = {}
    for target, column in TARGET_COLUMN.items():
        for box in boxes.get(column, []) or []:
            if (box.text or "").strip().lower() == target.lower():
                found[target] = box
                break
    return found


def _purchase_point(box: UpgradeBox) -> Point:
    x, y, w, h = box.rect
    if w <= 0 or h <= 0:
        raise ValueError("Invalid level-skip upgrade box")
    return x + int(w * 0.78), y + int(h * 0.70)


def _wave(frame: Frame) -> Optional[int]:
    try:
        value, _confidence = detect_wave_number_from_image(frame)
        return value
    except Exception:
        return None


def initialize_level_skips(
    *,
    screenshot: Optional[Frame] = None,
    capture_fn: Callable[[], Optional[Frame]] = capture_adb_screenshot,
    tap_fn: Callable[..., bool] = _default_tap,
    scroll_fn: Callable[[], bool] = _default_scroll_to_bottom,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    timeout_s: float = 35.0,
    target_wave: int = 40,
    taps_per_burst: int = 1,
) -> LevelSkipInitializationResult:
    """Gold-box EHLS then EALS with minimum capture and navigation overhead."""

    started = monotonic_fn()
    deadline = started + max(5.0, float(timeout_s))
    taps_sent = 0
    completion_waves: Dict[str, Optional[int]] = {EHLS: None, EALS: None}
    maxed = {EHLS: False, EALS: False}
    eals_first_tap_wave: Optional[int] = None
    eals_first_tap_elapsed_s: Optional[float] = None

    def finish(reason: str) -> LevelSkipInitializationResult:
        return _result(
            started,
            monotonic_fn,
            maxed,
            completion_waves,
            taps_sent,
            reason,
            eals_first_tap_wave=eals_first_tap_wave,
            eals_first_tap_elapsed_s=eals_first_tap_elapsed_s,
        )

    frame = screenshot if screenshot is not None else capture_fn()
    if frame is None:
        return finish("capture_failed")

    detection = detect_state_and_overlays(frame)
    if detection.get("state") != "RUNNING":
        return finish("not_running")

    if detection.get("menu") != "UTILITY_MENU":
        if not safe_tap(
            "navigation.goto_utility",
            require_visible=False,
            dispatch="now",
        ):
            return finish("utility_nav_failed")
        sleep_fn(0.12)
        frame = capture_fn()
        if frame is None or not _is_verified_utility(frame):
            return finish("utility_not_verified")
    elif not _is_verified_utility(frame):
        return finish("utility_not_verified")

    quantity = detect_current_buy_quantity(screenshot=frame)
    if quantity != "max":
        log_mission(
            f"[RUN_INIT] Utility buy quantity is {quantity!r}; setting Max before level skips",
            "INFO",
        )
        try:
            frame = ensure_buy_quantity(
                "max",
                screenshot=frame,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
            )
        except Exception as exc:
            log(f"[RUN_INIT] Unable to set Max buy quantity: {exc}", "WARN")
            return finish("max_quantity_failed")
        if frame is None or not _is_verified_utility(frame):
            return finish("utility_lost_after_quantity")

    boxes = _target_boxes(frame)
    for _attempt in range(3):
        if EHLS in boxes and EALS in boxes:
            break
        if not _is_verified_utility(frame):
            return finish("utility_lost_before_scroll")
        if not scroll_fn():
            return finish("utility_scroll_failed")
        sleep_fn(0.12)
        frame = capture_fn()
        if frame is None:
            return finish("capture_after_scroll_failed")
        boxes = _target_boxes(frame)

    if EHLS not in boxes or EALS not in boxes:
        missing = [target for target in (EHLS, EALS) if target not in boxes]
        log(f"[RUN_INIT] Level-skip boxes not found together: {missing}", "WARN")
        return finish("level_skip_boxes_missing")

    for target in (EHLS, EALS):
        handoff_tap_sent = False
        while monotonic_fn() < deadline:
            if not _is_verified_utility(frame):
                return finish("utility_screen_lost")

            boxes = _target_boxes(frame)
            box = boxes.get(target)
            if box is None:
                return finish(f"{target}_not_visible")
            if (box.affordability or "").lower() == "maxed":
                maxed[target] = True

                # EHLS and EALS are deliberately kept in view together. Once
                # EHLS is verified Max, dispatch the first EALS purchase from
                # this same verified frame before wave OCR, logging, another
                # state scan, or another screenshot can consume the handoff.
                observed_wave: Optional[int] = None
                if target == EHLS:
                    eals_box = boxes.get(EALS)
                    if eals_box is None:
                        return finish(f"{EALS}_not_visible_at_handoff")
                    if (eals_box.affordability or "").lower() != "maxed":
                        if not tap_fn(_purchase_point(eals_box), label=f"level_skip:{EALS}"):
                            return finish("eals_handoff_tap_dispatch_failed")
                        taps_sent += 1
                        handoff_tap_sent = True
                        eals_first_tap_elapsed_s = monotonic_fn() - started
                        observed_wave = _wave(frame)
                        eals_first_tap_wave = observed_wave
                        log_mission(
                            f"[RUN_INIT] EALS first tap dispatched immediately after EHLS "
                            f"at {eals_first_tap_elapsed_s:.2f}s wave={eals_first_tap_wave}",
                            "INFO",
                        )

                completion_waves[target] = observed_wave if observed_wave is not None else _wave(frame)
                elapsed = monotonic_fn() - started
                log_mission(
                    f"[RUN_INIT] {target} gold boxed in {elapsed:.2f}s "
                    f"at wave={completion_waves[target]}",
                    "INFO",
                )
                if completion_waves[target] is not None and completion_waves[target] > target_wave:
                    log(
                        f"[RUN_INIT] {target} missed wave-{target_wave} objective "
                        f"(completed at wave {completion_waves[target]})",
                        "WARN",
                    )
                break

            point = _purchase_point(box)
            for _ in range(max(1, int(taps_per_burst))):
                if not tap_fn(point, label=f"level_skip:{target}"):
                    return finish("tap_dispatch_failed")
                taps_sent += 1
                if target == EALS and eals_first_tap_elapsed_s is None:
                    eals_first_tap_elapsed_s = monotonic_fn() - started
                    eals_first_tap_wave = _wave(frame)
                sleep_fn(0.04)

            frame = capture_fn()
            if frame is None:
                return finish("capture_after_tap_failed")
        else:
            return finish(f"{target}_timeout")

        if handoff_tap_sent:
            frame = capture_fn()
            if frame is None:
                return finish("capture_after_eals_handoff_failed")

    return finish("complete")


def _result(
    started: float,
    monotonic_fn: Callable[[], float],
    maxed: Dict[str, bool],
    waves: Dict[str, Optional[int]],
    taps_sent: int,
    reason: str,
    *,
    eals_first_tap_wave: Optional[int] = None,
    eals_first_tap_elapsed_s: Optional[float] = None,
) -> LevelSkipInitializationResult:
    return LevelSkipInitializationResult(
        success=bool(maxed[EHLS] and maxed[EALS]),
        ehls_maxed=bool(maxed[EHLS]),
        eals_maxed=bool(maxed[EALS]),
        elapsed_s=max(0.0, monotonic_fn() - started),
        ehls_wave=waves[EHLS],
        eals_wave=waves[EALS],
        taps_sent=taps_sent,
        reason=reason,
        eals_first_tap_wave=eals_first_tap_wave,
        eals_first_tap_elapsed_s=eals_first_tap_elapsed_s,
    )


__all__ = [
    "EALS",
    "EHLS",
    "LevelSkipInitializationResult",
    "initialize_level_skips",
]
