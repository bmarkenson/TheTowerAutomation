from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock, patch

from core.adb_connection import AdbConnectionCoordinator


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
    assert is_connected.call_count == connect.call_count
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
    assert is_connected.call_count == 1
    assert connect.call_count == 1


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
