from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from core.battle_lifecycle import HomeBattleControl
from core.home_perk_configuration import (
    BAN_SELECTED_TOGGLE_X,
    HomePerkConfigurationError,
    HomePerkConfigurationRepairExhausted,
    _capture_bans_with_ocr_retries,
    _capture_ranked_frames,
    _capture_ranked_order_with_ocr_retries,
    _close_to_home,
    _locate_auto_pick_key,
    _reacquire_auto_pick_move_context,
    _repair_auto_pick_order,
    _repair_bans,
    _scroll_ban_configuration_top,
    _tap_configuration_row,
    ensure_home_perk_configuration,
)
from core.perk_configuration import (
    FARM_AUTO_PICK_ORDER,
    FARM_PERK_BANS,
    PERK_CONFIGURATION_OCR_EXEMPLARS,
    classify_perk_configuration_text,
)


LABELS = {
    "cash_tradeoff": "x13.20 cash per wave, but enemy kills don't give cash",
    "enemies_damage_tradeoff": (
        "Enemies damage -55.0%, but tower damage -50%"
    ),
    "lifesteal_knockback_tradeoff": (
        "Lifesteal x2.75, but knockback force -70%"
    ),
    "interest": "Interest x1.88",
    "defense_absolute": "x1.44 Defense Absolute",
    "land_mine_damage": "Land Mine Damage x4.38",
    "cash_bonus": "x1.44 Cash Bonus",
    "perk_wave_requirement": "Perk wave requirement -25.00%",
    "game_speed": "Increase max game speed by +1.25",
    "coin_tradeoff": "x1.98 coins, but tower max health -70.0%",
    "golden_tower_bonus": "Golden tower bonus x1.5",
    "max_health": "x1.50 max health",
    "empty_slot": "Empty Slot",
}


def _row(key, index):
    text = (
        LABELS[key]
        if key in LABELS
        else PERK_CONFIGURATION_OCR_EXEMPLARS[key]
    )
    return {
        "top": 430 + index * 172,
        "bottom": 587 + index * 172,
        "display_text": text,
        "text_raw": text,
        "confidence": 95.0,
        "background_value_median": 100.0,
    }


def test_auto_pick_repair_inserts_missing_coin_tradeoff_at_strategy_rank():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    order = [
        "perk_wave_requirement",
        "game_speed",
        "golden_tower_bonus",
        "max_health",
        "cash_bonus",
        "coin_tradeoff",
    ]
    expected = [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
        "golden_tower_bonus",
        "max_health",
        "cash_bonus",
    ]
    observed = list(order)

    def rows(_frame):
        return [_row(key, index) for index, key in enumerate(order)]

    def move_up(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        index = order.index(key)
        order[index - 1], order[index] = order[index], order[index - 1]
        return current

    with (
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            side_effect=lambda current, **_kwargs: current,
        ) as scroll_top,
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=move_up,
        ) as tap,
        patch(
            "core.home_perk_configuration._locate_auto_pick_key",
            wraps=_locate_auto_pick_key,
        ) as locate,
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            wraps=_capture_ranked_order_with_ocr_retries,
        ) as final_scan,
    ):
        _repair_auto_pick_order(
            frame,
            expected,
            capture_fn=lambda: frame,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            observed_keys=observed,
            sleep_fn=lambda _seconds: None,
        )

    assert order == expected
    assert tap.call_count == 3
    assert tap.call_args.kwargs["action"] == "auto_pick_move_up:coin_tradeoff"
    assert [call.args[1] for call in locate.call_args_list] == ["coin_tradeoff"]
    assert final_scan.call_count == 1
    # One initial re-anchor from the prior full scan and one final read-back.
    # Correct suffix ranks do not trigger another trip to the top.
    assert scroll_top.call_count == 2


def test_auto_pick_repair_acquires_only_live_shaped_mismatches():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    expected = list(FARM_AUTO_PICK_ORDER)
    order = [key for key in expected if key != "free_upgrade_chance"]
    order.insert(12, "free_upgrade_chance")
    order.insert(15, "max_health")
    order.insert(16, "health_regen")
    observed = list(order[: len(expected)])

    assert order.index("free_upgrade_chance") + 1 == 13
    assert order.index("inner_land_mines") + 1 == 18
    assert order.index("damage") + 1 == 19

    def rows(_frame):
        return [_row(key, index) for index, key in enumerate(order)]

    def move_up(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        index = order.index(key)
        order[index - 1], order[index] = order[index], order[index - 1]
        return current

    with (
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            side_effect=lambda current, **_kwargs: current,
        ) as scroll_top,
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=move_up,
        ) as tap,
        patch(
            "core.home_perk_configuration._locate_auto_pick_key",
            wraps=_locate_auto_pick_key,
        ) as locate,
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            wraps=_capture_ranked_order_with_ocr_retries,
        ) as final_scan,
    ):
        _repair_auto_pick_order(
            frame,
            expected,
            capture_fn=lambda: frame,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            observed_keys=observed,
            sleep_fn=lambda _seconds: None,
        )

    assert order[: len(expected)] == expected
    assert tap.call_count == 9
    assert [call.args[1] for call in locate.call_args_list] == [
        "free_upgrade_chance",
        "inner_land_mines",
        "damage",
    ]
    assert final_scan.call_count == 1
    assert scroll_top.call_count == 2


def test_auto_pick_repair_moves_black_hole_past_off_prefix_chrono():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    expected = [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
        "golden_tower_bonus",
        "black_hole_duration",
    ]
    order = [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
        "enemy_health_tradeoff",
        "enemy_speed_tradeoff",
        "golden_tower_bonus",
        "chain_lightning_damage",
        "chrono_field_duration",
        "black_hole_duration",
    ]
    observed = list(order[: len(expected)])

    def rows(_frame):
        return [_row(key, index) for index, key in enumerate(order)]

    def move_up(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        index = order.index(key)
        order[index - 1], order[index] = order[index], order[index - 1]
        return current

    final = {
        "quality": {"valid": True},
        "selected": [{"key": key} for key in expected],
    }
    with (
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            side_effect=lambda current, **_kwargs: current,
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=move_up,
        ) as tap,
        patch(
            "core.home_perk_configuration._recapture_auto_pick_semantic_order",
            side_effect=AssertionError(
                "Chrono has a canonical local identity; the ranked prefix "
                "does not need to be rescanned"
            ),
        ) as recapture,
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            return_value=([frame], frame, final),
        ),
    ):
        _repair_auto_pick_order(
            frame,
            expected,
            capture_fn=lambda: frame,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            observed_keys=observed,
            sleep_fn=lambda _seconds: None,
        )

    assert order[: len(expected)] == expected
    assert tap.call_count == 6
    recapture.assert_not_called()


def test_auto_pick_unknown_predecessor_retries_locally_without_prefix_rescan():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    order = [
        "perk_wave_requirement",
        "game_speed",
        "golden_tower_bonus",
        "unknown_perk",
        "coin_tradeoff",
    ]
    sleeps = []

    def rows(_frame):
        result = []
        for index, key in enumerate(order):
            if key == "unknown_perk":
                result.append(
                    {
                        "top": 430 + index * 172,
                        "bottom": 587 + index * 172,
                        "display_text": "Unreadable local perk text",
                        "text_raw": "Unreadable local perk text",
                        "confidence": 35.0,
                        "background_value_median": 100.0,
                    }
                )
            else:
                result.append(_row(key, index))
        return result

    with (
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            side_effect=lambda current, **_kwargs: current,
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
        ) as tap,
        patch(
            "core.home_perk_configuration._recapture_auto_pick_semantic_order",
        ) as recapture,
    ):
        with pytest.raises(
            HomePerkConfigurationRepairExhausted,
            match="Unreadable local perk text",
        ):
            _repair_auto_pick_order(
                frame,
                [
                    "perk_wave_requirement",
                    "game_speed",
                    "coin_tradeoff",
                ],
                capture_fn=lambda: frame,
                detector=lambda _frame: {"state": "PERKS"},
                safe_tap_fn=lambda *_args, **_kwargs: True,
                visible_fn=lambda *_args, **_kwargs: True,
                swipe_fn=lambda _key: True,
                row_fn=rows,
                row_near_fn=lambda *_args, **_kwargs: None,
                observed_keys=order[:3],
                sleep_fn=sleeps.append,
            )

    tap.assert_not_called()
    recapture.assert_not_called()
    assert sleeps == [0.6, 0.6]


def test_auto_pick_repair_rechecks_rank_after_viewport_reflow():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    order = [
        "perk_wave_requirement",
        "game_speed",
        "golden_tower_bonus",
        "coin_tradeoff",
    ]
    viewport_offset = {"value": 0}

    def rows(_frame):
        result = [_row(key, index) for index, key in enumerate(order)]
        for row in result:
            row["top"] += viewport_offset["value"]
            row["bottom"] += viewport_offset["value"]
        return result

    def scroll_top(current, **_kwargs):
        viewport_offset["value"] = 0
        return current

    def move_up(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        index = order.index(key)
        order[index - 1], order[index] = order[index], order[index - 1]
        # Simulate the live list reflow keeping the moved row at the same
        # physical Y coordinate even though its semantic rank advanced.
        viewport_offset["value"] = 172
        return current

    with (
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            side_effect=scroll_top,
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=move_up,
        ),
    ):
        _repair_auto_pick_order(
            frame,
            [
                "perk_wave_requirement",
                "game_speed",
                "coin_tradeoff",
            ],
            capture_fn=lambda: frame,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            observed_keys=[
                "perk_wave_requirement",
                "game_speed",
                "golden_tower_bonus",
            ],
            sleep_fn=lambda _seconds: None,
        )

    assert order[:3] == [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
    ]


def test_auto_pick_repair_resyncs_conflicting_context_before_input():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    expected = [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
        "golden_tower_bonus",
    ]
    order = [
        "perk_wave_requirement",
        "game_speed",
        "golden_tower_bonus",
        "coin_tradeoff",
    ]
    context_consistent = {"value": False}
    events = []

    def rows(_frame):
        visible = (
            order
            if context_consistent["value"]
            else [
                "perk_wave_requirement",
                "game_speed",
                "max_health",
                "coin_tradeoff",
            ]
        )
        return [_row(key, index) for index, key in enumerate(visible)]

    def resync(current, **_kwargs):
        events.append("resync")
        context_consistent["value"] = True
        return current, list(order)

    def move_up(current, row, **_kwargs):
        events.append("tap")
        key = classify_perk_configuration_text(row["display_text"])
        index = order.index(key)
        order[index - 1], order[index] = order[index], order[index - 1]
        return current

    final = {
        "quality": {"valid": True},
        "selected": [{"key": key} for key in expected],
    }
    with (
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            side_effect=lambda current, **_kwargs: current,
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=move_up,
        ) as tap,
        patch(
            "core.home_perk_configuration._recapture_auto_pick_semantic_order",
            side_effect=resync,
        ) as recapture,
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            return_value=([frame], frame, final),
        ),
    ):
        _repair_auto_pick_order(
            frame,
            expected,
            capture_fn=lambda: frame,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            observed_keys=list(order),
            sleep_fn=lambda _seconds: None,
        )

    assert events == ["resync", "tap"]
    recapture.assert_called_once()
    tap.assert_called_once()
    assert order == expected


def test_auto_pick_repair_refuses_move_without_one_rank_progress():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    order = [
        "perk_wave_requirement",
        "game_speed",
        "golden_tower_bonus",
        "coin_tradeoff",
    ]

    def rows(_frame):
        return [_row(key, index) for index, key in enumerate(order)]

    with (
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            side_effect=lambda current, **_kwargs: current,
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=lambda current, _row, **_kwargs: current,
        ),
    ):
        with pytest.raises(
            HomePerkConfigurationError,
            match="did not make one verified local upward swap",
        ):
            _repair_auto_pick_order(
                frame,
                [
                    "perk_wave_requirement",
                    "game_speed",
                    "coin_tradeoff",
                ],
                capture_fn=lambda: frame,
                detector=lambda _frame: {"state": "PERKS"},
                safe_tap_fn=lambda *_args, **_kwargs: True,
                visible_fn=lambda *_args, **_kwargs: True,
                swipe_fn=lambda _key: True,
                row_fn=rows,
                row_near_fn=lambda *_args, **_kwargs: None,
                observed_keys=[
                    "perk_wave_requirement",
                    "game_speed",
                    "golden_tower_bonus",
                ],
                sleep_fn=lambda _seconds: None,
            )


def test_auto_pick_repair_confirms_delayed_local_swap():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    expected = [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
    ]
    order = [
        "perk_wave_requirement",
        "game_speed",
        "golden_tower_bonus",
        "coin_tradeoff",
    ]
    sleeps = []
    pending_move = {"value": False}

    def rows(_frame):
        return [_row(key, index) for index, key in enumerate(order)]

    def move_up(current, _row, **_kwargs):
        pending_move["value"] = True
        return current

    def sleep(seconds):
        sleeps.append(seconds)
        if seconds == 0.8 and pending_move["value"]:
            order[2], order[3] = order[3], order[2]
            pending_move["value"] = False

    with (
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=move_up,
        ) as tap,
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            return_value=(
                [frame],
                frame,
                {
                    "quality": {"valid": True},
                    "selected": [{"key": key} for key in expected],
                },
            ),
        ),
    ):
        _repair_auto_pick_order(
            frame,
            expected,
            capture_fn=lambda: frame,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            observed_keys=[
                "perk_wave_requirement",
                "game_speed",
                "golden_tower_bonus",
            ],
            sleep_fn=sleep,
        )

    assert tap.call_count == 1
    assert 0.8 in sleeps


def test_auto_pick_repair_scrolls_up_locally_when_moved_row_leaves_viewport():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    order = [
        "perk_wave_requirement",
        "game_speed",
        "golden_tower_bonus",
        "coin_tradeoff",
    ]
    viewport_start = {"value": 2}

    def rows(_frame):
        visible = order[viewport_start["value"] : viewport_start["value"] + 2]
        return [_row(key, index) for index, key in enumerate(visible)]

    def move_up(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        index = order.index(key)
        order[index - 1], order[index] = order[index], order[index - 1]
        # The moved row is now just above the retained viewport.
        viewport_start["value"] = 3
        return current

    def capture():
        return np.full_like(frame, 10 * viewport_start["value"])

    swipes = []

    def previous(key):
        assert key == "gesture_targets.goto_previous:perks"
        swipes.append(key)
        viewport_start["value"] = max(0, viewport_start["value"] - 1)
        return True

    with (
        patch(
            "core.home_perk_configuration._locate_auto_pick_key",
            return_value=(4, frame, _row("coin_tradeoff", 1)),
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=move_up,
        ),
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            side_effect=lambda *_args, **_kwargs: (
                [frame],
                frame,
                {
                    "quality": {"valid": True},
                    "selected": [{"key": key} for key in order[:3]],
                },
            ),
        ),
    ):
        _repair_auto_pick_order(
            frame,
            [
                "perk_wave_requirement",
                "game_speed",
                "coin_tradeoff",
            ],
            capture_fn=capture,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=previous,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            observed_keys=[
                "perk_wave_requirement",
                "game_speed",
                "golden_tower_bonus",
            ],
            sleep_fn=lambda _seconds: None,
        )

    assert order[:3] == [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
    ]
    assert swipes == [
        "gesture_targets.goto_previous:perks",
        "gesture_targets.goto_previous:perks",
    ]


def test_ban_ocr_retry_uses_fresh_capture_without_configuration_input():
    initial = np.zeros((1920, 1080, 3), dtype=np.uint8)
    fresh = np.ones((1920, 1080, 3), dtype=np.uint8)
    invalid = {
        "quality": {
            "valid": False,
            "ocr_retry_recommended": True,
            "closest_matches": [],
            "warnings": ["Unrecognized banned perks: Cosh Bonvs"],
        }
    }
    valid = {
        "quality": {
            "valid": True,
            "ocr_retry_recommended": False,
            "warnings": [],
        }
    }
    capture = Mock(return_value=fresh)
    sleeps = []

    with patch(
        "core.home_perk_configuration.extract_configured_perk_bans",
        side_effect=[invalid, valid],
    ) as extract:
        frame, result = _capture_bans_with_ocr_retries(
            initial,
            capture_fn=capture,
            detector=lambda _frame: {"state": "PERKS"},
            visible_fn=lambda *_args, **_kwargs: True,
            row_fn=lambda _frame: [],
            sleep_fn=sleeps.append,
        )

    assert frame is fresh
    assert result is valid
    assert extract.call_count == 2
    capture.assert_called_once_with()
    assert sleeps == [0.6]


def test_ban_top_scroll_requires_complete_selected_block_boundary():
    current = np.full((1920, 1080, 3), 1, dtype=np.uint8)
    available = np.full((1920, 1080, 3), 2, dtype=np.uint8)
    selected_top = np.full((1920, 1080, 3), 3, dtype=np.uint8)
    captures = iter((available, selected_top))
    swipes = []

    def rows(frame):
        at_top = int(frame[0, 0, 0]) == 3
        keys = (
            FARM_PERK_BANS
            if at_top
            else (
                "perk_wave_requirement",
                "game_speed",
                "golden_tower_bonus",
                "max_health",
                "cash_bonus",
                "defense_absolute",
            )
        )
        return [
            {
                **_row(key, index),
                "selected_outline": at_top,
            }
            for index, key in enumerate(keys)
        ]

    result = _scroll_ban_configuration_top(
        current,
        capture_fn=lambda: next(captures),
        visible_fn=lambda *_args, **_kwargs: True,
        swipe_fn=lambda key: swipes.append(key) or True,
        row_fn=rows,
        sleep_fn=lambda _seconds: None,
    )

    assert result is selected_top
    assert swipes == [
        "gesture_targets.goto_top:perks",
        "gesture_targets.goto_top:perks",
    ]


def test_auto_pick_ranked_scan_retries_one_ignored_next_swipe():
    top = np.full((1920, 1080, 3), 1, dtype=np.uint8)
    next_page = np.full((1920, 1080, 3), 2, dtype=np.uint8)
    captures = iter((top, next_page))
    swipes = []

    def rows(frame):
        keys = (
            ("perk_wave_requirement", "game_speed")
            if int(frame[0, 0, 0]) == 1
            else ("game_speed", "coin_tradeoff")
        )
        return [_row(key, index) for index, key in enumerate(keys)]

    frames, current = _capture_ranked_frames(
        top,
        ranking_count=3,
        capture_fn=lambda: next(captures),
        visible_fn=lambda *_args, **_kwargs: True,
        swipe_fn=lambda key: swipes.append(key) or True,
        row_fn=rows,
        sleep_fn=lambda _seconds: None,
    )

    assert frames == [top, next_page]
    assert current is next_page
    assert swipes == [
        "gesture_targets.goto_next:perks",
        "gesture_targets.goto_next:perks",
    ]


def test_auto_pick_key_locator_retries_one_ignored_next_swipe():
    top = np.full((1920, 1080, 3), 1, dtype=np.uint8)
    next_page = np.full((1920, 1080, 3), 2, dtype=np.uint8)
    captures = iter((top, next_page))
    swipes = []

    def rows(frame):
        keys = (
            ("perk_wave_requirement", "game_speed")
            if int(frame[0, 0, 0]) == 1
            else ("game_speed", "coin_tradeoff")
        )
        return [_row(key, index) for index, key in enumerate(keys)]

    with patch(
        "core.home_perk_configuration._scroll_configuration_top",
        return_value=top,
    ):
        rank, current, row = _locate_auto_pick_key(
            top,
            "coin_tradeoff",
            capture_fn=lambda: next(captures),
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda key: swipes.append(key) or True,
            row_fn=rows,
            sleep_fn=lambda _seconds: None,
        )

    assert rank == 3
    assert current is next_page
    assert row["key"] == "coin_tradeoff"
    assert swipes == [
        "gesture_targets.goto_next:perks",
        "gesture_targets.goto_next:perks",
    ]


def test_auto_pick_local_reacquire_retries_one_ignored_previous_swipe():
    current = np.full((1920, 1080, 3), 1, dtype=np.uint8)
    previous_page = np.full((1920, 1080, 3), 2, dtype=np.uint8)
    captures = iter((current, previous_page))
    swipes = []

    def rows(frame):
        keys = (
            ("coin_tradeoff",)
            if int(frame[0, 0, 0]) == 1
            else ("game_speed", "coin_tradeoff")
        )
        return [_row(key, index) for index, key in enumerate(keys)]

    frame, row, predecessor, following, at_top = (
        _reacquire_auto_pick_move_context(
            current,
            "coin_tradeoff",
            capture_fn=lambda: next(captures),
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda key: swipes.append(key) or True,
            row_fn=rows,
            sleep_fn=lambda _seconds: None,
        )
    )

    assert frame is previous_page
    assert row["key"] == "coin_tradeoff"
    assert predecessor is not None
    assert predecessor["key"] == "game_speed"
    assert following is None
    assert at_top is False
    assert swipes == [
        "gesture_targets.goto_previous:perks",
        "gesture_targets.goto_previous:perks",
    ]


def test_auto_pick_ocr_retry_rescans_locally_from_top_once():
    initial = np.zeros((1920, 1080, 3), dtype=np.uint8)
    fresh = np.ones((1920, 1080, 3), dtype=np.uint8)
    invalid = {
        "quality": {
            "valid": False,
            "ocr_retry_recommended": True,
            "closest_matches": [
                {
                    "display_text": "Chain lightninq damaqe x2",
                    "suggested_label": "Chain Lightning Damage",
                    "score": 0.91,
                    "margin": 0.40,
                    "retry_recommended": True,
                }
            ],
            "warnings": [
                "Unrecognized Auto Pick perks: Chain lightninq damaqe x2"
            ],
        }
    }
    valid = {
        "quality": {
            "valid": True,
            "ocr_retry_recommended": False,
            "warnings": [],
        },
        "selected": [{"key": "chain_lightning_damage"}],
    }
    capture = Mock(return_value=fresh)
    sleeps = []

    with (
        patch(
            "core.home_perk_configuration._capture_ranked_frames",
            side_effect=[([initial], initial), ([fresh], fresh)],
        ) as scan,
        patch(
            "core.home_perk_configuration.extract_ranked_auto_pick_order",
            side_effect=[invalid, valid],
        ),
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            return_value=fresh,
        ) as scroll_top,
    ):
        frames, current, result = _capture_ranked_order_with_ocr_retries(
            initial,
            ranking_count=1,
            capture_fn=capture,
            detector=lambda _frame: {"state": "PERKS"},
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=lambda _frame: [],
            sleep_fn=sleeps.append,
        )

    assert frames == [fresh]
    assert current is fresh
    assert result is valid
    assert scan.call_count == 2
    capture.assert_called_once_with()
    scroll_top.assert_called_once()
    assert sleeps == [0.6]


def test_close_to_home_waits_for_new_battle_control_after_home_appears():
    unknown = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    new_battle = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    captures = iter((unknown, new_battle))
    sleeps = []

    result = _close_to_home(
        unknown,
        capture_fn=lambda: next(captures),
        detector=lambda _frame: {"state": "HOME_SCREEN"},
        detect_home_control_fn=lambda frame: SimpleNamespace(
            control=(
                HomeBattleControl.UNKNOWN
                if frame is unknown
                else HomeBattleControl.NEW_BATTLE
            )
        ),
        tap_visible_fn=lambda *_args, **_kwargs: True,
        sleep_fn=sleeps.append,
    )

    assert result is new_battle
    assert sleeps == [0.25]


def test_ban_repair_toggles_only_the_strategy_set():
    page = {"value": 0}
    selected = [
        "enemies_damage_tradeoff",
        "lifesteal_knockback_tradeoff",
        "interest",
        "defense_absolute",
        "coin_tradeoff",
    ]

    def screenshot():
        return np.full(
            (1920, 1080, 3),
            10 + page["value"],
            dtype=np.uint8,
        )

    def rows(_frame):
        if page["value"] == 0:
            keys = [*selected]
            keys.extend(["empty_slot"] * (6 - len(keys)))
        else:
            keys = ["coin_tradeoff", "land_mine_damage", "cash_bonus"]
        return [
            {
                **_row(key, index),
                "selected_outline": page["value"] == 0,
            }
            for index, key in enumerate(keys)
        ]

    def swipe(_current, _key, **_kwargs):
        page["value"] += 1
        return screenshot()

    def toggle(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        configured = key
        if configured in selected:
            selected.remove(configured)
        else:
            selected.append(configured)
        return current

    def top(current, **_kwargs):
        page["value"] = 0
        return current

    with (
        patch(
            "core.home_perk_configuration._swipe_configuration",
            side_effect=swipe,
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=toggle,
        ) as tap,
        patch(
            "core.home_perk_configuration._scroll_ban_configuration_top",
            side_effect=top,
        ),
    ):
        _repair_bans(
            screenshot(),
            FARM_PERK_BANS,
            capture_fn=screenshot,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            sleep_fn=lambda _seconds: None,
        )

    assert set(selected) == set(FARM_PERK_BANS)
    assert tap.call_count == 3
    assert tap.call_args_list[0].kwargs["x"] == BAN_SELECTED_TOGGLE_X
    assert (
        tap.call_args_list[0].kwargs["action"]
        == "perk_ban_deselect:coin_tradeoff"
    )
    assert (
        tap.call_args_list[1].kwargs["action"]
        == "perk_ban_toggle:land_mine_damage"
    )
    assert (
        tap.call_args_list[2].kwargs["action"]
        == "perk_ban_toggle:cash_bonus"
    )


def test_ban_repair_confirms_delayed_deselection_without_leaving_tab():
    page = {"value": 0}
    selected = [
        key
        for key in FARM_PERK_BANS
        if key not in {"land_mine_damage", "cash_bonus"}
    ]
    selected.extend(["cash_tradeoff", "coins_bonus"])
    pending_removal = {"key": None}
    sleeps = []
    selected_counts = [len(selected)]

    def screenshot():
        transient_missing_coins = False
        if pending_removal["key"] is not None:
            removed = pending_removal["key"]
            selected.remove(removed)
            pending_removal["key"] = None
            transient_missing_coins = (
                removed == "cash_tradeoff" and "coins_bonus" in selected
            )
            selected_counts.append(len(selected))
        marker = 200 if transient_missing_coins else 10 + page["value"]
        return np.full(
            (1920, 1080, 3),
            marker,
            dtype=np.uint8,
        )

    def rows(frame):
        if page["value"] == 0:
            keys = [*selected]
            if int(frame[0, 0, 0]) == 200:
                keys.remove("coins_bonus")
            keys.extend(["empty_slot"] * (6 - len(keys)))
        else:
            keys = [
                "cash_tradeoff",
                "coins_bonus",
                "land_mine_damage",
                "cash_bonus",
            ]
        return [
            {
                **_row(key, index),
                "selected_outline": page["value"] == 0,
            }
            for index, key in enumerate(keys)
        ]

    def swipe(_current, _key, **_kwargs):
        page["value"] += 1
        return screenshot()

    def toggle(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        if key in selected:
            pending_removal["key"] = key
        else:
            assert len(selected) < 6
            selected.append(key)
            selected_counts.append(len(selected))
        return current

    def sleep(seconds):
        sleeps.append(seconds)

    def top(current, **_kwargs):
        page["value"] = 0
        return current

    with (
        patch(
            "core.home_perk_configuration._swipe_configuration",
            side_effect=swipe,
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=toggle,
        ) as tap,
        patch(
            "core.home_perk_configuration._scroll_ban_configuration_top",
            side_effect=top,
        ),
    ):
        _repair_bans(
            screenshot(),
            FARM_PERK_BANS,
            capture_fn=screenshot,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=lambda *_args, **_kwargs: True,
            visible_fn=lambda *_args, **_kwargs: True,
            swipe_fn=lambda _key: True,
            row_fn=rows,
            row_near_fn=lambda *_args, **_kwargs: None,
            sleep_fn=sleep,
        )

    assert set(selected) == set(FARM_PERK_BANS)
    assert [call.kwargs["action"] for call in tap.call_args_list] == [
        "perk_ban_deselect:cash_tradeoff",
        "perk_ban_deselect:coins_bonus",
        "perk_ban_toggle:land_mine_damage",
        "perk_ban_toggle:cash_bonus",
    ]
    assert max(selected_counts) == 6
    assert sleeps


def test_ban_repair_bounds_a_stable_no_op_before_any_capacity_fill():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    selected = [
        key for key in FARM_PERK_BANS if key != "cash_bonus"
    ]
    selected.append("coin_tradeoff")

    def rows(_frame):
        return [
            {
                **_row(key, index),
                "selected_outline": True,
            }
            for index, key in enumerate(selected)
        ]

    with (
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            side_effect=lambda current, _row, **_kwargs: current,
        ) as tap,
        patch(
            "core.home_perk_configuration._scroll_ban_configuration_top",
            side_effect=lambda current, **_kwargs: current,
        ),
    ):
        with pytest.raises(
            HomePerkConfigurationRepairExhausted,
            match="made no stable progress",
        ):
            _repair_bans(
                frame,
                FARM_PERK_BANS,
                capture_fn=lambda: frame,
                detector=lambda _frame: {"state": "PERKS"},
                safe_tap_fn=lambda *_args, **_kwargs: True,
                visible_fn=lambda *_args, **_kwargs: True,
                swipe_fn=lambda _key: True,
                row_fn=rows,
                row_near_fn=lambda *_args, **_kwargs: None,
                sleep_fn=lambda _seconds: None,
            )

    assert tap.call_count == 2
    assert {
        call.kwargs["action"] for call in tap.call_args_list
    } == {"perk_ban_deselect:coin_tradeoff"}
    assert len(selected) == 6


def test_home_perk_repair_finishes_bans_before_opening_auto_pick():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    events = []
    selected = [
        {
            **_row(key, index),
            "key": key,
        }
        for index, key in enumerate([*FARM_PERK_BANS, "coin_tradeoff"])
    ]

    def select(current, *, field, **_kwargs):
        events.append(f"select:{field}")
        return current

    def repair(current, _expected, **_kwargs):
        events.append("repair:perk_bans")
        return current, {
            "quality": {"valid": True},
            "selected": [{"key": key} for key in FARM_PERK_BANS],
        }

    evidence = {
        "boundary": "NEW_BATTLE",
        "checked": True,
        "valid": True,
        "failed_checks": [],
        "perk_bans": {
            "valid": True,
            "expected": list(FARM_PERK_BANS),
            "observed": list(FARM_PERK_BANS),
        },
        "perk_auto_pick_order": {
            "valid": True,
            "expected": ["perk_wave_requirement"],
            "observed": ["perk_wave_requirement"],
        },
    }

    with (
        patch("core.home_perk_configuration._require_new_battle_home"),
        patch(
            "core.home_perk_configuration._open_configuration",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration._select_and_scroll_top",
            side_effect=select,
        ),
        patch(
            "core.home_perk_configuration.extract_configured_perk_bans",
            return_value={
                "quality": {"valid": True},
                "selected": selected,
            },
        ) as extract_bans,
        patch(
            "core.home_perk_configuration._repair_bans",
            side_effect=repair,
        ),
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            return_value=(
                [frame],
                frame,
                {"quality": {"valid": True}},
            ),
        ),
        patch(
            "core.home_perk_configuration.evaluate_profile_perk_configuration",
            return_value=evidence,
        ),
        patch(
            "core.home_perk_configuration._close_to_home",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration.log_action_intent",
        ) as action_log,
        patch("core.home_perk_configuration.log_result") as result_log,
    ):
        result = ensure_home_perk_configuration(
            {
                "perk_bans": list(FARM_PERK_BANS),
                "perk_auto_pick_order": ["perk_wave_requirement"],
            },
            home_screenshot=frame,
        )

    assert result.valid
    assert events == [
        "select:perk_bans",
        "repair:perk_bans",
        "select:perk_auto_pick_order",
    ]
    extract_bans.assert_called_once()
    action_log.assert_called_once()
    assert action_log.call_args.args[0] == "Checking Home Perk configuration"
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Home Perk configuration complete — repaired and verified Ban Perks"
    )


def test_home_perk_repair_reuses_its_authoritative_final_ranked_readback():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    expected = [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
    ]
    observed = [
        "perk_wave_requirement",
        "coin_tradeoff",
        "game_speed",
    ]
    captured_bans = {
        "quality": {"valid": True},
        "selected": [{"key": key} for key in FARM_PERK_BANS],
    }
    initial_auto = {
        "quality": {"valid": True},
        "selected": [{"key": key} for key in observed],
    }
    final_auto = {
        "quality": {"valid": True},
        "selected": [{"key": key} for key in expected],
    }

    with (
        patch("core.home_perk_configuration._require_new_battle_home"),
        patch(
            "core.home_perk_configuration._open_configuration",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration._select_and_scroll_top",
            side_effect=lambda current, **_kwargs: current,
        ),
        patch(
            "core.home_perk_configuration._capture_bans_with_ocr_retries",
            return_value=(frame, captured_bans),
        ),
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            return_value=([frame], frame, initial_auto),
        ) as ranked_scan,
        patch(
            "core.home_perk_configuration._repair_auto_pick_order",
            return_value=([frame], frame, final_auto),
        ) as repair,
        patch(
            "core.home_perk_configuration._close_to_home",
            return_value=frame,
        ),
        patch("core.home_perk_configuration.log_action_intent"),
        patch("core.home_perk_configuration.log_result"),
    ):
        result = ensure_home_perk_configuration(
            {
                "perk_bans": list(FARM_PERK_BANS),
                "perk_auto_pick_order": expected,
            },
            home_screenshot=frame,
        )

    assert result.valid
    assert result.changed
    ranked_scan.assert_called_once()
    repair.assert_called_once()
    assert repair.call_args.kwargs["observed_keys"] == observed
    assert result.evidence["perk_auto_pick_order"]["observed"] == expected


def test_home_perk_does_not_repair_an_incomplete_auto_pick_capture():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    selected_bans = [
        {
            **_row(key, index),
            "key": key,
        }
        for index, key in enumerate(FARM_PERK_BANS)
    ]
    evidence = {
        "boundary": "NEW_BATTLE",
        "checked": True,
        "valid": False,
        "failed_checks": ["perk_auto_pick_order"],
        "perk_bans": {
            "valid": True,
            "expected": list(FARM_PERK_BANS),
            "observed": list(FARM_PERK_BANS),
            "capture": {"quality": {"valid": True}},
        },
        "perk_auto_pick_order": {
            "valid": False,
            "expected": [
                "perk_wave_requirement",
                "game_speed",
                "coin_tradeoff",
            ],
            "observed": [
                "perk_wave_requirement",
                "game_speed",
            ],
            "reason": (
                "Auto Pick exposed 2 of 3 ranked perks before the "
                "ranking boundary"
            ),
            "capture": {
                "quality": {
                    "valid": False,
                    "ranking_boundary_seen": True,
                    "warnings": [
                        "Auto Pick exposed 2 of 3 ranked perks before the "
                        "ranking boundary"
                    ],
                }
            },
        },
    }

    with (
        patch("core.home_perk_configuration._require_new_battle_home"),
        patch(
            "core.home_perk_configuration._open_configuration",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration._select_and_scroll_top",
            side_effect=lambda current, **_kwargs: current,
        ),
        patch(
            "core.home_perk_configuration.extract_configured_perk_bans",
            return_value={
                "quality": {"valid": True},
                "selected": selected_bans,
            },
        ),
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            return_value=(
                [frame],
                frame,
                {
                    "quality": {
                        "valid": False,
                        "ocr_retry_recommended": True,
                    }
                },
            ),
        ),
        patch(
            "core.home_perk_configuration.evaluate_profile_perk_configuration",
            return_value=evidence,
        ),
        patch(
            "core.home_perk_configuration._repair_auto_pick_order",
        ) as repair,
        patch(
            "core.home_perk_configuration._close_to_home",
            return_value=frame,
        ),
        patch("core.home_perk_configuration.log_result") as result_log,
    ):
        result = ensure_home_perk_configuration(
            {
                "perk_bans": list(FARM_PERK_BANS),
                "perk_auto_pick_order": [
                    "perk_wave_requirement",
                    "game_speed",
                    "coin_tradeoff",
                ],
            },
            home_screenshot=frame,
        )

    assert result.valid is False
    assert result.failed_check == "perk_auto_pick_order"
    repair.assert_not_called()
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Home Perk configuration failed — "
        "Auto Pick exposed 2 of 3 ranked perks before the ranking boundary"
    )


def test_home_perk_repairs_first_choice_independently_of_auto_pick_order():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    parsed = Mock(
        side_effect=[
            {
                "quality": {"valid": True},
                "selected": [{"key": "damage"}],
            },
            {
                "quality": {"valid": True},
                "selected": [{"key": "perk_wave_requirement"}],
            },
        ]
    )
    evidence = {
        "boundary": "NEW_BATTLE",
        "checked": True,
        "valid": True,
        "failed_checks": [],
        "perk_bans": {
            "valid": True,
            "expected": list(FARM_PERK_BANS),
            "observed": list(FARM_PERK_BANS),
        },
        "perk_auto_pick_order": {
            "valid": True,
            "expected": ["game_speed"],
            "observed": ["game_speed"],
        },
    }

    with (
        patch("core.home_perk_configuration._require_new_battle_home"),
        patch(
            "core.home_perk_configuration._open_configuration",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration._select_and_scroll_top",
            side_effect=lambda current, **_kwargs: current,
        ),
        patch(
            "core.home_perk_configuration._capture_configuration_pages",
            return_value=([frame], frame, True),
        ),
        patch(
            "core.home_perk_configuration._scroll_configuration_top",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration._locate_auto_pick_key",
            return_value=(1, frame, {"key": "perk_wave_requirement"}),
        ),
        patch(
            "core.home_perk_configuration._tap_configuration_row",
            return_value=frame,
        ) as set_first,
        patch(
            "core.home_perk_configuration.extract_configured_perk_bans",
            return_value={
                "quality": {"valid": True},
                "selected": [
                    {"key": key}
                    for key in FARM_PERK_BANS
                ],
            },
        ),
        patch(
            "core.home_perk_configuration."
            "_capture_ranked_order_with_ocr_retries",
            return_value=([frame], frame, {"quality": {"valid": True}}),
        ),
        patch(
            "core.home_perk_configuration.evaluate_profile_perk_configuration",
            return_value=evidence,
        ),
        patch(
            "core.home_perk_configuration._close_to_home",
            return_value=frame,
        ),
        patch("core.home_perk_configuration.log_result"),
    ):
        result = ensure_home_perk_configuration(
            {
                "perk_first_choice": "perk_wave_requirement",
                "perk_bans": list(FARM_PERK_BANS),
                "perk_auto_pick_order": ["game_speed"],
            },
            home_screenshot=frame,
            parse_selection_fn=parsed,
        )

    assert result.valid
    assert result.changed
    assert result.evidence["perk_first_choice"]["valid"] is True
    assert result.evidence["perk_first_choice"]["observed"] == (
        "perk_wave_requirement"
    )
    assert result.evidence["perk_auto_pick_order"]["expected"] == [
        "game_speed"
    ]
    set_first.assert_called_once()


def test_home_perk_omits_externally_satisfied_tabs_from_ui_pass():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    selected_bans = {
        "quality": {"valid": True},
        "selected": [{"key": key} for key in FARM_PERK_BANS],
    }

    with (
        patch("core.home_perk_configuration._require_new_battle_home"),
        patch(
            "core.home_perk_configuration._open_configuration",
            return_value=frame,
        ),
        patch(
            "core.home_perk_configuration._select_and_scroll_top",
            return_value=frame,
        ) as select_tab,
        patch(
            "core.home_perk_configuration._capture_bans_with_ocr_retries",
            return_value=(frame, selected_bans),
        ),
        patch(
            "core.home_perk_configuration._close_to_home",
            return_value=frame,
        ),
        patch("core.home_perk_configuration.log_result"),
    ):
        result = ensure_home_perk_configuration(
            {
                "perk_first_choice": "perk_wave_requirement",
                "perk_bans": list(FARM_PERK_BANS),
                "perk_auto_pick_order": ["game_speed"],
            },
            home_screenshot=frame,
            waived_fields=(
                "perk_first_choice",
                "perk_auto_pick_order",
            ),
        )

    assert result.valid
    assert [call.kwargs["field"] for call in select_tab.call_args_list] == [
        "perk_bans"
    ]
    assert result.evidence["perk_auto_pick_order"] == {
        "boundary": "NEW_BATTLE",
        "checked": False,
        "valid": True,
        "reason": "field omitted from this UI pass",
        "changed": False,
    }


def test_auto_pick_move_requires_fresh_identity_and_visual_change():
    stale = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    before = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    after = np.full((1920, 1080, 3), 40, dtype=np.uint8)
    expected = _row("coin_tradeoff", 2)
    reacquired = dict(expected)
    reacquired["top"] -= 78
    reacquired["bottom"] -= 78
    captures = iter((before, after))
    tapped = []

    def safe_tap(target, *, verification, **_kwargs):
        tapped.append(target)
        return verification.authorizes(target)

    result = _tap_configuration_row(
        stale,
        expected,
        x=915,
        action="auto_pick_move_up:coin_tradeoff",
        capture_fn=lambda: next(captures),
        detector=lambda _frame: {"state": "PERKS"},
        safe_tap_fn=safe_tap,
        visible_fn=lambda *_args, **_kwargs: True,
        row_fn=lambda _frame: [dict(reacquired)],
        row_near_fn=lambda _frame, _y, **_kwargs: dict(reacquired),
        sleep_fn=lambda _seconds: None,
        require_identity_after=False,
    )

    assert result is after
    assert tapped == [
        (915, (reacquired["top"] + reacquired["bottom"]) // 2)
    ]

    with pytest.raises(
        HomePerkConfigurationError,
        match="row did not change",
    ):
        unchanged_captures = iter((before, before.copy()))
        _tap_configuration_row(
            stale,
            expected,
            x=915,
            action="auto_pick_move_up:coin_tradeoff",
            capture_fn=lambda: next(unchanged_captures),
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=safe_tap,
            visible_fn=lambda *_args, **_kwargs: True,
            row_fn=lambda _frame: [dict(reacquired)],
            row_near_fn=lambda _frame, _y, **_kwargs: dict(reacquired),
            sleep_fn=lambda _seconds: None,
            require_identity_after=False,
        )
