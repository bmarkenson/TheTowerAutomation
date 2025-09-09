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
from utils.coin_detector import get_coins_from_image, format_compact_decimal
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
parser.add_argument("--coins-log", default=None,
                    help="Optional CSV to append coins/min samples: time_iso,epoch,wave,coins_decimal,conf,pretty")
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
                    # Coins/min OCR
                    try:
                        coins_val, coins_conf = get_coins_from_image(img)
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
                # Append coins sample for graphing
                if args.coins_log:
                    try:
                        os.makedirs(os.path.dirname(args.coins_log) or '.', exist_ok=True)
                        ts_iso = datetime.now().isoformat(timespec='seconds')
                        epoch = int(now)
                        with open(args.coins_log, 'a', encoding='utf-8') as f:
                            if f.tell() == 0:
                                f.write("time_iso,epoch,wave,coins_decimal,conf,pretty\n")
                            coins_decimal = str(coins_val) if coins_val is not None else ""
                            f.write(f"{ts_iso},{epoch},{wave_str},{coins_decimal},{coins_conf:.1f},{coins_str}\n")
                    except Exception:
                        pass
                    # Save ROI overlay for the wave-number region (red box)
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
                last_status_ts = now

            # Handle known states
            if new_state == "GAME_OVER":
                log("Detected GAME_OVER. Executing handler.", "INFO")
                handle_game_over()
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
