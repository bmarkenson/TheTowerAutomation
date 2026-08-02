"""Guarded scrolling helpers for screen-specific automation flows."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional, Tuple

import numpy as np

from core.input import swipe_now
from core.label_tapper import is_visible
from core.ss_capture import capture_adb_screenshot
from utils.logger import log


Frame = np.ndarray
Region = Tuple[int, int, int, int]
StopFn = Callable[[Frame], Optional[str]]
ActionGuardFn = Callable[[], bool]


@dataclass(frozen=True)
class ScrollResult:
    """Outcome from a bounded, screen-guarded scroll operation."""

    success: bool
    screenshot: Optional[Frame]
    swipes: int
    reason: str


@dataclass(frozen=True)
class ScrollCaptureResult:
    """Outcome from capturing every distinct viewport during a bounded scroll."""

    success: bool
    screenshots: Tuple[Frame, ...]
    swipes: int
    reason: str


def guarded_swipe(
    swipe_key: str,
    *,
    source_label: str,
    screenshot: Optional[Frame] = None,
    settle_s: float = 1.0,
    capture_fn: Callable[[], Optional[Frame]] = capture_adb_screenshot,
    visible_fn: Callable[..., bool] = is_visible,
    swipe_fn: Callable[[str], bool] = swipe_now,
    sleep_fn: Callable[[float], None] = time.sleep,
    action_guard_fn: Optional[ActionGuardFn] = None,
) -> ScrollResult:
    """Send one swipe only if the expected screen is visible before and after."""

    before = screenshot if screenshot is not None else capture_fn()
    if before is None:
        return ScrollResult(False, None, 0, "capture_before_failed")
    if not visible_fn(source_label, screenshot=before):
        log(
            f"[SCROLL] Refusing '{swipe_key}': source '{source_label}' is not visible",
            "DEBUG",
        )
        return ScrollResult(False, before, 0, "wrong_source_screen")
    if action_guard_fn is not None and not action_guard_fn():
        log(
            f"[SCROLL] Refusing '{swipe_key}': action authority was lost",
            "DEBUG",
        )
        return ScrollResult(False, before, 0, "action_authority_lost")
    if not swipe_fn(swipe_key):
        log(f"[SCROLL] Swipe dispatch failed: {swipe_key}", "DEBUG")
        return ScrollResult(False, before, 0, "swipe_dispatch_failed")

    sleep_fn(max(0.0, settle_s))
    after = capture_fn()
    if after is None:
        return ScrollResult(False, None, 1, "capture_after_failed")
    if not visible_fn(source_label, screenshot=after):
        log(
            f"[SCROLL] Source '{source_label}' disappeared after '{swipe_key}'",
            "DEBUG",
        )
        return ScrollResult(False, after, 1, "source_screen_lost")
    return ScrollResult(True, after, 1, "swiped")


def scroll_to_edge(
    swipe_key: str,
    *,
    source_label: str,
    screenshot: Optional[Frame] = None,
    progress_region: Optional[Region] = None,
    max_swipes: int = 8,
    settle_s: float = 1.0,
    stable_threshold: float = 1.0,
    capture_fn: Callable[[], Optional[Frame]] = capture_adb_screenshot,
    visible_fn: Callable[..., bool] = is_visible,
    swipe_fn: Callable[[str], bool] = swipe_now,
    sleep_fn: Callable[[float], None] = time.sleep,
    action_guard_fn: Optional[ActionGuardFn] = None,
) -> ScrollResult:
    """Repeat a guarded swipe until the content no longer moves or the bound is hit."""

    current = screenshot if screenshot is not None else capture_fn()
    if current is None:
        return ScrollResult(False, None, 0, "capture_before_failed")
    if not visible_fn(source_label, screenshot=current):
        log(
            f"[SCROLL] Refusing edge scroll: source '{source_label}' is not visible",
            "DEBUG",
        )
        return ScrollResult(False, current, 0, "wrong_source_screen")

    total_swipes = 0
    for _ in range(max(1, int(max_swipes))):
        step = guarded_swipe(
            swipe_key,
            source_label=source_label,
            screenshot=current,
            settle_s=settle_s,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
            action_guard_fn=action_guard_fn,
        )
        total_swipes += step.swipes
        if not step.success or step.screenshot is None:
            return ScrollResult(False, step.screenshot, total_swipes, step.reason)

        difference = _mean_abs_difference(current, step.screenshot, progress_region)
        current = step.screenshot
        if difference <= max(0.0, stable_threshold):
            log(
                f"[SCROLL] Reached edge with '{swipe_key}' after {total_swipes} swipe(s) "
                f"(difference={difference:.2f})",
                "DEBUG",
            )
            return ScrollResult(True, current, total_swipes, "edge_reached")

    log(
        f"[SCROLL] Edge not reached with '{swipe_key}' after {total_swipes} swipe(s)",
        "DEBUG",
    )
    return ScrollResult(False, current, total_swipes, "max_swipes_exceeded")


def capture_scroll_to_edge(
    swipe_key: str,
    *,
    source_label: str,
    screenshot: Optional[Frame] = None,
    progress_region: Optional[Region] = None,
    max_swipes: int = 16,
    settle_s: float = 1.0,
    stable_threshold: float = 1.0,
    capture_fn: Callable[[], Optional[Frame]] = capture_adb_screenshot,
    visible_fn: Callable[..., bool] = is_visible,
    swipe_fn: Callable[[str], bool] = swipe_now,
    sleep_fn: Callable[[float], None] = time.sleep,
    stop_fn: Optional[StopFn] = None,
    action_guard_fn: Optional[ActionGuardFn] = None,
) -> ScrollCaptureResult:
    """Capture distinct viewports until a proven stop condition or an edge.

    Unlike :func:`scroll_to_edge`, this helper retains the overlapping frames
    encountered along the way.  Callers can therefore reconstruct long pages
    without writing routine screenshots to disk. A caller-supplied stop reason
    is a successful, bounded completion whose final proving frame is retained.
    """

    current = screenshot if screenshot is not None else capture_fn()
    if current is None:
        return ScrollCaptureResult(False, (), 0, "capture_before_failed")
    if not visible_fn(source_label, screenshot=current):
        log(
            f"[SCROLL] Refusing capture scroll: source '{source_label}' is not visible",
            "DEBUG",
        )
        return ScrollCaptureResult(False, (current,), 0, "wrong_source_screen")
    stop_reason = stop_fn(current) if stop_fn is not None else None
    if stop_reason:
        return ScrollCaptureResult(True, (current,), 0, stop_reason)

    screenshots = [current]
    total_swipes = 0
    for _ in range(max(1, int(max_swipes))):
        step = guarded_swipe(
            swipe_key,
            source_label=source_label,
            screenshot=current,
            settle_s=settle_s,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
            action_guard_fn=action_guard_fn,
        )
        total_swipes += step.swipes
        if not step.success or step.screenshot is None:
            return ScrollCaptureResult(
                False,
                tuple(screenshots),
                total_swipes,
                step.reason,
            )
        stop_reason = stop_fn(step.screenshot) if stop_fn is not None else None
        if stop_reason:
            screenshots.append(step.screenshot)
            return ScrollCaptureResult(
                True,
                tuple(screenshots),
                total_swipes,
                stop_reason,
            )

        difference = _mean_abs_difference(current, step.screenshot, progress_region)
        current = step.screenshot
        if difference <= max(0.0, stable_threshold):
            log(
                f"[SCROLL] Captured edge with '{swipe_key}' after {total_swipes} "
                f"swipe(s) ({len(screenshots)} distinct viewport(s), "
                f"difference={difference:.2f})",
                "DEBUG",
            )
            return ScrollCaptureResult(
                True,
                tuple(screenshots),
                total_swipes,
                "edge_reached",
            )

        screenshots.append(current)

    log(
        f"[SCROLL] Capture edge not reached with '{swipe_key}' after "
        f"{total_swipes} swipe(s)",
        "DEBUG",
    )
    return ScrollCaptureResult(
        False,
        tuple(screenshots),
        total_swipes,
        "max_swipes_exceeded",
    )


def scroll_until_visible(
    swipe_key: str,
    *,
    source_label: str,
    target_label: str,
    screenshot: Optional[Frame] = None,
    progress_region: Optional[Region] = None,
    max_swipes: int = 8,
    settle_s: float = 1.0,
    stable_threshold: float = 1.0,
    capture_fn: Callable[[], Optional[Frame]] = capture_adb_screenshot,
    visible_fn: Callable[..., bool] = is_visible,
    swipe_fn: Callable[[str], bool] = swipe_now,
    sleep_fn: Callable[[float], None] = time.sleep,
    stop_fn: Optional[StopFn] = None,
    action_guard_fn: Optional[ActionGuardFn] = None,
) -> ScrollResult:
    """Swipe on a verified source screen until a target or stop condition is seen.

    ``stop_fn`` may return a non-empty reason when the current frame proves that
    scrolling should end without finding the target. This is useful for screens
    where an unavailable state is visually distinct from the actionable state.
    The target check always wins when both are present.
    """

    current = screenshot if screenshot is not None else capture_fn()
    if current is None:
        return ScrollResult(False, None, 0, "capture_before_failed")
    if not visible_fn(source_label, screenshot=current):
        log(
            f"[SCROLL] Refusing target scroll: source '{source_label}' is not visible",
            "DEBUG",
        )
        return ScrollResult(False, current, 0, "wrong_source_screen")
    if visible_fn(target_label, screenshot=current):
        return ScrollResult(True, current, 0, "target_visible")
    stop_reason = stop_fn(current) if stop_fn is not None else None
    if stop_reason:
        return ScrollResult(False, current, 0, stop_reason)

    total_swipes = 0
    for _ in range(max(1, int(max_swipes))):
        step = guarded_swipe(
            swipe_key,
            source_label=source_label,
            screenshot=current,
            settle_s=settle_s,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
            action_guard_fn=action_guard_fn,
        )
        total_swipes += step.swipes
        if not step.success or step.screenshot is None:
            return ScrollResult(False, step.screenshot, total_swipes, step.reason)
        if visible_fn(target_label, screenshot=step.screenshot):
            return ScrollResult(True, step.screenshot, total_swipes, "target_visible")
        stop_reason = stop_fn(step.screenshot) if stop_fn is not None else None
        if stop_reason:
            return ScrollResult(False, step.screenshot, total_swipes, stop_reason)

        difference = _mean_abs_difference(current, step.screenshot, progress_region)
        current = step.screenshot
        if difference <= max(0.0, stable_threshold):
            log(
                f"[SCROLL] Reached an edge before finding '{target_label}'",
                "DEBUG",
            )
            return ScrollResult(False, current, total_swipes, "edge_before_target")

    log(
        f"[SCROLL] Target '{target_label}' not found after {total_swipes} swipe(s)",
        "DEBUG",
    )
    return ScrollResult(False, current, total_swipes, "max_swipes_exceeded")


def _mean_abs_difference(
    before: Frame,
    after: Frame,
    region: Optional[Region],
) -> float:
    if before.shape != after.shape:
        return float("inf")

    before_roi = _crop(before, region)
    after_roi = _crop(after, region)
    if before_roi.size == 0 or after_roi.size == 0 or before_roi.shape != after_roi.shape:
        return float("inf")
    delta = np.abs(before_roi.astype(np.int16) - after_roi.astype(np.int16))
    return float(delta.mean())


def _crop(frame: Frame, region: Optional[Region]) -> Frame:
    if region is None:
        return frame
    x, y, w, h = region
    height, width = frame.shape[:2]
    x1 = max(0, min(int(x), width))
    y1 = max(0, min(int(y), height))
    x2 = max(x1, min(x1 + max(0, int(w)), width))
    y2 = max(y1, min(y1 + max(0, int(h)), height))
    return frame[y1:y2, x1:x2]


__all__ = [
    "ScrollCaptureResult",
    "ScrollResult",
    "capture_scroll_to_edge",
    "guarded_swipe",
    "scroll_to_edge",
    "scroll_until_visible",
]
