from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from utils.logger import log, log_mission
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
    ):
        self.mission = mission
        self.strategy = strategy
        self.ctx = MissionContext()
        self._started = False
        self._last_state = None
        self._mission_was_complete = False
        self._startup_gates_deferred = bool(defer_startup_gates_until_next_run)
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
        self.ctx.data["startup_gates_deferred"] = self._startup_gates_deferred
        self._started = True

    def maybe_run_start(self, detection: Detection) -> None:
        """Emit run-start hooks only when the battle lifecycle starts anew."""

        state = detection.get("state")
        control = detection.get("home_battle_control", "UNKNOWN")
        normalized_state = str(state or "UNKNOWN").upper()
        if self._startup_gates_deferred and (
            normalized_state in {"GAME_OVER", "TOURNAMENT_RESULTS"}
            or (
                normalized_state in {"HOME", "HOME_SCREEN"}
                and HomeBattleControl.parse(control) is HomeBattleControl.NEW_BATTLE
            )
        ):
            self._arm_startup_gates()
        battle_started = self._battle_lifecycle.observe(
            state,
            home_control=control,
        )
        if self._battle_lifecycle.last_observation_adopted:
            log(
                "[RUN_INIT] Attached to existing battle; startup gates deferred "
                "until the next run boundary",
                "INFO",
                console=True,
            )
        if battle_started:
            self._arm_startup_gates()
            if self.mission:
                self.mission.on_run_start(self.ctx)
                try:
                    self._mission_was_complete = bool(self.mission.is_complete(self.ctx))
                except Exception:
                    self._mission_was_complete = False
            if self.strategy:
                self.strategy.on_run_start(self.ctx)
        self._last_state = state

    def _arm_startup_gates(self) -> None:
        if not self._startup_gates_deferred:
            return
        self._startup_gates_deferred = False
        self.ctx.data["startup_gates_deferred"] = False
        log(
            "[RUN_INIT] Run boundary observed; startup gates armed for the next battle",
            "INFO",
            console=True,
        )

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

    def run_initialization_pending(self) -> bool:
        """Return whether the active battle still requires initialization."""

        if (
            self._startup_gates_deferred
            or not self._battle_lifecycle.active_battle_observed
            or not self.strategy
        ):
            return False
        if not self.strategy.requires_run_initialization():
            return False
        return not self.strategy.is_run_initialization_complete(self.ctx)

    def session_preflight_pending(self) -> bool:
        """Return whether the active battle is waiting on session validation."""

        if (
            self._startup_gates_deferred
            or not self._battle_lifecycle.active_battle_observed
            or not self.strategy
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

    def mark_no_battle_setup_complete(self, evidence: Mapping[str, Any]) -> None:
        mv = self.ctx.data.setdefault("mission_vars", {})
        repairing = bool(
            mv.get("gc_session_preflight_repair_required")
            or mv.get("gc_session_preflight_repair_in_progress")
        )
        mv["gc_no_battle_setup_completed"] = True
        mv["gc_no_battle_setup_evidence"] = dict(evidence)
        mv["gc_session_preflight_repair_required"] = False
        mv["gc_session_preflight_repair_in_progress"] = False
        if repairing:
            # The next battle must establish fresh session evidence for the
            # corrected no-battle settings.
            mv["gc_session_preflight_attempted"] = False
            mv["gc_session_preflight_completed"] = False
            mv["gc_session_preflight_blocked"] = False

    def tick(self, screen, detection: Detection, *, strategy_only: bool = False) -> None:
        state = detection.get("state")
        self.ctx.data["last_detection_state"] = state
        self.ctx.data["last_detection"] = detection
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["last_detection_state"] = state

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
                execute_actions(screen, mission_actions + strategy_actions, self.ctx)
            except Exception as exc:
                log(f"[EXEC] error: {exc}", "ERROR")

def _materialize_actions(actions: Optional[Iterable[Any]]) -> List[Any]:
    if not actions:
        return []
    try:
        return list(actions)
    except TypeError:
        return [actions]
