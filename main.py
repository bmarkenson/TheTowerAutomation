#!/usr/bin/env python3
# main.py

import threading
import time
from datetime import datetime
import os
import cv2
import argparse
from decimal import Decimal
import json

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
from core.label_tapper import tap_label_now, is_visible
from core.matcher import get_match as _get_match
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
parser.add_argument("--save-coin-samples", default=None,
                    help="Directory to save per-status coin samples: raw frame (and bin winner). Filename encodes coins.")
parser.add_argument("--coins-log", default="logs/coins_per_min.csv",
                    help="CSV path to append coins/min samples (default: logs/coins_per_min.csv)")
parser.add_argument("--no-coins-log", action="store_true",
                    help="Disable coins/min CSV logging")
parser.add_argument("--no-auto-return", action="store_true",
                    help="Disable auto 'Return to Game' press when stuck (default: enabled)")
parser.add_argument("--auto-return-minutes", type=int, default=15,
                    help="Minutes of continuous visibility before auto 'Return to Game' tap (default: 15)")
parser.add_argument("--control-file", default="logs/automation_ctl.json",
                    help="Path to JSON control file for pause/resume/mode (default: logs/automation_ctl.json)")
parser.add_argument("--no-auto-resume", action="store_true",
                    help="Disable automatic resume from PAUSED after timeout")
parser.add_argument("--auto-resume-minutes", type=int, default=15,
                    help="Minutes to auto-resume from PAUSED (default: 15)")
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
    # Coins/min toggle debounce state
    last_coins_toggle_ts = 0.0
    coins_has_min_miss = 0
    COINS_TOGGLE_COOLDOWN = 15.0
    COINS_CONF_FLOOR = 60.0
    # Coins plausibility gate state (guard against absurd jumps)
    last_coins_val = None  # Decimal|None
    COINS_MAX_JUMP_FACTOR = 8.0      # reject increases >8x unless confidence is very high
    COINS_JUMP_CONF_FLOOR = 90.0     # override threshold: accept big jumps at very high confidence
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
    AUTO_RESUME_ENABLED = not args.no_auto_resume
    AUTO_RESUME_SECS = max(0, int(args.auto_resume_minutes)) * 60
    AUTO_RETURN_ENABLED = not args.no_auto_return
    AUTO_RETURN_SECS = max(0, int(args.auto_return_minutes)) * 60

    # Control file state tracking
    ctrl_path = args.control_file
    last_applied_state = None
    last_applied_mode = None
    paused_since_ts = None
    # Return-to-Game tracking
    rtg_visible_since_ts = None

    def _load_and_apply_control():
        nonlocal last_applied_state, last_applied_mode, paused_since_ts
        try:
            if not os.path.exists(ctrl_path):
                return
            with open(ctrl_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            st = (data.get("state") or "").upper()
            md = (data.get("mode") or "").upper()
            # Apply state if valid
            if st in {"RUNNING", "PAUSED", "STOPPED"} and st != last_applied_state:
                try:
                    AUTOMATION.state = st
                    log(f"[CTRL] State set to {st} via control file", "INFO")
                    last_applied_state = st
                    if st == "PAUSED":
                        paused_since_ts = time.time()
                        stop_blind_gem_tapper()
                    else:
                        paused_since_ts = None
                except Exception:
                    pass
            # Apply mode if valid
            if md in {"RETRY", "WAIT", "HOME"} and md != last_applied_mode:
                try:
                    AUTOMATION.mode = md
                    log(f"[CTRL] Mode set to {md} via control file", "INFO")
                    last_applied_mode = md
                except Exception:
                    pass
        except Exception:
            pass

    try:
        while True:
            img = capture_and_save_screenshot(log_capture=False)
            if img is None:
                log("Failed to capture screenshot.", level="FAIL")
                time.sleep(2)
                continue

            # Apply external control (pause/mode) and auto-resume if needed
            _load_and_apply_control()
            is_paused = (str(AUTOMATION.state) == "RunState.PAUSED") or (getattr(AUTOMATION, 'state', None) == 'PAUSED')
            if AUTO_RESUME_ENABLED and is_paused and paused_since_ts is not None:
                if (time.time() - paused_since_ts) >= AUTO_RESUME_SECS:
                    AUTOMATION.state = "RUNNING"
                    is_paused = False
                    paused_since_ts = None
                    log("[CTRL] Auto-resume: State=RUNNING after pause timeout", "INFO")

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
                coins_eff = None  # reset effective coins unless recomputed below
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
                        # If saving, write the chosen bin image to a temp file and rename after we decide the base name
                        coins_debug_tmp = None
                        if args.save_coin_samples:
                            os.makedirs(args.save_coin_samples, exist_ok=True)
                            coins_debug_tmp = os.path.join(args.save_coin_samples, "_tmp_coin_bin.png")

                        coins_val, coins_conf, has_min = detect_coins_from_image(img, debug_out=coins_debug_tmp)
                        # Plausibility gate: ignore improbable multi‑X jumps unless confidence is very high
                        coins_eff = coins_val
                        try:
                            if last_coins_val is not None and coins_val is not None and last_coins_val > 0:
                                ratio = (coins_val / last_coins_val)
                                if ratio > Decimal(str(COINS_MAX_JUMP_FACTOR)) and coins_conf < COINS_JUMP_CONF_FLOOR:
                                    log(f"[COINS] Ignoring implausible jump {format_compact_decimal(last_coins_val)} → {format_compact_decimal(coins_val)} (×{ratio:.2f}, conf={coins_conf:.1f})", "WARN")
                                    coins_eff = last_coins_val
                        except Exception:
                            coins_eff = coins_val
                        # Only perform UI toggling logic if not paused
                        if not is_paused:
                            now_ts = time.time()
                            if has_min:
                                coins_has_min_miss = 0
                            else:
                                # Count only confident misses
                                if coins_conf >= COINS_CONF_FLOOR:
                                    coins_has_min_miss += 1
                                # Toggle only after two consecutive confident misses and cooldown
                                if coins_has_min_miss >= 2 and (now_ts - last_coins_toggle_ts) >= COINS_TOGGLE_COOLDOWN:
                                    tap_label_now("buttons.coin_toggle")
                                    last_coins_toggle_ts = now_ts
                                    coins_has_min_miss = 0
                                    time.sleep(0.6)
                                    img2 = capture_and_save_screenshot(log_capture=False)
                                    if img2 is not None:
                                        coins_val, coins_conf, has_min = detect_coins_from_image(img2, debug_out=coins_debug_tmp)
                                        # Re-apply plausibility after re-capture
                                        coins_eff = coins_val
                                        try:
                                            if last_coins_val is not None and coins_val is not None and last_coins_val > 0:
                                                ratio = (coins_val / last_coins_val)
                                                if ratio > Decimal(str(COINS_MAX_JUMP_FACTOR)) and coins_conf < COINS_JUMP_CONF_FLOOR:
                                                    log(f"[COINS] Ignoring implausible jump {format_compact_decimal(last_coins_val)} → {format_compact_decimal(coins_val)} (×{ratio:.2f}, conf={coins_conf:.1f})", "WARN")
                                                    coins_eff = last_coins_val
                                        except Exception:
                                            pass
                    except Exception:
                        coins_val, coins_conf = None, -1.0
                        coins_eff = None
                wave_str = str(wave) if wave is not None else "—"
                # Use plausibility-filtered value for reporting
                coins_str = format_compact_decimal(coins_eff) if coins_eff is not None else "—"
                menu_str = menu or "—"
                sec_str = ", ".join(sorted(secondary)) if secondary else "—"
                ovl_str = ", ".join(sorted(overlays)) if overlays else "—"
                # Compact automation hint: append '/PAUSED' to UI state when automation is paused
                state_str = f"{new_state}/PAUSED" if 'is_paused' in locals() and is_paused else new_state
                log(f"[STATUS] State={state_str} | Wave={wave_str} | Coins/min={coins_str} | Menu={menu_str} | Secondary=[{sec_str}] | Overlays=[{ovl_str}]", "INFO")
                # Update last accepted coins value
                try:
                    if coins_eff is not None:
                        last_coins_val = coins_eff
                except Exception:
                    pass
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

                # Optionally persist coins sample (raw + overlay + chosen bin)
                if args.save_coin_samples:
                    try:
                        ts_c = datetime.now().strftime("%Y%m%d-%H%M%S")
                        base_c = f"{ts_c}_coins-{coins_str}"
                        img_path_c = os.path.join(args.save_coin_samples, base_c + ".png")
                        note_path_c = os.path.join(args.save_coin_samples, base_c + ".txt")
                        cv2.imwrite(img_path_c, img)
                        try:
                            with open(note_path_c, "w", encoding="utf-8") as f:
                                f.write(f"state={new_state}\nmenu={menu_str}\nsecondary={sec_str}\noverlays={ovl_str}\ncoins={coins_str}\ncoins_conf={coins_conf:.1f}\nhas_min={('yes' if 'has_min' in locals() and has_min else 'no')}\n")
                        except Exception:
                            pass
                        # ROI overlay for coins
                        try:
                            overlay_c = img.copy()
                            cm = get_clickmap()
                            entry_c = resolve_dot_path("_shared_match_regions.coins", cm) or {}
                            mr_c = entry_c.get("match_region") if isinstance(entry_c, dict) else None
                            if mr_c:
                                x, y, w, h = int(mr_c.get("x",0)), int(mr_c.get("y",0)), int(mr_c.get("w",0)), int(mr_c.get("h",0))
                                cv2.rectangle(overlay_c, (x,y), (x+w, y+h), (0,255,0), 2)
                            cv2.imwrite(os.path.join(args.save_coin_samples, base_c + "_overlay.png"), overlay_c)
                        except Exception:
                            pass
                        # Rename chosen bin image if we wrote a temp one earlier
                        tmp_coin = os.path.join(args.save_coin_samples, "_tmp_coin_bin.png")
                        if os.path.exists(tmp_coin):
                            os.replace(tmp_coin, os.path.join(args.save_coin_samples, base_c + "_bin.png"))
                    except Exception:
                        pass

                # Append coins sample for graphing
                if coins_log_path:
                    try:
                        os.makedirs(os.path.dirname(coins_log_path) or '.', exist_ok=True)
                        ts_iso = datetime.now().isoformat(timespec='seconds')
                        epoch = int(now)
                        with open(coins_log_path, 'a', encoding='utf-8') as f:
                            if f.tell() == 0:
                                f.write("time_iso,epoch,wave,coins_decimal,conf,pretty\n")
                            coins_decimal = str(coins_eff) if coins_eff is not None else ""
                            f.write(f"{ts_iso},{epoch},{wave_str},{coins_decimal},{coins_conf:.1f},{coins_str}\n")
                    except Exception:
                        pass
                last_status_ts = now

            # Auto Return-to-Game: if the button is persistently visible while not RUNNING, tap it
            if AUTO_RETURN_ENABLED and not is_paused and new_state != "RUNNING":
                try:
                    # Primary visibility via label_tapper; fallback to matcher with a slightly softer threshold
                    visible = is_visible("buttons.return_to_game", screenshot=img)
                    if not visible:
                        try:
                            pt, conf = _get_match("buttons.return_to_game", screenshot=img)
                            visible = bool(pt) and (conf >= 0.85)
                            if visible:
                                log(f"[AUTO] Return-to-Game matched via fallback (conf={conf:.2f})", "DEBUG")
                        except Exception:
                            visible = False

                    if visible:
                        if rtg_visible_since_ts is None:
                            rtg_visible_since_ts = time.time()
                            mins = (AUTO_RETURN_SECS // 60) if AUTO_RETURN_SECS > 0 else 0
                            log(f"[AUTO] Return-to-Game detected; starting timer ({mins}m)", "INFO")
                        elif (time.time() - rtg_visible_since_ts) >= AUTO_RETURN_SECS > 0:
                            elapsed = int(time.time() - rtg_visible_since_ts)
                            log(f"[AUTO] Return-to-Game visible for {elapsed}s — tapping now.", "ACTION")
                            tap_label_now("buttons.return_to_game")
                            rtg_visible_since_ts = None
                    else:
                        if rtg_visible_since_ts is not None:
                            elapsed = int(time.time() - rtg_visible_since_ts)
                            log(f"[AUTO] Return-to-Game disappeared before threshold — cancelling timer (after {elapsed}s)", "INFO")
                            rtg_visible_since_ts = None
                except Exception:
                    # On any error, reset and continue
                    rtg_visible_since_ts = None

            # If auto-return timer was running but UI transitioned (e.g., user pressed it manually), cancel and log once
            if rtg_visible_since_ts is not None and (new_state == "RUNNING" or is_paused or not AUTO_RETURN_ENABLED):
                try:
                    elapsed = int(time.time() - rtg_visible_since_ts)
                except Exception:
                    elapsed = 0
                reason = (
                    "state RUNNING" if new_state == "RUNNING" else
                    "paused/disabled"
                )
                log(f"[AUTO] Return-to-Game timer cancelled due to {reason} (after {elapsed}s)", "INFO")
                rtg_visible_since_ts = None

            # Handle known states (skip actions while paused)
            if not is_paused:
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
