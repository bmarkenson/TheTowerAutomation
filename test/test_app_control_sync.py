from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from core.adb_connection import AdbConnectionCoordinator
from core.app import App
from core.ss_capture import ScreenshotCaptureResult, ScreenshotFailure


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
