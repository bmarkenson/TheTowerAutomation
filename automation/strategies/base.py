from __future__ import annotations

from typing import Any, Dict, List, MutableSet

from automation.missions.base import MissionContext


Action = Dict[str, Any]


class BaseStrategy:
    """Common interface for runtime strategies layered on top of missions."""

    name = "base"

    def __init__(self) -> None:
        self.skip_upgrades: MutableSet[str] = set()

    def on_start(self, ctx: MissionContext) -> None:
        """Called once when the strategy is initialised."""

    def on_run_start(self, ctx: MissionContext) -> None:
        """Called when the automation loop transitions into RUNNING."""

        self.skip_upgrades.clear()

    def tick(self, ctx: MissionContext, screen, detection: Dict[str, Any]) -> List[Action]:
        """Return executor actions for the current frame. Default: no-op."""

        return []

    def requires_run_initialization(self) -> bool:
        """Whether this strategy owns an exclusive new-run initialization phase."""

        return False

    def is_run_initialization_complete(self, ctx: MissionContext) -> bool:
        """Return whether normal automation actions may resume for this run."""

        return True

    def requires_session_preflight(self) -> bool:
        """Whether this strategy owns a once-per-process preflight gate."""

        return False

    def is_session_preflight_complete(self, ctx: MissionContext) -> bool:
        """Return whether this process has verified its session requirements."""

        return True

    def on_game_over(self, ctx: MissionContext) -> None:
        """Optional hook when GAME_OVER is handled."""
