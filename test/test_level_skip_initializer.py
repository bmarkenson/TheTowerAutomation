from unittest.mock import patch

import numpy as np

from automation.missions.base import MissionContext
from core.action_executor import execute_actions
from core.level_skip_initializer import (
    EALS,
    EHLS,
    LevelSkipInitializationResult,
    initialize_level_skips,
)
from core.upgrade_box_detector import UpgradeBox


def _frame(value: int):
    return np.full((1920, 1080, 3), value, dtype=np.uint8)


def _box(label: str, status: str) -> UpgradeBox:
    if label == EHLS:
        return UpgradeBox("left", (26, 1577, 511, 218), text=label, affordability=status)
    return UpgradeBox("right", (546, 1368, 509, 226), text=label, affordability=status)


def test_fast_initializer_burst_taps_ehls_before_eals():
    initial = _frame(1)
    after_ehls = _frame(2)
    complete = _frame(3)
    captures = iter((after_ehls, complete))
    taps = []
    events = []

    def boxes(frame, *, menu):
        value = int(frame[0, 0, 0])
        ehls = "maxed" if value >= 2 else "affordable"
        eals = "maxed" if value >= 3 else "affordable"
        if value == 2:
            events.append("ehls_max_observed")
        return {"left": [_box(EHLS, ehls)], "right": [_box(EALS, eals)]}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch("core.level_skip_initializer.detect_visible_boxes", side_effect=boxes),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            side_effect=lambda _frame: events.append("wave_ocr") or (20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: next(captures),
            tap_fn=lambda point, *, label: (
                taps.append((label, point)),
                events.append(label),
                True,
            )[-1],
            sleep_fn=lambda _seconds: None,
            taps_per_burst=2,
        )

    assert result.success
    assert result.ehls_maxed and result.eals_maxed
    assert result.ehls_wave == 20
    assert result.eals_wave == 20
    assert result.eals_first_tap_wave == 20
    assert result.taps_sent == 3
    assert [label for label, _point in taps] == [
        f"level_skip:{EHLS}",
        f"level_skip:{EHLS}",
        f"level_skip:{EALS}",
    ]
    handoff = events.index("ehls_max_observed")
    assert events[handoff:handoff + 2] == [
        "ehls_max_observed",
        f"level_skip:{EALS}",
    ]
    assert events.index(f"level_skip:{EALS}") < events.index("wave_ocr")


def test_fast_initializer_refuses_non_running_screen_without_taps():
    taps = []
    with patch(
        "core.level_skip_initializer.detect_state_and_overlays",
        return_value={"state": "HOME_SCREEN", "menu": None},
    ):
        result = initialize_level_skips(
            screenshot=_frame(1),
            tap_fn=lambda point, *, label: taps.append((label, point)) or True,
        )

    assert not result.success
    assert result.reason == "not_running"
    assert taps == []


def test_executor_records_fast_initializer_metrics():
    ctx = MissionContext()
    ctx.data["mission_vars"] = {"last_detection_state": "RUNNING"}
    result = LevelSkipInitializationResult(
        success=True,
        ehls_maxed=True,
        eals_maxed=True,
        elapsed_s=5.25,
        ehls_wave=20,
        eals_wave=30,
        taps_sent=8,
        reason="complete",
        eals_first_tap_wave=20,
        eals_first_tap_elapsed_s=4.75,
    )

    with patch("core.action_executor.initialize_level_skips", return_value=result):
        execute_actions(
            _frame(1),
            [{"type": "level_skip_initialize", "_strategy": True}],
            ctx,
        )

    mv = ctx.data["mission_vars"]
    assert mv["ehls_completed"] is True
    assert mv["eals_completed"] is True
    assert mv["ehls_completion_wave"] == 20
    assert mv["eals_completion_wave"] == 30
    assert mv["eals_first_tap_wave"] == 20
    assert mv["eals_first_tap_elapsed_s"] == 4.75
    assert mv["level_skip_elapsed_s"] == 5.25
    assert mv["level_skip_taps_sent"] == 8
