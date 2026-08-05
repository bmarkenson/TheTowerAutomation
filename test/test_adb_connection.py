from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
from unittest.mock import Mock, call, patch

from core.adb_connection import (
    AdbConnectionCoordinator,
    PersistentAdbConnectionManager,
    _adb_connect,
)


def test_long_outage_uses_bounded_backoff_and_rate_limited_warnings():
    now = {"value": 0.0}
    is_connected = Mock(return_value=False)
    connect = Mock(return_value=False)
    coordinator = AdbConnectionCoordinator(
        clock=lambda: now["value"],
        is_connected=is_connected,
        connect=connect,
        reconnect_delays_s=(1.0, 2.0, 5.0, 10.0, 30.0),
        warning_after_failures=3,
        warning_repeat_s=300.0,
    )

    with (
        patch("core.adb_connection.log") as runtime_log,
        patch("core.adb_connection.log_result") as runtime_result,
    ):
        for second in range(621):
            now["value"] = float(second)
            assert not coordinator.ensure_connected(target="localhost:5555")

    assert connect.call_count < 30
    assert is_connected.call_count == connect.call_count * 2
    warnings = [
        call.args[0]
        for call in runtime_log.call_args_list
        if call.args[1] == "WARN"
    ]
    assert len(warnings) == 3
    assert "is unavailable after 3 reconnect attempts" in warnings[0]
    assert all("bounded retries continue" in warning for warning in warnings)
    assert all(call.args[1] == "WARN" for call in runtime_log.call_args_list)
    runtime_result.assert_not_called()

    snapshot = coordinator.snapshot(target="localhost:5555")
    assert snapshot.connected is False
    assert snapshot.warning_active
    assert snapshot.retry_in_s <= 30.0


def test_concurrent_callers_share_one_due_connection_attempt():
    start = threading.Barrier(8)
    is_connected = Mock(return_value=False)
    connect = Mock(return_value=False)
    coordinator = AdbConnectionCoordinator(
        clock=lambda: 100.0,
        is_connected=is_connected,
        connect=connect,
        reconnect_delays_s=(30.0,),
    )

    def ensure() -> bool:
        start.wait()
        return coordinator.ensure_connected(target="localhost:5555")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: ensure(), range(8)))

    assert results == [False] * 8
    assert is_connected.call_count == 2
    assert connect.call_count == 1


def test_reported_connect_success_requires_post_connect_device_state():
    is_connected = Mock(side_effect=[False, False])
    connect = Mock(return_value=True)
    coordinator = AdbConnectionCoordinator(
        clock=lambda: 10.0,
        is_connected=is_connected,
        connect=connect,
        reconnect_delays_s=(30.0,),
    )

    with patch("core.adb_connection.log"):
        assert not coordinator.ensure_connected(target="localhost:5555")

    connect.assert_called_once_with("localhost:5555")
    assert is_connected.call_args_list == [
        call("localhost:5555"),
        call("localhost:5555"),
    ]
    snapshot = coordinator.snapshot(target="localhost:5555")
    assert snapshot.connected is False
    assert snapshot.failures == 1
    assert snapshot.retry_in_s == 30.0


def test_tcp_reconnect_refreshes_only_the_selected_target():
    disconnect_result = subprocess.CompletedProcess(
        ["adb", "disconnect", "localhost:5555"],
        0,
        stdout="disconnected localhost:5555\n",
        stderr="",
    )
    connect_result = subprocess.CompletedProcess(
        ["adb", "connect", "localhost:5555"],
        0,
        stdout="already connected to localhost:5555\n",
        stderr="",
    )

    with patch(
        "core.adb_connection.subprocess.run",
        side_effect=[disconnect_result, connect_result],
    ) as run:
        assert _adb_connect("localhost:5555")

    assert [call.args[0] for call in run.call_args_list] == [
        ["adb", "disconnect", "localhost:5555"],
        ["adb", "connect", "localhost:5555"],
    ]


def test_persistent_outage_records_one_recovery_after_supported_capture():
    now = {"value": 0.0}
    connected = {"value": False}
    coordinator = AdbConnectionCoordinator(
        clock=lambda: now["value"],
        is_connected=lambda _target: connected["value"],
        connect=lambda _target: False,
        reconnect_delays_s=(1.0, 2.0, 5.0),
        warning_after_failures=3,
    )

    with (
        patch("core.adb_connection.log"),
        patch("core.adb_connection.log_result") as runtime_result,
    ):
        for timestamp in (0.0, 1.0, 3.0):
            now["value"] = timestamp
            assert not coordinator.ensure_connected(target="localhost:5555")

        connected["value"] = True
        now["value"] = 8.0
        assert coordinator.ensure_connected(target="localhost:5555")
        runtime_result.assert_not_called()

        coordinator.record_capture_success(target="localhost:5555")
        coordinator.record_capture_success(target="localhost:5555")

    runtime_result.assert_called_once()
    assert "Connection recovered" in runtime_result.call_args.args[0]
    assert "3 failed reconnect attempts" in runtime_result.call_args.args[0]
    snapshot = coordinator.snapshot(target="localhost:5555")
    assert snapshot.connected is True
    assert snapshot.failures == 0
    assert not snapshot.warning_active


def test_target_switch_has_independent_schedule_and_retains_old_outage():
    now = {"value": 10.0}
    connected_targets = set()
    connect = Mock(return_value=False)
    coordinator = AdbConnectionCoordinator(
        clock=lambda: now["value"],
        is_connected=lambda target: target in connected_targets,
        connect=connect,
        reconnect_delays_s=(30.0,),
    )

    with (
        patch("core.adb_connection.log"),
        patch("core.adb_connection.log_result"),
    ):
        assert not coordinator.ensure_connected(target="localhost:5555")
        assert not coordinator.ensure_connected(
            target="localhost:5565",
            force=True,
        )
        assert connect.call_count == 2

        assert not coordinator.ensure_connected(target="localhost:5555")
        assert connect.call_count == 2

        connected_targets.add("localhost:5565")
        assert coordinator.ensure_connected(
            target="localhost:5565",
            force=True,
        )
        coordinator.record_capture_success(target="localhost:5565")

    assert coordinator.snapshot(target="localhost:5565").connected is True
    old_snapshot = coordinator.snapshot(target="localhost:5555")
    assert old_snapshot.connected is False
    assert old_snapshot.failures == 1


def test_observer_does_not_manage_or_emit_for_a_missing_target():
    connect = Mock(return_value=False)
    coordinator = AdbConnectionCoordinator(
        clock=lambda: 10.0,
        is_connected=lambda _target: False,
        connect=connect,
        reconnect_delays_s=(0.0,),
        warning_after_failures=1,
        manage_connections=False,
        emit_events=False,
    )

    with patch("core.adb_connection.log") as runtime_log:
        assert not coordinator.ensure_connected(target="localhost:5555")

    connect.assert_not_called()
    runtime_log.assert_not_called()
    assert coordinator.snapshot(target="localhost:5555").failures == 1


def test_persistent_owner_records_recovery_from_exact_device_state():
    now = {"value": 0.0}
    connected = {"value": False}
    coordinator = AdbConnectionCoordinator(
        clock=lambda: now["value"],
        is_connected=lambda _target: connected["value"],
        connect=lambda _target: False,
        reconnect_delays_s=(0.0,),
        warning_after_failures=1,
        recovery_requires_capture=False,
    )

    with (
        patch("core.adb_connection.log"),
        patch("core.adb_connection.log_result") as runtime_result,
    ):
        assert not coordinator.ensure_connected(target="localhost:5555")
        connected["value"] = True
        now["value"] = 2.0
        assert coordinator.ensure_connected(target="localhost:5555")

    runtime_result.assert_called_once()
    assert "Connection registration recovered" in runtime_result.call_args.args[0]
    snapshot = coordinator.snapshot(target="localhost:5555")
    assert snapshot.connected is True
    assert snapshot.failures == 0


def test_persistent_manager_registers_and_reports_configured_target():
    is_connected = Mock(side_effect=[False, True])
    connect = Mock(return_value=True)
    coordinator = AdbConnectionCoordinator(
        is_connected=is_connected,
        connect=connect,
        reconnect_delays_s=(30.0,),
        recovery_requires_capture=False,
    )
    manager = PersistentAdbConnectionManager(
        lambda: "localhost:5555",
        coordinator=coordinator,
        timestamp=lambda: "2026-08-05T12:00:00-07:00",
    )

    assert manager.ensure_configured_target(force=True)

    connect.assert_called_once_with("localhost:5555")
    assert manager.status() == {
        "owner": "control-surface",
        "target": "localhost:5555",
        "state": "device",
        "connected": True,
        "failures": 0,
        "warning_active": False,
        "retry_in_seconds": 0.0,
        "last_checked_at": "2026-08-05T12:00:00-07:00",
        "error": None,
    }


def test_persistent_manager_follows_only_the_new_persisted_target():
    configured_target = {"value": "localhost:5555"}
    connect = Mock(return_value=False)
    coordinator = AdbConnectionCoordinator(
        is_connected=lambda _target: False,
        connect=connect,
        reconnect_delays_s=(30.0,),
        recovery_requires_capture=False,
    )
    manager = PersistentAdbConnectionManager(
        lambda: configured_target["value"],
        coordinator=coordinator,
    )

    with patch("core.adb_connection.log"):
        assert not manager.ensure_configured_target(force=True)
        configured_target["value"] = "localhost:5565"
        assert not manager.ensure_configured_target(force=True)

    assert connect.call_args_list == [
        call("localhost:5555"),
        call("localhost:5565"),
    ]
    status = manager.status()
    assert status["target"] == "localhost:5565"
    assert status["state"] == "unavailable"
    assert status["failures"] == 1


def test_persistent_manager_reports_configuration_error_and_recovers():
    configured_target = {"value": "invalid"}
    coordinator = AdbConnectionCoordinator(
        is_connected=lambda _target: True,
        reconnect_delays_s=(30.0,),
        recovery_requires_capture=False,
    )
    manager = PersistentAdbConnectionManager(
        lambda: configured_target["value"],
        coordinator=coordinator,
    )

    with (
        patch("core.adb_connection.log") as runtime_log,
        patch("core.adb_connection.log_result") as runtime_result,
    ):
        assert not manager.ensure_configured_target()
        assert not manager.ensure_configured_target()
        status = manager.status()
        assert status["state"] == "configuration_error"
        assert status["target"] is None
        assert "localhost:PORT" in status["error"]
        assert runtime_log.call_count == 1

        configured_target["value"] = "localhost:5555"
        assert manager.ensure_configured_target(force=True)

    runtime_result.assert_called_once_with(
        "[ADB] Connection configuration recovered — monitoring localhost:5555"
    )
    assert manager.status()["state"] == "device"


def test_persistent_manager_runs_until_its_service_owner_stops():
    stop_event = threading.Event()
    observations = {"count": 0}

    def is_connected(_target):
        observations["count"] += 1
        if observations["count"] == 2:
            stop_event.set()
        return True

    manager = PersistentAdbConnectionManager(
        lambda: "localhost:5555",
        coordinator=AdbConnectionCoordinator(
            is_connected=is_connected,
            reconnect_delays_s=(30.0,),
            recovery_requires_capture=False,
        ),
        poll_interval_s=0.1,
    )

    manager.run(stop_event)

    assert observations["count"] == 2
    assert manager.status()["state"] == "device"
