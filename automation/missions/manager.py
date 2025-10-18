from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from utils.logger import log, log_mission
from automation.missions.base import BaseMission, MissionContext
from automation.strategies.base import BaseStrategy
from core.action_executor import execute_actions


Detection = Dict[str, Any]


class MissionManager:
    def __init__(self, mission: Optional[BaseMission], strategy: Optional[BaseStrategy]):
        self.mission = mission
        self.strategy = strategy
        self.ctx = MissionContext()
        self._started = False
        self._last_state = None
        self._mission_was_complete = False
        self._run_started = False

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
        self._started = True

    def maybe_run_start(self, detection: Detection) -> None:
        state = detection.get("state")
        if state == "RUNNING":
            if not self._run_started:
                if self.mission:
                    self.mission.on_run_start(self.ctx)
                    try:
                        self._mission_was_complete = bool(self.mission.is_complete(self.ctx))
                    except Exception:
                        self._mission_was_complete = False
                if self.strategy:
                    self.strategy.on_run_start(self.ctx)
            self._run_started = True
        else:
            if state in {"GAME_OVER", "HOME"}:
                self._run_started = False
        self._last_state = state

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

    def tick(self, screen, detection: Detection) -> None:
        state = detection.get("state")
        self.ctx.data["last_detection_state"] = state
        self.ctx.data["last_detection"] = detection
        mv = self.ctx.data.setdefault("mission_vars", {})
        mv["last_detection_state"] = state

        mission_actions: List[Any] = []
        mission_complete = not bool(self.mission)
        if self.mission:
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
        if (
            detection.get("state") == "RUNNING"
            and self.strategy
            and (not self.mission or mission_complete)
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
