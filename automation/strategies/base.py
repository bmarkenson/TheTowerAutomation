from __future__ import annotations

from typing import Dict, Any, List


class BaseStrategy:
    name = "base"

    def __init__(self) -> None:
        # Per-run caches (e.g., skip_upgrades)
        self.skip_upgrades = set()

    def on_start(self, ctx) -> None:
        pass

    def on_run_start(self, ctx) -> None:
        # Reset per-run caches
        self.skip_upgrades.clear()

    def tick(self, ctx, screen, detection: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return a list of actions for the executor. Default: no actions."""
        return []

