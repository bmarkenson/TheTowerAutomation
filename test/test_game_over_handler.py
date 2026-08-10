#!/usr/bin/env python3
# test/test_game_over_handler.py

import traceback
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np

from handlers.game_over_handler import (
    _capture_game_over_perk_tail,
    _capture_game_over_perks,
    _game_stats_visible,
    _record_game_over_mapping_observation,
    _resolve_game_over_perks,
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


def _complete_terminal_save_report():
    return {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "mapping_id": "data-9-game-1073",
        "completed_entry": {"schema_version": 1},
        "ui_fallback": {"required": False},
    }


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
        action_guard_fn=None,
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


def test_perks_capture_retries_close_until_game_stats_is_restored():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    perks = {"quality": {"perk_count": 1}}
    taps = []

    with (
        patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
        patch("handlers.game_over_handler._game_stats_visible", return_value=True),
        patch("handlers.game_over_handler._wait_for_visible", return_value=frame),
        patch(
            "handlers.game_over_handler.scroll_to_edge",
            return_value=ScrollResult(False, frame, 0, "edge_reached"),
        ),
        patch("handlers.game_over_handler.ocr_selected_perks", return_value=perks),
        patch(
            "handlers.game_over_handler.tap_if_visible",
            side_effect=lambda label, **_kwargs: taps.append(label) or True,
        ),
        patch("handlers.game_over_handler.is_visible", return_value=True),
        patch(
            "handlers.game_over_handler._wait_for_game_stats",
            side_effect=(None, frame),
        ),
    ):
        result, frames, restored = _capture_game_over_perks()

    assert result is perks
    assert frames == [frame]
    assert restored is True
    assert taps == [
        "buttons.perks:game_over",
        "buttons.close:perks",
        "buttons.close:perks",
    ]


def test_terminal_tail_capture_never_scrolls_through_the_full_inventory():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    parsed = {
        "source_method": "ocr",
        "order_semantics": "latest_selected_first",
        "selected": [{"display_text": "Orbs +1", "confidence": 95.0}],
        "quality": {"valid": True, "source_complete": True},
    }

    with (
        patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
        patch("handlers.game_over_handler._game_stats_visible", return_value=True),
        patch("handlers.game_over_handler._wait_for_visible", return_value=frame),
        patch(
            "handlers.game_over_handler.scroll_to_edge",
            return_value=ScrollResult(True, frame, 2, "edge_reached"),
        ),
        patch("handlers.game_over_handler.ocr_selected_perks", return_value=parsed),
        patch("handlers.game_over_handler.tap_if_visible", return_value=True),
        patch("handlers.game_over_handler._close_game_over_perks", return_value=True),
        patch("handlers.game_over_handler.capture_scroll_to_edge") as full_scroll,
    ):
        result, frames, restored = _capture_game_over_perk_tail()

    assert result["capture_scope"] == "newest_visible_prefix"
    assert result["quality"]["scope_complete"] is True
    assert result["quality"]["inventory_complete"] is False
    assert frames == [frame]
    assert restored is True
    full_scroll.assert_not_called()


def test_proven_final_prefix_skips_terminal_perks_navigation():
    checkpoint = {
        "picked_count": 1,
        "picks": [
            {
                "sequence": 1,
                "saved_wave": 100,
                "perk_id": 0,
                "perk_key": "max_health",
                "level_after": 1,
            }
        ],
    }
    inventory = {
        "source_method": "player_save_perk_checkpoint",
        "status": "complete_exact_saved_inventory",
        "exact_saved_prefix": checkpoint,
        "exact_saved_picks": checkpoint["picks"],
        "quality": {"valid": True, "source_complete": True},
    }
    context = {
        "perk_save_monitoring": {
            "status": "complete_final_prefix",
            "context_status": "bound",
            "active_failure_reason": None,
            "round_conflict_reason": None,
            "checkpoint": checkpoint,
            "exhaustion": {"binding_status": "active_round_identity_bound"},
            "terminal_window": {
                "status": "closed_by_inactive_cleared_projection"
            },
            "ui_fallback": {"required": False},
            "final_inventory": inventory,
        }
    }

    with patch(
        "handlers.game_over_handler._capture_game_over_perks"
    ) as capture_perks:
        perks, frames, restored = _resolve_game_over_perks(context)

    assert perks == inventory
    assert frames == []
    assert restored is True
    capture_perks.assert_not_called()


def test_malformed_complete_looking_inventory_preserves_terminal_perks_navigation():
    terminal_ui = {
        "selected": [],
        "quality": {"valid": False, "source_complete": False},
    }
    context = {
        "perk_save_monitoring": {
            "status": "complete_final_prefix",
            "context_status": "bound",
            "checkpoint": {"picked_count": 0, "picks": []},
            "exhaustion": {},
            "terminal_window": {
                "status": "closed_by_inactive_cleared_projection"
            },
            "ui_fallback": {"required": False},
            "final_inventory": {
                "source_method": "player_save_perk_checkpoint",
                "status": "complete_exact_saved_inventory",
                "exact_saved_prefix": {"picked_count": 0, "picks": []},
                "exact_saved_picks": [],
                "quality": {"valid": True, "source_complete": True},
            },
        }
    }

    with patch(
        "handlers.game_over_handler._capture_game_over_perk_tail",
        return_value=(terminal_ui, ["frame"], True),
    ) as capture_perks:
        perks, frames, restored = _resolve_game_over_perks(context)

    assert perks["selected"] == terminal_ui["selected"]
    assert perks["quality"]["valid"] is False
    assert perks["quality"]["retain_source_images"] is True
    assert any(
        "monitoring_record_unavailable" in warning
        for warning in perks["quality"]["warnings"]
    )
    assert frames == ["frame"]
    assert restored is True
    capture_perks.assert_called_once_with(action_guard_fn=None)


def test_pending_saved_prefix_uses_only_terminal_top_reconciliation():
    checkpoint = {
        "saved_wave": 200,
        "picked_count": 1,
        "picks": [
            {
                "sequence": 1,
                "saved_wave": 100,
                "perk_id": 0,
                "perk_key": "max_health",
                "level_after": 1,
            }
        ],
        "levels": [
            {"perk_id": 0, "perk_key": "max_health", "level": 1}
        ],
    }
    terminal_top = {
        "source_method": "terminal_perks_top_prefix_ocr",
        "capture_scope": "newest_visible_prefix",
        "order_semantics": "latest_selected_first",
        "selected": [
            {
                "display_text": "Orbs +1",
                "latest_selection_rank": 1,
                "instance_model": "single_instance",
                "confidence": 96.0,
            },
            {
                "display_text": "x1.25 Max Health",
                "latest_selection_rank": 2,
                "instance_model": "leveled",
                "confidence": 95.0,
            },
        ],
        "quality": {"valid": True, "scope_complete": True},
    }
    context = {
        "last_wave": 300,
        "perk_selection_timeline": {
            "passive_top_bar": {
                "selection_boundaries": [
                    {"scheduled_wave": 250, "boundary_coverage": "complete"}
                ]
            }
        },
        "perk_save_monitoring": {
            "schema_version": 1,
            "status": "fallback_required",
            "context_status": "bound",
            "active_failure_reason": None,
            "round_conflict_reason": None,
            "checkpoint": checkpoint,
        },
    }

    with (
        patch(
            "handlers.game_over_handler._capture_game_over_perk_tail",
            return_value=(terminal_top, ["top-frame"], True),
        ) as capture_top,
        patch("handlers.game_over_handler._capture_game_over_perks") as capture_full,
    ):
        perks, frames, restored = _resolve_game_over_perks(context)

    assert perks["source_method"] == (
        "player_save_checkpoint_plus_terminal_top_prefix"
    )
    assert perks["terminal_tail"]["aggregates"][0]["perk_key"] == "orbs"
    assert frames == ["top-frame"]
    assert restored is True
    capture_top.assert_called_once_with(action_guard_fn=None)
    capture_full.assert_not_called()


def test_missing_terminal_closure_preserves_existing_perks_navigation():
    terminal_ui = {
        "selected": [],
        "quality": {"valid": False, "source_complete": False},
    }
    context = {
        "perk_save_monitoring": {
            "status": "fallback_required",
            "reason": "terminal_checkpoint_window_unbound",
            "checkpoint": None,
            "ui_fallback": {"required": True},
        }
    }

    with patch(
        "handlers.game_over_handler._capture_game_over_perks",
        return_value=(terminal_ui, ["frame"], True),
    ) as capture_perks:
        perks, frames, restored = _resolve_game_over_perks(context)

    assert perks is terminal_ui
    assert frames == ["frame"]
    assert restored is True
    capture_perks.assert_called_once_with(action_guard_fn=None)


def test_home_mode_taps_game_stats_home_instead_of_retry():
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME
    try:
        with (
            patch("handlers.game_over_handler._make_session_id", return_value="test"),
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.log_action_intent") as action_log,
            patch("handlers.game_over_handler.log_result") as result_log,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(capture_stats=False)
    finally:
        AUTOMATION.mode = original_mode

    tap.assert_called_once_with(
        "buttons.home:game_over",
        retries=1,
        action_guard_fn=None,
    )
    action_log.assert_called_once_with(
        "Completing the finished battle",
        reason="follow the configured post-run route without stats capture",
        detail="[GAME_OVER] session=test capture_stats=False",
    )
    result_log.assert_called_once_with(
        "Finished-battle handling complete — stats capture skipped; returned Home",
        detail=(
            "[GAME_OVER] result=completed session=test route=home stats=skipped"
        ),
    )


def test_game_over_terminal_failure_preserves_enabled_for_retry():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.HOME
    try:
        with (
            patch("handlers.game_over_handler._make_session_id", return_value="test"),
            patch("handlers.game_over_handler.tap_if_visible", return_value=False),
            patch("handlers.game_over_handler.capture_adb_screenshot"),
            patch("handlers.game_over_handler.save_image"),
            patch("handlers.game_over_handler.log_result") as result_log,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            outcome = handle_game_over(capture_stats=False)
    finally:
        assert AUTOMATION.state is RunState.RUNNING
        assert AUTOMATION.mode is ExecMode.HOME
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert outcome.route_completed is False
    assert outcome.route == "pending_retry"
    assert outcome.failure_step == "Go Home from Game Stats"
    result_log.assert_called_once_with(
        "Finished-battle route pending — Go Home from Game Stats did not complete",
        detail=(
            "[GAME_OVER] result=pending_retry session=test "
            "failed_step=Go Home from Game Stats terminal_policy=HOME "
            "action_authority=RUNNING retry=true"
        ),
    )


def test_game_over_route_retries_after_a_transient_terminal_tap_failure():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
    try:
        with (
            patch(
                "handlers.game_over_handler.tap_if_visible",
                side_effect=[False, True],
            ),
            patch("handlers.game_over_handler.capture_adb_screenshot"),
            patch("handlers.game_over_handler.save_image"),
            patch("handlers.game_over_handler.time.sleep"),
        ):
            first = handle_game_over(capture_stats=False)
            second = handle_game_over(capture_stats=False)
    finally:
        assert AUTOMATION.state is RunState.RUNNING
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert first.route_completed is False
    assert first.route == "pending_retry"
    assert second.route_completed is True
    assert second.route == "retry"


def test_unavailable_stats_capture_does_not_block_retry():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
    try:
        with (
            patch("handlers.game_over_handler.capture_adb_screenshot", return_value=None),
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            outcome = handle_game_over(capture_stats=True)
    finally:
        assert AUTOMATION.state is RunState.RUNNING
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert outcome.route_completed is True
    assert outcome.route == "retry"
    assert outcome.stats_status == "unavailable"
    tap.assert_called_once_with(
        "buttons.retry:game_over",
        retries=1,
        action_guard_fn=None,
    )


def test_game_over_stop_emits_interrupted_terminal_result():
    original_state = AUTOMATION.state
    AUTOMATION.state = RunState.STOPPED
    try:
        with (
            patch("handlers.game_over_handler._make_session_id", return_value="test"),
            patch("handlers.game_over_handler.tap_if_visible") as tap,
            patch("handlers.game_over_handler.log_result") as result_log,
        ):
            handle_game_over(capture_stats=False)
    finally:
        AUTOMATION.state = original_state

    tap.assert_not_called()
    result_log.assert_called_once_with(
        (
            "Finished-battle handling interrupted — automation stopped "
            "before post-run navigation"
        ),
        detail=(
            "[GAME_OVER] result=interrupted session=test capture_stats=False "
            "stats_saved=False"
        ),
    )


def test_guarded_preflight_repair_forces_home_after_control_allows_actions():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
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

    tap.assert_called_once_with(
        "buttons.home:game_over",
        retries=1,
        action_guard_fn=None,
    )


def test_required_post_run_home_inventory_bypasses_wait_mode():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.WAIT
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

    tap.assert_called_once_with(
        "buttons.home:game_over",
        retries=1,
        action_guard_fn=None,
    )


def test_required_post_run_home_inventory_still_waits_while_paused():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.PAUSED
    AUTOMATION.mode = ExecMode.WAIT
    sync_calls = 0

    def sync_control():
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            AUTOMATION.state = RunState.RUNNING

    try:
        with patch("handlers.game_over_handler.time.sleep") as sleep:
            direction = _wait_for_game_over_direction(
                sync_control,
                wait_mode_blocks=False,
            )
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert direction is ExecMode.WAIT
    assert sync_calls == 2
    sleep.assert_called_once_with(1)


def test_game_over_wait_reports_completed_actions_before_polling_for_direction():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.WAIT
    result_log = None

    def sync_control():
        assert result_log is not None
        assert result_log.call_count == 1
        AUTOMATION.mode = ExecMode.HOME

    try:
        with (
            patch("handlers.game_over_handler._make_session_id", return_value="test"),
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.log_action_intent") as action_log,
            patch("handlers.game_over_handler.log_result") as result_log,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(
                capture_stats=False,
                control_sync=sync_control,
            )
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    tap.assert_called_once_with(
        "buttons.home:game_over",
        retries=1,
        action_guard_fn=None,
    )
    assert action_log.call_count == 2
    action_log.assert_any_call(
        "Following the finished-battle direction",
        reason="mode HOME was selected after WAIT",
        detail=(
            "[GAME_OVER] session=test previous_mode=WAIT next_mode=HOME"
        ),
    )
    assert result_log.call_args_list[0].args == (
        "Finished-battle actions complete — stats capture skipped; automation "
        "is waiting on the Game Over screen (mode WAIT)",
    )
    assert result_log.call_args_list[0].kwargs == {
        "detail": (
            "[GAME_OVER] result=completed session=test route=wait "
            "stats=skipped next_mode=WAIT"
        )
    }
    assert result_log.call_args_list[1].args == (
        "Finished-battle handling complete — stats capture skipped; returned Home",
    )


def test_game_over_finalizes_boundary_before_terminal_navigation():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
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
                after_retry_started=lambda: events.append(("scope", None)),
            )
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert events == [
        ("boundary", None),
        ("tap", "buttons.retry:game_over"),
        ("scope", None),
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
            AUTOMATION.mode = ExecMode.NEXT_BATTLE
        else:
            AUTOMATION.state = RunState.RUNNING

    try:
        with patch("handlers.game_over_handler.time.sleep") as sleep:
            direction = _wait_for_game_over_direction(sync_control)
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode

    assert direction is ExecMode.NEXT_BATTLE
    assert sync_calls == 2
    sleep.assert_called_once_with(1)


def test_game_over_wait_exits_when_control_stops_automation():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    AUTOMATION.state = RunState.RUNNING
    AUTOMATION.mode = ExecMode.WAIT

    def sync_control():
        AUTOMATION.state = RunState.STOPPED
        AUTOMATION.mode = ExecMode.NEXT_BATTLE

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
    disposition = {
        "outcome": "surrendered",
        "collection": "full_terminal_ui",
        "representative": False,
    }
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
            patch("handlers.game_over_handler._wait_for_game_stats", return_value=frame),
            patch("handlers.game_over_handler.time.sleep"),
        ):
            handle_game_over(
                capture_stats=True,
                report_disposition=disposition,
            )
    finally:
        AUTOMATION.mode = original_mode

    capture_scroll.assert_not_called()
    assert save_record.call_args.kwargs["source_complete"] is False
    assert save_record.call_args.kwargs["source_reason"] == "top_max_swipes_exceeded"
    assert save_record.call_args.kwargs["report_disposition"] == disposition
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
        },
        "game_stats": {
            "fields": {
                "killed_by": {"value": "Boss", "raw": "B0ss"},
            }
        },
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
    disposition = {
        "outcome": "surrendered",
        "collection": "full_terminal_ui",
        "representative": False,
    }
    mapping_observations = []
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
            patch("handlers.game_over_handler._wait_for_game_stats", return_value=frame),
            patch("handlers.game_over_handler.time.sleep"),
        ):
            result = handle_game_over(
                capture_stats=True,
                report_disposition=disposition,
                mapping_observation_fn=(
                    lambda check_id, evidence: mapping_observations.append(
                        (check_id, evidence)
                    )
                ),
            )
    finally:
        AUTOMATION.mode = original_mode

    scroll.assert_not_called()
    assert result.route_completed is True
    assert result.record is record
    assert result.stats_status == "saved"
    assert record["perks"] == perks
    assert record["report_disposition"] == disposition
    assert persist.call_args.kwargs["perks_frames"] == [frame]
    assert len(mapping_observations) == 1
    check_id, evidence = mapping_observations[0]
    assert check_id == "battle_history_killed_by"
    assert evidence["canonical_values"] == ["Boss"]
    assert evidence["locator_values"] == {"killed_by": "Boss"}
    assert "B0ss" not in str(evidence)
    assert [call.args[0] for call in tap.call_args_list] == [
        "buttons.more_stats:game_over",
        "buttons.close:more_stats",
        "buttons.home:game_over",
    ]


def test_player_save_success_never_opens_more_stats_and_keeps_perk_order():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    record = {
        "quality": {
            "valid": True,
            "retain_source_images": False,
            "warnings": [],
        },
        "more_stats": {"quality": {"row_count": 144}},
        "game_stats": {
            "fields": {"killed_by": {"value": "Boss", "raw": "B0ss"}}
        },
    }
    perks = {
        "selected": [
            {
                "latest_selection_rank": 1,
                "display_text": "Coins +1.98x, but Tower Max Health -70%",
            }
        ],
        "quality": {"valid": True, "warnings": []},
    }
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME
    disposition = {
        "outcome": "surrendered",
        "collection": "full_terminal_ui",
        "representative": False,
    }
    try:
        with (
            patch("handlers.game_over_handler.capture_adb_screenshot", return_value=frame),
            patch(
                "handlers.game_over_handler._capture_game_over_perks",
                return_value=(perks, [frame], True),
            ),
            patch(
                "handlers.game_over_handler.build_battle_record_from_player_save",
                return_value=record,
            ) as build,
            patch(
                "handlers.game_over_handler._capture_clipboard_battle_record"
            ) as clipboard,
            patch("handlers.game_over_handler.tap_if_visible", return_value=True) as tap,
            patch("handlers.game_over_handler.scroll_to_edge") as scroll,
            patch("handlers.game_over_handler._persist_battle_stats_record") as persist,
            patch("handlers.game_over_handler.time.sleep"),
        ):
            result = handle_game_over(
                capture_stats=True,
                battle_context={
                    "strategy": "farm_t19",
                    "terminal_state": "GAME_OVER",
                    "terminal_save_report": _complete_terminal_save_report(),
                },
                report_disposition=disposition,
                mapping_observation_fn=lambda *_args: (_ for _ in ()).throw(
                    RuntimeError("diagnostic write failed")
                ),
            )
    finally:
        AUTOMATION.mode = original_mode

    assert result.route_completed is True
    assert result.record is record
    assert result.stats_status == "saved"
    assert record["perks"] == perks
    assert record["report_disposition"] == disposition
    assert build.call_args.kwargs["strategy_name"] == "farm_t19"
    assert "terminal_save_report" not in build.call_args.kwargs["runtime_context"]
    persist.assert_called_once()
    clipboard.assert_not_called()
    scroll.assert_not_called()
    assert [call.args[0] for call in tap.call_args_list] == [
        "buttons.home:game_over"
    ]


def test_terminal_mapping_observation_never_uses_raw_ocr_text():
    observations = []
    _record_game_over_mapping_observation(
        {
            "quality": {"valid": True},
            "game_stats": {
                "fields": {"killed_by": {"value": None, "raw": "B0ss"}}
            },
        },
        lambda check_id, evidence: observations.append((check_id, evidence)),
        observed_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    assert observations == []


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
