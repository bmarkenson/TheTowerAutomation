from __future__ import annotations

from typing import Optional
from .base import BaseMission


class NoOpMission(BaseMission):
    name = "none"


def get_mission(name: str) -> Optional[BaseMission]:
    nm = (name or "").lower()
    if nm in ("", "none"):
        return None
    # Future: import concrete missions here (e.g., demon_mode, nuke)
    # For now, return a NoOp if unknown to avoid breaking flows
    try:
        from .nuke import NukeMission  # type: ignore
        from .demon_mode import DemonModeMission  # type: ignore
        from .demon_nuke import DemonNukeMission  # type: ignore
        if nm == "nuke":
            return NukeMission()
        if nm == "demon_mode":
            return DemonModeMission()
        if nm == "demon_nuke":
            return DemonNukeMission()
    except Exception:
        pass
    return NoOpMission()
