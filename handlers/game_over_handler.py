# handlers/game_over_handler.py
from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.run_state import AUTOMATION, ExecMode
from core.input import tap_if_visible, tap_now
from core.scrolling import guarded_swipe, scroll_to_edge
from core.adb_utils import adb_shell
from utils.wave_detector import set_wave_hint
# Note: OCR fallback for More Stats is currently disabled; keeping imports out.
import time
import os
import cv2


MORE_STATS_INDICATOR = "indicators.more_stats"
MORE_STATS_CONTENT_REGION = (100, 330, 880, 1370)

def handle_game_over(*, capture_stats: bool = True):
    """
    Handle the GAME OVER flow: capture stats, close stats, and retry or pause.

    Workflow:
      1) Save initial game-over stats screenshot.
      2) Tap "More Stats"; if it fails, abort handler.
      3) Swipe to top, save screenshot; swipe to page 2, save; swipe to bottom, save.
      4) Close "More Stats"; if it fails, abort handler.
      5) Based on AUTOMATION.mode:
         - WAIT: loop until mode changes.
         - HOME: tap the Game Stats Home button and return to the home screen.
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
    log(f"Handling GAME OVER — Session: {session_id}", "INFO", console=True)

    # Clear the monotonic wave hint immediately so fresh runs accept wave 1 detections.
    set_wave_hint(None)
    log("[WAVE] Cleared wave hint on game over", "INFO")

    if capture_stats:
        # Save first screen
        img_game_stats = capture_adb_screenshot()
        save_image(img_game_stats, f"{session_id}_game_stats")

        # Step 1: Tap "More Stats"
        if not tap_if_visible("buttons.more_stats:game_over", retries=1):
            return _abort_handler("Tap More Stats", session_id)

        time.sleep(1.5)

        # Step 2: Repeatedly swipe to the true top, verifying the Round Stats
        # panel before and after every gesture.
        top = scroll_to_edge(
            "gesture_targets.goto_top:more_stats",
            source_label=MORE_STATS_INDICATOR,
            progress_region=MORE_STATS_CONTENT_REGION,
            max_swipes=8,
            settle_s=1.2,
        )
        if not top.success or top.screenshot is None:
            return _abort_handler(f"Scroll More Stats to top ({top.reason})", session_id)
        save_image(top.screenshot, f"{session_id}_more_stats_1")

        # Step 3: Swipe to page 2 and capture
        page_two = guarded_swipe(
            "gesture_targets.goto_pg2:more_stats",
            source_label=MORE_STATS_INDICATOR,
            screenshot=top.screenshot,
            settle_s=1.2,
        )
        if not page_two.success or page_two.screenshot is None:
            return _abort_handler(f"Scroll More Stats to page 2 ({page_two.reason})", session_id)
        save_image(page_two.screenshot, f"{session_id}_more_stats_2")

        # Step 4: Repeatedly swipe to the true bottom and capture.
        bottom = scroll_to_edge(
            "gesture_targets.goto_bottom:more_stats",
            source_label=MORE_STATS_INDICATOR,
            screenshot=page_two.screenshot,
            progress_region=MORE_STATS_CONTENT_REGION,
            max_swipes=12,
            settle_s=1.2,
        )
        if not bottom.success or bottom.screenshot is None:
            return _abort_handler(f"Scroll More Stats to bottom ({bottom.reason})", session_id)
        save_image(bottom.screenshot, f"{session_id}_more_stats_3")

        # Step 5: Attempt to capture stats text (clipboard first, then OCR fallback)
        _save_stats_text(session_id)

        # Step 6: Close More Stats
        if not tap_if_visible("buttons.close:more_stats", retries=1):
            return _abort_handler("Close More Stats", session_id)

        time.sleep(1.2)
    else:
        log("[GAME_OVER] Fast mode enabled — skipping More Stats capture.", "INFO")

    # Step 7: Decide next action based on mode
    mode = AUTOMATION.mode
    if mode == ExecMode.WAIT:
        log("Pausing on Game Over — waiting for user signal.", "INFO", console=True)
        while AUTOMATION.mode is ExecMode.WAIT:
            time.sleep(1)
    elif mode == ExecMode.HOME:
        if not tap_if_visible("buttons.home:game_over", retries=1):
            return _abort_handler("Go Home from Game Stats", session_id)
        log("Mode = HOME — returned from Game Stats", "INFO", console=True)
    else:
        if not tap_if_visible("buttons.retry:game_over", retries=1):
            return _abort_handler("Retry Game", session_id)
        # After a successful retry the wave counter resets to 1; reset the hint.
        set_wave_hint(1)
        log("[WAVE] Reset wave hint to 1 after game restart", "INFO")

    time.sleep(2)

def _save_stats_text(session_id: str) -> None:
    """
    On the More Stats screen, tap the save-stats button to copy text to the
    clipboard, then read the clipboard and write it to a file next to the
    screenshots for this session.
    """
    # Disabled: save-stats tap and clipboard capture are unreliable with
    # host-clipboard emulators (e.g., BlueStacks on Windows). Skipping.
    log("[MORE_STATS] save-stats capture disabled.", "INFO")
    return

    # Try to read clipboard via standard ADB command (Android 10+)
    res = adb_shell(["cmd", "clipboard", "get"], capture_output=True, check=False)
    text = None
    if res and res.stdout is not None:
        out = res.stdout.strip()
        if out and out.lower() not in {"(null)", "null", "no primary clip"}:
            text = out

    path = os.path.join("screenshots", "matches", f"{session_id}_more_stats.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if not text:
        # OCR fallback disabled for now (clipboard may be on host OS in emulators).
        log("[MORE_STATS] Clipboard empty; OCR fallback disabled — skipping stats text capture.", "WARN")
        text = None

    if text:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            log(f"[CAPTURE] Saved More Stats text: {path}", "INFO")
        except Exception as e:
            log(f"[ERROR] Failed to write stats text: {e}", "ERROR")
    else:
        log("[MORE_STATS] No stats text captured via clipboard or OCR.", "WARN")

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
