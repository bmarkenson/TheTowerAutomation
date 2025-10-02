# core/status_report.py
"""
Logic for generating periodic status reports and saving debug samples.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple

import cv2
import numpy as np
from numpy.typing import NDArray

from utils.logger import log
from utils.wave_detector import detect_wave_number_from_image
from utils.coin_detector import detect_coins_from_image, format_compact_decimal
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.automation_supervisor import AutomationSupervisor, CoinsTotalSnapshot


Frame = NDArray[np.uint8]


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


@dataclass
class SamplePaths:
    base_path: Path
    note_path: Path
    overlay_path: Path
    tmp_bin: Optional[Path]


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
        self._save_wave_samples = Path(save_wave_samples) if save_wave_samples else None
        self._save_coin_samples = Path(save_coin_samples) if save_coin_samples else None
        self._coins_log_enabled = coins_log_enabled
        self._coins_log_base = coins_log_base

        self._last_status_ts: float = 0.0
        self._coins_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._coins_log_path: Optional[str] = None
        if self._coins_log_enabled:
            self._coins_log_path = self._make_coins_log_path(self._coins_session_id)

        self._coins_last_total_value: Optional[Decimal] = None
        self._coins_last_total_ts: Optional[float] = None
        self._coins_last_total_str: Optional[str] = None
        self._coins_hourly_rate: Optional[Decimal] = None
        self._coins_last_run_gain: Optional[Decimal] = None

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
        img: Frame,
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
        total_snapshot: Optional[CoinsTotalSnapshot] = None
        coins_debug_tmp: Optional[Path] = None
        per_min_refresh: Optional[Tuple[Optional[Decimal], float, bool]] = None

        if ui_state == "RUNNING":
            debug_out = self._prepare_tmp_path(self._save_wave_samples, "_tmp_bin.png")
            wave, wave_conf = detect_wave_number_from_image(img, debug_out=str(debug_out) if debug_out else None)

            try:
                coins_debug_tmp = self._prepare_tmp_path(self._save_coin_samples, "_tmp_coin_bin.png")

                coins_val, coins_conf, has_min = detect_coins_from_image(
                    img, debug_out=str(coins_debug_tmp) if coins_debug_tmp else None
                )

                if self._supervisor.should_capture_total(now):
                    total_snapshot, per_min_refresh = self._supervisor.capture_total_snapshot(
                        current_img=img,
                        current_value=coins_val,
                        current_confidence=coins_conf,
                        current_has_min=has_min,
                        debug_out=str(coins_debug_tmp) if coins_debug_tmp else None,
                    )
                    if per_min_refresh is not None:
                        coins_val, coins_conf, has_min = per_min_refresh

                coins_val, coins_conf, has_min, coins_eff = self._supervisor.process_coins(
                    img,
                    coins_val,
                    coins_conf,
                    has_min,
                    debug_out=str(coins_debug_tmp) if coins_debug_tmp else None,
                )
            except Exception:
                coins_val, coins_conf, coins_eff = None, -1.0, None
                total_snapshot = None

        if total_snapshot is not None:
            self._handle_total_snapshot(
                total_snapshot,
                ui_state=ui_state,
                menu=menu,
                secondary=secondary,
                overlays=overlays,
            )

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
            self._persist_sample(
                img=img,
                paths=self._build_sample_paths(self._save_wave_samples, f"wave-{wave_str}"),
                overlay_paths=[("_shared_match_regions.wave_number", (0, 0, 255))],
                note_lines=[
                    f"state={ui_state}",
                    f"menu={menu_str}",
                    f"secondary={sec_str}",
                    f"overlays={ovl_str}",
                    f"wave={wave_str}",
                    f"conf={wave_conf:.1f}",
                    f"coins={coins_str}",
                    f"coins_conf={coins_conf:.1f}",
                ],
            )

        if self._save_coin_samples:
            self._persist_sample(
                img=img,
                paths=self._build_sample_paths(self._save_coin_samples, f"coins-{coins_str}"),
                overlay_paths=[("_shared_match_regions.coins", (0, 255, 0))],
                note_lines=[
                    f"state={ui_state}",
                    f"menu={menu_str}",
                    f"secondary={sec_str}",
                    f"overlays={ovl_str}",
                    f"coins={coins_str}",
                    f"coins_conf={coins_conf:.1f}",
                    f"has_min={'yes' if has_min else 'no'}",
                ],
            )

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

    def _handle_total_snapshot(
        self,
        snapshot: CoinsTotalSnapshot,
        *,
        ui_state: str,
        menu: Optional[str],
        secondary: Set[str],
        overlays: Set[str],
    ) -> None:
        self._coins_last_total_value = snapshot.value
        self._coins_last_total_ts = snapshot.timestamp
        self._coins_last_total_str = format_compact_decimal(snapshot.value)

        delta_prev: Optional[Decimal] = None
        delta_hours: Optional[float] = None
        coins_hr: Optional[Decimal] = None

        if snapshot.previous_value is not None and snapshot.previous_timestamp is not None:
            delta_prev = snapshot.value - snapshot.previous_value
            dt_seconds = snapshot.timestamp - snapshot.previous_timestamp
            if dt_seconds > 0:
                delta_hours = dt_seconds / 3600.0
                try:
                    hours_decimal = Decimal(str(dt_seconds)) / Decimal("3600")
                    if hours_decimal > 0:
                        coins_hr = delta_prev / hours_decimal
                except Exception:
                    coins_hr = None

        self._coins_hourly_rate = coins_hr

        run_gain: Optional[Decimal] = None
        if snapshot.previous_run_start_value is not None:
            run_gain = snapshot.value - snapshot.previous_run_start_value
        self._coins_last_run_gain = run_gain

        def _fmt_signed(val: Optional[Decimal]) -> str:
            if val is None:
                return "—"
            sign = "+" if val >= 0 else "-"
            return f"{sign}{format_compact_decimal(val.copy_abs())}"

        report_total = snapshot.reason in {"startup", "hourly"}
        report_rate = snapshot.reason == "hourly" and coins_hr is not None

        if report_total:
            total_parts = [f"Total={self._coins_last_total_str}"]
            if run_gain is not None:
                total_parts.append(f"RunΔ={_fmt_signed(run_gain)}")
            if delta_prev is not None and delta_hours is not None and snapshot.reason == "hourly":
                total_parts.append(f"Δprev={_fmt_signed(delta_prev)} over {delta_hours:.2f}h")
            log(f"[STATUS] " + " | ".join(total_parts), "INFO")

        if report_rate and delta_hours is not None:
            rate_str = format_compact_decimal(coins_hr)
            log(
                f"[STATUS] Coins/hr≈{rate_str} over {delta_hours:.2f}h",
                "INFO",
            )

        if snapshot.reason not in {"startup", "hourly"}:
            parts = [f"Total={self._coins_last_total_str}"]
            if run_gain is not None:
                parts.append(f"RunΔ={_fmt_signed(run_gain)}")
            log(
                f"[COINS] Total snapshot ({snapshot.reason}) — " + " | ".join(parts),
                "INFO",
            )

        if self._save_coin_samples and snapshot.image is not None:
            menu_str = menu or "—"
            sec_str = ", ".join(sorted(secondary)) if secondary else "—"
            ovl_str = ", ".join(sorted(overlays)) if overlays else "—"
            descriptor = f"total-{snapshot.reason}-{self._coins_last_total_str}"
            try:
                self._persist_sample(
                    img=snapshot.image,
                    paths=self._build_sample_paths(self._save_coin_samples, descriptor),
                    overlay_paths=[("_shared_match_regions.coins", (0, 255, 0))],
                    note_lines=[
                        f"state={ui_state}",
                        f"menu={menu_str}",
                        f"secondary={sec_str}",
                        f"overlays={ovl_str}",
                        f"reason={snapshot.reason}",
                        f"total={self._coins_last_total_str}",
                        f"delta_prev={_fmt_signed(delta_prev)}",
                        f"coins_hr={format_compact_decimal(coins_hr) if coins_hr is not None else '—'}",
                        f"run_gain={_fmt_signed(run_gain)}",
                    ],
                )
            except Exception:
                pass

    def _make_coins_log_path(self, session_id: str) -> str:
        base = self._coins_log_base or ""
        root, ext = os.path.splitext(base)
        if ext.lower() == ".csv":
            directory = os.path.dirname(root) or "."
            name = os.path.basename(root)
            return os.path.join(directory, f"{name}_{session_id}.csv")
        directory = base or "."
        return os.path.join(directory, f"coins_{session_id}.csv")

    def _prepare_tmp_path(self, root: Optional[Path], tmp_name: str) -> Optional[Path]:
        if root is None:
            return None
        root.mkdir(parents=True, exist_ok=True)
        return root / tmp_name

    def _build_sample_paths(self, root: Path, descriptor: str) -> SamplePaths:
        root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = root / f"{ts}_{descriptor}"
        return SamplePaths(
            base_path=base.with_suffix(".png"),
            note_path=base.with_suffix(".txt"),
            overlay_path=base.with_name(base.name + "_overlay.png"),
            tmp_bin=root / "_tmp_bin.png" if "wave" in descriptor else root / "_tmp_coin_bin.png",
        )

    def _persist_sample(
        self,
        *,
        img: Frame,
        paths: SamplePaths,
        overlay_paths: Iterable[tuple[str, tuple[int, int, int]]],
        note_lines: Iterable[str],
    ) -> None:
        try:
            cv2.imwrite(str(paths.base_path), img)
            self._write_note(paths.note_path, note_lines)
            overlay = img.copy()
            for dot_path, colour in overlay_paths:
                region = _resolve_match_region(dot_path)
                if region:
                    x = int(region.get("x", 0))
                    y = int(region.get("y", 0))
                    w = int(region.get("w", 0))
                    h = int(region.get("h", 0))
                    cv2.rectangle(overlay, (x, y), (x + w, y + h), colour, 2)
            cv2.imwrite(str(paths.overlay_path), overlay)
            if paths.tmp_bin and paths.tmp_bin.exists():
                os.replace(paths.tmp_bin, paths.base_path.with_name(paths.base_path.stem + "_bin.png"))
        except Exception:
            pass

    @staticmethod
    def _write_note(path: Path, lines: Iterable[str]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except Exception:
            pass


@lru_cache(maxsize=16)
def _resolve_match_region(dot_path: str) -> Optional[dict]:
    entry = resolve_dot_path(dot_path, get_clickmap()) or {}
    if isinstance(entry, dict):
        region = entry.get("match_region")
        if isinstance(region, dict):
            return region
    return None


__all__ = ["StateChangeTracker", "StatusReporter"]
