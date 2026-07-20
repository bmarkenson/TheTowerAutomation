#!/usr/bin/env python3
# test/test_game_over_handler.py

import traceback
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np

from handlers.game_over_handler import (
    _capture_game_over_perks,
    _game_stats_visible,
    _save_battle_stats_record,
    _wait_for_game_over_direction,
    handle_game_over,
)
from core.run_state import AUTOMATION, ExecMode, RunState
from core.matcher import get_match
from core.scrolling import ScrollResult
from utils.logger import log


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"


def test_game_stats_visibility_accepts_more_stats_when_title_is_degraded():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch(
        "handlers.game_over_handler.is_visible",
        side_effect=lambda label, *, screenshot: (
            label == "buttons.more_stats:game_over"
        ),
    ) as visible:
        assert _game_stats_visible(frame)

    assert [call.args[0] for call in visible.call_args_list] == [
        "indicators.game_over",
        "buttons.more_stats:game_over",
    ]


def test_game_over_perks_button_requires_visible_button_artwork():
    positive = cv2.imread(str(FIXTURES / "game_over_stats_20260715.png"))
    negative = cv2.imread(str(FIXTURES / "home_screen_new_day_store_badge_20260713.png"))
    assert positive is not None
    assert negative is not None

    point, confidence = get_match("buttons.perks:game_over", screenshot=positive)
    negative_point, negative_confidence = get_match(
        "buttons.perks:game_over",
        screenshot=negative,
    )

    assert point == (720, 1034)
    assert confidence >= 0.99
    assert negative_point is None
    assert negative_confidence < 0.9


def test_missing_game_over_perks_button_is_recoverable_without_blind_tap():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    perks = {"quality": {"source_reason": "perks_button_not_visible"}}

    with (
        patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
        patch("handlers.game_over_handler.is_visible", return_value=True),
        patch("handlers.game_over_handler.tap_if_visible", return_value=False) as tap,
        patch("handlers.game_over_handler.ocr_selected_perks", return_value=perks),
        patch("handlers.game_over_handler.scroll_to_edge") as scroll,
    ):
        result, frames, restored = _capture_game_over_perks()

    assert result is perks
    assert frames == []
    assert restored
    tap.assert_called_once_with(
        "buttons.perks:game_over",
        screenshot=frame,
        retries=1,
    )
    scroll.assert_not_called()


def test_missing_perks_panel_continues_only_if_game_stats_is_still_visible():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    perks = {"quality": {"source_reason": "perks_panel_not_visible"}}

    def visible(label, *, screenshot):
        return label == "indicators.game_over"

    with (
        patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
        patch("handlers.game_over_handler.is_visible", side_effect=visible),
        patch("handlers.game_over_handler.tap_if_visible", return_value=True),
        patch("handlers.game_over_handler._wait_for_visible", return_value=None),
        patch("handlers.game_over_handler.ocr_selected_perks", return_value=perks),
        patch("handlers.game_over_handler.scroll_to_edge") as scroll,
    ):
        result, frames, restored = _capture_game_over_perks()

    assert result is perks
    assert frames == []
    assert restored
    scroll.assert_not_called()


def test_home_mode_taps_game_stats_home_instead_of_retry():
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME
    try:
        with (
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(capture_stats=False)
    finally:
        AUTOMATION.mode = original_mode

    tap.assert_called_once_with("buttons.home:game_over", retries=1)


def test_guarded_preflight_repair_forces_home_after_control_allows_actions():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.RETRY
    try:
        with (
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(
                capture_stats=False,
                return_home_after_battle=True,
            )
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    tap.assert_called_once_with("buttons.home:game_over", retries=1)


def test_game_over_finalizes_boundary_before_terminal_navigation():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.RETRY
    events = []

    def tap(*args, **kwargs):
        events.append(("tap", args[0]))
        return True

    try:
        with (
            patch("handlers.game_over_handler.tap_if_visible", side_effect=tap),
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(
                capture_stats=False,
                before_terminal_action=lambda: events.append(("boundary", None)),
            )
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert events == [
        ("boundary", None),
        ("tap", "buttons.retry:game_over"),
    ]


def test_game_over_wait_polls_control_and_blocks_retry_while_paused():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.WAIT
    sync_calls = 0

    def sync_control():
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            AUTOMATION.state = RunState.PAUSED
            AUTOMATION.mode = ExecMode.RETRY
        else:
            AUTOMATION.state = RunState.RUNNING

    try:
        with patch("handlers.game_over_handler.time.sleep") as sleep:
            direction = _wait_for_game_over_direction(sync_control)
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert direction is ExecMode.RETRY
    assert sync_calls == 2
    sleep.assert_called_once_with(1)


def test_game_over_wait_exits_when_control_stops_automation():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.WAIT

    def sync_control():
        AUTOMATION.state = RunState.STOPPED
        AUTOMATION.mode = ExecMode.RETRY

    try:
        with patch("handlers.game_over_handler.time.sleep") as sleep:
            direction = _wait_for_game_over_direction(sync_control)
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert direction is None
    sleep.assert_not_called()


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
    perks = {"quality": {"valid": True, "warnings": []}}
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME
    try:
        with (
            patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
            patch(
                "handlers.game_over_handler._capture_game_over_perks",
                return_value=(perks, [], True),
            ),
            patch(
                "handlers.game_over_handler._capture_clipboard_battle_record",
                return_value=(None, "clipboard_service_failed"),
            ),
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


def test_clipboard_success_skips_more_stats_scrolling_and_keeps_perk_order():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    record = {
        "quality": {
            "valid": True,
            "retain_source_images": False,
            "warnings": [],
        }
    }
    perks = {
        "selected": [
            {
                "latest_selection_rank": 1,
                "display_text": "Boss health -73.5%, but boss speed +50%",
            }
        ],
        "quality": {"valid": True, "warnings": []},
    }
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME
    try:
        with (
            patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
            patch(
                "handlers.game_over_handler._capture_game_over_perks",
                return_value=(perks, [frame], True),
            ),
            patch(
                "handlers.game_over_handler._capture_clipboard_battle_record",
                return_value=(record, "clipboard_copy"),
            ),
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.scroll_to_edge") as scroll,
            patch("handlers.game_over_handler._persist_battle_stats_record") as persist,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(capture_stats=True)
    finally:
        AUTOMATION.mode = original_mode

    scroll.assert_not_called()
    assert record["perks"] == perks
    assert persist.call_args.kwargs["perks_frames"] == [frame]
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
