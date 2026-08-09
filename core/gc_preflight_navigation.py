"""Guarded GC session preflight navigation and in-run correction."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from functools import wraps
import time
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.auto_pick_perks import measure_auto_pick_perks
from core.gc_module_loadout import gc_module_loadout_evidence_from_assignments
from core.gc_preflight import (
    GcSessionPreflightEvidence,
    merge_ultimate_weapon_observations,
    summarize_gc_preflight_mismatch,
    summarize_gc_preflight_variations,
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
    ensure_poison_swamp_stun,
)
from core.player_save import save_check_matches_requirement
from core.player_save_temporal import ROUND_INVARIANT_ATTACHMENT_CHECKS
from core.run_controls import go_home_from_run
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import detect_visible_boxes
from core.upgrade_navigation import swipe_upgrade_menu
from utils.logger import log, log_action_intent, log_result


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]
HomeControlDetector = Callable[[Frame], HomeBattleEvidence]


_ATTACHMENT_SAVE_ONLY_REQUIREMENT_CHECKS = (
    "card_recharge_modes",
    "perk_first_choice",
    "perk_bans",
    "perk_auto_pick_order",
)


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


def _log_gc_preflight_workflow(func):
    """Pair the complete preflight route, including its cleanup, with a result."""

    @wraps(func)
    def wrapped(requirements: Mapping[str, Any], *args, **kwargs):
        log_action_intent(
            "Checking session configuration",
            reason=(
                "confirm active loadouts and combat settings match the selected "
                "run profile before automation continues"
            ),
            detail=(
                f"[GC_PREFLIGHT] requirements={sorted(requirements)} "
                f"uses_home_evidence={kwargs.get('no_battle_setup_evidence') is not None} "
                f"stay_in_battle={kwargs.get('stay_in_battle') is True}"
            ),
        )
        try:
            result = func(requirements, *args, **kwargs)
        except Exception as exc:
            log_result(
                f"Session configuration check failed — {exc}",
                detail=(
                    "[GC_PREFLIGHT] result=failed "
                    f"unhandled_exception={exc!r}"
                ),
            )
            raise

        evidence = result.evidence
        as_dict = getattr(evidence, "as_dict", None)
        evidence_payload = as_dict() if callable(as_dict) else {}
        variation_summary = summarize_gc_preflight_variations(evidence_payload)
        if result.status is GcPreflightNavigationStatus.COMPLETE:
            if variation_summary:
                variation_kind = (
                    "immutable attachment mismatch reported"
                    if evidence_payload.get("reported_attachment_mismatches")
                    else "module variation observed"
                )
                summary = (
                    "Session configuration check complete — required settings "
                    f"verified; {variation_kind} — {variation_summary}"
                )
                if result.reason.endswith("boundary checks deferred"):
                    summary += "; boundary checks deferred"
            elif result.reason.endswith("boundary checks deferred"):
                summary = (
                    "Session configuration check complete — active requirements "
                    "verified; boundary checks deferred"
                )
            else:
                summary = (
                    "Session configuration check complete — all requirements "
                    "verified"
                )
        elif result.status is GcPreflightNavigationStatus.MISMATCH:
            mismatch_summary = summarize_gc_preflight_mismatch(
                evidence_payload
            )
            summary = (
                "Session configuration check complete — mismatch found; "
                f"{mismatch_summary}"
            )
        elif result.status is GcPreflightNavigationStatus.BATTLE_ENDED:
            summary = (
                "Session configuration check interrupted — the battle ended "
                "during inspection"
            )
        else:
            summary = f"Session configuration check failed — {result.reason}"
        log_result(
            summary,
            detail=(
                f"[GC_PREFLIGHT] result={result.status.value} "
                f"reason={result.reason} valid={result.valid} "
                f"deferred_checks={list(getattr(evidence, 'deferred_checks', ()))}"
            ),
        )
        return result

    return wrapped


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
        if tap_visible_fn(
            key,
            screenshot=frame,
            retries=0,
            failure_log_level="DEBUG",
        ):
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
        stun_state = (
            str(toggles.get("stun") or "").strip().lower()
            if isinstance(toggles, Mapping)
            else ""
        )
        if (
            str(label or "").strip().lower() == "poison swamp"
            and stun_state in {"on", "off"}
        ):
            return {"Poison Swamp": {"stun": stun_state}}
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


@_log_gc_preflight_workflow
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
    ] = ensure_poison_swamp_stun,
    measure_auto_pick_fn: Callable[[Frame], Any] = measure_auto_pick_perks,
    no_battle_setup_evidence: Optional[Mapping[str, Any]] = None,
    free_upgrade_lock_boundary_evidence: Optional[Mapping[str, Any]] = None,
    player_save_preflight: Any = None,
    stay_in_battle: bool = False,
    detect_home_control_fn: HomeControlDetector = detect_home_battle_control,
    sleep_fn: Callable[[float], None] = time.sleep,
    validate_fn: Callable[
        ..., GcSessionPreflightEvidence
    ] = validate_gc_session_preflight_screens,
) -> GcLivePreflightResult:
    """Verify session requirements and return to the original battle.

    When ``stay_in_battle`` is true, the route never invokes the resumable game
    Home path. Exact bound save facts may replace their redundant UI
    observations; the evidence owner decides which temporal classes are safe
    to consume. Any unresolved Home-only Workshop preset check is reported as
    deferred.
    """

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
        ultimate_boundary_payload = (
            no_battle_setup_evidence.get("ultimate_weapons")
            if isinstance(no_battle_setup_evidence, Mapping)
            else None
        )
        ultimate_boundary_save_backed = bool(
            isinstance(ultimate_boundary_payload, Mapping)
            and ultimate_boundary_payload.get("source")
            in {"player_save_preflight", "bound_player_save_preflight"}
        )
        use_no_battle_evidence = bool(
            isinstance(configuration_boundary_evidence, Mapping)
            and (
                module_mode == "preserve"
                or isinstance(module_boundary_evidence, Mapping)
            )
        )
        free_upgrade_lock_requirements = requirements.get("free_upgrade_locks")
        consume_save = getattr(player_save_preflight, "consume", None)
        fallback_save_checks = getattr(
            player_save_preflight,
            "fallback_checks",
            None,
        )
        attachment_temporal_class = getattr(
            player_save_preflight,
            "temporal_class",
            None,
        )
        attachment_report_only = getattr(
            player_save_preflight,
            "mismatch_is_report_only",
            None,
        )
        running_attachment = bool(
            getattr(player_save_preflight, "is_running_attachment", False)
        )
        gate_waivers = requirements.get("_gate_waivers")
        active_gate_waivers = (
            gate_waivers if isinstance(gate_waivers, Mapping) else {}
        )
        attachment_requirement_checks: dict[str, dict[str, Any]] = {}
        reported_attachment_mismatches: dict[str, dict[str, Any]] = {}
        attachment_report_only_requirements = (
            {
                check_id: copy.deepcopy(requirements[check_id])
                for check_id in ROUND_INVARIANT_ATTACHMENT_CHECKS
                if check_id in requirements
                and check_id not in active_gate_waivers
            }
            if stay_in_battle
            else {}
        )

        def fallback_carried_check(check_id: str, reason: str) -> None:
            if callable(fallback_save_checks):
                fallback_save_checks(reason, check_ids=(check_id,))

        def temporal_class_name(check_id: str) -> str:
            if not callable(attachment_temporal_class):
                return ""
            temporal_class = attachment_temporal_class(check_id)
            return str(getattr(temporal_class, "value", temporal_class) or "")

        def record_attachment_save_check(
            check_id: str,
            expected: Any,
            observed: Any,
            *,
            matches: Optional[bool],
            disposition: str,
            blocking: bool,
        ) -> dict[str, Any]:
            payload = {
                "source": "bound_player_save_preflight",
                "disposition": disposition,
                "expected": copy.deepcopy(expected),
                "observed": copy.deepcopy(observed),
                "valid": matches,
                "blocking": bool(blocking),
                "temporal_class": temporal_class_name(check_id),
            }
            attachment_requirement_checks[check_id] = payload
            if disposition == "save_mismatch_reported":
                reported_attachment_mismatches[check_id] = dict(payload)
            return payload

        def record_unavailable_attachment_check(check_id: str) -> None:
            attachment_requirement_checks[check_id] = {
                "source": "ui_fallback",
                "disposition": "unavailable_deferred",
                "expected": copy.deepcopy(requirements[check_id]),
                "observed": None,
                "valid": None,
                "blocking": False,
                "temporal_class": "",
                "reason": "attachment_save_fact_unavailable",
            }

        def saved_mismatch_disposition(
            check_id: str,
            expected: Any,
            observed: Any,
        ) -> str:
            report_only = bool(
                callable(attachment_report_only)
                and attachment_report_only(check_id)
            )
            disposition = (
                "save_mismatch_reported" if report_only else "save_mismatch"
            )
            record_attachment_save_check(
                check_id,
                expected,
                observed,
                matches=False,
                disposition=disposition,
                blocking=not report_only,
            )
            log(
                "[PLAYER_SAVE_ATTACHMENT] Authoritative saved mismatch "
                f"check={check_id} disposition={disposition} "
                f"temporal_class={temporal_class_name(check_id) or 'unknown'}",
                "WARN",
                console=True,
            )
            return disposition

        record_save_ui_verification = getattr(
            player_save_preflight,
            "record_ui_verification",
            None,
        )

        def record_ui_verification(check_id: str, *, changed: bool) -> None:
            if callable(record_save_ui_verification) and (
                record_save_ui_verification(check_id, changed=changed) is False
            ):
                raise _NavigationFailure(
                    "trusted player-save evidence contradicted current UI for "
                    f"{check_id}"
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
        auto_pick_skipped = "auto_pick_perks" in active_gate_waivers
        carried_auto_pick = (
            consume_save("auto_pick_perks")
            if callable(consume_save)
            else None
        )
        auto_pick_boundary_evidence = (
            {
                "source": "bound_player_save_preflight",
                "value": True,
            }
            if carried_auto_pick is True and auto_pick_perks is True
            else None
        )
        if (
            running_attachment
            and carried_auto_pick is not None
            and auto_pick_boundary_evidence is None
            and auto_pick_perks is not None
        ):
            log(
                "[PLAYER_SAVE_ATTACHMENT] Auto Pick mismatch requires the "
                "existing guarded in-battle repair",
                "INFO",
            )
        perks = None
        if (
            auto_pick_perks
            and not auto_pick_skipped
            and auto_pick_boundary_evidence is None
        ):
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
            auto_pick_before = measure_auto_pick_fn(perks)
            perks = _ensure_auto_pick_perks_enabled(
                perks,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                sleep_fn=sleep_fn,
                measure_fn=measure_auto_pick_fn,
            )
            record_ui_verification(
                "auto_pick_perks",
                changed=not auto_pick_before.enabled,
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

        ultimate_observations: dict[str, dict[str, str]] = {
            label: dict(toggles)
            for label, toggles in ultimate_boundary_observations.items()
        }
        save_ultimate_observations: dict[str, dict[str, str]] = (
            {
                label: dict(toggles)
                for label, toggles in ultimate_boundary_observations.items()
            }
            if ultimate_boundary_save_backed
            else {}
        )
        accepted_sections: dict[str, dict[str, Any]] = {}
        carried_module_boundary_evidence = None
        if not use_no_battle_evidence and callable(consume_save):
            for section, check_id in (
                ("cards", "cards_deck"),
                ("workshop", "workshop_preset"),
                ("bots", "bots_preset"),
                ("guardians", "guardian_chips"),
            ):
                if check_id not in requirements:
                    continue
                carried_value = consume_save(check_id)
                if carried_value is not None and save_check_matches_requirement(
                    check_id,
                    requirements[check_id],
                    carried_value,
                ):
                    accepted_sections[section] = {
                        "disposition": "save_match",
                        "source": "bound_player_save_preflight",
                    }
                elif carried_value is not None and running_attachment:
                    disposition = saved_mismatch_disposition(
                        check_id,
                        requirements[check_id],
                        carried_value,
                    )
                    accepted_sections[section] = {
                        "disposition": disposition,
                        "source": "bound_player_save_preflight",
                        "expected": copy.deepcopy(requirements[check_id]),
                        "observed": copy.deepcopy(carried_value),
                    }
                elif carried_value is not None:
                    fallback_carried_check(
                        check_id,
                        "configuration_boundary_requirement_changed",
                    )

            if module_mode != "preserve":
                carried_modules = consume_save("modules")
                if isinstance(carried_modules, Mapping):
                    carried_module_evidence = (
                        gc_module_loadout_evidence_from_assignments(
                            module_requirements,
                            carried_modules,
                        )
                    )
                    if carried_module_evidence.fully_observed and (
                        module_mode == "observe"
                        or carried_module_evidence.valid
                    ):
                        carried_module_boundary_evidence = {
                            **carried_module_evidence.as_dict(),
                            "source": "bound_player_save_preflight",
                            "status": (
                                "save_observation"
                                if module_mode == "observe"
                                else "save_match"
                            ),
                            "disposition": (
                                "save_observation"
                                if module_mode == "observe"
                                else "save_match"
                            ),
                            "checked": False,
                            "reason": "bound_player_save_configuration_observation",
                        }
                    elif (
                        carried_module_evidence.fully_observed
                        and running_attachment
                    ):
                        disposition = saved_mismatch_disposition(
                            "modules",
                            module_requirements,
                            carried_modules,
                        )
                        if disposition == "save_mismatch_reported":
                            carried_module_boundary_evidence = {
                                **carried_module_evidence.as_dict(),
                                "source": "bound_player_save_preflight",
                                "status": disposition,
                                "disposition": disposition,
                                "checked": False,
                                "reason": (
                                    "active_battle_module_loadout_is_immutable"
                                ),
                            }
                    else:
                        fallback_carried_check(
                            "modules",
                            "module_boundary_requirement_changed",
                        )
                elif carried_modules is not None:
                    fallback_carried_check(
                        "modules",
                        "module_boundary_requirement_changed",
                    )
            if (
                free_upgrade_lock_requirements is not None
                and free_upgrade_lock_boundary_evidence is None
            ):
                carried_locks = consume_save("free_upgrade_locks")
                if (
                    isinstance(carried_locks, list)
                    and save_check_matches_requirement(
                        "free_upgrade_locks",
                        free_upgrade_lock_requirements,
                        carried_locks,
                    )
                ):
                    free_upgrade_lock_boundary_evidence = {
                        "status": "save_match",
                        "source": "bound_player_save_preflight",
                        "boundary": HomeBattleControl.NEW_BATTLE.value,
                        "required": list(free_upgrade_lock_requirements),
                        "observed": list(carried_locks),
                        "checked": False,
                        "valid": True,
                    }
                elif carried_locks is not None:
                    if running_attachment:
                        saved_mismatch_disposition(
                            "free_upgrade_locks",
                            free_upgrade_lock_requirements,
                            carried_locks,
                        )
                    else:
                        fallback_carried_check(
                            "free_upgrade_locks",
                            "free_upgrade_lock_boundary_requirement_changed",
                        )
            if running_attachment:
                for check_id in _ATTACHMENT_SAVE_ONLY_REQUIREMENT_CHECKS:
                    if (
                        check_id not in requirements
                        or check_id in active_gate_waivers
                    ):
                        continue
                    carried_value = consume_save(check_id)
                    if carried_value is None:
                        record_unavailable_attachment_check(check_id)
                        continue
                    matches = save_check_matches_requirement(
                        check_id,
                        requirements[check_id],
                        carried_value,
                    )
                    if matches:
                        record_attachment_save_check(
                            check_id,
                            requirements[check_id],
                            carried_value,
                            matches=True,
                            disposition="save_match",
                            blocking=False,
                        )
                    else:
                        saved_mismatch_disposition(
                            check_id,
                            requirements[check_id],
                            carried_value,
                        )
        if stay_in_battle and not running_attachment:
            for check_id in _ATTACHMENT_SAVE_ONLY_REQUIREMENT_CHECKS:
                if (
                    check_id in requirements
                    and check_id not in active_gate_waivers
                ):
                    record_unavailable_attachment_check(check_id)
        carried_primaries = (
            consume_save("ultimate_weapon_primaries")
            if callable(consume_save)
            else None
        )
        if isinstance(carried_primaries, Mapping):
            for label, toggles in carried_primaries.items():
                if isinstance(toggles, Mapping):
                    ultimate_observations.setdefault(str(label), {}).update(
                        {
                            str(toggle): str(state)
                            for toggle, state in toggles.items()
                        }
                    )
                    save_ultimate_observations.setdefault(
                        str(label),
                        {},
                    ).update(
                        {
                            str(toggle): str(state)
                            for toggle, state in toggles.items()
                        }
                    )
        carried_stun = (
            consume_save("poison_swamp_stun")
            if callable(consume_save)
            else None
        )
        if carried_stun in {"on", "off"}:
            ultimate_observations.setdefault("Poison Swamp", {})["stun"] = (
                str(carried_stun)
            )
            save_ultimate_observations.setdefault("Poison Swamp", {})[
                "stun"
            ] = str(carried_stun)
        carried_missiles = (
            consume_save("spotlight_missiles")
            if callable(consume_save)
            else None
        )
        if carried_missiles in {"on", "off"}:
            ultimate_observations.setdefault("Spotlight", {})["missiles"] = str(
                carried_missiles
            )
            save_ultimate_observations.setdefault("Spotlight", {})[
                "missiles"
            ] = str(carried_missiles)
        poison_swamp_label: Optional[str] = None
        poison_swamp_stun_required: Optional[str] = None
        for label, toggles in ultimate_requirements.items():
            if str(label).strip().lower() != "poison swamp":
                continue
            poison_swamp_label = str(label).strip()
            if isinstance(toggles, Mapping) and "stun" in toggles:
                poison_swamp_stun_required = str(
                    toggles.get("stun") or ""
                ).strip().lower()
            break
        saved_stun_repair_required = bool(
            carried_stun in {"on", "off"}
            and poison_swamp_stun_required in {"on", "off"}
            and str(carried_stun) != poison_swamp_stun_required
        )
        ultimate_ui_required = bool(
            not _ultimate_observations_complete(
                ultimate_requirements,
                ultimate_observations,
            )
            or saved_stun_repair_required
        )
        ultimate_weapons_source = (
            "mixed"
            if save_ultimate_observations and ultimate_ui_required
            else "bound_player_save_preflight"
            if save_ultimate_observations
            else "ui"
        )
        normalized_save_ultimate_observations = {
            str(label).strip().casefold(): {
                str(toggle).strip().casefold(): str(state).strip().casefold()
                for toggle, state in toggles.items()
            }
            for label, toggles in save_ultimate_observations.items()
        }
        if ultimate_ui_required:
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
                    raise _NavigationFailure(
                        "UW menu guard failed before top scroll"
                    )
                swipe_fn("towards_top", "extended")
                sleep_fn(0.5)
        poison_swamp_stun_observed = (
            poison_swamp_stun_required is None
            or any(
                str(label).strip().lower() == "poison swamp"
                and str(toggles.get("stun") or "").strip().lower()
                == poison_swamp_stun_required
                for label, toggles in ultimate_observations.items()
            )
        )
        for position in range(6 if ultimate_ui_required else 0):
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
            contradiction_checks: set[str] = set()
            for label, toggles in visible_observations.items():
                normalized_label = str(label).strip().casefold()
                saved_toggles = normalized_save_ultimate_observations.get(
                    normalized_label,
                    {},
                )
                for toggle, state in toggles.items():
                    normalized_toggle = str(toggle).strip().casefold()
                    normalized_state = str(state).strip().casefold()
                    saved_state = saved_toggles.get(normalized_toggle)
                    if saved_state is None or saved_state == normalized_state:
                        continue
                    if normalized_toggle == "primary":
                        contradiction_checks.add("ultimate_weapon_primaries")
                    elif normalized_label == "poison swamp" and (
                        normalized_toggle == "stun"
                    ):
                        contradiction_checks.add("poison_swamp_stun")
                    elif normalized_label == "spotlight" and (
                        normalized_toggle == "missiles"
                    ):
                        contradiction_checks.add("spotlight_missiles")
            if contradiction_checks:
                invalidate = getattr(player_save_preflight, "invalidate", None)
                if callable(invalidate):
                    invalidate("save_ui_contradiction")
                log(
                    "[PLAYER_SAVE_PREFLIGHT] Current Ultimate Weapon UI "
                    "contradicted accepted save evidence: checks="
                    f"{sorted(contradiction_checks)}",
                    "ERROR",
                    console=True,
                )
                raise _NavigationFailure(
                    "accepted player-save Ultimate Weapon evidence "
                    "contradicted current UI"
                )
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
                    required_state=poison_swamp_stun_required,
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
                record_ui_verification(
                    "poison_swamp_stun",
                    changed=result.changed,
                )
                log(
                    "[GC_PREFLIGHT] Poison Swamp Stun verified "
                    f"{poison_swamp_stun_required}"
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

        cards = None
        if not use_no_battle_evidence and "cards" not in accepted_sections:
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
                ultimate_weapons_source=ultimate_weapons_source,
                configuration_boundary_evidence=(
                    configuration_boundary_evidence
                ),
                module_boundary_evidence=module_boundary_evidence,
                detector=detector,
            )
            if attachment_requirement_checks:
                validation_args["attachment_requirement_checks"] = (
                    attachment_requirement_checks
                )
            if reported_attachment_mismatches:
                validation_args["reported_attachment_mismatches"] = (
                    reported_attachment_mismatches
                )
            if attachment_report_only_requirements:
                validation_args["attachment_report_only_requirements"] = (
                    attachment_report_only_requirements
                )
            waivers = requirements.get("_gate_waivers")
            if isinstance(waivers, Mapping) and waivers:
                validation_args["waivers"] = dict(waivers)
            if auto_pick_boundary_evidence is not None:
                validation_args["auto_pick_boundary_evidence"] = (
                    auto_pick_boundary_evidence
                )
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
            elif getattr(evidence, "reported_attachment_mismatches", {}):
                reason = (
                    "active requirements checked; immutable mismatches reported"
                )
            elif getattr(evidence, "deferred_checks", ()):
                reason = "active requirements verified; boundary checks deferred"
            else:
                reason = "all requirements verified"
            return GcLivePreflightResult(status, reason, evidence)

        modules = None
        if (
            module_mode != "preserve"
            and carried_module_boundary_evidence is None
        ):
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

        bots = None
        if "bots" not in accepted_sections:
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

        guardians = None
        if "guardians" not in accepted_sections:
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

        deferred_checks: tuple[str, ...] = ()
        workshop = None
        if stay_in_battle:
            # Attached validation must not leave the current battle merely to
            # inspect a Home-only preset. Consume exact save evidence only when
            # it is already bound to this active run; otherwise defer the check.
            if "workshop" not in accepted_sections:
                deferred_checks = ("workshop_preset",)
        else:
            # A normal session preflight may inspect the Workshop preset through
            # the verified resumable Home route. Free Upgrade locks have no
            # in-battle UI route and require exact new-battle boundary evidence.
            if "workshop" not in accepted_sections:
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
            ultimate_weapons_source=ultimate_weapons_source,
            detector=detector,
        )
        if deferred_checks:
            validation_args["deferred_checks"] = deferred_checks
        if accepted_sections:
            validation_args["accepted_sections"] = accepted_sections
        if carried_module_boundary_evidence is not None:
            validation_args["module_boundary_evidence"] = (
                carried_module_boundary_evidence
            )
        if attachment_requirement_checks:
            validation_args["attachment_requirement_checks"] = (
                attachment_requirement_checks
            )
        if reported_attachment_mismatches:
            validation_args["reported_attachment_mismatches"] = (
                reported_attachment_mismatches
            )
        if attachment_report_only_requirements:
            validation_args["attachment_report_only_requirements"] = (
                attachment_report_only_requirements
            )
        waivers = requirements.get("_gate_waivers")
        if isinstance(waivers, Mapping) and waivers:
            validation_args["waivers"] = dict(waivers)
        if auto_pick_boundary_evidence is not None:
            validation_args["auto_pick_boundary_evidence"] = (
                auto_pick_boundary_evidence
            )
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
        elif getattr(evidence, "reported_attachment_mismatches", {}):
            reason = "active requirements checked; immutable mismatches reported"
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
