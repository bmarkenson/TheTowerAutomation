"""Guarded Poison Swamp detail inspection and dynamic Stun correction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import time
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.free_upgrade_locks import measure_workshop_upgrade_menu
from core.input import TapVerification, safe_tap, tap_if_visible
from core.matcher import get_match_result
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import detect_visible_boxes
from core.upgrade_navigation import swipe_upgrade_menu
from handlers.dismiss_uw_detail import handle_upgrade_detail_popup
from utils.ocr_utils import ocr_text_and_conf

try:
    import pytesseract
except ImportError:  # pragma: no cover - production packages include Tesseract
    pytesseract = None


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]

_WORKSHOP_HEADER_REGION = (20, 400, 680, 90)
_WORKSHOP_CONTENT_REGION = (0, 490, 1080, 1125)
_WORKSHOP_CONTENT_BOTTOM = 1615
_MIN_WORKSHOP_OCR_CONFIDENCE = 65.0


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


@dataclass(frozen=True)
class WorkshopPoisonSwampSource:
    """Localized, non-purchase action target on the Home Workshop card."""

    visible: bool
    header_text: str
    header_confidence: float
    title_text: str
    title_confidence: float
    title_region: Optional[tuple[int, int, int, int]]
    icon_region: Optional[tuple[int, int, int, int]]
    icon_point: Optional[tuple[int, int]]


class PoisonSwampStunError(RuntimeError):
    pass


def _normalize_text(text: Any) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", str(text or "").upper()))


def _default_word_data(crop: Frame) -> Mapping[str, Any]:
    if pytesseract is None:
        return {}
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return pytesseract.image_to_data(
        rgb,
        config="--psm 11",
        output_type=pytesseract.Output.DICT,
    )


def locate_workshop_poison_swamp_source(
    screenshot: Frame,
    *,
    text_fn: Callable[[Frame], tuple[str, float]] = lambda crop: (
        ocr_text_and_conf(crop, psm=7)
    ),
    word_data_fn: Callable[[Frame], Mapping[str, Any]] = _default_word_data,
) -> WorkshopPoisonSwampSource:
    """Locate the Poison Swamp icon only on Workshop Ultimate Upgrades."""

    empty = WorkshopPoisonSwampSource(
        False,
        "",
        -1.0,
        "",
        -1.0,
        None,
        None,
        None,
    )
    if not _frame_complete(screenshot):
        return empty
    header = _crop(screenshot, _WORKSHOP_HEADER_REGION)
    content = _crop(screenshot, _WORKSHOP_CONTENT_REGION)
    if header is None or content is None:
        return empty
    header_text, header_confidence = text_fn(header)
    if (
        _normalize_text(header_text) != "ULTIMATE UPGRADES"
        or float(header_confidence) < _MIN_WORKSHOP_OCR_CONFIDENCE
    ):
        return WorkshopPoisonSwampSource(
            False,
            str(header_text or ""),
            float(header_confidence),
            "",
            -1.0,
            None,
            None,
            None,
        )

    data = word_data_fn(content)
    words = list(data.get("text") or ())
    confidences = list(data.get("conf") or ())
    lefts = list(data.get("left") or ())
    tops = list(data.get("top") or ())
    widths = list(data.get("width") or ())
    heights = list(data.get("height") or ())
    candidates: list[
        tuple[tuple[int, int, int, int], float, str]
    ] = []
    count = min(
        len(words),
        len(confidences),
        len(lefts),
        len(tops),
        len(widths),
        len(heights),
    )
    for index in range(max(0, count - 1)):
        first = _normalize_text(words[index])
        second = _normalize_text(words[index + 1])
        if first != "POISON" or second != "SWAMP":
            continue
        try:
            first_confidence = float(confidences[index])
            second_confidence = float(confidences[index + 1])
            first_x = int(lefts[index])
            first_y = int(tops[index])
            first_width = int(widths[index])
            first_height = int(heights[index])
            second_x = int(lefts[index + 1])
            second_y = int(tops[index + 1])
            second_width = int(widths[index + 1])
            second_height = int(heights[index + 1])
        except (TypeError, ValueError):
            continue
        confidence = min(first_confidence, second_confidence)
        vertical_overlap = min(
            first_y + first_height,
            second_y + second_height,
        ) - max(first_y, second_y)
        if (
            confidence < _MIN_WORKSHOP_OCR_CONFIDENCE
            or vertical_overlap <= 0
            or second_x < first_x + first_width - 12
            or second_x - (first_x + first_width) > 60
        ):
            continue
        x = _WORKSHOP_CONTENT_REGION[0] + first_x
        y = _WORKSHOP_CONTENT_REGION[1] + min(first_y, second_y)
        right = _WORKSHOP_CONTENT_REGION[0] + second_x + second_width
        bottom = _WORKSHOP_CONTENT_REGION[1] + max(
            first_y + first_height,
            second_y + second_height,
        )
        candidates.append(
            ((x, y, right - x, bottom - y), confidence, "Poison Swamp")
        )

    if len(candidates) != 1:
        return WorkshopPoisonSwampSource(
            False,
            str(header_text or ""),
            float(header_confidence),
            "",
            -1.0,
            None,
            None,
            None,
        )
    title_region, title_confidence, title_text = candidates[0]
    card_top = title_region[1] - 35
    icon_region = (60, card_top + 75, 190, 190)
    icon_bottom = icon_region[1] + icon_region[3]
    if icon_region[1] < _WORKSHOP_CONTENT_REGION[1] or (
        icon_bottom > _WORKSHOP_CONTENT_BOTTOM
    ):
        return WorkshopPoisonSwampSource(
            False,
            str(header_text or ""),
            float(header_confidence),
            title_text,
            title_confidence,
            title_region,
            None,
            None,
        )
    return WorkshopPoisonSwampSource(
        True,
        str(header_text or ""),
        float(header_confidence),
        title_text,
        title_confidence,
        title_region,
        icon_region,
        (
            icon_region[0] + icon_region[2] // 2,
            icon_region[1] + icon_region[3] // 2,
        ),
    )


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


def ensure_poison_swamp_stun(
    screenshot: Optional[Frame] = None,
    *,
    required_state: PoisonSwampStunState | str,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    detect_boxes_fn: Callable[..., Mapping[str, list[Any]]] = detect_visible_boxes,
    locate_workshop_fn: Callable[
        [Frame], WorkshopPoisonSwampSource
    ] = locate_workshop_poison_swamp_source,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    dismiss_fn: Callable[..., Optional[Frame]] = handle_upgrade_detail_popup,
    measure_fn: Callable[[Frame], PoisonSwampStunEvidence] = measure_poison_swamp_stun,
    measure_workshop_menu_fn: Callable[
        [Optional[Frame]], Optional[str]
    ] = measure_workshop_upgrade_menu,
    swipe_fn: Callable[[str, str], Any] = swipe_upgrade_menu,
    repair_observer_fn: Callable[[], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> PoisonSwampStunResult:
    """Ensure Stun has the required state from Workshop or the active UW menu."""

    del screenshot  # Actions always reacquire a fresh UW source frame.
    try:
        required = PoisonSwampStunState(
            str(
                required_state.value
                if isinstance(required_state, PoisonSwampStunState)
                else required_state
            ).strip().lower()
        )
    except ValueError as exc:
        raise PoisonSwampStunError(
            f"unsupported Poison Swamp Stun requirement {required_state!r}"
        ) from exc
    if required is PoisonSwampStunState.UNKNOWN:
        raise PoisonSwampStunError(
            "Poison Swamp Stun requirement cannot be unknown"
        )

    current = _capture_complete(capture_fn, sleep_fn)
    detection = detector(current)
    source_kind: str
    target_point: tuple[int, int]
    target_region: tuple[int, int, int, int]
    source_verifier: Callable[[Frame], bool]
    if detection.get("state") == "RUNNING" and detection.get("menu") == "UW_MENU":
        source_kind = "battle"
        boxes = _battle_poison_swamp_boxes(current, detect_boxes_fn)
        if len(boxes) != 1:
            raise PoisonSwampStunError(
                f"expected one visible Poison Swamp tile, found {len(boxes)}"
            )
        x, y, width, height = boxes[0].rect
        target_point = (int(x + 0.30 * width), int(y + 0.25 * height))
        target_region = boxes[0].rect
        source_verifier = lambda frame: (
            (candidate_detection := detector(frame)).get("state") == "RUNNING"
            and candidate_detection.get("menu") == "UW_MENU"
            and len(_battle_poison_swamp_boxes(frame, detect_boxes_fn)) == 1
        )
    elif detection.get("state") == "WORKSHOP":
        source_kind = "workshop"
        current, source = _locate_workshop_poison_swamp(
            current,
            capture_fn=capture_fn,
            detector=detector,
            locate_workshop_fn=locate_workshop_fn,
            measure_workshop_menu_fn=measure_workshop_menu_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        if source.icon_point is None or source.icon_region is None:
            raise PoisonSwampStunError(
                "Poison Swamp Workshop icon was not actionable"
            )
        target_point = source.icon_point
        target_region = source.icon_region

        def source_verifier(frame: Frame) -> bool:
            candidate_detection = detector(frame)
            candidate = locate_workshop_fn(frame)
            return bool(
                candidate_detection.get("state") == "WORKSHOP"
                and "UPGRADE_DETAIL"
                not in set(candidate_detection.get("overlays") or ())
                and measure_workshop_menu_fn(frame) == "ultimate weapons"
                and candidate.visible
                and candidate.icon_point is not None
                and abs(candidate.icon_point[0] - target_point[0]) <= 20
                and abs(candidate.icon_point[1] - target_point[1]) <= 20
            )

    else:
        raise PoisonSwampStunError(
            "Poison Swamp correction requires WORKSHOP Ultimate Upgrades "
            "or RUNNING/UW_MENU"
        )

    if not safe_tap_fn(
        target_point,
        dispatch="now",
        log_label="uw_detail:Poison Swamp",
        verification=TapVerification(
            screenshot=current,
            target_region=target_region,
            description=(
                "ultimate_weapon:Poison Swamp"
                if source_kind == "battle"
                else "workshop:ultimate_weapon:Poison Swamp"
            ),
            verifier=source_verifier,
        ),
    ):
        raise PoisonSwampStunError("Poison Swamp detail tap failed")

    detail, evidence = _wait_for_stun(
        capture_fn,
        measure_fn,
        sleep_fn,
    )
    changed = False
    if evidence.state is PoisonSwampStunState.UNKNOWN:
        raise PoisonSwampStunError("Poison Swamp Stun state was ambiguous")
    if evidence.state is not required:
        visible_control = (
            "buttons.poison_swamp_stun_on"
            if evidence.state is PoisonSwampStunState.ON
            else "buttons.poison_swamp_stun_off"
        )
        if repair_observer_fn is not None:
            repair_observer_fn()
        if not tap_visible_fn(visible_control, screenshot=detail):
            raise PoisonSwampStunError(
                f"verified Stun-{evidence.state.value} checkbox tap failed"
            )
        changed = True
        detail, evidence = _wait_for_stun(
            capture_fn,
            measure_fn,
            sleep_fn,
            expected=required,
        )

    cleared = dismiss_fn(
        screenshot=detail,
        capture_fn=capture_fn,
        sleep_fn=sleep_fn,
    )
    if cleared is None or not _frame_complete(cleared):
        raise PoisonSwampStunError("Poison Swamp detail dismissal was not captured")
    cleared_detection = detector(cleared)
    cleared_overlays = set(cleared_detection.get("overlays") or ())
    if source_kind == "battle":
        restored = bool(
            cleared_detection.get("state") == "RUNNING"
            and cleared_detection.get("menu") == "UW_MENU"
            and "UPGRADE_DETAIL" not in cleared_overlays
        )
    else:
        restored = bool(
            cleared_detection.get("state") == "WORKSHOP"
            and measure_workshop_menu_fn(cleared) == "ultimate weapons"
            and "UPGRADE_DETAIL" not in cleared_overlays
        )
    if not restored:
        raise PoisonSwampStunError("Poison Swamp detail dismissal was not verified")
    return PoisonSwampStunResult(cleared, evidence, changed)


def ensure_poison_swamp_stun_off(
    screenshot: Optional[Frame] = None,
    **kwargs: Any,
) -> PoisonSwampStunResult:
    """Backward-compatible Farm helper for the Stun-off requirement."""

    return ensure_poison_swamp_stun(
        screenshot,
        required_state=PoisonSwampStunState.OFF,
        **kwargs,
    )


def _battle_poison_swamp_boxes(
    frame: Frame,
    detect_boxes_fn: Callable[..., Mapping[str, list[Any]]],
) -> list[Any]:
    return [
        box
        for column in detect_boxes_fn(frame, menu="ultimate weapons").values()
        for box in (column or [])
        if str(getattr(box, "text", "") or "").strip().lower()
        == "poison swamp"
    ]


def _require_workshop_ultimate(
    frame: Frame,
    *,
    detector: Detector,
    measure_workshop_menu_fn: Callable[[Optional[Frame]], Optional[str]],
) -> None:
    detection = detector(frame)
    if (
        detection.get("state") != "WORKSHOP"
        or set(detection.get("overlays") or ())
        or measure_workshop_menu_fn(frame) != "ultimate weapons"
    ):
        raise PoisonSwampStunError(
            "expected unobscured WORKSHOP Ultimate Upgrades"
        )


def _locate_workshop_poison_swamp(
    current: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    locate_workshop_fn: Callable[[Frame], WorkshopPoisonSwampSource],
    measure_workshop_menu_fn: Callable[[Optional[Frame]], Optional[str]],
    swipe_fn: Callable[[str, str], Any],
    sleep_fn: Callable[[float], None],
) -> tuple[Frame, WorkshopPoisonSwampSource]:
    _require_workshop_ultimate(
        current,
        detector=detector,
        measure_workshop_menu_fn=measure_workshop_menu_fn,
    )
    source = locate_workshop_fn(current)
    if source.visible:
        return current, source

    for _ in range(3):
        _require_workshop_ultimate(
            current,
            detector=detector,
            measure_workshop_menu_fn=measure_workshop_menu_fn,
        )
        swipe_fn("towards_top", "extended")
        sleep_fn(0.45)
        current = _capture_complete(capture_fn, sleep_fn)

    for position in range(9):
        _require_workshop_ultimate(
            current,
            detector=detector,
            measure_workshop_menu_fn=measure_workshop_menu_fn,
        )
        source = locate_workshop_fn(current)
        if source.visible:
            return current, source
        if position < 8:
            swipe_fn("towards_bottom", "medium")
            sleep_fn(0.45)
            current = _capture_complete(capture_fn, sleep_fn)
    raise PoisonSwampStunError(
        "could not locate Poison Swamp in Workshop Ultimate Upgrades"
    )


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


def _crop(
    frame: Optional[Frame],
    region: tuple[int, int, int, int],
) -> Optional[Frame]:
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim < 2:
        return None
    x, y, width, height = region
    if x < 0 or y < 0 or x + width > frame.shape[1] or y + height > frame.shape[0]:
        return None
    crop = frame[y : y + height, x : x + width]
    return crop if crop.size else None


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
    "WorkshopPoisonSwampSource",
    "ensure_poison_swamp_stun",
    "ensure_poison_swamp_stun_off",
    "locate_workshop_poison_swamp_source",
    "measure_poison_swamp_stun",
]
