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

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
from numpy.typing import NDArray

from utils.logger import log
from core.run_state import AUTOMATION
from core.input import tap_if_visible
from core.label_tapper import is_visible
from core.matcher import get_match as _get_match
from core.ss_capture import capture_and_save_screenshot
from utils.coin_detector import detect_coins_from_image, format_compact_decimal


Frame = NDArray[np.uint8]


@dataclass
class CoinsTotalSnapshot:
    value: Decimal
    confidence: float
    timestamp: float
    reason: str
    image: Optional[Frame]
    previous_value: Optional[Decimal]
    previous_timestamp: Optional[float]
    previous_run_start_value: Optional[Decimal]
    previous_run_start_timestamp: Optional[float]
    session_start_value: Optional[Decimal]
    session_start_timestamp: Optional[float]

_ALLOWED_STATES = {"RUNNING", "PAUSED", "STOPPED"}
_ALLOWED_MODES = {"RETRY", "WAIT", "HOME"}


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
        self.control_file = Path(control_file)
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
        self._coins_ignore_plausibility_once: bool = False

        self._coins_pending_total_reason: Optional[str] = None
        self._coins_next_hourly_check_ts: Optional[float] = None
        self._coins_last_total: Optional[Decimal] = None
        self._coins_last_total_ts: Optional[float] = None
        self._coins_total_session_start: Optional[Decimal] = None
        self._coins_total_session_start_ts: Optional[float] = None
        self._coins_run_start_total: Optional[Decimal] = None
        self._coins_run_start_ts: Optional[float] = None

        self._rtg_visible_since_ts: Optional[float] = None

    # ------------------------- control / pause -------------------------------
    @property
    def is_paused(self) -> bool:
        st = getattr(AUTOMATION, "state", None)
        return str(st) == "RunState.PAUSED" or st == "PAUSED"

    def apply_control(self) -> None:
        """Poll the control file, apply state/mode, and auto-resume if needed."""

        directives = self._load_control_directive()
        if directives:
            self._apply_state(directives.get("state"))
            self._apply_mode(directives.get("mode"))

        self._auto_resume_if_needed()

    def format_state(self, ui_state: str) -> str:
        return f"{ui_state}/PAUSED" if self.is_paused else ui_state

    # --------------------- coins: total snapshots --------------------------
    def schedule_total_snapshot(self, reason: str = "scheduled") -> None:
        self._coins_pending_total_reason = reason

    def record_run_restart(self) -> None:
        self.schedule_total_snapshot("run_restart")

    def should_capture_total(self, now: Optional[float] = None) -> bool:
        if self._coins_pending_total_reason:
            return True
        if self._coins_next_hourly_check_ts is None:
            return False
        ts = time.time() if now is None else float(now)
        return ts >= self._coins_next_hourly_check_ts

    def _capture_coins_frame(self) -> Optional[Frame]:
        try:
            return capture_and_save_screenshot(log_capture=False)
        except Exception:
            return None

    def _detect_coins_from_frame(
        self, frame: Optional[Frame], debug_out: Optional[str] = None
    ) -> Tuple[Optional[Decimal], float, bool]:
        if frame is None:
            return None, -1.0, False
        try:
            val, conf, has_min = detect_coins_from_image(frame, debug_out=debug_out)
            return val, conf, has_min
        except Exception:
            return None, -1.0, False

    def _toggle_for_total(self) -> Tuple[Optional[Decimal], float, Optional[Frame], int]:
        toggles = 0
        if not tap_if_visible("buttons.coin_toggle", retries=1):
            log("[COINS] Failed to toggle coin display for total snapshot", "WARN")
            return None, -1.0, None, toggles

        toggles = 1
        total_val: Optional[Decimal] = None
        total_conf = -1.0
        total_img: Optional[Frame] = None

        for _ in range(3):
            time.sleep(0.4)
            frame = self._capture_coins_frame()
            if frame is None:
                continue
            val, conf, has_min = self._detect_coins_from_frame(frame)
            if val is not None and not has_min:
                total_val, total_conf, total_img = val, conf, frame
                break

        if total_img is None:
            log("[COINS] Unable to read total coins after toggle", "WARN")
            if tap_if_visible("buttons.coin_toggle", retries=1):
                toggles += 1
                time.sleep(0.4)
            return None, -1.0, None, toggles

        return total_val, total_conf, total_img, toggles

    def capture_total_snapshot(
        self,
        *,
        current_img: Optional[Frame],
        current_value: Optional[Decimal],
        current_confidence: float,
        current_has_min: bool,
        debug_out: Optional[str] = None,
    ) -> Tuple[Optional[CoinsTotalSnapshot], Optional[Tuple[Optional[Decimal], float, bool]]]:
        now = time.time()
        reason = self._coins_pending_total_reason or "hourly"

        prev_value = self._coins_last_total
        prev_ts = self._coins_last_total_ts
        prev_run_start_val = self._coins_run_start_total
        prev_run_start_ts = self._coins_run_start_ts

        total_val: Optional[Decimal] = None
        total_conf = -1.0
        total_img: Optional[Frame] = None
        toggles = 0

        if not current_has_min and current_img is not None and current_value is not None:
            total_val = current_value
            total_conf = current_confidence
            total_img = current_img
            toggles = 0
        else:
            total_val, total_conf, total_img, toggles = self._toggle_for_total()
            if total_val is None or total_img is None:
                return None, None

        restore_toggle = 1 if (toggles % 2 == 1 or not current_has_min) else 0
        per_min_frame: Optional[Frame] = None

        if restore_toggle:
            if tap_if_visible("buttons.coin_toggle", retries=1):
                toggles += 1
                time.sleep(0.4)
                per_min_frame = self._capture_coins_frame()
            else:
                log("[COINS] Failed to restore coins/min display after total snapshot", "WARN")
        else:
            per_min_frame = self._capture_coins_frame()

        per_min_val, per_min_conf, per_min_has_min = self._detect_coins_from_frame(
            per_min_frame, debug_out=debug_out
        )

        if per_min_has_min:
            self._coins_has_min_miss = 0
        else:
            log("[COINS] Post-snapshot detection missing '/min'; downstream toggle may retry", "WARN")

        if restore_toggle or toggles > 0:
            self._coins_ignore_plausibility_once = True
            self._last_coins_toggle_ts = now

        if total_val is None:
            # Keep pending reason so we retry on next cycle.
            return None, (per_min_val, per_min_conf, per_min_has_min)

        if self._coins_total_session_start is None:
            self._coins_total_session_start = total_val
            self._coins_total_session_start_ts = now

        snapshot = CoinsTotalSnapshot(
            value=total_val,
            confidence=total_conf,
            timestamp=now,
            reason=reason,
            image=total_img,
            previous_value=prev_value,
            previous_timestamp=prev_ts,
            previous_run_start_value=prev_run_start_val,
            previous_run_start_timestamp=prev_run_start_ts,
            session_start_value=self._coins_total_session_start,
            session_start_timestamp=self._coins_total_session_start_ts,
        )

        self._coins_last_total = total_val
        self._coins_last_total_ts = now
        self._coins_pending_total_reason = None
        self._coins_next_hourly_check_ts = now + 3600.0

        if reason in {"startup", "run_restart"}:
            self._coins_run_start_total = total_val
            self._coins_run_start_ts = now

        return snapshot, (per_min_val, per_min_conf, per_min_has_min)

    # --------------------- coins: toggle + plausibility ----------------------
    def _apply_plausibility(self, coins_val: Optional[Decimal], coins_conf: float) -> Optional[Decimal]:
        coins_eff = coins_val
        try:
            if self._coins_ignore_plausibility_once:
                self._coins_ignore_plausibility_once = False
                return coins_eff
            if (
                self._last_coins_val is not None
                and coins_val is not None
                and self._last_coins_val > 0
                and coins_val > 0
            ):
                ratio = coins_val / self._last_coins_val
                if ratio > self.coins_max_jump_factor and coins_conf < self.coins_jump_conf_floor:
                    log(
                        f"[COINS] Ignoring implausible jump {format_compact_decimal(self._last_coins_val)} → {format_compact_decimal(coins_val)} "
                        f"(×{ratio:.2f}, conf={coins_conf:.1f})",
                        "WARN",
                    )
                    coins_eff = self._last_coins_val
                else:
                    drop_factor = self._last_coins_val / coins_val
                    if drop_factor > self.coins_max_jump_factor:
                        log(
                            f"[COINS] Ignoring implausible drop {format_compact_decimal(self._last_coins_val)} → {format_compact_decimal(coins_val)} "
                            f"(÷{drop_factor:.2f}, conf={coins_conf:.1f})",
                            "WARN",
                        )
                        coins_eff = self._last_coins_val
        except Exception:
            pass
        return coins_eff

    def process_coins(
        self,
        img: Frame,
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

        if not has_min:
            # When '/min' is missing, stick with the last trusted coins/min value (if any)
            if self._last_coins_val is not None:
                coins_eff = self._last_coins_val
            else:
                coins_eff = None

        if not self.is_paused:
            now_ts = time.time()
            if has_min:
                self._coins_has_min_miss = 0
            else:
                ratio = None
                if (
                    coins_val is not None
                    and self._last_coins_val is not None
                    and self._last_coins_val > 0
                ):
                    try:
                        ratio = (coins_val / self._last_coins_val) if coins_val > 0 else None
                    except Exception:
                        ratio = None

                if coins_val is not None or coins_conf >= self.coins_conf_floor:
                    self._coins_has_min_miss += 1

                should_toggle = False
                toggle_reason = ""
                if ratio is not None and ratio >= self.coins_max_jump_factor:
                    should_toggle = True
                    toggle_reason = f"ratio={ratio:.2f}"
                elif self._coins_has_min_miss >= 2:
                    should_toggle = True
                    toggle_reason = f"miss_count={self._coins_has_min_miss}"

                if should_toggle and (now_ts - self._last_coins_toggle_ts) >= self.coins_toggle_cooldown:
                    log(f"[COINS] Auto-toggle coin display ({toggle_reason or 'missing /min'})", "INFO")
                    if tap_if_visible("buttons.coin_toggle", retries=1):
                        self._last_coins_toggle_ts = now_ts
                        self._coins_has_min_miss = 0
                        self._coins_ignore_plausibility_once = True
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
            if coins_eff is not None and has_min:
                self._last_coins_val = coins_eff
        except Exception:
            pass

        return coins_val, coins_conf, has_min, coins_eff

    # ------------------------- auto return-to-game ---------------------------
    def auto_return_check(self, img: Frame, ui_state: str) -> None:
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
                log(
                    f"[AUTO] Return-to-Game timer cancelled due to {reason} (after {elapsed}s)",
                    "INFO",
                    console=True,
                )
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
                    log(
                        f"[AUTO] Return-to-Game detected; starting timer ({mins}m)",
                        "INFO",
                        console=True,
                    )
                elif (time.time() - self._rtg_visible_since_ts) >= self.auto_return_secs > 0:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                    log(
                        f"[AUTO] Return-to-Game visible for {elapsed}s — tapping now.",
                        "ACTION",
                        console=True,
                    )
                    tap_if_visible("buttons.return_to_game", retries=1)
                    self._rtg_visible_since_ts = None
            else:
                if self._rtg_visible_since_ts is not None:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                    log(
                        f"[AUTO] Return-to-Game disappeared before threshold — cancelling timer (after {elapsed}s)",
                        "INFO",
                        console=True,
                    )
                    self._rtg_visible_since_ts = None
        except Exception:
            self._rtg_visible_since_ts = None

    # ------------------------------ helpers ---------------------------------
    def _load_control_directive(self) -> Dict[str, str]:
        if not self.control_file.exists():
            return {}
        try:
            with self.control_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle) or {}
        except (OSError, json.JSONDecodeError) as exc:
            log(f"[CTRL] Failed reading control file: {exc}", "WARN")
            return {}
        return data if isinstance(data, dict) else {}

    def _apply_state(self, state: Optional[str]) -> None:
        if not state:
            return
        normalized = state.upper()
        if normalized not in _ALLOWED_STATES or normalized == self._last_applied_state:
            return
        try:
            AUTOMATION.state = normalized
            log(
                f"[CTRL] State set to {normalized} via control file",
                "INFO",
                console=True,
            )
            self._last_applied_state = normalized
            self._paused_since_ts = time.time() if normalized == "PAUSED" else None
        except Exception as exc:
            log(f"[CTRL] Failed to set state={normalized}: {exc}", "WARN")

    def _apply_mode(self, mode: Optional[str]) -> None:
        if not mode:
            return
        normalized = mode.upper()
        if normalized not in _ALLOWED_MODES or normalized == self._last_applied_mode:
            return
        try:
            AUTOMATION.mode = normalized
            log(
                f"[CTRL] Mode set to {normalized} via control file",
                "INFO",
                console=True,
            )
            self._last_applied_mode = normalized
        except Exception as exc:
            log(f"[CTRL] Failed to set mode={normalized}: {exc}", "WARN")

    def _auto_resume_if_needed(self) -> None:
        if not self.auto_resume_enabled or self.auto_resume_secs <= 0:
            return
        if not self.is_paused or self._paused_since_ts is None:
            return
        try:
            if (time.time() - self._paused_since_ts) >= self.auto_resume_secs:
                AUTOMATION.state = "RUNNING"
                self._paused_since_ts = None
                log("[CTRL] Auto-resume: State=RUNNING after pause timeout", "INFO")
                self._last_applied_state = "RUNNING"
        except Exception as exc:
            log(f"[CTRL] Auto-resume failed: {exc}", "WARN")

# Re-exports for convenience
try:
    from core.run_state import RunState, ExecMode
    __all__ = [
        "AutomationSupervisor",
        "CoinsTotalSnapshot",
        "AUTOMATION",
        "RunState",
        "ExecMode",
    ]
except Exception:
    __all__ = ["AutomationSupervisor", "CoinsTotalSnapshot", "AUTOMATION"]
