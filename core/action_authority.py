"""Typed runtime authority for observation, auxiliary, strategy, and lifecycle work.

The authority in this module deliberately does not dispatch input.  It answers
who may dispatch input at the current runtime boundary and publishes that
decision through a runtime-owned, structured status file.  Observation is a
separate class because capture and interpretation must never depend on an
input-action guard.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Iterable, Mapping, Optional
from uuid import uuid4

from utils.logger import log


STRATEGY_ACTION_GATE_SCHEMA_VERSION = 1
STRATEGY_ACTION_GATE_STALE_AFTER_SECONDS = 30


class RuntimeActionClass(str, Enum):
    """The four authority classes exposed by the runtime contract."""

    OBSERVATION = "observation"
    AUXILIARY_COLLECTION = "auxiliary_collection"
    STRATEGY_ACTION = "strategy_action"
    LIFECYCLE_ACTION = "lifecycle_action"


class AuxiliaryCollector(str, Enum):
    """Independently reviewed collectors with typed runtime authority."""

    HOME_AD_GEM = "home_ad_gem"
    IN_BATTLE_AD_GEM = "in_battle_ad_gem"
    FLOATING_GEM_SCAN = "floating_gem_scan"
    DAILY_GEM_STORE = "daily_gem_store"
    DAILY_MISSION_REWARDS = "daily_mission_rewards"
    WEEKLY_MISSION_REWARDS = "weekly_mission_rewards"
    EVENT_MISSION_REWARDS = "event_mission_rewards"
    GUILD_CHEST_REWARDS = "guild_chest_rewards"


STRATEGY_GATE_AUXILIARY_ALLOWLIST = (
    AuxiliaryCollector.IN_BATTLE_AD_GEM,
    AuxiliaryCollector.FLOATING_GEM_SCAN,
    AuxiliaryCollector.DAILY_GEM_STORE,
    AuxiliaryCollector.DAILY_MISSION_REWARDS,
    AuxiliaryCollector.WEEKLY_MISSION_REWARDS,
    AuxiliaryCollector.EVENT_MISSION_REWARDS,
    AuxiliaryCollector.GUILD_CHEST_REWARDS,
)


class AuthorityHold(str, Enum):
    """Exclusive owners whose routes are stronger than the Strategy Gate."""

    BATTLE_IDENTITY = "battle_identity"
    ACTIVITY_CONTINUITY = "activity_continuity"
    RUN_INITIALIZATION = "run_initialization"
    SESSION_PREFLIGHT = "session_preflight"
    EXCLUSIVE_VALIDATION = "exclusive_validation"
    EXCLUSIVE_OWNERSHIP = "exclusive_ownership"
    EXTERNAL_DEVELOPMENT = "external_development"
    OPERATOR_WORKFLOW = "operator_workflow"
    MANUAL_CONTROL_RETURN = "manual_control_return"
    SETUP_CAPTURE = "setup_capture"
    EMULATOR_MAINTENANCE = "emulator_maintenance"
    BLOCKING_MODAL_RECOVERY = "blocking_modal_recovery"


class StrategyGateExitEvent(str, Enum):
    """Authoritative events that may end a running-battle Strategy Gate."""

    SUCCESSFUL_VALIDATION = "successful_validation"
    ACCEPTED_RETRY = "accepted_retry"
    RUN_SCOPED_WAIVER = "run_scoped_waiver"
    ACTIVE_STRATEGY_CHANGE = "active_strategy_change"
    AUTHORIZED_REPAIR_TRANSITION = "authorized_repair_transition"
    NATURAL_BATTLE_BOUNDARY = "natural_battle_boundary"
    BATTLE_IDENTITY_CHANGE = "battle_identity_change"


@dataclass(frozen=True)
class AuthorityHoldState:
    hold: AuthorityHold
    reason: str
    allowed_auxiliary_collectors: tuple[AuxiliaryCollector, ...] = ()


@dataclass(frozen=True)
class ActionAuthorityDecision:
    """One immutable answer from the central authority model."""

    action_class: RuntimeActionClass
    allowed: bool
    reason: str
    collector: Optional[AuxiliaryCollector] = None
    owner: Optional[str] = None

    def as_dict(self) -> dict[str, object]:
        return {
            "action_class": self.action_class.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "collector": self.collector.value if self.collector else None,
            "owner": self.owner,
        }


@dataclass(frozen=True)
class StrategyActionGateState:
    """Run-scoped state for one enforced running-battle mismatch."""

    gate_id: str
    strategy: str
    battle_scope: Optional[str]
    source: str
    phase: str
    failed_check_ids: tuple[str, ...]
    reason: str
    activated_at: str
    updated_at: str


@dataclass(frozen=True)
class AuxiliaryRouteLease:
    """Exclusive ownership token for one guarded multi-screen collector route."""

    route_id: str
    collectors: tuple[AuxiliaryCollector, ...]
    battle_scope: Optional[str]
    source_state: str
    gate_id: Optional[str]
    claimed_at: str


@dataclass(frozen=True)
class AuxiliaryRouteState:
    lease: AuxiliaryRouteLease
    expected_state: str
    cleanup_pending: bool
    suspended_reason: Optional[str] = None


@dataclass(frozen=True)
class RuntimeAuthorityContext:
    """Fresh non-gate inputs to the authority matrix."""

    global_pause: bool = False
    active_battle: bool = False
    battle_scope: Optional[str] = None
    primary_state: str = "UNKNOWN"
    holds: tuple[AuthorityHoldState, ...] = ()
    runtime_stopped: bool = False
    shutting_down: bool = False


@dataclass(frozen=True)
class RuntimeActionAuthoritySnapshot:
    """Immutable operator/test snapshot of the complete current authority."""

    active: bool
    gate_id: Optional[str]
    strategy: Optional[str]
    battle_scope: Optional[str]
    source: Optional[str]
    phase: Optional[str]
    failed_check_ids: tuple[str, ...]
    reason: str
    activated_at: Optional[str]
    updated_at: str
    global_pause: bool
    runtime_stopped: bool
    active_battle: bool
    runtime_battle_scope: Optional[str]
    primary_state: str
    holds: tuple[AuthorityHoldState, ...]
    observation_authority: ActionAuthorityDecision
    auxiliary_collection_authority: ActionAuthorityDecision
    allowed_auxiliary_collectors: tuple[AuxiliaryCollector, ...]
    strategy_action_authority: ActionAuthorityDecision
    lifecycle_action_authority: ActionAuthorityDecision
    auxiliary_route: Optional[AuxiliaryRouteState]

    def as_dict(self) -> dict[str, object]:
        route = None
        if self.auxiliary_route is not None:
            lease = self.auxiliary_route.lease
            route = {
                "route_id": lease.route_id,
                "collectors": [value.value for value in lease.collectors],
                "battle_scope": lease.battle_scope,
                "source_state": lease.source_state,
                "gate_id": lease.gate_id,
                "claimed_at": lease.claimed_at,
                "expected_state": self.auxiliary_route.expected_state,
                "cleanup_pending": self.auxiliary_route.cleanup_pending,
                "suspended_reason": self.auxiliary_route.suspended_reason,
            }
        return {
            "active": self.active,
            "gate_id": self.gate_id,
            "strategy": self.strategy,
            "battle_scope": self.battle_scope,
            "source": self.source,
            "phase": self.phase,
            "failed_check_ids": list(self.failed_check_ids),
            "reason": self.reason,
            "activated_at": self.activated_at,
            "updated_at": self.updated_at,
            "global_pause": self.global_pause,
            "runtime_stopped": self.runtime_stopped,
            "active_battle": self.active_battle,
            "runtime_battle_scope": self.runtime_battle_scope,
            "primary_state": self.primary_state,
            "holds": [
                {
                    "hold": item.hold.value,
                    "reason": item.reason,
                    **(
                        {
                            "allowed_auxiliary_collectors": [
                                collector.value
                                for collector in item.allowed_auxiliary_collectors
                            ]
                        }
                        if item.allowed_auxiliary_collectors
                        else {}
                    ),
                }
                for item in self.holds
            ],
            "observation_authority": self.observation_authority.as_dict(),
            "auxiliary_collection_authority": (
                self.auxiliary_collection_authority.as_dict()
            ),
            "allowed_auxiliary_collectors": [
                value.value for value in self.allowed_auxiliary_collectors
            ],
            "strategy_action_authority": (
                self.strategy_action_authority.as_dict()
            ),
            "lifecycle_action_authority": (
                self.lifecycle_action_authority.as_dict()
            ),
            "auxiliary_route": route,
        }


def _timestamp(now: Optional[float] = None) -> str:
    value = time.time() if now is None else float(now)
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().isoformat(
        timespec="microseconds"
    )


def _collector(value: AuxiliaryCollector | str) -> AuxiliaryCollector:
    if isinstance(value, AuxiliaryCollector):
        return value
    return AuxiliaryCollector(str(value).strip().lower())


def _gate_exit_event(
    value: StrategyGateExitEvent | str,
) -> StrategyGateExitEvent:
    if isinstance(value, StrategyGateExitEvent):
        return value
    return StrategyGateExitEvent(str(value).strip().lower())


class RuntimeActionAuthority:
    """Central mutable owner of immutable action-authority decisions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._context = RuntimeAuthorityContext()
        self._gate: Optional[StrategyActionGateState] = None
        self._route: Optional[AuxiliaryRouteState] = None

    @property
    def strategy_gate(self) -> Optional[StrategyActionGateState]:
        with self._lock:
            return self._gate

    @property
    def auxiliary_route(self) -> Optional[AuxiliaryRouteState]:
        with self._lock:
            return self._route

    def update_context(
        self,
        *,
        global_pause: bool,
        active_battle: bool,
        battle_scope: Optional[str],
        primary_state: str,
        holds: Iterable[AuthorityHoldState] = (),
        runtime_stopped: bool = False,
        shutting_down: bool = False,
    ) -> None:
        normalized_scope = str(battle_scope or "").strip() or None
        normalized_holds = tuple(holds)
        with self._lock:
            self._context = RuntimeAuthorityContext(
                global_pause=bool(global_pause),
                active_battle=bool(active_battle),
                battle_scope=normalized_scope,
                primary_state=str(primary_state or "UNKNOWN").upper(),
                holds=normalized_holds,
                runtime_stopped=bool(runtime_stopped),
                shutting_down=bool(shutting_down),
            )

    def activate_strategy_gate(
        self,
        *,
        strategy: str,
        battle_scope: Optional[str],
        source: str,
        phase: str,
        failed_check_ids: Iterable[str],
        reason: str,
        now: Optional[float] = None,
    ) -> StrategyActionGateState:
        """Enter or update one run-scoped gate without changing Pause state."""

        timestamp = _timestamp(now)
        normalized_strategy = str(strategy or "none").strip().lower()
        normalized_scope = str(battle_scope or "").strip() or None
        normalized_source = str(source or "runtime").strip().lower()
        normalized_phase = str(phase or "running_battle").strip().lower()
        normalized_checks = tuple(
            dict.fromkeys(
                str(check).strip()
                for check in failed_check_ids
                if str(check).strip()
            )
        )
        normalized_reason = str(reason or "strategy validation failed").strip()
        with self._lock:
            previous = self._gate
            same_gate = bool(
                previous
                and previous.strategy == normalized_strategy
                and previous.battle_scope == normalized_scope
                and previous.source == normalized_source
                and previous.phase == normalized_phase
            )
            if same_gate and previous is not None:
                if (
                    previous.failed_check_ids == normalized_checks
                    and previous.reason == normalized_reason
                ):
                    return previous
                updated = replace(
                    previous,
                    failed_check_ids=normalized_checks,
                    reason=normalized_reason,
                    updated_at=timestamp,
                )
                self._gate = updated
                log(
                    "[STRATEGY_GATE] Updated running-battle gate "
                    f"{updated.gate_id}: checks="
                    f"{','.join(updated.failed_check_ids) or 'unknown'}; "
                    f"reason={updated.reason}",
                    "INFO",
                    console=True,
                )
                return updated

            if previous is not None:
                log(
                    "[STRATEGY_GATE] Exited running-battle gate "
                    f"{previous.gate_id}: superseded by a new scoped gate",
                    "INFO",
                    console=True,
                )
            gate = StrategyActionGateState(
                gate_id=uuid4().hex,
                strategy=normalized_strategy,
                battle_scope=normalized_scope,
                source=normalized_source,
                phase=normalized_phase,
                failed_check_ids=normalized_checks,
                reason=normalized_reason,
                activated_at=timestamp,
                updated_at=timestamp,
            )
            self._gate = gate
            log(
                "[STRATEGY_GATE] Entered running-battle gate "
                f"{gate.gate_id}: strategy={gate.strategy} "
                f"scope={gate.battle_scope or 'unavailable'} "
                f"checks={','.join(gate.failed_check_ids) or 'unknown'}; "
                f"{gate.reason}",
                "WARN",
                console=True,
            )
            return gate

    def clear_strategy_gate(
        self,
        *,
        reason: str,
        event: StrategyGateExitEvent | str,
    ) -> bool:
        """Clear a gate only at a caller-proven authoritative event."""

        exit_event = _gate_exit_event(event)
        with self._lock:
            gate = self._gate
            if gate is None:
                return False
            self._gate = None
            log(
                "[STRATEGY_GATE] Exited running-battle gate "
                f"{gate.gate_id}: event={exit_event.value}; "
                f"{str(reason).strip() or 'resolved'}",
                "INFO",
                console=True,
            )
            return True

    def scope_gate_if_missing(self, battle_scope: Optional[str]) -> bool:
        """Attach later authoritative run identity without replacing a gate."""

        normalized_scope = str(battle_scope or "").strip() or None
        if normalized_scope is None:
            return False
        with self._lock:
            if self._gate is None or self._gate.battle_scope is not None:
                return False
            self._gate = replace(
                self._gate,
                battle_scope=normalized_scope,
                updated_at=_timestamp(),
            )
            log(
                "[STRATEGY_GATE] Updated running-battle gate "
                f"{self._gate.gate_id}: authoritative battle scope="
                f"{normalized_scope}",
                "INFO",
                console=True,
            )
            return True

    def _matching_hold_owner(self, owner: Optional[str]) -> bool:
        holds = self._context.holds
        normalized_owner = str(owner or "").strip().lower()
        return bool(
            normalized_owner
            and holds
            and all(
                item.hold is not AuthorityHold.EXTERNAL_DEVELOPMENT
                for item in holds
            )
            and all(item.hold.value == normalized_owner for item in holds)
        )

    def _base_input_denial(
        self,
        action_class: RuntimeActionClass,
        *,
        owner: Optional[str],
    ) -> Optional[ActionAuthorityDecision]:
        unconditional = self._unconditional_input_denial(
            action_class,
            owner=owner,
        )
        if unconditional is not None:
            return unconditional
        context = self._context
        if context.holds and not self._matching_hold_owner(owner):
            descriptions = ", ".join(
                item.hold.value for item in context.holds
            )
            return ActionAuthorityDecision(
                action_class,
                False,
                f"exclusive runtime ownership is held by {descriptions}",
                owner=owner,
            )
        return None

    def _unconditional_input_denial(
        self,
        action_class: RuntimeActionClass,
        *,
        owner: Optional[str],
    ) -> Optional[ActionAuthorityDecision]:
        """Return the Pause/Stop/shutdown denial that no hold may bypass."""

        context = self._context
        if context.shutting_down:
            return ActionAuthorityDecision(
                action_class,
                False,
                "the runtime is shutting down",
                owner=owner,
            )
        if context.runtime_stopped:
            return ActionAuthorityDecision(
                action_class,
                False,
                "the STOPPED control state blocks every handler and input action",
                owner=owner,
            )
        if context.global_pause:
            return ActionAuthorityDecision(
                action_class,
                False,
                "global Pause blocks every handler and input action",
                owner=owner,
            )
        return None

    def _auxiliary_decision(
        self,
        collector: AuxiliaryCollector,
        *,
        owner: Optional[str],
        route_id: Optional[str],
        ignore_route: bool = False,
    ) -> ActionAuthorityDecision:
        denial = self._unconditional_input_denial(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            owner=owner,
        )
        if denial is not None:
            return replace(denial, collector=collector)
        context = self._context
        holds_allow_collector = bool(context.holds) and all(
            collector in hold.allowed_auxiliary_collectors
            for hold in context.holds
        )
        if context.holds and not holds_allow_collector:
            descriptions = ", ".join(
                item.hold.value for item in context.holds
            )
            return ActionAuthorityDecision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                False,
                f"exclusive runtime ownership is held by {descriptions}",
                collector=collector,
                owner=owner,
            )
        gate = self._gate
        route = self._route
        if gate is not None and not context.active_battle:
            return ActionAuthorityDecision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                False,
                "the running-battle precondition is no longer authoritative",
                collector=collector,
                owner=owner,
            )
        if (
            gate is not None
            and gate.battle_scope is not None
            and context.battle_scope is not None
            and gate.battle_scope != context.battle_scope
        ):
            return ActionAuthorityDecision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                False,
                "the observed battle identity no longer matches the gate scope",
                collector=collector,
                owner=owner,
            )
        if collector in {
            AuxiliaryCollector.IN_BATTLE_AD_GEM,
            AuxiliaryCollector.FLOATING_GEM_SCAN,
        } and context.primary_state != "RUNNING":
            return ActionAuthorityDecision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                False,
                "the collector requires a freshly observed RUNNING battle frame",
                collector=collector,
                owner=owner,
            )
        if (
            collector is AuxiliaryCollector.HOME_AD_GEM
            and context.primary_state not in {"HOME", "HOME_SCREEN"}
        ):
            return ActionAuthorityDecision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                False,
                "the collector requires a freshly observed Home frame",
                collector=collector,
                owner=owner,
            )
        if route is not None and not ignore_route:
            lease = route.lease
            if route_id != lease.route_id or collector not in lease.collectors:
                return ActionAuthorityDecision(
                    RuntimeActionClass.AUXILIARY_COLLECTION,
                    False,
                    "another auxiliary collector owns the multi-screen route",
                    collector=collector,
                    owner=owner,
                )
            current_gate_id = gate.gate_id if gate is not None else None
            if lease.gate_id != current_gate_id:
                return ActionAuthorityDecision(
                    RuntimeActionClass.AUXILIARY_COLLECTION,
                    False,
                    "auxiliary authority changed after the route was claimed",
                    collector=collector,
                    owner=owner,
                )
            if (
                lease.battle_scope is not None
                and context.battle_scope is not None
                and lease.battle_scope != context.battle_scope
            ):
                return ActionAuthorityDecision(
                    RuntimeActionClass.AUXILIARY_COLLECTION,
                    False,
                    "the auxiliary route no longer matches the battle identity",
                    collector=collector,
                    owner=owner,
                )
        if gate is not None and collector not in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
            return ActionAuthorityDecision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                False,
                "the collector is not allowlisted by the active Strategy Gate",
                collector=collector,
                owner=owner,
            )
        return ActionAuthorityDecision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            True,
            (
                "explicitly allowlisted independent collection under the active "
                "Strategy Gate"
                if gate is not None
                else "normal runtime auxiliary authority"
            ),
            collector=collector,
            owner=owner,
        )

    def decision(
        self,
        action_class: RuntimeActionClass,
        *,
        collector: Optional[AuxiliaryCollector | str] = None,
        owner: Optional[str] = None,
        route_id: Optional[str] = None,
    ) -> ActionAuthorityDecision:
        """Return the current immutable authority answer for one action."""

        if not isinstance(action_class, RuntimeActionClass):
            action_class = RuntimeActionClass(str(action_class))
        with self._lock:
            if action_class is RuntimeActionClass.OBSERVATION:
                return ActionAuthorityDecision(
                    action_class,
                    True,
                    "capture, detection, interpretation, evidence, and status remain active",
                    owner=owner,
                )
            if action_class is RuntimeActionClass.AUXILIARY_COLLECTION:
                if collector is None:
                    return ActionAuthorityDecision(
                        action_class,
                        False,
                        "auxiliary authority requires an explicitly named collector",
                        owner=owner,
                    )
                return self._auxiliary_decision(
                    _collector(collector),
                    owner=owner,
                    route_id=route_id,
                )

            denial = self._base_input_denial(action_class, owner=owner)
            if denial is not None:
                return denial
            if self._matching_hold_owner(owner):
                return ActionAuthorityDecision(
                    action_class,
                    True,
                    f"exclusive {owner} ownership authorizes its bounded route",
                    owner=owner,
                )
            if self._route is not None:
                return ActionAuthorityDecision(
                    action_class,
                    False,
                    "an exclusive auxiliary route owns the screen",
                    owner=owner,
                )
            if self._gate is not None:
                return ActionAuthorityDecision(
                    action_class,
                    False,
                    (
                        "the active Strategy Gate blocks strategy-dependent work"
                        if action_class is RuntimeActionClass.STRATEGY_ACTION
                        else "the active Strategy Gate blocks battle-boundary transitions"
                    ),
                    owner=owner,
                )
            return ActionAuthorityDecision(
                action_class,
                True,
                "normal runtime authority",
                owner=owner,
            )

    def begin_auxiliary_route(
        self,
        collectors: Iterable[AuxiliaryCollector | str],
        *,
        battle_scope: Optional[str],
        source_state: str,
    ) -> Optional[AuxiliaryRouteLease]:
        """Claim one exclusive route before its first input."""

        normalized_collectors = tuple(
            dict.fromkeys(_collector(value) for value in collectors)
        )
        if not normalized_collectors:
            return None
        normalized_scope = str(battle_scope or "").strip() or None
        normalized_source = str(source_state or "UNKNOWN").upper()
        with self._lock:
            if self._route is not None:
                return None
            if not self._context.active_battle or normalized_source != "RUNNING":
                return None
            for collector in normalized_collectors:
                if not self._auxiliary_decision(
                    collector,
                    owner=None,
                    route_id=None,
                    ignore_route=True,
                ).allowed:
                    return None
            if (
                normalized_scope is not None
                and self._context.battle_scope is not None
                and normalized_scope != self._context.battle_scope
            ):
                return None
            lease = AuxiliaryRouteLease(
                route_id=uuid4().hex,
                collectors=normalized_collectors,
                battle_scope=normalized_scope,
                source_state=normalized_source,
                gate_id=self._gate.gate_id if self._gate is not None else None,
                claimed_at=_timestamp(),
            )
            self._route = AuxiliaryRouteState(
                lease=lease,
                expected_state=lease.source_state,
                cleanup_pending=False,
            )
            log(
                "[AUXILIARY_ROUTE] Claimed exclusive route "
                f"{lease.route_id}: collectors="
                f"{','.join(value.value for value in lease.collectors)} "
                f"scope={lease.battle_scope or 'unavailable'}",
                "DEBUG",
            )
            return lease

    def update_auxiliary_route(
        self,
        lease: AuxiliaryRouteLease,
        *,
        expected_state: str,
        cleanup_pending: bool,
        suspended_reason: Optional[str] = None,
    ) -> bool:
        with self._lock:
            if self._route is None or self._route.lease.route_id != lease.route_id:
                return False
            self._route = replace(
                self._route,
                expected_state=str(expected_state or "UNKNOWN").upper(),
                cleanup_pending=bool(cleanup_pending),
                suspended_reason=(
                    str(suspended_reason).strip() if suspended_reason else None
                ),
            )
            return True

    def resume_auxiliary_route(
        self,
        lease: AuxiliaryRouteLease,
    ) -> Optional[AuxiliaryRouteLease]:
        """Rebind suspended cleanup after fresh authority and scope checks."""

        with self._lock:
            route = self._route
            if route is None or route.lease.route_id != lease.route_id:
                return None
            if not self._context.active_battle:
                return None
            if (
                lease.battle_scope is not None
                and self._context.battle_scope is not None
                and lease.battle_scope != self._context.battle_scope
            ):
                return None
            for collector in lease.collectors:
                if not self._auxiliary_decision(
                    collector,
                    owner=None,
                    route_id=lease.route_id,
                    ignore_route=True,
                ).allowed:
                    return None
            rebound = replace(
                lease,
                gate_id=self._gate.gate_id if self._gate is not None else None,
            )
            self._route = replace(
                route,
                lease=rebound,
                suspended_reason=None,
            )
            return rebound

    def release_auxiliary_route(
        self,
        lease: AuxiliaryRouteLease,
        *,
        reason: str,
    ) -> bool:
        with self._lock:
            if self._route is None or self._route.lease.route_id != lease.route_id:
                return False
            self._route = None
            log(
                "[AUXILIARY_ROUTE] Released exclusive route "
                f"{lease.route_id}: {str(reason).strip() or 'completed'}",
                "DEBUG",
            )
            return True

    def abandon_auxiliary_route(self, *, reason: str) -> bool:
        """Drop route ownership without dispatching cleanup input."""

        with self._lock:
            if self._route is None:
                return False
            route_id = self._route.lease.route_id
            self._route = None
            log(
                "[AUXILIARY_ROUTE] Abandoned exclusive route "
                f"{route_id} without cleanup input: {reason}",
                "INFO",
            )
            return True

    def snapshot(self, *, now: Optional[float] = None) -> RuntimeActionAuthoritySnapshot:
        with self._lock:
            gate = self._gate
            observation = self.decision(RuntimeActionClass.OBSERVATION)
            strategy = self.decision(RuntimeActionClass.STRATEGY_ACTION)
            lifecycle = self.decision(RuntimeActionClass.LIFECYCLE_ACTION)
            allowed_collectors = tuple(
                collector
                for collector in AuxiliaryCollector
                if self.decision(
                    RuntimeActionClass.AUXILIARY_COLLECTION,
                    collector=collector,
                    route_id=(
                        self._route.lease.route_id
                        if self._route is not None
                        and collector in self._route.lease.collectors
                        else None
                    ),
                ).allowed
            )
            if allowed_collectors:
                auxiliary = ActionAuthorityDecision(
                    RuntimeActionClass.AUXILIARY_COLLECTION,
                    True,
                    "one or more explicitly allowlisted collectors currently have authority",
                )
            else:
                first = self.decision(
                    RuntimeActionClass.AUXILIARY_COLLECTION,
                    collector=AuxiliaryCollector.IN_BATTLE_AD_GEM,
                )
                auxiliary = ActionAuthorityDecision(
                    RuntimeActionClass.AUXILIARY_COLLECTION,
                    False,
                    first.reason,
                )
            return RuntimeActionAuthoritySnapshot(
                active=gate is not None,
                gate_id=gate.gate_id if gate else None,
                strategy=gate.strategy if gate else None,
                battle_scope=gate.battle_scope if gate else None,
                source=gate.source if gate else None,
                phase=gate.phase if gate else None,
                failed_check_ids=gate.failed_check_ids if gate else (),
                reason=gate.reason if gate else "no running-battle Strategy Gate is active",
                activated_at=gate.activated_at if gate else None,
                updated_at=_timestamp(now),
                global_pause=self._context.global_pause,
                runtime_stopped=self._context.runtime_stopped,
                active_battle=self._context.active_battle,
                runtime_battle_scope=self._context.battle_scope,
                primary_state=self._context.primary_state,
                holds=self._context.holds,
                observation_authority=observation,
                auxiliary_collection_authority=auxiliary,
                allowed_auxiliary_collectors=allowed_collectors,
                strategy_action_authority=strategy,
                lifecycle_action_authority=lifecycle,
                auxiliary_route=self._route,
            )


class RuntimeActionAuthorityPublisher:
    """Atomically publish one fresh runtime-owned authority snapshot."""

    def __init__(
        self,
        path: Path | str,
        *,
        owner: Mapping[str, object],
        stale_after_seconds: int = STRATEGY_ACTION_GATE_STALE_AFTER_SECONDS,
    ) -> None:
        self.path = Path(path)
        self.owner = self._normalize_owner(owner)
        self.stale_after_seconds = max(1, int(stale_after_seconds))
        self._last_error: Optional[str] = None

    @staticmethod
    def _normalize_owner(owner: Mapping[str, object]) -> dict[str, object]:
        normalized: dict[str, object] = {
            "runtime_id": str(owner.get("runtime_id") or ""),
            "pid": int(owner.get("pid") or os.getpid()),
            "adb_target": str(owner.get("adb_target") or "unknown"),
        }
        target_generation = owner.get("target_generation")
        if (
            isinstance(target_generation, int)
            and not isinstance(target_generation, bool)
            and target_generation >= 1
        ):
            normalized["target_generation"] = target_generation
        return normalized

    def publish(
        self,
        snapshot: RuntimeActionAuthoritySnapshot,
        *,
        runtime_active: bool = True,
        now: Optional[float] = None,
        owner: Optional[Mapping[str, object]] = None,
        interactive_development_lease: Optional[
            Mapping[str, object]
        ] = None,
        control_model: Optional[Mapping[str, object]] = None,
        acknowledgements: Optional[Mapping[str, object]] = None,
    ) -> bool:
        published_owner = dict(self.owner)
        if owner is not None:
            try:
                published_owner = self._normalize_owner(owner)
            except (TypeError, ValueError):
                published_owner = dict(self.owner)
        payload = {
            "schema_version": STRATEGY_ACTION_GATE_SCHEMA_VERSION,
            "owner": published_owner,
            "observed_at": _timestamp(now),
            "stale_after_seconds": self.stale_after_seconds,
            "runtime_active": bool(runtime_active),
            "interactive_development_lease": (
                dict(interactive_development_lease)
                if isinstance(interactive_development_lease, Mapping)
                else None
            ),
            "control_model": (
                dict(control_model)
                if isinstance(control_model, Mapping)
                else None
            ),
            "acknowledgements": (
                dict(acknowledgements)
                if isinstance(acknowledgements, Mapping)
                else None
            ),
            **snapshot.as_dict(),
        }
        temporary: Optional[str] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except (OSError, TypeError, ValueError) as exc:
            message = str(exc)
            if message != self._last_error:
                log(
                    f"[STRATEGY_GATE_STATUS] Could not publish {self.path}: {message}",
                    "WARN",
                )
            self._last_error = message
            return False
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        if self._last_error is not None:
            log(
                f"[STRATEGY_GATE_STATUS] Structured status publication recovered: {self.path}",
                "INFO",
            )
        self._last_error = None
        return True


__all__ = [
    "ActionAuthorityDecision",
    "AuthorityHold",
    "AuthorityHoldState",
    "AuxiliaryCollector",
    "AuxiliaryRouteLease",
    "AuxiliaryRouteState",
    "RuntimeActionAuthority",
    "RuntimeActionAuthorityPublisher",
    "RuntimeActionAuthoritySnapshot",
    "RuntimeActionClass",
    "STRATEGY_ACTION_GATE_SCHEMA_VERSION",
    "STRATEGY_ACTION_GATE_STALE_AFTER_SECONDS",
    "STRATEGY_GATE_AUXILIARY_ALLOWLIST",
    "StrategyGateExitEvent",
    "StrategyActionGateState",
]
