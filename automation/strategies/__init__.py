from __future__ import annotations

from typing import Optional

from .base import BaseStrategy
from .blender import BlenderStrategy


class NoOpStrategy(BaseStrategy):
    name = "none"


def get_strategy(name: str) -> Optional[BaseStrategy]:
    nm = (name or "").lower()
    if nm in ("", "none"):
        return None
    if nm == BlenderStrategy.name:
        return BlenderStrategy()
    return NoOpStrategy()
