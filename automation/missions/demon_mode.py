from __future__ import annotations

import time
from typing import Dict, Any, List
from utils.logger import log
from .base import BaseMission, MissionContext
from core.floating_button_detector import detect_floating_buttons


class DemonModeMission(BaseMission):
    name = "demon_mode"

    def __init__(self, duration_seconds: int = 45):
        self.duration_seconds = max(1, int(duration_seconds))

    def on_run_start(self, ctx: MissionContext) -> None:
        super().on_run_start(ctx)
        ctx.data["demon"] = {"fired": False, "active_until": 0.0}
        log(f"[MISSION demon] Run #{ctx.run_id} started; will fire Demon Mode once", "INFO")

    def tick(self, ctx: MissionContext, screen, detection: Dict[str, Any]):
        if detection.get("state") != "RUNNING":
            return None
        st = ctx.data.get("demon") or {}
        if not st.get("fired"):
            # Only fire when the button is available on screen
            buttons = detect_floating_buttons(screen)
            if any(b["name"] == "floating_buttons.demon_mode" for b in buttons):
                log("[MISSION demon] Firing Demon Mode", "ACTION")
                st["fired"] = True
                st["active_until"] = time.time() + self.duration_seconds
                ctx.data["demon"] = st
                return [{"type": "fire_floating", "name": "floating_buttons.demon_mode"}]
            else:
                return None
        # Already fired: nothing else to do in this basic mission
        return None

