"""Guarded inspection and feedback-driven control of Orb Distance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import re
import time
from typing import Any, Callable, Final, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.input import tap_if_visible
from core.matcher import get_match
from core.run_controls import ensure_menu_open
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_navigation import UpgradeSearchResult, find_upgrade
from utils.logger import log, log_action_intent
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
CaptureFn = Callable[[], Optional[Frame]]
SleepFn = Callable[[float], None]

EXTRA_VALUE_REGION: Final[tuple[int, int, int, int]] = (400, 1270, 285, 130)
WORKSHOP_VALUE_REGION: Final[tuple[int, int, int, int]] = (400, 1590, 285, 130)
_DISTANCE_QUANTUM: Final[Decimal] = Decimal("0.01")
_MIN_OCR_CONFIDENCE: Final[float] = 50.0


@dataclass(frozen=True)
class RangeReading:
    distance: Optional[str]
    ocr_text: str
    ocr_confidence: float

    @property
    def authoritative(self) -> bool:
        return (
            self.distance is not None
            and self.ocr_confidence >= _MIN_OCR_CONFIDENCE
        )


@dataclass(frozen=True)
class OrbDistanceReading:
    visible: bool
    extra: Optional[str]
    workshop: Optional[str]
    extra_ocr_text: str
    workshop_ocr_text: str
    extra_ocr_confidence: float
    workshop_ocr_confidence: float
    panel_confidence: float
    screenshot: Optional[Frame] = field(default=None, repr=False, compare=False)

    @property
    def authoritative(self) -> bool:
        return (
            self.visible
            and self.extra is not None
            and self.workshop is not None
            and self.extra_ocr_confidence >= _MIN_OCR_CONFIDENCE
            and self.workshop_ocr_confidence >= _MIN_OCR_CONFIDENCE
        )


@dataclass(frozen=True)
class OrbDistanceResult:
    mode: str
    range_basis: str
    range_observed: Optional[str]
    expected_extra: str
    expected_workshop: str
    initial_extra: Optional[str]
    initial_workshop: Optional[str]
    final_extra: Optional[str]
    final_workshop: Optional[str]
    observed: bool
    matches: bool
    changed: bool
    extra_steps: int
    workshop_steps: int
    dismissed: bool
    reason: str
    preserved: bool = False

    @property
    def success(self) -> bool:
        return self.preserved or (
            self.observed and self.matches and self.dismissed
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["success"] = self.success
        return payload


def normalize_distance(value: Any) -> str:
    """Return a positive distance in the panel's two-decimal meter notation."""

    raw = re.sub(r"\s+", "", str(value or "").lower())
    if raw.endswith("m"):
        raw = raw[:-1]
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        raise ValueError(f"invalid Orb Distance {value!r}")
    try:
        numeric = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid Orb Distance {value!r}") from exc
    if not numeric.is_finite() or numeric <= 0:
        raise ValueError("Orb Distance must be positive and finite")
    quantized = numeric.quantize(_DISTANCE_QUANTUM)
    if quantized != numeric:
        raise ValueError("Orb Distance must use at most two decimal places")
    return f"{quantized:.2f}m"


def normalize_orb_distance_preset(raw: Any) -> dict[str, str]:
    """Validate and normalize one Range-bound pair of Orb Distance values."""

    if not isinstance(raw, Mapping):
        raise ValueError("Orb Distance preset must be a mapping")
    expected_keys = {"range_basis", "extra", "workshop"}
    if set(raw) != expected_keys:
        raise ValueError(
            "Orb Distance preset must define exactly range_basis, extra, "
            "and workshop"
        )
    return {
        key: normalize_distance(raw[key])
        for key in ("range_basis", "extra", "workshop")
    }


def normalize_orb_distance_presets(raw: Any) -> list[dict[str, str]]:
    """Normalize the complete set of Range-selectable distance presets."""

    candidates: Sequence[Any]
    if isinstance(raw, Mapping):
        candidates = list(raw.values())
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        candidates = list(raw)
    else:
        raise ValueError("Orb Distance presets must be a mapping or list")
    if not candidates:
        raise ValueError("Orb Distance presets cannot be empty")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        preset = normalize_orb_distance_preset(candidate)
        basis = preset["range_basis"]
        if basis in seen:
            raise ValueError(
                f"Orb Distance presets repeat Range basis {basis}"
            )
        normalized.append(preset)
        seen.add(basis)
    return normalized


def _canonical_distance(text: str) -> Optional[str]:
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[mM]\b", text)
    if match is None:
        return None
    try:
        return normalize_distance(match.group(1))
    except ValueError:
        return None


def read_orb_distance(screenshot: Optional[Frame]) -> OrbDistanceReading:
    """Read both values from a verified Distance Adjuster panel."""

    if not is_complete_screenshot(screenshot):
        return OrbDistanceReading(
            False, None, None, "", "", -1.0, -1.0, -1.0
        )

    point, panel_confidence = get_match(
        "indicators.distance_adjuster",
        screenshot=screenshot,
    )
    if point is None:
        return OrbDistanceReading(
            False,
            None,
            None,
            "",
            "",
            -1.0,
            -1.0,
            panel_confidence,
        )

    values: dict[str, tuple[Optional[str], str, float]] = {}
    for name, (x, y, width, height) in (
        ("extra", EXTRA_VALUE_REGION),
        ("workshop", WORKSHOP_VALUE_REGION),
    ):
        crop = screenshot[y : y + height, x : x + width]
        if crop.size == 0:
            values[name] = (None, "", -1.0)
            continue
        text, confidence = ocr_text_and_conf(crop, psm=7)
        values[name] = (_canonical_distance(text), text, confidence)

    return OrbDistanceReading(
        True,
        values["extra"][0],
        values["workshop"][0],
        values["extra"][1],
        values["workshop"][1],
        values["extra"][2],
        values["workshop"][2],
        panel_confidence,
        screenshot=screenshot,
    )


def _read_range_from_result(
    result: Optional[UpgradeSearchResult],
) -> RangeReading:
    if result is None:
        return RangeReading(None, "", -1.0)
    screenshot = result.screenshot
    if not is_complete_screenshot(screenshot):
        return RangeReading(None, "", -1.0)
    x, y, width, height = result.box.rect
    crop = screenshot[
        y : y + max(1, int(height * 0.68)),
        x + int(width * 0.52) : x + int(width * 0.95),
    ]
    if crop.size == 0:
        return RangeReading(None, "", -1.0)
    text, confidence = ocr_text_and_conf(crop, psm=6)
    direct = RangeReading(_canonical_distance(text), text, confidence)
    if direct.authoritative:
        return direct

    # Maxed upgrade values are dim gray. On the taller live Attack tile,
    # unprocessed OCR can return no tokens even though the meter value is
    # visibly clear. Preserve the complete value crop but isolate its local
    # contrast before the one bounded retry.
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    isolated = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        3,
    )
    retry_text, retry_confidence = ocr_text_and_conf(isolated, psm=6)
    retry = RangeReading(
        _canonical_distance(retry_text),
        retry_text,
        retry_confidence,
    )
    return retry if retry.authoritative else direct


def read_attack_range(
    *,
    capture_fn: CaptureFn = capture_adb_screenshot,
    find_upgrade_fn: Callable[..., Optional[UpgradeSearchResult]] = find_upgrade,
    sleep_fn: SleepFn = time.sleep,
) -> RangeReading:
    """Locate the Attack Range tile and return its displayed meter value."""

    try:
        result = find_upgrade_fn(
            "attack",
            "Range",
            attempt_purchase=False,
            capture_fn=capture_fn,
            sleep_fn=sleep_fn,
        )
    except Exception as exc:
        log(f"[ORB_DISTANCE] Unable to inspect Attack Range: {exc}", "WARN")
        return RangeReading(None, "", -1.0)
    reading = _read_range_from_result(result)
    if not reading.authoritative:
        log(
            "[ORB_DISTANCE] Attack Range OCR was not authoritative: "
            f"text={reading.ocr_text!r} confidence={reading.ocr_confidence:.1f}",
            "WARN",
        )
    return reading


def open_orb_distance(
    *,
    capture_fn: CaptureFn = capture_adb_screenshot,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    ensure_menu_fn: Callable[[], bool] = ensure_menu_open,
    sleep_fn: SleepFn = time.sleep,
    attempts: int = 6,
) -> Optional[OrbDistanceReading]:
    """Open Distance Adjuster from a verified running side menu."""

    screenshot = capture_fn()
    existing = read_orb_distance(screenshot)
    if existing.visible:
        return existing
    if not ensure_menu_fn():
        log("[ORB_DISTANCE] Unable to open the in-run side menu", "WARN")
        return None
    screenshot = capture_fn()
    if screenshot is None:
        log("[ORB_DISTANCE] Screenshot unavailable before open", "WARN")
        return None
    detection = detect_state_and_overlays(screenshot)
    if (
        detection["state"] != "RUNNING"
        or "MENU_OPEN" not in set(detection["overlays"])
    ):
        log(
            "[ORB_DISTANCE] Refusing open from "
            f"state={detection['state']!r} overlays={detection['overlays']!r}",
            "WARN",
        )
        return None
    if not tap_visible_fn(
        "navigation.distance_adjuster",
        screenshot=screenshot,
        retries=1,
    ):
        log("[ORB_DISTANCE] Distance Adjuster control did not match", "WARN")
        return None

    for _ in range(max(1, attempts)):
        sleep_fn(0.25)
        reading = read_orb_distance(capture_fn())
        if reading.visible:
            return reading
    log("[ORB_DISTANCE] Panel did not appear after verified control tap", "WARN")
    return None


def dismiss_orb_distance(
    *,
    capture_fn: CaptureFn = capture_adb_screenshot,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    sleep_fn: SleepFn = time.sleep,
    attempts: int = 6,
) -> bool:
    """Close Distance Adjuster and verify the running side menu returned."""

    screenshot = capture_fn()
    reading = read_orb_distance(screenshot)
    if not reading.visible:
        if screenshot is not None:
            detection = detect_state_and_overlays(screenshot)
            if (
                detection["state"] == "RUNNING"
                and "MENU_OPEN" in set(detection["overlays"])
            ):
                return True
        log("[ORB_DISTANCE] Refusing dismiss without the panel guard", "WARN")
        return False
    if not tap_visible_fn(
        "buttons.close:distance_adjuster",
        screenshot=screenshot,
        retries=1,
    ):
        return False

    for _ in range(max(1, attempts)):
        sleep_fn(0.25)
        frame = capture_fn()
        if frame is None:
            continue
        detection = detect_state_and_overlays(frame)
        if (
            detection["state"] == "RUNNING"
            and "MENU_OPEN" in set(detection["overlays"])
        ):
            return True
    log("[ORB_DISTANCE] Running side menu did not return after dismiss", "WARN")
    return False


def _distance_value(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(normalize_distance(value)[:-1])
    except (InvalidOperation, ValueError):
        return None


def _wait_for_changed_reading(
    row: str,
    previous: Decimal,
    expected: Decimal,
    *,
    capture_fn: CaptureFn,
    sleep_fn: SleepFn,
    attempts: int,
) -> OrbDistanceReading:
    latest = OrbDistanceReading(
        False, None, None, "", "", -1.0, -1.0, -1.0
    )
    last_value: Optional[Decimal] = None
    stable_reads = 0
    for _ in range(max(1, int(attempts))):
        sleep_fn(0.25)
        latest = read_orb_distance(capture_fn())
        if not latest.authoritative:
            last_value = None
            stable_reads = 0
            continue
        value = _distance_value(getattr(latest, row))
        if value is None or value == previous:
            last_value = None
            stable_reads = 0
            continue
        if value == expected:
            return latest
        if value == last_value:
            stable_reads += 1
        else:
            last_value = value
            stable_reads = 1
        if stable_reads >= 2:
            return latest
    return latest


def configure_orb_distance(
    *,
    range_basis: Any,
    extra: Any,
    workshop: Any,
    range_presets: Any = None,
    mode: str = "enforce",
    capture_fn: CaptureFn = capture_adb_screenshot,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    read_range_fn: Callable[..., RangeReading] = read_attack_range,
    ensure_menu_fn: Callable[[], bool] = ensure_menu_open,
    sleep_fn: SleepFn = time.sleep,
    max_steps_per_row: int = 160,
    settle_attempts: int = 6,
) -> OrbDistanceResult:
    """Observe or enforce one Range-bound Orb Distance preset."""

    canonical_mode = str(mode or "").strip().lower()
    if canonical_mode not in {"observe", "enforce"}:
        raise ValueError("Orb Distance mode must be observe or enforce")
    expected = normalize_orb_distance_preset(
        {
            "range_basis": range_basis,
            "extra": extra,
            "workshop": workshop,
        }
    )
    configured_presets = (
        normalize_orb_distance_presets(range_presets)
        if range_presets is not None
        else None
    )
    if canonical_mode == "observe":
        log_action_intent(
            "Checking Orb Distance for the observed Attack Range",
            reason=(
                "select a matching configured Range preset when one exists "
                "and preserve unconfigured experimental Ranges"
            ),
        )
    else:
        log_action_intent(
            "Enforcing Orb Distance only for configured Attack Ranges",
            reason=(
                "read Attack Range, select its matching preset, and preserve "
                "unconfigured experimental Ranges without Distance Adjuster "
                "input"
            ),
        )

    result_values: dict[str, Any] = {
        "range_observed": None,
        "initial_extra": None,
        "initial_workshop": None,
        "final_extra": None,
        "final_workshop": None,
        "observed": False,
        "matches": False,
        "changed": False,
        "extra_steps": 0,
        "workshop_steps": 0,
        "dismissed": False,
        "reason": "not_started",
        "preserved": False,
    }

    range_reading = read_range_fn(
        capture_fn=capture_fn,
        sleep_fn=sleep_fn,
    )
    result_values["range_observed"] = range_reading.distance
    if not range_reading.authoritative:
        result_values["reason"] = "range_not_authoritative"
        return OrbDistanceResult(
            mode=canonical_mode,
            range_basis=expected["range_basis"],
            expected_extra=expected["extra"],
            expected_workshop=expected["workshop"],
            **result_values,
        )
    if configured_presets is not None:
        matching_preset = next(
            (
                preset
                for preset in configured_presets
                if preset["range_basis"] == range_reading.distance
            ),
            None,
        )
        if matching_preset is None:
            result_values.update(
                observed=True,
                dismissed=True,
                reason="unconfigured_range_preserved",
                preserved=True,
            )
            log(
                "[ORB_DISTANCE] Preserving unconfigured Attack Range "
                f"{range_reading.distance}; no Distance Adjuster input is "
                "authorized",
                "INFO",
            )
            return OrbDistanceResult(
                mode=canonical_mode,
                range_basis=expected["range_basis"],
                expected_extra=expected["extra"],
                expected_workshop=expected["workshop"],
                **result_values,
            )
        expected = matching_preset
    elif range_reading.distance != expected["range_basis"]:
        result_values["reason"] = "range_basis_mismatch"
        return OrbDistanceResult(
            mode=canonical_mode,
            range_basis=expected["range_basis"],
            expected_extra=expected["extra"],
            expected_workshop=expected["workshop"],
            **result_values,
        )

    if canonical_mode == "observe":
        log(
            "[ORB_DISTANCE] Observed Attack Range "
            f"{range_reading.distance} selected preset "
            f"Extra {expected['extra']} / Workshop {expected['workshop']} "
            "for read-only comparison",
            "INFO",
        )
    else:
        log(
            "[ORB_DISTANCE] Observed Attack Range "
            f"{range_reading.distance} selected preset "
            f"Extra {expected['extra']} / Workshop {expected['workshop']}",
            "INFO",
        )

    reading = open_orb_distance(
        capture_fn=capture_fn,
        tap_visible_fn=tap_visible_fn,
        ensure_menu_fn=ensure_menu_fn,
        sleep_fn=sleep_fn,
    )
    if reading is None:
        result_values["reason"] = "panel_not_verified"
        return OrbDistanceResult(
            mode=canonical_mode,
            range_basis=expected["range_basis"],
            expected_extra=expected["extra"],
            expected_workshop=expected["workshop"],
            **result_values,
        )

    try:
        result_values["initial_extra"] = reading.extra
        result_values["initial_workshop"] = reading.workshop
        result_values["final_extra"] = reading.extra
        result_values["final_workshop"] = reading.workshop
        if not reading.authoritative:
            result_values["reason"] = "value_not_authoritative"
        else:
            result_values["observed"] = True
            result_values["matches"] = (
                reading.extra == expected["extra"]
                and reading.workshop == expected["workshop"]
            )
            if canonical_mode == "observe":
                result_values["reason"] = (
                    "matched"
                    if result_values["matches"]
                    else "observed_mismatch"
                )
            else:
                for row in ("extra", "workshop"):
                    target = _distance_value(expected[row])
                    current = _distance_value(getattr(reading, row))
                    assert target is not None
                    if current is None:
                        result_values["reason"] = "value_not_authoritative"
                        break
                    seen = {current}
                    while (
                        current != target
                        and result_values[f"{row}_steps"]
                        < max(0, int(max_steps_per_row))
                    ):
                        direction = "decrease" if current > target else "increase"
                        button = f"buttons.distance_adjuster:{row}:{direction}"
                        log(
                            f"[ORB_DISTANCE] {row.title()} {getattr(reading, row)} "
                            f"toward {expected[row]} with one {direction} tap",
                            "INFO",
                        )
                        if not tap_visible_fn(
                            button,
                            screenshot=reading.screenshot,
                            retries=0,
                        ):
                            result_values["reason"] = "arrow_not_verified"
                            break
                        result_values[f"{row}_steps"] += 1
                        updated = _wait_for_changed_reading(
                            row,
                            current,
                            target,
                            capture_fn=capture_fn,
                            sleep_fn=sleep_fn,
                            attempts=settle_attempts,
                        )
                        updated_value = (
                            _distance_value(getattr(updated, row))
                            if updated.authoritative
                            else None
                        )
                        if updated_value is None or updated_value == current:
                            result_values["reason"] = "value_did_not_change"
                            break
                        result_values["final_extra"] = updated.extra
                        result_values["final_workshop"] = updated.workshop
                        result_values["changed"] = True
                        if abs(updated_value - target) >= abs(current - target):
                            result_values["reason"] = "value_moved_away_from_target"
                            break
                        if updated_value in seen and updated_value != target:
                            result_values["reason"] = "value_cycle_detected"
                            break
                        seen.add(updated_value)
                        reading = updated
                        current = updated_value
                        result_values["reason"] = (
                            "matched" if current == target else "adjusting"
                        )
                    if current != target:
                        if result_values["reason"] in {
                            "not_started",
                            "matched",
                            "adjusting",
                        }:
                            result_values["reason"] = "step_limit_reached"
                        break

                result_values["matches"] = (
                    reading.authoritative
                    and reading.extra == expected["extra"]
                    and reading.workshop == expected["workshop"]
                )
                if result_values["matches"]:
                    result_values["reason"] = "matched"
    finally:
        result_values["dismissed"] = dismiss_orb_distance(
            capture_fn=capture_fn,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )

    if not result_values["dismissed"]:
        result_values["reason"] = f"{result_values['reason']};dismiss_failed"
    return OrbDistanceResult(
        mode=canonical_mode,
        range_basis=expected["range_basis"],
        expected_extra=expected["extra"],
        expected_workshop=expected["workshop"],
        **result_values,
    )


__all__ = [
    "EXTRA_VALUE_REGION",
    "WORKSHOP_VALUE_REGION",
    "OrbDistanceReading",
    "OrbDistanceResult",
    "RangeReading",
    "configure_orb_distance",
    "dismiss_orb_distance",
    "normalize_distance",
    "normalize_orb_distance_preset",
    "normalize_orb_distance_presets",
    "open_orb_distance",
    "read_attack_range",
    "read_orb_distance",
]
