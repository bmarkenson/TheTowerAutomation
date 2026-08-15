from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.adb_connection import AdbConnectionCoordinator
from core.app import App
from core.ss_capture import ScreenshotCaptureResult, ScreenshotFailure


def _emulator_location(host_id: str, host_name: str, process_id: int) -> dict:
    return {
        "schema_version": 1,
        "host_id": host_id,
        "host_name": host_name,
        "linux_adb_port": 5555,
        "request_id": f"request-{process_id}",
        "selected_at": "2026-08-15T20:00:00+00:00",
        "bluestacks_listener": {
            "adb_port": 5565,
            "process_id": process_id,
            "process_started_at": f"2026-08-15T{process_id % 24:02d}:00:00+00:00",
            "executable_path": r"C:\BlueStacks\HD-Player.exe",
            "instance_name": "Nougat32",
        },
    }


def test_control_is_synchronized_before_each_capture_attempt():
    events = []
    supervisor = MagicMock()
    supervisor.is_paused = True
    supervisor.apply_control.side_effect = lambda: events.append("control")

    app = App.__new__(App)
    app._config = SimpleNamespace(wait_on_start=False)
    app._supervisor = supervisor
    app._blind_tapper_suspended = False
    app._adb_connection_coordinator = MagicMock()
    app._adb_connection_coordinator.ensure_connected.return_value = False

    capture_results = iter((None, KeyboardInterrupt()))

    def capture():
        events.append("capture")
        result = next(capture_results)
        if isinstance(result, BaseException):
            raise result
        return result

    app._capture_frame = capture

    with (
        patch("core.app.threading.Thread"),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.log"),
    ):
        app.run()

    assert events == [
        "control",
        "control",
        "capture",
        "control",
        "capture",
    ]


def test_long_paused_outage_skips_known_disconnected_capture_noise():
    now = {"value": 0.0}
    is_connected = Mock(return_value=False)
    # A stale TCP transport can claim "already connected" while ADB still
    # reports the exact target as offline.
    connect = Mock(return_value=True)
    coordinator = AdbConnectionCoordinator(
        clock=lambda: now["value"],
        is_connected=is_connected,
        connect=connect,
    )
    app = App.__new__(App)
    app._adb_connection_coordinator = coordinator
    empty = ScreenshotCaptureResult(
        None,
        ScreenshotFailure.EMPTY,
        "empty screenshot data",
    )

    with (
        patch(
            "core.app.capture_and_save_screenshot_result",
            return_value=empty,
        ) as capture,
        patch("core.app.time.sleep"),
        patch("core.app.log") as app_log,
        patch("core.adb_connection.log") as connection_log,
        patch("core.adb_connection.log_result") as connection_result,
    ):
        for cycle in range(1800):
            now["value"] = float(cycle * 2)
            assert app._capture_frame() is None

    capture.assert_called_once_with(
        log_capture=False,
        log_empty=False,
        report_adb_errors=False,
    )
    assert connect.call_count < 130
    assert is_connected.call_count == connect.call_count * 2
    app_log.assert_not_called()
    assert 10 <= len(connection_log.call_args_list) <= 13
    assert all(
        call.args[1] == "WARN" for call in connection_log.call_args_list
    )
    connection_result.assert_not_called()


def test_connected_capture_corruption_retains_terminal_failure():
    coordinator = MagicMock()
    coordinator.capture_allowed.return_value = True
    coordinator.ensure_connected.return_value = True
    app = App.__new__(App)
    app._adb_connection_coordinator = coordinator
    malformed = ScreenshotCaptureResult(
        None,
        ScreenshotFailure.MALFORMED,
        "invalid PNG",
    )

    with (
        patch(
            "core.app.capture_and_save_screenshot_result",
            side_effect=[malformed, malformed],
        ) as capture,
        patch("core.app.time.sleep"),
        patch("core.app.log") as app_log,
    ):
        assert app._capture_frame() is None

    assert capture.call_count == 2
    coordinator.record_capture_success.assert_not_called()
    assert any(
        call.kwargs.get("level") == "FAIL"
        and "ADB remained connected" in call.args[0]
        and "invalid PNG" in call.args[0]
        for call in app_log.call_args_list
    )


def test_landscape_launcher_capture_is_retained_without_retry_noise():
    coordinator = MagicMock()
    coordinator.capture_allowed.return_value = True
    app = App.__new__(App)
    app._adb_connection_coordinator = coordinator
    app._emulator_maintenance_hold_active = True
    app._supervisor = SimpleNamespace(
        emulator_maintenance={
            "state": "host_restarted",
            "runtime": {"adb_target": "localhost:5555"},
        }
    )
    landscape = ScreenshotCaptureResult(
        None,
        ScreenshotFailure.UNSUPPORTED_GEOMETRY,
        "Unsupported emulator resolution 1920x1080",
        adb_target="localhost:5555",
        native_width=1920,
        native_height=1080,
    )

    with (
        patch(
            "core.app.capture_and_save_screenshot_result",
            return_value=landscape,
        ) as capture,
        patch("core.app.time.sleep") as sleep,
        patch("core.app.log") as app_log,
    ):
        assert app._capture_frame() is None

    capture.assert_called_once_with(
        log_capture=False,
        log_empty=False,
        report_adb_errors=False,
    )
    coordinator.ensure_connected.assert_not_called()
    coordinator.record_capture_success.assert_called_once_with(
        target="localhost:5555"
    )
    assert app._last_screenshot_capture_result is landscape
    sleep.assert_called_once_with(1)
    app_log.assert_not_called()


def test_paused_target_handoff_forces_validation_and_records_fresh_capture():
    app = App.__new__(App)
    app._blind_tapper_suspended = False
    app._adb_connection_coordinator = MagicMock()
    app._adb_connection_coordinator.ensure_connected.return_value = True
    session = MagicMock()
    session.handoff.side_effect = lambda _target, *, validate: validate()
    app._adb_target_session = session
    frame = object()

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
        patch(
            "core.app.capture_and_save_screenshot_result",
            return_value=ScreenshotCaptureResult(frame),
        ),
        patch("core.app.log"),
    ):
        assert app._handoff_adb_port(5565)

    session.handoff.assert_called_once()
    assert session.handoff.call_args.args[0] == "localhost:5565"
    app._adb_connection_coordinator.ensure_connected.assert_called_once_with(
        force=True
    )
    app._adb_connection_coordinator.record_capture_success.assert_called_once()


def test_emulator_location_timeline_marks_mixed_host_battles_out_of_cph_cohort():
    host_a = _emulator_location(
        "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686",
        "MAIN-PC",
        101,
    )
    host_b = _emulator_location(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "WORKSTATION-B",
        202,
    )
    app = App.__new__(App)
    app._adb_target_session = MagicMock()
    app._adb_target_session.snapshot.return_value = SimpleNamespace(
        generation=7,
        owned=True,
    )
    app._emulator_location_binding = host_a
    app._emulator_location_round = None

    app._begin_emulator_location_round("battle-a")
    complete = app._terminal_emulator_location("battle-a")
    assert complete["status"] == "complete"
    assert complete["analytics_host_id"] == host_a["host_id"]

    app._record_emulator_location(host_b, active_round_identity="battle-a")
    app._record_emulator_location(host_a, active_round_identity="battle-a")
    mixed = app._terminal_emulator_location("battle-a")

    assert mixed["status"] == "mixed_hosts"
    assert mixed["coverage_complete"] is True
    assert mixed["host_change_count"] == 2
    assert mixed["listener_lifetime_count"] == 2
    assert mixed["analytics_host_id"] is None
    assert [item["host_name"] for item in mixed["locations"]] == [
        "MAIN-PC",
        "WORKSTATION-B",
        "MAIN-PC",
    ]


def test_paused_same_port_host_handoff_keeps_observed_battle_attribution():
    host_a = _emulator_location(
        "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686",
        "MAIN-PC",
        101,
    )
    host_b = _emulator_location(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "WORKSTATION-B",
        202,
    )
    app = App.__new__(App)
    app._adb_target_session = None
    app._emulator_location_binding = host_a
    app._emulator_location_round = None
    app._observed_active_round_identity_fingerprint = "battle-a"
    app._current_player_save_observation_context = lambda: None
    app._handoff_adb_port = MagicMock(return_value=True)
    app._begin_emulator_location_round("battle-a")

    assert app._handoff_emulator_location(5555, host_b)

    app._handoff_adb_port.assert_called_once_with(
        5555,
        revalidate_current=True,
    )
    evidence = app._terminal_emulator_location("battle-a")
    assert evidence["status"] == "mixed_hosts"
    assert evidence["host_change_count"] == 1


def test_paused_target_handoff_waits_for_validation_terminal_claim():
    app = App.__new__(App)
    app._blind_tapper_suspended = False
    app._adb_connection_coordinator = MagicMock()
    app._adb_connection_coordinator.ensure_connected.return_value = True
    session = MagicMock()
    session.handoff.side_effect = lambda _target, *, validate: validate()
    app._adb_target_session = session
    app._exclusive_validation_terminal_hold = "validation-cleanup"
    frame = object()

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
        patch(
            "core.app.capture_and_save_screenshot_result",
            return_value=ScreenshotCaptureResult(frame),
        ),
        patch("core.app.log") as runtime_log,
    ):
        assert not app._handoff_adb_port(5565)
        session.handoff.assert_not_called()
        app._adb_connection_coordinator.ensure_connected.assert_not_called()

        app._exclusive_validation_terminal_hold = None
        assert app._handoff_adb_port(5565)

    session.handoff.assert_called_once()
    assert any(
        call.args
        and "Deferring paused ADB target handoff" in call.args[0]
        for call in runtime_log.call_args_list
    )


@pytest.mark.parametrize(
    ("validation_active", "launch_active"),
    ((True, False), (False, True)),
)
def test_paused_target_handoff_waits_for_active_validation_route(
    validation_active,
    launch_active,
):
    app = App.__new__(App)
    app._adb_target_session = MagicMock()
    app._supervisor = MagicMock()
    app._mission_mgr = MagicMock()
    app._exclusive_validation_terminal_hold = None
    app._exclusive_validation_in_progress = MagicMock(
        return_value=validation_active
    )
    app._exclusive_validation_launch_in_progress = MagicMock(
        return_value=launch_active
    )

    with patch("core.app.log"):
        assert not app._handoff_adb_port(5565)

    app._adb_target_session.handoff.assert_not_called()


@pytest.mark.parametrize(
    "active_field",
    (
        "_active_exclusive_validation_request_id",
        "_active_exclusive_validation_launch_request_id",
        "_exclusive_validation_passive_battle_hold",
    ),
)
def test_paused_target_handoff_fails_closed_on_cached_validation_identity(
    active_field,
):
    app = App.__new__(App)
    app._adb_target_session = MagicMock()
    app._supervisor = MagicMock()
    app._mission_mgr = MagicMock()
    app._exclusive_validation_terminal_hold = None
    app._active_exclusive_validation_request_id = None
    app._active_exclusive_validation_launch_request_id = None
    app._exclusive_validation_passive_battle_hold = None
    app._exclusive_validation_ownership_hold = False
    setattr(app, active_field, "cached-validation-owner")
    app._exclusive_validation_in_progress = MagicMock(return_value=False)
    app._exclusive_validation_launch_in_progress = MagicMock(
        return_value=False
    )

    with patch("core.app.log"):
        assert not app._handoff_adb_port(5565)

    app._adb_target_session.handoff.assert_not_called()
    app._exclusive_validation_in_progress.assert_not_called()
    app._exclusive_validation_launch_in_progress.assert_not_called()
