"""Guarded inspection and feedback-driven control of the Damage adjuster."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
import time
from typing import Any, Callable, Final, Optional

import numpy as np

from core.input import safe_tap, tap_if_visible
from core.matcher import get_match
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_navigation import ensure_upgrade_menu
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


@dataclass(frozen=True)
class DamageSliderResult:
    mode: str
    expected: str
    initial: Optional[str]
    final: Optional[str]
    observed: bool
    matches: bool
    changed: bool
    steps: int
    dismissed: bool
    reason: str

    @property
    def success(self) -> bool:
        return self.observed and self.matches and self.dismissed

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["success"] = self.success
        return payload


def normalize_damage_percentage(value: Any) -> str:
    """Return a positive percentage in the panel's compact canonical form."""

    raw = re.sub(r"\s+", "", str(value or "").upper())
    if raw.endswith("%"):
        raw = raw[:-1]
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:E[+-]?\d+)?", raw):
        raise ValueError(f"invalid Damage Slider percentage {value!r}")
    try:
        numeric = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid Damage Slider percentage {value!r}") from exc
    if not numeric.is_finite() or numeric <= 0:
        raise ValueError("Damage Slider percentage must be positive and finite")
    normalized = str(numeric.normalize()).upper()
    normalized = normalized.replace("E+", "E")
    return f"{normalized}%"


def _percentage_value(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(normalize_damage_percentage(value)[:-1])
    except (InvalidOperation, ValueError):
        return None


def _canonical_percentage(text: str) -> Optional[str]:
    compact = re.sub(r"\s+", "", text.upper())
    compact = compact.replace("–", "-").replace("—", "-")
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?(?:E-\d+)?)%", compact)
    return f"{match.group(1)}%" if match else None


def read_damage_adjuster(screenshot: Optional[Frame]) -> DamageAdjusterReading:
    """Read the configurable percentage, excluding the derived damage value."""

    if not is_complete_screenshot(screenshot):
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


def configure_damage_slider(
    expected: Any,
    *,
    mode: str = "enforce",
    capture_fn: CaptureFn = capture_adb_screenshot,
    tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    ensure_menu_fn: Callable[..., Optional[Frame]] = ensure_upgrade_menu,
    sleep_fn: SleepFn = time.sleep,
    max_steps: int = 64,
    settle_attempts: int = 6,
) -> DamageSliderResult:
    """Observe or enforce one percentage using guarded single-step feedback."""

    canonical_mode = str(mode or "").strip().lower()
    if canonical_mode not in {"observe", "enforce"}:
        raise ValueError("Damage Slider mode must be observe or enforce")
    canonical_expected = normalize_damage_percentage(expected)
    expected_value = _percentage_value(canonical_expected)
    assert expected_value is not None

    initial: Optional[str] = None
    final: Optional[str] = None
    observed = False
    matches = False
    changed = False
    steps = 0
    dismissed = False
    reason = "not_started"

    attack = ensure_menu_fn("attack", capture_fn=capture_fn)
    if attack is None:
        return DamageSliderResult(
            canonical_mode,
            canonical_expected,
            initial,
            final,
            observed,
            matches,
            changed,
            steps,
            dismissed,
            "attack_menu_not_verified",
        )

    reading = open_damage_adjuster(
        capture_fn=capture_fn,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    if reading is None:
        return DamageSliderResult(
            canonical_mode,
            canonical_expected,
            initial,
            final,
            observed,
            matches,
            changed,
            steps,
            dismissed,
            "panel_not_verified",
        )

    try:
        initial = reading.percentage
        final = initial
        current_value = _valid_slider_value(reading)
        if current_value is None:
            reason = "value_not_authoritative"
        else:
            observed = True
            matches = current_value == expected_value
            if canonical_mode == "observe":
                reason = "matched" if matches else "observed_mismatch"
            else:
                seen = {current_value}
                while not matches and steps < max(0, int(max_steps)):
                    fresh = read_damage_adjuster(capture_fn())
                    fresh_value = _valid_slider_value(fresh)
                    if fresh_value is None:
                        reason = "fresh_value_not_authoritative"
                        break
                    final = fresh.percentage
                    if fresh_value == expected_value:
                        matches = True
                        reason = "matched"
                        break

                    button = (
                        "buttons.damage_adjuster:decrease"
                        if fresh_value > expected_value
                        else "buttons.damage_adjuster:increase"
                    )
                    if not tap_fn(
                        button,
                        require_visible=False,
                        dispatch="now",
                    ):
                        reason = "arrow_tap_failed"
                        break
                    steps += 1

                    updated = _wait_for_changed_value(
                        fresh_value,
                        capture_fn=capture_fn,
                        sleep_fn=sleep_fn,
                        attempts=settle_attempts,
                    )
                    updated_value = _valid_slider_value(updated)
                    if updated_value is None or updated_value == fresh_value:
                        reason = "value_did_not_change"
                        break
                    final = updated.percentage
                    changed = changed or updated_value != current_value
                    if abs(updated_value - expected_value) >= abs(
                        fresh_value - expected_value
                    ):
                        reason = "value_moved_away_from_target"
                        break
                    if updated_value in seen and updated_value != expected_value:
                        reason = "value_cycle_detected"
                        break
                    seen.add(updated_value)
                    current_value = updated_value
                    matches = current_value == expected_value
                    reason = "matched" if matches else "adjusting"

                if not matches and reason == "adjusting":
                    reason = "step_limit_reached"
                elif not matches and steps == 0 and reason == "not_started":
                    reason = "step_limit_reached"
    finally:
        dismissed = dismiss_damage_adjuster(
            capture_fn=capture_fn,
            tap_fn=tap_fn,
            sleep_fn=sleep_fn,
        )

    if not dismissed:
        reason = f"{reason};dismiss_failed"
    return DamageSliderResult(
        canonical_mode,
        canonical_expected,
        initial,
        final,
        observed,
        matches,
        changed,
        steps,
        dismissed,
        reason,
    )


def _valid_slider_value(reading: DamageAdjusterReading) -> Optional[Decimal]:
    if (
        not reading.visible
        or reading.mode != DAMAGE_SELECTOR_MODE
        or reading.ocr_confidence < 50.0
    ):
        return None
    return _percentage_value(reading.percentage)


def _wait_for_changed_value(
    previous: Decimal,
    *,
    capture_fn: CaptureFn,
    sleep_fn: SleepFn,
    attempts: int,
) -> DamageAdjusterReading:
    latest = DamageAdjusterReading(False, None, None, "", -1.0, -1.0)
    for _ in range(max(1, int(attempts))):
        sleep_fn(0.25)
        latest = read_damage_adjuster(capture_fn())
        value = _valid_slider_value(latest)
        if value is not None and value != previous:
            return latest
    return latest


__all__ = [
    "DAMAGE_SELECTOR_MODE",
    "DAMAGE_SELECTOR_REGION",
    "DamageAdjusterReading",
    "DamageSliderResult",
    "configure_damage_slider",
    "dismiss_damage_adjuster",
    "normalize_damage_percentage",
    "open_damage_adjuster",
    "read_damage_adjuster",
]
