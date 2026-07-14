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


def test_default_runtime_configuration_has_no_pause_expiry_options():
    from core.app_setup import config_from_args, parse_args

    config = config_from_args(parse_args([]))

    assert not hasattr(config, "auto_resume_enabled")
    assert not hasattr(config, "auto_resume_secs")
    with pytest.raises(SystemExit):
        parse_args(["--auto-resume-minutes", "15"])
