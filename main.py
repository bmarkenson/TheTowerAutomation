#!/usr/bin/env python3
# main.py

import threading
import time
from datetime import datetime
import os
import cv2
import argparse

from core.watchdog import watchdog_process_check
from core.ss_capture import capture_and_save_screenshot
from core.automation_state import AUTOMATION
from core.state_detector import detect_state_and_overlays
from handlers.game_over_handler import handle_game_over
from handlers.home_screen_handler import handle_home_screen
from handlers.ad_gem_handler import handle_ad_gem, stop_blind_gem_tapper
from handlers.daily_gem_handler import handle_daily_gem
from utils.logger import log
from utils.wave_detector import detect_wave_number_from_image, set_wave_hint  # use detect_* for conf + debug
from utils.coin_detector import get_coins_from_image, detect_coins_from_image, format_compact_decimal
from core.label_tapper import tap_label_now
from core.clickmap_access import get_clickmap, resolve_dot_path

SCREENSHOT_PATH = "screenshots/latest.png"

parser = argparse.ArgumentParser()
parser.add_argument("--no-restart", action="store_true", help="Disable auto restart on home screen")
parser.add_argument("--match-trace", action="store_true", help="Emit per-frame match logs from detector")
parser.add_argument("--status-interval", type=int, default=60, help="Seconds between status summaries (0=disable)")
parser.add_argument("--reset-wave-hint", action="store_true",
                    help="Reset the wave OCR monotonic/time-weighted hint at startup")  # <-- new flag
parser.add_argument("--save-wave-samples", default=None,
                    help="Directory to save per-status wave samples: raw frame (and bin winner). Filename encodes wave.")
parser.add_argument("--coins-log", default="logs/coins_per_min.csv",
                    help="CSV path to append coins/min samples (default: logs/coins_per_min.csv)")
parser.add_argument("--no-coins-log", action="store_true",
                    help="Disable coins/min CSV logging")
args = parser.parse_args()
AUTO_START_ENABLED = not args.no_restart
STATUS_INTERVAL = max(0, args.status_interval)
log(f"AUTO_START_ENABLED = {AUTO_START_ENABLED}", "DEBUG")

# If requested, clear the wave hint so new runs start fresh (monotonic scorer won't reject small values)
if args.reset_wave_hint:
    set_wave_hint(None)
    log("[WAVE] Reset wave hint at startup", "DEBUG")


def main():
    log("Starting main heartbeat loop.", level="INFO")
    threading.Thread(target=watchdog_process_check, daemon=True).start()

    last_ui_state = None
    last_secondary_states = None  # set[str] (non-menu only)
    last_menu = None              # str|None (mutually exclusive)
    last_overlays = None          # set[str]
    last_status_ts = 0.0
    # Resolve coins log path (default enabled unless --no-coins-log). We generate
    # a per-run file using a session id, and rotate it after GAME_OVER restarts.
    def _make_coins_log_path(session_id: str) -> str:
        base = args.coins_log or "logs/coins_per_min.csv"
        # If base looks like a CSV file, inject the session id before extension.
        root, ext = os.path.splitext(base)
        if ext.lower() == ".csv":
            directory = os.path.dirname(root) or "."
            name = os.path.basename(root)
            path = os.path.join(directory, f"{name}_{session_id}.csv")
        else:
            # Treat base as a directory; create a default filename
            directory = base
            path = os.path.join(directory, f"coins_{session_id}.csv")
        return path

    coins_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    coins_log_path = None if args.no_coins_log else _make_coins_log_path(coins_session_id)

    try:
        while True:
            img = capture_and_save_screenshot(log_capture=False)
            if img is None:
                log("Failed to capture screenshot.", level="FAIL")
                time.sleep(2)
                continue

            # Detect current state from image
            detection = detect_state_and_overlays(img, log_matches=args.match_trace)
            new_state = detection["state"]           # e.g., "GAME_OVER", "HOME_SCREEN"
            menu = detection.get("menu") or None     # 'ATTACK_MENU', etc., or None
            secondary = set(detection.get("secondary_states") or [])  # already excludes menu
            overlays = set(detection.get("overlays") or [])

            # Primary state change
            if new_state != last_ui_state:
                log(f"UI state change: {last_ui_state} → {new_state}", "STATE")
                last_ui_state = new_state

            # Menu change (mutually exclusive)
            if menu != last_menu:
                if menu and not last_menu:
                    log(f"Menu opened: {menu}", "MATCH")
                elif last_menu and not menu:
                    log(f"Menu closed: {last_menu}", "MATCH")
                else:
                    log(f"Menu switched: {last_menu} → {menu}", "MATCH")
                last_menu = menu

            # Secondary state changes (non-menu)
            if last_secondary_states is None:
                if secondary:
                    log(f"Secondary states now: {sorted(secondary)}", "MATCH")
            else:
                sec_added = sorted(secondary - last_secondary_states)
                sec_removed = sorted(last_secondary_states - secondary)
                if sec_added:
                    log(f"Secondary states added: {sec_added}", "MATCH")
                if sec_removed:
                    log(f"Secondary states removed: {sec_removed}", "MATCH")
            last_secondary_states = secondary

            # Overlay changes
            if last_overlays is None:
                if overlays:
                    log(f"Overlays now: {sorted(overlays)}", "MATCH")
            else:
                added = sorted(overlays - last_overlays)
                removed = sorted(last_overlays - overlays)
                if added:
                    log(f"Overlays added: {added}", "MATCH")
                if removed:
                    log(f"Overlays removed: {removed}", "MATCH")
            last_overlays = overlays

            # Periodic status heartbeat (compute wave from the SAME img)
            now = time.time()
            if STATUS_INTERVAL and (now - last_status_ts >= STATUS_INTERVAL):
                wave = None
                wave_conf = -1.0
                coins_val = None
                coins_conf = -1.0
                if new_state == "RUNNING":
                    # Reuse current frame; also save winner bin if we're writing samples
                    debug_out = None
                    if args.save_wave_samples:
                        os.makedirs(args.save_wave_samples, exist_ok=True)
                        # debug_out path finalized after wave_str; use a temp first
                        debug_out = os.path.join(args.save_wave_samples, "_tmp_bin.png")
                    wave, wave_conf = detect_wave_number_from_image(img, debug_out=debug_out)
                    # Coins/min OCR with per-min toggle if needed
                    try:
                        coins_val, coins_conf, has_min = detect_coins_from_image(img)
                        if not has_min:
                            # Attempt one toggle to switch display, then re-capture once
                            tap_label_now("buttons.coin_toggle")
                            time.sleep(0.6)
                            img2 = capture_and_save_screenshot(log_capture=False)
                            if img2 is not None:
                                coins_val, coins_conf, has_min = detect_coins_from_image(img2)
                    except Exception:
                        coins_val, coins_conf = None, -1.0
                wave_str = str(wave) if wave is not None else "—"
                coins_str = format_compact_decimal(coins_val) if coins_val is not None else "—"
                menu_str = menu or "—"
                sec_str = ", ".join(sorted(secondary)) if secondary else "—"
                ovl_str = ", ".join(sorted(overlays)) if overlays else "—"
                log(f"[STATUS] State={new_state} | Wave={wave_str} | Coins/min={coins_str} | Menu={menu_str} | Secondary=[{sec_str}] | Overlays=[{ovl_str}]", "INFO")
                # Optionally persist the actual input image alongside a debug note
                if args.save_wave_samples:
                    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                    base = f"{ts}_wave-{wave_str}"
                    img_path = os.path.join(args.save_wave_samples, base + ".png")
                    note_path = os.path.join(args.save_wave_samples, base + ".txt")
                    cv2.imwrite(img_path, img)
                    try:
                        with open(note_path, "w", encoding="utf-8") as f:
                            f.write(f"state={new_state}\nmenu={menu_str}\nsecondary={sec_str}\noverlays={ovl_str}\nwave={wave_str}\nconf={wave_conf:.1f}\ncoins={coins_str}\ncoins_conf={coins_conf:.1f}\n")
                    except Exception:
                        pass
                # Save ROI overlay and rename bin image if sample saving enabled
                if args.save_wave_samples:
                    try:
                        overlay = img.copy()
                        cm = get_clickmap()
                        entry = resolve_dot_path("_shared_match_regions.wave_number", cm) or {}
                        mr = entry.get("match_region") if isinstance(entry, dict) else None
                        if mr:
                            x, y, w, h = int(mr.get("x",0)), int(mr.get("y",0)), int(mr.get("w",0)), int(mr.get("h",0))
                            cv2.rectangle(overlay, (x,y), (x+w, y+h), (0,0,255), 2)
                        cv2.imwrite(os.path.join(args.save_wave_samples, base + "_overlay.png"), overlay)
                    except Exception:
                        pass
                    # If we wrote a temp bin image above, rename it to align with this sample
                    tmp_bin = os.path.join(args.save_wave_samples, "_tmp_bin.png")
                    if os.path.exists(tmp_bin):
                        os.replace(tmp_bin, os.path.join(args.save_wave_samples, base + "_bin.png"))

                # Append coins sample for graphing
                if coins_log_path:
                    try:
                        os.makedirs(os.path.dirname(coins_log_path) or '.', exist_ok=True)
                        ts_iso = datetime.now().isoformat(timespec='seconds')
                        epoch = int(now)
                        with open(coins_log_path, 'a', encoding='utf-8') as f:
                            if f.tell() == 0:
                                f.write("time_iso,epoch,wave,coins_decimal,conf,pretty\n")
                            coins_decimal = str(coins_val) if coins_val is not None else ""
                            f.write(f"{ts_iso},{epoch},{wave_str},{coins_decimal},{coins_conf:.1f},{coins_str}\n")
                    except Exception:
                        pass
                last_status_ts = now

            # Handle known states
            if new_state == "GAME_OVER":
                log("Detected GAME_OVER. Executing handler.", "INFO")
                handle_game_over()
                # Rotate coins log for the new run segment
                if not args.no_coins_log:
                    coins_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
                    coins_log_path = _make_coins_log_path(coins_session_id)
                    log(f"[COINS] Started new coins log: {coins_log_path}", "INFO")
            elif new_state == "HOME_SCREEN":
                log("Detected HOME_SCREEN. Executing handler.", "INFO")
                handle_home_screen(restart_enabled=AUTO_START_ENABLED)

            if "AD_GEMS_AVAILABLE" in overlays:
                handle_ad_gem()
            if "DAILY_GEMS_AVAILABLE" in overlays:
                handle_daily_gem()

            time.sleep(5)  # Ctrl+C interrupts here immediately
    except KeyboardInterrupt:
        log("KeyboardInterrupt — shutting down.", "INFO")
    finally:
        stop_blind_gem_tapper()
        log("Exited cleanly.", "INFO")


if __name__ == "__main__":
    main()
