from __future__ import annotations

from typing import Optional, Dict, Any
from utils.logger import log
from automation.missions.base import BaseMission, MissionContext
from automation.strategies.base import BaseStrategy
from core.action_executor import execute_actions


class MissionManager:
    def __init__(self, mission: Optional[BaseMission], strategy: Optional[BaseStrategy]):
        self.mission = mission
        self.strategy = strategy
        self.ctx = MissionContext()
        self._started = False
        self._last_state = None

    def start(self) -> None:
        if self._started:
            return
        if self.mission:
            self.mission.on_start(self.ctx)
        if self.strategy:
            self.strategy.on_start(self.ctx)
        self._started = True

    def maybe_run_start(self, detection: Dict[str, Any]) -> None:
        state = detection.get("state")
        if self._last_state != "RUNNING" and state == "RUNNING":
            if self.mission:
                self.mission.on_run_start(self.ctx)
            if self.strategy:
                self.strategy.on_run_start(self.ctx)
        self._last_state = state

    def handle_overlays(self, detection: Dict[str, Any]) -> None:
        if not self.mission:
            return
        for name in (detection.get("overlays") or []):
            try:
                self.mission.on_overlay(self.ctx, name)
            except Exception:
                pass

    def on_state(self, detection: Dict[str, Any]) -> None:
        if self.mission:
            try:
                self.mission.on_state(self.ctx, detection)
            except Exception:
                pass

    def on_home(self) -> None:
        if self.mission:
            try:
                self.mission.on_home(self.ctx)
            except Exception:
                pass

    def on_game_over(self) -> None:
        if self.mission:
            try:
                self.mission.on_game_over(self.ctx)
            except Exception:
                pass

    def tick(self, screen, detection: Dict[str, Any]) -> None:
        # Mission level tick (timers/sequencing)
        mission_actions = []
        if self.mission:
            try:
                ma = self.mission.tick(self.ctx, screen, detection)
                if ma:
                    mission_actions = list(ma)
            except Exception as e:
                log(f"[MISSION] tick error: {e}", "ERROR")

        # Strategy actions only when RUNNING
        strategy_actions = []
        if detection.get("state") == "RUNNING" and self.strategy:
            try:
                sa = self.strategy.tick(self.ctx, screen, detection)
                if sa:
                    strategy_actions = list(sa)
            except Exception as e:
                log(f"[STRATEGY] tick error: {e}", "ERROR")

        # Combine and execute all actions
        all_actions = mission_actions + strategy_actions
        if all_actions:
            try:
                # Mission actions run first, then strategy actions
                execute_actions(screen, all_actions)
            except Exception as e:
                log(f"[EXEC] error: {e}", "ERROR")
