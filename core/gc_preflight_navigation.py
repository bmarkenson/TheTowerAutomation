"""Guarded, read-only navigation for the GC session preflight."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.gc_preflight import (
    GcSessionPreflightEvidence,
    merge_ultimate_weapon_observations,
    validate_gc_session_preflight_screens,
)
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import HomeBattleEvidence, detect_home_battle_control
from core.input import safe_tap, tap_if_visible
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
    if detection.get("state") == "GAME_OVER":
        raise _BattleEnded("natural Game Over observed during GC preflight")
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
    if not safe_tap_fn(key, require_visible=False, dispatch="now"):
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


def _select_running_menu(
    key: str,
    menu: str,
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    """Select an in-run menu, allowing one tap to be consumed by Cinematic Mode."""

    last_failure: Optional[Exception] = None
    for _ in range(2):
        _guarded_static_tap(
            key,
            allowed_states={"RUNNING"},
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
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
        require_visible=False,
        dispatch="now",
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
        if state in {"WORKSHOP", "EVENT", "GUILD"}:
            if not safe_tap_fn(
                "navigation.goto_home", require_visible=False, dispatch="now"
            ):
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


def run_read_only_gc_preflight(
    requirements: Mapping[str, Any],
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    go_home_fn: Callable[[], bool] = go_home_from_run,
    swipe_fn: Callable[[str, str], None] = swipe_upgrade_menu,
    detect_boxes_fn: Callable[
        ..., Mapping[str, list[Any]]
    ] = detect_visible_boxes,
    detect_home_control_fn: HomeControlDetector = detect_home_battle_control,
    sleep_fn: Callable[[float], None] = time.sleep,
    validate_fn: Callable[
        ..., GcSessionPreflightEvidence
    ] = validate_gc_session_preflight_screens,
) -> GcLivePreflightResult:
    """Inspect every GC session requirement and return to the same battle."""

    route_completed = False
    try:
        ultimate_requirements = requirements.get("ultimate_weapons")
        if not isinstance(ultimate_requirements, Mapping):
            raise _NavigationFailure(
                "profile did not supply Ultimate Weapon requirements"
            )
        _wait_for(
            state="RUNNING",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )

        frame, detection = _capture_detection(capture_fn, detector)
        overlays = set(detection.get("overlays") or ())
        if "MENU_OPEN" not in overlays:
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
            )
            _wait_for(
                state="RUNNING",
                overlay="MENU_OPEN",
                capture_fn=capture_fn,
                detector=detector,
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
            safe_tap_fn=safe_tap_fn,
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

        ultimate_observations: dict[str, dict[str, str]] = {}
        required_labels = {str(label).strip().lower() for label in ultimate_requirements}
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
            ultimate_observations.update(merge_ultimate_weapon_observations(visible))
            observed_labels = {label.lower() for label in ultimate_observations}
            if required_labels <= observed_labels:
                break
            if position < 5:
                swipe_fn("towards_bottom", "medium")
                sleep_fn(0.5)

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

        _guarded_visible_tap(
            "navigation.home_event",
            allowed_states={"HOME_SCREEN"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            retries=16,
            retry_delay_s=0.5,
            sleep_fn=sleep_fn,
        )
        _wait_for(
            state="EVENT",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _guarded_static_tap(
            "navigation.event:bots_tab",
            allowed_states={"EVENT"},
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
        )
        bots = _wait_for(
            state="EVENT",
            secondary="EVENT_BOTS_SCREEN",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _guarded_static_tap(
            "navigation.goto_home",
            allowed_states={"EVENT"},
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

        _guarded_visible_tap(
            "navigation.home_guild",
            allowed_states={"HOME_SCREEN"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            retries=16,
            retry_delay_s=0.5,
            sleep_fn=sleep_fn,
        )
        _wait_for(
            state="GUILD",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _guarded_static_tap(
            "navigation.guild:guardian_tab",
            allowed_states={"GUILD"},
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
        )
        guardians = _wait_for(
            state="GUILD",
            secondary="GUILD_GUARDIAN_SCREEN",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        _guarded_static_tap(
            "navigation.goto_home",
            allowed_states={"GUILD"},
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

        evidence = validate_fn(
            cards_screen=cards,
            workshop_screen=workshop,
            bots_screen=bots,
            guardians_screen=guardians,
            perks_screen=perks,
            ultimate_requirements=ultimate_requirements,
            ultimate_observations=ultimate_observations,
            detector=detector,
        )
        status = (
            GcPreflightNavigationStatus.COMPLETE
            if evidence.valid
            else GcPreflightNavigationStatus.MISMATCH
        )
        reason = "all requirements verified" if evidence.valid else "configuration mismatch"
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
