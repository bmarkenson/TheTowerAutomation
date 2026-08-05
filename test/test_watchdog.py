from unittest.mock import Mock, patch

import pytest

from core.adb_connection import AdbConnectionCoordinator
from core.run_state import AUTOMATION, RunState
from core.watchdog import CooperativeMutationGuard, _watchdog_process_check_once


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


@pytest.mark.parametrize("state", (RunState.PAUSED, RunState.STOPPED))
def test_watchdog_connection_check_remains_action_free_under_operator_control(
    state,
):
    AUTOMATION.state = state
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


def test_watchdog_process_missing_recovery_is_blocked_by_mutation_guard():
    AUTOMATION.state = RunState.RUNNING
    connection = AdbConnectionCoordinator(is_connected=lambda _target: True)
    allowed = Mock(return_value=False)
    guard = CooperativeMutationGuard(allowed)

    with (
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running", return_value=False) as pid_running,
        patch(
            "core.watchdog.is_game_foregrounded",
            return_value=True,
        ) as foregrounded,
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once(connection, guard)

    pid_running.assert_called_once()
    foregrounded.assert_called_once()
    allowed.assert_called_once_with()
    restart.assert_not_called()
    foreground.assert_not_called()


def test_watchdog_foreground_recovery_is_blocked_by_mutation_guard():
    AUTOMATION.state = RunState.RUNNING
    connection = AdbConnectionCoordinator(is_connected=lambda _target: True)
    allowed = Mock(return_value=False)
    guard = CooperativeMutationGuard(allowed)

    with (
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running", return_value=True),
        patch("core.watchdog.is_game_foregrounded", return_value=False),
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once(connection, guard)

    allowed.assert_called_once_with()
    restart.assert_not_called()
    foreground.assert_not_called()


@pytest.mark.parametrize(
    ("pid_running", "foregrounded", "expected_action"),
    (
        (False, True, "restart"),
        (True, False, "foreground"),
    ),
)
def test_watchdog_recovery_runs_when_mutation_guard_allows_it(
    pid_running,
    foregrounded,
    expected_action,
):
    AUTOMATION.state = RunState.RUNNING
    connection = AdbConnectionCoordinator(is_connected=lambda _target: True)
    guard = CooperativeMutationGuard(lambda: True)

    with (
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running", return_value=pid_running),
        patch(
            "core.watchdog.is_game_foregrounded",
            return_value=foregrounded,
        ),
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once(connection, guard)

    if expected_action == "restart":
        restart.assert_called_once_with()
        foreground.assert_not_called()
    else:
        restart.assert_not_called()
        foreground.assert_called_once_with()


def test_watchdog_operator_control_is_rechecked_at_mutating_dispatch():
    AUTOMATION.state = RunState.RUNNING
    connection = AdbConnectionCoordinator(is_connected=lambda _target: True)

    def pause_during_final_authority_check():
        AUTOMATION.state = RunState.PAUSED
        return True

    guard = CooperativeMutationGuard(pause_during_final_authority_check)
    with (
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running", return_value=False),
        patch("core.watchdog.is_game_foregrounded", return_value=True),
        patch("core.watchdog.restart_game") as restart,
    ):
        _watchdog_process_check_once(connection, guard)

    restart.assert_not_called()
