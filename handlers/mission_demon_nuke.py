# handlers/mission_demon_nuke.py

import time
from core.ss_capture import capture_and_save_screenshot
from core.clickmap_access import tap_now
from core.floating_button_detector import detect_floating_buttons, tap_floating_button
from core.state_detector import detect_state_and_overlays
from core.label_tapper import tap_label_now
from utils.logger import log


def run_demon_nuke_strategy():
    log("[MISSION] Starting Demon Mode -> Nuke -> Restart mission", "ACTION")

    # Step 1: Wait for RUNNING state
    while True:
        screen = capture_and_save_screenshot()
        result = detect_state_and_overlays(screen)
        if result["state"] == "RUNNING":
            log("[MISSION] Game is in RUNNING state", "INFO")
            break
        log("[MISSION] Waiting for RUNNING state...", "DEBUG")
        time.sleep(2)

    # Step 2: Wait for demon_mode button
    while True:
        screen = capture_and_save_screenshot()
        buttons = detect_floating_buttons(screen)
        if any(b["name"] == "floating_buttons.demon_mode" for b in buttons):
            log("[MISSION] Demon Mode button detected!", "INFO")
            tap_floating_button("floating_buttons.demon_mode", buttons)
            break
        log("[MISSION] Waiting for Demon Mode button...", "DEBUG")
        time.sleep(1)

    # Step 3: Wait ~30 seconds
    log("[MISSION] Demon Mode activated. Waiting 10s...", "INFO")
    time.sleep(10)

    # Step 4: Wait for Nuke button
    while True:
        screen = capture_and_save_screenshot()
        buttons = detect_floating_buttons(screen)
        if any(b["name"] == "floating_buttons.nuke" for b in buttons):
            log("[MISSION] Nuke button detected!", "INFO")
            tap_floating_button("floating_buttons.nuke", buttons)
            break
        log("[MISSION] Waiting for Nuke button...", "DEBUG")
        time.sleep(1)

    # Step 5: Wait a bit more
    log("[MISSION] Nuke launched. Waiting 5s before restart...", "INFO")
    time.sleep(1)

    # Step 6: End game sequence
    screen = capture_and_save_screenshot()
    result = detect_state_and_overlays(screen)
    if "MENU_OPEN" not in result["overlays"]:
        log("[MISSION] Menu is closed — opening it", "DEBUG")
        tap_now("overlays.toggle_menu")
        time.sleep(1)

    try:
        tap_label_now("overlays.end_round")
    except Exception as e:
        log(f"[MISSION] Failed to tap End Round: {e}", "WARN")
    time.sleep(1)

    try:
        screen = capture_and_save_screenshot()
        tap_label_now("buttons.yes:end_round")
    except Exception as e:
        log(f"[MISSION] Confirm Yes not visible: {e}", "WARN")
    time.sleep(1)

    try:
        screen = capture_and_save_screenshot()
        tap_label_now("buttons.retry:game_over")
    except Exception as e:
        log(f"[MISSION] Retry button not visible: {e}", "WARN")

    log("[MISSION] Demon-Nuke strategy complete", "SUCCESS")
