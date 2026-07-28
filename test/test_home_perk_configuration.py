from unittest.mock import patch

import numpy as np
import pytest

from core.home_perk_configuration import (
    BAN_SELECTED_TOGGLE_X,
    HomePerkConfigurationError,
    _repair_auto_pick_order,
    _repair_bans,
    _tap_configuration_row,
    ensure_home_perk_configuration,
)
from core.perk_configuration import (
    FARM_PERK_BANS,
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
    text = LABELS[key]
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
        "coin_tradeoff",
    ]

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
    assert tap.call_count == 1
    assert tap.call_args.kwargs["action"] == "auto_pick_move_up:coin_tradeoff"
    assert scroll_top.call_count == 3


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
            sleep_fn=lambda _seconds: None,
        )

    assert order[:3] == [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
    ]


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
            match="expected exactly one-rank upward progress",
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
                sleep_fn=lambda _seconds: None,
            )


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
        return [_row(key, index) for index, key in enumerate(keys)]

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
            "core.home_perk_configuration._scroll_configuration_top",
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
        return current

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
        ),
        patch(
            "core.home_perk_configuration._repair_bans",
            side_effect=repair,
        ),
        patch(
            "core.home_perk_configuration._capture_ranked_frames",
            return_value=([frame], frame),
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
    action_log.assert_called_once()
    assert action_log.call_args.args[0] == "Checking Home Perk configuration"
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Home Perk configuration complete — repaired and verified Ban Perks"
    )


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
            "core.home_perk_configuration._capture_ranked_frames",
            return_value=([frame], frame),
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
