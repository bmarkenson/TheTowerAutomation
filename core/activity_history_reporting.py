"""Best-effort Battle History reporting for activity-log segments.

Activity scopes are mutable log/report metadata.  This module may attach an
already-acquired History projection to that metadata, but it never determines
battle continuity, acquires a save, navigates the UI, or grants input authority.
Canonical ``ActiveRoundIdentity`` observations own battle continuity elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveTargetBinding,
)
from core.player_save_history import PlayerSaveAttachmentContext
from core.player_save_temporal import (
    RunningAttachmentSaveObservations,
    RunningAttachmentTemporalBinding,
)
from core.terminal_save_report import (
    terminal_history_handoff_matches_source_scope,
    validate_terminal_history_handoff,
)
from utils.logger import (
    get_activity_scope,
    log,
    log_result,
    record_activity_scope_battle_history,
    record_activity_scope_terminal_history_handoff,
    take_activity_scope_terminal_history_handoff,
)


PLAYER_SAVE_HISTORY_SOURCE = "player_save"


@dataclass(frozen=True)
class RunningAttachmentProjection:
    """Save-backed running attachment projection consumed by ``App``.

    The value is produced only from a forced save and carries no
    activity-scope authority.
    """

    pending: bool = False
    recapture: bool = False
    battle_relation: Optional[str] = None
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


@dataclass(frozen=True)
class HomeHistoryBaselineOutcome:
    """Result of best-effort report metadata persistence.

    ``ui_required`` and ``blocked`` are retained as compatibility fields and
    are always false.  Missing report metadata must never route UI input or
    block a battle workflow.
    """

    accepted: bool = False
    ui_required: bool = False
    blocked: bool = False
    reason: str = ""


@dataclass(frozen=True)
class TerminalHistoryHandoffOutcome:
    accepted: bool = False
    reason: str = ""
    scope_id: Optional[str] = None


class ActivityHistoryReporter:
    """Store already-projected History facts as non-authoritative metadata."""

    def publish_terminal_history_handoff(
        self,
        history_transition: Mapping[str, Any],
    ) -> bool:
        """Persist a proven terminal tail on its source report segment."""

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
                "[BATTLE_HISTORY_REPORT] Terminal History handoff could not "
                "be persisted; gameplay authority is unchanged",
                "WARN",
            )
            return False
        log(
            "[BATTLE_HISTORY_REPORT] Terminal History tail staged for "
            f"one-use report handoff scope_id={run_id}",
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
        """Consume a redacted report tail without another save or UI read."""

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
        log_result(
            "Battle History report baseline accepted from the terminal handoff",
            detail=(
                "[BATTLE_HISTORY_REPORT] disposition=terminal_handoff_accepted "
                f"terminal={pending.get('handoff', {}).get('terminal_state')} "
                f"latest={_metadata_detail(normalized)} scope_id={run_id} "
                "save_reads=0 history_navigation=0 authority=none"
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
            "[BATTLE_HISTORY_REPORT] Terminal History handoff rejected; "
            f"gameplay authority is unchanged reason={reason} scope_id={run_id}",
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
        """Attach an already-acquired Home History tail to report metadata."""

        if str(player_save_mode) != "save_first":
            return HomeHistoryBaselineOutcome(
                reason="player_save_mode_has_no_save_history_report",
            )
        if history_tail.get("disposition") != "save_match":
            return HomeHistoryBaselineOutcome(
                reason=str(
                    history_tail.get("reason")
                    or "save_history_report_unavailable"
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
                reason="save_history_report_metadata_invalid",
            )
        scope = get_activity_scope()
        run_id = str(scope.get("run_id") or "") if scope else ""
        if not run_id or run_id != str(expected_scope_id or ""):
            return HomeHistoryBaselineOutcome(
                reason="home_history_report_scope_unavailable",
            )
        updated = record_activity_scope_battle_history(
            run_id=run_id,
            latest_completed_battle=metadata,
        )
        if updated is None:
            return HomeHistoryBaselineOutcome(
                reason="save_history_baseline_report_write_failed",
            )
        log_result(
            "Battle History report baseline recorded from the forced Home save",
            detail=(
                "[BATTLE_HISTORY_REPORT] disposition=save_baseline_recorded "
                f"source={metadata['source']} mapping={metadata['mapping_id']} "
                f"latest={_metadata_detail(metadata)} scope_id={run_id} "
                "authority=none"
            ),
        )
        return HomeHistoryBaselineOutcome(
            accepted=True,
            reason="save_history_baseline_recorded",
        )


def _normalize_history_metadata(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint or raw.get("schema_version") != 2:
        return None
    source = str(raw.get("source") or "").strip()
    mapping_id = str(raw.get("mapping_id") or "").strip()
    identity_schema = raw.get("identity_schema_version")
    expected_identity_schema = 2 if source == PLAYER_SAVE_HISTORY_SOURCE else 1
    if not source or not mapping_id or identity_schema != expected_identity_schema:
        return None
    result = {
        "schema_version": 2,
        "source": source,
        "mapping_id": mapping_id,
        "identity_schema_version": identity_schema,
        "fingerprint": fingerprint,
        "tier": raw.get("tier"),
        "wave": raw.get("wave"),
    }
    effective_mapping_fingerprint = str(
        raw.get("effective_mapping_fingerprint") or ""
    ).strip()
    if (
        source == PLAYER_SAVE_HISTORY_SOURCE
        and len(effective_mapping_fingerprint) != 64
    ):
        return None
    if effective_mapping_fingerprint:
        result["effective_mapping_fingerprint"] = effective_mapping_fingerprint
    for key in (
        "is_tournament",
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


def _metadata_detail(identity: Mapping[str, Any]) -> str:
    return (
        f"source={identity.get('source') or 'unknown'} "
        f"mapping={identity.get('mapping_id') or 'unknown'} "
        f"tier={identity.get('tier') or 'unknown'} "
        f"wave={identity.get('wave') or 'unknown'} "
        f"fingerprint={identity.get('fingerprint') or 'unknown'}"
    )


__all__ = [
    "ActivityHistoryReporter",
    "RunningAttachmentProjection",
    "HomeHistoryBaselineOutcome",
    "TerminalHistoryHandoffOutcome",
]
