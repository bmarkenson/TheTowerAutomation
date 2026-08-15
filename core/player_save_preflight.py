"""Guarded save-first evidence across exact new-battle boundaries.

The save can suppress redundant observation only.  It never authorizes input,
repair, lifecycle progression, attachment, or strategy dispatch.  All device
operations remain bound to the process-owned exact ADB target and every public
record is deliberately free of raw save data and private target identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import os
import time
from typing import Any, Callable, Mapping, Optional

from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.player_save import (
    PlayerSaveSnapshot,
    SAVE_ACCEPTED_DISPOSITIONS,
    SAVE_MISMATCH_DISPOSITION,
    reconcile_acquired_requirements,
    reconcile_direct_retry_requirements,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_history import history_metadata_from_acquisition
from core.player_save_confirmed_local_mapping import (
    ConfirmedLocalMappingError,
    ConfirmedLocalMappingStore,
)
from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    PlayerSaveMappingCandidateError,
    build_mapping_candidate_record,
    fingerprint_json,
    resolve_mapping_candidates,
)
from core.player_save_serialization import (
    GuardedPlayerSaveSerializer,
    GuardedSerializationStatus,
    background_to_android_home,
    restore_tower_launcher,
)
from core.state_detector import detect_state_and_overlays
from utils.logger import (
    log,
    log_action_intent,
    log_input,
    log_result,
    new_operation_id,
)


PLAYER_SAVE_PREFLIGHT_MODES = frozenset(
    {"save_first", "force_ui", "comparison_audit"}
)
DEFAULT_PLAYER_SAVE_PREFLIGHT_MODE = "save_first"

HOME_SAVE_CHECKS = frozenset(
    {
        "cards_deck",
        "card_recharge_modes",
        "workshop_preset",
        "bots_preset",
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
        "free_upgrade_locks",
        "guardian_chips",
        "modules",
        "poison_swamp_stun",
    }
)
CARRIED_SAVE_CHECKS = frozenset(
    {
        *HOME_SAVE_CHECKS,
        "auto_pick_perks",
        "damage_slider",
        "orb_distance",
        "target_priority",
        "ultimate_weapon_primaries",
        "spotlight_missiles",
    }
)


class PlayerSavePreflightStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"


class CarriedEvidenceState(str, Enum):
    PENDING_LAUNCH = "pending_launch"
    LAUNCH_DISPATCHED = "launch_dispatched"
    SUSPENDED = "suspended"
    BOUND_RUNNING = "bound_running"
    INVALIDATED = "invalidated"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class PlayerSavePreflightContext:
    """Exact private continuity identity for one Home preflight."""

    runtime_session_id: str
    preflight_session_id: str
    activity_scope_id: str
    strategy_name: str
    configuration_fingerprint: str
    target: str
    target_generation: int

    def matches(self, other: "PlayerSavePreflightContext") -> bool:
        """Match the operation owner; activity scope is report metadata only."""

        return bool(
            isinstance(other, PlayerSavePreflightContext)
            and self.runtime_session_id == other.runtime_session_id
            and self.preflight_session_id == other.preflight_session_id
            and self.strategy_name == other.strategy_name
            and self.configuration_fingerprint
            == other.configuration_fingerprint
            and self.target == other.target
            and self.target_generation == other.target_generation
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "runtime_session": _redacted(self.runtime_session_id),
            "preflight_session": _redacted(self.preflight_session_id),
            "activity_scope": _redacted(self.activity_scope_id),
            "strategy": self.strategy_name,
            "configuration_fingerprint": self.configuration_fingerprint,
            "target_generation": _redacted(
                f"{self.target}\0{self.target_generation}"
            ),
        }


@dataclass
class CarriedPlayerSaveEvidence:
    """Single-use save decisions for the exact next runtime-owned battle."""

    context: PlayerSavePreflightContext
    snapshot_fingerprint: str
    effective_mapping_fingerprint: str
    values: dict[str, Any]
    state: CarriedEvidenceState = CarriedEvidenceState.PENDING_LAUNCH
    launch_kind: str = "home_new_battle"
    source_activity_scope_id: str = field(default="", repr=False)
    battle_started_observed: bool = False
    consumed: set[str] = field(default_factory=set)
    fallback_reasons: dict[str, str] = field(default_factory=dict)
    invalidation_reason: str = ""

    def mark_runtime_launch(
        self,
        context: PlayerSavePreflightContext,
        *,
        control: HomeBattleControl,
        action_authorized: bool,
        dispatched: bool,
    ) -> bool:
        if self.state is not CarriedEvidenceState.PENDING_LAUNCH:
            return False
        if self.launch_kind != "home_new_battle":
            self.invalidate("runtime_launch_kind_changed")
            return False
        if (
            not self.context.matches(context)
            or control is not HomeBattleControl.NEW_BATTLE
        ):
            self.invalidate("runtime_owned_new_battle_launch_unproven")
            return False
        if not dispatched:
            return False
        if not action_authorized:
            self.invalidate("runtime_launch_dispatched_without_authority")
            return False
        self.state = CarriedEvidenceState.LAUNCH_DISPATCHED
        return True

    def bind_running(
        self,
        context: PlayerSavePreflightContext,
        *,
        battle_started: bool,
        stable_running: bool,
        continuity_verified: bool,
    ) -> bool:
        if self.state is CarriedEvidenceState.BOUND_RUNNING:
            if self.context.matches(context):
                return True
            self.invalidate("carried_evidence_context_changed")
            return False
        if self.state is not CarriedEvidenceState.LAUNCH_DISPATCHED:
            if battle_started:
                self.invalidate("first_running_boundary_continuity_failed")
            return False
        if not self.context.matches(context):
            self.invalidate("first_running_boundary_continuity_failed")
            return False
        if battle_started:
            self.battle_started_observed = True
        if (
            not self.battle_started_observed
            or not stable_running
            or not continuity_verified
        ):
            return False
        self.state = CarriedEvidenceState.BOUND_RUNNING
        return True

    def consume(
        self,
        check_id: str,
        context: PlayerSavePreflightContext,
    ) -> Any:
        normalized = str(check_id)
        if (
            self.state is CarriedEvidenceState.BOUND_RUNNING
            and not self.context.matches(context)
        ):
            self.invalidate("carried_evidence_context_changed")
            return None
        if (
            self.state is not CarriedEvidenceState.BOUND_RUNNING
            or normalized in self.consumed
            or normalized not in self.values
        ):
            return None
        self.consumed.add(normalized)
        value = self.values[normalized]
        if self.consumed == set(self.values):
            self.state = CarriedEvidenceState.CONSUMED
        return value

    def reject_checks(self, check_ids: tuple[str, ...], reason: str) -> None:
        """Route only changed requirements to UI without distrusting the save."""

        normalized_reason = str(reason or "check_requires_ui_fallback")
        for check_id in {str(value) for value in check_ids}:
            if check_id in self.values:
                self.values.pop(check_id, None)
                self.fallback_reasons[check_id] = normalized_reason
        if (
            self.state not in {
                CarriedEvidenceState.INVALIDATED,
                CarriedEvidenceState.CONSUMED,
            }
            and not (set(self.values) - self.consumed)
        ):
            self.state = CarriedEvidenceState.CONSUMED

    def invalidate(self, reason: str) -> None:
        if self.state in {
            CarriedEvidenceState.INVALIDATED,
            CarriedEvidenceState.CONSUMED,
        }:
            return
        self.values.clear()
        self.state = CarriedEvidenceState.INVALIDATED
        self.invalidation_reason = str(reason or "continuity_invalidated")

    def suspend(self, reason: str) -> None:
        """Retain diagnostics while requiring fresh save or UI evidence."""

        if self.state in {
            CarriedEvidenceState.INVALIDATED,
            CarriedEvidenceState.CONSUMED,
        }:
            return
        self.state = CarriedEvidenceState.SUSPENDED
        self.invalidation_reason = str(reason or "carry_revalidation_required")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "state": self.state.value,
            "provenance": self.context.redacted(),
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "effective_mapping_fingerprint": (
                self.effective_mapping_fingerprint
            ),
            "transition": {
                "kind": self.launch_kind,
                "source_activity_scope": (
                    _redacted(self.source_activity_scope_id)
                    if self.source_activity_scope_id
                    else None
                ),
            },
            "available_checks": sorted(set(self.values) - self.consumed),
            "consumed_checks": sorted(self.consumed),
            "fallback_checks": dict(sorted(self.fallback_reasons.items())),
            "invalidation_reason": self.invalidation_reason,
        }


@dataclass(frozen=True)
class PlayerSavePreflightResult:
    status: PlayerSavePreflightStatus
    reason: str
    mode: str
    decisions: Mapping[str, Mapping[str, Any]]
    provenance: Mapping[str, Any]
    safe_ui_fallback: bool
    history_tail: Mapping[str, Any] = field(default_factory=dict)
    history_scope_id: Optional[str] = field(
        default=None,
        repr=False,
        compare=False,
    )
    carry: Optional[CarriedPlayerSaveEvidence] = field(
        default=None,
        repr=False,
        compare=False,
    )
    acquisition: Optional[PlayerSaveAcquisitionBundle] = field(
        default=None,
        repr=False,
        compare=False,
    )
    context: Optional[PlayerSavePreflightContext] = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def ready(self) -> bool:
        return self.status is PlayerSavePreflightStatus.READY

    @property
    def accepted_checks(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                check_id
                for check_id, decision in self.decisions.items()
                if decision.get("disposition") in SAVE_ACCEPTED_DISPOSITIONS
            )
        )

    @property
    def trusted_mismatch_checks(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                check_id
                for check_id, decision in self.decisions.items()
                if decision.get("disposition") == SAVE_MISMATCH_DISPOSITION
            )
        )

    @property
    def ui_required_checks(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                check_id
                for check_id, decision in self.decisions.items()
                if decision.get("ui_required") is True
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status.value,
            "reason": self.reason,
            "mode": self.mode,
            "safe_ui_fallback": self.safe_ui_fallback,
            "provenance": dict(self.provenance),
            "checks": {
                str(key): dict(value)
                for key, value in sorted(self.decisions.items())
            },
            "accepted_checks": list(self.accepted_checks),
            "trusted_mismatch_checks": list(self.trusted_mismatch_checks),
            "ui_required_checks": list(self.ui_required_checks),
            "history_tail": dict(self.history_tail),
            "carry": self.carry.as_dict() if self.carry is not None else None,
        }


def normalize_player_save_preflight_mode(value: Any) -> str:
    mode = str(value or DEFAULT_PLAYER_SAVE_PREFLIGHT_MODE).strip().lower()
    if mode not in PLAYER_SAVE_PREFLIGHT_MODES:
        raise ValueError(
            "runtime_policy.player_save_preflight must be save_first, "
            "force_ui, or comparison_audit"
        )
    return mode


class PlayerSavePreflightCoordinator:
    """Acquire, reconcile, and bind one authoritative boundary snapshot."""

    def __init__(
        self,
        *,
        acquirer: StablePlayerSaveAcquirer,
        context_fn: Callable[[], PlayerSavePreflightContext],
        action_guard_fn: Callable[[], bool],
        capture_fn: Optional[Callable[[], Any]] = None,
        detector: Callable[[Any], Mapping[str, Any]] = (
            detect_state_and_overlays
        ),
        home_control_fn: Callable[[Any], Any] = detect_home_battle_control,
        background_fn: Optional[Callable[[str], bool]] = None,
        foreground_fn: Optional[Callable[[str], bool]] = None,
        mapping_candidate_store: Optional[
            AppendOnlyMappingCandidateStore
        ] = None,
        confirmed_local_mapping_store: Optional[
            ConfirmedLocalMappingStore
        ] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(acquirer, StablePlayerSaveAcquirer):
            raise TypeError("player-save preflight requires the shared acquirer")
        self._context_fn = context_fn
        self._action_guard_fn = action_guard_fn
        self._capture_fn = capture_fn or _capture_default
        self._detector = detector
        self._home_control_fn = home_control_fn
        self._background_fn = background_fn or background_to_android_home
        self._foreground_fn = foreground_fn or restore_tower_launcher
        self._acquirer = acquirer
        self._mapping_candidate_store = (
            mapping_candidate_store or AppendOnlyMappingCandidateStore()
        )
        self._confirmed_local_mapping_store = (
            confirmed_local_mapping_store or ConfirmedLocalMappingStore()
        )
        self._sleep_fn = sleep_fn
        self._carry: Optional[CarriedPlayerSaveEvidence] = None
        self._decisions: dict[str, dict[str, Any]] = {}
        self._ui_verified_checks: dict[str, str] = {}
        self._snapshot_invalidation_reason = ""
        self._mapping_candidate_context: Optional[
            PlayerSavePreflightContext
        ] = None
        self._mapping_candidate_snapshot: Optional[PlayerSaveSnapshot] = None
        self._mapping_candidate_records: list[dict[str, Any]] = []
        self._mapping_candidate_record_ids: set[str] = set()
        self._mapping_candidate_observation_keys: set[str] = set()
        self._mapping_candidate_observation_count = 0

    @property
    def carry(self) -> Optional[CarriedPlayerSaveEvidence]:
        return self._carry

    @property
    def snapshot_invalidated(self) -> bool:
        return bool(self._snapshot_invalidation_reason)

    @property
    def ui_verified_checks(self) -> Mapping[str, str]:
        return dict(self._ui_verified_checks)

    @property
    def mapping_candidate_records(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(dict(record) for record in self._mapping_candidate_records)

    def decision(self, check_id: str) -> Mapping[str, Any]:
        return dict(self._decisions.get(str(check_id), {}))

    def acquire(
        self,
        requirements: Mapping[str, Any],
        *,
        mode: Any = DEFAULT_PLAYER_SAVE_PREFLIGHT_MODE,
        initial_frame: Any = None,
    ) -> PlayerSavePreflightResult:
        selected_mode = normalize_player_save_preflight_mode(mode)
        requested = requested_player_save_check_ids(requirements)
        if self._carry is not None:
            self._carry.invalidate("superseded_by_new_home_preflight")
            self._carry = None
        self._decisions = {}
        self._ui_verified_checks = {}
        self._snapshot_invalidation_reason = ""
        self._reset_mapping_candidate_window()
        provenance: dict[str, Any] = {
            "context": {"status": "not_acquired"},
            "serialization": "not_attempted",
            "freshness": "unverified",
            "snapshot_trust": {
                "status": "not_acquired",
                "reason": "not_attempted",
            },
        }
        if selected_mode == "force_ui":
            decisions = _all_ui_decisions(requested, "force_ui_policy")
            provenance["snapshot_trust"] = {
                "status": "not_acquired",
                "reason": "force_ui_policy",
            }
            self._decisions = {
                check_id: dict(decision)
                for check_id, decision in decisions.items()
            }
            return PlayerSavePreflightResult(
                PlayerSavePreflightStatus.READY,
                "force_ui_policy",
                selected_mode,
                decisions,
                provenance,
                True,
                _history_ui_decision("force_ui_policy"),
            )

        operation_id = new_operation_id()
        try:
            context = self._context_fn()
        except Exception:
            log_action_intent(
                "Refreshing Home configuration evidence",
                reason=(
                    "verify the exact owned target before attempting the "
                    "Android-Home serialization boundary"
                ),
                detail=(
                    "[PLAYER_SAVE_PREFLIGHT] result=pending "
                    f"mode={selected_mode} checks={sorted(requested)} "
                    "provenance=context_unavailable"
                ),
                operation_id=operation_id,
            )
            return self._blocked_result(
                requested,
                selected_mode,
                provenance,
                "preflight_context_unavailable",
                operation_id,
            )
        provenance["context"] = context.redacted()
        log_action_intent(
            "Refreshing Home configuration evidence",
            reason=(
                "background the game at the verified New Battle boundary so "
                "the exact owned target serializes one stable save"
            ),
            detail=(
                "[PLAYER_SAVE_PREFLIGHT] result=pending "
                f"mode={selected_mode} checks={sorted(requested)} "
                f"provenance={context.redacted()}"
            ),
            operation_id=operation_id,
        )

        serializer = GuardedPlayerSaveSerializer(
            acquirer=self._acquirer,
            context_guard_fn=lambda: self._same_context(context),
            action_guard_fn=self._action_allowed,
            source_guard_fn=lambda frame, stable: self._verify_home(
                frame,
                stable=stable,
            ),
            background_fn=self._background_fn,
            foreground_fn=self._foreground_fn,
            sleep_fn=self._sleep_fn,
            input_log_fn=log_input,
            debug_log_fn=log,
            log_prefix="PLAYER_SAVE_PREFLIGHT",
        )
        serialized = serializer.acquire(
            expected_target=context.target,
            expected_generation=context.target_generation,
            target_generation_detail=context.redacted()["target_generation"],
            source_label="the verified New Battle boundary",
            initial_frame=initial_frame,
        )
        provenance["background_dispatched"] = bool(
            serialized.background_dispatched
        )
        provenance["lifecycle_input_attempted"] = bool(
            serialized.lifecycle_input_attempted
        )
        provenance["source_restored"] = bool(serialized.source_restored)
        if serialized.background_dispatched:
            provenance["serialization"] = "background_dispatched"
        if serialized.status is GuardedSerializationStatus.BLOCKED:
            reason = {
                "initial_source_boundary_unverified": (
                    "initial_new_battle_boundary_unverified"
                ),
                "restored_source_boundary_unverified": (
                    "restored_target_or_new_battle_boundary_unverified"
                ),
                "restored_target_binding_unverified": (
                    "restored_target_or_new_battle_boundary_unverified"
                ),
                "restored_context_boundary_unverified": (
                    "restored_target_or_new_battle_boundary_unverified"
                ),
                "restored_source_convergence_timeout": (
                    "restored_new_battle_boundary_convergence_timeout"
                ),
            }.get(serialized.reason, serialized.reason)
            return self._blocked_result(
                requested,
                selected_mode,
                provenance,
                reason,
                operation_id,
            )

        acquisition = serialized.acquisition
        if acquisition is not None:
            provenance["acquisition"] = acquisition.redacted_provenance()
        snapshot = serialized.snapshot
        acquisition_reason = serialized.reason

        provenance["serialization"] = "verified_android_home_boundary"
        provenance["freshness"] = "verified"
        if snapshot is None:
            decisions = _all_ui_decisions(requested, acquisition_reason)
            provenance["snapshot_trust"] = {
                "status": "invalidated",
                "reason": acquisition_reason,
            }
            self._decisions = {
                check_id: dict(decision)
                for check_id, decision in decisions.items()
            }
            return self._ready_result(
                acquisition_reason,
                selected_mode,
                decisions,
                provenance,
                operation_id,
                history_tail=_history_ui_decision(acquisition_reason),
                acquisition=acquisition,
                context=context,
            )

        provenance["source_fingerprint"] = _redacted(
            f"snapshot\0{snapshot.source_sha256}"
        )
        provenance["mapping_id"] = snapshot.mapping_id
        provenance["effective_mapping_fingerprint"] = (
            snapshot.effective_mapping_fingerprint
        )
        provenance["save_version"] = {
            "data": snapshot.data_version,
            "game": snapshot.game_version,
        }
        assert acquisition is not None
        plan = reconcile_acquired_requirements(
            acquisition,
            requirements,
            force_ui_audit=selected_mode == "comparison_audit",
        )
        decisions = {
            str(key): dict(value)
            for key, value in (plan.get("checks") or {}).items()
        }
        provenance["snapshot_trust"] = dict(
            plan.get("snapshot_trust") or {
                "status": "invalidated",
                "reason": "snapshot_trust_unavailable",
            }
        )
        self._decisions = {
            check_id: dict(decision)
            for check_id, decision in decisions.items()
        }
        if (
            provenance["snapshot_trust"].get("status") == "trusted"
            and snapshot.mapping_id is not None
            and snapshot.data_version is not None
            and snapshot.game_version is not None
            and snapshot.mapping_resolution
            in {"exact", "compatible_exact_revision"}
        ):
            self._mapping_candidate_context = context
            self._mapping_candidate_snapshot = snapshot
        try:
            history_observation = history_metadata_from_acquisition(acquisition)
        except Exception:
            history_tail = _history_ui_decision(
                "runtime_history_projection_unavailable"
            )
        else:
            history_tail = _history_save_decision(
                history_observation,
                mode=selected_mode,
                mapping_id=snapshot.mapping_id,
            )
        carry = None
        if selected_mode == "save_first":
            values = {
                check_id: decision.get("observed")
                for check_id, decision in decisions.items()
                if check_id in CARRIED_SAVE_CHECKS
                and decision.get("disposition") in SAVE_ACCEPTED_DISPOSITIONS
            }
            if values:
                carry = CarriedPlayerSaveEvidence(
                    context=context,
                    snapshot_fingerprint=str(
                        provenance["source_fingerprint"]
                    ),
                    effective_mapping_fingerprint=str(
                        snapshot.effective_mapping_fingerprint or ""
                    ),
                    values=values,
                )
                self._carry = carry
        return self._ready_result(
            "save_reconciled",
            selected_mode,
            decisions,
            provenance,
            operation_id,
            history_tail=history_tail,
            history_scope_id=context.activity_scope_id,
            carry=carry,
            acquisition=acquisition,
            context=context,
        )

    def stage_direct_retry(
        self,
        acquisition: Optional[PlayerSaveAcquisitionBundle],
        requirements: Mapping[str, Any],
        *,
        source_activity_scope_id: str,
        mode: Any = DEFAULT_PLAYER_SAVE_PREFLIGHT_MODE,
    ) -> PlayerSavePreflightResult:
        """Stage accepted terminal-save facts for one verified direct Retry.

        The Retry tap and successor activity scope must already exist.  Failure
        here never owns or reverses that lifecycle action; it simply leaves the
        existing per-check UI validation path in place.
        """

        selected_mode = normalize_player_save_preflight_mode(mode)
        requested = requested_player_save_check_ids(requirements)
        self.discard_carry("superseded_by_direct_retry_boundary")
        self._decisions = {}
        self._ui_verified_checks = {}
        self._snapshot_invalidation_reason = ""
        self._reset_mapping_candidate_window()
        context: Optional[PlayerSavePreflightContext]
        try:
            context = self._context_fn()
        except Exception:
            context = None

        reason = "direct_retry_save_unavailable"
        decisions: dict[str, dict[str, Any]]
        provenance: dict[str, Any] = {
            "transition": "game_over_direct_retry",
            "snapshot_trust": {
                "status": "not_acquired",
                "reason": reason,
            },
        }
        if context is not None:
            provenance["context"] = context.redacted()
        if selected_mode != "save_first":
            reason = f"{selected_mode}_policy"
            decisions = _all_ui_decisions(requested, reason)
        elif context is None:
            reason = "direct_retry_context_unavailable"
            decisions = _all_ui_decisions(requested, reason)
        elif not isinstance(acquisition, PlayerSaveAcquisitionBundle):
            decisions = _all_ui_decisions(requested, reason)
        else:
            try:
                plan = reconcile_direct_retry_requirements(
                    acquisition,
                    requirements,
                    runtime_session_id=context.runtime_session_id,
                    source_activity_scope_id=source_activity_scope_id,
                    successor_activity_scope_id=context.activity_scope_id,
                    expected_binding=PlayerSaveTargetBinding(
                        context.target,
                        context.target_generation,
                    ),
                )
            except (TypeError, ValueError):
                reason = "direct_retry_binding_unverified"
                decisions = _all_ui_decisions(requested, reason)
            else:
                reason = "direct_retry_save_reconciled"
                decisions = {
                    str(key): dict(value)
                    for key, value in (plan.get("checks") or {}).items()
                }
                provenance.update(
                    acquisition=acquisition.redacted_provenance(),
                    mapping_id=acquisition.snapshot.mapping_id,
                    source_fingerprint=_redacted(
                        f"snapshot\0{acquisition.snapshot.source_sha256}"
                    ),
                    effective_mapping_fingerprint=(
                        acquisition.snapshot.effective_mapping_fingerprint
                    ),
                    snapshot_trust=dict(plan.get("snapshot_trust") or {}),
                    authority=str(plan.get("authority") or ""),
                )

        self._decisions = {
            check_id: dict(decision)
            for check_id, decision in decisions.items()
        }
        carry = None
        if (
            reason == "direct_retry_save_reconciled"
            and context is not None
            and isinstance(acquisition, PlayerSaveAcquisitionBundle)
        ):
            values = {
                check_id: decision.get("observed")
                for check_id, decision in decisions.items()
                if check_id in CARRIED_SAVE_CHECKS
                and decision.get("disposition") in SAVE_ACCEPTED_DISPOSITIONS
            }
            if values:
                carry = CarriedPlayerSaveEvidence(
                    context=context,
                    snapshot_fingerprint=str(provenance["source_fingerprint"]),
                    effective_mapping_fingerprint=str(
                        acquisition.snapshot.effective_mapping_fingerprint
                        or ""
                    ),
                    values=values,
                    state=CarriedEvidenceState.LAUNCH_DISPATCHED,
                    launch_kind="game_over_direct_retry",
                    source_activity_scope_id=str(source_activity_scope_id),
                )
                self._carry = carry
        if reason != "direct_retry_save_reconciled":
            provenance["snapshot_trust"] = {
                "status": "not_authoritative",
                "reason": reason,
            }
        result = PlayerSavePreflightResult(
            PlayerSavePreflightStatus.READY,
            reason,
            selected_mode,
            decisions,
            provenance,
            True,
            _history_ui_decision("terminal_history_handoff_is_independent"),
            carry=carry,
            acquisition=(
                acquisition
                if isinstance(acquisition, PlayerSaveAcquisitionBundle)
                else None
            ),
            context=context,
        )
        log(
            "[PLAYER_SAVE_PREFLIGHT] Direct-Retry evidence staging "
            f"result={'accepted' if carry is not None else 'ui_fallback'} "
            f"reason={reason} accepted={list(result.accepted_checks)} "
            f"ui_fallback={list(result.ui_required_checks)}",
            "INFO",
        )
        return result

    def discard_carry(self, reason: str) -> None:
        """Discard transition applicability without distrusting the snapshot."""

        carry = self._carry
        if carry is None:
            return
        carry.invalidate(str(reason or "carry_continuity_invalidated"))
        self._carry = None

    def suspend_carry(self, reason: str) -> None:
        """Require fresh evidence after Pause without quarantining the snapshot."""

        if self._carry is not None:
            self._carry.suspend(str(reason or "carry_revalidation_required"))

    def fallback_checks(
        self,
        reason: str,
        *,
        check_ids: tuple[str, ...],
    ) -> None:
        """Downgrade changed checks to their UI path and preserve the rest."""

        normalized_reason = str(reason or "check_requires_ui_fallback")
        normalized_ids = tuple(sorted({str(value) for value in check_ids}))
        if self._carry is not None:
            self._carry.reject_checks(normalized_ids, normalized_reason)
        for check_id in normalized_ids:
            decision = self._decisions.get(check_id)
            if decision is None:
                continue
            decision.update(
                disposition="ui_required",
                reason=normalized_reason,
                save_evidence_authoritative=False,
                ui_required=True,
                ui_requirement_kind="fallback",
                repair_queued=False,
            )
        log(
            "[PLAYER_SAVE_PREFLIGHT] Checks routed to UI without snapshot "
            f"invalidation: reason={normalized_reason} checks={list(normalized_ids)} "
            "remaining_accepted_carry="
            f"{self._available_carry_checks()}",
            "INFO",
        )

    def _available_carry_checks(self) -> list[str]:
        carry = self._carry
        if carry is None:
            return []
        return sorted(set(carry.values) - carry.consumed)

    def invalidate(
        self,
        reason: str,
        *,
        check_ids: tuple[str, ...] = (),
    ) -> None:
        normalized_reason = str(reason or "continuity_invalidated")
        first_invalidation = not self._snapshot_invalidation_reason
        if first_invalidation:
            self._snapshot_invalidation_reason = normalized_reason
            self.close_mapping_candidate_window(normalized_reason)
        carry = self._carry
        if carry is not None:
            carry.invalidate(normalized_reason)
        if first_invalidation:
            log(
                "[PLAYER_SAVE_PREFLIGHT] Snapshot authority invalidated: "
                f"reason={normalized_reason} "
                f"checks={sorted({str(value) for value in check_ids})} "
                "remaining_accepted_carry=[]",
                "WARN" if normalized_reason == "save_ui_contradiction" else "INFO",
                console=True,
            )

    def record_ui_verification(
        self,
        check_id: str,
        *,
        changed: bool,
    ) -> bool:
        """Record normalized UI proof without promoting it into save carry.

        A first inspection that finds a trusted saved mismatch already matching
        is contradictory.  A later retry may find an unchanged value only when
        this coordinator already recorded its own verified repair.
        """

        normalized = str(check_id)
        decision = self._decisions.get(normalized, {})
        trusted_mismatch = (
            decision.get("disposition") == SAVE_MISMATCH_DISPOSITION
        )
        prior_repair = self._ui_verified_checks.get(normalized) == (
            "ui_verified_repair"
        )
        if (
            trusted_mismatch
            and not changed
            and not prior_repair
            and not self._snapshot_invalidation_reason
        ):
            self.invalidate(
                "save_ui_contradiction",
                check_ids=(normalized,),
            )
            log(
                "[PLAYER_SAVE_PREFLIGHT] UI contradicted the trusted saved "
                f"mismatch: check={normalized} disposition=contradiction",
                "ERROR",
                console=True,
            )
            return False

        status = "ui_verified_repair" if changed else "ui_verified"
        if prior_repair and not changed:
            status = "ui_verified_after_repair"
        if changed or normalized not in self._ui_verified_checks:
            self._ui_verified_checks[normalized] = status
        if changed:
            self.close_mapping_candidate_window(f"ui_repair:{normalized}")
        log(
            "[PLAYER_SAVE_PREFLIGHT] Current UI evidence recorded: "
            f"check={normalized} disposition={status} "
            f"save_disposition={decision.get('disposition') or 'none'} "
            "carry_promoted=False remaining_accepted_carry="
            f"{self._available_carry_checks()}",
            "INFO",
        )
        return True

    def _reset_mapping_candidate_window(self) -> None:
        self._mapping_candidate_context = None
        self._mapping_candidate_snapshot = None
        self._mapping_candidate_records = []
        self._mapping_candidate_record_ids = set()
        self._mapping_candidate_observation_keys = set()
        self._mapping_candidate_observation_count = 0

    def close_mapping_candidate_window(self, reason: str) -> None:
        """Prevent a pre-action save from pairing with later UI evidence."""

        if self._mapping_candidate_context is None:
            return
        self._mapping_candidate_context = None
        self._mapping_candidate_snapshot = None
        log(
            "[PLAYER_SAVE_MAPPING] Candidate correlation window closed: "
            f"reason={str(reason or 'ui_mutation')}",
            "DEBUG",
        )

    def record_mapping_observation(
        self,
        check_id: str,
        ui_evidence: Mapping[str, Any],
    ) -> int:
        """Persist same-boundary discoveries and accept narrow safe values."""

        normalized = str(check_id)
        context = self._mapping_candidate_context
        snapshot = self._mapping_candidate_snapshot
        if (
            context is None
            or snapshot is None
            or self._snapshot_invalidation_reason
            or not self._same_context(context)
        ):
            self.close_mapping_candidate_window("context_changed")
            return 0
        decision = self._decisions.get(normalized, {})
        diagnostics = decision.get("diagnostics")
        pending = (
            diagnostics.get("mapping_candidates")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if (
            decision.get("snapshot_trusted") is not True
            or decision.get("ui_required") is not True
            or not isinstance(pending, list)
            or not pending
            or not isinstance(ui_evidence, Mapping)
            or ui_evidence.get("pre_mutation") is not True
        ):
            return 0
        try:
            resolved = resolve_mapping_candidates(
                normalized,
                pending,
                ui_evidence,
            )
        except PlayerSaveMappingCandidateError:
            log(
                "[PLAYER_SAVE_MAPPING] Candidate pairing rejected: "
                f"check={normalized} reason=unsafe_or_malformed_evidence",
                "DEBUG",
            )
            return 0

        self._mapping_candidate_observation_count += 1
        workflow = _mapping_candidate_workflow_provenance(
            context,
            snapshot,
            check_id=normalized,
            observation_number=self._mapping_candidate_observation_count,
        )
        mapping = {
            "mapping_id": snapshot.mapping_id,
            "data_version": snapshot.data_version,
            "game_version": snapshot.game_version,
            "root_class": snapshot.root_class,
            "resolution": snapshot.mapping_resolution,
            "authority_mapping_id": snapshot.mapping_authority_id,
            "structural_mapping_id": snapshot.mapping_structural_id,
            "canonical_dependency_fingerprint": (
                snapshot.mapping_semantic_fingerprint
            ),
        }
        snapshot_fingerprint = _full_fingerprint(
            "mapping-candidate-snapshot",
            snapshot.source_sha256,
            snapshot.mapping_semantic_fingerprint,
        )
        ui_fingerprint = fingerprint_json(dict(ui_evidence))
        source_fingerprint = ui_evidence.get(
            "source_observation_fingerprint"
        )
        recorded = 0
        for candidate in resolved:
            observation_key = fingerprint_json(
                {
                    "check_id": normalized,
                    "source_observation_fingerprint": source_fingerprint,
                    "candidate": candidate,
                }
            )
            if observation_key in self._mapping_candidate_observation_keys:
                continue
            try:
                record = build_mapping_candidate_record(
                    mapping=mapping,
                    check_id=normalized,
                    candidate=candidate,
                    snapshot_fingerprint=snapshot_fingerprint,
                    ui_evidence_fingerprint=ui_fingerprint,
                    source_observation_fingerprint=source_fingerprint,
                    workflow_provenance=workflow,
                    observed_at=ui_evidence.get("observed_at"),
                )
            except PlayerSaveMappingCandidateError:
                continue
            record_id = str(record["record_id"])
            if record_id in self._mapping_candidate_record_ids:
                continue
            try:
                appended = self._mapping_candidate_store.append_once(record)
            except Exception:
                log(
                    "[PLAYER_SAVE_MAPPING] Candidate receipt write failed: "
                    f"check={normalized} reason=append_failed; UI fallback "
                    "and action authority are unchanged",
                    "WARN",
                    console=True,
                )
                continue
            self._mapping_candidate_record_ids.add(record_id)
            self._mapping_candidate_observation_keys.add(observation_key)
            self._mapping_candidate_records.append(record)
            if appended:
                recorded += 1
            payload = record["candidate"]
            log(
                "[PLAYER_SAVE_MAPPING] Candidate observation recorded: "
                f"mapping_id={record['mapping']['mapping_id']} "
                f"check={normalized} value_kind={payload['value_kind']} "
                f"semantic={payload['semantic_value']!r} "
                f"status={payload['status']} disposition=candidate_only",
                "INFO",
                console=True,
            )
            if not (
                payload["status"] == "ready_for_review"
                and payload["check_id"] == "modules"
                and payload["value_kind"] == "module_info_index"
                and snapshot.mapping_semantic_fingerprint is not None
            ):
                continue
            try:
                durable_record = self._mapping_candidate_store.get(record_id)
                accepted = self._confirmed_local_mapping_store.accept_candidate(
                    durable_record
                )
            except (
                ConfirmedLocalMappingError,
                PlayerSaveMappingCandidateError,
                OSError,
            ) as exc:
                log(
                    "[PLAYER_SAVE_MAPPING] Local confirmation write failed: "
                    f"check={normalized} reason={exc}; the durable candidate "
                    "receipt remains pending and UI fallback is unchanged",
                    "WARN",
                    console=True,
                )
                continue
            log(
                "[PLAYER_SAVE_MAPPING] Exact-version local confirmation "
                f"{'accepted' if accepted['changed'] else 'already active'}: "
                f"event_id={accepted['event_id']} generation="
                f"{accepted['generation']}; a later fresh save decode may "
                "use the identity for diagnostics, slot-scoped UI fallback "
                "is unchanged, and canonical integration remains pending",
                "WARN",
                console=True,
            )
        return recorded

    def mark_runtime_launch(
        self,
        *,
        control: HomeBattleControl,
        action_authorized: bool,
        dispatched: bool,
    ) -> bool:
        carry = self._carry
        if carry is None:
            return False
        try:
            accepted = carry.mark_runtime_launch(
                self._context_fn(),
                control=control,
                action_authorized=action_authorized,
                dispatched=dispatched,
            )
        except Exception:
            if dispatched:
                carry.invalidate("launch_context_unavailable_after_dispatch")
            accepted = False
        log(
            "[PLAYER_SAVE_PREFLIGHT] Carried launch binding "
            f"result={'accepted' if accepted else 'pending_or_rejected'} "
            f"dispatched={bool(dispatched)} state={carry.state.value} "
            f"reason={carry.invalidation_reason or 'none'}",
            "INFO",
        )
        return accepted

    def bind_running(
        self,
        *,
        battle_started: bool,
        stable_running: bool,
        continuity_verified: bool,
    ) -> bool:
        carry = self._carry
        if carry is None:
            return False
        try:
            accepted = carry.bind_running(
                self._context_fn(),
                battle_started=battle_started,
                stable_running=stable_running,
                continuity_verified=continuity_verified,
            )
        except Exception:
            accepted = False
        log(
            "[PLAYER_SAVE_PREFLIGHT] First RUNNING carry binding "
            f"result={'accepted' if accepted else 'pending_or_rejected'} "
            f"battle_started={bool(battle_started)} "
            f"stable_running={bool(stable_running)} "
            f"continuity_verified={bool(continuity_verified)} "
            f"state={carry.state.value} "
            f"reason={carry.invalidation_reason or 'none'}",
            "INFO",
        )
        return accepted

    def consume(self, check_id: str) -> Any:
        carry = self._carry
        if carry is None:
            return None
        try:
            value = carry.consume(check_id, self._context_fn())
        except Exception:
            return None
        if value is not None:
            log(
                "[PLAYER_SAVE_PREFLIGHT] Consumed exact carried evidence "
                f"for {check_id}",
                "INFO",
            )
        return value

    def _same_context(self, expected: PlayerSavePreflightContext) -> bool:
        try:
            return expected.matches(self._context_fn())
        except Exception:
            return False

    def _action_allowed(self) -> bool:
        try:
            return self._action_guard_fn() is True
        except Exception:
            return False

    def _verify_home(self, initial_frame: Any, *, stable: bool) -> bool:
        attempts = 2 if stable else 1
        frame = initial_frame
        for attempt in range(attempts):
            if frame is None or attempt > 0:
                frame = self._capture_fn()
            if frame is None:
                return False
            try:
                detection = self._detector(frame)
                control = self._home_control_fn(frame).control
            except Exception:
                return False
            if (
                str(detection.get("state") or "").upper()
                not in {"HOME", "HOME_SCREEN"}
                or control is not HomeBattleControl.NEW_BATTLE
            ):
                return False
            if stable and attempt == 0:
                self._sleep_fn(0.2)
        return True

    def _blocked_result(
        self,
        requested: set[str],
        mode: str,
        provenance: Mapping[str, Any],
        reason: str,
        operation_id: str,
    ) -> PlayerSavePreflightResult:
        self.invalidate(reason)
        blocked_provenance = dict(provenance)
        blocked_provenance["snapshot_trust"] = {
            "status": "invalidated",
            "reason": reason,
        }
        decisions = _all_ui_decisions(requested, reason)
        self._decisions = {
            check_id: dict(decision)
            for check_id, decision in decisions.items()
        }
        result = PlayerSavePreflightResult(
            PlayerSavePreflightStatus.BLOCKED,
            reason,
            mode,
            decisions,
            blocked_provenance,
            False,
            _history_ui_decision(reason, safe_ui_fallback=False),
        )
        log_result(
            "Save-first Home preflight blocked — no UI or battle input is "
            "authorized",
            detail=f"[PLAYER_SAVE_PREFLIGHT] result=blocked reason={reason}",
            operation_id=operation_id,
        )
        return result

    @staticmethod
    def _ready_result(
        reason: str,
        mode: str,
        decisions: Mapping[str, Mapping[str, Any]],
        provenance: Mapping[str, Any],
        operation_id: str,
        *,
        history_tail: Optional[Mapping[str, Any]] = None,
        history_scope_id: Optional[str] = None,
        carry: Optional[CarriedPlayerSaveEvidence] = None,
        acquisition: Optional[PlayerSaveAcquisitionBundle] = None,
        context: Optional[PlayerSavePreflightContext] = None,
    ) -> PlayerSavePreflightResult:
        accepted = sorted(
            check_id
            for check_id, decision in decisions.items()
            if decision.get("disposition") in SAVE_ACCEPTED_DISPOSITIONS
        )
        trusted_mismatches = sorted(
            check_id
            for check_id, decision in decisions.items()
            if decision.get("disposition") == SAVE_MISMATCH_DISPOSITION
        )
        fallback = sorted(
            check_id
            for check_id, decision in decisions.items()
            if decision.get("disposition") == "ui_required"
        )
        result = PlayerSavePreflightResult(
            PlayerSavePreflightStatus.READY,
            reason,
            mode,
            decisions,
            dict(provenance),
            True,
            dict(history_tail or _history_ui_decision(reason)),
            history_scope_id=history_scope_id,
            carry=carry,
            acquisition=acquisition,
            context=context,
        )
        for check_id, decision in sorted(decisions.items()):
            log(
                "[PLAYER_SAVE_PREFLIGHT] "
                f"check={check_id} mapping={decision.get('mapping_id') or 'none'} "
                "complete="
                f"{bool(decision.get('save_evidence_complete'))} "
                "supported="
                f"{bool(decision.get('save_requirement_supported'))} "
                f"disposition={decision.get('disposition') or 'ui_required'} "
                f"reason={decision.get('reason') or 'unspecified'}",
                "INFO",
            )
        log(
            "[PLAYER_SAVE_PREFLIGHT] check=battle_history_tail "
            f"mapping={(result.history_tail.get('mapping_id') or 'none')} "
            "complete="
            f"{bool(result.history_tail.get('complete'))} "
            "supported="
            f"{bool(result.history_tail.get('supported'))} "
            "disposition="
            f"{result.history_tail.get('disposition') or 'ui_required'} "
            f"reason={result.history_tail.get('reason') or 'unspecified'}",
            "INFO",
        )
        log_result(
            "Save-first Home preflight complete — configuration evidence "
            "reconciled",
            detail=(
                f"[PLAYER_SAVE_PREFLIGHT] result=ready reason={reason} "
                f"mode={mode} "
                "snapshot_trust="
                f"{dict(provenance.get('snapshot_trust') or {})} "
                f"accepted={accepted} "
                f"trusted_mismatches={trusted_mismatches} "
                f"ui_fallback={fallback}"
            ),
            operation_id=operation_id,
        )
        return result


def requested_player_save_check_ids(
    requirements: Mapping[str, Any],
) -> set[str]:
    """Return normalized configuration checks owned by save/UI reconciliation."""

    values: Mapping[str, Any] = requirements
    for key in ("invariants", "settings"):
        nested = requirements.get(key)
        if isinstance(nested, Mapping):
            values = nested
            break
    result = {str(value) for value in values}
    result.difference_update(
        {"loadout_policies", "profile_skips", "_gate_waivers"}
    )
    ultimate = values.get("ultimate_weapons")
    if isinstance(ultimate, Mapping):
        result.discard("ultimate_weapons")
        if any(
            isinstance(value, Mapping) and "primary" in value
            for value in ultimate.values()
        ):
            result.add("ultimate_weapon_primaries")
        poison = ultimate.get("Poison Swamp")
        if isinstance(poison, Mapping) and "stun" in poison:
            result.add("poison_swamp_stun")
        spotlight = ultimate.get("Spotlight")
        if isinstance(spotlight, Mapping) and "missiles" in spotlight:
            result.add("spotlight_missiles")
    return result


def _all_ui_decisions(
    check_ids: set[str],
    reason: str,
) -> dict[str, dict[str, Any]]:
    return {
        check_id: {
            "mapping_id": None,
            "disposition": "ui_required",
            "reason": reason,
            "snapshot_trusted": False,
            "save_evidence_authoritative": False,
            "save_evidence_status": "unmapped",
            "matches": None,
            "observed": None,
            "save_evidence_complete": False,
            "save_check_validated": False,
            "save_requirement_supported": False,
            "diagnostics": {},
            "ui_required": True,
            "ui_requirement_kind": "fallback",
            "repair_queued": False,
            "fallback": "existing_ui_check",
        }
        for check_id in sorted(check_ids)
    }


def _history_save_decision(
    observation: Any,
    *,
    mode: str,
    mapping_id: Optional[str],
) -> dict[str, Any]:
    complete = bool(observation.complete and observation.metadata is not None)
    if not complete:
        return _history_ui_decision(
            observation.reason or "history_tail_unavailable",
            mapping_id=mapping_id,
            complete=False,
            supported=False,
            safe_ui_fallback=bool(observation.safe_ui_fallback),
        )
    if mode == "comparison_audit":
        disposition = "ui_required"
        reason = "comparison_audit_requires_ui"
    else:
        disposition = "save_match"
        reason = "structural_history_tail_observed"
    return {
        "mapping_id": mapping_id,
        "complete": True,
        "supported": True,
        "disposition": disposition,
        "reason": reason,
        "metadata": dict(observation.metadata),
        "safe_ui_fallback": True,
        "fallback": "existing_battle_history_ui",
    }


def _history_ui_decision(
    reason: str,
    *,
    mapping_id: Optional[str] = None,
    complete: bool = False,
    supported: bool = False,
    safe_ui_fallback: bool = True,
) -> dict[str, Any]:
    return {
        "mapping_id": mapping_id,
        "complete": complete,
        "supported": supported,
        "disposition": "ui_required",
        "reason": str(reason or "history_tail_unavailable"),
        "metadata": None,
        "safe_ui_fallback": safe_ui_fallback,
        "fallback": "existing_battle_history_ui",
    }


def _mapping_candidate_workflow_provenance(
    context: PlayerSavePreflightContext,
    snapshot: PlayerSaveSnapshot,
    *,
    check_id: str,
    observation_number: int,
) -> dict[str, Any]:
    capture_identity = _full_fingerprint(
        "home-save-capture",
        context.runtime_session_id,
        context.preflight_session_id,
        snapshot.source_sha256,
    )
    return {
        "capture_request_id": f"capture-{capture_identity[:32]}",
        "inspection_request_id": (
            f"inspection-{str(check_id)[:64]}-{observation_number}"
        ),
        "runtime_session_fingerprint": _full_fingerprint(
            "runtime-session", context.runtime_session_id
        ),
        "pid": max(1, os.getpid()),
        "target_generation_fingerprint": _full_fingerprint(
            "target-generation",
            context.target,
            context.target_generation,
        ),
        "activity_scope_fingerprint": _full_fingerprint(
            "activity-scope", context.activity_scope_id
        ),
        "game_state": "home_new_battle",
        "active_round_identity_fingerprint": None,
        "boundary_fingerprint": _full_fingerprint(
            "home-new-battle-boundary",
            context.runtime_session_id,
            context.preflight_session_id,
            context.activity_scope_id,
            context.strategy_name,
            context.configuration_fingerprint,
            context.target,
            context.target_generation,
            snapshot.source_sha256,
        ),
    }


def _full_fingerprint(label: str, *values: Any) -> str:
    rendered = "\0".join([str(label), *(str(value) for value in values)])
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _redacted(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _capture_default():
    from core.ss_capture import capture_adb_screenshot

    return capture_adb_screenshot()


def _background_default(target: str) -> Any:
    return background_to_android_home(target)


def _foreground_default(target: str) -> Any:
    return restore_tower_launcher(target)


__all__ = [
    "CARRIED_SAVE_CHECKS",
    "DEFAULT_PLAYER_SAVE_PREFLIGHT_MODE",
    "HOME_SAVE_CHECKS",
    "PLAYER_SAVE_PREFLIGHT_MODES",
    "CarriedEvidenceState",
    "CarriedPlayerSaveEvidence",
    "PlayerSavePreflightContext",
    "PlayerSavePreflightCoordinator",
    "PlayerSavePreflightResult",
    "PlayerSavePreflightStatus",
    "normalize_player_save_preflight_mode",
    "requested_player_save_check_ids",
]
