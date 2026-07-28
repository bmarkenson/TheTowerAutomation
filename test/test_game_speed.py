from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.app import App
from core.game_speed import (
    GAME_SPEED_PLUS_POINT,
    GameSpeedGuard,
    GameSpeedReading,
    GameSpeedResult,
    NORMAL_MAX_GAME_SPEED,
    maximize_game_speed,
    measure_game_speed,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"


def _complete_frame() -> np.ndarray:
    return np.full((1920, 1080, 3), 32, dtype=np.uint8)


def _reading(value: float) -> GameSpeedReading:
    return GameSpeedReading(True, value, f"x{value:.1f}", 90.0, True, "visible")


def test_retained_battle_fixtures_read_normal_and_perk_raised_maximum():
    normal = cv2.imread(
        str(FIXTURES / "running_menu_tournament_trophy_20260718.png")
    )
    raised = cv2.imread(
        str(FIXTURES / "running_menu_no_reward_badges_20260715.png")
    )

    normal_reading = measure_game_speed(normal)
    raised_reading = measure_game_speed(raised)

    assert normal_reading.valid is True
    assert normal_reading.value == 5.0
    assert raised_reading.valid is True
    assert raised_reading.value == 6.3


def test_measurement_rejects_the_control_outside_an_active_battle():
    screenshot = cv2.imread(
        str(FIXTURES / "running_menu_no_reward_badges_20260715.png")
    )

    reading = measure_game_speed(
        screenshot,
        detector=lambda _frame: {"state": "HOME_SCREEN"},
    )

    assert reading.valid is False
    assert reading.reason == "not_running"


@pytest.mark.parametrize("speed", (5.0, 6.3))
def test_maximize_accepts_normal_and_perk_maximum_without_tapping(speed):
    frame = _complete_frame()

    with patch(
        "core.game_speed.measure_game_speed",
        return_value=_reading(speed),
    ):
        result = maximize_game_speed(
            screenshot=frame,
            tap_fn=lambda *_args, **_kwargs: pytest.fail(
                "an already-satisfied speed must not be probed"
            ),
        )

    assert result == GameSpeedResult(
        True,
        speed,
        speed,
        0,
        0,
        "target_satisfied",
    )


def test_maximize_stops_as_soon_as_normal_maximum_is_reached():
    frame = _complete_frame()
    speed = {"value": 1.0}
    taps = []

    def tap(point, *, verification, **_kwargs):
        assert point == GAME_SPEED_PLUS_POINT
        assert verification.authorizes(point)
        taps.append(point)
        speed["value"] += 2.0
        return True

    with (
        patch(
            "core.game_speed.measure_game_speed",
            side_effect=lambda *_args, **_kwargs: _reading(speed["value"]),
        ),
        patch(
            "core.game_speed.read_game_speed_control",
            side_effect=lambda *_args, **_kwargs: _reading(speed["value"]),
        ),
    ):
        result = maximize_game_speed(
            screenshot=frame,
            capture_fn=lambda: frame,
            tap_fn=tap,
            sleep_fn=lambda _seconds: None,
        )

    assert result == GameSpeedResult(
        True,
        1.0,
        NORMAL_MAX_GAME_SPEED,
        2,
        2,
        "target_reached",
    )
    assert taps == [GAME_SPEED_PLUS_POINT] * 2


def test_maximize_fails_if_a_submaximum_plus_tap_has_no_effect():
    frame = _complete_frame()

    with (
        patch(
            "core.game_speed.measure_game_speed",
            return_value=_reading(3.0),
        ),
        patch(
            "core.game_speed.read_game_speed_control",
            return_value=_reading(3.0),
        ),
    ):
        result = maximize_game_speed(
            screenshot=frame,
            capture_fn=lambda: frame,
            tap_fn=lambda *_args, **_kwargs: True,
            sleep_fn=lambda _seconds: None,
        )

    assert result == GameSpeedResult(
        False,
        3.0,
        3.0,
        1,
        0,
        "speed_did_not_increase",
    )


def test_maximize_rechecks_runtime_authority_before_every_tap():
    frame = _complete_frame()

    with patch(
        "core.game_speed.measure_game_speed",
        return_value=_reading(1.0),
    ):
        result = maximize_game_speed(
            screenshot=frame,
            tap_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("tap must remain blocked")
            ),
            action_guard_fn=lambda: False,
        )

    assert result.success is False
    assert result.reason == "actions_blocked"
    assert result.taps_sent == 0


def test_guard_checks_only_running_battles_and_resets_at_home():
    now = {"value": 100.0}
    guard = GameSpeedGuard(clock=lambda: now["value"], check_interval_s=30.0)
    frame = _complete_frame()
    calls = []

    def maximize(**_kwargs):
        calls.append(now["value"])
        return GameSpeedResult(True, 5.0, 5.0, 0, 0, "target_satisfied")

    assert not guard.handle(
        frame,
        {"state": "RUNNING"},
        action_guard_fn=lambda: True,
        maximize_fn=maximize,
    )
    assert not guard.handle(
        frame,
        {"state": "RUNNING"},
        action_guard_fn=lambda: True,
        maximize_fn=maximize,
    )
    assert not guard.handle(
        frame,
        {"state": "HOME_SCREEN"},
        action_guard_fn=lambda: True,
        maximize_fn=maximize,
    )
    assert not guard.handle(
        frame,
        {"state": "RUNNING"},
        action_guard_fn=lambda: True,
        maximize_fn=maximize,
    )
    assert calls == [100.0, 100.0]


def test_guard_does_not_repeat_stable_noop_log_entries():
    now = {"value": 100.0}
    guard = GameSpeedGuard(clock=lambda: now["value"], check_interval_s=30.0)
    frame = _complete_frame()
    result = GameSpeedResult(True, 6.3, 6.3, 0, 0, "target_satisfied")

    with patch("core.game_speed.log") as log:
        assert not guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: result,
        )
        now["value"] = 131.0
        assert not guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: result,
        )

    log.assert_called_once()


def test_guard_warns_only_after_persistent_failures_and_rate_limits_reminders():
    now = {"value": 100.0}
    guard = GameSpeedGuard(
        clock=lambda: now["value"],
        retry_interval_s=5.0,
        warning_after_failures=3,
        warning_repeat_s=300.0,
    )
    frame = _complete_frame()
    failed = GameSpeedResult(
        False,
        None,
        None,
        0,
        0,
        "speed_ocr_failed",
    )

    with patch("core.game_speed.log") as log:
        for timestamp in (100.0, 106.0):
            now["value"] = timestamp
            guard.handle(
                frame,
                {"state": "RUNNING"},
                action_guard_fn=lambda: True,
                maximize_fn=lambda **_kwargs: failed,
            )

        assert [
            call for call in log.call_args_list if call.args[1] == "WARN"
        ] == []

        now["value"] = 112.0
        guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: failed,
        )
        now["value"] = 118.0
        guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: failed,
        )
        now["value"] = 413.0
        guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: failed,
        )

    warnings = [
        call.args[0] for call in log.call_args_list if call.args[1] == "WARN"
    ]
    assert warnings == [
        "[GAME_SPEED] Unable to verify normal battle speed after 3 "
        "consecutive checks; automation will retry (reason=speed_ocr_failed)",
        "[GAME_SPEED] Still unable to verify normal battle speed after 5 "
        "consecutive checks; automation will retry (reason=speed_ocr_failed)",
    ]


def test_guard_records_recovery_after_persistent_speed_failures():
    now = {"value": 100.0}
    guard = GameSpeedGuard(
        clock=lambda: now["value"],
        retry_interval_s=0.0,
        warning_after_failures=2,
    )
    frame = _complete_frame()
    failed = GameSpeedResult(
        False,
        None,
        None,
        0,
        0,
        "speed_ocr_failed",
    )
    recovered = GameSpeedResult(
        True,
        5.0,
        5.0,
        0,
        0,
        "target_satisfied",
    )

    with patch("core.game_speed.log") as log:
        for result in (failed, failed, recovered):
            guard.handle(
                frame,
                {"state": "RUNNING"},
                action_guard_fn=lambda: True,
                maximize_fn=lambda **_kwargs: result,
            )

    assert any(
        call.args
        == (
            "[GAME_SPEED] Verification recovered after 2 consecutive failed "
            "checks (final=5.0 reason=target_satisfied)",
            "INFO",
        )
        for call in log.call_args_list
    )


def test_farm_level_skips_remain_ahead_of_game_speed():
    app = App.__new__(App)
    mission_vars = {
        "ehls_completed": False,
        "eals_completed": False,
    }
    app._mission_mgr = SimpleNamespace(
        ctx=SimpleNamespace(data={"mission_vars": mission_vars})
    )

    assert not app._game_speed_priority_ready(initialization_pending=True)
    mission_vars["ehls_completed"] = True
    assert not app._game_speed_priority_ready(initialization_pending=True)
    mission_vars["eals_completed"] = True
    assert app._game_speed_priority_ready(initialization_pending=True)


def test_non_farm_and_attached_battles_do_not_invent_a_level_skip_dependency():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(
        ctx=SimpleNamespace(data={"mission_vars": {}})
    )
    assert app._game_speed_priority_ready(initialization_pending=True)

    app._mission_mgr.ctx.data["mission_vars"] = {
        "ehls_completed": False,
        "eals_completed": False,
    }
    assert app._game_speed_priority_ready(initialization_pending=False)
