from core import tap_dispatcher
from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.automation_state import AUTOMATION
from core.clickmap_access import tap_now, swipe_now
import cv2
import os
import json
import time

def handle_game_over():
    print("Handling")
    session_id = _make_session_id()
    log(f"Handling GAME OVER — Session: {session_id}", "INFO")

    # Step 1: Save the initial Game Stats screen
    img_game_stats = capture_adb_screenshot()
    save_image(img_game_stats, f"{session_id}_game_stats")

    # Step 2: Tap "More Stats"
    tap_now("more_stats")
    time.sleep(1.5)

    swipe_now("goto_top")
    time.sleep(1.5)

    # Step 3: Save first More Stats screen
    img_more1 = capture_adb_screenshot()
    save_image(img_more1, f"{session_id}_more_stats_1")

    # Step 4: Scroll to next stats page
    swipe_now("more_stats_pg2")
    time.sleep(1.2)

    # Step 5: Save second More Stats screen
    img_more2 = capture_adb_screenshot()
    save_image(img_more2, f"{session_id}_more_stats_2")

    # Step 6: Scroll to next stats page
    swipe_now("more_stats_pg3")
    time.sleep(1.2)

    # Step 7: Save third More Stats screen
    img_more2 = capture_adb_screenshot()
    save_image(img_more2, f"{session_id}_more_stats_3")

    tap_now("close_more_stats")
    time.sleep(1.2)

    # Step 8: Retry or pause depending on mode
    mode = AUTOMATION.get_mode()
    if mode == "WAIT":
        log("Pausing on Game Over — waiting for user signal.", "INFO")
        while AUTOMATION.get_mode() == "WAIT":
            time.sleep(1)
    elif mode == "HOME":
        log("Mode = HOME (not implemented yet)", "INFO")
        # e.g., swipe to home button or tap a known area
    else:
        tap_now("Retry")

    time.sleep(2)

def _make_session_id():
    return "Game" + time.strftime("%Y%m%d_%H%M")

def save_image(img, tag):
    path = os.path.join("screenshots", "matches", f"{tag}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)
    log(f"[CAPTURE] Saved screenshot: {path}", "INFO")
