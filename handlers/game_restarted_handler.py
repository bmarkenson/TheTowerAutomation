"""Guarded inputs for The Tower's post-process-restart Welcome Back modal."""

from __future__ import annotations

from enum import Enum
import time
from typing import Callable, Optional

import numpy as np

from core.input import ActionGuard, safe_tap
from core.input import TapDispatchOutcome, TapDispatchStatus
from core.emulator_recovery import (
    RecoveryUiDispatchOutcome,
    RecoveryUiDispatchStatus,
)
from core.state_detector import detect_state_and_overlays
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from utils.logger import log


Frame = np.ndarray
CaptureFn = Callable[[], Optional[Frame]]


class GameRestartedAction(str, Enum):
    NONE = "none"
    RESUME = "resume"
    END_RUN = "end_run"


def handle_game_restarted(
    screenshot: Optional[Frame],
    *,
    action: GameRestartedAction,
    action_guard_fn: ActionGuard = None,
    capture_fn: CaptureFn = capture_adb_screenshot,
    max_attempts: int = 2,
    verification_polls: int = 4,
    poll_interval_s: float = 0.25,
) -> RecoveryUiDispatchOutcome:
    """Run one bounded Welcome Back input and postcondition transaction."""

    def frame_state(frame: Optional[Frame]) -> Optional[str]:
        if frame is None or not is_complete_screenshot(frame):
            return None
        return str(
            detect_state_and_overlays(frame).get("state") or "UNKNOWN"
        ).upper()

    initial_state = frame_state(screenshot)
    if initial_state is None:
        return RecoveryUiDispatchOutcome(
            RecoveryUiDispatchStatus.DEFERRED,
            final_state="UNKNOWN",
            reason="the scheduling frame was incomplete",
        )
    if initial_state != "GAME_RESTARTED":
        log(
            "[EMULATOR_RECOVERY] Refusing Welcome Back input from "
            f"state={initial_state!r}",
            "DEBUG",
        )
        return RecoveryUiDispatchOutcome(
            RecoveryUiDispatchStatus.ALREADY_RESOLVED,
            final_state=initial_state,
            reason="the Welcome Back modal was already absent",
        )
    if action is GameRestartedAction.NONE:
        return RecoveryUiDispatchOutcome(
            RecoveryUiDispatchStatus.DEFERRED,
            final_state=initial_state,
            reason="no Welcome Back action was selected",
        )
    key = (
        "buttons.resume_game:game_restarted"
        if action is GameRestartedAction.RESUME
        else "buttons.end_run:game_restarted"
    )
    dispatched = False
    attempts = 0
    final_state = initial_state
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        # The scheduling frame cannot authorize input.  Recapture immediately
        # before every attempt so a delayed transition never becomes a blind
        # tap at the old button location.
        current = capture_fn()
        pre_input_state = frame_state(current)
        if pre_input_state is None:
            return RecoveryUiDispatchOutcome(
                RecoveryUiDispatchStatus.UNCERTAIN
                if dispatched
                else RecoveryUiDispatchStatus.DEFERRED,
                input_dispatched=dispatched,
                attempts=attempts,
                final_state=final_state,
                reason=(
                    "no complete post-input source could be recovered"
                    if dispatched
                    else "a fresh complete pre-input frame was unavailable"
                ),
            )
        final_state = pre_input_state
        if pre_input_state != "GAME_RESTARTED":
            return RecoveryUiDispatchOutcome(
                RecoveryUiDispatchStatus.RESOLVED
                if dispatched
                else RecoveryUiDispatchStatus.ALREADY_RESOLVED,
                input_dispatched=dispatched,
                attempts=attempts,
                final_state=final_state,
                reason="the Welcome Back modal is absent on fresh evidence",
            )
        if action_guard_fn is None or not action_guard_fn():
            return RecoveryUiDispatchOutcome(
                RecoveryUiDispatchStatus.INTERRUPTED,
                input_dispatched=dispatched,
                attempts=attempts,
                final_state=final_state,
                reason="exact maintenance input authority was lost",
            )
        attempts = attempt
        raw_outcome = safe_tap(
            key,
            screenshot=current,
            dispatch="now",
            retries=0,
            failure_log_level="DEBUG",
            action_guard_fn=action_guard_fn,
            return_dispatch_outcome=True,
        )
        outcome = (
            raw_outcome
            if isinstance(raw_outcome, TapDispatchOutcome)
            else TapDispatchOutcome(
                TapDispatchStatus.DISPATCHED
                if raw_outcome
                else TapDispatchStatus.NOT_DISPATCHED
            )
        )
        if outcome.uncertain:
            return RecoveryUiDispatchOutcome(
                RecoveryUiDispatchStatus.UNCERTAIN,
                input_dispatched=True,
                attempts=attempts,
                final_state=final_state,
                reason="the Welcome Back input dispatch result was uncertain",
            )
        if not outcome.dispatched:
            if action_guard_fn is None or not action_guard_fn():
                return RecoveryUiDispatchOutcome(
                    RecoveryUiDispatchStatus.INTERRUPTED,
                    input_dispatched=dispatched,
                    attempts=attempts,
                    final_state=final_state,
                    reason="maintenance authority changed at input dispatch",
                )
            continue
        dispatched = True
        observed_complete = False
        for _ in range(max(1, int(verification_polls))):
            time.sleep(max(0.0, float(poll_interval_s)))
            candidate = capture_fn()
            candidate_state = frame_state(candidate)
            if candidate_state is None:
                continue
            observed_complete = True
            final_state = candidate_state
            if candidate_state != "GAME_RESTARTED":
                return RecoveryUiDispatchOutcome(
                    RecoveryUiDispatchStatus.RESOLVED,
                    input_dispatched=True,
                    attempts=attempts,
                    final_state=final_state,
                    reason="the modal cleared on fresh post-input evidence",
                )
        if not observed_complete:
            return RecoveryUiDispatchOutcome(
                RecoveryUiDispatchStatus.UNCERTAIN,
                input_dispatched=True,
                attempts=attempts,
                final_state=final_state,
                reason=(
                    "the input was accepted, but no complete post-input frame "
                    "proved its result"
                ),
            )

    return RecoveryUiDispatchOutcome(
        RecoveryUiDispatchStatus.FAILED,
        input_dispatched=dispatched,
        attempts=attempts,
        final_state=final_state,
        reason="the Welcome Back modal persisted after the bounded transaction",
    )


__all__ = ["GameRestartedAction", "handle_game_restarted"]
