# core/status_report.py
"""
Logic for generating periodic status reports and saving debug samples.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional, Set

import cv2

from utils.logger import log
from utils.wave_detector import detect_wave_number_from_image
from utils.coin_detector import detect_coins_from_image, format_compact_decimal
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.automation_supervisor import AutomationSupervisor


class StateChangeTracker:
    """Emit structured logs when primary/menu/overlay states change."""

    def __init__(self) -> None:
        self._last_state: Optional[str] = None
        self._last_menu: Optional[str] = None
        self._last_secondary: Optional[Set[str]] = None
        self._last_overlays: Optional[Set[str]] = None

    def update(self, *, state: str, menu: Optional[str], secondary: Set[str], overlays: Set[str]) -> None:
        if state != self._last_state:
            log(f"UI state change: {self._last_state} → {state}", "STATE")
            self._last_state = state

        if menu != self._last_menu:
            if menu and not self._last_menu:
                log(f"Menu opened: {menu}", "MATCH")
            elif self._last_menu and not menu:
                log(f"Menu closed: {self._last_menu}", "MATCH")
            elif self._last_menu != menu:
                log(f"Menu switched: {self._last_menu} → {menu}", "MATCH")
            self._last_menu = menu

        if self._last_secondary is None:
            if secondary:
                log(f"Secondary states now: {sorted(secondary)}", "MATCH")
        else:
            added = sorted(secondary - self._last_secondary)
            removed = sorted(self._last_secondary - secondary)
            if added:
                log(f"Secondary states added: {added}", "MATCH")
            if removed:
                log(f"Secondary states removed: {removed}", "MATCH")
        self._last_secondary = set(secondary)

        if self._last_overlays is None:
            if overlays:
                log(f"Overlays now: {sorted(overlays)}", "MATCH")
        else:
            added = sorted(overlays - self._last_overlays)
            removed = sorted(self._last_overlays - overlays)
            if added:
                log(f"Overlays added: {added}", "MATCH")
            if removed:
                log(f"Overlays removed: {removed}", "MATCH")
        self._last_overlays = set(overlays)


class StatusReporter:
    """Periodic RUNNING-state heartbeat with OCR samples and CSV logging."""

    def __init__(
        self,
        *,
        interval_secs: int,
        supervisor: AutomationSupervisor,
        save_wave_samples: Optional[str],
        save_coin_samples: Optional[str],
        coins_log_base: str,
        coins_log_enabled: bool,
    ) -> None:
        self._interval = max(0, int(interval_secs))
        self._supervisor = supervisor
        self._save_wave_samples = save_wave_samples
        self._save_coin_samples = save_coin_samples
        self._coins_log_enabled = coins_log_enabled
        self._coins_log_base = coins_log_base

        self._last_status_ts: float = 0.0
        self._coins_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._coins_log_path: Optional[str] = None
        if self._coins_log_enabled:
            self._coins_log_path = self._make_coins_log_path(self._coins_session_id)

    @property
    def coins_log_path(self) -> Optional[str]:
        return self._coins_log_path

    def rotate_coins_log(self) -> Optional[str]:
        if not self._coins_log_enabled:
            return None
        self._coins_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._coins_log_path = self._make_coins_log_path(self._coins_session_id)
        return self._coins_log_path

    def maybe_report(
        self,
        *,
        img,
        ui_state: str,
        menu: Optional[str],
        secondary: Set[str],
        overlays: Set[str],
        now_ts: Optional[float] = None,
    ) -> None:
        if self._interval == 0:
            return

        now = time.time() if now_ts is None else float(now_ts)
        if now - self._last_status_ts < self._interval:
            return

        wave = None
        wave_conf = -1.0
        coins_val = None
        coins_conf = -1.0
        coins_eff = None
        has_min = False

        if ui_state == "RUNNING":
            debug_out = None
            if self._save_wave_samples:
                os.makedirs(self._save_wave_samples, exist_ok=True)
                debug_out = os.path.join(self._save_wave_samples, "_tmp_bin.png")
            wave, wave_conf = detect_wave_number_from_image(img, debug_out=debug_out)

            try:
                coins_debug_tmp = None
                if self._save_coin_samples:
                    os.makedirs(self._save_coin_samples, exist_ok=True)
                    coins_debug_tmp = os.path.join(self._save_coin_samples, "_tmp_coin_bin.png")

                coins_val, coins_conf, has_min = detect_coins_from_image(img, debug_out=coins_debug_tmp)
                coins_val, coins_conf, has_min, coins_eff = self._supervisor.process_coins(
                    img,
                    coins_val,
                    coins_conf,
                    has_min,
                    debug_out=coins_debug_tmp,
                )
            except Exception:
                coins_val, coins_conf, coins_eff = None, -1.0, None

        wave_str = str(wave) if wave is not None else "—"
        coins_str = format_compact_decimal(coins_eff) if coins_eff is not None else "—"
        menu_str = menu or "—"
        sec_str = ", ".join(sorted(secondary)) if secondary else "—"
        ovl_str = ", ".join(sorted(overlays)) if overlays else "—"
        state_str = self._supervisor.format_state(ui_state)

        log(
            f"[STATUS] State={state_str} | Wave={wave_str} | Coins/min={coins_str} | Menu={menu_str} | "
            f"Secondary=[{sec_str}] | Overlays=[{ovl_str}]",
            "INFO",
        )

        if self._save_wave_samples:
            try:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                base = f"{ts}_wave-{wave_str}"
                img_path = os.path.join(self._save_wave_samples, base + ".png")
                note_path = os.path.join(self._save_wave_samples, base + ".txt")
                cv2.imwrite(img_path, img)
                try:
                    with open(note_path, "w", encoding="utf-8") as handle:
                        note_contents = (
                            f"state={ui_state}\n"
                            f"menu={menu_str}\n"
                            f"secondary={sec_str}\n"
                            f"overlays={ovl_str}\n"
                            f"wave={wave_str}\n"
                            f"conf={wave_conf:.1f}\n"
                            f"coins={coins_str}\n"
                            f"coins_conf={coins_conf:.1f}\n"
                        )
                        handle.write(note_contents)
                except Exception:
                    pass

                try:
                    overlay = img.copy()
                    cm = get_clickmap()
                    entry = resolve_dot_path("_shared_match_regions.wave_number", cm) or {}
                    match_region = entry.get("match_region") if isinstance(entry, dict) else None
                    if match_region:
                        x = int(match_region.get("x", 0))
                        y = int(match_region.get("y", 0))
                        w = int(match_region.get("w", 0))
                        h = int(match_region.get("h", 0))
                        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 255), 2)
                    cv2.imwrite(os.path.join(self._save_wave_samples, base + "_overlay.png"), overlay)
                except Exception:
                    pass

                tmp_bin = os.path.join(self._save_wave_samples, "_tmp_bin.png")
                if os.path.exists(tmp_bin):
                    os.replace(tmp_bin, os.path.join(self._save_wave_samples, base + "_bin.png"))
            except Exception:
                pass

        if self._save_coin_samples:
            try:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                base = f"{ts}_coins-{coins_str}"
                img_path = os.path.join(self._save_coin_samples, base + ".png")
                note_path = os.path.join(self._save_coin_samples, base + ".txt")
                cv2.imwrite(img_path, img)
                try:
                    with open(note_path, "w", encoding="utf-8") as handle:
                        note_contents = (
                            f"state={ui_state}\n"
                            f"menu={menu_str}\n"
                            f"secondary={sec_str}\n"
                            f"overlays={ovl_str}\n"
                            f"coins={coins_str}\n"
                            f"coins_conf={coins_conf:.1f}\n"
                            f"has_min={'yes' if has_min else 'no'}\n"
                        )
                        handle.write(note_contents)
                except Exception:
                    pass

                try:
                    overlay = img.copy()
                    cm = get_clickmap()
                    entry = resolve_dot_path("_shared_match_regions.coins", cm) or {}
                    match_region = entry.get("match_region") if isinstance(entry, dict) else None
                    if match_region:
                        x = int(match_region.get("x", 0))
                        y = int(match_region.get("y", 0))
                        w = int(match_region.get("w", 0))
                        h = int(match_region.get("h", 0))
                        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    cv2.imwrite(os.path.join(self._save_coin_samples, base + "_overlay.png"), overlay)
                except Exception:
                    pass

                tmp_coin = os.path.join(self._save_coin_samples, "_tmp_coin_bin.png")
                if os.path.exists(tmp_coin):
                    os.replace(tmp_coin, os.path.join(self._save_coin_samples, base + "_bin.png"))
            except Exception:
                pass

        if self._coins_log_path:
            try:
                os.makedirs(os.path.dirname(self._coins_log_path) or ".", exist_ok=True)
                ts_iso = datetime.now().isoformat(timespec="seconds")
                epoch = int(now)
                with open(self._coins_log_path, "a", encoding="utf-8") as handle:
                    if handle.tell() == 0:
                        handle.write("time_iso,epoch,wave,coins_decimal,conf,pretty\n")
                    coins_decimal = str(coins_eff) if coins_eff is not None else ""
                    handle.write(
                        f"{ts_iso},{epoch},{wave_str},{coins_decimal},{coins_conf:.1f},{coins_str}\n"
                    )
            except Exception:
                pass

        self._last_status_ts = now

    def _make_coins_log_path(self, session_id: str) -> str:
        base = self._coins_log_base or ""
        root, ext = os.path.splitext(base)
        if ext.lower() == ".csv":
            directory = os.path.dirname(root) or "."
            name = os.path.basename(root)
            return os.path.join(directory, f"{name}_{session_id}.csv")
        directory = base or "."
        return os.path.join(directory, f"coins_{session_id}.csv")


__all__ = ["StateChangeTracker", "StatusReporter"]
