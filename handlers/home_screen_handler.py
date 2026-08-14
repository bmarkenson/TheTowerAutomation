# handlers/home_screen_handler.py

from dataclasses import dataclass
import time
from utils.logger import log, log_action_intent, log_result
from core.input import (
    ActionGuard,
    TapDispatchOutcome,
    TapDispatchStatus,
    TapVerification,
    safe_tap,
    tap_if_visible,
)
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import (
    HOME_BATTLE_CONTROL_REGION,
    HOME_TIER_SELECTOR_REGION,
    HomeTierEvidence,
    detect_home_battle_control,
    detect_home_tier,
)
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays


_HOME_TIER_POSTCONDITION_ATTEMPTS = 6
_HOME_TIER_POSTCONDITION_DELAY_SECONDS = 0.25


@dataclass(frozen=True)
class HomeTierSelectionResult:
    """Outcome of reconciling the ordinary Home tier selector."""

    verified: bool
    status: TapDispatchStatus
    observed_tier: int | None
    taps: int
    reason: str

    @property
    def uncertain(self) -> bool:
        return self.status is TapDispatchStatus.UNCERTAIN


def _typed_outcome(
    status: TapDispatchStatus,
    *,
    return_dispatch_outcome: bool,
) -> bool | TapDispatchOutcome:
    outcome = TapDispatchOutcome(status)
    return outcome if return_dispatch_outcome else outcome.dispatched


def _normalize_tap_outcome(value: object) -> TapDispatchOutcome:
    if isinstance(value, TapDispatchOutcome):
        return value
    return TapDispatchOutcome(
        TapDispatchStatus.DISPATCHED
        if value
        else TapDispatchStatus.NOT_DISPATCHED
    )


def _validate_required_tier(required_tier: int) -> int:
    if type(required_tier) is not int or not 1 <= required_tier <= 100:
        raise ValueError("required Home tier must be an integer between 1 and 100")
    return required_tier


def _verified_new_battle_tier(screenshot) -> HomeTierEvidence:
    if screenshot is None:
        return HomeTierEvidence(None, "capture_failed")
    detection = detect_state_and_overlays(screenshot)
    if detection.get("state") != "HOME_SCREEN":
        return HomeTierEvidence(
            None,
            f"state:{detection.get('state') or 'UNKNOWN'}",
        )
    control = detect_home_battle_control(screenshot)
    if control.control is not HomeBattleControl.NEW_BATTLE:
        return HomeTierEvidence(
            None,
            f"home_control:{control.control.value}",
            control.confidence,
            control.raw_text,
        )
    return detect_home_tier(screenshot)


def select_verified_home_tier(
    required_tier: int,
    *,
    action_guard_fn: ActionGuard = None,
    capture_fn=None,
    sleep_fn=None,
) -> HomeTierSelectionResult:
    """Move Home to ``required_tier`` one observed selector step at a time."""

    required_tier = _validate_required_tier(required_tier)
    capture_fn = capture_fn or capture_adb_screenshot
    sleep_fn = sleep_fn or time.sleep
    screenshot = capture_fn()
    evidence = _verified_new_battle_tier(screenshot)
    if evidence.tier is None:
        return HomeTierSelectionResult(
            False,
            TapDispatchStatus.NOT_DISPATCHED,
            None,
            0,
            "the current Home New Battle tier was not verified "
            f"(source={evidence.source}, text={evidence.raw_text!r})",
        )
    if evidence.tier == required_tier:
        return HomeTierSelectionResult(
            True,
            TapDispatchStatus.NOT_DISPATCHED,
            evidence.tier,
            0,
            f"Home already shows Tier {required_tier}",
        )

    current_tier = evidence.tier
    direction = 1 if required_tier > current_tier else -1
    label = (
        "buttons.home_tier:increase"
        if direction > 0
        else "buttons.home_tier:decrease"
    )
    log(
        f"[HOME] Reconciling Home tier from {current_tier} to {required_tier}",
        "INFO",
    )
    taps = 0
    while current_tier != required_tier:
        expected_before = current_tier
        expected_after = current_tier + direction
        raw_outcome = safe_tap(
            label,
            dispatch="now",
            verification=TapVerification(
                screenshot=screenshot,
                target_region=HOME_TIER_SELECTOR_REGION,
                description=(
                    f"home_new_battle_tier:{expected_before}->{expected_after}"
                ),
                verifier=lambda frame, expected=expected_before: (
                    _verified_new_battle_tier(frame).tier == expected
                ),
            ),
            action_guard_fn=action_guard_fn,
            return_dispatch_outcome=True,
        )
        outcome = _normalize_tap_outcome(raw_outcome)
        if not outcome.attempted:
            return HomeTierSelectionResult(
                False,
                TapDispatchStatus.NOT_DISPATCHED,
                current_tier,
                taps,
                f"Tier {expected_before} selector input was not dispatched",
            )
        taps += 1

        last_evidence = HomeTierEvidence(None, "postcondition_unobserved")
        for _attempt in range(_HOME_TIER_POSTCONDITION_ATTEMPTS):
            sleep_fn(_HOME_TIER_POSTCONDITION_DELAY_SECONDS)
            candidate = capture_fn()
            last_evidence = _verified_new_battle_tier(candidate)
            if last_evidence.tier == expected_after:
                screenshot = candidate
                current_tier = expected_after
                if outcome.uncertain:
                    log(
                        "[HOME] Tier selector dispatch uncertainty was resolved "
                        f"by the exact Tier {expected_after} postcondition",
                        "DEBUG",
                    )
                break
            if last_evidence.tier == expected_before:
                continue
            if last_evidence.tier is not None:
                return HomeTierSelectionResult(
                    False,
                    TapDispatchStatus.UNCERTAIN,
                    last_evidence.tier,
                    taps,
                    "tier selector reached unexpected "
                    f"Tier {last_evidence.tier}; expected Tier {expected_after}",
                )
        else:
            if last_evidence.tier == expected_before:
                return HomeTierSelectionResult(
                    False,
                    TapDispatchStatus.NOT_DISPATCHED,
                    expected_before,
                    taps,
                    f"tier selector remained at Tier {expected_before}",
                )
            return HomeTierSelectionResult(
                False,
                TapDispatchStatus.UNCERTAIN,
                last_evidence.tier,
                taps,
                "tier selector postcondition could not be verified "
                f"(source={last_evidence.source}, text={last_evidence.raw_text!r})",
            )

    return HomeTierSelectionResult(
        True,
        TapDispatchStatus.DISPATCHED,
        current_tier,
        taps,
        f"verified Home Tier {required_tier} after {taps} selector taps",
    )


def _tap_verified_home_battle_control(
    required_control: HomeBattleControl | None = None,
    *,
    required_tier: int | None = None,
    action_guard_fn: ActionGuard = None,
    return_dispatch_outcome: bool = False,
) -> bool | TapDispatchOutcome:
    """OCR and tap Battle/Resume only on a verified home screen."""

    if required_tier is not None:
        required_tier = _validate_required_tier(required_tier)
        if required_control is not HomeBattleControl.NEW_BATTLE:
            raise ValueError("a required Home tier applies only to New Battle")

    screenshot = capture_adb_screenshot()
    if screenshot is None:
        return _typed_outcome(
            TapDispatchStatus.NOT_DISPATCHED,
            return_dispatch_outcome=return_dispatch_outcome,
        )
    detection = detect_state_and_overlays(screenshot)
    if detection["state"] != "HOME_SCREEN":
        log(
            f"[HOME] Refusing Battle fallback from state={detection['state']!r}",
            "WARN",
        )
        return _typed_outcome(
            TapDispatchStatus.NOT_DISPATCHED,
            return_dispatch_outcome=return_dispatch_outcome,
        )
    evidence = detect_home_battle_control(screenshot)
    if evidence.control is HomeBattleControl.UNKNOWN:
        log(
            f"[HOME] Battle fallback was not verified: source={evidence.source} "
            f"text={evidence.raw_text!r} confidence={evidence.confidence:.1f}",
            "WARN",
        )
        return _typed_outcome(
            TapDispatchStatus.NOT_DISPATCHED,
            return_dispatch_outcome=return_dispatch_outcome,
        )
    if required_control is not None and evidence.control is not required_control:
        log(
            f"[HOME] Refusing {evidence.control.value}; this action requires "
            f"{required_control.value}",
            "WARN",
        )
        return _typed_outcome(
            TapDispatchStatus.NOT_DISPATCHED,
            return_dispatch_outcome=return_dispatch_outcome,
        )
    if required_tier is not None:
        tier_evidence = detect_home_tier(screenshot)
        if tier_evidence.tier != required_tier:
            log(
                f"[HOME] Refusing New Battle at Tier {tier_evidence.tier}; "
                f"this action requires Tier {required_tier} "
                f"(source={tier_evidence.source}, text={tier_evidence.raw_text!r})",
                "WARN",
            )
            return _typed_outcome(
                TapDispatchStatus.NOT_DISPATCHED,
                return_dispatch_outcome=return_dispatch_outcome,
            )
    log(
        f"[HOME] Verified {evidence.control.value} via {evidence.source} "
        f"(confidence={evidence.confidence:.1f})",
        "DEBUG",
    )
    typed_kwargs = (
        {"return_dispatch_outcome": True}
        if return_dispatch_outcome
        else {}
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
                and (
                    required_tier is None
                    or detect_home_tier(frame).tier == required_tier
                )
            ),
        ),
        action_guard_fn=action_guard_fn,
        **typed_kwargs,
    )


def tap_verified_new_battle(
    *,
    required_tier: int | None = None,
    action_guard_fn: ActionGuard = None,
    return_dispatch_outcome: bool = False,
) -> bool | TapDispatchOutcome:
    """Tap only the ordinary Home NEW_BATTLE control on fresh evidence."""

    typed_kwargs = (
        {"return_dispatch_outcome": True}
        if return_dispatch_outcome
        else {}
    )
    return _tap_verified_home_battle_control(
        HomeBattleControl.NEW_BATTLE,
        required_tier=required_tier,
        action_guard_fn=action_guard_fn,
        **typed_kwargs,
    )


def handle_home_screen(
    restart_enabled: bool = True,
    *,
    require_new_battle: bool = False,
    require_resume_battle: bool = False,
    required_tier: int | None = None,
    operation_id: str | None = None,
    action_purpose: str | None = None,
    action_reason: str | None = None,
    action_guard_fn: ActionGuard = None,
    return_dispatch_outcome: bool = False,
) -> bool | TapDispatchOutcome:
    """
    Handle the HOME_SCREEN state by optionally starting a battle.

    Args:
        restart_enabled (bool, optional):
            When True (default), taps the 'Battle' button to auto-start gameplay.
            When False, performs no input and leaves hold-state reporting to the
            owning app policy.

    Returns:
        bool by default, or a typed dispatch outcome when requested.

    Side effects:
        [tap] Reconciles a required numeric tier, then taps Battle, when
              restart_enabled=True and an exact New Battle is requested.
        [tap] Taps the verified Resume control for an exact resume request.
        [log] Emits launch-attempt INFO logs when restart_enabled=True.
        (Also sleeps ≈2s after tapping to allow UI to transition.)

    Defaults:
        restart_enabled=True; adds a ~2s pause after tapping when enabled.

    Errors:
        Invalid or conflicting explicit requirements raise ValueError. Missing
        evidence and input failures return a non-dispatched typed outcome.
    """
    if require_new_battle and require_resume_battle:
        raise ValueError(
            "Home battle control cannot require both New Battle and Resume Battle"
        )
    if required_tier is not None:
        required_tier = _validate_required_tier(required_tier)
        if not require_new_battle:
            raise ValueError("a required Home tier requires explicit New Battle")
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
        typed_kwargs = (
            {"return_dispatch_outcome": True}
            if return_dispatch_outcome
            else {}
        )
        if require_new_battle:
            tier_selection = None
            if required_tier is not None:
                tier_selection = select_verified_home_tier(
                    required_tier,
                    action_guard_fn=action_guard_fn,
                )
            if tier_selection is not None and not tier_selection.verified:
                launched = TapDispatchOutcome(tier_selection.status)
                log(
                    "[HOME] Required tier was not verified; refusing Battle: "
                    f"{tier_selection.reason}",
                    "WARN",
                )
            else:
                launched = tap_verified_new_battle(
                    required_tier=required_tier,
                    action_guard_fn=action_guard_fn,
                    **typed_kwargs,
                )
        elif require_resume_battle:
            launched = _tap_verified_home_battle_control(
                HomeBattleControl.RESUME_BATTLE,
                action_guard_fn=action_guard_fn,
                **typed_kwargs,
            )
        else:
            launched = TapDispatchOutcome(
                TapDispatchStatus.NOT_DISPATCHED
            )
            for label in (
                "buttons.battle:home",
                "buttons.resume_battle:home",
            ):
                candidate = tap_if_visible(
                    label,
                    retries=0,
                    failure_log_level="DEBUG",
                    action_guard_fn=action_guard_fn,
                    return_dispatch_outcome=True,
                )
                outcome = (
                    candidate
                    if isinstance(candidate, TapDispatchOutcome)
                    else TapDispatchOutcome(
                        TapDispatchStatus.DISPATCHED
                        if candidate
                        else TapDispatchStatus.NOT_DISPATCHED
                    )
                )
                if outcome.dispatched or outcome.uncertain:
                    launched = outcome
                    break
            else:
                launched = _tap_verified_home_battle_control(
                    action_guard_fn=action_guard_fn,
                    return_dispatch_outcome=True,
                )
        outcome = (
            launched
            if isinstance(launched, TapDispatchOutcome)
            else TapDispatchOutcome(
                TapDispatchStatus.DISPATCHED
                if launched
                else TapDispatchStatus.NOT_DISPATCHED
            )
        )
        if not outcome.dispatched and not outcome.uncertain:
            log(
                "[HOME] Battle/Resume controls not verified; leaving handler",
                "WARN",
            )
        if operation_id is not None:
            if required_tier is not None and outcome.dispatched:
                result_summary = (
                    f"Verified Home Tier {required_tier} and New Battle "
                    "control dispatched"
                )
            elif required_tier is not None and outcome.uncertain:
                result_summary = (
                    "Home tier or New Battle dispatch result was uncertain"
                )
            elif required_tier is not None:
                result_summary = (
                    f"Required Home Tier {required_tier} and New Battle "
                    "were not dispatched"
                )
            else:
                result_summary = (
                    "Verified Home battle control dispatched"
                    if outcome.dispatched
                    else "Home battle control dispatch result was uncertain"
                    if outcome.uncertain
                    else "Verified Home battle control was not dispatched"
                )
            log_result(
                result_summary,
                operation_id=operation_id,
            )
        time.sleep(2)
        return outcome if return_dispatch_outcome else outcome.dispatched
    outcome = TapDispatchOutcome(TapDispatchStatus.NOT_DISPATCHED)
    return outcome if return_dispatch_outcome else False
