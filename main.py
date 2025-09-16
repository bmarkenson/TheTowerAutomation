#!/usr/bin/env python3
# main.py

import threading
import time
from datetime import datetime
import os
import cv2
import argparse
from decimal import Decimal
import json  # kept for other modules if needed (supervisor now handles control file)

from core.watchdog import watchdog_process_check, ensure_adb_connected
from core.ss_capture import capture_and_save_screenshot
from core.run_state import AUTOMATION
from core.state_detector import detect_state_and_overlays
from handlers.game_over_handler import handle_game_over
from handlers.home_screen_handler import handle_home_screen
from handlers.ad_gem_handler import handle_ad_gem, stop_blind_gem_tapper
from handlers.daily_gem_handler import handle_daily_gem
from utils.logger import log
from utils.wave_detector import detect_wave_number_from_image, set_wave_hint  # use detect_* for conf + debug
from utils.coin_detector import get_coins_from_image, detect_coins_from_image, format_compact_decimal
from core.label_tapper import tap_label_now, is_visible
from core.automation_supervisor import AutomationSupervisor
from core.clickmap_access import get_clickmap, resolve_dot_path
from automation.missions.manager import MissionManager
from automation.missions import get_mission
from automation.strategies import get_strategy
from automation.missions.yaml_mission import YamlMission

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
parser.add_argument("--fast-game-over", action="store_true",
                    help="Skip More Stats capture on GAME_OVER (default: enabled when --mission != none)")
parser.add_argument("--full-game-over", action="store_true",
                    help="Force capture of More Stats on GAME_OVER even when a mission is active")
parser.add_argument("--mission", default="none", help="Mission to run (none|demon_nuke|nuke|demon_mode)")
parser.add_argument("--strategy", default="none", help="Run-time strategy (none|aggressive|coins|safe)")
parser.add_argument("--mission-config", default=None, help="Path to YAML mission config (overrides --mission)")
parser.add_argument("--strategy-config", default=None, help="Path to YAML strategy config (reserved)")
parser.add_argument("--wait-on-start", action="store_true", help="Start with ExecMode=WAIT (pause auto progression)")
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
    # Try to establish ADB connectivity upfront to avoid first-capture failures
    if ensure_adb_connected():
        time.sleep(2)
    # Start watchdog after initial connect to avoid duplicate connect logs
    threading.Thread(target=watchdog_process_check, daemon=True).start()
    # Apply startup wait mode if requested
    if args.wait_on_start:
        try:
            from core.run_state import ExecMode
            AUTOMATION.mode = ExecMode.WAIT
            log("[CTRL] Startup flag: ExecMode set to WAIT", "INFO")
        except Exception:
            pass

    last_ui_state = None
    last_secondary_states = None  # set[str] (non-menu only)
    last_menu = None              # str|None (mutually exclusive)
    last_overlays = None          # set[str]
    last_status_ts = 0.0
    # Supervisor encapsulates control-file, pause/auto-resume, coins logic, auto-return
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

    sup = AutomationSupervisor(
        control_file=args.control_file,
        auto_resume_secs=AUTO_RESUME_SECS,
        auto_resume_enabled=AUTO_RESUME_ENABLED,
        auto_return_secs=AUTO_RETURN_SECS,
        auto_return_enabled=AUTO_RETURN_ENABLED,
        auto_return_conf_threshold=0.85,
        coins_toggle_cooldown=15.0,
        coins_conf_floor=60.0,
        coins_max_jump_factor=8.0,
        coins_jump_conf_floor=90.0,
    )
    # Mission/Strategy (optional; default to no-ops to avoid behavior changes)
    if args.mission_config:
        try:
            mission = YamlMission.from_file(args.mission_config)
            log(f"[MISSION] Loaded YAML mission from {args.mission_config}", "INFO")
        except Exception as e:
            log(f"[MISSION] Failed to load YAML mission: {e}", "ERROR")
            mission = get_mission(args.mission)
    else:
        mission = get_mission(args.mission)
    strategy = get_strategy(args.strategy)
    mission_mgr = MissionManager(mission, strategy)
    mission_mgr.start()
    # Decide default behavior for game-over capture: fast by default when a mission is active
    MISSION_ACTIVE = bool((args.mission and args.mission.lower() != "none") or args.mission_config)
    FAST_GAME_OVER = args.fast_game_over or (MISSION_ACTIVE and not args.full_game_over)

    # (control file + auto-resume now handled by AutomationSupervisor)

    try:
        while True:
            img = capture_and_save_screenshot(log_capture=False)
            if img is None:
                # One-shot reconnect attempt to avoid per-loop adb checks
                if ensure_adb_connected():
                    time.sleep(1)
                    img = capture_and_save_screenshot(log_capture=False)
                if img is None:
                    log("Failed to capture screenshot.", level="FAIL")
                    time.sleep(2)
                    continue

            # Apply external control (pause/mode) and auto-resume if needed
            sup.apply_control()
            is_paused = sup.is_paused

            # Detect current state from image
            detection = detect_state_and_overlays(img, log_matches=args.match_trace)
            new_state = detection["state"]           # e.g., "GAME_OVER", "HOME_SCREEN"
            menu = detection.get("menu") or None     # 'ATTACK_MENU', etc., or None
            secondary = set(detection.get("secondary_states") or [])  # already excludes menu
            overlays = set(detection.get("overlays") or [])
            # Auto-pause when tournament detected
            if "TOURNAMENT" in secondary:
                try:
                    from core.run_state import ExecMode
                    if AUTOMATION.mode != ExecMode.WAIT:
                        AUTOMATION.mode = ExecMode.WAIT
                        log("[CTRL] Tournament detected — ExecMode set to WAIT", "INFO")
                except Exception:
                    pass

            # Mission manager signals and per-frame hooks (non-invasive)
            mission_mgr.maybe_run_start(detection)
            mission_mgr.on_state(detection)

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
                        coins_val, coins_conf, has_min, coins_eff = sup.process_coins(
                            img,
                            coins_val,
                            coins_conf,
                            has_min,
                            debug_out=coins_debug_tmp,
                        )
                    except Exception:
                        coins_val, coins_conf = None, -1.0
                        coins_eff = None
                wave_str = str(wave) if wave is not None else "—"
                # Use plausibility-filtered value for reporting
                coins_str = format_compact_decimal(coins_eff) if coins_eff is not None else "—"
                menu_str = menu or "—"
                sec_str = ", ".join(sorted(secondary)) if secondary else "—"
                ovl_str = ", ".join(sorted(overlays)) if overlays else "—"
                # Compact automation hint: append '/PAUSED' when automation is paused
                state_str = sup.format_state(new_state)
                log(f"[STATUS] State={state_str} | Wave={wave_str} | Coins/min={coins_str} | Menu={menu_str} | Secondary=[{sec_str}] | Overlays=[{ovl_str}]", "INFO")
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

            # Auto Return-to-Game
            sup.auto_return_check(img, new_state)

            # Mission/Strategy tick (execute only when not paused; executor lives inside)
            if not is_paused:
                mission_mgr.tick(img, detection)

            # Handle known states (skip actions while paused)
            if not is_paused:
                if new_state == "GAME_OVER":
                    log("Detected GAME_OVER. Executing handler.", "INFO")
                    handle_game_over(capture_stats=(not FAST_GAME_OVER))
                    mission_mgr.on_game_over()
                    # Rotate coins log for the new run segment
                    if not args.no_coins_log:
                        coins_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
                        coins_log_path = _make_coins_log_path(coins_session_id)
                        log(f"[COINS] Started new coins log: {coins_log_path}", "INFO")
                elif new_state == "HOME_SCREEN":
                    log("Detected HOME_SCREEN. Executing handler.", "INFO")
                    handle_home_screen(restart_enabled=AUTO_START_ENABLED)
                    mission_mgr.on_home()

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
