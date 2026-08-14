"""Bounded recovery for the known Free Ticket blocking modal."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Callable, Optional

import numpy as np

from core.input import (
    ActionGuard,
    TapDispatchOutcome,
    TapDispatchStatus,
    safe_tap,
)
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log_action_intent, log_result, new_operation_id


Frame = np.ndarray
CaptureFn = Callable[[], Optional[Frame]]


class FreeTicketRecoveryStatus(str, Enum):
    DISMISSED = "dismissed"
    ALREADY_RESOLVED = "already_resolved"
    DEFERRED = "deferred"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class FreeTicketRecoveryResult:
    status: FreeTicketRecoveryStatus
    input_dispatched: bool
    attempts: int
    final_state: str
    reason: str
    dispatch_uncertain: bool = False

    @property
    def recovered(self) -> bool:
        return self.status in {
            FreeTicketRecoveryStatus.DISMISSED,
            FreeTicketRecoveryStatus.ALREADY_RESOLVED,
        }


def _state(frame: Optional[Frame]) -> Optional[str]:
    if frame is None or not is_complete_screenshot(frame):
        return None
    return str(detect_state_and_overlays(frame).get("state") or "UNKNOWN")


def handle_free_ticket_modal(
    screenshot: Frame,
    *,
    action_guard_fn: ActionGuard,
    capture_fn: CaptureFn = capture_adb_screenshot,
    max_attempts: int = 2,
    verification_polls: int = 4,
    poll_interval_s: float = 0.25,
    operation_id: Optional[str] = None,
) -> FreeTicketRecoveryResult:
    """Claim a recognized Free Ticket modal and verify that it disappeared."""

    operation_id = operation_id or new_operation_id()
    current: Optional[Frame] = screenshot
    dispatched = False
    attempts = 0
    intent_logged = False
    final_state = _state(current) or "UNKNOWN"
    status = FreeTicketRecoveryStatus.FAILED
    reason = "complete modal evidence was unavailable"

    try:
        if final_state != "FREE_TICKET":
            status = FreeTicketRecoveryStatus.ALREADY_RESOLVED
            reason = "the modal was absent on the fresh recovery frame"
            return FreeTicketRecoveryResult(
                status, dispatched, attempts, final_state, reason
            )

        for attempt in range(1, max(1, int(max_attempts)) + 1):
            # The scheduling frame is never reusable input authority. Capture
            # again before every attempt, including the first, so a user or a
            # delayed launch transition cannot turn the old Claim location
            # into a blind tap.
            current = capture_fn()
            pre_input_state = _state(current)
            if pre_input_state is None:
                status = (
                    FreeTicketRecoveryStatus.FAILED
                    if dispatched
                    else FreeTicketRecoveryStatus.DEFERRED
                )
                reason = (
                    "a fresh retry frame was unavailable after the modal was "
                    "authoritatively observed to persist"
                    if dispatched
                    else "a fresh complete pre-input frame was unavailable"
                )
                break
            final_state = pre_input_state
            if pre_input_state != "FREE_TICKET":
                status = FreeTicketRecoveryStatus.ALREADY_RESOLVED
                reason = (
                    "the modal changed before recovery input; no dismissal or "
                    "retry authority was inferred"
                )
                return FreeTicketRecoveryResult(
                    status, dispatched, attempts, final_state, reason
                )
            if action_guard_fn is None or not action_guard_fn():
                status = FreeTicketRecoveryStatus.INTERRUPTED
                reason = "launch-transition authority was lost before input"
                break
            if not intent_logged:
                log_action_intent(
                    "Claiming the blocking Free Tournament Ticket",
                    reason=(
                        "a verified launch transition is waiting behind the "
                        "recognized Free Ticket modal"
                    ),
                    detail=(
                        "[FREE_TICKET_RECOVERY] "
                        "label=buttons.claim:free_ticket"
                    ),
                    operation_id=operation_id,
                )
                intent_logged = True
            attempts = attempt
            raw_tap = safe_tap(
                "buttons.claim:free_ticket",
                screenshot=current,
                retries=0,
                dispatch="now",
                action_guard_fn=action_guard_fn,
                return_dispatch_outcome=True,
            )
            tap_outcome = (
                raw_tap
                if isinstance(raw_tap, TapDispatchOutcome)
                else TapDispatchOutcome(
                    TapDispatchStatus.DISPATCHED
                    if raw_tap
                    else TapDispatchStatus.NOT_DISPATCHED
                )
            )
            if tap_outcome.uncertain:
                dispatched = True
                status = FreeTicketRecoveryStatus.UNCERTAIN
                reason = (
                    "the Claim input may have reached the device, but its "
                    "dispatch result was uncertain"
                )
                break
            if not tap_outcome.dispatched:
                if action_guard_fn is None or not action_guard_fn():
                    status = FreeTicketRecoveryStatus.INTERRUPTED
                    reason = "launch-transition authority was lost at input"
                else:
                    status = FreeTicketRecoveryStatus.FAILED
                    reason = (
                        "the modal remained but the verified Claim target could "
                        "not be dispatched"
                    )
                break
            dispatched = True

            complete_post_input_observed = False
            for _ in range(max(1, int(verification_polls))):
                time.sleep(max(0.0, float(poll_interval_s)))
                candidate = capture_fn()
                candidate_state = _state(candidate)
                if candidate_state is None:
                    continue
                complete_post_input_observed = True
                current = candidate
                final_state = candidate_state
                if candidate_state != "FREE_TICKET":
                    status = FreeTicketRecoveryStatus.DISMISSED
                    reason = "the modal disappeared on a fresh complete frame"
                    return FreeTicketRecoveryResult(
                        status, dispatched, attempts, final_state, reason
                    )

            if not complete_post_input_observed:
                status = FreeTicketRecoveryStatus.UNCERTAIN
                reason = (
                    "the Claim input was accepted, but no complete post-input "
                    "observation was available"
                )
                break

        else:
            status = FreeTicketRecoveryStatus.FAILED
            reason = "the modal persisted after the bounded recovery budget"

        return FreeTicketRecoveryResult(
            status,
            dispatched,
            attempts,
            final_state,
            reason,
            status is FreeTicketRecoveryStatus.UNCERTAIN,
        )
    finally:
        if intent_logged:
            result_word = status.value
            log_result(
                (
                    "Free Ticket recovery completed"
                    if status is FreeTicketRecoveryStatus.DISMISSED
                    else "Free Ticket recovery yielded safely"
                    if status is FreeTicketRecoveryStatus.INTERRUPTED
                    else "Free Ticket recovery found the modal already resolved"
                    if status is FreeTicketRecoveryStatus.ALREADY_RESOLVED
                    else "Free Ticket recovery deferred for fresh evidence"
                    if status is FreeTicketRecoveryStatus.DEFERRED
                    else "Free Ticket recovery outcome is uncertain"
                    if status is FreeTicketRecoveryStatus.UNCERTAIN
                    else "Free Ticket recovery exhausted its bounded attempts"
                ),
                detail=(
                    f"[FREE_TICKET_RECOVERY] result={result_word} "
                    f"input_dispatched={dispatched} attempts={attempts} "
                    f"dispatch_uncertain="
                    f"{status is FreeTicketRecoveryStatus.UNCERTAIN} "
                    f"final_state={final_state} reason={reason}"
                ),
                operation_id=operation_id,
            )
