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

from core.adb_target_session import (
    ADB_TARGET_OPERATION_LOCK,
    AdbTargetSnapshot,
)
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.player_save import (
    PlayerSaveSnapshot,
    decode_player_save_bytes,
    pull_player_save_bytes,
    reconcile_requirements,
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
    carry: Optional[CarriedPlayerSaveEvidence] = field(
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
                if decision.get("disposition") == "save_match"
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
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._target_snapshot_fn = target_snapshot_fn
        self._context_fn = context_fn
        self._action_guard_fn = action_guard_fn
        self._capture_fn = capture_fn or _capture_default
        self._detector = detector
        self._home_control_fn = home_control_fn
        self._background_fn = background_fn or _background_default
        self._foreground_fn = foreground_fn or _foreground_default
        self._pull_fn = pull_fn
        self._decode_fn = decode_fn
        self._sleep_fn = sleep_fn
        self._carry: Optional[CarriedPlayerSaveEvidence] = None

    @property
    def carry(self) -> Optional[CarriedPlayerSaveEvidence]:
        return self._carry

    def acquire(
        self,
        requirements: Mapping[str, Any],
        *,
        mode: Any = DEFAULT_PLAYER_SAVE_PREFLIGHT_MODE,
        initial_frame: Any = None,
    ) -> PlayerSavePreflightResult:
        selected_mode = normalize_player_save_preflight_mode(mode)
        requested = _requested_check_ids(requirements)
        if self._carry is not None:
            self._carry.invalidate("superseded_by_new_home_preflight")
            self._carry = None
        provenance: dict[str, Any] = {
            "context": {"status": "not_acquired"},
            "serialization": "not_attempted",
            "freshness": "unverified",
        }
        if selected_mode == "force_ui":
            decisions = _all_ui_decisions(requested, "force_ui_policy")
            return PlayerSavePreflightResult(
                PlayerSavePreflightStatus.READY,
                "force_ui_policy",
                selected_mode,
                decisions,
                provenance,
                True,
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

        with ADB_TARGET_OPERATION_LOCK:
            try:
                target_before = self._target_snapshot_fn()
            except Exception:
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "exact_target_ownership_unverified",
                    operation_id,
                )
            if not _target_matches_context(target_before, context):
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "exact_target_ownership_unverified",
                    operation_id,
                )
            if not self._same_context(context) or not self._verify_home(
                initial_frame,
                stable=False,
            ):
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "initial_new_battle_boundary_unverified",
                    operation_id,
                )
            if not self._action_allowed():
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "control_authority_interrupted_before_background",
                    operation_id,
                )

            log_input(
                "Backgrounding The Tower to Android Home",
                detail=(
                    "[PLAYER_SAVE_PREFLIGHT] input=KEYCODE_HOME "
                    f"target_generation={context.redacted()['target_generation']}"
                ),
            )
            try:
                backgrounded = bool(self._background_fn(context.target))
            except Exception:
                backgrounded = False
            log(
                "[PLAYER_SAVE_PREFLIGHT] Android Home dispatch "
                f"result={'accepted' if backgrounded else 'failed'}",
                "DEBUG",
            )
            if not backgrounded:
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "background_serialization_boundary_failed",
                    operation_id,
                )
            provenance["serialization"] = "background_dispatched"
            self._sleep_fn(0.25)

            snapshot: Optional[PlayerSaveSnapshot] = None
            acquisition_reason = "save_acquired"
            try:
                pull_kwargs: dict[str, Any] = {"device_id": context.target}
                if self._pull_fn is pull_player_save_bytes:
                    pull_kwargs["read_fn"] = _quiet_player_save_read
                payload = self._pull_fn(**pull_kwargs)
                snapshot = self._decode_fn(payload, source_name="playerInfo.dat")
                del payload
            except Exception:
                acquisition_reason = "save_acquisition_failed"

            if not self._action_allowed():
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "control_authority_interrupted_before_foreground",
                    operation_id,
                )
            log_input(
                "Restoring The Tower from Android Home",
                detail=(
                    "[PLAYER_SAVE_PREFLIGHT] input=launcher_restore "
                    f"target_generation={context.redacted()['target_generation']}"
                ),
            )
            try:
                foregrounded = bool(self._foreground_fn(context.target))
            except Exception:
                foregrounded = False
            log(
                "[PLAYER_SAVE_PREFLIGHT] launcher restore "
                f"result={'accepted' if foregrounded else 'failed'}",
                "DEBUG",
            )
            if not foregrounded:
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "foreground_restoration_failed",
                    operation_id,
                )
            self._sleep_fn(0.5)

            try:
                target_after = self._target_snapshot_fn()
            except Exception:
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "restored_target_or_new_battle_boundary_unverified",
                    operation_id,
                )
            boundary_restored = bool(
                _same_target_snapshot(target_before, target_after)
                and _target_matches_context(target_after, context)
                and self._same_context(context)
                and self._action_allowed()
                and self._verify_home(None, stable=True)
            )
            if not boundary_restored:
                return self._blocked_result(
                    requested,
                    selected_mode,
                    provenance,
                    "restored_target_or_new_battle_boundary_unverified",
                    operation_id,
                )

        provenance["serialization"] = "verified_android_home_boundary"
        provenance["freshness"] = "verified"
        if snapshot is None:
            decisions = _all_ui_decisions(requested, acquisition_reason)
            return self._ready_result(
                acquisition_reason,
                selected_mode,
                decisions,
                provenance,
                operation_id,
            )

        provenance["source_fingerprint"] = _redacted(
            f"snapshot\0{snapshot.source_sha256}"
        )
        provenance["mapping_id"] = snapshot.mapping_id
        provenance["save_version"] = {
            "data": snapshot.data_version,
            "game": snapshot.game_version,
        }
        plan = reconcile_requirements(
            snapshot,
            requirements,
            freshness_verified=True,
            force_ui_audit=selected_mode == "comparison_audit",
        )
        decisions = {
            str(key): dict(value)
            for key, value in (plan.get("checks") or {}).items()
        }
        carry = None
        if selected_mode == "save_first":
            values = {
                check_id: decision.get("observed")
                for check_id, decision in decisions.items()
                if check_id in CARRIED_SAVE_CHECKS
                and decision.get("disposition") == "save_match"
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
            carry=carry,
        )

    def invalidate(self, reason: str) -> None:
        carry = self._carry
        if carry is None:
            return
        carry.invalidate(reason)
        log(
            "[PLAYER_SAVE_PREFLIGHT] Carried evidence invalidated: "
            f"reason={carry.invalidation_reason}",
            "INFO",
            console=True,
        )

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
            f"result={'accepted' if accepted else 'rejected'}",
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
        result = PlayerSavePreflightResult(
            PlayerSavePreflightStatus.BLOCKED,
            reason,
            mode,
            _all_ui_decisions(requested, reason),
            dict(provenance),
            False,
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
        carry: Optional[CarriedPlayerSaveEvidence] = None,
    ) -> PlayerSavePreflightResult:
        accepted = sorted(
            check_id
            for check_id, decision in decisions.items()
            if decision.get("disposition") == "save_match"
        )
        fallback = sorted(
            check_id
            for check_id, decision in decisions.items()
            if decision.get("ui_required") is True
        )
        result = PlayerSavePreflightResult(
            PlayerSavePreflightStatus.READY,
            reason,
            mode,
            decisions,
            dict(provenance),
            True,
            carry=carry,
        )
        log_result(
            "Save-first Home preflight complete — configuration evidence "
            "reconciled",
            detail=(
                f"[PLAYER_SAVE_PREFLIGHT] result=ready reason={reason} "
                f"mode={mode} accepted={accepted} ui_fallback={fallback}"
            ),
            operation_id=operation_id,
        )
        return result


def _requested_check_ids(requirements: Mapping[str, Any]) -> set[str]:
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
            "disposition": "ui_required",
            "reason": reason,
            "matches": None,
            "observed": None,
            "save_evidence_complete": False,
            "save_check_validated": False,
            "save_requirement_supported": False,
            "ui_required": True,
            "fallback": "existing_ui_check",
        }
        for check_id in sorted(check_ids)
    }


def _target_matches_context(
    target: AdbTargetSnapshot,
    context: PlayerSavePreflightContext,
) -> bool:
    return bool(
        target.owned
        and target.target == context.target
        and target.generation == context.target_generation
    )


def _same_target_snapshot(
    before: AdbTargetSnapshot,
    after: AdbTargetSnapshot,
) -> bool:
    return bool(
        before.owned
        and after.owned
        and before.target == after.target
        and before.generation == after.generation
    )


def _redacted(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _capture_default():
    from core.ss_capture import capture_adb_screenshot

    return capture_adb_screenshot()


def _background_default(target: str) -> bool:
    from core.adb_utils import adb_shell

    return adb_shell(
        ["input", "keyevent", "KEYCODE_HOME"],
        device_id=target,
        report_errors=False,
    ) is not None


def _foreground_default(target: str) -> bool:
    from core.adb_utils import adb_shell

    return adb_shell(
        [
            "monkey",
            "-p",
            "com.TechTreeGames.TheTower",
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        device_id=target,
        report_errors=False,
    ) is not None


def _quiet_player_save_read(
    path: str,
    *,
    device_id: Optional[str] = None,
) -> Optional[bytes]:
    """Read the private save without printing target-bearing exceptions."""

    from core.adb_utils import read_device_file

    return read_device_file(
        path,
        device_id=device_id,
        report_errors=False,
    )


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
]
