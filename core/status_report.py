# core/status_report.py
"""
Logic for generating periodic status reports and saving debug samples.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Set

import cv2
import numpy as np
from numpy.typing import NDArray

from utils.logger import log, log_status
from utils.wave_detector import detect_wave_number_from_image
from utils.coin_detector import detect_coins_from_image, format_compact_decimal
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.automation_supervisor import AutomationSupervisor
from core.game_speed import read_game_speed_control


Frame = NDArray[np.uint8]

_GAME_SPEED_OCR_RETRIES = 2


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
    """Periodic RUNNING-state heartbeat with optional diagnostic samples."""

    def __init__(
        self,
        *,
        interval_secs: int,
        supervisor: AutomationSupervisor,
        save_wave_samples: Optional[str],
        save_coin_samples: Optional[str],
    ) -> None:
        self._interval = max(0, int(interval_secs))
        self._supervisor = supervisor
        self._save_wave_samples = Path(save_wave_samples) if save_wave_samples else None
        self._save_coin_samples = Path(save_coin_samples) if save_coin_samples else None

        self._last_status_ts: float = 0.0
        self._game_speed_ocr_misses = 0
        self._coin_rate_samples: list[dict[str, object]] = []

    @property
    def coin_rate_samples(self) -> list[dict[str, object]]:
        """Return a detached copy of the current battle's numeric rate history."""

        return [dict(sample) for sample in self._coin_rate_samples]

    def reset_coin_rate_samples(self) -> None:
        """Start a fresh rate history after an authoritative run boundary."""

        self._coin_rate_samples.clear()

    def request_immediate_report(self) -> None:
        """Make the next captured frame publish a fresh status observation."""

        self._last_status_ts = 0.0
        self._game_speed_ocr_misses = 0

    def maybe_report(
        self,
        *,
        img: Frame,
        ui_state: str,
        menu: Optional[str],
        secondary: Set[str],
        overlays: Set[str],
        wave: Optional[int],
        wave_conf: float,
        now_ts: Optional[float] = None,
        allow_actions: bool = True,
    ) -> None:
        if self._interval == 0:
            return

        now = time.time() if now_ts is None else float(now_ts)
        if now - self._last_status_ts < self._interval:
            return

        coins_val = None
        coins_conf = -1.0
        coins_eff = None
        has_min = False
        coins_debug_tmp: Optional[Path] = None
        game_speed = None
        game_speed_conf = -1.0

        if ui_state != "RUNNING":
            self._game_speed_ocr_misses = 0
            wave = None
            wave_conf = -1.0
        else:
            try:
                speed_reading = read_game_speed_control(img)
                if speed_reading.valid and speed_reading.value is not None:
                    game_speed = speed_reading.value
                    game_speed_conf = speed_reading.confidence
            except Exception:
                game_speed, game_speed_conf = None, -1.0

            # A status report is fed one fresh main-loop frame per call. Defer
            # a missing reading through two more frames so a transient OCR
            # miss does not immediately replace the observed speed. This
            # performs no extra capture or input and keeps the eventual status
            # fields contemporaneous with the frame that is actually logged.
            if game_speed is None:
                if self._game_speed_ocr_misses < _GAME_SPEED_OCR_RETRIES:
                    self._game_speed_ocr_misses += 1
                    return
            self._game_speed_ocr_misses = 0

            # The app already observed this frame's wave. Repeat OCR only when
            # an explicit diagnostic sample requested the winning bin image;
            # never let this auxiliary pass replace the shared observation.
            debug_out = self._prepare_tmp_path(
                self._save_wave_samples,
                "_tmp_bin.png",
            )
            if debug_out is not None:
                try:
                    detect_wave_number_from_image(img, debug_out=str(debug_out))
                except Exception:
                    pass

            try:
                coins_debug_tmp = self._prepare_tmp_path(self._save_coin_samples, "_tmp_coin_bin.png")

                coins_val, coins_conf, has_min = detect_coins_from_image(
                    img, debug_out=str(coins_debug_tmp) if coins_debug_tmp else None
                )

                coins_val, coins_conf, has_min, coins_eff = self._supervisor.process_coins(
                    img,
                    coins_val,
                    coins_conf,
                    has_min,
                    debug_out=str(coins_debug_tmp) if coins_debug_tmp else None,
                    allow_actions=allow_actions,
                )
            except Exception:
                coins_val, coins_conf, coins_eff = None, -1.0, None

        wave_str = str(wave) if wave is not None else "—"
        coins_str = format_compact_decimal(coins_eff) if coins_eff is not None else "—"
        speed_str = f"x{game_speed:.1f}" if game_speed is not None else "—"
        menu_str = menu or "—"
        sec_str = ", ".join(sorted(secondary)) if secondary else "—"
        ovl_str = ", ".join(sorted(overlays)) if overlays else "—"
        state_str = self._supervisor.format_state(ui_state)

        status_summary = (
            f"State={state_str} | Wave={wave_str} | Coins/min={coins_str} | "
            f"Speed={speed_str}"
        )
        log_status(
            status_summary,
            detail=(
                f"[STATUS_DETAIL] {status_summary} | Menu={menu_str} | "
                f"Secondary=[{sec_str}] | Overlays=[{ovl_str}]"
            ),
        )

        if ui_state == "RUNNING" and has_min and coins_eff is not None:
            self._coin_rate_samples.append(
                {
                    "captured_at": datetime.fromtimestamp(now).astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "wave": wave,
                    "coins_per_minute_decimal": str(coins_eff),
                    "display": coins_str,
                    "confidence": round(float(coins_conf), 1),
                    "game_speed": game_speed,
                    "game_speed_confidence": (
                        round(float(game_speed_conf), 1)
                        if game_speed is not None
                        else None
                    ),
                }
            )
            # A missed terminal boundary must not grow process memory forever.
            del self._coin_rate_samples[:-4096]

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
                    f"game_speed={speed_str}",
                    f"game_speed_conf={game_speed_conf:.1f}",
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
                    f"game_speed={speed_str}",
                    f"game_speed_conf={game_speed_conf:.1f}",
                ],
            )

        self._last_status_ts = now

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
