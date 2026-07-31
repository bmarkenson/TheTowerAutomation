from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.app import App
from core.game_speed import (
    GAME_SPEED_MINUS_POINT,
    GAME_SPEED_PLUS_POINT,
    MAXIMUM_GAME_SPEED_TARGET,
    GameSpeedGuard,
    GameSpeedReading,
    GameSpeedResult,
    enforce_game_speed,
    maximize_game_speed,
    measure_game_speed,
    read_game_speed_control,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"


def _complete_frame() -> np.ndarray:
    return np.full((1920, 1080, 3), 32, dtype=np.uint8)


def _reading(value: float) -> GameSpeedReading:
    return GameSpeedReading(
        True,
        value,
        f"x{value:.1f}",
        90.0,
        True,
        "visible",
        True,
    )


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
    assert normal_reading.minus_visible is True
    assert raised_reading.valid is True
    assert raised_reading.value == 6.3
    assert raised_reading.minus_visible is True


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


def test_maximize_accepts_perk_maximum_without_tapping():
    frame = _complete_frame()

    with patch(
        "core.game_speed.measure_game_speed",
        return_value=_reading(6.3),
    ):
        result = maximize_game_speed(
            screenshot=frame,
            tap_fn=lambda *_args, **_kwargs: pytest.fail(
                "an already-satisfied speed must not be probed"
            ),
        )

    assert result == GameSpeedResult(
        True,
        6.3,
        6.3,
        0,
        0,
        "target_satisfied",
    )


def test_maximize_probes_x5_and_accepts_a_no_perk_ceiling():
    frame = _complete_frame()

    with (
        patch(
            "core.game_speed.measure_game_speed",
            return_value=_reading(5.0),
        ),
        patch(
            "core.game_speed.read_game_speed_control",
            return_value=_reading(5.0),
        ),
    ):
        result = maximize_game_speed(
            screenshot=frame,
            capture_fn=lambda: frame,
            tap_fn=lambda *_args, **_kwargs: True,
            sleep_fn=lambda _seconds: None,
        )

    assert result == GameSpeedResult(
        True,
        5.0,
        5.0,
        1,
        0,
        "maximum_available_confirmed",
    )


def test_maximize_does_not_assume_x5_is_maximum_when_perk_is_active():
    frame = _complete_frame()
    speed = {"value": 5.0}
    taps = []

    def tap(point, *, verification, **_kwargs):
        assert point == GAME_SPEED_PLUS_POINT
        assert verification.authorizes(point)
        taps.append(point)
        speed["value"] = 6.3
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
        5.0,
        6.3,
        1,
        1,
        "target_reached",
    )
    assert taps == [GAME_SPEED_PLUS_POINT]


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


def test_exact_target_accepts_x4_without_tapping():
    frame = _complete_frame()

    with patch(
        "core.game_speed.measure_game_speed",
        return_value=_reading(4.0),
    ):
        result = enforce_game_speed(
            target=4.0,
            screenshot=frame,
            tap_fn=lambda *_args, **_kwargs: pytest.fail(
                "the exact target must not be changed"
            ),
        )

    assert result == GameSpeedResult(
        True,
        4.0,
        4.0,
        0,
        0,
        "target_satisfied",
        target=4.0,
    )


def test_exact_target_walks_perk_maximum_down_to_x4():
    frame = _complete_frame()
    speed = {"value": 6.3}
    next_values = iter((6.0, 5.5, 5.0, 4.5, 4.0))
    taps = []

    def tap(point, *, verification, **_kwargs):
        assert point == GAME_SPEED_MINUS_POINT
        assert verification.authorizes(point)
        taps.append(point)
        speed["value"] = next(next_values)
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
        patch("core.game_speed.log_action_intent") as action_log,
        patch("core.game_speed.log_result") as result_log,
    ):
        result = enforce_game_speed(
            target=4.0,
            screenshot=frame,
            capture_fn=lambda: frame,
            tap_fn=tap,
            sleep_fn=lambda _seconds: None,
        )

    assert result == GameSpeedResult(
        True,
        6.3,
        4.0,
        5,
        0,
        "target_reached",
        decreases=5,
        target=4.0,
    )
    assert taps == [GAME_SPEED_MINUS_POINT] * 5
    action_log.assert_called_once()
    result_log.assert_called_once()


def test_exact_target_raises_a_lower_speed_to_x4():
    frame = _complete_frame()
    speed = {"value": 3.0}
    taps = []

    def tap(point, *, verification, **_kwargs):
        assert point == GAME_SPEED_PLUS_POINT
        assert verification.authorizes(point)
        taps.append(point)
        speed["value"] += 0.5
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
        patch("core.game_speed.log_action_intent"),
        patch("core.game_speed.log_result"),
    ):
        result = enforce_game_speed(
            target=4.0,
            screenshot=frame,
            capture_fn=lambda: frame,
            tap_fn=tap,
            sleep_fn=lambda _seconds: None,
        )

    assert result.success is True
    assert result.final == 4.0
    assert result.increases == 2
    assert result.decreases == 0
    assert taps == [GAME_SPEED_PLUS_POINT] * 2


def test_exact_target_refuses_to_lower_without_visible_minus_control():
    frame = _complete_frame()
    reading = GameSpeedReading(
        True,
        5.0,
        "x5.0",
        90.0,
        True,
        "visible",
        False,
    )

    with patch("core.game_speed.measure_game_speed", return_value=reading):
        result = enforce_game_speed(
            target=4.0,
            screenshot=frame,
            tap_fn=lambda *_args, **_kwargs: pytest.fail(
                "minus input must remain blocked"
            ),
        )

    assert result.success is False
    assert result.reason == "minus_control_not_visible"
    assert result.taps_sent == 0


def test_exact_adjustment_stops_when_control_target_changes():
    frame = _complete_frame()
    speed = {"value": 5.0}
    guard_checks = iter((True, False))

    def tap(*_args, **_kwargs):
        speed["value"] = 4.5
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
        patch("core.game_speed.log_action_intent"),
        patch("core.game_speed.log_result"),
    ):
        result = enforce_game_speed(
            target=4.0,
            screenshot=frame,
            capture_fn=lambda: frame,
            tap_fn=tap,
            sleep_fn=lambda _seconds: None,
            action_guard_fn=lambda: next(guard_checks),
        )

    assert result.success is False
    assert result.reason == "actions_blocked"
    assert result.taps_sent == 1
    assert result.final == 4.5


def test_zero_is_a_valid_visible_speed_and_exact_target():
    frame = _complete_frame()
    with (
        patch("core.game_speed._plus_control_visible", return_value=True),
        patch("core.game_speed._minus_control_visible", return_value=True),
    ):
        reading = read_game_speed_control(
            frame,
            text_fn=lambda _crop: ("x0.0", 99.0),
        )

    assert reading.valid is True
    assert reading.value == 0.0


def test_maximum_ceiling_proof_is_retained_without_repeated_taps():
    frame = _complete_frame()
    with patch(
        "core.game_speed.measure_game_speed",
        return_value=_reading(5.0),
    ):
        result = enforce_game_speed(
            target=MAXIMUM_GAME_SPEED_TARGET,
            maximum_ceiling_confirmed=True,
            screenshot=frame,
            tap_fn=lambda *_args, **_kwargs: pytest.fail(
                "a retained maximum must not be probed repeatedly"
            ),
        )

    assert result.success is True
    assert result.reason == "maximum_available_retained"


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
        "[GAME_SPEED] Unable to enforce battle speed target x6.3 after 3 "
        "consecutive checks; automation will retry (reason=speed_ocr_failed)",
        "[GAME_SPEED] Still unable to enforce battle speed target x6.3 after 5 "
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


def test_custom_target_warns_immediately_and_rate_limits_reminders():
    now = {"value": 100.0}
    guard = GameSpeedGuard(
        clock=lambda: now["value"],
        custom_reminder_interval_s=300.0,
    )
    frame = _complete_frame()
    satisfied = GameSpeedResult(
        True,
        4.0,
        4.0,
        0,
        0,
        "target_satisfied",
        target=4.0,
    )

    with patch("core.game_speed.log") as runtime_log:
        assert guard.set_target(4.0, wave=123)
        guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: satisfied,
        )
        now["value"] = 399.0
        guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: satisfied,
        )
        now["value"] = 400.0
        guard.handle(
            frame,
            {"state": "RUNNING"},
            action_guard_fn=lambda: True,
            maximize_fn=lambda **_kwargs: satisfied,
        )

    warnings = [
        call.args[0]
        for call in runtime_log.call_args_list
        if len(call.args) > 1 and call.args[1] == "WARN"
    ]
    assert warnings == [
        "[GAME_SPEED] Custom target x4.0 is active; battle speed will remain "
        "there until the target is changed",
        "[GAME_SPEED] Custom target x4.0 remains active",
    ]
    snapshot = guard.snapshot()
    assert snapshot["target"] == 4.0
    assert snapshot["timeline"][0]["approximate_wave"] == 123


def test_battle_reset_records_the_active_target_as_initial_metadata():
    guard = GameSpeedGuard()
    with patch("core.game_speed.log"):
        guard.set_target(4.0, wave=900)
    guard.reset_battle(wave=1)

    assert guard.snapshot()["timeline"] == [
        {
            "changed_at": guard.snapshot()["timeline"][0]["changed_at"],
            "target": 4.0,
            "target_semantics": "exact",
            "source": "battle_start",
            "approximate_wave": 1,
        }
    ]


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
