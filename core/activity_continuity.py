"""Bind Current run activity to source-tagged Battle History identity."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping, Optional

from core.battle_history import (
    BattleHistoryReadResult,
    BattleHistoryReadStatus,
    read_latest_completed_battle,
)
from core.battle_lifecycle import HomeBattleControl
from core.player_save_history import (
    BATTLE_HISTORY_UI_MAPPING_ID,
    BATTLE_HISTORY_UI_SOURCE,
    PlayerSaveHistoryReadResult,
    PlayerSaveHistoryReadStatus,
    corroborate_ui_and_save_history,
    history_sources_compatible,
    ui_history_bridge_eligible,
    valid_history_tail_advance,
)
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


@dataclass(frozen=True)
class HomeHistoryBaselineOutcome:
    accepted: bool = False
    ui_required: bool = False
    blocked: bool = False
    reason: str = ""


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

        if self._pending_source is None:
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
                return ActivityContinuityOutcome()
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

        use_save = self._should_read_save(scope, player_save_mode)
        if not self._action_logged:
            self._boundary = capture_activity_boundary()
            self._log_action(run_id, use_save=use_save)
            self._action_logged = True

        if use_save:
            force_ui_fallback = False
            fallback_reason = ""
            save_result = self._save_history_reader(
                source_state=self._pending_source,
                expected_home_control=self._pending_home_control,
                expected_scope_id=run_id,
                action_guard_fn=action_guard_fn,
                serialize_active_attachment=(
                    self._pending_mode == "compare"
                    and self._pending_source == "RUNNING"
                ),
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
                return ActivityContinuityOutcome(pending=True, recapture=True)
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
                    else:
                        baseline = _scope_battle_history(scope)
                        compatible = history_sources_compatible(
                            baseline,
                            metadata,
                        )
                        if baseline is None:
                            force_ui_fallback = True
                            fallback_reason = "history_baseline_unavailable"
                        elif compatible and (
                            baseline["fingerprint"]
                            == metadata["fingerprint"]
                        ):
                            return self._handle_metadata(scope, metadata)
                        elif compatible and not valid_history_tail_advance(
                            baseline,
                            metadata,
                        ):
                            log(
                                "[BATTLE_CONTINUITY] Attached save History "
                                "tail changed without a valid append/rollover "
                                "transition; restoring the guarded UI fallback",
                                "INFO",
                            )
                            force_ui_fallback = True
                            fallback_reason = (
                                "history_tail_transition_invalid"
                            )
                        elif compatible:
                            return self._handle_metadata(scope, metadata)
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
                                )
                            force_ui_fallback = True
                            fallback_reason = corroboration.reason
                else:
                    force_ui_fallback = True
                    fallback_reason = "save_history_metadata_invalid"
            if not force_ui_fallback and not save_result.safe_ui_fallback:
                self._action_logged = False
                self._boundary = None
                self._retry_at = self._clock() + SOURCE_RETRY_INTERVAL_SECONDS
                return ActivityContinuityOutcome(pending=True, recapture=True)
            log(
                "[BATTLE_CONTINUITY] Stable save evidence could not establish "
                "continuity "
                f"({fallback_reason or save_result.reason}); using the guarded "
                "UI route",
                "INFO",
            )

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
            return self._handle_failed_read(scope, result)
        metadata = _normalize_history_metadata(result.identity.scope_metadata())
        if metadata is None:
            return self._handle_failed_read(
                scope,
                BattleHistoryReadResult(
                    BattleHistoryReadStatus.FAILED,
                    "UI History metadata normalization failed",
                    source_restored=True,
                ),
            )
        if self._pending_mode == "post_retry_baseline":
            previous = _pending_previous_battle(scope)
            if (
                previous is not None
                and history_sources_compatible(previous, metadata)
                and previous["fingerprint"] == metadata["fingerprint"]
            ):
                return self._handle_unchanged_post_retry(metadata)
        return self._handle_metadata(scope, metadata)

    def _handle_cross_source_migration(
        self,
        scope: Mapping[str, object],
        metadata: Mapping[str, Any],
        *,
        reason: str,
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
            return ActivityContinuityOutcome(pending=True, recapture=True)

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
        return ActivityContinuityOutcome(
            recapture=True,
            confirmed_same_battle_scope_id=run_id,
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
        elif (
            self._pending_mode == "compare"
            and self._pending_source == "RUNNING"
        ):
            # A save baseline is directly comparable. A retained UI baseline
            # enters only when Tier/Wave/Battle Date can support the explicit
            # cross-source bridge; fingerprints remain incomparable.
            reference = _scope_battle_history(scope)
        else:
            return False
        return bool(
            reference is not None
            and (
                reference.get("source") == PLAYER_SAVE_HISTORY_SOURCE
                or ui_history_bridge_eligible(reference)
            )
        )

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

        if changed:
            reason = (
                "battle_history_unavailable_on_attachment"
                if source_incompatible_attachment
                else "battle_history_changed_on_attachment"
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
                return ActivityContinuityOutcome(recapture=True)
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
            log_result(
                "Attached battle identified as a later run — Current run "
                "activity restarted conservatively",
                detail=(
                    "[BATTLE_CONTINUITY] disposition=new_attachment_scope "
                    f"source_compatible={compatible} "
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
        elif changed:
            confirmed_later_battle_scope_id = run_id

        self._checked_scope_id = run_id
        self._reset_pending()
        return ActivityContinuityOutcome(
            recapture=True,
            confirmed_same_battle_scope_id=confirmed_same_battle_scope_id,
            confirmed_later_battle_scope_id=confirmed_later_battle_scope_id,
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
            self._retry_at = self._clock() + SOURCE_RETRY_INTERVAL_SECONDS
            return ActivityContinuityOutcome(pending=True, recapture=True)

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
]
