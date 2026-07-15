"""Game Over navigation and structured battle-stat capture."""

import os
import time
from datetime import datetime
from typing import Any, Mapping, Optional

import cv2

from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.run_state import AUTOMATION, ExecMode
from core.input import tap_if_visible
from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from core.battle_stats import (
    build_battle_record,
    make_battle_id,
    persist_battle_record,
)
from utils.wave_detector import set_wave_hint


MORE_STATS_INDICATOR = "indicators.more_stats"
MORE_STATS_CONTENT_REGION = (100, 330, 880, 1370)


def handle_game_over(
    *,
    capture_stats: bool = True,
    battle_context: Optional[Mapping[str, Any]] = None,
):
    """
    Handle the GAME OVER flow: capture stats, close stats, and retry or pause.

    Workflow:
      1) Capture the initial Game Stats dialog in memory.
      2) Tap "More Stats"; if it fails, abort handler.
      3) Swipe to top, then retain every overlapping viewport through the bottom.
      4) OCR and persist a structured JSON/Markdown record. Screenshots are
         retained only when capture/OCR validation fails.
      5) Close "More Stats"; if it fails, abort handler.
      6) Based on AUTOMATION.mode:
         - WAIT: loop until mode changes.
         - HOME: tap the Game Stats Home button and return to the home screen.
         - else: tap "Retry"; if it fails, abort handler.

    Returns:
        None — mission/handler side-effects only.

    Side effects:
        [adb] Captures screenshots in memory.
        [ocr][fs] Writes a structured battle record and optional failure images.
        [fs] Creates directories and files.
        [tap][swipe] Sends UI input.
        [log] Emits structured logs.
        [loop] May wait/sleep and/or loop while in WAIT mode.

    Defaults:
        Uses several sleeps between actions (≈1.2–1.5s), and a final 2s sleep.

    Errors:
        Tap failures cause an early abort via _abort_handler(), which sets AUTOMATION.mode=WAIT.
    """
    captured_at = datetime.now().astimezone()
    session_id = _make_session_id(captured_at.timetuple())
    battle_id = make_battle_id(captured_at)
    log(f"Handling GAME OVER — Session: {session_id}", "INFO", console=True)

    # Clear the monotonic wave hint immediately so fresh runs accept wave 1 detections.
    set_wave_hint(None)
    log("[WAVE] Cleared wave hint on game over", "INFO")

    if capture_stats:
        # Keep source frames in memory. Routine successful captures persist only
        # structured data; screenshots are failure evidence.
        img_game_stats = capture_adb_screenshot()
        if img_game_stats is None:
            return _abort_handler("Capture Game Stats", session_id)

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
        if top.screenshot is None:
            return _abort_handler(f"Scroll More Stats to top ({top.reason})", session_id)

        if top.success:
            capture = capture_scroll_to_edge(
                "gesture_targets.goto_pg2:more_stats",
                source_label=MORE_STATS_INDICATOR,
                screenshot=top.screenshot,
                progress_region=MORE_STATS_CONTENT_REGION,
                max_swipes=16,
                settle_s=1.2,
            )
            more_stats_frames = list(capture.screenshots)
            source_complete = capture.success
            source_reason = capture.reason
        else:
            # Capture/OCR failure must not strand a completed battle. Preserve
            # the available evidence, produce an explicitly incomplete record,
            # and continue to the verified close button.
            more_stats_frames = [top.screenshot]
            source_complete = False
            source_reason = f"top_{top.reason}"

        _save_battle_stats_record(
            battle_id=battle_id,
            session_id=session_id,
            game_stats_frame=img_game_stats,
            more_stats_frames=more_stats_frames,
            source_complete=source_complete,
            source_reason=source_reason,
            battle_context=battle_context,
            captured_at=captured_at,
        )

        # Step 5: Close More Stats
        if not tap_if_visible("buttons.close:more_stats", retries=1):
            return _abort_handler("Close More Stats", session_id)

        time.sleep(1.2)
    else:
        log("[GAME_OVER] Fast mode enabled — skipping More Stats capture.", "INFO")

    # Step 6: Decide next action based on mode
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


def _save_battle_stats_record(
    *,
    battle_id: str,
    session_id: str,
    game_stats_frame,
    more_stats_frames,
    source_complete: bool,
    source_reason: str,
    battle_context: Optional[Mapping[str, Any]],
    captured_at: Optional[datetime] = None,
) -> None:
    """Persist OCR output and retain source images only for uncertain records."""

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    try:
        record = build_battle_record(
            game_stats_frame,
            more_stats_frames,
            source_complete=source_complete,
            source_reason=source_reason,
            battle_id=battle_id,
            captured_at=captured_at,
            strategy_name=strategy_name,
            runtime_context=context,
        )
        json_path, markdown_path = persist_battle_record(record)
        log(
            f"[BATTLE_STATS] Saved record: {json_path} (view: {markdown_path})",
            "INFO",
            console=True,
        )
        if not record["quality"]["retain_source_images"]:
            return
        warnings = "; ".join(record["quality"]["warnings"]) or "validation failed"
        log(f"[BATTLE_STATS] Retaining source screenshots: {warnings}", "WARN")
    except Exception as exc:
        log(f"[BATTLE_STATS] Structured capture failed: {exc}", "ERROR", console=True)

    save_image(game_stats_frame, f"{session_id}_game_stats_OCR_EVIDENCE")
    for index, frame in enumerate(more_stats_frames, start=1):
        save_image(frame, f"{session_id}_more_stats_{index}_OCR_EVIDENCE")


def _make_session_id(captured_at=None):
    """
    Build a session identifier for captured artifacts.

    Returns:
        str: "GameYYYYMMDD_%H%M%S"
    """
    return "Game" + time.strftime("%Y%m%d_%H%M%S", captured_at)


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
