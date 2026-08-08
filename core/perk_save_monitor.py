"""Bound, monotonic Perk evidence from passive player-save checkpoints.

The monitor consumes only the privacy-safe ``NormalizedRuntimeSave`` projection.
It never reads a save itself, sends input, backgrounds the game, or grants action
authority.  A complete saved prefix remains exact; terminal UI evidence can add
only aggregate or uniquely-correlated tail facts without rewriting that prefix.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
import re
from typing import Any, Mapping, Optional, Sequence

from core.perk_configuration import classify_perk_configuration_text
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveTargetBinding,
)
from core.runtime_save import NormalizedRuntimeSave, RuntimePerkSnapshot


PERK_SAVE_MONITOR_SCHEMA_VERSION = 1
PERK_FINAL_INVENTORY_SCHEMA_VERSION = 1
PERK_TERMINAL_MERGE_SCHEMA_VERSION = 1
MAX_REJECTION_RECORDS = 16
MIN_EXHAUSTION_OCR_CONFIDENCE = 80.0
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,95}")


@dataclass(frozen=True, repr=False)
class PerkSaveMonitorContext:
    """Exact private ownership binding for one active battle."""

    runtime_session_id: str = field(repr=False)
    activity_scope_id: str = field(repr=False)
    target_binding: PlayerSaveTargetBinding = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_session_id",
            str(self.runtime_session_id or "").strip(),
        )
        object.__setattr__(
            self,
            "activity_scope_id",
            str(self.activity_scope_id or "").strip(),
        )
        if not isinstance(self.target_binding, PlayerSaveTargetBinding):
            raise TypeError("Perk monitor context requires a typed target binding")

    def valid(self) -> bool:
        return bool(
            self.runtime_session_id
            and self.activity_scope_id
            and isinstance(self.target_binding, PlayerSaveTargetBinding)
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "runtime_session_fingerprint": _fingerprint_text(
                self.runtime_session_id
            ),
            "activity_scope_fingerprint": _fingerprint_text(
                self.activity_scope_id
            ),
            "target_binding_fingerprint": self.target_binding.fingerprint,
        }

    def __repr__(self) -> str:
        return (
            "PerkSaveMonitorContext("
            f"binding='{self.target_binding.fingerprint[:16]}...')"
        )


class PerkSaveMonitor:
    """Pure same-round state machine for complete active Perk prefixes.

    The runtime coordinator serializes calls that cross its passive-worker and
    lifecycle threads.  This domain owner performs no acquisition and owns no
    device or synchronization lock.
    """

    def __init__(self) -> None:
        self._context: Optional[PerkSaveMonitorContext] = None
        self._identity: Optional[dict[str, Any]] = None
        self._checkpoint: Optional[dict[str, Any]] = None
        self._terminal_window: Optional[dict[str, Any]] = None
        self._exhaustion: Optional[dict[str, Any]] = None
        self._pending_exhaustion: Optional[dict[str, Any]] = None
        self._active_failure_reason: Optional[str] = None
        self._round_conflict_reason: Optional[str] = None
        self._rejections: list[dict[str, Any]] = []

    def bind_context(
        self,
        context: PerkSaveMonitorContext,
        *,
        new_activity: bool = False,
    ) -> bool:
        """Bind one owned activity, resetting only at an explicit new boundary."""

        if not isinstance(context, PerkSaveMonitorContext) or not context.valid():
            return False
        current = self._context
        if current is None:
            self._context = context
            return True
        if current == context:
            return True
        if (
            new_activity
            and current.runtime_session_id == context.runtime_session_id
            and current.activity_scope_id != context.activity_scope_id
        ):
            self._reset_evidence()
            self._context = context
            return True
        reason = (
            "target_binding_changed"
            if current.target_binding != context.target_binding
            else "runtime_or_activity_binding_changed"
        )
        self._reject(reason, component="binding")
        return False

    def observe_bundle(
        self,
        acquisition: PlayerSaveAcquisitionBundle,
        *,
        context: PerkSaveMonitorContext,
    ) -> str:
        """Consume one shared typed acquisition without another save read."""

        if not self._bind_observation_context(context):
            return "rejected_binding"
        if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
            self._reject("typed_acquisition_required", component="acquisition")
            return "rejected_acquisition"
        if acquisition.binding != context.target_binding:
            self._reject(
                "acquisition_target_binding_mismatch",
                component="binding",
                acquisition=acquisition,
            )
            return "rejected_binding"
        if (
            acquisition.status is not PlayerSaveAcquisitionStatus.COMPLETE
            or not acquisition.complete
            or acquisition.snapshot is None
        ):
            self._reject(
                acquisition.reason,
                component="acquisition",
                acquisition=acquisition,
            )
            return "rejected_acquisition"
        if acquisition.acquisition_type not in {
            PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
            PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
            PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        }:
            self._reject(
                "unsupported_acquisition_type",
                component="acquisition",
                acquisition=acquisition,
            )
            return "rejected_acquisition"
        snapshot = acquisition.snapshot
        runtime = getattr(snapshot, "runtime_save", None)
        game_version = getattr(snapshot, "game_version", None)
        mapping_id = getattr(snapshot, "mapping_id", None)
        shape_valid = getattr(snapshot, "shape_valid", None)
        mapping_supported = getattr(snapshot, "mapping_supported", None)
        if (
            not isinstance(runtime, NormalizedRuntimeSave)
            or type(game_version) is not int
            or game_version < 0
            or not isinstance(mapping_id, str)
            or mapping_id != runtime.mapping_id
            or shape_valid is not True
            or mapping_supported is not True
        ):
            self._reject(
                "runtime_projection_unavailable",
                component="mapping",
            )
            return "rejected_runtime_projection"
        return self._observe_runtime(
            runtime,
            game_version=game_version,
            context=context,
            acquisition=acquisition,
        )

    def observe_exhaustion(
        self,
        evidence: Mapping[str, Any],
        *,
        context: PerkSaveMonitorContext,
    ) -> bool:
        """Bind persisted stable ``View Perks`` evidence to this same round."""

        if not self._bind_observation_context(context):
            return False
        try:
            normalized = _normalize_exhaustion(evidence, context)
        except (TypeError, ValueError):
            return False
        normalized["checkpoint_capture_at_observation"] = (
            self._checkpoint.get("captured_at")
            if self._checkpoint is not None
            else None
        )
        if self._identity is None:
            self._pending_exhaustion = normalized
            return True
        return self._bind_exhaustion(normalized)

    def healthy_for(self, context: Optional[PerkSaveMonitorContext]) -> bool:
        """Return whether save monitoring can replace maintenance panel visits."""

        return bool(
            context is not None
            and self._context == context
            and self._checkpoint is not None
            and self._active_failure_reason is None
            and self._round_conflict_reason is None
        )

    def bound_exhaustion_evidence(
        self,
        context: Optional[PerkSaveMonitorContext],
    ) -> Optional[dict[str, Any]]:
        """Return exhaustion only after it carries the exact round identity."""

        if context is None or self._context != context:
            return None
        if self._exhaustion is None or self._identity is None:
            return None
        if self._exhaustion.get("active_round_identity") != self._identity:
            return None
        return copy.deepcopy(self._exhaustion)

    def terminal_evidence(
        self,
        *,
        context: Optional[PerkSaveMonitorContext],
        terminal_state: str,
    ) -> dict[str, Any]:
        """Return the versioned Game Over decision and retained source evidence."""

        status = "fallback_required"
        reason = "checkpoint_unavailable"
        context_matches = context is not None and self._context == context
        if str(terminal_state or "").upper() != "GAME_OVER":
            reason = "unsupported_terminal_route"
        elif not context_matches:
            reason = "terminal_context_unbound"
        elif self._round_conflict_reason:
            reason = self._round_conflict_reason
        elif self._active_failure_reason:
            reason = self._active_failure_reason
        elif self._checkpoint is None:
            reason = "checkpoint_unavailable"
        elif self._exhaustion is None:
            reason = "exhaustion_not_authoritatively_observed"
        elif self._terminal_window is None:
            reason = "terminal_checkpoint_window_unbound"
        else:
            qualifies, reason = self._checkpoint_includes_exhaustion()
            if qualifies:
                status = "complete_final_prefix"

        payload: dict[str, Any] = {
            "schema_version": PERK_SAVE_MONITOR_SCHEMA_VERSION,
            "status": status,
            "reason": reason,
            "source": "shared_player_save_perk_monitor",
            "ui_action_authority": False,
            "context_status": "bound" if context_matches else "unbound",
            "binding": (
                self._context.redacted() if self._context is not None else None
            ),
            "active_round_identity": copy.deepcopy(self._identity),
            "checkpoint": copy.deepcopy(self._checkpoint),
            "exhaustion": copy.deepcopy(self._exhaustion),
            "terminal_window": copy.deepcopy(self._terminal_window),
            "active_failure_reason": self._active_failure_reason,
            "round_conflict_reason": self._round_conflict_reason,
            "rejections": copy.deepcopy(self._rejections),
            "ui_fallback": {
                "required": status != "complete_final_prefix",
                "route": "existing_game_over_perks_panel",
            },
        }
        if status == "complete_final_prefix":
            payload["final_inventory"] = _saved_final_inventory(payload)
        return payload

    def _observe_runtime(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        game_version: int,
        context: PerkSaveMonitorContext,
        acquisition: PlayerSaveAcquisitionBundle,
    ) -> str:
        try:
            common = _validated_runtime_common(
                runtime,
                game_version=game_version,
                context=context,
                acquisition=acquisition,
            )
        except (TypeError, ValueError) as exc:
            self._reject(_safe_reason(exc), component="runtime")
            return "rejected_runtime"

        checkpoint = self._checkpoint
        if checkpoint is not None:
            if common["mapping_id"] != checkpoint["mapping_id"]:
                self._reject(
                    "mapping_changed", component="mapping", sticky=True
                )
                return "rejected_mapping"
            if common["audit_matrix_id"] != checkpoint["audit_matrix_id"]:
                self._reject(
                    "audit_matrix_changed", component="mapping", sticky=True
                )
                return "rejected_mapping"
            if common["game_version"] != checkpoint["game_version"]:
                self._reject(
                    "game_version_changed", component="mapping", sticky=True
                )
                return "rejected_mapping"

        if not runtime.round_active:
            return self._observe_inactive(
                runtime,
                common,
                acquisition=acquisition,
                context=context,
            )
        if acquisition.acquisition_type is PlayerSaveAcquisitionType.NATURAL_BOUNDARY:
            self._reject(
                "active_projection_at_terminal_boundary",
                component="boundary",
                acquisition=acquisition,
            )
            return "rejected_terminal_active_projection"

        identity = runtime.active_round_identity
        if identity is None:
            self._reject("active_identity_unavailable", component="identity")
            return "rejected_identity"
        identity_payload = identity.as_dict()
        if identity.game_version != game_version or not _sha256(identity.fingerprint):
            self._reject("active_identity_invalid", component="identity")
            return "rejected_identity"
        if self._identity is not None and self._identity != identity_payload:
            self._reject(
                "active_identity_changed", component="identity", sticky=True
            )
            return "rejected_identity"
        if runtime.perks_status != "observed" or runtime.perks is None:
            self._reject(
                _safe_reason(runtime.perks_reason or "perk_projection_unavailable"),
                component="perks",
            )
            return "rejected_perks"
        try:
            candidate = _checkpoint_from_runtime(
                runtime.perks,
                identity=identity_payload,
                common=common,
            )
        except (TypeError, ValueError) as exc:
            self._reject(_safe_reason(exc), component="perks")
            return "rejected_perks"

        if checkpoint is None:
            disposition = "initial_complete_prefix"
        else:
            prior_picks = checkpoint["picks"]
            candidate_picks = candidate["picks"]
            if candidate_picks == prior_picks:
                if (
                    candidate["saved_wave"] < checkpoint["saved_wave"]
                    or _parse_timestamp(candidate["captured_at"])
                    <= _parse_timestamp(checkpoint["captured_at"])
                ):
                    return "ignored_lagging_same_prefix"
                disposition = "unchanged_complete_prefix_observed_later"
            elif (
                len(candidate_picks) > len(prior_picks)
                and candidate_picks[: len(prior_picks)] == prior_picks
            ):
                if (
                    candidate["saved_wave"] < checkpoint["saved_wave"]
                    or _parse_timestamp(candidate["captured_at"])
                    <= _parse_timestamp(checkpoint["captured_at"])
                ):
                    self._reject(
                        "prefix_extension_predates_complete_checkpoint",
                        component="perks",
                        acquisition=acquisition,
                        sticky=True,
                    )
                    return "rejected_prefix_conflict"
                disposition = "strict_prefix_extension"
            elif (
                len(candidate_picks) < len(prior_picks)
                and prior_picks[: len(candidate_picks)] == candidate_picks
            ):
                self._reject(
                    "complete_prefix_regressed",
                    component="perks",
                    acquisition=acquisition,
                    sticky=True,
                )
                return "rejected_prefix_regression"
            else:
                self._reject(
                    "non_prefix_mutation",
                    component="perks",
                    acquisition=acquisition,
                    sticky=True,
                )
                return "rejected_non_prefix"

        candidate["acceptance"] = disposition
        self._identity = identity_payload
        self._checkpoint = candidate
        self._terminal_window = None
        self._active_failure_reason = None
        if self._pending_exhaustion is not None:
            pending = self._pending_exhaustion
            self._pending_exhaustion = None
            self._bind_exhaustion(pending)
        return disposition

    def _observe_inactive(
        self,
        runtime: NormalizedRuntimeSave,
        common: Mapping[str, Any],
        *,
        acquisition: PlayerSaveAcquisitionBundle,
        context: PerkSaveMonitorContext,
    ) -> str:
        boundary = acquisition.boundary
        if (
            acquisition.acquisition_type
            is not PlayerSaveAcquisitionType.NATURAL_BOUNDARY
            or boundary is None
            or boundary.kind is not PlayerSaveBoundaryKind.GAME_OVER
            or boundary.runtime_session_id != context.runtime_session_id
            or boundary.activity_scope_id != context.activity_scope_id
            or boundary.observed_at >= acquisition.acquisition_started_at
        ):
            self._reject(
                "inactive_clear_requires_bound_game_over_boundary",
                component="boundary",
                acquisition=acquisition,
            )
            return "rejected_unbound_terminal_clear"
        if runtime.active_round_identity is not None:
            self._reject("inactive_projection_has_identity", component="identity")
            return "rejected_inactive_identity"
        if (
            runtime.perks_status != "observed"
            or runtime.perks is None
            or runtime.perks.state != "cleared"
            or runtime.perks.picked_count != 0
            or runtime.perks.picks
            or runtime.perks.levels
        ):
            self._reject("terminal_perk_projection_not_cleared", component="perks")
            return "rejected_terminal_perks"
        checkpoint = self._checkpoint
        if checkpoint is None:
            self._reject("terminal_without_active_checkpoint", component="binding")
            return "rejected_terminal_without_checkpoint"
        if _parse_timestamp(common.get("captured_at")) <= _parse_timestamp(
            checkpoint.get("captured_at")
        ):
            self._reject(
                "terminal_capture_predates_active_checkpoint",
                component="boundary",
                acquisition=acquisition,
            )
            return "rejected_stale_terminal"
        if common["source_fingerprint"] == checkpoint["source_fingerprint"]:
            self._reject(
                "terminal_clear_reuses_active_payload",
                component="boundary",
                acquisition=acquisition,
            )
            return "rejected_terminal_payload_conflict"
        self._terminal_window = {
            "status": "closed_by_inactive_cleared_projection",
            **dict(common),
        }
        return "terminal_cleared_prefix_retained"

    def _bind_observation_context(self, context: PerkSaveMonitorContext) -> bool:
        if not isinstance(context, PerkSaveMonitorContext) or not context.valid():
            self._reject("invalid_observation_context", component="binding")
            return False
        if self._context is None:
            self._context = context
            return True
        if self._context != context:
            self._reject("observation_context_changed", component="binding")
            return False
        return True

    def _bind_exhaustion(self, evidence: Mapping[str, Any]) -> bool:
        if self._identity is None or self._context is None:
            return False
        if evidence.get("activity_scope_fingerprint") != _fingerprint_text(
            self._context.activity_scope_id
        ):
            return False
        claimed_identity = evidence.get("active_round_identity")
        if claimed_identity is not None and claimed_identity != self._identity:
            self._reject("exhaustion_identity_mismatch", component="exhaustion")
            return False
        bound = {
            **dict(evidence),
            "binding_status": "active_round_identity_bound",
            "active_round_identity": copy.deepcopy(self._identity),
        }
        current = self._exhaustion
        if current is not None:
            if current.get("event_id") != bound.get("event_id"):
                self._reject(
                    "conflicting_exhaustion_evidence",
                    component="exhaustion",
                    sticky=True,
                )
                return False
            current["stable_observation_count"] = max(
                int(current.get("stable_observation_count") or 0),
                int(bound.get("stable_observation_count") or 0),
            )
            current["ocr_confidence"] = max(
                float(current.get("ocr_confidence") or 0.0),
                float(bound.get("ocr_confidence") or 0.0),
            )
            return True
        self._exhaustion = bound
        return True

    def _checkpoint_includes_exhaustion(self) -> tuple[bool, str]:
        checkpoint = self._checkpoint or {}
        exhaustion = self._exhaustion or {}
        terminal = self._terminal_window or {}
        if exhaustion.get("active_round_identity") != self._identity:
            return False, "exhaustion_identity_mismatch"
        if terminal.get("status") != "closed_by_inactive_cleared_projection":
            return False, "terminal_checkpoint_window_unbound"
        captured_at = _parse_timestamp(checkpoint.get("captured_at"))
        observed_at = _parse_timestamp(exhaustion.get("observed_at"))
        if captured_at <= observed_at:
            return False, "checkpoint_predates_exhaustion"
        observed_wave = exhaustion.get("observed_wave")
        if type(observed_wave) is not int or checkpoint.get("saved_wave", -1) < observed_wave:
            return False, "checkpoint_wave_predates_exhaustion"
        if checkpoint.get("picked_count", 0) < 1:
            return False, "empty_checkpoint_cannot_prove_absence"
        return True, "complete_checkpoint_includes_exhaustion_boundary"

    def _reject(
        self,
        reason: str,
        *,
        component: str,
        acquisition: Optional[PlayerSaveAcquisitionBundle] = None,
        sticky: bool = False,
    ) -> None:
        safe_reason = _safe_reason(reason)
        self._active_failure_reason = safe_reason
        if sticky and self._round_conflict_reason is None:
            self._round_conflict_reason = safe_reason
        record = {"reason": safe_reason, "component": _safe_reason(component)}
        if isinstance(acquisition, PlayerSaveAcquisitionBundle):
            record["acquisition"] = acquisition.redacted_provenance()
        self._rejections.append(record)
        if len(self._rejections) > MAX_REJECTION_RECORDS:
            del self._rejections[: len(self._rejections) - MAX_REJECTION_RECORDS]

    def _reset_evidence(self) -> None:
        self._identity = None
        self._checkpoint = None
        self._terminal_window = None
        self._exhaustion = None
        self._pending_exhaustion = None
        self._active_failure_reason = None
        self._round_conflict_reason = None
        self._rejections = []


def merge_terminal_perk_evidence(
    monitoring: Mapping[str, Any],
    terminal_ui: Mapping[str, Any],
    *,
    top_bar_timeline: Optional[Mapping[str, Any]] = None,
    game_over_wave: Optional[int] = None,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Merge a saved prefix with final aggregate UI evidence conservatively."""

    base = {
        "schema_version": PERK_TERMINAL_MERGE_SCHEMA_VERSION,
        "source": "saved_prefix_plus_terminal_perks_panel",
        "status": "conflict",
        "reason": "invalid_input",
    }
    if (
        not isinstance(monitoring, Mapping)
        or monitoring.get("schema_version") != PERK_SAVE_MONITOR_SCHEMA_VERSION
    ):
        return None, {**base, "reason": "monitoring_record_unavailable"}
    checkpoint = monitoring.get("checkpoint")
    if not isinstance(checkpoint, Mapping):
        return None, {**base, "reason": "saved_prefix_unavailable"}
    quality = terminal_ui.get("quality") if isinstance(terminal_ui, Mapping) else None
    selected = terminal_ui.get("selected") if isinstance(terminal_ui, Mapping) else None
    if (
        not isinstance(quality, Mapping)
        or quality.get("valid") is not True
        or quality.get("source_complete") is not True
        or not isinstance(selected, Sequence)
        or isinstance(selected, (str, bytes, bytearray))
        or terminal_ui.get("order_semantics") != "latest_selected_first"
    ):
        return None, {
            **base,
            "reason": "terminal_ui_incomplete",
            "saved_prefix": _prefix_provenance(checkpoint),
        }

    terminal_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_ranks: set[int] = set()
    for index, raw in enumerate(selected, start=1):
        if not isinstance(raw, Mapping):
            return None, {**base, "reason": "terminal_ui_row_malformed"}
        display = " ".join(str(raw.get("display_text") or "").split())
        key = classify_perk_configuration_text(display)
        if not key or key == "empty_slot" or key in seen_keys:
            return None, {
                **base,
                "reason": "terminal_ui_family_unresolved_or_duplicate",
                "saved_prefix": _prefix_provenance(checkpoint),
            }
        seen_keys.add(key)
        rank = raw.get("latest_selection_rank", index)
        if type(rank) is not int or rank < 1 or rank in seen_ranks:
            return None, {**base, "reason": "terminal_ui_rank_invalid"}
        seen_ranks.add(rank)
        explicit_level = raw.get("final_level", raw.get("level"))
        if explicit_level is not None and (
            type(explicit_level) is not int or explicit_level < 1
        ):
            return None, {**base, "reason": "terminal_ui_level_invalid"}
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            return None, {**base, "reason": "terminal_ui_confidence_invalid"}
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            return None, {**base, "reason": "terminal_ui_confidence_invalid"}
        terminal_rows.append(
            {
                "perk_key": key,
                "display_text": display,
                "latest_selection_rank": rank,
                "instance_model": str(raw.get("instance_model") or "unknown"),
                "confidence": confidence,
                "final_level": explicit_level,
            }
        )
    if seen_ranks != set(range(1, len(terminal_rows) + 1)):
        return None, {**base, "reason": "terminal_ui_rank_invalid"}

    prefix_levels = {
        str(level.get("perk_key")): int(level.get("level"))
        for level in checkpoint.get("levels", [])
        if isinstance(level, Mapping)
        and isinstance(level.get("perk_key"), str)
        and type(level.get("level")) is int
    }
    terminal_by_key = {row["perk_key"]: row for row in terminal_rows}
    missing = sorted(set(prefix_levels) - set(terminal_by_key))
    if missing:
        return None, {
            **base,
            "reason": "terminal_ui_contradicts_saved_prefix",
            "missing_saved_families": missing,
            "saved_prefix": _prefix_provenance(checkpoint),
        }

    aggregates: list[dict[str, Any]] = []
    unresolved_existing_levels: list[str] = []
    final_inventory: list[dict[str, Any]] = []
    for row in terminal_rows:
        key = row["perk_key"]
        prior_level = prefix_levels.get(key, 0)
        final_level = row["final_level"]
        if final_level is None and row["instance_model"] == "single_instance":
            final_level = 1
        if final_level is not None and final_level < prior_level:
            return None, {
                **base,
                "reason": "terminal_ui_level_regresses_saved_prefix",
                "perk_key": key,
                "saved_prefix": _prefix_provenance(checkpoint),
            }
        final_inventory.append(
            {
                **row,
                "saved_level": prior_level,
                "final_level": final_level,
                "level_status": "exact" if final_level is not None else "unknown",
            }
        )
        if prior_level == 0:
            aggregates.append(
                {
                    "perk_key": key,
                    "kind": "aggregate_addition",
                    "level_before": 0,
                    "level_after": final_level,
                    "net_level_change": final_level,
                    "latest_selection_rank": row["latest_selection_rank"],
                }
            )
        elif final_level is None:
            unresolved_existing_levels.append(key)
        elif final_level > prior_level:
            aggregates.append(
                {
                    "perk_key": key,
                    "kind": "aggregate_level_change",
                    "level_before": prior_level,
                    "level_after": final_level,
                    "net_level_change": final_level - prior_level,
                    "latest_selection_rank": row["latest_selection_rank"],
                }
            )

    last_saved_wave = checkpoint.get("saved_wave")
    if type(last_saved_wave) is not int:
        return None, {**base, "reason": "saved_wave_unavailable"}
    if game_over_wave is not None and (
        type(game_over_wave) is not int or game_over_wave < last_saved_wave
    ):
        return None, {**base, "reason": "game_over_wave_conflicts_with_prefix"}
    scheduled = _tail_scheduled_waves(
        top_bar_timeline,
        after_wave=last_saved_wave,
        through_wave=game_over_wave,
    )
    atomic = bool(
        not unresolved_existing_levels
        and aggregates
        and type(game_over_wave) is int
        and len(scheduled) == len(aggregates)
        and all(item.get("net_level_change") == 1 for item in aggregates)
    )
    if atomic:
        chronological = list(
            reversed(sorted(aggregates, key=lambda item: item["latest_selection_rank"]))
        )
        for sequence, (aggregate, wave) in enumerate(
            zip(chronological, scheduled),
            start=len(checkpoint.get("picks", [])) + 1,
        ):
            aggregate.update(
                {
                    "sequence": sequence,
                    "wave": wave,
                    "order_status": "exact_unique_correspondence",
                    "wave_status": "exact_passive_schedule_correspondence",
                }
            )
    else:
        for aggregate in aggregates:
            aggregate.update(
                {
                    "sequence": None,
                    "wave": None,
                    "order_status": "unknown",
                    "wave_status": "bounded_interval",
                    "interval": {
                        "after_saved_wave_exclusive": last_saved_wave,
                        "before_game_over_wave_inclusive": game_over_wave,
                    },
                }
            )

    warnings = []
    if unresolved_existing_levels:
        warnings.append(
            "Terminal collapsed rows could not prove whether saved leveled "
            "families changed after the checkpoint: "
            + ", ".join(sorted(unresolved_existing_levels))
        )
    if aggregates and not atomic:
        warnings.append(
            "Terminal tail order and waves remain bounded rather than exact"
        )
    merge = {
        **base,
        "status": "complete",
        "reason": "terminal_aggregate_merged_without_rewriting_saved_prefix",
        "saved_prefix": _prefix_provenance(checkpoint),
        "terminal_aggregate": {
            "source_method": "terminal_perks_panel_ocr",
            "order_semantics": "latest_selection_recency",
            "rows": terminal_rows,
        },
        "scheduled_tail_waves": scheduled,
        "tail_correspondence": (
            "unique" if atomic else "interval_or_unknown"
        ),
        "tail_aggregates": aggregates,
        "warnings": warnings,
    }
    normalized = {
        "schema_version": PERK_FINAL_INVENTORY_SCHEMA_VERSION,
        "source_method": "player_save_checkpoint_plus_terminal_ui",
        "status": "complete_with_terminal_aggregate",
        "order_semantics": "saved_prefix_exact_oldest_first",
        "exact_saved_prefix": copy.deepcopy(dict(checkpoint)),
        "exact_saved_picks": copy.deepcopy(list(checkpoint.get("picks", []))),
        "final_inventory": final_inventory,
        "terminal_tail": {
            "status": "merged",
            "aggregate_semantics": (
                "one_collapsed_family_row_never_implies_individual_selections"
            ),
            "aggregates": copy.deepcopy(aggregates),
            "warnings": list(warnings),
        },
        "terminal_ui": copy.deepcopy(dict(terminal_ui)),
        "quality": {
            "valid": True,
            "source_complete": True,
            "source_reason": "saved_prefix_and_complete_terminal_aggregate",
            "warnings": list(warnings),
            "retain_source_images": False,
        },
    }
    return normalized, merge


def _saved_final_inventory(monitoring: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = monitoring["checkpoint"]
    return {
        "schema_version": PERK_FINAL_INVENTORY_SCHEMA_VERSION,
        "source_method": "player_save_perk_checkpoint",
        "status": "complete_exact_saved_inventory",
        "order_semantics": "oldest_selected_first_exact_saved_order",
        "exact_saved_prefix": copy.deepcopy(checkpoint),
        "exact_saved_picks": copy.deepcopy(checkpoint["picks"]),
        "final_inventory": copy.deepcopy(checkpoint["levels"]),
        "exhaustion": copy.deepcopy(monitoring["exhaustion"]),
        "terminal_tail": {
            "status": "not_required",
            "aggregates": [],
            "reason": "qualifying_post_exhaustion_active_checkpoint",
        },
        "quality": {
            "valid": True,
            "source_complete": True,
            "source_reason": "complete_checkpoint_includes_exhaustion_boundary",
            "warnings": [],
            "retain_source_images": False,
        },
    }


def _validated_runtime_common(
    runtime: NormalizedRuntimeSave,
    *,
    game_version: int,
    context: PerkSaveMonitorContext,
    acquisition: PlayerSaveAcquisitionBundle,
) -> dict[str, Any]:
    if not isinstance(runtime, NormalizedRuntimeSave):
        raise ValueError("runtime_projection_unavailable")
    if type(game_version) is not int or game_version < 0:
        raise ValueError("game_version_unavailable")
    if not runtime.mapping_id or not runtime.audit_matrix_id:
        raise ValueError("mapping_identity_unavailable")
    if type(runtime.save_revision) is not int or runtime.save_revision < 0:
        raise ValueError("save_revision_invalid")
    if type(runtime.current_wave) is not int or runtime.current_wave < 0:
        raise ValueError("saved_wave_invalid")
    capture = runtime.capture
    if not isinstance(capture, Mapping):
        raise ValueError("capture_provenance_unavailable")
    captured_at = _parse_timestamp(capture.get("captured_at"))
    if acquisition.captured_at is None or captured_at != acquisition.captured_at:
        raise ValueError("capture_time_disagrees_with_acquisition")
    source_fingerprint = capture.get("source_sha256")
    if not _sha256(source_fingerprint):
        raise ValueError("source_fingerprint_invalid")
    started = acquisition.acquisition_started_at
    completed = acquisition.acquisition_completed_at
    return {
        "mapping_id": runtime.mapping_id,
        "audit_matrix_id": runtime.audit_matrix_id,
        "game_version": game_version,
        "save_revision": runtime.save_revision,
        "saved_wave": runtime.current_wave,
        "source_fingerprint": source_fingerprint,
        "captured_at": captured_at.isoformat(),
        "capture_provenance": {
            "source_name": str(capture.get("source_name") or "playerInfo.dat"),
            "source_fingerprint": source_fingerprint,
            "acquisition": acquisition.redacted_provenance(),
            "acquisition_started_at": started.isoformat(),
            "acquisition_completed_at": completed.isoformat(),
        },
        "acquisition_type": acquisition.acquisition_type.value,
        "acquisition_started_at": started.isoformat(),
        "acquisition_completed_at": completed.isoformat(),
        "binding": context.redacted(),
    }


def _checkpoint_from_runtime(
    perks: RuntimePerkSnapshot,
    *,
    identity: Mapping[str, Any],
    common: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(perks, RuntimePerkSnapshot) or perks.state != "active_round":
        raise ValueError("complete_active_perk_projection_required")
    if perks.picked_count != len(perks.picks) or not _sha256(perks.fingerprint):
        raise ValueError("perk_prefix_count_or_fingerprint_invalid")
    picks: list[dict[str, Any]] = []
    levels_by_id: dict[int, tuple[str, int]] = {}
    ids_by_key: dict[str, int] = {}
    prior_wave = -1
    for index, pick in enumerate(perks.picks, start=1):
        if (
            pick.sequence != index
            or type(pick.wave) is not int
            or pick.wave < 0
            or pick.wave < prior_wave
            or pick.wave > common["saved_wave"]
            or type(pick.perk_id) is not int
            or pick.perk_id < 0
            or not isinstance(pick.perk_key, str)
            or _SAFE_KEY_RE.fullmatch(pick.perk_key) is None
            or type(pick.level_after) is not int
            or pick.level_after < 1
        ):
            raise ValueError("perk_pick_malformed")
        prior = levels_by_id.get(pick.perk_id)
        if prior is not None and prior[0] != pick.perk_key:
            raise ValueError("perk_id_semantic_conflict")
        prior_id = ids_by_key.get(pick.perk_key)
        if prior_id is not None and prior_id != pick.perk_id:
            raise ValueError("perk_semantic_key_id_conflict")
        expected_level = (prior[1] if prior is not None else 0) + 1
        if pick.level_after != expected_level:
            raise ValueError("perk_pick_level_not_monotonic")
        levels_by_id[pick.perk_id] = (pick.perk_key, pick.level_after)
        ids_by_key[pick.perk_key] = pick.perk_id
        prior_wave = pick.wave
        picks.append(
            {
                "sequence": pick.sequence,
                "saved_wave": pick.wave,
                "perk_id": pick.perk_id,
                "perk_key": pick.perk_key,
                "level_after": pick.level_after,
                "source": "exact_saved_pick",
            }
        )
    expected_levels = sorted(
        (perk_id, perk_key, level)
        for perk_id, (perk_key, level) in levels_by_id.items()
    )
    if sorted(perks.levels) != expected_levels:
        raise ValueError("perk_levels_disagree_with_pick_prefix")
    levels = [
        {"perk_id": perk_id, "perk_key": perk_key, "level": level}
        for perk_id, perk_key, level in expected_levels
    ]
    return {
        "schema_version": 1,
        **dict(common),
        "active_round_identity": copy.deepcopy(dict(identity)),
        "complete": True,
        "picked_count": perks.picked_count,
        "order_semantics": "oldest_selected_first_exact_saved_order",
        "picks": picks,
        "levels": levels,
        "prefix_fingerprint": perks.fingerprint,
    }


def _normalize_exhaustion(
    evidence: Mapping[str, Any],
    context: PerkSaveMonitorContext,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise TypeError("exhaustion evidence must be a mapping")
    if evidence.get("source") != "stable_top_bar_view_perks":
        raise ValueError("unsupported exhaustion source")
    if str(evidence.get("activity_scope_id") or "") != context.activity_scope_id:
        raise ValueError("exhaustion activity scope mismatch")
    observed_wave = evidence.get("observed_wave")
    stable_count = evidence.get("stable_observation_count")
    confidence = evidence.get("ocr_confidence")
    if type(observed_wave) is not int or observed_wave < 0:
        raise ValueError("exhaustion wave invalid")
    if type(stable_count) is not int or stable_count < 2:
        raise ValueError("exhaustion is not stable")
    if (
        not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not MIN_EXHAUSTION_OCR_CONFIDENCE <= float(confidence) <= 100
    ):
        raise ValueError("exhaustion confidence invalid")
    observed_at = _parse_timestamp(evidence.get("observed_at"))
    provenance = evidence.get("capture_provenance")
    if not isinstance(provenance, Mapping) or not _sha256(
        provenance.get("source_fingerprint")
    ):
        raise ValueError("exhaustion capture provenance invalid")
    event_id = str(evidence.get("event_id") or "")
    if not event_id or _SHA256_RE.fullmatch(event_id) is None:
        raise ValueError("exhaustion event id invalid")
    binding_status = str(evidence.get("binding_status") or "")
    raw_identity = evidence.get("active_round_identity")
    if binding_status == "active_round_identity_bound":
        claimed_identity = _normalized_active_round_identity(raw_identity)
    elif binding_status in {"", "pending_active_round_identity"}:
        if raw_identity is not None:
            raise ValueError("pending exhaustion has an active identity")
        claimed_identity = None
    else:
        raise ValueError("exhaustion binding status invalid")
    normalized = {
        "schema_version": 1,
        "source": "stable_top_bar_view_perks",
        "event_id": event_id,
        "activity_scope_fingerprint": _fingerprint_text(
            context.activity_scope_id
        ),
        "binding_status": (
            "active_round_identity_bound"
            if claimed_identity is not None
            else "pending_active_round_identity"
        ),
        "observed_wave": observed_wave,
        "observed_at": observed_at.isoformat(),
        "stable_observation_count": stable_count,
        "ocr_confidence": float(confidence),
        "capture_provenance": {
            "source": str(provenance.get("source") or "main_loop_frame"),
            "region": str(provenance.get("region") or "perk_progress_text"),
            "source_fingerprint": str(provenance["source_fingerprint"]),
        },
    }
    if claimed_identity is not None:
        normalized["active_round_identity"] = claimed_identity
    return normalized


def _tail_scheduled_waves(
    timeline: Optional[Mapping[str, Any]],
    *,
    after_wave: int,
    through_wave: Optional[int],
) -> list[int]:
    if not isinstance(timeline, Mapping):
        return []
    top_bar = timeline.get("passive_top_bar")
    if not isinstance(top_bar, Mapping):
        return []
    raw = top_bar.get("selection_boundaries")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    waves: list[int] = []
    for item in raw:
        if not isinstance(item, Mapping):
            return []
        wave = item.get("scheduled_wave")
        coverage = item.get("boundary_coverage")
        if type(wave) is not int or coverage != "complete":
            return []
        if wave <= after_wave or (through_wave is not None and wave > through_wave):
            continue
        if wave in waves:
            return []
        waves.append(wave)
    return sorted(waves)


def _prefix_provenance(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mapping_id": checkpoint.get("mapping_id"),
        "save_revision": checkpoint.get("save_revision"),
        "saved_wave": checkpoint.get("saved_wave"),
        "picked_count": checkpoint.get("picked_count"),
        "prefix_fingerprint": checkpoint.get("prefix_fingerprint"),
        "captured_at": checkpoint.get("captured_at"),
    }


def _normalized_active_round_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("active round identity unavailable")
    normalized = {
        "game_version": value.get("game_version"),
        "current_tier": value.get("current_tier"),
        "rounds_started_this_tier": value.get("rounds_started_this_tier"),
        "round_seed": value.get("round_seed"),
        "fingerprint": value.get("fingerprint"),
    }
    if (
        type(normalized["game_version"]) is not int
        or normalized["game_version"] < 0
        or type(normalized["current_tier"]) is not int
        or normalized["current_tier"] < 0
        or type(normalized["rounds_started_this_tier"]) is not int
        or normalized["rounds_started_this_tier"] < 0
        or type(normalized["round_seed"]) is not int
        or normalized["round_seed"] <= 0
        or not _sha256(normalized["fingerprint"])
    ):
        raise ValueError("active round identity invalid")
    return normalized


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp unavailable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp malformed") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _fingerprint_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _safe_reason(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "unknown").lower())
    return normalized.strip("_")[:128] or "unknown"


__all__ = [
    "PERK_FINAL_INVENTORY_SCHEMA_VERSION",
    "PERK_SAVE_MONITOR_SCHEMA_VERSION",
    "PERK_TERMINAL_MERGE_SCHEMA_VERSION",
    "PerkSaveMonitor",
    "PerkSaveMonitorContext",
    "merge_terminal_perk_evidence",
]
