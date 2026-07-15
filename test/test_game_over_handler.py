#!/usr/bin/env python3
# test/test_game_over_handler.py

import traceback
from pathlib import Path
from unittest.mock import patch
import numpy as np

from handlers.game_over_handler import handle_game_over, _save_battle_stats_record
from core.run_state import AUTOMATION, ExecMode
from core.scrolling import ScrollResult
from utils.logger import log


def test_home_mode_taps_game_stats_home_instead_of_retry():
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME
    try:
        with (
            patch("handlers.game_over_handler.set_wave_hint"),
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(capture_stats=False)
    finally:
        AUTOMATION.mode = original_mode

    tap.assert_called_once_with("buttons.home:game_over", retries=1)


def test_valid_structured_stats_do_not_save_routine_screenshots():
    frame = np.zeros((12, 12, 3), dtype=np.uint8)
    record = {"quality": {"retain_source_images": False, "warnings": []}}
    with (
        patch("handlers.game_over_handler.build_battle_record", return_value=record),
        patch(
            "handlers.game_over_handler.persist_battle_record",
            return_value=(Path("record.json"), Path("record.md")),
        ),
        patch("handlers.game_over_handler.save_image") as save_image,
    ):
        _save_battle_stats_record(
            battle_id="Battle1",
            session_id="Game1",
            game_stats_frame=frame,
            more_stats_frames=[frame, frame],
            source_complete=True,
            source_reason="edge_reached",
            battle_context={"strategy": "gc_farm_t19_experiment"},
        )

    save_image.assert_not_called()


def test_uncertain_structured_stats_retain_source_screenshots():
    frame = np.zeros((12, 12, 3), dtype=np.uint8)
    record = {
        "quality": {
            "retain_source_images": True,
            "warnings": ["Low-confidence rows: damage.projectiles"],
        }
    }
    with (
        patch("handlers.game_over_handler.build_battle_record", return_value=record),
        patch(
            "handlers.game_over_handler.persist_battle_record",
            return_value=(Path("record.json"), Path("record.md")),
        ),
        patch("handlers.game_over_handler.save_image") as save_image,
    ):
        _save_battle_stats_record(
            battle_id="Battle1",
            session_id="Game1",
            game_stats_frame=frame,
            more_stats_frames=[frame, frame],
            source_complete=True,
            source_reason="edge_reached",
            battle_context=None,
        )

    assert save_image.call_count == 3


def test_capture_failure_is_recorded_without_stranding_game_over_navigation():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME
    try:
        with (
            patch("handlers.game_over_handler.set_wave_hint"),
            patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch(
                "handlers.game_over_handler.scroll_to_edge",
                return_value=ScrollResult(False, frame, 1, "max_swipes_exceeded"),
            ),
            patch("handlers.game_over_handler.capture_scroll_to_edge") as capture_scroll,
            patch("handlers.game_over_handler._save_battle_stats_record") as save_record,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(capture_stats=True)
    finally:
        AUTOMATION.mode = original_mode

    capture_scroll.assert_not_called()
    assert save_record.call_args.kwargs["source_complete"] is False
    assert save_record.call_args.kwargs["source_reason"] == "top_max_swipes_exceeded"
    assert [call.args[0] for call in tap.call_args_list] == [
        "buttons.more_stats:game_over",
        "buttons.close:more_stats",
        "buttons.home:game_over",
    ]


def run_test():
    """
    Exercise the Game Over handler safely.

    Behavior:
      - Saves current AUTOMATION.mode, switches to WAIT for safety, runs handle_game_over(),
        logs any exception with traceback, then restores the original mode.

    Returns:
      Action result (logs lifecycle; no explicit return).

    Side effects:
      Temporarily mutates global automation mode; invokes handler which may perform ADB I/O,
      template matching, taps/swipes, and filesystem writes.

    Errors:
      Exceptions from the handler are caught and logged; original mode is always restored.
    """
    log("[TEST] Starting Game Over handler test", "INFO")

    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.WAIT
    log(f"[TEST] Automation mode set to: {AUTOMATION.mode.value}", "INFO")

    try:
        handle_game_over()
    except Exception as e:
        log(f"[TEST] Exception raised during handler: {e}", "ERROR")
        log(traceback.format_exc(), "ERROR")
    finally:
        AUTOMATION.mode = original_mode
        log(f"[TEST] Automation mode restored to: {AUTOMATION.mode.value}", "INFO")

    log("[TEST] Game Over handler test complete", "INFO")


if __name__ == "__main__":
    run_test()
