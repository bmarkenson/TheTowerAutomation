"""Guarded save-first evidence at the ordinary Home ``NEW_BATTLE`` boundary.

The save can suppress redundant observation only.  It never authorizes input,
repair, lifecycle progression, attachment, or strategy dispatch.  All device
operations remain bound to the process-owned exact ADB target and every public
record is deliberately free of raw save data and private target identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import time
from typing import Any, Callable, Mapping, Optional

from core.adb_target_session import AdbTargetSnapshot
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.player_save import (
    PlayerSaveSnapshot,
    SAVE_ACCEPTED_DISPOSITIONS,
    SAVE_MISMATCH_DISPOSITION,
    decode_player_save_bytes,
    pull_player_save_bytes,
    reconcile_acquired_requirements,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    StablePlayerSaveAcquirer,
)
from core.player_save_history import history_metadata_from_acquisition
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
        return self == other

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
    values: dict[str, Any]
    state: CarriedEvidenceState = CarriedEvidenceState.PENDING_LAUNCH
    consumed: set[str] = field(default_factory=set)
    invalidation_reason: str = ""

    def mark_runtime_launch(
        self,
        context: PlayerSavePreflightContext,
        *,
        control: HomeBattleControl,
        action_authorized: bool,
        dispatched: bool,
    ) -> bool:
        if (
            self.state is not CarriedEvidenceState.PENDING_LAUNCH
            or not self.context.matches(context)
            or control is not HomeBattleControl.NEW_BATTLE
            or not action_authorized
            or not dispatched
        ):
            self.invalidate("runtime_owned_new_battle_launch_unproven")
            return False
        self.state = CarriedEvidenceState.LAUNCH_DISPATCHED
        return True

    def bind_running(
        self,
        context: PlayerSavePreflightContext,
        *,
        battle_started: bool,
        stable_running: bool,
        action_authorized: bool,
    ) -> bool:
        if (
            self.state is not CarriedEvidenceState.LAUNCH_DISPATCHED
            or not self.context.matches(context)
            or not battle_started
            or not stable_running
            or not action_authorized
        ):
            self.invalidate("first_running_boundary_continuity_failed")
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

    def invalidate(self, reason: str) -> None:
        if self.state in {
            CarriedEvidenceState.INVALIDATED,
            CarriedEvidenceState.CONSUMED,
        }:
            return
        self.values.clear()
        self.state = CarriedEvidenceState.INVALIDATED
        self.invalidation_reason = str(reason or "continuity_invalidated")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "state": self.state.value,
            "provenance": self.context.redacted(),
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "available_checks": sorted(self.values),
            "consumed_checks": sorted(self.consumed),
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
    """Acquire, reconcile, and bind one authoritative Home snapshot."""

    def __init__(
        self,
        *,
        target_snapshot_fn: Callable[[], AdbTargetSnapshot],
        context_fn: Callable[[], PlayerSavePreflightContext],
        action_guard_fn: Callable[[], bool],
        capture_fn: Optional[Callable[[], Any]] = None,
        detector: Callable[[Any], Mapping[str, Any]] = (
            detect_state_and_overlays
        ),
        home_control_fn: Callable[[Any], Any] = detect_home_battle_control,
        background_fn: Optional[Callable[[str], bool]] = None,
        foreground_fn: Optional[Callable[[str], bool]] = None,
        pull_fn: Callable[..., bytes] = pull_player_save_bytes,
        decode_fn: Callable[..., PlayerSaveSnapshot] = decode_player_save_bytes,
        acquirer: Optional[StablePlayerSaveAcquirer] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._target_snapshot_fn = target_snapshot_fn
        self._context_fn = context_fn
        self._action_guard_fn = action_guard_fn
        self._capture_fn = capture_fn or _capture_default
        self._detector = detector
        self._home_control_fn = home_control_fn
        self._background_fn = background_fn or background_to_android_home
        self._foreground_fn = foreground_fn or restore_tower_launcher
        self._pull_fn = pull_fn
        self._decode_fn = decode_fn
        self._acquirer = acquirer or StablePlayerSaveAcquirer(
            target_snapshot_fn=target_snapshot_fn,
            pull_fn=pull_fn,
            decode_fn=decode_fn,
        )
        self._sleep_fn = sleep_fn
        self._carry: Optional[CarriedPlayerSaveEvidence] = None
        self._decisions: dict[str, dict[str, Any]] = {}
        self._ui_verified_checks: dict[str, str] = {}
        self._snapshot_invalidation_reason = ""

    @property
    def carry(self) -> Optional[CarriedPlayerSaveEvidence]:
        return self._carry

    @property
    def snapshot_invalidated(self) -> bool:
        return bool(self._snapshot_invalidation_reason)

    @property
    def ui_verified_checks(self) -> Mapping[str, str]:
        return dict(self._ui_verified_checks)

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
            target_snapshot_fn=self._target_snapshot_fn,
            context_guard_fn=lambda: self._same_context(context),
            action_guard_fn=self._action_allowed,
            source_guard_fn=lambda frame, stable: self._verify_home(
                frame,
                stable=stable,
            ),
            background_fn=self._background_fn,
            foreground_fn=self._foreground_fn,
            pull_fn=self._pull_fn,
            decode_fn=self._decode_fn,
            acquirer=self._acquirer,
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
        if trusted_mismatch and not changed and not prior_repair:
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
        log(
            "[PLAYER_SAVE_PREFLIGHT] Current UI evidence recorded: "
            f"check={normalized} disposition={status} "
            f"save_disposition={decision.get('disposition') or 'none'} "
            "carry_promoted=False remaining_accepted_carry="
            f"{sorted(self._carry.values) if self._carry is not None else []}",
            "INFO",
        )
        return True

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
            carry.invalidate("launch_context_unavailable")
            accepted = False
        log(
            "[PLAYER_SAVE_PREFLIGHT] Carried launch binding "
            f"result={'accepted' if accepted else 'rejected'}",
            "INFO" if accepted else "WARN",
        )
        return accepted

    def bind_running(
        self,
        *,
        battle_started: bool,
        stable_running: bool,
        action_authorized: bool,
    ) -> bool:
        carry = self._carry
        if carry is None:
            return False
        try:
            accepted = carry.bind_running(
                self._context_fn(),
                battle_started=battle_started,
                stable_running=stable_running,
                action_authorized=action_authorized,
            )
        except Exception:
            carry.invalidate("running_context_unavailable")
            accepted = False
        log(
            "[PLAYER_SAVE_PREFLIGHT] First RUNNING carry binding "
            f"result={'accepted' if accepted else 'rejected'} "
            f"battle_started={bool(battle_started)} "
            f"stable_running={bool(stable_running)} "
            f"action_authorized={bool(action_authorized)} "
            f"state={carry.state.value} "
            f"reason={carry.invalidation_reason or 'none'}",
            "INFO" if accepted else "WARN",
        )
        return accepted

    def consume(self, check_id: str) -> Any:
        carry = self._carry
        if carry is None:
            return None
        try:
            value = carry.consume(check_id, self._context_fn())
        except Exception:
            carry.invalidate("consumption_context_unavailable")
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


def _redacted(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _capture_default():
    from core.ss_capture import capture_adb_screenshot

    return capture_adb_screenshot()


def _background_default(target: str) -> bool:
    return background_to_android_home(target)


def _foreground_default(target: str) -> bool:
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
