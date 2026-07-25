from unittest.mock import patch

import numpy as np
import pytest

from core.home_perk_configuration import (
    HomePerkConfigurationError,
    _repair_auto_pick_order,
    _repair_bans,
    _tap_configuration_row,
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
        "max_health",
    ]

    def screenshot():
        return np.full(
            (1920, 1080, 3),
            10 + page["value"],
            dtype=np.uint8,
        )

    def rows(_frame):
        if page["value"] == 0:
            keys = [*selected, "empty_slot"]
        else:
            keys = ["max_health", "cash_tradeoff"]
        return [_row(key, index) for index, key in enumerate(keys)]

    def swipe(_current, _key, **_kwargs):
        page["value"] += 1
        return screenshot()

    def toggle(current, row, **_kwargs):
        key = classify_perk_configuration_text(row["display_text"])
        configured = key or "max_health"
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
