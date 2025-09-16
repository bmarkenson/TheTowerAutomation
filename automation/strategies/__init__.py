from __future__ import annotations

from typing import Optional
from .base import BaseStrategy


class NoOpStrategy(BaseStrategy):
    name = "none"


def get_strategy(name: str) -> Optional[BaseStrategy]:
    nm = (name or "").lower()
    if nm in ("", "none"):
        return None
    # Future: import concrete strategies here
    return NoOpStrategy()

