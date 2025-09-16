from __future__ import annotations

import time
from typing import Dict, Any, List
from utils.logger import log
from .base import BaseMission, MissionContext


class NukeMission(BaseMission):
    name = "nuke"

    def __init__(self, accrue_seconds: int = 20, post_nuke_delay: int = 5):
        self.accrue_seconds = max(0, int(accrue_seconds))
        self.post_nuke_delay = max(0, int(post_nuke_delay))

    def on_run_start(self, ctx: MissionContext) -> None:
        super().on_run_start(ctx)
        # Per-run flags
        ctx.data["nuke"] = {"scheduled": False, "fired": False}
        log(f"[MISSION nuke] Run #{ctx.run_id} started; accruing for {self.accrue_seconds}s", "INFO")

    def _run_elapsed(self, ctx: MissionContext) -> float:
        if not ctx.run_started_ts:
            return 0.0
        return max(0.0, time.time() - ctx.run_started_ts)

    def tick(self, ctx: MissionContext, screen, detection: Dict[str, Any]):
        # Only act while RUNNING
        if detection.get("state") != "RUNNING":
            return None

        st = ctx.data.get("nuke") or {}
        fired = st.get("fired", False)
        if fired:
            return None

        elapsed = self._run_elapsed(ctx)
        actions: List[Dict[str, Any]] = []

        if elapsed < self.accrue_seconds:
            # Keep accruing enemies
            return None

        # Fire Nuke when available (only mark fired if button present)
        from core.floating_button_detector import detect_floating_buttons
        buttons = detect_floating_buttons(screen)
        if any(b["name"] == "floating_buttons.nuke" for b in buttons):
            log("[MISSION nuke] Accrual complete; firing Nuke", "ACTION")
            actions.append({"type": "fire_floating", "name": "floating_buttons.nuke"})
            st["fired"] = True
            ctx.data["nuke"] = st
        else:
            # Not yet available; try again next tick
            return None
        if self.post_nuke_delay:
            actions.append({"type": "sleep", "ms": int(self.post_nuke_delay * 1000)})
        actions.append({"type": "restart_run"})
        return actions
