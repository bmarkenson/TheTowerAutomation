"""Bind Current run activity to copied in-game Battle History identity."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping, Optional

from core.battle_history import (
    BattleHistoryIdentity,
    BattleHistoryReadResult,
    BattleHistoryReadStatus,
    read_latest_completed_battle,
)
from core.battle_lifecycle import HomeBattleControl
from utils.logger import (
    capture_activity_boundary,
    get_activity_scope,
    log,
    log_action_intent,
    log_result,
    record_activity_scope_battle_history,
    start_activity_scope,
)


POST_RETRY_HISTORY_POLL_INTERVAL_SECONDS = 15.0


@dataclass(frozen=True)
class ActivityContinuityOutcome:
    pending: bool = False
    recapture: bool = False
    confirmed_same_battle_scope_id: Optional[str] = None


class ActivityContinuityCoordinator:
    """Run one exclusive History check for each activity-scope boundary."""

    def __init__(
        self,
        *,
        history_reader: Callable[..., BattleHistoryReadResult] = (
            read_latest_completed_battle
        ),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._history_reader = history_reader
        self._clock = clock
        self._checked_scope_id: Optional[str] = None
        self._pending_scope_id: Optional[str] = None
        self._pending_source: Optional[str] = None
        self._pending_home_control = HomeBattleControl.UNKNOWN
        self._pending_mode: Optional[str] = None
        self._boundary: Optional[Mapping[str, object]] = None
        self._action_logged = False
        self._retry_at = 0.0

    def needs_check(
        self,
        detection: Mapping[str, Any],
        *,
        post_retry_poll_allowed: bool = True,
    ) -> bool:
        """Return whether this frame can advance an unchecked run scope."""

        scope = get_activity_scope()
        if scope is None:
            return False
        run_id = str(scope.get("run_id") or "").strip()
        if not run_id or run_id == self._checked_scope_id:
            return False
        post_retry_pending = _pending_latest_completed_battle(scope) is not None
        if self._pending_source is not None:
            if (
                self._pending_mode == "post_retry_baseline"
                and self._clock() < self._retry_at
            ):
                return False
            return True
        if post_retry_pending and (
            not post_retry_poll_allowed or self._clock() < self._retry_at
        ):
            return False
        state = str(detection.get("state") or "UNKNOWN").upper()
        if state in {"RUNNING", "BATTLE_HISTORY"}:
            return True
        if state not in {"HOME", "HOME_SCREEN"}:
            return False
        return HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        ) in {
            HomeBattleControl.NEW_BATTLE,
            HomeBattleControl.RESUME_BATTLE,
        }

    def handle(
        self,
        detection: Mapping[str, Any],
        *,
        actions_allowed: bool,
        action_guard_fn: Callable[[], bool],
        post_retry_poll_allowed: bool = True,
    ) -> ActivityContinuityOutcome:
        scope = get_activity_scope()
        if scope is None:
            return ActivityContinuityOutcome()
        run_id = str(scope.get("run_id") or "").strip()
        if not run_id or run_id == self._checked_scope_id:
            return ActivityContinuityOutcome()

        if self._pending_scope_id != run_id:
            self._reset_pending()
            self._pending_scope_id = run_id

        post_retry_pending = _pending_latest_completed_battle(scope) is not None
        if (
            self._pending_source is None
            and post_retry_pending
            and (
                not post_retry_poll_allowed
                or self._clock() < self._retry_at
            )
        ):
            return ActivityContinuityOutcome()

        if self._pending_source is None:
            state = str(detection.get("state") or "UNKNOWN").upper()
            control = HomeBattleControl.parse(
                detection.get("home_battle_control", "UNKNOWN")
            )
            if state == "RUNNING":
                self._pending_source = "RUNNING"
                self._pending_mode = (
                    "post_retry_baseline" if post_retry_pending else "compare"
                )
            elif state == "BATTLE_HISTORY":
                self._pending_source = "BATTLE_HISTORY"
                self._pending_mode = (
                    "post_retry_baseline" if post_retry_pending else "compare"
                )
            elif (
                state in {"HOME", "HOME_SCREEN"}
                and control is HomeBattleControl.RESUME_BATTLE
            ):
                self._pending_source = "HOME_SCREEN"
                self._pending_home_control = control
                self._pending_mode = (
                    "post_retry_baseline" if post_retry_pending else "compare"
                )
            elif (
                state in {"HOME", "HOME_SCREEN"}
                and control is HomeBattleControl.NEW_BATTLE
            ):
                if post_retry_pending:
                    self._pending_source = "HOME_SCREEN"
                    self._pending_home_control = control
                    self._pending_mode = "post_retry_baseline"
                else:
                    baseline = _scope_battle_history(scope)
                    if baseline is not None:
                        self._checked_scope_id = run_id
                        self._reset_pending()
                        return ActivityContinuityOutcome()
                    self._pending_source = "HOME_SCREEN"
                    self._pending_home_control = control
                    self._pending_mode = "baseline"
            else:
                return ActivityContinuityOutcome()

        if not actions_allowed:
            return ActivityContinuityOutcome(pending=True)
        if self._clock() < self._retry_at:
            return ActivityContinuityOutcome(
                pending=self._pending_mode != "post_retry_baseline"
            )

        if not self._action_logged:
            self._boundary = capture_activity_boundary()
            if self._pending_mode == "baseline":
                log_action_intent(
                    "Recording the Battle History baseline",
                    reason=(
                        "identify the latest completed battle before this run "
                        "starts"
                    ),
                    detail=(
                        "[BATTLE_CONTINUITY] mode=baseline "
                        f"scope_id={run_id}"
                    ),
                )
            elif self._pending_mode == "post_retry_baseline":
                log_action_intent(
                    "Polling the post-Retry Battle History baseline",
                    reason=(
                        "wait until the finished battle becomes the newest "
                        "History entry"
                    ),
                    detail=(
                        "[BATTLE_CONTINUITY] mode=post_retry_baseline "
                        f"scope_id={run_id}"
                    ),
                )
            else:
                log_action_intent(
                    "Checking attached battle continuity",
                    reason=(
                        "determine whether a battle completed while automation "
                        "was stopped"
                    ),
                    detail=(
                        "[BATTLE_CONTINUITY] mode=compare "
                        f"scope_id={run_id}"
                    ),
                )
            self._action_logged = True

        result = self._history_reader(
            source_state=self._pending_source,
            expected_home_control=self._pending_home_control,
            action_guard_fn=action_guard_fn,
        )
        if result.status is BattleHistoryReadStatus.PAUSED:
            return ActivityContinuityOutcome(pending=True, recapture=True)
        if result.status is BattleHistoryReadStatus.BATTLE_ENDED:
            log_result(
                "Battle continuity check interrupted — the battle ended during "
                "inspection",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=battle_ended "
                    f"reason={result.reason}"
                ),
            )
            self._checked_scope_id = run_id
            self._reset_pending()
            return ActivityContinuityOutcome(recapture=True)
        if not result.complete or result.identity is None:
            return self._handle_failed_read(
                scope,
                result,
            )
        return self._handle_identity(
            scope,
            result.identity,
        )

    def _handle_identity(
        self,
        scope: Mapping[str, object],
        identity: BattleHistoryIdentity,
    ) -> ActivityContinuityOutcome:
        run_id = str(scope["run_id"])
        baseline = _scope_battle_history(scope)
        pending_previous = _pending_previous_battle(scope)
        if (
            self._pending_mode == "post_retry_baseline"
            and pending_previous is not None
            and pending_previous["fingerprint"] == identity.fingerprint
        ):
            log(
                "[BATTLE_CONTINUITY] Post-Retry History still shows the "
                "previous completed battle; polling again after the game "
                "publishes the new latest entry",
                "DEBUG",
            )
            self._defer_post_retry_poll()
            return ActivityContinuityOutcome(recapture=True)
        changed = bool(
            self._pending_mode == "compare"
            and baseline is not None
            and baseline["fingerprint"] != identity.fingerprint
        )
        active_scope: Optional[dict[str, object]]
        if changed:
            active_scope = start_activity_scope(
                reason="battle_history_changed_on_attachment",
                boundary=self._boundary,
            )
            if active_scope is None:
                log_result(
                    "Attached battle continuity check failed — the new activity "
                    "scope could not be persisted",
                    detail=(
                        "[BATTLE_CONTINUITY] disposition=scope_write_failed "
                        f"latest={_identity_detail(identity)}"
                    ),
                )
                self._checked_scope_id = run_id
                self._reset_pending()
                return ActivityContinuityOutcome(recapture=True)
            run_id = str(active_scope["run_id"])
        else:
            active_scope = dict(scope)

        updated = record_activity_scope_battle_history(
            run_id=run_id,
            latest_completed_battle=identity.scope_metadata(),
        )
        if updated is None:
            log_result(
                "Battle continuity check failed — the copied History identity "
                "could not be saved",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=identity_write_failed "
                    f"scope_id={run_id} latest={_identity_detail(identity)}"
                ),
            )
        elif changed:
            log_result(
                "Attached battle identified as a later run — Current run "
                "activity restarted",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=new_attachment_scope "
                    f"previous={_baseline_detail(baseline)} "
                    f"latest={_identity_detail(identity)} scope_id={run_id}"
                ),
            )
        elif self._pending_mode == "post_retry_baseline":
            log_result(
                "Post-Retry Battle History baseline recorded — latest "
                f"completed battle is Tier {identity.tier}, wave {identity.wave}",
                detail=(
                    "[BATTLE_CONTINUITY] "
                    "disposition=post_retry_baseline_recorded "
                    f"previous={_baseline_detail(pending_previous)} "
                    f"latest={_identity_detail(identity)} scope_id={run_id}"
                ),
            )
        elif baseline is None:
            log_result(
                "Battle History baseline recorded — latest completed battle "
                f"is Tier {identity.tier}, wave {identity.wave}",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=baseline_recorded "
                    f"latest={_identity_detail(identity)} scope_id={run_id}"
                ),
            )
        else:
            log_result(
                "Attached battle continuity confirmed — latest completed battle "
                "is unchanged",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=scope_preserved "
                    f"latest={_identity_detail(identity)} scope_id={run_id}"
                ),
            )

        confirmed_same_battle_scope_id = None
        if (
            updated is not None
            and self._pending_mode == "compare"
            and baseline is not None
            and not changed
        ):
            confirmed_same_battle_scope_id = run_id

        self._checked_scope_id = run_id
        self._reset_pending()
        return ActivityContinuityOutcome(
            recapture=True,
            confirmed_same_battle_scope_id=(
                confirmed_same_battle_scope_id
            ),
        )

    def _handle_failed_read(
        self,
        scope: Mapping[str, object],
        result: BattleHistoryReadResult,
    ) -> ActivityContinuityOutcome:
        run_id = str(scope["run_id"])
        if not result.source_restored:
            log_result(
                "Battle continuity check failed — the source screen was not "
                "restored",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=retry_required "
                    f"reason={result.reason}"
                ),
            )
            self._action_logged = False
            self._boundary = None
            self._retry_at = self._clock() + 5.0
            return ActivityContinuityOutcome(pending=True, recapture=True)

        if self._pending_mode == "post_retry_baseline":
            log(
                "[BATTLE_CONTINUITY] Post-Retry History baseline was not "
                f"read ({result.reason}); polling again",
                "DEBUG",
            )
            self._defer_post_retry_poll()
            return ActivityContinuityOutcome(recapture=True)

        if self._pending_mode == "compare":
            replacement = start_activity_scope(
                reason="battle_history_unavailable_on_attachment",
                boundary=self._boundary,
            )
            if replacement is not None:
                run_id = str(replacement["run_id"])
            summary = (
                "Attached battle continuity could not be verified — Current run "
                "activity restarted conservatively"
            )
            disposition = "unverified_new_attachment_scope"
        else:
            summary = (
                "Battle History baseline could not be recorded — continuing "
                "with the new run scope"
            )
            disposition = "baseline_unavailable"
        log_result(
            summary,
            detail=(
                f"[BATTLE_CONTINUITY] disposition={disposition} "
                f"reason={result.reason} scope_id={run_id}"
            ),
        )
        self._checked_scope_id = run_id
        self._reset_pending()
        return ActivityContinuityOutcome(recapture=True)

    def _defer_post_retry_poll(self) -> None:
        """Release action authority until the next bounded History poll."""

        self._pending_source = None
        self._pending_home_control = HomeBattleControl.UNKNOWN
        self._pending_mode = None
        self._boundary = None
        self._retry_at = (
            self._clock() + POST_RETRY_HISTORY_POLL_INTERVAL_SECONDS
        )

    def _reset_pending(self) -> None:
        self._pending_scope_id = None
        self._pending_source = None
        self._pending_home_control = HomeBattleControl.UNKNOWN
        self._pending_mode = None
        self._boundary = None
        self._action_logged = False
        self._retry_at = 0.0


def _scope_battle_history(
    scope: Mapping[str, object],
) -> Optional[dict[str, str]]:
    raw = scope.get("latest_completed_battle")
    if not isinstance(raw, Mapping):
        return None
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint:
        return None
    return {
        "fingerprint": fingerprint,
        "battle_date": str(raw.get("battle_date") or "").strip(),
        "tier": str(raw.get("tier") or "").strip(),
        "wave": str(raw.get("wave") or "").strip(),
    }


def _pending_latest_completed_battle(
    scope: Mapping[str, object],
) -> Optional[Mapping[str, object]]:
    raw = scope.get("pending_latest_completed_battle")
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        return None
    return raw


def _pending_previous_battle(
    scope: Mapping[str, object],
) -> Optional[dict[str, str]]:
    pending = _pending_latest_completed_battle(scope)
    if pending is None:
        return None
    raw = pending.get("previous_completed_battle")
    if not isinstance(raw, Mapping):
        return None
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint:
        return None
    return {
        "fingerprint": fingerprint,
        "battle_date": str(raw.get("battle_date") or "").strip(),
        "tier": str(raw.get("tier") or "").strip(),
        "wave": str(raw.get("wave") or "").strip(),
    }


def _baseline_detail(identity: Optional[Mapping[str, str]]) -> str:
    if identity is None:
        return "none"
    return (
        f"date={identity.get('battle_date') or 'unknown'} "
        f"tier={identity.get('tier') or 'unknown'} "
        f"wave={identity.get('wave') or 'unknown'} "
        f"fingerprint={identity.get('fingerprint') or 'unknown'}"
    )


def _identity_detail(identity: BattleHistoryIdentity) -> str:
    return (
        f"date={identity.battle_date} tier={identity.tier} "
        f"wave={identity.wave} fingerprint={identity.fingerprint}"
    )


__all__ = [
    "ActivityContinuityCoordinator",
    "ActivityContinuityOutcome",
]
