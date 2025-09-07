# handlers/game_over_handler.py
from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.automation_state import AUTOMATION, ExecMode
from core.clickmap_access import tap_now, swipe_now
from core.label_tapper import tap_label_now
import time
import os
import cv2

def handle_game_over():
    """
    Handle the GAME OVER flow: capture stats, close stats, and retry or pause.

    Workflow:
      1) Save initial game-over stats screenshot.
      2) Tap "More Stats"; if it fails, abort handler.
      3) Swipe to top, save screenshot; swipe to page 2, save; swipe to bottom, save.
      4) Close "More Stats"; if it fails, abort handler.
      5) Based on AUTOMATION.mode:
         - WAIT: loop until mode changes.
         - HOME: log and exit (not implemented beyond logging).
         - else: tap "Retry"; if it fails, abort handler.

    Returns:
        None — mission/handler side-effects only.

    Side effects:
        [adb] Captures screenshots.
        [cv2] Writes images to disk.
        [fs] Creates directories and files.
        [tap][swipe] Sends UI input.
        [log] Emits structured logs.
        [loop] May wait/sleep and/or loop while in WAIT mode.

    Defaults:
        Uses several sleeps between actions (≈1.2–1.5s), and a final 2s sleep.

    Errors:
        Tap failures cause an early abort via _abort_handler(), which sets AUTOMATION.mode=WAIT.
    """
    session_id = _make_session_id()
    log(f"Handling GAME OVER — Session: {session_id}", "INFO")

    if not tap_label_now("buttons.retry:game_over"):
        return _abort_handler("Retry Game", session_id)

    time.sleep(2)

def _make_session_id():
    """
    Build a session identifier for captured artifacts.

    Returns:
        str: "GameYYYYMMDD_%H%M"
    """
    return "Game" + time.strftime("%Y%m%d_%H%M")

def save_image(img, tag):
    """
    Persist a screenshot to the matches directory with a descriptive tag.

    Args:
        img (ndarray | None): BGR image to write (cv2). If None, skip with a warning.
        tag (str): Filename tag (without extension).

    Returns:
        None

    Side effects:
        [fs] Ensures parent directories exist and writes a PNG file.
        [cv2] Uses cv2.imwrite to serialize the image.
        [log] Logs path and warns if img is None.
    """
    if img is None:
        log(f"[CAPTURE] No image to save for tag '{tag}' (img=None). Skipping.", "WARN")
        return
    path = os.path.join("screenshots", "matches", f"{tag}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)
    log(f"[CAPTURE] Saved screenshot: {path}", "INFO")

def _abort_handler(step, session_id):
    """
    Abort helper for the GAME OVER handler.

    Logs an error, captures a debug screenshot, writes it to disk, and forces
    AUTOMATION.mode to WAIT so the system pauses for manual intervention.

    Args:
        step (str): Human-readable step name that failed.
        session_id (str): Session identifier used for artifact naming.

    Returns:
        None

    Side effects:
        [adb][cv2][fs][log] Capture & persist debug screenshot; emit error.
        [state] Sets AUTOMATION.mode = ExecMode.WAIT to pause automation.
    """
    log(f"[ABORT] Game Over handler failed at: {step}", "ERROR")
    debug_img = capture_adb_screenshot()
    save_image(debug_img, f"{session_id}_ABORT_{step.replace(' ', '_')}")
    AUTOMATION.mode = ExecMode.WAIT
    return
