from unittest.mock import patch

import pytest

from core.run_state import AUTOMATION, RunState
from core.watchdog import (
    _AdbConnectionLogState,
    _watchdog_process_check_once,
    ensure_adb_connected,
)


@pytest.fixture(autouse=True)
def restore_automation_state():
    original = AUTOMATION.state
    try:
        yield
    finally:
        AUTOMATION.state = original


def test_watchdog_does_not_infer_missing_game_when_adb_is_unreachable():
    AUTOMATION.state = RunState.RUNNING
    with (
        patch("core.watchdog.ensure_adb_connected", return_value=False),
        patch("core.watchdog._pid_running") as pid_running,
        patch("core.watchdog.is_game_foregrounded") as foregrounded,
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once()

    pid_running.assert_not_called()
    foregrounded.assert_not_called()
    restart.assert_not_called()
    foreground.assert_not_called()


def test_watchdog_connection_check_remains_action_free_while_paused():
    AUTOMATION.state = RunState.PAUSED
    with (
        patch("core.watchdog.ensure_adb_connected", return_value=True),
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running") as pid_running,
        patch("core.watchdog.is_game_foregrounded") as foregrounded,
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once()

    pid_running.assert_not_called()
    foregrounded.assert_not_called()
    restart.assert_not_called()
    foreground.assert_not_called()


def test_adb_connect_warning_requires_persistence_and_rate_limits_reminders():
    now = {"value": 100.0}
    connected = {"value": False}
    log_state = _AdbConnectionLogState(
        clock=lambda: now["value"],
        warning_after_failures=3,
        warning_repeat_s=300.0,
    )

    with (
        patch("core.watchdog._adb_target", return_value="localhost:5565"),
        patch(
            "core.watchdog._adb_is_connected",
            side_effect=lambda _target: connected["value"],
        ),
        patch("core.watchdog._adb_connect", return_value=False),
        patch("core.watchdog._adb_connection_log_state", new=log_state),
        patch("core.watchdog.log") as runtime_log,
    ):
        for timestamp in (100.0, 101.0):
            now["value"] = timestamp
            assert not ensure_adb_connected()

        assert [
            call for call in runtime_log.call_args_list
            if call.args[1] == "WARN"
        ] == []

        now["value"] = 102.0
        assert not ensure_adb_connected()
        now["value"] = 103.0
        assert not ensure_adb_connected()
        now["value"] = 403.0
        assert not ensure_adb_connected()
        connected["value"] = True
        now["value"] = 404.0
        assert ensure_adb_connected()

    warnings = [
        call.args[0]
        for call in runtime_log.call_args_list
        if call.args[1] == "WARN"
    ]
    assert warnings == [
        "[WATCHDOG] ADB target localhost:5565 is unavailable after 3 "
        "connection attempts; automation inputs remain suspended while "
        "retries continue",
        "[WATCHDOG] ADB target localhost:5565 remains unavailable after 5 "
        "connection attempts; automation inputs remain suspended while "
        "retries continue",
    ]
    assert any(
        call.args
        == (
            "[WATCHDOG] ADB target localhost:5565 recovered after 5 failed "
            "connection attempt(s)",
            "INFO",
        )
        for call in runtime_log.call_args_list
    )
