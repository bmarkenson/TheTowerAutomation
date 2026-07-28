from unittest.mock import patch

from core.tap_dispatcher import _execute_tap


def test_queued_tap_logs_dispatch_only_after_adb_success(tmp_path, monkeypatch):
    action_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(action_log))

    with patch("core.tap_dispatcher.input_tap", return_value=object()):
        assert _execute_tap(10, 20, "test_target", log_it=True)

    lines = action_log.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("[INPUT ")
    assert lines[0].endswith("] Tap dispatched: test_target")
    assert lines[1].endswith("] TAP test_target at (10,20)")


def test_queued_tap_reports_adb_failure_without_claiming_dispatch(
    tmp_path,
    monkeypatch,
):
    action_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(action_log))

    with patch("core.tap_dispatcher.input_tap", return_value=None):
        assert not _execute_tap(10, 20, "test_target", log_it=True)

    lines = action_log.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("[WARN ")
    assert lines[0].endswith("] Queued tap failed: test_target")
    assert lines[1].startswith("[DEBUG ")
    assert lines[1].endswith("] TAP failed label=test_target at (10,20)")
    assert not any(line.startswith("[INPUT ") for line in lines)
