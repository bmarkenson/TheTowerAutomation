import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.automation_supervisor import AutomationSupervisor
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
        patch.object(supervisor, "_write_control_directive", return_value=False),
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
