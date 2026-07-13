from __future__ import annotations

from typing import Optional
from .base import BaseMission

LEGACY_MSG = (
    "Python missions have been removed; provide --mission-config with a YAML plan instead."
)


class NoOpMission(BaseMission):
    name = "none"


def get_mission(name: str) -> Optional[BaseMission]:
    nm = (name or "").strip().lower()
    if nm in ("", "none"):
        return None
    raise ValueError(LEGACY_MSG)
