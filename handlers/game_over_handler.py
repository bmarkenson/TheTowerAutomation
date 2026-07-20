"""Game Over navigation and structured battle-stat capture."""

import os
import time
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

import cv2

from utils.logger import log
from core.android_clipboard import read_battle_report_clipboard
from core.ss_capture import capture_adb_screenshot
from core.run_state import AUTOMATION, ExecMode, RunState
from core.input import tap_if_visible
from core.label_tapper import is_visible
from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from core.battle_stats import (
    attach_battle_perks,
    build_battle_record,
    build_battle_record_from_clipboard,
    make_battle_id,
    persist_battle_record,
)
from core.battle_perks import ocr_selected_perks


MORE_STATS_INDICATOR = "indicators.more_stats"
MORE_STATS_CONTENT_REGION = (100, 330, 880, 1370)
PERKS_INDICATOR = "indicators.perks_panel"
PERKS_CONTENT_REGION = (100, 414, 880, 1340)


def _game_stats_visible(screenshot) -> bool:
    """Accept either stable Game Stats title or More Stats button evidence."""

    return is_visible(
        "indicators.game_over",
        screenshot=screenshot,
    ) or is_visible(
        "buttons.more_stats:game_over",
        screenshot=screenshot,
    )


def handle_game_over(
    *,
    capture_stats: bool = True,
    battle_context: Optional[Mapping[str, Any]] = None,
    control_sync: Optional[Callable[[], None]] = None,
    return_home_after_battle: bool = False,
):
    """
    Handle the GAME OVER flow: capture stats, close stats, and retry or pause.

    Workflow:
      1) Capture the initial Game Stats dialog in memory.
      2) Capture the complete ordered Selected Perks list and return to Game Stats.
      3) Tap "More Stats"; if it fails, abort handler.
      4) Copy the complete report through Android's clipboard service and
         persist a structured JSON/Markdown record.
      5) If clipboard acquisition or freshness validation fails, fall back to
         guarded overlapping screenshots and OCR.
      6) Close "More Stats"; if it fails, abort handler.
      7) Based on AUTOMATION.mode, unless a guarded preflight repair requires Home:
         - WAIT: loop until mode changes.
         - HOME: tap the Game Stats Home button and return to the home screen.
         - else: tap "Retry"; if it fails, abort handler.

    Returns:
        None — mission/handler side-effects only.

    Side effects:
        [adb] Captures screenshots in memory.
        [clipboard][ocr][fs] Writes a structured record and optional evidence.
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

    if capture_stats:
        # Keep source frames in memory. Routine successful captures persist only
        # structured data; screenshots are failure evidence.
        img_game_stats = capture_adb_screenshot()
        if img_game_stats is None:
            return _abort_handler("Capture Game Stats", session_id)

        perks, perks_frames, perks_screen_restored = _capture_game_over_perks()
        if not perks_screen_restored:
            return _abort_handler("Close Perks", session_id)

        # Tap "More Stats" after Perks capture has restored Game Stats.
        if not tap_if_visible("buttons.more_stats:game_over", retries=1):
            return _abort_handler("Tap More Stats", session_id)

        time.sleep(1.5)

        record, clipboard_reason = _capture_clipboard_battle_record(
            battle_id=battle_id,
            game_stats_frame=img_game_stats,
            battle_context=battle_context,
            captured_at=captured_at,
        )
        if record is not None:
            attach_battle_perks(record, perks)
            _persist_battle_stats_record(
                record,
                session_id=session_id,
                game_stats_frame=img_game_stats,
                more_stats_frames=[],
                perks_frames=perks_frames,
            )
        else:
            log(
                f"[BATTLE_STATS] Clipboard capture unavailable ({clipboard_reason}); "
                "using guarded OCR fallback",
                "WARN",
            )
            # Repeatedly swipe to the true top, verifying the Round Stats panel
            # before and after every fallback gesture.
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
                # Preserve incomplete evidence but do not strand the completed
                # battle on a paging/OCR failure.
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
                perks=perks,
                perks_frames=perks_frames,
            )

        # Step 5: Close More Stats
        if not tap_if_visible("buttons.close:more_stats", retries=1):
            return _abort_handler("Close More Stats", session_id)

        time.sleep(1.2)
    else:
        log("[GAME_OVER] Fast mode enabled — skipping More Stats capture.", "INFO")

    # Decide the terminal action while continuing to consume the same control
    # file used by the main loop. PAUSED blocks every action; STOPPED exits.
    mode = AUTOMATION.mode
    if mode == ExecMode.WAIT:
        log("Pausing on Game Over — waiting for user signal.", "INFO", console=True)
    mode = _wait_for_game_over_direction(control_sync)
    if mode is None:
        log("Automation stopped while waiting on Game Over.", "INFO", console=True)
        return
    if return_home_after_battle:
        mode = ExecMode.HOME
    if mode == ExecMode.HOME:
        if not tap_if_visible("buttons.home:game_over", retries=1):
            return _abort_handler("Go Home from Game Stats", session_id)
        log("Mode = HOME — returned from Game Stats", "INFO", console=True)
    else:
        if not tap_if_visible("buttons.retry:game_over", retries=1):
            return _abort_handler("Retry Game", session_id)

    time.sleep(2)


def _wait_for_game_over_direction(
    control_sync: Optional[Callable[[], None]],
) -> Optional[ExecMode]:
    """Wait interruptibly for a runnable RETRY/HOME terminal direction."""

    while True:
        if control_sync is not None:
            control_sync()
        state = AUTOMATION.state
        if state is RunState.STOPPED:
            return None
        if state is RunState.PAUSED or AUTOMATION.mode is ExecMode.WAIT:
            time.sleep(1)
            continue
        return AUTOMATION.mode


def _capture_game_over_perks():
    """Capture every ordered Selected Perks row and restore Game Stats."""

    game_stats_screen = capture_adb_screenshot()
    if game_stats_screen is None:
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="game_stats_capture_failed",
            ),
            [],
            True,
        )
    if not _game_stats_visible(game_stats_screen):
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="game_stats_not_visible",
            ),
            [],
            False,
        )
    if not tap_if_visible(
        "buttons.perks:game_over",
        screenshot=game_stats_screen,
        retries=1,
    ):
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="perks_button_not_visible",
            ),
            [],
            True,
        )

    perks_screen = _wait_for_visible(PERKS_INDICATOR, timeout=5.0)
    if perks_screen is None:
        current = capture_adb_screenshot()
        restored = bool(
            current is not None
            and _game_stats_visible(current)
        )
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="perks_panel_not_visible",
            ),
            [],
            restored,
        )

    top = scroll_to_edge(
        "gesture_targets.goto_top:perks",
        source_label=PERKS_INDICATOR,
        screenshot=perks_screen,
        progress_region=PERKS_CONTENT_REGION,
        max_swipes=8,
        settle_s=0.8,
    )
    if top.screenshot is None:
        frames = []
        source_complete = False
        source_reason = f"top_{top.reason}"
    elif top.success:
        capture = capture_scroll_to_edge(
            "gesture_targets.goto_next:perks",
            source_label=PERKS_INDICATOR,
            screenshot=top.screenshot,
            progress_region=PERKS_CONTENT_REGION,
            max_swipes=20,
            settle_s=0.8,
        )
        frames = list(capture.screenshots)
        source_complete = capture.success
        source_reason = capture.reason
    else:
        frames = [top.screenshot]
        source_complete = False
        source_reason = f"top_{top.reason}"

    try:
        perks = ocr_selected_perks(
            frames,
            source_complete=source_complete,
            source_reason=source_reason,
        )
        log(
            f"[BATTLE_PERKS] Captured {perks['quality']['perk_count']} ordered perk(s)",
            "INFO",
            console=True,
        )
    except Exception as exc:
        log(f"[BATTLE_PERKS] OCR failed: {exc}", "ERROR", console=True)
        perks = ocr_selected_perks(
            [],
            source_complete=False,
            source_reason=f"ocr_failed:{exc}",
        )

    closed = tap_if_visible("buttons.close:perks", retries=1)
    if closed:
        time.sleep(1.0)
    return perks, frames, closed


def _wait_for_visible(label: str, *, timeout: float, poll: float = 0.25):
    """Return a fresh frame only after the expected control is visible."""

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        screenshot = capture_adb_screenshot()
        if screenshot is not None and is_visible(label, screenshot=screenshot):
            return screenshot
        time.sleep(max(0.05, poll))
    return None


def _capture_clipboard_battle_record(
    *,
    battle_id: str,
    game_stats_frame,
    battle_context: Optional[Mapping[str, Any]],
    captured_at: datetime,
):
    """Copy, read, parse, and freshness-check the visible More Stats report."""

    stats_screen = capture_adb_screenshot()
    if stats_screen is None:
        return None, "capture_before_copy_failed"
    if not is_visible(MORE_STATS_INDICATOR, screenshot=stats_screen):
        return None, "more_stats_not_visible"

    before = read_battle_report_clipboard()
    if not tap_if_visible(
        "buttons.copy:more_stats",
        screenshot=stats_screen,
        retries=1,
    ):
        return None, "copy_tap_failed"

    candidate = None
    after_reason = "clipboard_read_failed"
    for attempt in range(4):
        if attempt:
            time.sleep(0.25)
        after = read_battle_report_clipboard()
        after_reason = after.reason
        if not after.success:
            continue
        candidate = after.text
        if not before.success or candidate != before.text:
            break
    if candidate is None:
        return None, after_reason

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    run_configuration = context.pop("run_configuration", None)
    try:
        record = build_battle_record_from_clipboard(
            game_stats_frame,
            candidate,
            battle_id=battle_id,
            captured_at=captured_at,
            strategy_name=strategy_name,
            run_configuration=run_configuration,
            runtime_context=context,
        )
    except Exception as exc:
        return None, f"clipboard_parse_failed:{exc}"

    more_quality = record["more_stats"]["quality"]
    if not more_quality["valid"]:
        warnings = "; ".join(more_quality.get("warnings", []))
        return None, f"clipboard_validation_failed:{warnings or 'invalid_report'}"

    identity = record["quality"]["identity"]
    if identity["mismatches"]:
        fields = ",".join(item["field"] for item in identity["mismatches"])
        return None, f"clipboard_identity_mismatch:{fields}"

    changed = bool(before.success and candidate != before.text)
    checked = set(identity["checked_fields"])
    strongly_identified = {"wave", "tier"}.issubset(checked)
    if not changed and not strongly_identified:
        return None, "clipboard_freshness_unproven"

    log(
        f"[BATTLE_STATS] Copied {more_quality['row_count']} exact Stats rows "
        "from the Android clipboard",
        "INFO",
        console=True,
    )
    return record, "clipboard_copy"


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
    perks: Optional[Mapping[str, Any]] = None,
    perks_frames=(),
) -> None:
    """Persist OCR output and retain source images only for uncertain records."""

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    run_configuration = context.pop("run_configuration", None)
    try:
        record = build_battle_record(
            game_stats_frame,
            more_stats_frames,
            source_complete=source_complete,
            source_reason=source_reason,
            battle_id=battle_id,
            captured_at=captured_at,
            strategy_name=strategy_name,
            run_configuration=run_configuration,
            runtime_context=context,
        )
        if perks is not None:
            attach_battle_perks(record, perks)
        _persist_battle_stats_record(
            record,
            session_id=session_id,
            game_stats_frame=game_stats_frame,
            more_stats_frames=more_stats_frames,
            perks_frames=perks_frames,
        )
        return
    except Exception as exc:
        log(f"[BATTLE_STATS] Structured capture failed: {exc}", "ERROR", console=True)

    save_image(game_stats_frame, f"{session_id}_game_stats_OCR_EVIDENCE")
    for index, frame in enumerate(more_stats_frames, start=1):
        save_image(frame, f"{session_id}_more_stats_{index}_OCR_EVIDENCE")
    for index, frame in enumerate(perks_frames, start=1):
        save_image(frame, f"{session_id}_perks_{index}_OCR_EVIDENCE")


def _persist_battle_stats_record(
    record: Mapping[str, Any],
    *,
    session_id: str,
    game_stats_frame,
    more_stats_frames,
    perks_frames=(),
) -> None:
    """Persist a built record and retain only the source frames it needs."""

    try:
        json_path, markdown_path = persist_battle_record(record)
        log(
            f"[BATTLE_STATS] Saved record: {json_path} (view: {markdown_path})",
            "INFO",
            console=True,
        )
    except Exception as exc:
        log(f"[BATTLE_STATS] Record persistence failed: {exc}", "ERROR", console=True)
        save_image(game_stats_frame, f"{session_id}_game_stats_OCR_EVIDENCE")
        for index, frame in enumerate(more_stats_frames, start=1):
            save_image(frame, f"{session_id}_more_stats_{index}_OCR_EVIDENCE")
        for index, frame in enumerate(perks_frames, start=1):
            save_image(frame, f"{session_id}_perks_{index}_OCR_EVIDENCE")
        return

    if not record["quality"]["retain_source_images"]:
        return
    warnings = "; ".join(record["quality"]["warnings"]) or "validation failed"
    log(f"[BATTLE_STATS] Retaining source screenshots: {warnings}", "WARN")
    save_image(game_stats_frame, f"{session_id}_game_stats_OCR_EVIDENCE")
    for index, frame in enumerate(more_stats_frames, start=1):
        save_image(frame, f"{session_id}_more_stats_{index}_OCR_EVIDENCE")
    for index, frame in enumerate(perks_frames, start=1):
        save_image(frame, f"{session_id}_perks_{index}_OCR_EVIDENCE")


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
