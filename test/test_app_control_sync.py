from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.app import App


def test_control_is_synchronized_before_each_capture_attempt():
    events = []
    supervisor = MagicMock()
    supervisor.is_paused = True
    supervisor.apply_control.side_effect = lambda: events.append("control")

    app = App.__new__(App)
    app._config = SimpleNamespace(wait_on_start=False)
    app._supervisor = supervisor
    app._blind_tapper_suspended = False

    capture_results = iter((None, KeyboardInterrupt()))

    def capture():
        events.append("capture")
        result = next(capture_results)
        if isinstance(result, BaseException):
            raise result
        return result

    app._capture_frame = capture

    with (
        patch("core.app.ensure_adb_connected", return_value=False),
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
