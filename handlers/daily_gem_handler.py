from core import tap_dispatcher
from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.automation_state import AUTOMATION, ExecMode
from core.clickmap_access import tap_now, swipe_now
from core.label_tapper import tap_label_now
import time
import os
import cv2

def handle_game_over():
    print("Handling")
    session_id = _make_session_id()
    log(f"Handling GAME OVER — Session: {session_id}", "INFO")

    # Save first screen
    img_game_stats = capture_adb_screenshot()
    save_image(img_game_stats, f"{session_id}_game_stats")

    # Step 1: Tap "More Stats"
    if not tap_label_now("buttons.more_stats:game_over"):
        return _abort_handler("Tap More Stats", session_id)

    time.sleep(1.5)

    # Step 2: Swipe to top and capture
    swipe_now("gesture_targets.goto_top:more_stats")
    time.sleep(1.5)
    save_image(capture_adb_screenshot(), f"{session_id}_more_stats_1")

    # Step 3: Swipe to page 2 and capture
    swipe_now("gesture_targets.goto_pg2:more_stats")
    time.sleep(1.2)
    save_image(capture_adb_screenshot(), f"{session_id}_more_stats_2")

    # Step 4: Swipe to bottom and capture
    swipe_now("gesture_targets.goto_bottom:more_stats")
    time.sleep(1.2)
    save_image(capture_adb_screenshot(), f"{session_id}_more_stats_3")

    # Step 5: Close More Stats
    if not tap_label_now("buttons.close:more_stats"):
        return _abort_handler("Close More Stats", session_id)

    time.sleep(1.2)

    # Step 6: Decide next action based on mode
    mode = AUTOMATION.mode
    if mode == "WAIT":
        log("Pausing on Game Over — waiting for user signal.", "INFO")
        while AUTOMATION.mode is ExecMode.WAIT:
            time.sleep(1)
    elif mode == "HOME":
        log("Mode = HOME (not implemented yet)", "INFO")
        return  # Exit cleanly
    else:
        if not tap_label_now("buttons.retry:game_over"):
            return _abort_handler("Retry Game", session_id)

    time.sleep(2)

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
    log(f"[ABORT] Game Over handler failed at: {step}", "ERROR")
    debug_img = capture_adb_screenshot()
    save_image(debug_img, f"{session_id}_ABORT_{step.replace(' ', '_')}")
    AUTOMATION.mode = ExecMode.WAIT
    return


