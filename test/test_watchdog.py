import subprocess
import threading
from unittest.mock import Mock, patch

import pytest

from core.adb_connection import AdbConnectionCoordinator
from core.adb_utils import AdbShellDispatchOutcome
from core.run_state import AUTOMATION, RunState
from core.watchdog import (
    CooperativeMutationGuard,
    _watchdog_process_check_once,
    bring_to_foreground,
    restart_game,
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


def test_bring_to_foreground_reports_exact_launcher_acceptance():
    accepted = AdbShellDispatchOutcome(result=object(), attempted=True)
    with (
        patch("core.watchdog.adb_shell", return_value=accepted) as adb_shell,
        patch("core.watchdog.time.sleep") as sleep,
    ):
        assert bring_to_foreground() is True

    adb_shell.assert_called_once_with(
        [
            "monkey",
            "-p",
            "com.TechTreeGames.TheTower",
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        check=False,
        return_dispatch_outcome=True,
    )
    sleep.assert_called_once_with(5)


def test_bring_to_foreground_does_not_wait_after_launcher_rejection():
    rejected = AdbShellDispatchOutcome()
    with (
        patch("core.watchdog.adb_shell", return_value=rejected),
        patch("core.watchdog.time.sleep") as sleep,
    ):
        assert bring_to_foreground() is False

    sleep.assert_not_called()


def test_bring_to_foreground_audits_recovery_launcher_input():
    accepted = AdbShellDispatchOutcome(result=object(), attempted=True)
    with (
        patch("core.watchdog.adb_shell", return_value=accepted),
        patch("core.watchdog.log_input") as input_log,
        patch("core.watchdog.time.sleep"),
    ):
        assert bring_to_foreground(input_reason="emulator_recovery request-1")

    input_log.assert_called_once()
    assert "package=com.TechTreeGames.TheTower" in (
        input_log.call_args.kwargs["detail"]
    )
    assert "request-1" in input_log.call_args.kwargs["detail"]


def test_pause_waits_for_one_atomic_watchdog_mutation():
    AUTOMATION.state = RunState.RUNNING
    guard = CooperativeMutationGuard(lambda: True)
    mutation_started = threading.Event()
    release_mutation = threading.Event()
    pause_applied = threading.Event()

    def mutate():
        with guard.authorize_mutation() as allowed:
            assert allowed
            mutation_started.set()
            assert release_mutation.wait(timeout=2)

    def pause():
        AUTOMATION.state = RunState.PAUSED
        pause_applied.set()

    mutation_thread = threading.Thread(target=mutate)
    mutation_thread.start()
    assert mutation_started.wait(timeout=2)
    pause_thread = threading.Thread(target=pause)
    pause_thread.start()
    assert not pause_applied.wait(timeout=0.05)

    release_mutation.set()
    mutation_thread.join(timeout=2)
    pause_thread.join(timeout=2)

    assert pause_applied.is_set()
    assert AUTOMATION.state is RunState.PAUSED


def test_restart_game_does_not_overwrite_operator_control_state():
    AUTOMATION.state = RunState.RUNNING

    with (
        patch(
            "core.watchdog.adb_shell",
            return_value=AdbShellDispatchOutcome(
                result=object(),
                attempted=True,
            ),
        ) as adb,
        patch("core.watchdog.time.sleep"),
    ):
        restart_game()

    assert adb.call_count == 2
    assert AUTOMATION.state is RunState.RUNNING


def test_watchdog_rechecks_pause_after_passive_revalidation():
    AUTOMATION.state = RunState.RUNNING
    connection = AdbConnectionCoordinator(is_connected=lambda _target: True)
    pid_checks = 0

    def pid_running(_package):
        nonlocal pid_checks
        pid_checks += 1
        if pid_checks == 2:
            AUTOMATION.state = RunState.PAUSED
        return False

    with (
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running", side_effect=pid_running),
        patch("core.watchdog.is_game_foregrounded", return_value=True),
        patch("core.watchdog.restart_game") as restart,
    ):
        _watchdog_process_check_once(
            connection,
            CooperativeMutationGuard(lambda: True),
        )

    assert pid_checks == 2
    restart.assert_not_called()


def test_restart_failure_after_force_stop_is_catastrophic():
    AUTOMATION.state = RunState.RUNNING
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with (
            patch(
                "core.watchdog.adb_shell",
                side_effect=(
                    AdbShellDispatchOutcome(result=object(), attempted=True),
                    AdbShellDispatchOutcome(),
                    AdbShellDispatchOutcome(),
                ),
            ) as adb,
            patch("core.watchdog.time.sleep"),
        ):
            assert restart_game() is False

        assert adb.call_count == 3
        assert AUTOMATION.state is RunState.PAUSED
        assert len(failures) == 1
        assert "launcher restoration was not accepted" in failures[0]
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_single_step_foreground_timeout_is_catastrophic():
    AUTOMATION.state = RunState.RUNNING
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired("adb", 10),
        ):
            assert bring_to_foreground() is False

        assert AUTOMATION.state is RunState.PAUSED
        assert len(failures) == 1
        assert "timed out after dispatch" in failures[0]
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_restart_interrupt_after_force_stop_restores_before_propagating():
    AUTOMATION.state = RunState.RUNNING
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with (
            patch(
                "core.watchdog.adb_shell",
                side_effect=(
                    KeyboardInterrupt(),
                    AdbShellDispatchOutcome(result=object(), attempted=True),
                ),
            ) as adb,
            patch("core.watchdog.time.sleep"),
            pytest.raises(KeyboardInterrupt),
        ):
            restart_game()

        assert adb.call_count == 2
        assert failures == []
        assert AUTOMATION.state is RunState.RUNNING
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_restart_retries_interrupted_launcher_before_propagating():
    AUTOMATION.state = RunState.RUNNING
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with (
            patch(
                "core.watchdog.adb_shell",
                side_effect=(
                    AdbShellDispatchOutcome(result=object(), attempted=True),
                    KeyboardInterrupt(),
                    AdbShellDispatchOutcome(result=object(), attempted=True),
                ),
            ) as adb,
            patch("core.watchdog.time.sleep"),
            pytest.raises(KeyboardInterrupt),
        ):
            restart_game()

        assert adb.call_count == 3
        assert failures == []
        assert AUTOMATION.state is RunState.RUNNING
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_restart_unrestored_launcher_interrupt_is_catastrophic():
    AUTOMATION.state = RunState.RUNNING
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with (
            patch(
                "core.watchdog.adb_shell",
                side_effect=(
                    AdbShellDispatchOutcome(result=object(), attempted=True),
                    KeyboardInterrupt(),
                    KeyboardInterrupt(),
                ),
            ),
            patch("core.watchdog.time.sleep"),
            pytest.raises(KeyboardInterrupt),
        ):
            restart_game()

        assert len(failures) == 1
        assert "launcher restoration was not accepted" in failures[0]
        assert AUTOMATION.state is RunState.PAUSED
    finally:
        AUTOMATION.clear_mutation_guard(token)
