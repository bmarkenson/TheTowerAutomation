"""Guarded inspection and feedback-driven control of the Damage adjuster."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import re
import time
from typing import Any, Callable, Final, Optional

import numpy as np

from core.clickmap_access import resolve_dot_path
from core.input import TapVerification, safe_tap, tap_if_visible
from core.matcher import get_match
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_navigation import ensure_upgrade_menu, find_upgrade
from utils.logger import log, log_action_intent, log_result
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
    screenshot: Optional[Frame] = field(default=None, repr=False, compare=False)


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


def _finish_damage_slider(result: DamageSliderResult) -> DamageSliderResult:
    """Emit the terminal operator result for one Damage Slider workflow."""

    expected = format_damage_percentage(result.expected)
    final = format_damage_percentage(result.final)
    if result.mode == "observe" and result.observed and result.dismissed:
        if result.matches:
            summary = f"Damage Slider check complete — matched {expected}"
        else:
            summary = (
                "Damage Slider check complete — "
                f"observed {final or 'unknown'}, expected {expected}"
            )
    elif result.success:
        summary = f"Damage Slider setup complete — {final or expected} verified"
    else:
        workflow = "check" if result.mode == "observe" else "setup"
        summary = (
            f"Damage Slider {workflow} failed — "
            f"{result.reason.replace('_', ' ')}"
        )
    log_result(
        summary,
        detail=(
            f"[DAMAGE_ADJUSTER] result={'completed' if result.success else result.reason} "
            f"mode={result.mode} expected={result.expected} initial={result.initial} "
            f"final={result.final} observed={result.observed} matches={result.matches} "
            f"changed={result.changed} steps={result.steps} "
            f"dismissed={result.dismissed}"
        ),
    )
    return result


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


def format_damage_percentage(value: Optional[Any]) -> Optional[str]:
    """Render a canonical percentage using the notation shown to operators."""

    if value is None:
        return None
    canonical = normalize_damage_percentage(value)
    numeric = Decimal(canonical[:-1])
    if numeric == numeric.to_integral_value():
        return f"{format(numeric, 'f')}%"
    return canonical


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
        return DamageAdjusterReading(
            True,
            None,
            None,
            "",
            -1.0,
            panel_confidence,
            screenshot=screenshot,
        )

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
        screenshot=screenshot,
    )


def open_damage_adjuster(
    *,
    capture_fn: CaptureFn = capture_adb_screenshot,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    find_upgrade_fn: Callable[..., Any] = find_upgrade,
    swipe_fn: Optional[Callable[..., Any]] = None,
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
    opened = tap_visible_fn(
        "buttons.damage_adjuster:attack",
        screenshot=screenshot,
        retries=1,
    )
    if not opened:
        log(
            "[DAMAGE_ADJUSTER] Locating the Damage Slider because its label "
            "is outside or did not match in the current Attack viewport",
            "DEBUG",
        )

        def capture_verified_attack() -> Optional[Frame]:
            frame = capture_fn()
            if frame is None:
                return None
            current = detect_state_and_overlays(frame)
            if (
                current["state"] != "RUNNING"
                or current["menu"] != "ATTACK_MENU"
            ):
                log(
                    "[DAMAGE_ADJUSTER] Attack menu changed while locating "
                    "the Damage label: "
                    f"state={current['state']!r} menu={current['menu']!r}",
                    "WARN",
                )
                return None
            return frame

        find_kwargs: dict[str, Any] = {
            "attempt_purchase": False,
            "capture_fn": capture_verified_attack,
            "sleep_fn": sleep_fn,
            "ensure_menu": False,
        }
        if swipe_fn is not None:
            find_kwargs["swipe_fn"] = swipe_fn
        located = find_upgrade_fn("attack", "Damage", **find_kwargs)
        if located is None:
            log(
                "[DAMAGE_ADJUSTER] Damage label was not found in the "
                "Attack upgrade list",
                "WARN",
            )
            return None
        screenshot = located.screenshot
        opened = tap_visible_fn(
            "buttons.damage_adjuster:attack",
            screenshot=screenshot,
            retries=1,
        )
        if not opened:
            log(
                "[DAMAGE_ADJUSTER] Located Damage tile did not pass the "
                "final label guard",
                "WARN",
            )
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
        dispatch="now",
        verification=TapVerification(
            screenshot=screenshot,
            target_region=(0, 0, 1080, 1920),
            description="damage_adjuster:visible_backdrop",
            verifier=lambda frame: read_damage_adjuster(frame).visible,
        ),
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
    """Observe or enforce one percentage using guarded bounded feedback."""

    canonical_mode = str(mode or "").strip().lower()
    if canonical_mode not in {"observe", "enforce"}:
        raise ValueError("Damage Slider mode must be observe or enforce")
    canonical_expected = normalize_damage_percentage(expected)
    display_expected = format_damage_percentage(canonical_expected)
    expected_value = _percentage_value(canonical_expected)
    assert expected_value is not None
    assert display_expected is not None

    if canonical_mode == "observe":
        log_action_intent(
            "Checking the Damage Slider",
            reason=(
                f"record whether its current value matches "
                f"{display_expected} without changing it"
            ),
            detail=(
                f"[DAMAGE_ADJUSTER] mode={canonical_mode} "
                f"expected={canonical_expected}"
            ),
        )
    else:
        log_action_intent(
            f"Setting the Damage Slider to {display_expected}",
            reason=(
                "the selected strategy requires that starting value before "
                "normal run actions continue"
            ),
            detail=(
                f"[DAMAGE_ADJUSTER] mode={canonical_mode} "
                f"expected={canonical_expected}"
            ),
        )

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
        return _finish_damage_slider(
            DamageSliderResult(
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
        )

    reading = open_damage_adjuster(
        capture_fn=capture_fn,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    if reading is None:
        return _finish_damage_slider(
            DamageSliderResult(
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
                    button = (
                        "buttons.damage_adjuster:decrease"
                        if current_value > expected_value
                        else "buttons.damage_adjuster:increase"
                    )
                    remaining_steps = max(0, int(max_steps)) - steps
                    batch_steps = min(
                        _known_slider_step_count(current_value, expected_value),
                        remaining_steps,
                    )
                    if batch_steps <= 0:
                        reason = "step_limit_reached"
                        break
                    log(
                        "[DAMAGE_ADJUSTER] Applying "
                        f"{batch_steps} {button.rsplit(':', 1)[-1]} tap(s) "
                        f"from {format_damage_percentage(final)} "
                        f"toward {display_expected}",
                        "DEBUG",
                    )

                    arrow_authority = _damage_arrow_batch_authority(
                        reading,
                        button,
                        current_value,
                    )
                    if arrow_authority is None:
                        reason = "arrow_not_verified"
                        break
                    arrow_point, verification = arrow_authority
                    dispatched = 0
                    dispatch_failed = False
                    for _ in range(batch_steps):
                        if not tap_fn(
                            arrow_point,
                            dispatch="now",
                            log_label=button,
                            verification=verification,
                        ):
                            dispatch_failed = True
                            break
                        steps += 1
                        dispatched += 1
                    if dispatched == 0:
                        break

                    updated = _wait_for_settled_value(
                        current_value,
                        expected_value,
                        capture_fn=capture_fn,
                        sleep_fn=sleep_fn,
                        attempts=settle_attempts,
                    )
                    updated_value = _valid_slider_value(updated)
                    if updated_value is None or updated_value == current_value:
                        reason = "value_did_not_change"
                        break
                    final = updated.percentage
                    changed = changed or updated_value != current_value
                    if _slider_distance(
                        updated_value,
                        expected_value,
                    ) >= _slider_distance(current_value, expected_value):
                        reason = "value_moved_away_from_target"
                        break
                    if updated_value in seen and updated_value != expected_value:
                        reason = "value_cycle_detected"
                        break
                    seen.add(updated_value)
                    reading = updated
                    current_value = updated_value
                    matches = current_value == expected_value
                    reason = "matched" if matches else "adjusting"
                    if dispatch_failed and not matches:
                        reason = "arrow_tap_failed"
                        break

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
    return _finish_damage_slider(
        DamageSliderResult(
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
    )


def _valid_slider_value(reading: DamageAdjusterReading) -> Optional[Decimal]:
    if (
        not reading.visible
        or reading.mode != DAMAGE_SELECTOR_MODE
        or reading.ocr_confidence < 50.0
    ):
        return None
    return _percentage_value(reading.percentage)


def _damage_arrow_batch_authority(
    reading: DamageAdjusterReading,
    button: str,
    current_value: Decimal,
) -> Optional[tuple[tuple[int, int], TapVerification]]:
    """Authorize one urgent arrow batch from its initial verified panel frame."""

    screenshot = reading.screenshot
    if screenshot is None or _valid_slider_value(reading) != current_value:
        return None
    entry = resolve_dot_path(button)
    region = entry.get("match_region") if isinstance(entry, dict) else None
    if not isinstance(region, dict):
        return None
    point, _confidence = get_match(button, screenshot=screenshot)
    if point is None:
        return None
    target_region = (
        int(region["x"]),
        int(region["y"]),
        int(region["w"]),
        int(region["h"]),
    )
    return (
        (int(point[0]), int(point[1])),
        TapVerification(
            screenshot=screenshot,
            target_region=target_region,
            description=f"damage_slider_urgent_batch:{button}:{reading.percentage}",
            verifier=lambda candidate: (
                candidate is screenshot
                and reading.visible
                and reading.mode == DAMAGE_SELECTOR_MODE
                and _valid_slider_value(reading) == current_value
            ),
            reuse_authority=True,
        ),
    )


def _power_of_ten_exponent(value: Decimal) -> Optional[int]:
    normalized = value.normalize()
    sign, digits, exponent = normalized.as_tuple()
    if sign == 0 and digits == (1,):
        return int(exponent)
    return None


def _known_slider_step_count(current: Decimal, expected: Decimal) -> int:
    """Return an exact power-of-ten gap, else a single guarded fallback step."""

    current_exponent = _power_of_ten_exponent(current)
    expected_exponent = _power_of_ten_exponent(expected)
    if current_exponent is None or expected_exponent is None:
        return 1
    return max(1, abs(current_exponent - expected_exponent))


def _slider_distance(current: Decimal, expected: Decimal) -> Decimal:
    current_exponent = _power_of_ten_exponent(current)
    expected_exponent = _power_of_ten_exponent(expected)
    if current_exponent is not None and expected_exponent is not None:
        return Decimal(abs(current_exponent - expected_exponent))
    return abs(current - expected)


def _wait_for_settled_value(
    previous: Decimal,
    expected: Decimal,
    *,
    capture_fn: CaptureFn,
    sleep_fn: SleepFn,
    attempts: int,
) -> DamageAdjusterReading:
    latest = DamageAdjusterReading(False, None, None, "", -1.0, -1.0)
    last_value: Optional[Decimal] = None
    stable_reads = 0
    for _ in range(max(1, int(attempts))):
        sleep_fn(0.25)
        latest = read_damage_adjuster(capture_fn())
        value = _valid_slider_value(latest)
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
    if _valid_slider_value(latest) == expected:
        return latest
    return DamageAdjusterReading(False, None, None, "", -1.0, -1.0)


__all__ = [
    "DAMAGE_SELECTOR_MODE",
    "DAMAGE_SELECTOR_REGION",
    "DamageAdjusterReading",
    "DamageSliderResult",
    "configure_damage_slider",
    "dismiss_damage_adjuster",
    "format_damage_percentage",
    "normalize_damage_percentage",
    "open_damage_adjuster",
    "read_damage_adjuster",
]
