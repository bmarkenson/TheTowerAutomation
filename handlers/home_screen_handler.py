# handlers/home_screen_handler.py

import time
from utils.logger import log, log_action_intent, log_result
from core.input import ActionGuard, TapVerification, safe_tap, tap_if_visible
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import HOME_BATTLE_CONTROL_REGION, detect_home_battle_control
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays


def _tap_verified_home_battle_control(
    required_control: HomeBattleControl | None = None,
    *,
    action_guard_fn: ActionGuard = None,
) -> bool:
    """OCR and tap Battle/Resume only on a verified home screen."""

    screenshot = capture_adb_screenshot()
    if screenshot is None:
        return False
    detection = detect_state_and_overlays(screenshot)
    if detection["state"] != "HOME_SCREEN":
        log(
            f"[HOME] Refusing Battle fallback from state={detection['state']!r}",
            "WARN",
        )
        return False
    evidence = detect_home_battle_control(screenshot)
    if evidence.control is HomeBattleControl.UNKNOWN:
        log(
            f"[HOME] Battle fallback was not verified: source={evidence.source} "
            f"text={evidence.raw_text!r} confidence={evidence.confidence:.1f}",
            "WARN",
        )
        return False
    if required_control is not None and evidence.control is not required_control:
        log(
            f"[HOME] Refusing {evidence.control.value}; this action requires "
            f"{required_control.value}",
            "WARN",
        )
        return False
    log(
        f"[HOME] Verified {evidence.control.value} via {evidence.source} "
        f"(confidence={evidence.confidence:.1f})",
        "DEBUG",
    )
    return safe_tap(
        "buttons.battle_control:home",
        dispatch="now",
        verification=TapVerification(
            screenshot=screenshot,
            target_region=HOME_BATTLE_CONTROL_REGION,
            description=f"home_battle_control:{evidence.control.value}",
            verifier=lambda frame: (
                detect_state_and_overlays(frame)["state"] == "HOME_SCREEN"
                and detect_home_battle_control(frame).control is evidence.control
            ),
        ),
        action_guard_fn=action_guard_fn,
    )


def tap_verified_new_battle(*, action_guard_fn: ActionGuard = None) -> bool:
    """Tap only the ordinary Home NEW_BATTLE control on fresh evidence."""

    return _tap_verified_home_battle_control(
        HomeBattleControl.NEW_BATTLE,
        action_guard_fn=action_guard_fn,
    )


def handle_home_screen(
    restart_enabled: bool = True,
    *,
    require_new_battle: bool = False,
    require_resume_battle: bool = False,
    operation_id: str | None = None,
    action_purpose: str | None = None,
    action_reason: str | None = None,
    action_guard_fn: ActionGuard = None,
) -> bool:
    """
    Handle the HOME_SCREEN state by optionally starting a battle.

    Args:
        restart_enabled (bool, optional):
            When True (default), taps the 'Battle' button to auto-start gameplay.
            When False, performs no input and leaves hold-state reporting to the
            owning app policy.

    Returns:
        bool — whether this invocation dispatched a battle control.

    Side effects:
        [tap] Taps the Battle button when restart_enabled=True.
        [log] Emits launch-attempt INFO logs when restart_enabled=True.
        (Also sleeps ≈2s after tapping to allow UI to transition.)

    Defaults:
        restart_enabled=True; adds a ~2s pause after tapping when enabled.

    Errors:
        None material; tap failures (if any) are not explicitly handled here.
    """
    if require_new_battle and require_resume_battle:
        raise ValueError(
            "Home battle control cannot require both New Battle and Resume Battle"
        )
    if restart_enabled:
        if operation_id is not None:
            log_action_intent(
                action_purpose or "Dispatching the Home battle control",
                reason=(
                    action_reason
                    or "execute the operator-selected battle workflow"
                ),
                operation_id=operation_id,
            )
        log("[HOME] Auto-start enabled — tapping 'Battle' button", "INFO")
        if require_new_battle:
            launched = tap_verified_new_battle(
                action_guard_fn=action_guard_fn,
            )
        elif require_resume_battle:
            launched = _tap_verified_home_battle_control(
                HomeBattleControl.RESUME_BATTLE,
                action_guard_fn=action_guard_fn,
            )
        elif tap_if_visible(
            "buttons.battle:home",
            retries=1,
            failure_log_level="DEBUG",
            action_guard_fn=action_guard_fn,
        ):
            launched = True
        elif tap_if_visible(
            "buttons.resume_battle:home",
            retries=1,
            failure_log_level="DEBUG",
            action_guard_fn=action_guard_fn,
        ):
            launched = True
        else:
            launched = _tap_verified_home_battle_control(
                action_guard_fn=action_guard_fn,
            )
        if not launched:
            log(
                "[HOME] Battle/Resume controls not verified; leaving handler",
                "WARN",
            )
        if operation_id is not None:
            log_result(
                (
                    "Verified Home battle control dispatched"
                    if launched
                    else "Verified Home battle control was not dispatched"
                ),
                operation_id=operation_id,
            )
        time.sleep(2)
        return launched
    return False
