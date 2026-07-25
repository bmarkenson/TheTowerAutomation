"""Guarded GC session preflight navigation and in-run correction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.auto_pick_perks import measure_auto_pick_perks
from core.gc_preflight import (
    GcSessionPreflightEvidence,
    merge_ultimate_weapon_observations,
    validate_gc_session_preflight_screens,
)
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import (
    HOME_BATTLE_CONTROL_REGION,
    HomeBattleEvidence,
    detect_home_battle_control,
)
from core.input import TapVerification, safe_tap, swipe_now, tap_if_visible
from core.poison_swamp_stun import (
    PoisonSwampStunResult,
    ensure_poison_swamp_stun_off,
)
from core.run_controls import go_home_from_run
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import detect_visible_boxes
from core.upgrade_navigation import swipe_upgrade_menu
from utils.logger import log


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]
HomeControlDetector = Callable[[Frame], HomeBattleEvidence]


class GcPreflightNavigationStatus(str, Enum):
    COMPLETE = "complete"
    MISMATCH = "mismatch"
    FAILED = "failed"
    BATTLE_ENDED = "battle_ended"


@dataclass(frozen=True)
class GcLivePreflightResult:
    status: GcPreflightNavigationStatus
    reason: str
    evidence: Optional[GcSessionPreflightEvidence] = None

    @property
    def valid(self) -> bool:
        return (
            self.status is GcPreflightNavigationStatus.COMPLETE
            and self.evidence is not None
            and self.evidence.valid
        )


class _NavigationFailure(RuntimeError):
    pass


class _BattleEnded(RuntimeError):
    pass


def _capture_detection(
    capture_fn: Capture,
    detector: Detector,
) -> tuple[Frame, Mapping[str, Any]]:
    frame = capture_fn()
    if frame is None:
        raise _NavigationFailure("screenshot capture failed")
    detection = detector(frame)
    if detection.get("state") in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
        raise _BattleEnded("natural terminal result observed during GC preflight")
    return frame, detection


def _wait_for(
    *,
    state: str,
    capture_fn: Capture,
    detector: Detector,
    secondary: Optional[str] = None,
    menu: Optional[str] = None,
    overlay: Optional[str] = None,
    timeout_s: float = 8.0,
    poll_s: float = 0.35,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Frame:
    attempts = max(1, int(max(0.1, timeout_s) / max(0.05, poll_s)))
    deadline = time.monotonic() + max(0.1, timeout_s)
    last_description = "no observation"
    for _ in range(attempts):
        frame, detection = _capture_detection(capture_fn, detector)
        detected_state = str(detection.get("state") or "UNKNOWN")
        detected_secondary = set(detection.get("secondary_states") or ())
        detected_overlays = set(detection.get("overlays") or ())
        detected_menu = detection.get("menu")
        last_description = (
            f"state={detected_state} menu={detected_menu} "
            f"secondary={sorted(detected_secondary)} "
            f"overlays={sorted(detected_overlays)}"
        )
        if (
            detected_state == state
            and (secondary is None or secondary in detected_secondary)
            and (menu is None or detected_menu == menu)
            and (overlay is None or overlay in detected_overlays)
        ):
            return frame
        if time.monotonic() >= deadline:
            break
        sleep_fn(poll_s)
    raise _NavigationFailure(
        f"timed out waiting for state={state} secondary={secondary} menu={menu} "
        f"overlay={overlay}; "
        f"last {last_description}"
    )


def _ensure_event_bots_top(
    current: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    event_swipe_fn: Callable[[str], bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    """Restore retained Event Bots scroll until its preset evidence is visible."""

    for _ in range(4):
        detection = detector(current)
        if (
            detection.get("state") == "EVENT"
            and "EVENT_BOTS_SCREEN"
            in set(detection.get("secondary_states") or ())
        ):
            return current
        if detection.get("state") != "EVENT":
            raise _NavigationFailure("Event Bots top-scroll guard lost EVENT")
        if not event_swipe_fn("gesture_targets.goto_top:event_bots"):
            raise _NavigationFailure("Event Bots top swipe failed")
        sleep_fn(0.6)
        current, _detection = _capture_detection(capture_fn, detector)
    raise _NavigationFailure("Event Bots preset evidence remained offscreen")


def _guarded_static_tap(
    key: str,
    *,
    allowed_states: set[str],
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
) -> None:
    _frame, detection = _capture_detection(capture_fn, detector)
    state = str(detection.get("state") or "UNKNOWN")
    if state not in allowed_states:
        raise _NavigationFailure(
            f"refusing {key}: state={state}, expected={sorted(allowed_states)}"
        )
    if not safe_tap_fn(key, dispatch="now"):
        raise _NavigationFailure(f"tap failed: {key}")


def _guarded_visible_tap(
    key: str,
    *,
    allowed_states: set[str],
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    retries: int = 1,
    retry_delay_s: float = 0.5,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    attempts = max(0, int(retries)) + 1
    for attempt in range(attempts):
        frame, detection = _capture_detection(capture_fn, detector)
        state = str(detection.get("state") or "UNKNOWN")
        if state not in allowed_states:
            raise _NavigationFailure(
                f"refusing {key}: state={state}, expected={sorted(allowed_states)}"
            )
        if tap_visible_fn(key, screenshot=frame, retries=0):
            return
        if attempt < attempts - 1:
            sleep_fn(max(0.0, float(retry_delay_s)))
    raise _NavigationFailure(f"visible tap failed: {key}")


def _ensure_auto_pick_perks_enabled(
    current: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
    measure_fn: Callable[[Frame], Any],
) -> Frame:
    """Enable Auto Pick only from a verified Perks screen and remeasure it."""

    evidence = measure_fn(current)
    if not evidence.valid_region:
        raise _NavigationFailure("Auto Pick Perks region was unavailable")
    if evidence.enabled:
        log("[GC_PREFLIGHT] Auto Pick Perks verified enabled", "INFO")
        return current

    if not safe_tap_fn(
        "buttons.perks:auto_pick",
        dispatch="now",
        verification=TapVerification(
            screenshot=current,
            target_region=evidence.region,
            description="auto_pick_perks:disabled_checkbox",
            verifier=lambda frame: (
                detector(frame).get("state") == "PERKS"
                and (candidate := measure_fn(frame)).valid_region
                and not candidate.enabled
            ),
        ),
    ):
        raise _NavigationFailure("Auto Pick Perks checkbox tap failed")
    for _attempt in range(24):
        frame, detection = _capture_detection(capture_fn, detector)
        if detection.get("state") != "PERKS":
            raise _NavigationFailure(
                "Auto Pick Perks correction lost the Perks screen"
            )
        evidence = measure_fn(frame)
        if evidence.valid_region and evidence.enabled:
            log(
                "[GC_PREFLIGHT] Auto Pick Perks verified enabled after correction",
                "INFO",
            )
            return frame
        sleep_fn(0.35)
    raise _NavigationFailure(
        "Auto Pick Perks did not become enabled after the guarded toggle"
    )


def _select_running_menu(
    key: str,
    menu: str,
    *,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    """Select an in-run menu, allowing one tap to be consumed by Cinematic Mode."""

    frame, detection = _capture_detection(capture_fn, detector)
    state = str(detection.get("state") or "UNKNOWN")
    if state != "RUNNING":
        raise _NavigationFailure(
            f"refusing {key}: state={state}, expected=['RUNNING']"
        )
    if detection.get("menu") == menu:
        return frame

    last_failure: Optional[Exception] = None
    for _ in range(2):
        _guarded_visible_tap(
            key,
            allowed_states={"RUNNING"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        try:
            return _wait_for(
                state="RUNNING",
                menu=menu,
                capture_fn=capture_fn,
                detector=detector,
                timeout_s=6.0,
                sleep_fn=sleep_fn,
            )
        except _NavigationFailure as exc:
            last_failure = exc
            _frame, detection = _capture_detection(capture_fn, detector)
            if detection.get("state") != "RUNNING":
                raise
    raise _NavigationFailure(
        f"could not select {menu} after a guarded retry: {last_failure}"
    )


def _ensure_running_side_menu_open(
    *,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    """Open the in-run side menu only when fresh overlay evidence requires it."""

    frame, detection = _capture_detection(capture_fn, detector)
    if detection.get("state") != "RUNNING":
        raise _NavigationFailure("side-menu guard lost RUNNING")
    overlays = set(detection.get("overlays") or ())
    if "MENU_OPEN" in overlays:
        return frame
    if "MENU_CLOSED" not in overlays:
        raise _NavigationFailure(
            "in-run menu was neither verified open nor verified closed"
        )
    _guarded_visible_tap(
        "navigation.menu_open_button",
        allowed_states={"RUNNING"},
        capture_fn=capture_fn,
        detector=detector,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    return _wait_for(
        state="RUNNING",
        overlay="MENU_OPEN",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _return_to_game_from_section(
    *,
    state: str,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    """Use the visible in-run return strip and verify the battle view."""

    _guarded_visible_tap(
        "buttons.return_to_game",
        allowed_states={state},
        capture_fn=capture_fn,
        detector=detector,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    return _wait_for(
        state="RUNNING",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _verify_active_home(
    frame: Frame,
    detect_home_control_fn: HomeControlDetector,
) -> None:
    evidence = detect_home_control_fn(frame)
    if evidence.control is HomeBattleControl.NEW_BATTLE:
        raise _BattleEnded("Home changed to NEW_BATTLE during GC preflight")
    if evidence.control is not HomeBattleControl.RESUME_BATTLE:
        raise _NavigationFailure(
            "Home Resume control was not verified "
            f"(source={evidence.source}, confidence={evidence.confidence:.1f})"
        )


def _guarded_resume_battle(
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    detect_home_control_fn: HomeControlDetector,
) -> None:
    """Tap Resume only from fresh Home and Resume evidence on the same frame."""

    frame, detection = _capture_detection(capture_fn, detector)
    if detection.get("state") != "HOME_SCREEN":
        raise _NavigationFailure(
            "refusing battle control without fresh HOME_SCREEN evidence"
        )
    _verify_active_home(frame, detect_home_control_fn)
    if not safe_tap_fn(
        "buttons.battle_control:home",
        dispatch="now",
        verification=TapVerification(
            screenshot=frame,
            target_region=HOME_BATTLE_CONTROL_REGION,
            description="home_battle_control:resume",
            verifier=lambda screenshot: (
                detector(screenshot).get("state") == "HOME_SCREEN"
                and detect_home_control_fn(screenshot).control
                is HomeBattleControl.RESUME_BATTLE
            ),
        ),
    ):
        raise _NavigationFailure("Resume Battle tap failed")


def _return_to_running(
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    tap_visible_fn: Callable[..., bool],
    detect_home_control_fn: HomeControlDetector,
    sleep_fn: Callable[[float], None],
) -> None:
    """Best-effort guarded cleanup; never acts after Game Over evidence."""

    try:
        frame, detection = _capture_detection(capture_fn, detector)
        state = str(detection.get("state") or "UNKNOWN")
        if state == "RUNNING":
            return
        if state == "BATTLE_HEAT":
            if tap_visible_fn(
                "buttons.close:tournament_heat",
                screenshot=frame,
                retries=1,
            ):
                _wait_for(
                    state="RUNNING",
                    capture_fn=capture_fn,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )
            return
        if state == "CARDS":
            if tap_visible_fn("buttons.return_to_game", screenshot=frame, retries=1):
                _wait_for(
                    state="RUNNING",
                    capture_fn=capture_fn,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )
            return
        if state == "PERKS":
            if tap_visible_fn("buttons.close:perks", screenshot=frame, retries=1):
                _wait_for(
                    state="RUNNING",
                    capture_fn=capture_fn,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )
            return
        if state in {"MODULES", "EVENT", "GUILD"}:
            if tap_visible_fn(
                "buttons.return_to_game",
                screenshot=frame,
                retries=1,
            ):
                _wait_for(
                    state="RUNNING",
                    capture_fn=capture_fn,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )
                return
        if state in {"WORKSHOP", "MODULES", "EVENT", "GUILD"}:
            if not safe_tap_fn("navigation.goto_home", dispatch="now"):
                return
            frame = _wait_for(
                state="HOME_SCREEN",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            state = "HOME_SCREEN"
        if state == "HOME_SCREEN":
            _guarded_resume_battle(
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                detect_home_control_fn=detect_home_control_fn,
            )
            _wait_for(
                state="RUNNING",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
    except (_NavigationFailure, _BattleEnded) as exc:
        log(f"[GC_PREFLIGHT] Cleanup stopped: {exc}", "WARN")


def _home_ultimate_weapon_observations(
    no_battle_setup_evidence: Optional[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Accept only fresh Home proof for the supported Poison Stun control."""

    if not isinstance(no_battle_setup_evidence, Mapping):
        return {}
    candidate = no_battle_setup_evidence.get("ultimate_weapons")
    if not isinstance(candidate, Mapping):
        return {}
    checked_values = candidate.get("checked")
    if not isinstance(checked_values, (list, tuple)):
        return {}
    checked = {
        str(value or "").strip().lower()
        for value in checked_values
    }
    observations = candidate.get("observations")
    if (
        str(candidate.get("boundary") or "").strip().upper() != "NEW_BATTLE"
        or candidate.get("valid") is not True
        or "poison swamp.stun" not in checked
        or not isinstance(observations, Mapping)
    ):
        return {}
    for label, toggles in observations.items():
        if (
            str(label or "").strip().lower() == "poison swamp"
            and isinstance(toggles, Mapping)
            and str(toggles.get("stun") or "").strip().lower() == "off"
        ):
            return {"Poison Swamp": {"stun": "off"}}
    return {}


def _ultimate_observations_complete(
    requirements: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Any]],
) -> bool:
    observed = {
        str(label or "").strip().lower(): {
            str(toggle or "").strip().lower()
            for toggle in toggles
        }
        for label, toggles in observations.items()
        if isinstance(toggles, Mapping)
    }
    for label, toggles in requirements.items():
        if not isinstance(toggles, Mapping):
            return False
        required_toggles = {
            str(toggle or "").strip().lower() for toggle in toggles
        }
        if not required_toggles <= observed.get(
            str(label or "").strip().lower(),
            set(),
        ):
            return False
    return True


def run_read_only_gc_preflight(
    requirements: Mapping[str, Any],
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    go_home_fn: Callable[[], bool] = go_home_from_run,
    swipe_fn: Callable[[str, str], None] = swipe_upgrade_menu,
    event_swipe_fn: Callable[[str], bool] = swipe_now,
    detect_boxes_fn: Callable[
        ..., Mapping[str, list[Any]]
    ] = detect_visible_boxes,
    ensure_poison_swamp_stun_fn: Callable[
        ..., PoisonSwampStunResult
    ] = ensure_poison_swamp_stun_off,
    measure_auto_pick_fn: Callable[[Frame], Any] = measure_auto_pick_perks,
    no_battle_setup_evidence: Optional[Mapping[str, Any]] = None,
    free_upgrade_lock_boundary_evidence: Optional[Mapping[str, Any]] = None,
    detect_home_control_fn: HomeControlDetector = detect_home_battle_control,
    sleep_fn: Callable[[float], None] = time.sleep,
    validate_fn: Callable[
        ..., GcSessionPreflightEvidence
    ] = validate_gc_session_preflight_screens,
) -> GcLivePreflightResult:
    """Verify GC requirements, apply safe in-run corrections, and return."""

    route_completed = False
    try:
        ultimate_requirements = requirements.get("ultimate_weapons")
        if not isinstance(ultimate_requirements, Mapping):
            raise _NavigationFailure(
                "profile did not supply Ultimate Weapon requirements"
            )
        raw_policies = requirements.get("loadout_policies") or {}
        if not isinstance(raw_policies, Mapping):
            raise _NavigationFailure("profile loadout policies were invalid")
        module_mode = str(raw_policies.get("modules") or "enforce").strip().lower()
        if module_mode not in {"enforce", "observe", "preserve"}:
            raise _NavigationFailure(
                f"unsupported module policy {module_mode!r}"
            )
        module_requirements = requirements.get("modules")
        if module_mode != "preserve" and not isinstance(
            module_requirements,
            Mapping,
        ):
            raise _NavigationFailure(
                "profile did not supply module requirements"
            )
        configuration_boundary_evidence = (
            no_battle_setup_evidence.get("configuration")
            if isinstance(no_battle_setup_evidence, Mapping)
            else None
        )
        module_boundary_evidence = (
            no_battle_setup_evidence.get("modules")
            if isinstance(no_battle_setup_evidence, Mapping)
            else None
        )
        ultimate_boundary_observations = _home_ultimate_weapon_observations(
            no_battle_setup_evidence
        )
        use_no_battle_evidence = bool(
            isinstance(configuration_boundary_evidence, Mapping)
            and (
                module_mode == "preserve"
                or isinstance(module_boundary_evidence, Mapping)
            )
        )
        free_upgrade_lock_requirements = requirements.get("free_upgrade_locks")
        _wait_for(
            state="RUNNING",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )

        cards = None
        if not use_no_battle_evidence:
            _ensure_running_side_menu_open(
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )

            _guarded_visible_tap(
                "navigation.Cards",
                allowed_states={"RUNNING"},
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
            )
            cards = _wait_for(
                state="CARDS",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            _guarded_visible_tap(
                "buttons.return_to_game",
                allowed_states={"CARDS"},
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
            )
            _wait_for(
                state="RUNNING",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )

        auto_pick_perks = requirements.get("auto_pick_perks")
        if auto_pick_perks not in {True, False, None}:
            raise _NavigationFailure(
                "profile supplied an invalid Auto Pick Perks requirement"
            )
        perks = None
        if auto_pick_perks:
            _guarded_static_tap(
                "navigation.open_perks",
                allowed_states={"RUNNING"},
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
            )
            perks = _wait_for(
                state="PERKS",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            perks = _ensure_auto_pick_perks_enabled(
                perks,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                sleep_fn=sleep_fn,
                measure_fn=measure_auto_pick_fn,
            )
            _guarded_visible_tap(
                "buttons.close:perks",
                allowed_states={"PERKS"},
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
            )
            _wait_for(
                state="RUNNING",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )

        _select_running_menu(
            "navigation.goto_uw",
            "UW_MENU",
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        for _ in range(3):
            _frame, detection = _capture_detection(capture_fn, detector)
            if (
                detection.get("state") != "RUNNING"
                or detection.get("menu") != "UW_MENU"
            ):
                raise _NavigationFailure("UW menu guard failed before top scroll")
            swipe_fn("towards_top", "extended")
            sleep_fn(0.5)

        ultimate_observations: dict[str, dict[str, str]] = {
            label: dict(toggles)
            for label, toggles in ultimate_boundary_observations.items()
        }
        poison_swamp_label: Optional[str] = None
        poison_swamp_stun_required = False
        for label, toggles in ultimate_requirements.items():
            if str(label).strip().lower() != "poison swamp":
                continue
            poison_swamp_label = str(label).strip()
            if isinstance(toggles, Mapping) and str(
                toggles.get("stun") or ""
            ).strip().lower() == "off":
                poison_swamp_stun_required = True
            break
        poison_swamp_stun_observed = (
            not poison_swamp_stun_required
            or any(
                str(label).strip().lower() == "poison swamp"
                and str(toggles.get("stun") or "").strip().lower() == "off"
                for label, toggles in ultimate_observations.items()
            )
        )
        for position in range(6):
            frame = _wait_for(
                state="RUNNING",
                menu="UW_MENU",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            boxes_by_column = detect_boxes_fn(frame, menu="ultimate weapons")
            visible = [
                box
                for column in boxes_by_column.values()
                for box in (column or [])
            ]
            visible_observations = merge_ultimate_weapon_observations(visible)
            for label, toggles in visible_observations.items():
                ultimate_observations.setdefault(label, {}).update(toggles)
            poison_boxes = [
                box
                for box in visible
                if str(getattr(box, "text", "") or "").strip().lower()
                == "poison swamp"
            ]
            if (
                poison_swamp_stun_required
                and not poison_swamp_stun_observed
                and poison_boxes
            ):
                result = ensure_poison_swamp_stun_fn(
                    screenshot=frame,
                    capture_fn=capture_fn,
                    detector=detector,
                    detect_boxes_fn=detect_boxes_fn,
                    safe_tap_fn=safe_tap_fn,
                    tap_visible_fn=tap_visible_fn,
                    sleep_fn=sleep_fn,
                )
                frame = result.screenshot
                ultimate_observations.setdefault(
                    poison_swamp_label or "Poison Swamp",
                    {},
                )["stun"] = result.evidence.state.value
                poison_swamp_stun_observed = True
                log(
                    "[GC_PREFLIGHT] Poison Swamp Stun verified off"
                    + (" after correction" if result.changed else ""),
                    "INFO",
                )
            if (
                _ultimate_observations_complete(
                    ultimate_requirements,
                    ultimate_observations,
                )
                and poison_swamp_stun_observed
            ):
                break
            if position < 5:
                swipe_fn("towards_bottom", "medium")
                sleep_fn(0.5)

        if use_no_battle_evidence:
            validation_args = dict(
                cards_screen=None,
                workshop_screen=None,
                bots_screen=None,
                guardians_screen=None,
                modules_screen=None,
                perks_screen=perks,
                module_requirements=module_requirements,
                module_mode=module_mode,
                ultimate_requirements=ultimate_requirements,
                ultimate_observations=ultimate_observations,
                configuration_boundary_evidence=(
                    configuration_boundary_evidence
                ),
                module_boundary_evidence=module_boundary_evidence,
                detector=detector,
            )
            waivers = requirements.get("_gate_waivers")
            if isinstance(waivers, Mapping) and waivers:
                validation_args["waivers"] = dict(waivers)
            if free_upgrade_lock_requirements is not None:
                validation_args.update(
                    free_upgrade_lock_requirements=(
                        free_upgrade_lock_requirements
                    ),
                    free_upgrade_lock_boundary_evidence=(
                        free_upgrade_lock_boundary_evidence
                    ),
                )
            evidence = validate_fn(**validation_args)
            route_completed = True
            status = (
                GcPreflightNavigationStatus.COMPLETE
                if evidence.valid
                else GcPreflightNavigationStatus.MISMATCH
            )
            if not evidence.valid:
                reason = "configuration mismatch"
            elif getattr(evidence, "deferred_checks", ()):
                reason = "active requirements verified; boundary checks deferred"
            else:
                reason = "all requirements verified"
            return GcLivePreflightResult(status, reason, evidence)

        modules = None
        if module_mode != "preserve":
            _ensure_running_side_menu_open(
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            _guarded_visible_tap(
                "navigation.menu_modules",
                allowed_states={"RUNNING"},
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            modules = _wait_for(
                state="MODULES",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            _return_to_game_from_section(
                state="MODULES",
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )

        _ensure_running_side_menu_open(
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        _guarded_visible_tap(
            "navigation.menu_event",
            allowed_states={"RUNNING"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        _wait_for(
            state="EVENT",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _guarded_visible_tap(
            "navigation.event:bots_tab",
            allowed_states={"EVENT"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        sleep_fn(0.5)
        bots = _wait_for(
            state="EVENT",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        bots = _ensure_event_bots_top(
            bots,
            capture_fn=capture_fn,
            detector=detector,
            event_swipe_fn=event_swipe_fn,
            sleep_fn=sleep_fn,
        )
        _return_to_game_from_section(
            state="EVENT",
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )

        _ensure_running_side_menu_open(
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        _guarded_visible_tap(
            "navigation.menu_guild",
            allowed_states={"RUNNING"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        _wait_for(
            state="GUILD",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _guarded_visible_tap(
            "navigation.guild:guardian_tab",
            allowed_states={"GUILD"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        guardians = _wait_for(
            state="GUILD",
            secondary="GUILD_GUARDIAN_SCREEN",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _return_to_game_from_section(
            state="GUILD",
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )

        # The Workshop preset remains an active session requirement, so it is
        # inspected through the verified resumable Home route. Free Upgrade
        # locks are deliberately excluded: only NEW_BATTLE no-battle setup can
        # inspect or enforce them authoritatively.
        if not go_home_fn():
            _capture_detection(capture_fn, detector)
            raise _NavigationFailure("guarded Go Home failed")
        home = _wait_for(
            state="HOME_SCREEN",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _verify_active_home(home, detect_home_control_fn)

        _guarded_static_tap(
            "navigation.goto_workshop_home",
            allowed_states={"HOME_SCREEN"},
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
        )
        workshop = _wait_for(
            state="WORKSHOP",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _guarded_static_tap(
            "navigation.goto_home",
            allowed_states={"WORKSHOP"},
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
        )
        home = _wait_for(
            state="HOME_SCREEN",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _verify_active_home(home, detect_home_control_fn)
        _guarded_resume_battle(
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            detect_home_control_fn=detect_home_control_fn,
        )
        _wait_for(
            state="RUNNING",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        route_completed = True

        validation_args = dict(
            cards_screen=cards,
            workshop_screen=workshop,
            bots_screen=bots,
            guardians_screen=guardians,
            modules_screen=modules,
            perks_screen=perks,
            module_requirements=module_requirements,
            module_mode=module_mode,
            ultimate_requirements=ultimate_requirements,
            ultimate_observations=ultimate_observations,
            detector=detector,
        )
        waivers = requirements.get("_gate_waivers")
        if isinstance(waivers, Mapping) and waivers:
            validation_args["waivers"] = dict(waivers)
        if free_upgrade_lock_requirements is not None:
            validation_args.update(
                free_upgrade_lock_requirements=free_upgrade_lock_requirements,
                free_upgrade_lock_boundary_evidence=(
                    free_upgrade_lock_boundary_evidence
                ),
            )
        evidence = validate_fn(**validation_args)
        status = (
            GcPreflightNavigationStatus.COMPLETE
            if evidence.valid
            else GcPreflightNavigationStatus.MISMATCH
        )
        if not evidence.valid:
            reason = "configuration mismatch"
        elif getattr(evidence, "deferred_checks", ()):
            reason = "active requirements verified; boundary checks deferred"
        else:
            reason = "all requirements verified"
        return GcLivePreflightResult(status, reason, evidence)
    except _BattleEnded as exc:
        return GcLivePreflightResult(
            GcPreflightNavigationStatus.BATTLE_ENDED,
            str(exc),
        )
    except Exception as exc:
        return GcLivePreflightResult(
            GcPreflightNavigationStatus.FAILED,
            str(exc),
        )
    finally:
        if not route_completed:
            _return_to_running(
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                tap_visible_fn=tap_visible_fn,
                detect_home_control_fn=detect_home_control_fn,
                sleep_fn=sleep_fn,
            )


__all__ = [
    "GcLivePreflightResult",
    "GcPreflightNavigationStatus",
    "run_read_only_gc_preflight",
]
