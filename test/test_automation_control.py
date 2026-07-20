import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.automation_supervisor import AutomationSupervisor
from core.control_directives import ControlDirectiveError, ControlDirectiveStore
from core.run_state import AUTOMATION
from tools.automation_ctl import main as automation_ctl_main


@pytest.fixture(autouse=True)
def restore_automation_state():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    try:
        yield
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode


def _supervisor(control_file: Path) -> AutomationSupervisor:
    return AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
    )


def test_pause_remains_authoritative_until_explicit_resume(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    supervisor = _supervisor(control_file)

    assert automation_ctl_main(
        ["--control-file", str(control_file), "pause"]
    ) == 0
    supervisor.apply_control()
    assert supervisor.is_paused

    with patch("core.automation_supervisor.time.time", return_value=10**12):
        supervisor.apply_control()
    assert supervisor.is_paused
    assert json.loads(control_file.read_text(encoding="utf-8"))["state"] == "PAUSED"

    assert automation_ctl_main(
        ["--control-file", str(control_file), "resume"]
    ) == 0
    supervisor.apply_control()
    assert not supervisor.is_paused
    assert AUTOMATION.state.value == "RUNNING"


def test_timed_pause_expiry_persists_resume_before_changing_memory(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    supervisor = _supervisor(control_file)

    with patch("tools.automation_ctl.time.time", return_value=1_000.0):
        assert automation_ctl_main(
            [
                "--control-file",
                str(control_file),
                "pause",
                "--minutes",
                "5",
            ]
        ) == 0

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert saved["resume_at"] == 1_300.0

    with patch("core.automation_supervisor.time.time", return_value=1_299.0):
        supervisor.apply_control()
    assert supervisor.is_paused

    with patch("core.automation_supervisor.time.time", return_value=1_301.0):
        supervisor.apply_control()
    assert not supervisor.is_paused
    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "RUNNING"
    assert "resume_at" not in saved

    supervisor.apply_control()
    assert not supervisor.is_paused


def test_indefinite_pause_replaces_existing_timed_pause(tmp_path):
    control_file = tmp_path / "automation_ctl.json"

    with patch("tools.automation_ctl.time.time", return_value=1_000.0):
        assert automation_ctl_main(
            [
                "--control-file",
                str(control_file),
                "pause",
                "--minutes",
                "5",
            ]
        ) == 0
    assert automation_ctl_main(
        ["--control-file", str(control_file), "pause"]
    ) == 0

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert "resume_at" not in saved


def test_timed_pause_stays_paused_when_persisted_resume_fails(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    control_file.write_text(
        json.dumps({"state": "PAUSED", "resume_at": 1_300.0}),
        encoding="utf-8",
    )
    supervisor = _supervisor(control_file)

    with (
        patch("core.automation_supervisor.time.time", return_value=1_301.0),
        patch.object(
            supervisor._control_store,
            "resume_expired_pause",
            side_effect=ControlDirectiveError("simulated persistence failure"),
        ),
    ):
        supervisor.apply_control()

    assert supervisor.is_paused
    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert saved["resume_at"] == 1_300.0


def test_default_runtime_configuration_has_no_global_pause_expiry_options():
    from core.app_setup import config_from_args, parse_args

    config = config_from_args(parse_args([]))

    assert not hasattr(config, "auto_resume_enabled")
    assert not hasattr(config, "auto_resume_secs")
    with pytest.raises(SystemExit):
        parse_args(["--auto-resume-minutes", "15"])


def test_runtime_owned_mode_transition_is_persisted_before_waiting(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    control_file.write_text(
        json.dumps({"state": "RUNNING", "mode": "RETRY"}),
        encoding="utf-8",
    )
    supervisor = _supervisor(control_file)
    supervisor.apply_control()

    assert supervisor.persist_mode("WAIT")

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "RUNNING"
    assert saved["mode"] == "WAIT"
    assert saved["updated_at"]
    assert AUTOMATION.mode.value == "WAIT"


def test_paused_runtime_applies_adb_port_handoff(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    control_file.write_text(
        json.dumps(
            {
                "state": "PAUSED",
                "mode": "RETRY",
                "adb_port": 5565,
                "adb_port_updated_at": "2026-07-20T04:00:00-07:00",
            }
        ),
        encoding="utf-8",
    )
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda port: handoffs.append(port) or True,
    )

    with patch("core.automation_supervisor.log") as runtime_log:
        supervisor.apply_control()
        supervisor.apply_control()

    assert handoffs == [5565]
    assert any(
        call.args
        and call.args[0]
        == "[CTRL] ADB target set to localhost:5565 via control file"
        for call in runtime_log.call_args_list
    )


def test_running_runtime_defers_adb_port_until_paused(tmp_path, monkeypatch):
    monkeypatch.delenv("ADB_DEVICE", raising=False)
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    store.set_adb_port(5565, source="test")
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda port: handoffs.append(port) or True,
    )

    supervisor.apply_control()
    assert handoffs == []

    store.set_state("PAUSED", source="test")
    supervisor.apply_control()
    assert handoffs == [5565]


def test_running_runtime_acknowledges_already_selected_adb_target(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5565")
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    store.set_adb_port(5565, source="test")
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda port: handoffs.append(port) or True,
    )

    with patch("core.automation_supervisor.log") as runtime_log:
        supervisor.apply_control()

    assert handoffs == []
    assert any(
        call.args
        and call.args[0]
        == "[CTRL] ADB target set to localhost:5565 via control file"
        for call in runtime_log.call_args_list
    )
