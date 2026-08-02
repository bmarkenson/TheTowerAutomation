from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from utils.logger import (
    get_activity_scope,
    log,
    log_mission,
    record_activity_scope_session_preflight,
    start_activity_scope,
)
from automation.missions.base import BaseMission, MissionContext
from automation.strategies.base import BaseStrategy
from core.battle_lifecycle import BattleLifecycle, HomeBattleControl
from core.action_executor import execute_actions


Detection = Dict[str, Any]


class MissionManager:
    def __init__(
        self,
        mission: Optional[BaseMission],
        strategy: Optional[BaseStrategy],
        *,
        defer_startup_gates_until_next_run: bool = False,
        validate_attached_battle: bool = False,
        skip_attached_checks: bool = False,
        action_guard_fn: Optional[Callable[[], bool]] = None,
    ):
        self.mission = mission
        self.strategy = strategy
        self.ctx = MissionContext()
        self._started = False
        self._last_state = None
        self._mission_was_complete = False
        self._startup_gates_deferred = bool(defer_startup_gates_until_next_run)
        self._validate_initial_attachment = bool(validate_attached_battle)
        self._skip_initial_attachment_checks = bool(skip_attached_checks)
        self._action_guard_fn = action_guard_fn
        self._new_battle_home_observed = False
        self._exclusive_validation_prepared_request_id: Optional[str] = None
        self._session_preflight_receipt_key: Optional[tuple[str, str]] = None
        self._battle_lifecycle = BattleLifecycle(
            adopt_initial_battle=self._startup_gates_deferred,
        )

    def start(self) -> None:
        if self._started:
            return
        if self.mission:
            self.mission.on_start(self.ctx)
            try:
                self._mission_was_complete = bool(self.mission.is_complete(self.ctx))
            except Exception:
                self._mission_was_complete = False
        if self.strategy:
            self.strategy.on_start(self.ctx)
        self.ctx.data["exclusive_validation_battle"] = False
        self.ctx.data.setdefault("mission_vars", {})[
            "exclusive_validation_battle"
        ] = False
        self.ctx.data["startup_gates_deferred"] = self._startup_gates_deferred
        self._clear_attached_check_state()
        if self._startup_gates_deferred:
            self._record_deferred_free_upgrade_lock_evidence()
        self._started = True

    def maybe_run_start(self, detection: Detection) -> bool:
        """Emit run-start hooks only when the battle lifecycle starts anew."""

        state = detection.get("state")
        control = detection.get("home_battle_control", "UNKNOWN")
        normalized_state = str(state or "UNKNOWN").upper()
        parsed_control = HomeBattleControl.parse(control)
        new_battle_home = bool(
            normalized_state in {"HOME", "HOME_SCREEN"}
            and parsed_control is HomeBattleControl.NEW_BATTLE
        )
        starting_preflight_scope = bool(
            new_battle_home and not self._new_battle_home_observed
        )
        if starting_preflight_scope:
            start_activity_scope(reason="new_battle_preflight")
        if self._startup_gates_deferred and (
            normalized_state in {"GAME_OVER", "TOURNAMENT_RESULTS"}
            or new_battle_home
        ):
            self._arm_startup_gates()
        if starting_preflight_scope:
            self._rearm_free_upgrade_lock_gate()
            self._new_battle_home_observed = True
        elif normalized_state == "RUNNING":
            self._new_battle_home_observed = False
        battle_started = self._battle_lifecycle.observe(
            state,
            home_control=control,
        )
        if self._battle_lifecycle.last_observation_adopted:
            if self._skip_initial_attachment_checks:
                self.ctx.data["skip_attached_checks"] = True
            if self._validate_initial_attachment:
                self.ctx.data["attached_validation_requested"] = True
                self.ctx.data.setdefault("mission_vars", {})[
                    "attached_validation_requested"
                ] = True
            log(
                "[RUN_INIT] Attached to existing battle; startup gates deferred "
                "until the next run boundary"
                + (
                    "; requested strategy validation is armed"
                    if self._validate_initial_attachment
                    else (
                        "; strategy setup checks are skipped for this battle"
                        if self._skip_initial_attachment_checks
                        else ""
                    )
                ),
                "INFO",
                console=True,
            )
        if battle_started:
            self._arm_startup_gates()
            self.set_exclusive_validation_battle(False)
            if self.mission:
                self.mission.on_run_start(self.ctx)
                try:
                    self._mission_was_complete = bool(self.mission.is_complete(self.ctx))
                except Exception:
                    self._mission_was_complete = False
            if self.strategy:
                self.strategy.on_run_start(self.ctx)
        self._last_state = state
        return battle_started

    def active_battle_observed(self) -> bool:
        """Return the lifecycle-owned same-battle precondition."""

        return bool(self._battle_lifecycle.active_battle_observed)

    def _arm_startup_gates(self) -> None:
        self._clear_attached_check_state()
        if not self._startup_gates_deferred:
            return
        self._startup_gates_deferred = False
        self.ctx.data["startup_gates_deferred"] = False
        log(
            "[RUN_INIT] Run boundary observed; startup gates armed for the next battle",
            "INFO",
            console=True,
        )

    def _clear_attached_check_state(self) -> None:
        """Clear process-start choices at a strategy or real run boundary."""

        self.ctx.data["attached_validation_requested"] = False
        self.ctx.data["skip_attached_checks"] = False
        self.ctx.data["attached_session_preflight_reused"] = False
        self._session_preflight_receipt_key = None
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["attached_validation_requested"] = False
        mv["gc_session_preflight_restart_available"] = False

    def _session_preflight_identity(self) -> Optional[tuple[str, str]]:
        if not self.strategy or not self.strategy.requires_session_preflight():
            return None
        fingerprint = str(
            self.strategy.session_preflight_fingerprint() or ""
        ).strip()
        strategy_name = str(self.strategy.name or "").strip()
        if not fingerprint or not strategy_name:
            return None
        return strategy_name, fingerprint

    def persist_session_preflight_completion(self) -> bool:
        """Persist a scope-bound receipt after the configured checks finish."""

        if self._session_preflight_receipt_key is not None:
            return False
        if (
            not self._battle_lifecycle.active_battle_observed
            or not self.strategy
            or not self.strategy.requires_session_preflight()
            or not self.strategy.is_session_preflight_complete(self.ctx)
        ):
            return False
        identity = self._session_preflight_identity()
        if identity is None:
            return False
        scope = get_activity_scope()
        if scope is None:
            return False
        run_id = str(scope.get("run_id") or "").strip()
        if not run_id:
            return False
        strategy_name, fingerprint = identity
        receipt_key = (run_id, fingerprint)
        if self._session_preflight_receipt_key == receipt_key:
            return False
        existing = scope.get("session_preflight")
        if (
            isinstance(existing, Mapping)
            and existing.get("schema_version") == 1
            and existing.get("status") == "completed"
            and str(existing.get("strategy") or "") == strategy_name
            and str(existing.get("configuration_fingerprint") or "")
            == fingerprint
        ):
            self._session_preflight_receipt_key = receipt_key
            return False
        updated = record_activity_scope_session_preflight(
            run_id=run_id,
            strategy=strategy_name,
            configuration_fingerprint=fingerprint,
        )
        if updated is None:
            return False
        self._session_preflight_receipt_key = receipt_key
        log(
            "[SESSION_PREFLIGHT] Completed configuration-check receipt saved "
            f"for current run scope={run_id}",
            "DEBUG",
        )
        return True

    def reuse_session_preflight_for_confirmed_attachment(
        self,
        run_id: str,
    ) -> bool:
        """Suppress repeated checks only for a proven same-battle receipt."""

        expected_run_id = str(run_id or "").strip()
        identity = self._session_preflight_identity()
        if (
            not expected_run_id
            or identity is None
            or not self._startup_gates_deferred
            or not self._battle_lifecycle.active_battle_observed
        ):
            return False
        scope = get_activity_scope()
        if (
            scope is None
            or str(scope.get("run_id") or "") != expected_run_id
        ):
            return False
        receipt = scope.get("session_preflight")
        if not isinstance(receipt, Mapping):
            return False
        strategy_name, fingerprint = identity
        if not (
            receipt.get("schema_version") == 1
            and receipt.get("status") == "completed"
            and str(receipt.get("strategy") or "") == strategy_name
            and str(receipt.get("configuration_fingerprint") or "")
            == fingerprint
        ):
            return False

        self.ctx.data["attached_session_preflight_reused"] = True
        self.ctx.data["attached_validation_requested"] = False
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["attached_validation_requested"] = False
        mv["gc_session_preflight_restart_available"] = False
        self._session_preflight_receipt_key = (expected_run_id, fingerprint)
        log(
            "[SESSION_PREFLIGHT] Reusing completed configuration checks for "
            "the continuity-confirmed attached battle",
            "INFO",
            console=True,
        )
        return True

    def _free_upgrade_lock_requirements(self) -> list[Any]:
        if not self.strategy:
            return []
        requirements = self.strategy.session_preflight_requirements()
        if not isinstance(requirements, Mapping):
            return []
        raw = requirements.get("free_upgrade_locks")
        return list(raw) if isinstance(raw, (list, tuple)) else []

    def _record_deferred_free_upgrade_lock_evidence(self) -> None:
        required = self._free_upgrade_lock_requirements()
        if not required:
            return
        deferred = {
            "status": "unavailable_deferred",
            "boundary": HomeBattleControl.NEW_BATTLE.value,
            "required": required,
            "checked": False,
            "valid": None,
            "blocking_valid": True,
            "reason": (
                "attached battle has no authoritative no-battle NEW_BATTLE "
                "lock evidence"
            ),
        }
        mv = self.ctx.data.setdefault("mission_vars", {})
        setup_evidence = dict(mv.get("gc_no_battle_setup_evidence") or {})
        setup_evidence["free_upgrade_locks"] = copy.deepcopy(deferred)
        mv["gc_no_battle_setup_evidence"] = setup_evidence
        session_evidence = dict(mv.get("gc_session_preflight_evidence") or {})
        session_evidence["free_upgrade_locks"] = copy.deepcopy(deferred)
        mv["gc_session_preflight_evidence"] = session_evidence

    def _rearm_free_upgrade_lock_gate(self) -> None:
        """Require fresh Home proof once per genuine NEW_BATTLE boundary."""

        if not self._free_upgrade_lock_requirements():
            return
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["gc_no_battle_setup_completed"] = False
        mv["gc_no_battle_setup_evidence"] = {}
        session_evidence = dict(mv.get("gc_session_preflight_evidence") or {})
        session_evidence.pop("free_upgrade_locks", None)
        mv["gc_session_preflight_evidence"] = session_evidence

    def handle_overlays(self, detection: Detection) -> None:
        if not self.mission:
            return
        for name in (detection.get("overlays") or []):
            try:
                self.mission.on_overlay(self.ctx, name)
            except Exception:
                log(f"[MISSION] overlay handler error for {name}", "ERROR")

    def on_state(self, detection: Detection) -> None:
        if self.mission:
            try:
                self.mission.on_state(self.ctx, detection)
            except Exception:
                log("[MISSION] on_state handler error", "ERROR")

    def on_home(self) -> None:
        if self.mission:
            try:
                self.mission.on_home(self.ctx)
            except Exception:
                log("[MISSION] on_home handler error", "ERROR")

    def on_game_over(self) -> None:
        if self.mission:
            try:
                self.mission.on_game_over(self.ctx)
            except Exception:
                log("[MISSION] on_game_over handler error", "ERROR")
        if self.strategy:
            try:
                self.strategy.on_game_over(self.ctx)
            except Exception:
                log("[STRATEGY] on_game_over handler error", "ERROR")
        mv = self.ctx.data.setdefault("mission_vars", {})
        waivers = mv.get("gc_session_preflight_waivers")
        if isinstance(waivers, Mapping) and waivers:
            # Session preflight is normally retained for the process, but a
            # waived result is valid for only the run the operator accepted.
            mv["gc_no_battle_setup_completed"] = False
            mv["gc_no_battle_setup_evidence"] = {}
            mv["gc_session_preflight_attempted"] = False
            mv["gc_session_preflight_completed"] = False
            mv["gc_session_preflight_blocked"] = False
            mv["gc_session_preflight_repair_required"] = False
            mv["gc_session_preflight_repair_in_progress"] = False
            mv["gc_session_preflight_last_status"] = ""
            mv["gc_session_preflight_last_reason"] = ""
            mv["gc_session_preflight_evidence"] = {}
            self._reset_session_preflight_repair_attempts()
        mv["gc_session_preflight_waivers"] = {}
        mv["gc_session_preflight_failed_checks"] = []
        self.set_exclusive_validation_battle(False)

    def replace_strategy_at_boundary(
        self,
        strategy: Optional[BaseStrategy],
    ) -> None:
        """Replace strategy-owned state after the prior run is finalized."""

        self._replace_strategy(strategy)
        self._startup_gates_deferred = False
        self._new_battle_home_observed = False
        self.ctx.data["startup_gates_deferred"] = False
        self._clear_attached_check_state()

    def adopt_strategy_for_active_battle(
        self,
        strategy: Optional[BaseStrategy],
    ) -> None:
        """Adopt reporting and normal rules without inventing a run boundary."""

        self._replace_strategy(strategy)
        self._clear_attached_check_state()
        self._startup_gates_deferred = True
        self._new_battle_home_observed = False
        self.ctx.data["startup_gates_deferred"] = True
        self._battle_lifecycle.active_battle_observed = True
        self._battle_lifecycle.adopt_initial_battle = False
        self._record_deferred_free_upgrade_lock_evidence()

    def _replace_strategy(self, strategy: Optional[BaseStrategy]) -> None:
        """Replace strategy-owned variables without choosing boundary policy."""

        old_vars = getattr(self.strategy, "vars", {}) if self.strategy else {}
        mission_vars = self.ctx.data.setdefault("mission_vars", {})
        if isinstance(old_vars, Mapping):
            for key in old_vars:
                mission_vars.pop(str(key), None)
        self.ctx.data["rule_last_fire"] = {}
        self._exclusive_validation_prepared_request_id = None
        self.strategy = strategy
        if self.strategy:
            self.strategy.on_start(self.ctx)

    def run_initialization_pending(self) -> bool:
        """Return whether the active battle still requires initialization."""

        if (
            self._startup_gates_deferred
            or not self._battle_lifecycle.active_battle_observed
            or not self.strategy
            or self.ctx.data.get("exclusive_validation_battle") is True
        ):
            return False
        if not self.strategy.requires_run_initialization():
            return False
        return not self.strategy.is_run_initialization_complete(self.ctx)

    def set_exclusive_validation_battle(self, active: bool) -> None:
        """Mark only the disposable owned battle as exempt from run setup."""

        value = bool(active)
        previous = self.ctx.data.get("exclusive_validation_battle") is True
        self.ctx.data["exclusive_validation_battle"] = value
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["exclusive_validation_battle"] = value
        if value and not previous:
            # Every durable request must collect fresh battle-only evidence.
            # Do not let a prior Tournament observation or one-run waiver turn
            # this disposable battle into a seeded success.
            mv["damage_slider_checked"] = False
            mv["damage_slider_observation"] = {}
            mv["gc_session_preflight_attempted"] = False
            mv["gc_session_preflight_completed"] = False
            mv["gc_session_preflight_blocked"] = False
            mv["gc_session_preflight_repair_required"] = False
            mv["gc_session_preflight_repair_in_progress"] = False
            mv["gc_session_preflight_last_status"] = ""
            mv["gc_session_preflight_last_reason"] = ""
            mv["gc_session_preflight_evidence"] = {}
            mv["gc_session_preflight_failed_checks"] = []
            mv["gc_session_preflight_waivers"] = {}
            self._reset_session_preflight_repair_attempts()

    def prepare_exclusive_validation_request(self, request_id: str) -> bool:
        """Re-arm Home evidence exactly once for one durable request."""

        normalized = str(request_id or "").strip()
        if (
            not normalized
            or normalized == self._exclusive_validation_prepared_request_id
        ):
            return False
        self._exclusive_validation_prepared_request_id = normalized
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["gc_no_battle_setup_completed"] = False
        mv["gc_no_battle_setup_evidence"] = {}
        mv["gc_session_preflight_waivers"] = {}
        self._reset_session_preflight_repair_attempts()
        return True

    def session_preflight_pending(self) -> bool:
        """Return whether the active battle is waiting on session validation."""

        if (
            not self._battle_lifecycle.active_battle_observed
            or not self.strategy
        ):
            return False
        if self._startup_gates_deferred:
            if (
                self.ctx.data.get("skip_attached_checks") is True
                or self.ctx.data.get("attached_session_preflight_reused")
                is True
            ):
                return False
            if self.ctx.data.get("attached_validation_requested") is True:
                pass
            else:
                try:
                    policy = self.strategy.runtime_policy()
                except Exception:
                    policy = {}
                if not (
                    isinstance(policy, Mapping)
                    and policy.get("session_preflight_on_attach") is True
                ):
                    return False
        if self.run_initialization_pending():
            return False
        if not self.strategy.requires_session_preflight():
            return False
        return not self.strategy.is_session_preflight_complete(self.ctx)

    def session_preflight_repair_required(self) -> bool:
        """Return whether preflight requested a guarded no-battle repair."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        return bool(mv.get("gc_session_preflight_repair_required"))

    def attached_validation_requested(self) -> bool:
        """Return whether this process adopted and is validating a live battle."""

        return bool(self.ctx.data.get("attached_validation_requested"))

    def session_preflight_restart_available(self) -> bool:
        """Return whether attached validation found a Home-repairable mismatch."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        return bool(
            self.attached_validation_requested()
            and mv.get("gc_session_preflight_restart_available")
        )

    def authorize_session_preflight_restart(self) -> bool:
        """Convert a confirmed attached mismatch into the guarded repair path."""

        if not self.session_preflight_restart_available():
            return False
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["gc_session_preflight_blocked"] = False
        mv["gc_session_preflight_repair_required"] = True
        mv["gc_session_preflight_repair_in_progress"] = False
        mv["gc_session_preflight_restart_available"] = False
        return True

    def session_preflight_repair_in_progress(self) -> bool:
        """Return whether this process surrendered a run for preflight repair."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        return bool(mv.get("gc_session_preflight_repair_in_progress"))

    def session_preflight_terminally_blocked(self) -> bool:
        """Return whether validation failed without an owned repair transition."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        return bool(
            mv.get("gc_session_preflight_blocked")
            and not mv.get("gc_session_preflight_repair_required")
            and not mv.get("gc_session_preflight_repair_in_progress")
        )

    def session_preflight_failure_checks(self) -> list[str]:
        """Return requirement ids from the last authoritative mismatch."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        raw = mv.get("gc_session_preflight_failed_checks") or []
        if not isinstance(raw, list):
            return []
        return [str(check).strip() for check in raw if str(check).strip()]

    def session_preflight_waivers(self) -> Dict[str, Any]:
        mv = self.ctx.data.setdefault("mission_vars", {})
        raw = mv.get("gc_session_preflight_waivers") or {}
        return dict(raw) if isinstance(raw, Mapping) else {}

    def waive_session_preflight_check(
        self,
        check_id: str,
        waiver: Mapping[str, Any],
    ) -> None:
        """Apply one explicit run-scoped waiver and re-arm validation."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        waivers = self.session_preflight_waivers()
        waivers[str(check_id).strip()] = dict(waiver)
        mv["gc_session_preflight_waivers"] = waivers
        mv["gc_session_preflight_attempted"] = False
        mv["gc_session_preflight_completed"] = False
        mv["gc_session_preflight_blocked"] = False
        mv["gc_session_preflight_repair_required"] = False
        mv["gc_session_preflight_repair_in_progress"] = False
        mv["gc_session_preflight_restart_available"] = False
        mv["gc_session_preflight_failed_checks"] = []
        self._reset_session_preflight_repair_attempts()

    def retry_session_preflight(self) -> None:
        """Re-arm an unchanged session preflight after operator direction."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["gc_session_preflight_attempted"] = False
        mv["gc_session_preflight_completed"] = False
        mv["gc_session_preflight_blocked"] = False
        mv["gc_session_preflight_repair_required"] = False
        mv["gc_session_preflight_repair_in_progress"] = False
        mv["gc_session_preflight_restart_available"] = False
        mv["gc_session_preflight_failed_checks"] = []
        self._reset_session_preflight_repair_attempts()

    def _reset_session_preflight_repair_attempts(self) -> None:
        """Discard consecutive mismatch evidence after a policy boundary."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["gc_session_preflight_repair_attempts"] = 0
        mv["gc_session_preflight_repair_failure_key"] = ""

    def begin_session_preflight_repair(self) -> bool:
        """Claim the one guarded surrender transition for a repair request."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        if not mv.get("gc_session_preflight_repair_required") or mv.get(
            "gc_session_preflight_repair_in_progress"
        ):
            return False
        mv["gc_session_preflight_repair_in_progress"] = True
        return True

    def fail_session_preflight_repair(self, reason: str) -> None:
        """Fail closed after a surrender transition cannot be completed."""

        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["gc_session_preflight_repair_required"] = False
        mv["gc_session_preflight_repair_in_progress"] = False
        mv["gc_session_preflight_restart_available"] = False
        mv["gc_session_preflight_blocked"] = True
        mv["gc_session_preflight_last_reason"] = str(reason)

    def no_battle_setup_requirements(self) -> Dict[str, Any]:
        """Return profile settings still needing a verified no-battle pass."""

        if not self.strategy:
            return {}
        if self._startup_gates_deferred:
            return {}
        mv = self.ctx.data.setdefault("mission_vars", {})
        if mv.get("gc_no_battle_setup_completed"):
            return {}
        return dict(self.strategy.session_preflight_requirements())

    def gate_fallbacks(self, check_id: str) -> list[Dict[str, Any]]:
        """Return profile-declared choices for one failed gate."""

        if not self.strategy:
            return []
        configured = self.strategy.session_preflight_gate_fallbacks()
        if not isinstance(configured, Mapping):
            return []
        raw = configured.get(str(check_id or "").strip())
        if not isinstance(raw, list):
            return []
        return [dict(option) for option in raw if isinstance(option, Mapping)]

    def mark_no_battle_setup_complete(
        self,
        evidence: Mapping[str, Any],
        *,
        waivers: Optional[Mapping[str, Any]] = None,
    ) -> None:
        mv = self.ctx.data.setdefault("mission_vars", {})
        repairing = bool(
            mv.get("gc_session_preflight_repair_required")
            or mv.get("gc_session_preflight_repair_in_progress")
        )
        mv["gc_no_battle_setup_completed"] = True
        mv["gc_no_battle_setup_evidence"] = copy.deepcopy(dict(evidence))
        lock_evidence = evidence.get("free_upgrade_locks")
        if isinstance(lock_evidence, Mapping):
            session_evidence = dict(
                mv.get("gc_session_preflight_evidence") or {}
            )
            session_evidence["free_upgrade_locks"] = copy.deepcopy(
                dict(lock_evidence)
            )
            mv["gc_session_preflight_evidence"] = session_evidence
        target_priority_evidence = evidence.get("target_priority")
        if isinstance(target_priority_evidence, Mapping):
            target_priority_mode = str(
                target_priority_evidence.get("mode") or ""
            ).strip().lower()
            if target_priority_mode == "enforce" and bool(
                target_priority_evidence.get("valid")
            ):
                mv["target_priority_checked"] = True
            elif target_priority_mode == "observe" and bool(
                target_priority_evidence.get("observed")
            ):
                mv["target_priority_observed"] = True
                mv["target_priority_observation"] = copy.deepcopy(
                    dict(target_priority_evidence)
                )
        mv["gc_session_preflight_waivers"] = {
            str(key): dict(value) if isinstance(value, Mapping) else value
            for key, value in (waivers or {}).items()
        }
        mv["gc_session_preflight_repair_required"] = False
        mv["gc_session_preflight_repair_in_progress"] = False
        mv["gc_session_preflight_restart_available"] = False
        if repairing:
            # The next battle must establish fresh session evidence for the
            # corrected no-battle settings.
            mv["gc_session_preflight_attempted"] = False
            mv["gc_session_preflight_completed"] = False
            mv["gc_session_preflight_blocked"] = False
            self._reset_session_preflight_repair_attempts()

    def observe_detection(self, detection: Detection) -> None:
        """Update passive runtime context without materializing any action.

        Strategy Gates and global Pause suppress mission/strategy callbacks,
        but they must not leave the shared state interpretation frozen on the
        frame that activated the hold.
        """

        state = detection.get("state")
        self.ctx.data["last_detection_state"] = state
        self.ctx.data["last_detection"] = detection
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["last_detection_state"] = state

    def tick(self, screen, detection: Detection, *, strategy_only: bool = False) -> None:
        self.observe_detection(detection)

        mission_actions: List[Any] = []
        mission_complete = not bool(self.mission)
        if self.mission and not strategy_only:
            try:
                mission_actions = _materialize_actions(self.mission.tick(self.ctx, screen, detection))
            except Exception as exc:
                log(f"[MISSION] tick error: {exc}", "ERROR")
            try:
                mission_complete = bool(self.mission.is_complete(self.ctx))
            except Exception as exc:
                log(f"[MISSION] is_complete error: {exc}", "ERROR")
                mission_complete = False

            if mission_complete and not self._mission_was_complete:
                log_mission("mission complete; switching to strategy")

            self._mission_was_complete = mission_complete
        else:
            self._mission_was_complete = True

        strategy_actions: List[Any] = []
        strategy_allowed_states = {"RUNNING", "CARDS"}
        current_state = detection.get("state")
        if (
            current_state in strategy_allowed_states
            and self.strategy
            and (strategy_only or not self.mission or mission_complete)
        ):
            try:
                strategy_actions = _materialize_actions(self.strategy.tick(self.ctx, screen, detection))
            except Exception as exc:
                log(f"[STRATEGY] tick error: {exc}", "ERROR")

        # Mark strategy actions for downstream gating
        for act in strategy_actions:
            if isinstance(act, dict):
                act.setdefault("_strategy", True)

        if mission_actions or strategy_actions:
            try:
                execute_actions(
                    screen,
                    mission_actions + strategy_actions,
                    self.ctx,
                    action_guard_fn=self._action_guard_fn,
                )
            except Exception as exc:
                log(f"[EXEC] error: {exc}", "ERROR")
        self.persist_session_preflight_completion()

def _materialize_actions(actions: Optional[Iterable[Any]]) -> List[Any]:
    if not actions:
        return []
    try:
        return list(actions)
    except TypeError:
        return [actions]
