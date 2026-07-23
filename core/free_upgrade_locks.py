"""Inspect and enforce Farm Free Upgrade locks from the Home Workshop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import re
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.input import TapVerification, safe_tap
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import UpgradeBox, detect_visible_boxes
from core.upgrade_navigation import swipe_upgrade_menu
from utils.logger import log
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]

FARM_FREE_UPGRADE_LOCKS = (
    "Shockwave Size",
    "Bounce Shot Targets",
    "Bounce Shot Range",
)

_LOCK_SPECS = {
    "Shockwave Size": ("defense", "left"),
    "Bounce Shot Targets": ("attack", "right"),
    "Bounce Shot Range": ("attack", "left"),
}
_WORKSHOP_MENU_ACTIONS = {
    "attack": "navigation.workshop:attack",
    "defense": "navigation.workshop:defense",
}
_WORKSHOP_UPGRADE_ACTION = "navigation.workshop:upgrade"

_WORKSHOP_MENU_TITLE_REGION = (20, 400, 680, 90)
_WORKSHOP_UPGRADE_COLUMN_REGIONS = {
    "left": (26, 490, 511, 1125),
    "right": (546, 490, 513, 1125),
}
_DETAIL_TITLE_REGION = (260, 300, 570, 110)
_LOCK_LABEL_REGION = (300, 950, 350, 100)
_CHECKBOX_REGION = (205, 945, 105, 105)
_CHECKMARK_REGION = (230, 970, 55, 55)
_RANGE_UPGRADES_UNLOCK_REGION = (20, 800, 1040, 500)
_MIN_TITLE_CONFIDENCE = 70.0
_MIN_UNAVAILABLE_CONFIDENCE = 45.0
_MIN_CHECKBOX_OUTLINE_PIXELS = 1_000
_MIN_CHECKMARK_PIXELS = 200


class FreeUpgradeLockState(str, Enum):
    CHECKED = "checked"
    UNCHECKED = "unchecked"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FreeUpgradeLockEvidence:
    label: str
    state: FreeUpgradeLockState
    title_text: str
    title_confidence: float
    lock_text: str
    lock_confidence: float
    checkbox_outline_pixels: int
    checkmark_pixels: int

    @property
    def valid(self) -> bool:
        return self.state is FreeUpgradeLockState.CHECKED

    @property
    def authoritative_mismatch(self) -> bool:
        return self.state is FreeUpgradeLockState.UNCHECKED

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["valid"] = self.valid
        payload["authoritative_mismatch"] = self.authoritative_mismatch
        return payload


@dataclass(frozen=True)
class FreeUpgradeLocksEvidence:
    locks: tuple[FreeUpgradeLockEvidence, ...]

    @property
    def valid(self) -> bool:
        return bool(self.locks) and all(lock.valid for lock in self.locks)

    @property
    def has_authoritative_mismatch(self) -> bool:
        return any(lock.authoritative_mismatch for lock in self.locks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "locks": [lock.as_dict() for lock in self.locks],
            "valid": self.valid,
            "has_authoritative_mismatch": self.has_authoritative_mismatch,
        }


@dataclass(frozen=True)
class FreeUpgradeLockInspectionResult:
    evidence: FreeUpgradeLocksEvidence
    screenshot: Frame
    changed_labels: tuple[str, ...] = ()


class FreeUpgradeLockInspectionError(RuntimeError):
    pass


def normalize_free_upgrade_lock_requirements(
    raw: Any,
    *,
    require_farm_set: bool = False,
) -> tuple[str, ...]:
    """Return supported lock labels with duplicates and unknowns rejected."""

    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("free_upgrade_locks must be a list")
    labels = tuple(str(label or "").strip() for label in raw)
    if not labels or any(not label for label in labels):
        raise ValueError("free_upgrade_locks must contain non-empty labels")
    if len(set(labels)) != len(labels):
        raise ValueError("free_upgrade_locks cannot contain duplicates")
    unsupported = [label for label in labels if label not in _LOCK_SPECS]
    if unsupported:
        raise ValueError(
            "unsupported Free Upgrade locks: " + ", ".join(unsupported)
        )
    if require_farm_set and set(labels) != set(FARM_FREE_UPGRADE_LOCKS):
        raise ValueError(
            "Farm free_upgrade_locks must contain Shockwave Size, "
            "Bounce Shot Targets, and Bounce Shot Range"
        )
    return labels


def measure_workshop_upgrade_menu(screenshot: Optional[Frame]) -> Optional[str]:
    """Read the fixed Workshop upgrade-section heading."""

    crop = _crop(screenshot, _WORKSHOP_MENU_TITLE_REGION)
    if crop is None:
        return None
    text, confidence = ocr_text_and_conf(crop, psm=7)
    normalized = _normalize_text(text)
    if confidence < _MIN_TITLE_CONFIDENCE:
        return None
    if normalized == "ATTACK UPGRADES":
        return "attack"
    if normalized == "DEFENSE UPGRADES":
        return "defense"
    return None


def measure_free_upgrade_lock(
    screenshot: Optional[Frame],
    expected_label: str,
) -> FreeUpgradeLockEvidence:
    """Classify one visible upgrade-detail lock without sending input."""

    title_crop = _crop(screenshot, _DETAIL_TITLE_REGION)
    lock_crop = _crop(screenshot, _LOCK_LABEL_REGION)
    checkbox_crop = _crop(screenshot, _CHECKBOX_REGION)
    checkmark_crop = _crop(screenshot, _CHECKMARK_REGION)
    if any(crop is None for crop in (title_crop, lock_crop, checkbox_crop, checkmark_crop)):
        return FreeUpgradeLockEvidence(
            expected_label,
            FreeUpgradeLockState.UNKNOWN,
            "",
            -1.0,
            "",
            -1.0,
            0,
            0,
        )

    title_text, title_confidence = ocr_text_and_conf(title_crop, psm=7)
    lock_text, lock_confidence = ocr_text_and_conf(lock_crop, psm=7)
    checkbox_hsv = cv2.cvtColor(checkbox_crop, cv2.COLOR_BGR2HSV)
    checkmark_hsv = cv2.cvtColor(checkmark_crop, cv2.COLOR_BGR2HSV)
    outline_pixels = int(
        (
            (checkbox_hsv[:, :, 0] >= 75)
            & (checkbox_hsv[:, :, 0] <= 105)
            & (checkbox_hsv[:, :, 1] > 30)
            & (checkbox_hsv[:, :, 2] > 100)
        ).sum()
    )
    checkmark_pixels = int(
        (
            (checkmark_hsv[:, :, 1] < 70)
            & (checkmark_hsv[:, :, 2] > 180)
        ).sum()
    )
    authoritative = (
        title_confidence >= _MIN_TITLE_CONFIDENCE
        and _normalize_text(title_text) == _normalize_text(expected_label)
        and lock_confidence >= _MIN_TITLE_CONFIDENCE
        and _normalize_text(lock_text).startswith("LOCK LEVEL")
        and outline_pixels >= _MIN_CHECKBOX_OUTLINE_PIXELS
    )
    if not authoritative:
        state = FreeUpgradeLockState.UNKNOWN
    elif checkmark_pixels >= _MIN_CHECKMARK_PIXELS:
        state = FreeUpgradeLockState.CHECKED
    else:
        state = FreeUpgradeLockState.UNCHECKED
    return FreeUpgradeLockEvidence(
        expected_label,
        state,
        title_text,
        title_confidence,
        lock_text,
        lock_confidence,
        outline_pixels,
        checkmark_pixels,
    )


def measure_unavailable_free_upgrade_lock(
    screenshot: Optional[Frame],
    expected_label: str,
) -> Optional[FreeUpgradeLockEvidence]:
    """Recognize a lock whose containing Workshop upgrade tree is locked."""

    if expected_label not in {"Bounce Shot Targets", "Bounce Shot Range"}:
        return None
    crop = _crop(screenshot, _RANGE_UPGRADES_UNLOCK_REGION)
    if crop is None:
        return None
    text, confidence = ocr_text_and_conf(crop, psm=6)
    if (
        confidence < _MIN_UNAVAILABLE_CONFIDENCE
        or "UNLOCK RANGE UPGRADES" not in _normalize_text(text)
    ):
        return None
    return FreeUpgradeLockEvidence(
        expected_label,
        FreeUpgradeLockState.UNAVAILABLE,
        text,
        confidence,
        "Range Upgrades locked",
        confidence,
        0,
        0,
    )


def inspect_free_upgrade_locks(
    requirements: Any,
    *,
    screenshot: Optional[Frame] = None,
    enforce: bool = False,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    swipe_fn: Callable[[str, str], None] = swipe_upgrade_menu,
    detect_boxes_fn: Callable[..., Mapping[str, list[UpgradeBox]]] = detect_visible_boxes,
    measure_menu_fn: Callable[[Optional[Frame]], Optional[str]] = measure_workshop_upgrade_menu,
    measure_lock_fn: Callable[
        [Optional[Frame], str], FreeUpgradeLockEvidence
    ] = measure_free_upgrade_lock,
    measure_unavailable_fn: Callable[
        [Optional[Frame], str], Optional[FreeUpgradeLockEvidence]
    ] = measure_unavailable_free_upgrade_lock,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> FreeUpgradeLockInspectionResult:
    """Inspect supported Workshop locks, optionally checking verified mismatches."""

    labels = normalize_free_upgrade_lock_requirements(requirements)
    current = screenshot if screenshot is not None else capture_fn()
    evidence: list[FreeUpgradeLockEvidence] = []
    changed: list[str] = []
    detail_open = False
    try:
        current = _require_workshop(current, detector, measure_menu_fn, menu=None)
        for label in labels:
            menu, column = _LOCK_SPECS[label]
            current = _select_workshop_menu(
                current,
                menu,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                measure_menu_fn=measure_menu_fn,
                sleep_fn=sleep_fn,
            )
            unavailable = measure_unavailable_fn(current, label)
            if unavailable is not None:
                evidence.append(unavailable)
                log(
                    f"[FREE_UPGRADE_LOCKS] {label}=unavailable "
                    "because Range Upgrades are locked",
                    "INFO",
                )
                continue
            _box, current = _locate_workshop_upgrade(
                label,
                menu,
                column,
                current,
                capture_fn=capture_fn,
                detector=detector,
                swipe_fn=swipe_fn,
                detect_boxes_fn=detect_boxes_fn,
                measure_menu_fn=measure_menu_fn,
                sleep_fn=sleep_fn,
            )
            confirmed, current = _reconfirm_workshop_upgrade(
                label,
                menu,
                column,
                capture_fn=capture_fn,
                detector=detector,
                detect_boxes_fn=detect_boxes_fn,
                measure_menu_fn=measure_menu_fn,
                sleep_fn=sleep_fn,
            )
            x, y, width, height = confirmed.rect
            if not safe_tap_fn(
                (x + width // 4, y + height // 2),
                dispatch="now",
                log_label=f"workshop_detail:{_slug(label)}",
                verification=TapVerification(
                    screenshot=current,
                    target_region=confirmed.rect,
                    description=f"workshop_upgrade:{label}",
                    verifier=lambda frame, expected=confirmed.rect: (
                        detector(frame).get("state") == "WORKSHOP"
                        and (
                            candidate := _matching_box(
                                detect_boxes_fn(frame, menu=menu).get(column),
                                label,
                            )
                        )
                        is not None
                        and abs(candidate.rect[0] - expected[0]) <= 20
                        and abs(candidate.rect[1] - expected[1]) <= 20
                    ),
                ),
            ):
                raise FreeUpgradeLockInspectionError(
                    f"detail tap failed for {label}"
                )
            detail_open = True
            current, lock = _wait_for_lock_detail(
                label,
                capture_fn=capture_fn,
                detector=detector,
                measure_lock_fn=measure_lock_fn,
                sleep_fn=sleep_fn,
            )
            log(
                f"[FREE_UPGRADE_LOCKS] {label}={lock.state.value} "
                f"title_conf={lock.title_confidence:.1f} "
                f"outline_pixels={lock.checkbox_outline_pixels} "
                f"checkmark_pixels={lock.checkmark_pixels}",
                "INFO",
            )
            if enforce and lock.authoritative_mismatch:
                fresh = capture_fn()
                fresh_lock = measure_lock_fn(fresh, label)
                if not _is_expected_detail(fresh, detector, fresh_lock):
                    raise FreeUpgradeLockInspectionError(
                        f"lost authoritative unchecked evidence for {label}"
                    )
                if not safe_tap_fn(
                    "buttons.free_upgrade_lock:checkbox",
                    dispatch="now",
                    verification=TapVerification(
                        screenshot=fresh,
                        target_region=_CHECKBOX_REGION,
                        description=f"free_upgrade_lock:{label}:unchecked",
                        verifier=lambda frame: (
                            (
                                candidate := measure_lock_fn(frame, label)
                            ).state
                            is FreeUpgradeLockState.UNCHECKED
                            and _is_expected_detail(
                                frame,
                                detector,
                                candidate,
                            )
                        ),
                    ),
                ):
                    raise FreeUpgradeLockInspectionError(
                        f"lock checkbox tap failed for {label}"
                    )
                current, lock = _wait_for_lock_detail(
                    label,
                    capture_fn=capture_fn,
                    detector=detector,
                    measure_lock_fn=measure_lock_fn,
                    required_state=FreeUpgradeLockState.CHECKED,
                    sleep_fn=sleep_fn,
                )
                changed.append(label)
                log(
                    f"[FREE_UPGRADE_LOCKS] {label} checked and verified at Home",
                    "INFO",
                )
            evidence.append(lock)
            current = _dismiss_lock_detail(
                label,
                menu,
                current,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                measure_menu_fn=measure_menu_fn,
                measure_lock_fn=measure_lock_fn,
                sleep_fn=sleep_fn,
            )
            detail_open = False
    except Exception:
        if detail_open:
            _best_effort_dismiss(
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                sleep_fn=sleep_fn,
            )
        raise

    return FreeUpgradeLockInspectionResult(
        FreeUpgradeLocksEvidence(tuple(evidence)),
        current,
        tuple(changed),
    )


def _crop(frame: Optional[Frame], region: tuple[int, int, int, int]):
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim < 2:
        return None
    x, y, width, height = region
    if y + height > frame.shape[0] or x + width > frame.shape[1]:
        return None
    crop = frame[y : y + height, x : x + width]
    return crop if crop.size else None


def _normalize_text(text: Any) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", str(text or "").upper()))


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _require_workshop(frame, detector, measure_menu_fn, *, menu: Optional[str]):
    if frame is None:
        raise FreeUpgradeLockInspectionError("Workshop screenshot capture failed")
    detection = detector(frame)
    overlays = set(detection.get("overlays") or ())
    if detection.get("state") != "WORKSHOP" or overlays:
        raise FreeUpgradeLockInspectionError(
            "expected unobscured WORKSHOP, got "
            f"state={detection.get('state')!r} overlays={sorted(overlays)}"
        )
    actual_menu = measure_menu_fn(frame)
    if menu is not None and actual_menu != menu:
        raise FreeUpgradeLockInspectionError(
            f"expected Workshop {menu}, got {actual_menu!r}"
        )
    return frame


def _wait_for_workshop_menu(
    menu,
    *,
    capture_fn,
    detector,
    measure_menu_fn,
    sleep_fn,
    attempts=16,
):
    last_menu = None
    for _ in range(attempts):
        frame = capture_fn()
        try:
            _require_workshop(frame, detector, measure_menu_fn, menu=None)
        except FreeUpgradeLockInspectionError:
            sleep_fn(0.25)
            continue
        last_menu = measure_menu_fn(frame)
        if last_menu == menu:
            return frame
        sleep_fn(0.25)
    raise FreeUpgradeLockInspectionError(
        f"timed out waiting for Workshop {menu}; last menu={last_menu!r}"
    )


def _select_workshop_menu(
    current,
    menu,
    *,
    capture_fn,
    detector,
    safe_tap_fn,
    measure_menu_fn,
    sleep_fn,
):
    current = _require_workshop(current, detector, measure_menu_fn, menu=None)
    current_menu = measure_menu_fn(current)
    if current_menu == menu:
        return current
    if current_menu is None:
        if not safe_tap_fn(
            _WORKSHOP_UPGRADE_ACTION,
            dispatch="now",
            screenshot=current,
        ):
            raise FreeUpgradeLockInspectionError(
                "Workshop Upgrade navigation tap failed"
            )
        sleep_fn(0.25)
        current = _require_workshop(
            capture_fn(), detector, measure_menu_fn, menu=None
        )
        if measure_menu_fn(current) == menu:
            return current
    if not safe_tap_fn(
        _WORKSHOP_MENU_ACTIONS[menu],
        dispatch="now",
        screenshot=current,
    ):
        raise FreeUpgradeLockInspectionError(
            f"Workshop {menu} navigation tap failed"
        )
    return _wait_for_workshop_menu(
        menu,
        capture_fn=capture_fn,
        detector=detector,
        measure_menu_fn=measure_menu_fn,
        sleep_fn=sleep_fn,
    )


def _matching_box(boxes, label):
    for box in boxes or ():
        if str(getattr(box, "text", "") or "").strip() != label:
            continue
        return box
    return None


def _locate_workshop_upgrade(
    label,
    menu,
    column,
    current,
    *,
    capture_fn,
    detector,
    swipe_fn,
    detect_boxes_fn,
    measure_menu_fn,
    sleep_fn,
):
    current = _require_workshop(current, detector, measure_menu_fn, menu=menu)
    found = _matching_box(
        detect_boxes_fn(
            current,
            menu=menu,
            column_regions=_WORKSHOP_UPGRADE_COLUMN_REGIONS,
        ).get(column, ()),
        label,
    )
    if found is not None:
        return found, current

    for _ in range(3):
        current = _require_workshop(
            capture_fn(), detector, measure_menu_fn, menu=menu
        )
        swipe_fn("towards_top", "extended")
        sleep_fn(0.45)

    for position in range(9):
        current = _require_workshop(
            capture_fn(), detector, measure_menu_fn, menu=menu
        )
        found = _matching_box(
            detect_boxes_fn(
                current,
                menu=menu,
                column_regions=_WORKSHOP_UPGRADE_COLUMN_REGIONS,
            ).get(column, ()),
            label,
        )
        if found is not None:
            return found, current
        if position < 8:
            swipe_fn("towards_bottom", "medium")
            sleep_fn(0.45)
    raise FreeUpgradeLockInspectionError(
        f"could not locate {label} in Workshop {menu}"
    )


def _reconfirm_workshop_upgrade(
    label,
    menu,
    column,
    *,
    capture_fn,
    detector,
    detect_boxes_fn,
    measure_menu_fn,
    sleep_fn,
    attempts=4,
):
    for attempt in range(attempts):
        current = _require_workshop(
            capture_fn(), detector, measure_menu_fn, menu=menu
        )
        confirmed = _matching_box(
            detect_boxes_fn(
                current,
                menu=menu,
                column_regions=_WORKSHOP_UPGRADE_COLUMN_REGIONS,
            ).get(column, ()),
            label,
        )
        if confirmed is not None:
            return confirmed, current
        if attempt < attempts - 1:
            sleep_fn(0.15)
    raise FreeUpgradeLockInspectionError(
        f"could not reconfirm {label} before opening details"
    )


def _is_expected_detail(frame, detector, lock):
    if frame is None or lock.state is FreeUpgradeLockState.UNKNOWN:
        return False
    detection = detector(frame)
    return (
        detection.get("state") == "WORKSHOP"
        and "UPGRADE_DETAIL" in set(detection.get("overlays") or ())
    )


def _wait_for_lock_detail(
    label,
    *,
    capture_fn,
    detector,
    measure_lock_fn,
    sleep_fn,
    required_state=None,
    attempts=16,
):
    last = None
    last_state = None
    stable_observations = 0
    for _ in range(attempts):
        frame = capture_fn()
        last = measure_lock_fn(frame, label)
        if _is_expected_detail(frame, detector, last) and (
            required_state is None or last.state is required_state
        ):
            if last.state is last_state:
                stable_observations += 1
            else:
                last_state = last.state
                stable_observations = 1
            if stable_observations >= 2:
                return frame, last
        else:
            last_state = None
            stable_observations = 0
        sleep_fn(0.25)
    suffix = f" in state {required_state.value}" if required_state else ""
    raise FreeUpgradeLockInspectionError(
        f"timed out waiting for authoritative {label} lock detail{suffix}; "
        f"last={None if last is None else last.state.value}"
    )


def _dismiss_lock_detail(
    label,
    menu,
    current,
    *,
    capture_fn,
    detector,
    safe_tap_fn,
    measure_menu_fn,
    measure_lock_fn,
    sleep_fn,
):
    lock = measure_lock_fn(current, label)
    if not _is_expected_detail(current, detector, lock):
        raise FreeUpgradeLockInspectionError(
            f"refusing to dismiss unverified {label} detail"
        )
    if not safe_tap_fn(
        "gesture_targets.upgrade_detail_dismiss",
        dispatch="now",
        verification=TapVerification(
            screenshot=current,
            target_region=(0, 0, current.shape[1], current.shape[0]),
            description=f"free_upgrade_detail:{label}",
            verifier=lambda frame: _is_expected_detail(
                frame,
                detector,
                measure_lock_fn(frame, label),
            ),
        ),
    ):
        raise FreeUpgradeLockInspectionError(
            f"detail dismissal tap failed for {label}"
        )
    return _wait_for_workshop_menu(
        menu,
        capture_fn=capture_fn,
        detector=detector,
        measure_menu_fn=measure_menu_fn,
        sleep_fn=sleep_fn,
    )


def _best_effort_dismiss(*, capture_fn, detector, safe_tap_fn, sleep_fn):
    try:
        frame = capture_fn()
        detection = detector(frame) if frame is not None else {}
        if (
            detection.get("state") == "WORKSHOP"
            and "UPGRADE_DETAIL" in set(detection.get("overlays") or ())
        ):
            tapped = safe_tap_fn(
                "gesture_targets.upgrade_detail_dismiss",
                dispatch="now",
                verification=TapVerification(
                    screenshot=frame,
                    target_region=(0, 0, frame.shape[1], frame.shape[0]),
                    description="free_upgrade_detail:visible",
                    verifier=lambda candidate: (
                        detector(candidate).get("state") == "WORKSHOP"
                        and "UPGRADE_DETAIL"
                        in set(
                            detector(candidate).get("overlays") or ()
                        )
                    ),
                ),
            )
            if tapped:
                for _ in range(8):
                    sleep_fn(0.25)
                    refreshed = capture_fn()
                    refreshed_detection = (
                        detector(refreshed) if refreshed is not None else {}
                    )
                    if "UPGRADE_DETAIL" not in set(
                        refreshed_detection.get("overlays") or ()
                    ):
                        return
    except Exception:
        pass


__all__ = [
    "FARM_FREE_UPGRADE_LOCKS",
    "FreeUpgradeLockEvidence",
    "FreeUpgradeLockInspectionError",
    "FreeUpgradeLockInspectionResult",
    "FreeUpgradeLockState",
    "FreeUpgradeLocksEvidence",
    "inspect_free_upgrade_locks",
    "measure_free_upgrade_lock",
    "measure_unavailable_free_upgrade_lock",
    "measure_workshop_upgrade_menu",
    "normalize_free_upgrade_lock_requirements",
]
