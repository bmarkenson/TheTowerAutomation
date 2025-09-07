from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.automation_state import AUTOMATION
from core.clickmap_access import tap_now, swipe_now
from core.label_tapper import tap_label_now
import time
import os
import cv2

def handle_daily_gem():
    print("Handling")
    session_id = _make_session_id()
    log(f"Handling DAILY AD GEM — Session: {session_id}", "INFO")

    # Tap into Store
    if not tap_label_now("navigation.goto_store"):
        return _abort_handler("Goto Store", session_id)
    time.sleep(1.2)

    # Goto Top of Store
    swipe_now("gesture_targets.goto_top:store")
    time.sleep(1.5)

    # Save first screen
    img_game_stats = capture_adb_screenshot()
    save_image(img_game_stats, f"{session_id}_store_top")

    # Goto Claim Daily Gems`
    # Swipe and capture 
    swipe_now("gesture_targets.goto_claim_daily_gems:store")
    time.sleep(3)
    save_image(capture_adb_screenshot(), f"{session_id}claim_daily_gems")

    # Claim Daily Gem
    if not tap_label_now("buttons.claim_daily_gems"):
        return _abort_handler("Claim_daily_gems", session_id)
    time.sleep(1.2)

    # Skip
    if not tap_label_now("buttons.skip:claim_daily_gems"):
        return _abort_handler("Skip Claim_daily_gems", session_id)
    time.sleep(1.2)

    # Return to Game
    if not tap_label_now("buttons.return_to_game"):
        return _abort_handler("Return to Game", session_id)
    time.sleep(1.2)

def _make_session_id():
    return "Game" + time.strftime("%Y%m%d_%H%M")

def save_image(img, tag):
    path = os.path.join("screenshots", "matches", f"{tag}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)
    log(f"[CAPTURE] Saved screenshot: {path}", "INFO")

def _abort_handler(step, session_id):
    """
    Logs error, saves screenshot, and aborts handler.
    """
    log(f"[ABORT]  Daily Gem handler failed at: {step}", "ERROR")
    debug_img = capture_adb_screenshot()
    save_image(debug_img, f"{session_id}_ABORT_{step.replace(' ', '_')}")
    return


