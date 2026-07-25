"""Inspect and enforce Demon Mode/Nuke recharge behavior from Cards."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import time
from typing import Any, Callable, Iterator, Mapping, Optional

import cv2
import numpy as np

from core.input import TapVerification, safe_long_press, safe_tap, swipe_now
from core.matcher import get_match_result
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]

CARD_RECHARGE_MODE_REQUIREMENT = "card_recharge_modes"
CARD_RECHARGE_LABELS = ("Demon Mode", "Nuke")

_CARD_TEMPLATE_KEYS = {
    "Demon Mode": "buttons.card_inventory:demon_mode",
    "Nuke": "buttons.card_inventory:nuke",
}
_DETAIL_TEMPLATE_KEYS = {
    "Demon Mode": "indicators.card_detail:demon_mode",
    "Nuke": "indicators.card_detail:nuke",
}
_CHECKBOX_REGION = (855, 1448, 82, 84)
_CHECKMARK_REGION = (871, 1464, 50, 52)
_CHECKBOX_POINT = (895, 1490)
_MIN_CHECKBOX_OUTLINE_PIXELS = 350
_MIN_CHECKMARK_PIXELS = 100
_MAX_EMPTY_CHECKMARK_PIXELS = 30
_TOP_SWIPES = 3
_SEARCH_SWIPES = 6


class CardRechargeMode(str, Enum):
    AUTO_REACTIVATE = "auto_reactivate"
    READY_AFTER_RECHARGE = "ready_after_recharge"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CardRechargeModeEvidence:
    label: str
    required: CardRechargeMode
    observed: CardRechargeMode
    detail_visible: bool
    detail_confidence: float
    checkbox_outline_pixels: int
    checkmark_pixels: int

    @property
    def valid(self) -> bool:
        return self.observed is self.required

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["required"] = self.required.value
        payload["observed"] = self.observed.value
        payload["valid"] = self.valid
        return payload


@dataclass(frozen=True)
class CardRechargeModesResult:
    screenshot: Frame
    modes: tuple[CardRechargeModeEvidence, ...]
    changed_labels: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return len(self.modes) == len(CARD_RECHARGE_LABELS) and all(
            mode.valid for mode in self.modes
        )

    @property
    def changed(self) -> bool:
        return bool(self.changed_labels)

    def as_dict(self) -> dict[str, Any]:
        return {
            "modes": [mode.as_dict() for mode in self.modes],
            "valid": self.valid,
            "changed": self.changed,
            "changed_labels": list(self.changed_labels),
        }


class CardRechargeModeError(RuntimeError):
    pass


def normalize_card_recharge_modes(raw: Any) -> dict[str, CardRechargeMode]:
    """Validate the complete supported Demon Mode/Nuke recharge contract."""

    if not isinstance(raw, Mapping):
        raise ValueError("card_recharge_modes must be a mapping")
    labels = {str(label or "").strip() for label in raw}
    if labels != set(CARD_RECHARGE_LABELS):
        raise ValueError(
            "card_recharge_modes must define exactly Demon Mode and Nuke"
        )

    normalized: dict[str, CardRechargeMode] = {}
    for label in CARD_RECHARGE_LABELS:
        value = str(raw.get(label) or "").strip().lower()
        try:
            mode = CardRechargeMode(value)
        except ValueError as exc:
            raise ValueError(
                f"card_recharge_modes.{label} must be auto_reactivate "
                "or ready_after_recharge"
            ) from exc
        if mode is CardRechargeMode.UNKNOWN:
            raise ValueError(
                f"card_recharge_modes.{label} must be auto_reactivate "
                "or ready_after_recharge"
            )
        normalized[label] = mode
    return normalized


def measure_card_recharge_mode(
    screenshot: Optional[Frame],
    label: str,
    *,
    required: CardRechargeMode | str,
) -> CardRechargeModeEvidence:
    """Classify one activation-on-recharge checkbox on verified Card detail."""

    try:
        required_mode = CardRechargeMode(
            required.value
            if isinstance(required, CardRechargeMode)
            else str(required).strip().lower()
        )
    except ValueError as exc:
        raise ValueError(f"unsupported Card recharge mode {required!r}") from exc
    if label not in _DETAIL_TEMPLATE_KEYS:
        raise ValueError(f"unsupported Card recharge label {label!r}")

    detail = get_match_result(
        _DETAIL_TEMPLATE_KEYS[label],
        screenshot=screenshot,
    )
    checkbox = _crop(screenshot, _CHECKBOX_REGION)
    checkmark = _crop(screenshot, _CHECKMARK_REGION)
    outline_pixels = 0
    checkmark_pixels = 0
    if checkbox is not None:
        hsv = cv2.cvtColor(checkbox, cv2.COLOR_BGR2HSV)
        outline_pixels = int(
            (
                (hsv[:, :, 0] >= 75)
                & (hsv[:, :, 0] <= 105)
                & (hsv[:, :, 1] > 50)
                & (hsv[:, :, 2] > 150)
            ).sum()
        )
    if checkmark is not None:
        hsv = cv2.cvtColor(checkmark, cv2.COLOR_BGR2HSV)
        checkmark_pixels = int(
            ((hsv[:, :, 1] < 60) & (hsv[:, :, 2] > 180)).sum()
        )

    detail_visible = detail.matched
    checkbox_visible = outline_pixels >= _MIN_CHECKBOX_OUTLINE_PIXELS
    if (
        detail_visible
        and checkbox_visible
        and checkmark_pixels >= _MIN_CHECKMARK_PIXELS
    ):
        observed = CardRechargeMode.AUTO_REACTIVATE
    elif (
        detail_visible
        and checkbox_visible
        and checkmark_pixels <= _MAX_EMPTY_CHECKMARK_PIXELS
    ):
        observed = CardRechargeMode.READY_AFTER_RECHARGE
    else:
        observed = CardRechargeMode.UNKNOWN
    return CardRechargeModeEvidence(
        label=label,
        required=required_mode,
        observed=observed,
        detail_visible=detail_visible,
        detail_confidence=detail.confidence,
        checkbox_outline_pixels=outline_pixels,
        checkmark_pixels=checkmark_pixels,
    )


def ensure_card_recharge_modes(
    requirements: Mapping[str, Any],
    *,
    cards_screenshot: Optional[Frame] = None,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    safe_long_press_fn: Callable[..., bool] = safe_long_press,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    swipe_fn: Callable[[str], bool] = swipe_now,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> CardRechargeModesResult:
    """Restore Demon Mode/Nuke recharge behavior from the Cards inventory."""

    required = normalize_card_recharge_modes(requirements)
    current = cards_screenshot if cards_screenshot is not None else capture_fn()
    _require_cards_inventory(current, detector)

    evidence_by_label: dict[str, CardRechargeModeEvidence] = {}
    changed_labels: set[str] = set()
    for current in _inventory_search_frames(
        current,
        capture_fn=capture_fn,
        detector=detector,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    ):
        for label in CARD_RECHARGE_LABELS:
            if label in evidence_by_label:
                continue
            if not get_match_result(
                _CARD_TEMPLATE_KEYS[label],
                screenshot=current,
            ).matched:
                continue
            current, observed, changed = _ensure_inventory_card_mode(
                label,
                current,
                required=required[label],
                capture_fn=capture_fn,
                detector=detector,
                safe_long_press_fn=safe_long_press_fn,
                safe_tap_fn=safe_tap_fn,
                sleep_fn=sleep_fn,
            )
            evidence_by_label[label] = observed
            if changed:
                changed_labels.add(label)
        if len(evidence_by_label) == len(CARD_RECHARGE_LABELS):
            break

    missing = [
        label
        for label in CARD_RECHARGE_LABELS
        if label not in evidence_by_label
    ]
    if missing:
        if len(missing) == 1:
            raise CardRechargeModeError(
                f"{missing[0]} Card was not found in inventory"
            )
        raise CardRechargeModeError(
            f"{' and '.join(missing)} Cards were not found in inventory"
        )

    result = CardRechargeModesResult(
        screenshot=current,
        modes=tuple(
            evidence_by_label[label]
            for label in CARD_RECHARGE_LABELS
        ),
        changed_labels=tuple(
            label
            for label in CARD_RECHARGE_LABELS
            if label in changed_labels
        ),
    )
    if not result.valid:
        raise CardRechargeModeError(
            "Card recharge modes remained invalid after correction"
        )
    return result


def _inventory_search_frames(
    current: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    swipe_fn: Callable[[str], bool],
    sleep_fn: Callable[[float], None],
) -> Iterator[Frame]:
    _require_cards_inventory(current, detector)
    yield current
    for _ in range(_TOP_SWIPES):
        fresh = _capture_complete(capture_fn, sleep_fn)
        _require_cards_inventory(fresh, detector)
        if not swipe_fn("gesture_targets.goto_top:cards_inventory"):
            raise CardRechargeModeError("Cards inventory top swipe failed")
        sleep_fn(0.35)
        current = _capture_complete(capture_fn, sleep_fn)
        _require_cards_inventory(current, detector)
        yield current
    for _ in range(_SEARCH_SWIPES):
        fresh = _capture_complete(capture_fn, sleep_fn)
        _require_cards_inventory(fresh, detector)
        if not swipe_fn("gesture_targets.goto_next:cards_inventory"):
            raise CardRechargeModeError("Cards inventory search swipe failed")
        sleep_fn(0.35)
        current = _capture_complete(capture_fn, sleep_fn)
        _require_cards_inventory(current, detector)
        yield current


def _ensure_inventory_card_mode(
    label: str,
    current: Frame,
    *,
    required: CardRechargeMode,
    capture_fn: Capture,
    detector: Detector,
    safe_long_press_fn: Callable[..., bool],
    safe_tap_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> tuple[Frame, CardRechargeModeEvidence, bool]:
    template_key = _CARD_TEMPLATE_KEYS[label]
    if not safe_long_press_fn(
        template_key,
        duration_ms=800,
        retries=1,
        retry_delay=0.25,
        screenshot=current,
    ):
        raise CardRechargeModeError(f"verified long press failed for {label}")
    try:
        detail, observed = _wait_for_detail(
            label,
            required,
            capture_fn=capture_fn,
            sleep_fn=sleep_fn,
        )
        if observed.observed is CardRechargeMode.UNKNOWN:
            raise CardRechargeModeError(
                f"{label} recharge checkbox state was ambiguous"
            )
        changed = False
        if not observed.valid:
            fresh = _capture_complete(capture_fn, sleep_fn)
            fresh_evidence = measure_card_recharge_mode(
                fresh,
                label,
                required=required,
            )
            if fresh_evidence.observed is not observed.observed:
                raise CardRechargeModeError(
                    f"{label} recharge evidence changed before correction"
                )
            if not safe_tap_fn(
                _CHECKBOX_POINT,
                dispatch="now",
                log_label=f"card_recharge:{label}",
                verification=TapVerification(
                    screenshot=fresh,
                    target_region=_CHECKBOX_REGION,
                    description=(
                        f"card_recharge:{label}:"
                        f"{fresh_evidence.observed.value}"
                    ),
                    verifier=lambda frame, card=label, mode=required, state=(
                        fresh_evidence.observed
                    ): (
                        measure_card_recharge_mode(
                            frame,
                            card,
                            required=mode,
                        ).observed
                        is state
                    ),
                ),
            ):
                raise CardRechargeModeError(
                    f"{label} recharge checkbox tap failed"
                )
            detail, observed = _wait_for_detail(
                label,
                required,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
                expected=required,
            )
            changed = True
        log(
            f"[CARD_RECHARGE] {label}={observed.observed.value} "
            f"required={required.value} "
            f"detail_conf={observed.detail_confidence:.3f} "
            f"outline_pixels={observed.checkbox_outline_pixels} "
            f"checkmark_pixels={observed.checkmark_pixels}",
            "INFO",
        )
        current = _dismiss_detail(
            label,
            detail,
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            sleep_fn=sleep_fn,
        )
        return current, observed, changed
    except Exception:
        _best_effort_dismiss(
            capture_fn=capture_fn,
            safe_tap_fn=safe_tap_fn,
            sleep_fn=sleep_fn,
        )
        raise


def _wait_for_detail(
    label: str,
    required: CardRechargeMode,
    *,
    capture_fn: Capture,
    sleep_fn: Callable[[float], None],
    expected: Optional[CardRechargeMode] = None,
    timeout: float = 8.0,
) -> tuple[Frame, CardRechargeModeEvidence]:
    deadline = time.monotonic() + timeout
    last: Optional[CardRechargeModeEvidence] = None
    while time.monotonic() < deadline:
        frame = _capture_complete(capture_fn, sleep_fn)
        last = measure_card_recharge_mode(frame, label, required=required)
        if last.observed is not CardRechargeMode.UNKNOWN and (
            expected is None or last.observed is expected
        ):
            return frame, last
        sleep_fn(0.25)
    description = (
        f"expected {expected.value}"
        if expected is not None
        else "authoritative checkbox state"
    )
    raise CardRechargeModeError(
        f"timed out waiting for {label} detail ({description}); "
        f"last={last}"
    )


def _dismiss_detail(
    label: str,
    detail: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    if not safe_tap_fn(
        "buttons.card_detail:close",
        retries=1,
        retry_delay=0.2,
        screenshot=detail,
    ):
        raise CardRechargeModeError(f"{label} Card detail close failed")
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        current = _capture_complete(capture_fn, sleep_fn)
        if _is_cards_inventory(current, detector):
            return current
        sleep_fn(0.25)
    raise CardRechargeModeError(
        f"{label} Card detail dismissal was not verified"
    )


def _best_effort_dismiss(
    *,
    capture_fn: Capture,
    safe_tap_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> None:
    frame = capture_fn()
    if not is_complete_screenshot(frame):
        return
    try:
        safe_tap_fn("buttons.card_detail:close", screenshot=frame)
        sleep_fn(0.25)
    except Exception:
        return


def _is_cards_inventory(frame: Optional[Frame], detector: Detector) -> bool:
    if not is_complete_screenshot(frame):
        return False
    detection = detector(frame)
    if detection.get("state") != "CARDS":
        return False
    return not any(
        get_match_result(key, screenshot=frame).matched
        for key in _DETAIL_TEMPLATE_KEYS.values()
    )


def _require_cards_inventory(frame: Optional[Frame], detector: Detector) -> None:
    if not _is_cards_inventory(frame, detector):
        state = None
        if is_complete_screenshot(frame):
            state = detector(frame).get("state")
        raise CardRechargeModeError(
            f"expected unobscured CARDS inventory, got {state!r}"
        )


def _capture_complete(
    capture_fn: Capture,
    sleep_fn: Callable[[float], None],
    *,
    timeout: float = 5.0,
) -> Frame:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = capture_fn()
        if is_complete_screenshot(frame):
            return frame
        sleep_fn(0.2)
    raise CardRechargeModeError("screenshot capture failed")


def _crop(
    frame: Optional[Frame],
    region: tuple[int, int, int, int],
) -> Optional[Frame]:
    if not is_complete_screenshot(frame):
        return None
    x, y, width, height = region
    if y + height > frame.shape[0] or x + width > frame.shape[1]:
        return None
    crop = frame[y : y + height, x : x + width]
    return crop if crop.size else None


__all__ = [
    "CARD_RECHARGE_LABELS",
    "CARD_RECHARGE_MODE_REQUIREMENT",
    "CardRechargeMode",
    "CardRechargeModeError",
    "CardRechargeModeEvidence",
    "CardRechargeModesResult",
    "ensure_card_recharge_modes",
    "measure_card_recharge_mode",
    "normalize_card_recharge_modes",
]
