"""Guarded GC preset correction at a verified no-battle Home boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Callable, Mapping

from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.input import safe_tap, tap_if_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.workshop_preset import (
    BOTS_FARM_PRESET_SLOT,
    CARDS_GC_PRESET_SLOT,
    FARM_PRESET_SLOT,
    measure_preset_slot_selection,
)
from utils.logger import log


class GcNoBattleSetupStatus(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class GcNoBattleSetupResult:
    status: GcNoBattleSetupStatus
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.status is GcNoBattleSetupStatus.COMPLETE


class _SetupFailure(RuntimeError):
    pass


def run_gc_no_battle_setup(
    requirements: Mapping[str, Any],
    *,
    screenshot=None,
    capture_fn: Callable[[], Any] = capture_adb_screenshot,
    detector: Callable[[Any], Mapping[str, Any]] = detect_state_and_overlays,
    detect_home_control_fn: Callable[[Any], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    measure_selection_fn: Callable[..., Any] = measure_preset_slot_selection,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GcNoBattleSetupResult:
    """Correct supported persistent GC settings before a new battle starts."""

    unsupported = _unsupported_requirement(requirements)
    if unsupported:
        return GcNoBattleSetupResult(
            GcNoBattleSetupStatus.UNSUPPORTED,
            unsupported,
        )

    evidence: dict[str, Any] = {}
    current = screenshot if screenshot is not None else capture_fn()
    try:
        _require_no_battle_home(current, detector, detect_home_control_fn)

        cards = _open_static(
            current,
            "navigation.goto_cards_home",
            "HOME_SCREEN",
            "CARDS",
            capture_fn,
            detector,
            safe_tap_fn,
            sleep_fn,
        )
        cards = _ensure_preset(
            cards,
            state="CARDS",
            slot_secondary="CARDS_GC_SLOT",
            slot_label="indicators.cards:gc_slot",
            slot_region=CARDS_GC_PRESET_SLOT,
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            measure_selection_fn=measure_selection_fn,
            sleep_fn=sleep_fn,
        )
        evidence["cards_deck"] = "GC"
        current = _return_home(
            cards,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            sleep_fn,
        )

        workshop = _open_static(
            current,
            "navigation.goto_workshop_home",
            "HOME_SCREEN",
            "WORKSHOP",
            capture_fn,
            detector,
            safe_tap_fn,
            sleep_fn,
        )
        workshop = _ensure_preset(
            workshop,
            state="WORKSHOP",
            slot_secondary="WORKSHOP_FARM_SLOT",
            slot_label="indicators.workshop:farm_slot",
            slot_region=FARM_PRESET_SLOT,
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            measure_selection_fn=measure_selection_fn,
            sleep_fn=sleep_fn,
        )
        evidence["workshop_preset"] = "Farm"
        current = _return_home(
            workshop,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            sleep_fn,
        )

        event = _open_visible(
            current,
            "navigation.home_event",
            "HOME_SCREEN",
            "EVENT",
            capture_fn,
            detector,
            tap_visible_fn,
            sleep_fn,
        )
        bots = _open_static(
            event,
            "navigation.event:bots_tab",
            "EVENT",
            "EVENT",
            capture_fn,
            detector,
            safe_tap_fn,
            sleep_fn,
            required_secondary="EVENT_BOTS_SCREEN",
        )
        bots = _ensure_preset(
            bots,
            state="EVENT",
            slot_secondary="BOTS_FARM_SLOT",
            slot_label="indicators.bots:farm_slot",
            slot_region=BOTS_FARM_PRESET_SLOT,
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            measure_selection_fn=measure_selection_fn,
            sleep_fn=sleep_fn,
        )
        evidence["bots_preset"] = "Farm"
        current = _return_home(
            bots,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            sleep_fn,
        )

        guild = _open_visible(
            current,
            "navigation.home_guild",
            "HOME_SCREEN",
            "GUILD",
            capture_fn,
            detector,
            tap_visible_fn,
            sleep_fn,
        )
        guardians = _open_static(
            guild,
            "navigation.guild:guardian_tab",
            "GUILD",
            "GUILD",
            capture_fn,
            detector,
            safe_tap_fn,
            sleep_fn,
            required_secondary="GUILD_GUARDIAN_SCREEN",
        )
        guardians = _ensure_guardian_loadout(
            guardians,
            capture_fn,
            detector,
            tap_visible_fn,
            sleep_fn,
        )
        evidence["guardian_chips"] = ["Fetch", "Summon", "Scout"]
        _return_home(
            guardians,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            sleep_fn,
        )
    except Exception as exc:
        _recover_home(
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            sleep_fn,
        )
        log(f"[GC_NO_BATTLE] Setup failed: {exc}", "ERROR")
        return GcNoBattleSetupResult(
            GcNoBattleSetupStatus.FAILED,
            str(exc),
            evidence,
        )

    log("[GC_NO_BATTLE] GC presets verified/corrected before Battle", "INFO")
    return GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "supported no-battle requirements verified",
        evidence,
    )


def _unsupported_requirement(requirements: Mapping[str, Any]) -> str | None:
    fixed = {
        "cards_deck": "GC",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
    }
    for key, expected in fixed.items():
        if str(requirements.get(key) or "").strip() != expected:
            return f"unsupported {key}={requirements.get(key)!r}"
    chips = {str(chip).strip() for chip in requirements.get("guardian_chips") or []}
    if chips != {"Fetch", "Summon", "Scout"}:
        return f"unsupported guardian_chips={sorted(chips)!r}"
    return None


def _require_no_battle_home(frame, detector, detect_home_control_fn) -> None:
    detection = detector(frame)
    if detection.get("state") != "HOME_SCREEN":
        raise _SetupFailure(f"expected HOME_SCREEN, got {detection.get('state')!r}")
    home = detect_home_control_fn(frame)
    if home.control is not HomeBattleControl.NEW_BATTLE:
        raise _SetupFailure(f"expected NEW_BATTLE, got {home.control.value}")


def _wait_for(
    *,
    state: str,
    capture_fn,
    detector,
    sleep_fn,
    secondary: str | None = None,
    timeout: float = 8.0,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = capture_fn()
        detection = detector(frame)
        if detection.get("state") == state and (
            secondary is None
            or secondary in set(detection.get("secondary_states") or ())
        ):
            return frame
        sleep_fn(0.25)
    suffix = f"/{secondary}" if secondary else ""
    raise _SetupFailure(f"timed out waiting for {state}{suffix}")


def _open_static(
    source,
    label,
    source_state,
    destination,
    capture_fn,
    detector,
    safe_tap_fn,
    sleep_fn,
    *,
    required_secondary: str | None = None,
):
    state = detector(source).get("state")
    if state != source_state:
        raise _SetupFailure(
            f"static navigation guard failed for {label}: "
            f"expected {source_state}, got {state!r}"
        )
    if not safe_tap_fn(label, require_visible=False, dispatch="now"):
        raise _SetupFailure(f"static navigation failed for {label} from {state}")
    return _wait_for(
        state=destination,
        secondary=required_secondary,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _open_visible(
    source,
    label,
    source_state,
    destination,
    capture_fn,
    detector,
    tap_visible_fn,
    sleep_fn,
):
    if detector(source).get("state") != source_state:
        raise _SetupFailure(f"visible navigation guard failed for {label}")
    if not tap_visible_fn(label, screenshot=source, retries=1):
        raise _SetupFailure(f"visible navigation failed for {label}")
    return _wait_for(
        state=destination,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _ensure_preset(
    frame,
    *,
    state,
    slot_secondary,
    slot_label,
    slot_region,
    capture_fn,
    detector,
    tap_visible_fn,
    measure_selection_fn,
    sleep_fn,
):
    detection = detector(frame)
    if detection.get("state") != state:
        raise _SetupFailure(f"preset parent state changed from {state}")
    if slot_secondary not in set(detection.get("secondary_states") or ()):
        raise _SetupFailure(f"preset identity missing: {slot_secondary}")
    selection = measure_selection_fn(frame, slot_region)
    if selection.selected:
        return frame
    if not tap_visible_fn(slot_label, screenshot=frame, retries=1):
        raise _SetupFailure(f"preset tap failed: {slot_label}")
    updated = _wait_for(
        state=state,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    if not measure_selection_fn(updated, slot_region).selected:
        raise _SetupFailure(f"preset did not become selected: {slot_label}")
    return updated


def _ensure_guardian_loadout(
    frame,
    capture_fn,
    detector,
    tap_visible_fn,
    sleep_fn,
):
    desired = {
        "GUARDIAN_FETCH_EQUIPPED",
        "GUARDIAN_SUMMON_EQUIPPED",
        "GUARDIAN_SCOUT_EQUIPPED",
    }
    current = frame
    detected = set(detector(current).get("secondary_states") or ())
    if "GUARDIAN_FETCH_EQUIPPED" not in detected:
        current = _replace_guardian_chip(
            current,
            wrong_label="indicators.guardian:attack_equipped",
            inventory_label="buttons.guardian:fetch_inventory",
            expected_secondary="GUARDIAN_FETCH_EQUIPPED",
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
    detected = set(detector(current).get("secondary_states") or ())
    if "GUARDIAN_SUMMON_EQUIPPED" not in detected:
        current = _replace_guardian_chip(
            current,
            wrong_label="indicators.guardian:ally_equipped",
            inventory_label="buttons.guardian:summon_inventory",
            expected_secondary="GUARDIAN_SUMMON_EQUIPPED",
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
    detected = set(detector(current).get("secondary_states") or ())
    missing = desired - detected
    if missing:
        raise _SetupFailure(
            "unsupported Guardian mismatch: " + ", ".join(sorted(missing))
        )
    return current


def _replace_guardian_chip(
    frame,
    *,
    wrong_label,
    inventory_label,
    expected_secondary,
    capture_fn,
    detector,
    tap_visible_fn,
    sleep_fn,
):
    if not tap_visible_fn(wrong_label, screenshot=frame, retries=1):
        raise _SetupFailure(f"known Guardian replacement source missing: {wrong_label}")
    emptied = _wait_for(
        state="GUILD",
        secondary="GUILD_GUARDIAN_SCREEN",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    if not tap_visible_fn(inventory_label, screenshot=emptied, retries=1):
        raise _SetupFailure(f"Guardian inventory target missing: {inventory_label}")
    return _wait_for(
        state="GUILD",
        secondary=expected_secondary,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _return_home(
    frame,
    capture_fn,
    detector,
    detect_home_control_fn,
    safe_tap_fn,
    sleep_fn,
):
    if detector(frame).get("state") == "HOME_SCREEN":
        _require_no_battle_home(frame, detector, detect_home_control_fn)
        return frame
    if not safe_tap_fn("navigation.goto_home", require_visible=False, dispatch="now"):
        raise _SetupFailure("Home navigation failed")
    home = _wait_for(
        state="HOME_SCREEN",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    _require_no_battle_home(home, detector, detect_home_control_fn)
    return home


def _recover_home(
    capture_fn,
    detector,
    detect_home_control_fn,
    safe_tap_fn,
    sleep_fn,
) -> None:
    try:
        frame = capture_fn()
        if detector(frame).get("state") == "HOME_SCREEN":
            return
        _return_home(
            frame,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            sleep_fn,
        )
    except Exception:
        pass


__all__ = [
    "GcNoBattleSetupResult",
    "GcNoBattleSetupStatus",
    "run_gc_no_battle_setup",
]
