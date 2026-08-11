"""Bind Current run activity to source-tagged Battle History identity."""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Any, Callable, Mapping, Optional

from core.battle_history import (
    BattleHistoryReadResult,
    BattleHistoryReadStatus,
    read_latest_completed_battle,
)
from core.battle_lifecycle import HomeBattleControl
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_history import (
    BATTLE_HISTORY_UI_MAPPING_ID,
    BATTLE_HISTORY_UI_SOURCE,
    CrossSourceHistoryStatus,
    PlayerSaveAttachmentContext,
    PlayerSaveHistoryReadResult,
    PlayerSaveHistoryReadStatus,
    corroborate_ui_and_save_history,
    history_sources_compatible,
    valid_history_tail_advance,
)
from core.player_save_temporal import (
    RunningAttachmentSaveObservations,
    RunningAttachmentTemporalBinding,
)
from core.terminal_save_report import (
    terminal_history_handoff_matches_source_scope,
    validate_terminal_history_handoff,
)
from utils.logger import (
    capture_activity_boundary,
    get_activity_scope,
    log,
    log_action_intent,
    log_result,
    record_activity_scope_battle_history,
    record_activity_scope_terminal_history_handoff,
    start_activity_scope,
    take_activity_scope_terminal_history_handoff,
)


POST_RETRY_HISTORY_POLL_INTERVAL_SECONDS = 15.0
SOURCE_RETRY_INTERVAL_SECONDS = 5.0
UI_HISTORY_SOURCE = BATTLE_HISTORY_UI_SOURCE
UI_HISTORY_MAPPING_ID = BATTLE_HISTORY_UI_MAPPING_ID
PLAYER_SAVE_HISTORY_SOURCE = "player_save"


@dataclass(frozen=True)
class ActivityContinuityOutcome:
    pending: bool = False
    recapture: bool = False
    confirmed_same_battle_scope_id: Optional[str] = None
    confirmed_later_battle_scope_id: Optional[str] = None
    running_attachment_observations: Optional[
        RunningAttachmentSaveObservations
    ] = None
    running_attachment_temporal_binding: Optional[
        RunningAttachmentTemporalBinding
    ] = None
    running_attachment_acquisition: Optional[
        PlayerSaveAcquisitionBundle
    ] = None
    running_attachment_context: Optional[PlayerSaveAttachmentContext] = None
    operator_workflow_interruption_reason: Optional[str] = None
    operator_workflow_source_restored: Optional[bool] = None
    ui_monitoring_fallback: bool = False
    ui_fallback_complete: bool = False
    ui_fallback_reason: str = ""


@dataclass(frozen=True)
class HomeHistoryBaselineOutcome:
    accepted: bool = False
    ui_required: bool = False
    blocked: bool = False
    reason: str = ""


@dataclass(frozen=True)
class TerminalHistoryHandoffOutcome:
    accepted: bool = False
    reason: str = ""
    scope_id: Optional[str] = None


class ActivityContinuityCoordinator:
    """Run one exclusive continuity check for each activity-scope boundary."""

    def __init__(
        self,
        *,
        history_reader: Callable[..., BattleHistoryReadResult] = (
            read_latest_completed_battle
        ),
        save_history_reader: Optional[
            Callable[..., PlayerSaveHistoryReadResult]
        ] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._history_reader = history_reader
        self._save_history_reader = save_history_reader
        self._clock = clock
        self._checked_scope_id: Optional[str] = None
        self._pending_scope_id: Optional[str] = None
        self._pending_source: Optional[str] = None
        self._pending_home_control = HomeBattleControl.UNKNOWN
        self._pending_mode: Optional[str] = None
        self._boundary: Optional[Mapping[str, object]] = None
        self._action_logged = False
        self._retry_at = 0.0

    def publish_terminal_history_handoff(
        self,
        history_transition: Mapping[str, Any],
    ) -> bool:
        """Persist a structurally proven terminal tail on its exact source scope."""

        if not isinstance(history_transition, Mapping):
            return False
        handoff = history_transition.get("handoff")
        binding = history_transition.get("run_binding")
        if (
            history_transition.get("status") != "complete"
            or history_transition.get("complete") is not True
            or not isinstance(handoff, Mapping)
            or not isinstance(binding, Mapping)
        ):
            return False
        run_id = str(binding.get("activity_scope_run_id") or "").strip()
        scope = get_activity_scope()
        if (
            not run_id
            or scope is None
            or str(scope.get("run_id") or "") != run_id
            or not terminal_history_handoff_matches_source_scope(
                handoff, run_id
            )
        ):
            return False
        try:
            updated = record_activity_scope_terminal_history_handoff(
                run_id=run_id,
                handoff=handoff,
            )
        except ValueError:
            updated = None
        if updated is None:
            log(
                "[BATTLE_CONTINUITY] Structurally proven terminal History "
                "handoff could not be persisted; the next boundary will use "
                "its established acquisition or UI fallback",
                "WARN",
            )
            return False
        log(
            "[BATTLE_CONTINUITY] Structurally proven terminal History tail "
            f"staged for one-use handoff scope_id={run_id}",
            "INFO",
        )
        return True

    def accept_pending_terminal_history_handoff(
        self,
        *,
        expected_scope_id: str,
        runtime_session_id: str,
        target_snapshot: Any,
    ) -> TerminalHistoryHandoffOutcome:
        """Consume a redacted terminal tail without another save or UI read."""

        scope = get_activity_scope()
        run_id = str(expected_scope_id or "").strip()
        if (
            scope is None
            or not run_id
            or str(scope.get("run_id") or "") != run_id
            or not isinstance(
                scope.get("pending_terminal_history_handoff"), Mapping
            )
        ):
            return TerminalHistoryHandoffOutcome(
                reason="terminal_history_handoff_unavailable",
                scope_id=run_id or None,
            )
        pending = take_activity_scope_terminal_history_handoff(run_id=run_id)
        if not isinstance(pending, Mapping):
            return self._rejected_terminal_handoff(
                run_id, "terminal_history_handoff_consume_failed"
            )
        if (
            pending.get("schema_version") != 1
            or str(pending.get("destination_run_id") or "") != run_id
        ):
            return self._rejected_terminal_handoff(
                run_id, "terminal_history_handoff_scope_changed"
            )
        target_binding = PlayerSaveTargetBinding.from_snapshot(target_snapshot)
        metadata, reason = validate_terminal_history_handoff(
            pending.get("handoff"),
            runtime_session_id=runtime_session_id,
            target_binding=target_binding,
            destination_reason=str(scope.get("reason") or ""),
        )
        normalized = _normalize_history_metadata(metadata)
        if normalized is None:
            return self._rejected_terminal_handoff(
                run_id,
                reason
                if metadata is None
                else "terminal_history_handoff_identity_invalid",
            )
        updated = record_activity_scope_battle_history(
            run_id=run_id,
            latest_completed_battle=normalized,
        )
        if updated is None:
            return self._rejected_terminal_handoff(
                run_id, "terminal_history_handoff_write_failed"
            )
        self._checked_scope_id = run_id
        self._reset_pending()
        log_result(
            "Battle History baseline accepted from the terminal boundary handoff",
            detail=(
                "[BATTLE_CONTINUITY] disposition=terminal_handoff_accepted "
                f"terminal={pending.get('handoff', {}).get('terminal_state')} "
                f"latest={_metadata_detail(normalized)} scope_id={run_id} "
                "save_reads=0 history_navigation=0"
            ),
        )
        return TerminalHistoryHandoffOutcome(
            accepted=True,
            reason=reason,
            scope_id=run_id,
        )

    @staticmethod
    def _rejected_terminal_handoff(
        run_id: str,
        reason: str,
    ) -> TerminalHistoryHandoffOutcome:
        log(
            "[BATTLE_CONTINUITY] Terminal History handoff rejected; the "
            f"established boundary fallback remains authoritative reason={reason} "
            f"scope_id={run_id}",
            "INFO",
        )
        return TerminalHistoryHandoffOutcome(
            accepted=False,
            reason=reason,
            scope_id=run_id,
        )

    def accept_home_save_baseline(
        self,
        history_tail: Mapping[str, Any],
        *,
        expected_scope_id: Optional[str],
        player_save_mode: str,
    ) -> HomeHistoryBaselineOutcome:
        """Record the authoritative Home snapshot without opening History."""

        if str(player_save_mode) != "save_first":
            return HomeHistoryBaselineOutcome(
                ui_required=True,
                reason="player_save_mode_requires_ui",
            )
        if history_tail.get("disposition") != "save_match":
            safe_fallback = history_tail.get("safe_ui_fallback") is True
            return HomeHistoryBaselineOutcome(
                ui_required=safe_fallback,
                blocked=not safe_fallback,
                reason=str(
                    history_tail.get("reason")
                    or "save_history_baseline_unavailable"
                ),
            )
        metadata = _normalize_history_metadata(history_tail.get("metadata"))
        if (
            metadata is None
            or metadata.get("source") != PLAYER_SAVE_HISTORY_SOURCE
            or metadata.get("mapping_id")
            != str(history_tail.get("mapping_id") or "")
        ):
            return HomeHistoryBaselineOutcome(
                ui_required=True,
                reason="save_history_metadata_invalid",
            )
        scope = get_activity_scope()
        run_id = str(scope.get("run_id") or "") if scope else ""
        if not run_id or run_id != str(expected_scope_id or ""):
            return HomeHistoryBaselineOutcome(
                blocked=True,
                reason="home_history_activity_scope_binding_lost",
            )
        updated = record_activity_scope_battle_history(
            run_id=run_id,
            latest_completed_battle=metadata,
        )
        if updated is None:
            return HomeHistoryBaselineOutcome(
                blocked=True,
                reason="save_history_baseline_write_failed",
            )
        self._checked_scope_id = run_id
        self._reset_pending()
        log_result(
            "Battle History baseline recorded from the authoritative Home save",
            detail=(
                "[BATTLE_CONTINUITY] disposition=save_baseline_recorded "
                f"source={metadata['source']} "
                f"mapping={metadata['mapping_id']} "
                f"latest={_metadata_detail(metadata)} scope_id={run_id}"
            ),
        )
        return HomeHistoryBaselineOutcome(
            accepted=True,
            reason="save_history_baseline_recorded",
        )

    def needs_check(
        self,
        detection: Mapping[str, Any],
        *,
        post_retry_poll_allowed: bool = True,
        defer_home_baseline: bool = False,
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
        control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        )
        if (
            defer_home_baseline
            and state in {"HOME", "HOME_SCREEN"}
            and control is HomeBattleControl.NEW_BATTLE
            and not post_retry_pending
            and _scope_battle_history(scope) is None
        ):
            return False
        if state in {"RUNNING", "BATTLE_HISTORY"}:
            return True
        if state not in {"HOME", "HOME_SCREEN"}:
            return False
        return control in {
            HomeBattleControl.NEW_BATTLE,
            HomeBattleControl.RESUME_BATTLE,
        }

    def request_running_reconciliation(self, activity_scope_id: str) -> bool:
        """Rearm one same-scope check for an explicit Return Control request."""

        run_id = str(activity_scope_id or "").strip()
        scope = get_activity_scope()
        if (
            not run_id
            or scope is None
            or str(scope.get("run_id") or "") != run_id
        ):
            return False
        self._checked_scope_id = None
        self._reset_pending()
        return True

    def handle(
        self,
        detection: Mapping[str, Any],
        *,
        actions_allowed: bool,
        action_guard_fn: Callable[[], bool],
        post_retry_poll_allowed: bool = True,
        defer_home_baseline: bool = False,
        player_save_mode: str = "force_ui",
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

        state = str(detection.get("state") or "UNKNOWN").upper()
        control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        )
        if self._pending_source is None:
            if (
                defer_home_baseline
                and state in {"HOME", "HOME_SCREEN"}
                and control is HomeBattleControl.NEW_BATTLE
                and not post_retry_pending
                and _scope_battle_history(scope) is None
            ):
                return ActivityContinuityOutcome()
            if state == "RUNNING":
                self._pending_source = "RUNNING"
                if post_retry_pending:
                    self._pending_mode = "post_retry_baseline"
                elif _scope_battle_history(scope) is None:
                    self._pending_mode = "attachment_baseline"
                else:
                    self._pending_mode = "compare"
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
        elif self._pending_source == "HOME_SCREEN" and state == "RUNNING":
            self._pending_source = "RUNNING"
            self._pending_home_control = HomeBattleControl.UNKNOWN
            if self._pending_mode == "baseline":
                self._pending_mode = "attachment_baseline"
            self._retry_at = 0.0
            log(
                "[BATTLE_CONTINUITY] Pending Home continuity source advanced "
                "to RUNNING; continuing the same activity scope from the "
                "observed battle start",
                "INFO",
            )

        if not actions_allowed:
            return ActivityContinuityOutcome(pending=True)
        if self._clock() < self._retry_at:
            return ActivityContinuityOutcome(
                pending=self._pending_mode != "post_retry_baseline"
            )

        use_save = self._should_read_save(scope, player_save_mode)
        attachment_observations = None
        attachment_temporal_binding = None
        attachment_acquisition = None
        attachment_bundle_context = None
        ui_fallback_reason = ""
        if not self._action_logged:
            self._boundary = capture_activity_boundary()
            self._log_action(run_id, use_save=use_save)
            self._action_logged = True

        if use_save:
            active_attachment = bool(
                self._pending_source == "RUNNING"
                and self._pending_mode in {"compare", "attachment_baseline"}
            )
            force_ui_fallback = False
            fallback_reason = ""
            save_result = self._save_history_reader(
                source_state=self._pending_source,
                expected_home_control=self._pending_home_control,
                expected_scope_id=run_id,
                action_guard_fn=action_guard_fn,
                serialize_active_attachment=(
                    self._pending_mode
                    in {"compare", "attachment_baseline"}
                    and self._pending_source == "RUNNING"
                ),
            )
            attachment_observations = (
                save_result.running_attachment_observations
            )
            attachment_temporal_binding = (
                save_result.running_attachment_temporal_binding
            )
            if active_attachment:
                attachment_acquisition = save_result.acquisition
                attachment_bundle_context = save_result.running_attachment_context
                if (
                    attachment_bundle_context is None
                    and attachment_observations is not None
                ):
                    attachment_bundle_context = (
                        _attachment_context_from_observations(
                            attachment_observations
                        )
                    )
            if save_result.status is PlayerSaveHistoryReadStatus.BLOCKED:
                log_result(
                    "Save-backed Battle History continuity paused without UI input",
                    detail=(
                        "[BATTLE_CONTINUITY] disposition=source_binding_blocked "
                        f"reason={save_result.reason} scope_id={run_id}"
                    ),
                )
                self._action_logged = False
                self._boundary = None
                self._retry_at = self._clock() + SOURCE_RETRY_INTERVAL_SECONDS
                return ActivityContinuityOutcome(
                    pending=True,
                    recapture=True,
                    operator_workflow_interruption_reason=(
                        save_result.reason
                        if save_result.operator_workflow_interrupted
                        else None
                    ),
                    operator_workflow_source_restored=(
                        save_result.source_restored
                        if save_result.operator_workflow_interrupted
                        else None
                    ),
                )
            if save_result.complete:
                metadata = _normalize_history_metadata(save_result.metadata)
                if metadata is not None:
                    if self._pending_mode == "post_retry_baseline":
                        previous = _pending_previous_battle(scope)
                        compatible = history_sources_compatible(
                            previous,
                            metadata,
                        )
                        if previous is not None and not compatible:
                            force_ui_fallback = True
                            fallback_reason = "history_source_mapping_changed"
                        elif (
                            previous is not None
                            and previous["fingerprint"]
                            == metadata["fingerprint"]
                        ):
                            return self._handle_unchanged_post_retry(metadata)
                        if (
                            previous is not None
                            and compatible
                            and not valid_history_tail_advance(
                                previous,
                                metadata,
                            )
                        ):
                            log(
                                "[BATTLE_CONTINUITY] Save History tail changed "
                                "without a valid append/rollover transition; "
                                "restoring the guarded UI fallback",
                                "INFO",
                            )
                            force_ui_fallback = True
                            fallback_reason = "history_tail_transition_invalid"
                        elif not force_ui_fallback:
                            return self._handle_metadata(scope, metadata)
                    elif self._pending_mode == "attachment_baseline":
                        return self._handle_metadata(
                            scope,
                            metadata,
                            attachment_observations=attachment_observations,
                            attachment_temporal_binding=attachment_temporal_binding,
                            attachment_acquisition=attachment_acquisition,
                            attachment_bundle_context=attachment_bundle_context,
                        )
                    else:
                        baseline = _scope_battle_history(scope)
                        compatible = history_sources_compatible(
                            baseline,
                            metadata,
                        )
                        if baseline is None:
                            return self._handle_metadata(
                                scope,
                                metadata,
                                attachment_observations=attachment_observations,
                                attachment_temporal_binding=attachment_temporal_binding,
                                attachment_acquisition=attachment_acquisition,
                                attachment_bundle_context=attachment_bundle_context,
                            )
                        elif compatible and (
                            baseline["fingerprint"]
                            == metadata["fingerprint"]
                        ):
                            return self._handle_metadata(
                                scope,
                                metadata,
                                attachment_observations=attachment_observations,
                                attachment_temporal_binding=attachment_temporal_binding,
                                attachment_acquisition=attachment_acquisition,
                                attachment_bundle_context=attachment_bundle_context,
                            )
                        elif compatible and not valid_history_tail_advance(
                            baseline,
                            metadata,
                        ):
                            log(
                                "[BATTLE_CONTINUITY] Attached save History "
                                "tail changed without a valid append/rollover "
                                "transition; starting a conservative attachment "
                                "scope from the fresh save tail",
                                "INFO",
                            )
                            return self._handle_metadata(
                                scope,
                                metadata,
                                attachment_observations=attachment_observations,
                                attachment_temporal_binding=attachment_temporal_binding,
                                attachment_acquisition=attachment_acquisition,
                                attachment_bundle_context=attachment_bundle_context,
                            )
                        elif compatible:
                            return self._handle_metadata(
                                scope,
                                metadata,
                                attachment_observations=attachment_observations,
                                attachment_temporal_binding=attachment_temporal_binding,
                                attachment_acquisition=attachment_acquisition,
                                attachment_bundle_context=attachment_bundle_context,
                            )
                        else:
                            corroboration = corroborate_ui_and_save_history(
                                baseline,
                                metadata,
                            )
                            if corroboration.matched:
                                return self._handle_cross_source_migration(
                                    scope,
                                    metadata,
                                    reason=corroboration.reason,
                                    attachment_observations=(
                                        attachment_observations
                                    ),
                                    attachment_temporal_binding=(
                                        attachment_temporal_binding
                                    ),
                                    attachment_acquisition=attachment_acquisition,
                                    attachment_bundle_context=attachment_bundle_context,
                                )
                            return self._handle_metadata(
                                scope,
                                metadata,
                                attachment_observations=attachment_observations,
                                attachment_temporal_binding=attachment_temporal_binding,
                                attachment_acquisition=attachment_acquisition,
                                attachment_bundle_context=attachment_bundle_context,
                                attachment_change_confirmed=(
                                    corroboration.status
                                    is CrossSourceHistoryStatus.MISMATCH
                                ),
                                attachment_incompatibility_reason=(
                                    corroboration.reason
                                ),
                            )
                else:
                    force_ui_fallback = True
                    fallback_reason = "save_history_metadata_invalid"
            if not force_ui_fallback and not save_result.safe_ui_fallback:
                self._action_logged = False
                self._boundary = None
                self._retry_at = self._clock() + SOURCE_RETRY_INTERVAL_SECONDS
                return ActivityContinuityOutcome(
                    pending=True,
                    recapture=True,
                )
            log(
                "[BATTLE_CONTINUITY] Stable save evidence could not establish "
                "continuity "
                f"({fallback_reason or save_result.reason}); using the guarded "
                "UI route",
                "INFO",
            )
            ui_fallback_reason = fallback_reason or save_result.reason

        result = self._history_reader(
            source_state=self._pending_source,
            expected_home_control=self._pending_home_control,
            action_guard_fn=action_guard_fn,
        )
        if result.status is BattleHistoryReadStatus.PAUSED:
            return ActivityContinuityOutcome(
                pending=True,
                recapture=True,
            )
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
            return ActivityContinuityOutcome(
                recapture=True,
            )
        if not result.complete or result.identity is None:
            return self._handle_failed_read(
                scope,
                result,
                ui_fallback_reason=ui_fallback_reason,
            )
        metadata = _normalize_history_metadata(result.identity.scope_metadata())
        if metadata is None:
            return self._handle_failed_read(
                scope,
                BattleHistoryReadResult(
                    BattleHistoryReadStatus.FAILED,
                    "UI History metadata normalization failed",
                    source_restored=True,
                ),
                ui_fallback_reason=ui_fallback_reason,
            )
        if self._pending_mode == "post_retry_baseline":
            previous = _pending_previous_battle(scope)
            if (
                previous is not None
                and history_sources_compatible(previous, metadata)
                and previous["fingerprint"] == metadata["fingerprint"]
            ):
                return self._handle_unchanged_post_retry(metadata)
        return self._handle_metadata(
            scope,
            metadata,
            attachment_observations=attachment_observations,
            attachment_temporal_binding=attachment_temporal_binding,
            attachment_acquisition=attachment_acquisition,
            attachment_bundle_context=attachment_bundle_context,
            ui_fallback_reason=ui_fallback_reason,
        )

    def _handle_cross_source_migration(
        self,
        scope: Mapping[str, object],
        metadata: Mapping[str, Any],
        *,
        reason: str,
        attachment_observations: Optional[
            RunningAttachmentSaveObservations
        ] = None,
        attachment_temporal_binding: Optional[
            RunningAttachmentTemporalBinding
        ] = None,
        attachment_acquisition: Optional[PlayerSaveAcquisitionBundle] = None,
        attachment_bundle_context: Optional[PlayerSaveAttachmentContext] = None,
    ) -> ActivityContinuityOutcome:
        """Persist a UI-to-save bridge without comparing source fingerprints."""

        run_id = str(scope["run_id"])
        updated = record_activity_scope_battle_history(
            run_id=run_id,
            latest_completed_battle=metadata,
        )
        if updated is None:
            log_result(
                "Attached battle continuity migration failed — the normalized "
                "save identity could not be persisted",
                detail=(
                    "[BATTLE_CONTINUITY] "
                    "disposition=cross_source_identity_write_failed "
                    f"reason={reason} scope_id={run_id}"
                ),
            )
            self._action_logged = False
            self._boundary = None
            self._retry_at = self._clock() + SOURCE_RETRY_INTERVAL_SECONDS
            return ActivityContinuityOutcome(
                pending=True,
                recapture=True,
            )

        log_result(
            "Attached battle continuity confirmed — UI identity corroborated "
            "the fresh save tail",
            detail=(
                "[BATTLE_CONTINUITY] disposition=cross_source_scope_preserved "
                f"corroboration={reason} latest={_metadata_detail(metadata)} "
                f"scope_id={run_id} fingerprint_compared=False"
            ),
        )
        self._checked_scope_id = run_id
        self._reset_pending()
        bound_attachment = _bind_attachment_observations(
            attachment_observations,
            run_id,
        )
        bound_temporal = _bind_attachment_temporal_binding(
            attachment_temporal_binding,
            run_id,
        )
        bound_bundle_context = _bind_attachment_context(
            attachment_bundle_context,
            run_id,
        )
        return ActivityContinuityOutcome(
            recapture=True,
            confirmed_same_battle_scope_id=run_id,
            running_attachment_observations=bound_attachment,
            running_attachment_temporal_binding=bound_temporal,
            running_attachment_acquisition=(
                _matching_attachment_acquisition(
                    attachment_acquisition,
                    bound_bundle_context,
                )
            ),
            running_attachment_context=bound_bundle_context,
        )

    def _should_read_save(
        self,
        scope: Mapping[str, object],
        player_save_mode: str,
    ) -> bool:
        if (
            str(player_save_mode) != "save_first"
            or self._save_history_reader is None
            or self._pending_source == "BATTLE_HISTORY"
        ):
            return False
        if self._pending_mode == "post_retry_baseline":
            reference = _pending_previous_battle(scope)
            return bool(
                reference is not None
                and reference.get("source") == PLAYER_SAVE_HISTORY_SOURCE
            )
        if (
            self._pending_source == "RUNNING"
            and self._pending_mode in {"compare", "attachment_baseline"}
        ):
            # The guarded active-attachment serializer establishes a missing
            # baseline directly. Existing UI baselines still use the explicit
            # Tier/Wave/Battle Date bridge; source fingerprints never cross.
            return True
        return False

    def _log_action(self, run_id: str, *, use_save: bool) -> None:
        channel = "stable player save" if use_save else "Battle History UI"
        if self._pending_mode == "baseline":
            summary = "Recording the Battle History baseline"
            reason = "identify the latest completed battle before this run starts"
        elif self._pending_mode == "post_retry_baseline":
            summary = "Polling the post-Retry Battle History baseline"
            reason = (
                "wait until the finished battle becomes the newest History entry"
            )
        elif self._pending_mode == "attachment_baseline":
            summary = "Recording the attached battle's last-completed baseline"
            reason = (
                "bind the current run to a guarded active-battle save without "
                "opening Battle History"
            )
        else:
            summary = "Checking attached battle continuity"
            reason = "determine whether a battle completed while automation was stopped"
        log_action_intent(
            summary,
            reason=reason,
            detail=(
                f"[BATTLE_CONTINUITY] mode={self._pending_mode} "
                f"channel={channel} scope_id={run_id}"
            ),
        )

    def _handle_unchanged_post_retry(
        self,
        metadata: Mapping[str, Any],
    ) -> ActivityContinuityOutcome:
        log_result(
            "Post-Retry Battle History tail is unchanged — passive polling "
            "will continue",
            detail=(
                "[BATTLE_CONTINUITY] disposition=tail_unchanged "
                f"latest={_metadata_detail(metadata)}"
            ),
        )
        self._defer_post_retry_poll()
        return ActivityContinuityOutcome(recapture=True)

    def _handle_metadata(
        self,
        scope: Mapping[str, object],
        metadata: Mapping[str, Any],
        *,
        attachment_observations: Optional[
            RunningAttachmentSaveObservations
        ] = None,
        attachment_temporal_binding: Optional[
            RunningAttachmentTemporalBinding
        ] = None,
        attachment_acquisition: Optional[PlayerSaveAcquisitionBundle] = None,
        attachment_bundle_context: Optional[PlayerSaveAttachmentContext] = None,
        attachment_change_confirmed: Optional[bool] = None,
        attachment_incompatibility_reason: str = "",
        ui_fallback_reason: str = "",
    ) -> ActivityContinuityOutcome:
        run_id = str(scope["run_id"])
        baseline = _scope_battle_history(scope)
        pending_previous = _pending_previous_battle(scope)
        compatible = history_sources_compatible(baseline, metadata)
        source_migration = bool(
            self._pending_mode == "post_retry_baseline"
            and pending_previous is not None
            and not history_sources_compatible(pending_previous, metadata)
        )
        source_incompatible_attachment = bool(
            self._pending_mode == "compare"
            and baseline is not None
            and not compatible
        )
        changed = bool(
            self._pending_mode == "compare"
            and baseline is not None
            and (
                source_incompatible_attachment
                or baseline["fingerprint"] != metadata["fingerprint"]
            )
        )
        change_confirmed = bool(
            changed
            and (
                attachment_change_confirmed
                if attachment_change_confirmed is not None
                else not source_incompatible_attachment
            )
        )

        if changed:
            reason = (
                "battle_history_changed_on_attachment"
                if change_confirmed
                else "battle_history_unavailable_on_attachment"
            )
            active_scope = start_activity_scope(
                reason=reason,
                boundary=self._boundary,
            )
            if active_scope is None:
                log_result(
                    "Attached battle continuity check failed — the new activity "
                    "scope could not be persisted",
                    detail=(
                        "[BATTLE_CONTINUITY] disposition=scope_write_failed "
                        f"latest={_metadata_detail(metadata)}"
                    ),
                )
                self._checked_scope_id = run_id
                self._reset_pending()
                return ActivityContinuityOutcome(
                    recapture=True,
                )
            run_id = str(active_scope["run_id"])

        updated = record_activity_scope_battle_history(
            run_id=run_id,
            latest_completed_battle=metadata,
        )
        if updated is None:
            log_result(
                "Battle continuity check failed — the normalized History "
                "identity could not be saved",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=identity_write_failed "
                    f"scope_id={run_id} latest={_metadata_detail(metadata)}"
                ),
            )
        elif changed:
            if change_confirmed:
                summary = (
                    "Attached battle identified as a later run — Current run "
                    "activity restarted conservatively"
                )
                disposition = "new_attachment_scope"
            else:
                summary = (
                    "Attached battle could not be compared with the prior "
                    "History source — Current run activity restarted "
                    "conservatively from the fresh save"
                )
                disposition = "unverified_new_attachment_scope"
            log_result(
                summary,
                detail=(
                    f"[BATTLE_CONTINUITY] disposition={disposition} "
                    f"source_compatible={compatible} "
                    "incompatibility_reason="
                    f"{attachment_incompatibility_reason or 'none'} "
                    f"previous={_baseline_detail(baseline)} "
                    f"latest={_metadata_detail(metadata)} scope_id={run_id}"
                ),
            )
        elif self._pending_mode == "post_retry_baseline":
            disposition = (
                "post_retry_source_migrated_without_fingerprint_comparison"
                if source_migration
                else "post_retry_baseline_recorded"
            )
            log_result(
                "Post-Retry Battle History baseline recorded — latest completed "
                f"battle is Tier {metadata.get('tier')}, wave {metadata.get('wave')}",
                detail=(
                    f"[BATTLE_CONTINUITY] disposition={disposition} "
                    f"previous={_baseline_detail(pending_previous)} "
                    f"latest={_metadata_detail(metadata)} scope_id={run_id}"
                ),
            )
        elif self._pending_mode == "attachment_baseline":
            log_result(
                "Attached battle baseline recorded from the guarded player save "
                "— latest completed battle is "
                f"Tier {metadata.get('tier')}, wave {metadata.get('wave')}",
                detail=(
                    "[BATTLE_CONTINUITY] "
                    "disposition=attachment_save_baseline_recorded "
                    f"latest={_metadata_detail(metadata)} scope_id={run_id}"
                ),
            )
        elif baseline is None:
            log_result(
                "Battle History baseline recorded — latest completed battle is "
                f"Tier {metadata.get('tier')}, wave {metadata.get('wave')}",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=baseline_recorded "
                    f"latest={_metadata_detail(metadata)} scope_id={run_id}"
                ),
            )
        else:
            log_result(
                "Attached battle continuity confirmed — latest completed battle "
                "is unchanged",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=scope_preserved "
                    f"latest={_metadata_detail(metadata)} scope_id={run_id}"
                ),
            )

        confirmed_same_battle_scope_id = None
        confirmed_later_battle_scope_id = None
        if (
            updated is not None
            and self._pending_mode == "compare"
            and baseline is not None
            and compatible
            and not changed
        ):
            confirmed_same_battle_scope_id = run_id
        elif changed and change_confirmed:
            confirmed_later_battle_scope_id = run_id

        self._checked_scope_id = run_id
        self._reset_pending()
        bound_attachment = (
            _bind_attachment_observations(
                attachment_observations,
                run_id,
            )
            if updated is not None
            else None
        )
        bound_temporal = (
            _bind_attachment_temporal_binding(
                attachment_temporal_binding,
                run_id,
            )
            if updated is not None
            else None
        )
        bound_bundle_context = (
            _bind_attachment_context(
                attachment_bundle_context,
                run_id,
            )
            if updated is not None
            else None
        )
        return ActivityContinuityOutcome(
            recapture=True,
            confirmed_same_battle_scope_id=confirmed_same_battle_scope_id,
            confirmed_later_battle_scope_id=confirmed_later_battle_scope_id,
            running_attachment_observations=bound_attachment,
            running_attachment_temporal_binding=bound_temporal,
            running_attachment_acquisition=(
                _matching_attachment_acquisition(
                    attachment_acquisition,
                    bound_bundle_context,
                )
            ),
            running_attachment_context=bound_bundle_context,
            ui_monitoring_fallback=(
                str(metadata.get("source") or "") == BATTLE_HISTORY_UI_SOURCE
            ),
            ui_fallback_complete=(
                str(metadata.get("source") or "") == BATTLE_HISTORY_UI_SOURCE
            ),
            ui_fallback_reason=(
                str(ui_fallback_reason or "battle_history_ui_selected")
                if str(metadata.get("source") or "")
                == BATTLE_HISTORY_UI_SOURCE
                else ""
            ),
        )

    def _handle_failed_read(
        self,
        scope: Mapping[str, object],
        result: BattleHistoryReadResult,
        *,
        ui_fallback_reason: str = "",
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
            self._retry_at = self._clock() + SOURCE_RETRY_INTERVAL_SECONDS
            return ActivityContinuityOutcome(
                pending=True,
                recapture=True,
            )

        if self._pending_mode == "post_retry_baseline":
            log_result(
                "Post-Retry Battle History baseline was not read — passive "
                "polling will continue",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=read_retry_scheduled "
                    f"reason={result.reason} scope_id={run_id}"
                ),
            )
            self._defer_post_retry_poll()
            return ActivityContinuityOutcome(
                recapture=True,
                ui_monitoring_fallback=True,
                ui_fallback_complete=False,
                ui_fallback_reason=str(
                    ui_fallback_reason or result.reason or "history_ui_read_failed"
                ),
            )

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
        return ActivityContinuityOutcome(
            recapture=True,
            ui_monitoring_fallback=True,
            ui_fallback_complete=False,
            ui_fallback_reason=str(
                ui_fallback_reason or result.reason or "history_ui_read_failed"
            ),
        )

    def _defer_post_retry_poll(self) -> None:
        """Release action authority until the next bounded History poll."""

        self._pending_source = None
        self._pending_home_control = HomeBattleControl.UNKNOWN
        self._pending_mode = None
        self._boundary = None
        self._action_logged = False
        self._retry_at = self._clock() + POST_RETRY_HISTORY_POLL_INTERVAL_SECONDS

    def _reset_pending(self) -> None:
        self._pending_scope_id = None
        self._pending_source = None
        self._pending_home_control = HomeBattleControl.UNKNOWN
        self._pending_mode = None
        self._boundary = None
        self._action_logged = False
        self._retry_at = 0.0


def _normalize_history_metadata(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint:
        return None
    if raw.get("schema_version") == 2:
        source = str(raw.get("source") or "").strip()
        mapping_id = str(raw.get("mapping_id") or "").strip()
        identity_schema = raw.get("identity_schema_version")
        if not source or not mapping_id or identity_schema != 1:
            return None
        result = {
            "schema_version": 2,
            "source": source,
            "mapping_id": mapping_id,
            "identity_schema_version": 1,
            "fingerprint": fingerprint,
            "tier": raw.get("tier"),
            "wave": raw.get("wave"),
        }
        effective_mapping_fingerprint = str(
            raw.get("effective_mapping_fingerprint") or ""
        ).strip()
        if source == "player_save" and len(effective_mapping_fingerprint) != 64:
            return None
        if effective_mapping_fingerprint:
            result["effective_mapping_fingerprint"] = (
                effective_mapping_fingerprint
            )
        for key in (
            "battle_date",
            "entry_count",
            "capacity",
            "semantic_status",
            "semantic_reason",
            "captured_at",
            "acquisition",
        ):
            if key in raw:
                result[key] = raw[key]
        return result

    if raw.get("schema_version") != 1:
        return None

    # Activity scope v1 identities were written only by the copied UI report
    # reader.  Tag that known source contract during read; never reinterpret an
    # arbitrary schema-v2 or player-save fingerprint as UI evidence.
    return {
        "schema_version": 2,
        "source": UI_HISTORY_SOURCE,
        "mapping_id": UI_HISTORY_MAPPING_ID,
        "identity_schema_version": 1,
        "fingerprint": fingerprint,
        "battle_date": str(raw.get("battle_date") or "").strip(),
        "tier": str(raw.get("tier") or "").strip(),
        "wave": str(raw.get("wave") or "").strip(),
        "legacy_v1_migrated": True,
    }


def _scope_battle_history(
    scope: Mapping[str, object],
) -> Optional[dict[str, Any]]:
    return _normalize_history_metadata(scope.get("latest_completed_battle"))


def _pending_latest_completed_battle(
    scope: Mapping[str, object],
) -> Optional[Mapping[str, object]]:
    raw = scope.get("pending_latest_completed_battle")
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        return None
    return raw


def _pending_previous_battle(
    scope: Mapping[str, object],
) -> Optional[dict[str, Any]]:
    pending = _pending_latest_completed_battle(scope)
    if pending is None:
        return None
    return _normalize_history_metadata(pending.get("previous_completed_battle"))


def _bind_attachment_observations(
    observations: Optional[RunningAttachmentSaveObservations],
    activity_scope_id: str,
) -> Optional[RunningAttachmentSaveObservations]:
    """Publish attachment facts only after continuity persisted final scope."""

    if observations is None:
        return None
    try:
        return observations.bind_final_scope(activity_scope_id)
    except (TypeError, ValueError):
        return None


def _bind_attachment_temporal_binding(
    binding: Optional[RunningAttachmentTemporalBinding],
    activity_scope_id: str,
) -> Optional[RunningAttachmentTemporalBinding]:
    """Publish round identity even when no allowlisted fact was projected."""

    if binding is None:
        return None
    try:
        return binding.bind_final_scope(activity_scope_id)
    except (TypeError, ValueError):
        return None


def _matching_attachment_acquisition(
    acquisition: Optional[PlayerSaveAcquisitionBundle],
    context: Optional[PlayerSaveAttachmentContext],
) -> Optional[PlayerSaveAcquisitionBundle]:
    """Carry the same forced bundle only with its final-scope projection."""

    if (
        not isinstance(acquisition, PlayerSaveAcquisitionBundle)
        or context is None
        or not acquisition.complete
        or acquisition.acquisition_type
        is not PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        or acquisition.binding is None
        or acquisition.binding.target != context.target
        or acquisition.binding.generation != context.target_generation
    ):
        return None
    return acquisition


def _bind_attachment_context(
    context: Optional[PlayerSaveAttachmentContext],
    activity_scope_id: str,
) -> Optional[PlayerSaveAttachmentContext]:
    if context is None or not context.valid_for(context.activity_scope_id):
        return None
    try:
        return replace(context, activity_scope_id=str(activity_scope_id))
    except (TypeError, ValueError):
        return None


def _attachment_context_from_observations(
    observations: RunningAttachmentSaveObservations,
) -> Optional[PlayerSaveAttachmentContext]:
    binding = observations.binding
    try:
        return PlayerSaveAttachmentContext(
            runtime_session_id=binding.runtime_session_id,
            activity_scope_id=binding.source_activity_scope_id,
            target=binding.target_binding.target,
            target_generation=binding.target_binding.generation,
            active_battle_observed=True,
        )
    except (TypeError, ValueError):
        return None


def _baseline_detail(identity: Optional[Mapping[str, Any]]) -> str:
    if identity is None:
        return "none"
    return _metadata_detail(identity)


def _metadata_detail(identity: Mapping[str, Any]) -> str:
    return (
        f"source={identity.get('source') or 'unknown'} "
        f"mapping={identity.get('mapping_id') or 'unknown'} "
        f"tier={identity.get('tier') or 'unknown'} "
        f"wave={identity.get('wave') or 'unknown'} "
        f"fingerprint={identity.get('fingerprint') or 'unknown'}"
    )

__all__ = [
    "ActivityContinuityCoordinator",
    "ActivityContinuityOutcome",
    "HomeHistoryBaselineOutcome",
    "TerminalHistoryHandoffOutcome",
]
