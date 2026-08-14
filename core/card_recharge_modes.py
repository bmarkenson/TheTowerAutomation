"""Inspect and enforce Demon Mode/Nuke recharge behavior from Cards."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.input import TapVerification, safe_long_press, safe_tap, swipe_now
from core.matcher import get_match_result
from core.scrolling import guarded_swipe
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
# A freshly toggled live checkbox can render 342 cyan outline pixels while the
# detail identity and checkmark evidence remain stable.  Keep enough margin
# below that observed state for minor anti-aliasing differences.
_MIN_CHECKBOX_OUTLINE_PIXELS = 300
_MIN_CHECKMARK_PIXELS = 100
_MAX_EMPTY_CHECKMARK_PIXELS = 30
_MAX_TOP_SWIPES = 8
_MAX_SEARCH_SWIPES = 16
_SCROLL_SETTLE_SECONDS = 0.8
_SCROLL_STABLE_THRESHOLD = 1.0
_INVENTORY_PROGRESS_REGION = (0, 988, 1080, 714)
_FAILURE_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[1] / "screenshots" / "matches"
)


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


@dataclass(frozen=True)
class _InventoryViewportEvidence:
    phase: str
    index: int
    screenshot: Frame
    confidences: Mapping[str, float]


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
    repair_observer_fn: Callable[[], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> CardRechargeModesResult:
    """Restore Demon Mode/Nuke recharge behavior from the Cards inventory."""

    required = normalize_card_recharge_modes(requirements)
    current = cards_screenshot if cards_screenshot is not None else capture_fn()
    _require_cards_inventory(current, detector)

    evidence_by_label: dict[str, CardRechargeModeEvidence] = {}
    changed_labels: set[str] = set()
    viewport_evidence: list[_InventoryViewportEvidence] = []
    viewport_index = 0
    repair_announced = False

    def announce_repair() -> None:
        nonlocal repair_announced
        if repair_announced:
            return
        if repair_observer_fn is not None:
            repair_observer_fn()
        repair_announced = True

    def inspect_viewport(frame: Frame, phase: str) -> Frame:
        nonlocal viewport_index
        viewport_index += 1
        matches = {
            label: get_match_result(
                _CARD_TEMPLATE_KEYS[label],
                screenshot=frame,
            )
            for label in CARD_RECHARGE_LABELS
        }
        confidences = {
            label: float(match.confidence)
            for label, match in matches.items()
        }
        viewport_evidence.append(
            _InventoryViewportEvidence(
                phase=phase,
                index=viewport_index,
                screenshot=frame.copy(),
                confidences=confidences,
            )
        )
        log(
            f"[CARD_RECHARGE] viewport={viewport_index} phase={phase} "
            + " ".join(
                f"{label.replace(' ', '_').lower()}="
                f"{matches[label].confidence:.3f}/"
                f"{matches[label].threshold:.3f}"
                for label in CARD_RECHARGE_LABELS
            ),
            "DEBUG",
        )

        current_frame = frame
        for label in CARD_RECHARGE_LABELS:
            if label in evidence_by_label:
                continue
            match = (
                matches[label]
                if current_frame is frame
                else get_match_result(
                    _CARD_TEMPLATE_KEYS[label],
                    screenshot=current_frame,
                )
            )
            if not match.matched:
                continue
            current_frame, observed, changed = _ensure_inventory_card_mode(
                label,
                current_frame,
                required=required[label],
                capture_fn=capture_fn,
                detector=detector,
                safe_long_press_fn=safe_long_press_fn,
                safe_tap_fn=safe_tap_fn,
                repair_observer_fn=announce_repair,
                sleep_fn=sleep_fn,
            )
            evidence_by_label[label] = observed
            if changed:
                changed_labels.add(label)
        return current_frame

    try:
        current = inspect_viewport(current, "initial")
        if len(evidence_by_label) < len(CARD_RECHARGE_LABELS):
            current = _traverse_inventory(
                current,
                phase="towards_top",
                swipe_key="gesture_targets.goto_top:cards_inventory",
                max_swipes=_MAX_TOP_SWIPES,
                observe_fn=inspect_viewport,
                complete_fn=lambda: (
                    len(evidence_by_label) == len(CARD_RECHARGE_LABELS)
                ),
                capture_fn=capture_fn,
                detector=detector,
                swipe_fn=swipe_fn,
                sleep_fn=sleep_fn,
            )
        if len(evidence_by_label) < len(CARD_RECHARGE_LABELS):
            current = _traverse_inventory(
                current,
                phase="search",
                swipe_key="gesture_targets.goto_next:cards_inventory",
                max_swipes=_MAX_SEARCH_SWIPES,
                observe_fn=inspect_viewport,
                complete_fn=lambda: (
                    len(evidence_by_label) == len(CARD_RECHARGE_LABELS)
                ),
                capture_fn=capture_fn,
                detector=detector,
                swipe_fn=swipe_fn,
                sleep_fn=sleep_fn,
            )
    except Exception as exc:
        _retain_inventory_failure(viewport_evidence, reason=str(exc))
        raise

    missing = [
        label
        for label in CARD_RECHARGE_LABELS
        if label not in evidence_by_label
    ]
    if missing:
        _retain_inventory_failure(
            viewport_evidence,
            reason=f"missing={','.join(missing)}",
        )
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


def _traverse_inventory(
    current: Frame,
    *,
    phase: str,
    swipe_key: str,
    max_swipes: int,
    observe_fn: Callable[[Frame, str], Frame],
    complete_fn: Callable[[], bool],
    capture_fn: Capture,
    detector: Detector,
    swipe_fn: Callable[[str], bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    _require_cards_inventory(current, detector)
    for swipe_index in range(1, max(1, int(max_swipes)) + 1):
        before = _capture_complete(capture_fn, sleep_fn)
        _require_cards_inventory(before, detector)
        step = guarded_swipe(
            swipe_key,
            source_label="cards_inventory",
            screenshot=before,
            settle_s=_SCROLL_SETTLE_SECONDS,
            capture_fn=capture_fn,
            visible_fn=lambda _label, *, screenshot=None: (
                _is_cards_inventory(screenshot, detector)
            ),
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        if not step.success or step.screenshot is None:
            raise CardRechargeModeError(
                f"Cards inventory {phase} swipe failed ({step.reason})"
            )
        difference = _inventory_difference(before, step.screenshot)
        current = observe_fn(step.screenshot, phase)
        if complete_fn():
            return current
        if difference <= _SCROLL_STABLE_THRESHOLD:
            log(
                f"[CARD_RECHARGE] {phase} edge reached after "
                f"{swipe_index} swipe(s) (difference={difference:.2f})",
                "DEBUG",
            )
            return current
    raise CardRechargeModeError(
        f"Cards inventory {phase} edge was not reached within "
        f"{max_swipes} swipes"
    )


def _inventory_difference(before: Frame, after: Frame) -> float:
    if before.shape != after.shape:
        return float("inf")
    x, y, width, height = _INVENTORY_PROGRESS_REGION
    before_crop = before[y : y + height, x : x + width]
    after_crop = after[y : y + height, x : x + width]
    if (
        before_crop.size == 0
        or after_crop.size == 0
        or before_crop.shape != after_crop.shape
    ):
        return float("inf")
    delta = np.abs(
        before_crop.astype(np.int16) - after_crop.astype(np.int16)
    )
    return float(delta.mean())


def _retain_inventory_failure(
    viewports: list[_InventoryViewportEvidence],
    *,
    reason: str,
) -> tuple[Path, ...]:
    if not viewports:
        return ()
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    directory = _FAILURE_EVIDENCE_DIR / f"CardRecharge{stamp}"
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        log(
            f"[CARD_RECHARGE] Could not create failure evidence directory: {exc}",
            "DEBUG",
        )
        return ()

    paths: list[Path] = []
    for viewport in viewports:
        path = directory / (
            f"{viewport.index:02d}_{viewport.phase.replace(' ', '_')}.png"
        )
        if cv2.imwrite(str(path), viewport.screenshot):
            paths.append(path)
    log(
        f"[CARD_RECHARGE] Retained {len(paths)}/{len(viewports)} failure "
        f"viewport(s) under {directory}; reason={reason}; confidences="
        + repr(
            [
                {
                    "viewport": viewport.index,
                    "phase": viewport.phase,
                    **{
                        label: round(confidence, 3)
                        for label, confidence in viewport.confidences.items()
                    },
                }
                for viewport in viewports
            ]
        ),
        "DEBUG",
    )
    return tuple(paths)


def _ensure_inventory_card_mode(
    label: str,
    current: Frame,
    *,
    required: CardRechargeMode,
    capture_fn: Capture,
    detector: Detector,
    safe_long_press_fn: Callable[..., bool],
    safe_tap_fn: Callable[..., bool],
    repair_observer_fn: Callable[[], None],
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
            repair_observer_fn()
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
