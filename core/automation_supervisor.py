"""
core/automation_supervisor.py

Encapsulates runtime automation control & small recoveries so `main.py` stays
focused on capture → detect → dispatch.

Features:
- Control-file polling (pause/resume/mode) + optional auto-resume after N secs
- Coins toggle debounce and plausibility (jump) gate
- Auto "Return to Game" after sustained visibility, with logs and fallback match

Public usage (simplified):
    sup = AutomationSupervisor(...)
    sup.apply_control()        # updates AUTOMATION.state/mode, handles auto-resume
    paused = sup.is_paused
    coins_val, coins_conf, has_min, coins_eff = sup.process_coins(img, coins_val, coins_conf, has_min, debug_out)
    sup.auto_return_check(img, ui_state)
    state_label = sup.format_state(new_state)
"""

from __future__ import annotations

import os
import json
import time
from decimal import Decimal
from typing import Optional, Tuple

from utils.logger import log
from core.run_state import AUTOMATION
from core.tap import tap_if_visible
from core.label_tapper import is_visible
from core.matcher import get_match as _get_match
from core.ss_capture import capture_and_save_screenshot
from utils.coin_detector import detect_coins_from_image, format_compact_decimal


class AutomationSupervisor:
    def __init__(
        self,
        *,
        control_file: str,
        auto_resume_secs: int,
        auto_resume_enabled: bool = True,
        auto_return_secs: int = 0,
        auto_return_enabled: bool = True,
        auto_return_conf_threshold: float = 0.85,
        coins_toggle_cooldown: float = 15.0,
        coins_conf_floor: float = 60.0,
        coins_max_jump_factor: float = 8.0,
        coins_jump_conf_floor: float = 90.0,
    ) -> None:
        self.control_file = control_file
        self.auto_resume_secs = max(0, int(auto_resume_secs))
        self.auto_resume_enabled = bool(auto_resume_enabled)
        self.auto_return_secs = max(0, int(auto_return_secs))
        self.auto_return_enabled = bool(auto_return_enabled)
        self.auto_return_conf_threshold = float(auto_return_conf_threshold)

        self.coins_toggle_cooldown = float(coins_toggle_cooldown)
        self.coins_conf_floor = float(coins_conf_floor)
        self.coins_max_jump_factor = Decimal(str(coins_max_jump_factor))
        self.coins_jump_conf_floor = float(coins_jump_conf_floor)

        # Internal state
        self._last_applied_state: Optional[str] = None
        self._last_applied_mode: Optional[str] = None
        self._paused_since_ts: Optional[float] = None

        self._last_coins_toggle_ts: float = 0.0
        self._coins_has_min_miss: int = 0
        self._last_coins_val: Optional[Decimal] = None

        self._rtg_visible_since_ts: Optional[float] = None

    # ------------------------- control / pause -------------------------------
    @property
    def is_paused(self) -> bool:
        st = getattr(AUTOMATION, "state", None)
        return str(st) == "RunState.PAUSED" or st == "PAUSED"

    def apply_control(self) -> None:
        """Poll the control file, apply state/mode, and auto-resume if needed."""
        # Control file
        try:
            if os.path.exists(self.control_file):
                data = {}
                try:
                    with open(self.control_file, "r", encoding="utf-8") as f:
                        data = json.load(f) or {}
                except Exception:
                    data = {}
                st = (data.get("state") or "").upper()
                md = (data.get("mode") or "").upper()
                if st in {"RUNNING", "PAUSED", "STOPPED"} and st != self._last_applied_state:
                    try:
                        AUTOMATION.state = st
                        log(f"[CTRL] State set to {st} via control file", "INFO")
                        self._last_applied_state = st
                        if st == "PAUSED":
                            self._paused_since_ts = time.time()
                        else:
                            self._paused_since_ts = None
                    except Exception:
                        pass
                if md in {"RETRY", "WAIT", "HOME"} and md != self._last_applied_mode:
                    try:
                        AUTOMATION.mode = md
                        log(f"[CTRL] Mode set to {md} via control file", "INFO")
                        self._last_applied_mode = md
                    except Exception:
                        pass
        except Exception:
            pass

        # Auto-resume from paused
        try:
            if self.auto_resume_enabled and self.is_paused and self._paused_since_ts is not None:
                if (time.time() - self._paused_since_ts) >= self.auto_resume_secs > 0:
                    AUTOMATION.state = "RUNNING"
                    self._paused_since_ts = None
                    log("[CTRL] Auto-resume: State=RUNNING after pause timeout", "INFO")
        except Exception:
            pass

    def format_state(self, ui_state: str) -> str:
        return f"{ui_state}/PAUSED" if self.is_paused else ui_state

    # --------------------- coins: toggle + plausibility ----------------------
    def _apply_plausibility(self, coins_val: Optional[Decimal], coins_conf: float) -> Optional[Decimal]:
        coins_eff = coins_val
        try:
            if self._last_coins_val is not None and coins_val is not None and self._last_coins_val > 0:
                ratio = (coins_val / self._last_coins_val)
                if ratio > self.coins_max_jump_factor and coins_conf < self.coins_jump_conf_floor:
                    log(
                        f"[COINS] Ignoring implausible jump {format_compact_decimal(self._last_coins_val)} → {format_compact_decimal(coins_val)} "
                        f"(×{ratio:.2f}, conf={coins_conf:.1f})",
                        "WARN",
                    )
                    coins_eff = self._last_coins_val
        except Exception:
            pass
        return coins_eff

    def process_coins(
        self,
        img,
        coins_val: Optional[Decimal],
        coins_conf: float,
        has_min: bool,
        *,
        debug_out: Optional[str] = None,
    ) -> Tuple[Optional[Decimal], float, bool, Optional[Decimal]]:
        """
        Apply plausibility gate and, when not paused, debounce the coin-toggle to
        switch display if '/min' is missing. Returns updated (val, conf, has_min, eff).
        May issue one toggle and a re-capture.
        """
        # Plausibility first
        coins_eff = self._apply_plausibility(coins_val, coins_conf)

        if not self.is_paused:
            now_ts = time.time()
            if has_min:
                self._coins_has_min_miss = 0
            else:
                if coins_conf >= self.coins_conf_floor:
                    self._coins_has_min_miss += 1
                if self._coins_has_min_miss >= 2 and (now_ts - self._last_coins_toggle_ts) >= self.coins_toggle_cooldown:
                    tap_if_visible("buttons.coin_toggle", retries=1)
                    self._last_coins_toggle_ts = now_ts
                    self._coins_has_min_miss = 0
                    time.sleep(0.6)
                    img2 = capture_and_save_screenshot(log_capture=False)
                    if img2 is not None:
                        try:
                            coins_val, coins_conf, has_min = detect_coins_from_image(img2, debug_out=debug_out)
                            coins_eff = self._apply_plausibility(coins_val, coins_conf)
                        except Exception:
                            pass

        # Update accepted last value
        try:
            if coins_eff is not None:
                self._last_coins_val = coins_eff
        except Exception:
            pass

        return coins_val, coins_conf, has_min, coins_eff

    # ------------------------- auto return-to-game ---------------------------
    def auto_return_check(self, img, ui_state: str) -> None:
        if not self.auto_return_enabled or self.is_paused or ui_state == "RUNNING":
            # If timer was running but conditions no longer hold, cancel
            if self._rtg_visible_since_ts is not None:
                try:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                except Exception:
                    elapsed = 0
                reason = (
                    "state RUNNING" if ui_state == "RUNNING" else "paused/disabled"
                )
                log(f"[AUTO] Return-to-Game timer cancelled due to {reason} (after {elapsed}s)", "INFO")
                self._rtg_visible_since_ts = None
            return

        try:
            visible = is_visible("buttons.return_to_game", screenshot=img)
            if not visible:
                try:
                    pt, conf = _get_match("buttons.return_to_game", screenshot=img)
                    visible = bool(pt) and (conf >= self.auto_return_conf_threshold)
                    if visible:
                        log(f"[AUTO] Return-to-Game matched via fallback (conf={conf:.2f})", "DEBUG")
                except Exception:
                    visible = False

            if visible:
                if self._rtg_visible_since_ts is None:
                    self._rtg_visible_since_ts = time.time()
                    mins = (self.auto_return_secs // 60) if self.auto_return_secs > 0 else 0
                    log(f"[AUTO] Return-to-Game detected; starting timer ({mins}m)", "INFO")
                elif (time.time() - self._rtg_visible_since_ts) >= self.auto_return_secs > 0:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                    log(f"[AUTO] Return-to-Game visible for {elapsed}s — tapping now.", "ACTION")
                    tap_if_visible("buttons.return_to_game", retries=1)
                    self._rtg_visible_since_ts = None
            else:
                if self._rtg_visible_since_ts is not None:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                    log(f"[AUTO] Return-to-Game disappeared before threshold — cancelling timer (after {elapsed}s)", "INFO")
                    self._rtg_visible_since_ts = None
        except Exception:
            self._rtg_visible_since_ts = None

# Re-exports for convenience
try:
    from core.run_state import RunState, ExecMode
    __all__ = [
        "AutomationSupervisor",
        "AUTOMATION",
        "RunState",
        "ExecMode",
    ]
except Exception:
    __all__ = ["AutomationSupervisor", "AUTOMATION"]
