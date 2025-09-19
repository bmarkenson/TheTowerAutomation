from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time
from core.floating_button_detector import detect_floating_buttons


@dataclass
class MissionContext:
    run_id: int = 0
    run_started_ts: float = 0.0
    started_ts: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)


class BaseMission:
    name = "base"

    def on_start(self, ctx: MissionContext) -> None:
        pass

    def on_run_start(self, ctx: MissionContext) -> None:
        # Reset per-run data container; strategies may also reset on this signal
        ctx.run_id += 1
        ctx.run_started_ts = time.time()
        ctx.data.setdefault("run", {})
        ctx.data["run"] = {}

    def on_state(self, ctx: MissionContext, detection: Dict[str, Any]) -> None:
        pass

    def on_overlay(self, ctx: MissionContext, name: str) -> None:
        pass

    def on_home(self, ctx: MissionContext) -> None:
        pass

    def on_game_over(self, ctx: MissionContext) -> None:
        pass

    def tick(self, ctx: MissionContext, screen, detection: Dict[str, Any]):
        """Return optional list of executor actions (or None)."""
        return None

    def is_complete(self, ctx: MissionContext) -> bool:
        return False

    def _fire_floating_if_visible(self, screen, button_name: str) -> Optional[Dict[str, Any]]:
        """
        Helper to detect a floating button and return a 'fire_floating' action if it's visible.
        Returns the action dictionary on success, otherwise None.
        """
        buttons = detect_floating_buttons(screen)
        if any(b["name"] == button_name for b in buttons):
            return {"type": "fire_floating", "name": button_name}
        return None
