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
        ),
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
            sleep_fn=lambda _seconds: None,
        )

    assert order[:3] == [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
    ]
    assert tap.call_count == 1
    assert tap.call_args.kwargs["action"] == "auto_pick_move_up:coin_tradeoff"


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
            keys = ["coin_tradeoff", "cash_tradeoff"]
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
    assert tap.call_count == 2
    assert tap.call_args_list[0].kwargs["x"] == BAN_SELECTED_TOGGLE_X
    assert (
        tap.call_args_list[0].kwargs["action"]
        == "perk_ban_deselect:coin_tradeoff"
    )
    assert (
        tap.call_args_list[1].kwargs["action"]
        == "perk_ban_toggle:cash_tradeoff"
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


def test_auto_pick_move_requires_fresh_identity_and_strict_upward_progress():
    before = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    after = np.full((1920, 1080, 3), 40, dtype=np.uint8)
    expected = _row("coin_tradeoff", 2)

    def safe_tap(_target, *, verification, **_kwargs):
        return verification.authorizes((915, (expected["top"] + expected["bottom"]) // 2))

    def row_near(frame, _y, **_kwargs):
        row = dict(expected)
        if frame is after:
            row["top"] -= 172
            row["bottom"] -= 172
        return row

    result = _tap_configuration_row(
        before,
        expected,
        x=915,
        action="auto_pick_move_up:coin_tradeoff",
        capture_fn=lambda: after,
        detector=lambda _frame: {"state": "PERKS"},
        safe_tap_fn=safe_tap,
        visible_fn=lambda *_args, **_kwargs: True,
        row_near_fn=row_near,
        sleep_fn=lambda _seconds: None,
        require_vertical_move=True,
        direction=-1,
    )

    assert result is after

    with pytest.raises(HomePerkConfigurationError, match="no upward progress"):
        _tap_configuration_row(
            before,
            expected,
            x=915,
            action="auto_pick_move_up:coin_tradeoff",
            capture_fn=lambda: after,
            detector=lambda _frame: {"state": "PERKS"},
            safe_tap_fn=safe_tap,
            visible_fn=lambda *_args, **_kwargs: True,
            row_near_fn=lambda _frame, _y, **_kwargs: dict(expected),
            sleep_fn=lambda _seconds: None,
            require_vertical_move=True,
            direction=-1,
        )
