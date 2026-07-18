"""Guarded Poison Swamp detail inspection and dynamic Stun correction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.input import safe_tap, tap_if_visible
from core.matcher import get_match_result
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import detect_visible_boxes
from handlers.dismiss_uw_detail import handle_upgrade_detail_popup


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]


class PoisonSwampStunState(str, Enum):
    OFF = "off"
    ON = "on"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PoisonSwampStunEvidence:
    state: PoisonSwampStunState
    detail_visible: bool
    detail_confidence: float
    off_confidence: float
    on_confidence: float


@dataclass(frozen=True)
class PoisonSwampStunResult:
    screenshot: Frame
    evidence: PoisonSwampStunEvidence
    changed: bool


class PoisonSwampStunError(RuntimeError):
    pass


def measure_poison_swamp_stun(screenshot: Frame) -> PoisonSwampStunEvidence:
    """Classify the Stun checkbox only on a verified Poison Swamp detail."""

    detail = get_match_result("overlays.poison_swamp_detail", screenshot=screenshot)
    off = get_match_result("indicators.poison_swamp_stun_off", screenshot=screenshot)
    on = get_match_result("buttons.poison_swamp_stun_on", screenshot=screenshot)
    detail_visible = detail.matched
    if detail_visible and off.matched and not on.matched:
        state = PoisonSwampStunState.OFF
    elif detail_visible and on.matched and not off.matched:
        state = PoisonSwampStunState.ON
    else:
        state = PoisonSwampStunState.UNKNOWN
    return PoisonSwampStunEvidence(
        state=state,
        detail_visible=detail_visible,
        detail_confidence=detail.confidence,
        off_confidence=off.confidence,
        on_confidence=on.confidence,
    )


def ensure_poison_swamp_stun_off(
    screenshot: Optional[Frame] = None,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    detect_boxes_fn: Callable[..., Mapping[str, list[Any]]] = detect_visible_boxes,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    dismiss_fn: Callable[..., Optional[Frame]] = handle_upgrade_detail_popup,
    measure_fn: Callable[[Frame], PoisonSwampStunEvidence] = measure_poison_swamp_stun,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> PoisonSwampStunResult:
    """Ensure Poison Swamp Stun is off without ending the active battle."""

    del screenshot  # Actions always reacquire a fresh UW source frame.
    current = _capture_complete(capture_fn, sleep_fn)
    detection = detector(current)
    if detection.get("state") != "RUNNING" or detection.get("menu") != "UW_MENU":
        raise PoisonSwampStunError("Poison Swamp correction requires RUNNING/UW_MENU")

    boxes = [
        box
        for column in detect_boxes_fn(current, menu="ultimate weapons").values()
        for box in (column or [])
        if str(getattr(box, "text", "") or "").strip().lower() == "poison swamp"
    ]
    if len(boxes) != 1:
        raise PoisonSwampStunError(
            f"expected one visible Poison Swamp tile, found {len(boxes)}"
        )
    x, y, width, height = boxes[0].rect
    title_point = (int(x + 0.30 * width), int(y + 0.25 * height))
    if not safe_tap_fn(
        title_point,
        require_visible=False,
        dispatch="now",
        log_label="uw_detail:Poison Swamp",
    ):
        raise PoisonSwampStunError("Poison Swamp detail tap failed")

    detail, evidence = _wait_for_stun(
        capture_fn,
        measure_fn,
        sleep_fn,
    )
    changed = False
    if evidence.state is PoisonSwampStunState.ON:
        if not tap_visible_fn("buttons.poison_swamp_stun_on", screenshot=detail):
            raise PoisonSwampStunError("verified Stun-on checkbox tap failed")
        changed = True
        detail, evidence = _wait_for_stun(
            capture_fn,
            measure_fn,
            sleep_fn,
            expected=PoisonSwampStunState.OFF,
        )
    elif evidence.state is not PoisonSwampStunState.OFF:
        raise PoisonSwampStunError("Poison Swamp Stun state was ambiguous")

    cleared = dismiss_fn(
        screenshot=detail,
        capture_fn=capture_fn,
        sleep_fn=sleep_fn,
    )
    if cleared is None or not _frame_complete(cleared):
        raise PoisonSwampStunError("Poison Swamp detail dismissal was not captured")
    cleared_detection = detector(cleared)
    if (
        cleared_detection.get("state") != "RUNNING"
        or cleared_detection.get("menu") != "UW_MENU"
        or "UPGRADE_DETAIL" in set(cleared_detection.get("overlays") or ())
    ):
        raise PoisonSwampStunError("Poison Swamp detail dismissal was not verified")
    return PoisonSwampStunResult(cleared, evidence, changed)


def _wait_for_stun(
    capture_fn: Capture,
    measure_fn: Callable[[Frame], PoisonSwampStunEvidence],
    sleep_fn: Callable[[float], None],
    *,
    expected: Optional[PoisonSwampStunState] = None,
) -> tuple[Frame, PoisonSwampStunEvidence]:
    last: Optional[PoisonSwampStunEvidence] = None
    for _ in range(16):
        frame = capture_fn()
        if frame is not None and _frame_complete(frame):
            last = measure_fn(frame)
            if last.detail_visible and last.state is not PoisonSwampStunState.UNKNOWN:
                if expected is None or last.state is expected:
                    return frame, last
        sleep_fn(0.25)
    state = last.state.value if last is not None else "uncaptured"
    raise PoisonSwampStunError(f"timed out verifying Poison Swamp Stun ({state})")


def _capture_complete(
    capture_fn: Capture,
    sleep_fn: Callable[[float], None],
) -> Frame:
    for _ in range(4):
        frame = capture_fn()
        if frame is not None and _frame_complete(frame):
            return frame
        sleep_fn(0.2)
    raise PoisonSwampStunError("Poison Swamp capture was incomplete")


def _frame_complete(frame: Frame) -> bool:
    return bool(
        isinstance(frame, np.ndarray)
        and frame.ndim == 3
        and frame.shape[0] >= 1920
        and frame.shape[1] >= 1080
        and float(np.mean(np.max(frame[:, :, :3], axis=2) < 8)) < 0.5
    )


__all__ = [
    "PoisonSwampStunError",
    "PoisonSwampStunEvidence",
    "PoisonSwampStunResult",
    "PoisonSwampStunState",
    "ensure_poison_swamp_stun_off",
    "measure_poison_swamp_stun",
]
