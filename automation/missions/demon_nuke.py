from __future__ import annotations

import time
from typing import Dict, Any, List
from utils.logger import log
from .base import BaseMission, MissionContext


class DemonNukeMission(BaseMission):
    name = "demon_nuke"

    def __init__(self, demon_duration: int = 45, nuke_margin: int = 5, post_nuke_delay: int = 5):
        self.demon_duration = max(1, int(demon_duration))
        self.nuke_margin = max(0, int(nuke_margin))
        self.post_nuke_delay = max(0, int(post_nuke_delay))

    def on_run_start(self, ctx: MissionContext) -> None:
        super().on_run_start(ctx)
        ctx.data["demon"] = {"fired": False, "active_until": 0.0}
        ctx.data["nuke"] = {"fired": False}
        log(f"[MISSION demon_nuke] Run #{ctx.run_id} started; will chain Demon → Nuke", "INFO")

    def tick(self, ctx: MissionContext, screen, detection: Dict[str, Any]):
        if detection.get("state") != "RUNNING":
            return None

        actions: List[Dict[str, Any]] = []
        d = ctx.data.get("demon") or {}
        n = ctx.data.get("nuke") or {}

        # Step 1: Fire demon if not yet fired and available
        if not d.get("fired"):
            action = self._fire_floating_if_visible(screen, "floating_buttons.demon_mode")
            if action:
                log("[MISSION demon_nuke] Firing Demon Mode", "ACTION")
                actions.append(action)
                d["fired"] = True
                d["active_until"] = time.time() + self.demon_duration
                ctx.data["demon"] = d
            return actions or None

        # Step 2: Fire nuke near end of demon window if not yet fired
        if not n.get("fired"):
            active_until = float(d.get("active_until") or 0.0)
            now = time.time()
            # Wait until we're within margin window
            if active_until > 0 and now >= (active_until - self.nuke_margin):
                action = self._fire_floating_if_visible(screen, "floating_buttons.nuke")
                if action:
                    log("[MISSION demon_nuke] Firing Nuke near end of Demon Mode", "ACTION")
                    actions.append(action)
                    n["fired"] = True
                    ctx.data["nuke"] = n
                    if self.post_nuke_delay:
                        actions.append({"type": "sleep", "ms": int(self.post_nuke_delay * 1000)})
                    actions.append({"type": "restart_run"})
                    return actions
            # Not within the window or not available yet
            return None

        # Step 3: After nuke, nothing else — let handlers drive GAME_OVER/Retry
        return None
