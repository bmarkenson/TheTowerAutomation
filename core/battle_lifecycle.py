"""Battle lifecycle tracking independent of the currently visible screen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class HomeBattleControl(str, Enum):
    """Meaning of the primary battle control on the Home screen."""

    NEW_BATTLE = "NEW_BATTLE"
    RESUME_BATTLE = "RESUME_BATTLE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: Any) -> "HomeBattleControl":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except (TypeError, ValueError):
            return cls.UNKNOWN


@dataclass
class BattleLifecycle:
    """Track whether this process is still observing the same battle.

    UI navigation is intentionally not a run boundary. A battle ends only when
    a terminal state is observed or Home explicitly offers a new Battle rather
    than Resume Battle.
    """

    active_battle_observed: bool = False
    adopt_initial_battle: bool = False
    last_observation_adopted: bool = False

    def observe(
        self,
        ui_state: Any,
        *,
        home_control: HomeBattleControl | str = HomeBattleControl.UNKNOWN,
    ) -> bool:
        """Record an observation and return whether a battle start was emitted."""

        state = str(ui_state or "UNKNOWN").upper()
        control = HomeBattleControl.parse(home_control)
        self.last_observation_adopted = False

        if state == "RUNNING":
            if not self.active_battle_observed:
                self.active_battle_observed = True
                if self.adopt_initial_battle:
                    self.adopt_initial_battle = False
                    self.last_observation_adopted = True
                    return False
                return True
            return False

        if state in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
            self.active_battle_observed = False
            self.adopt_initial_battle = False
        elif state in {"HOME", "HOME_SCREEN"}:
            if control is HomeBattleControl.NEW_BATTLE:
                self.active_battle_observed = False
                self.adopt_initial_battle = False
            # RESUME_BATTLE and UNKNOWN preserve the current battle identity.

        return False


__all__ = ["BattleLifecycle", "HomeBattleControl"]
