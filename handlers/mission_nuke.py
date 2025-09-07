# handlers/mission_demon_nuke.py

"""
Mission: Demon Mode → Nuke → End Round → Retry.

This module runs a mission sequence intended for testing or scripted play:
1) Wait until the game is in RUNNING state.
2) Wait for and tap the Demon Mode floating button.
3) Wait a fixed duration to benefit from Demon Mode.
4) Wait for and tap the Nuke floating button.
5) Open the menu if needed, tap End Round, confirm, and tap Retry.

Notes
- Blocking loops: waits poll the screen until conditions are met; there are no timeouts.
- Side effects: ADB screenshots, OpenCV detection, on-device taps, and logging.
- Errors: Tap attempts inside the end-game sequence are guarded; failures are logged and the flow continues.
"""

import time
from core.ss_capture import capture_and_save_screenshot
from core.clickmap_access import tap_now
from core.floating_button_detector import detect_floating_buttons, tap_floating_button
from core.state_detector import detect_state_and_overlays
from core.label_tapper import tap_label_now
from utils.logger import log


def run_nuke_strategy():
    """
    Run the Demon-Mode-then-Nuke mission sequence and attempt an immediate restart.

    Flow
    - Poll for RUNNING state (2s interval).
    - Poll for Demon Mode button, tap it (1s interval), then wait 10s.
    - Poll for Nuke button, tap it (1s interval), then wait 5s.
    - Ensure menu is open, tap End Round, confirm Yes, then tap Retry.

    Returns
    - None. Action-oriented procedure; logs progress and issues.

    Side Effects
    - [adb][cv2][fs][state][tap][log][loop]
    """
    log("[MISSION] Starting Nuke -> Restart mission", "ACTION")

    # Step 1: Wait for RUNNING state
    while True:
        screen = capture_and_save_screenshot()
        result = detect_state_and_overlays(screen)
        if result["state"] == "RUNNING":
            log("[MISSION] Game is in RUNNING state", "INFO")
            break
        log("[MISSION] Waiting for RUNNING state...", "DEBUG")
        time.sleep(2)

    time.sleep(20)

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
    time.sleep(5)

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
