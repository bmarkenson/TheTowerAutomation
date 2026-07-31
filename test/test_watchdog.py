from unittest.mock import patch

import pytest

from core.adb_connection import AdbConnectionCoordinator
from core.run_state import AUTOMATION, RunState
from core.watchdog import _watchdog_process_check_once


@pytest.fixture(autouse=True)
def restore_automation_state():
    original = AUTOMATION.state
    try:
        yield
    finally:
        AUTOMATION.state = original


def test_watchdog_does_not_infer_missing_game_when_adb_is_unreachable():
    AUTOMATION.state = RunState.RUNNING
    connection = AdbConnectionCoordinator(
        is_connected=lambda _target: False,
        connect=lambda _target: False,
    )
    with (
        patch("core.watchdog._pid_running") as pid_running,
        patch("core.watchdog.is_game_foregrounded") as foregrounded,
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once(connection)

    pid_running.assert_not_called()
    foregrounded.assert_not_called()
    restart.assert_not_called()
    foreground.assert_not_called()


def test_watchdog_connection_check_remains_action_free_while_paused():
    AUTOMATION.state = RunState.PAUSED
    connection = AdbConnectionCoordinator(
        is_connected=lambda _target: True,
    )
    with (
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running") as pid_running,
        patch("core.watchdog.is_game_foregrounded") as foregrounded,
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once(connection)

    pid_running.assert_not_called()
    foregrounded.assert_not_called()
    restart.assert_not_called()
    foreground.assert_not_called()
