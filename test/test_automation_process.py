from __future__ import annotations

import fcntl
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import stat
import time

import pytest

from core.automation_process import (
    AutomationProcessError,
    SystemdAutomationManager,
)
from core.app_setup import parse_args
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService


class FakeManager:
    def __init__(self, *, active: bool = False, fail_start: bool = False) -> None:
        self.active = active
        self.fail_start = fail_start
        self.adb_port = 5555
        self.strategy = "farm"
        self.startup_gate_policy = "auto_validate"
        self.pid = os.getpid()
        self.calls: list[str] = []
        self.on_start = None
        self.on_stop = None

    def status(self):
        return {
            "manager": "fake",
            "service": "thetower-automation.service",
            "available": True,
            "active": self.active,
            "active_state": "active" if self.active else "inactive",
            "sub_state": "running" if self.active else "dead",
            "main_pid": self.pid if self.active else None,
            "adb_port": self.adb_port,
            "adb_target": f"localhost:{self.adb_port}",
            "strategy": self.strategy,
            "startup_gate_policy": self.startup_gate_policy,
            "error": None,
        }

    def set_adb_port(self, port):
        self.calls.append(f"set_adb_port:{port}")
        if self.active:
            raise AutomationProcessError(
                "Completely stop automation before changing the ADB port"
            )
        self.adb_port = port
        return self.status()

    def persist_adb_port(self, port):
        self.calls.append(f"persist_adb_port:{port}")
        self.adb_port = port
        return self.status()

    def set_strategy(self, strategy):
        self.calls.append(f"set_strategy:{strategy}")
        if self.active:
            raise AutomationProcessError(
                "Completely stop automation before changing the strategy"
            )
        if strategy not in {
            "farm_t18",
            "farm_t19",
            "tournament",
            "none",
        }:
            raise AutomationProcessError("invalid strategy")
        self.strategy = strategy
        return self.status()

    def persist_strategy(self, strategy):
        self.calls.append(f"persist_strategy:{strategy}")
        if strategy not in {
            "farm_t18",
            "farm_t19",
            "tournament",
            "none",
        }:
            raise AutomationProcessError("invalid strategy")
        self.strategy = strategy
        return self.status()

    def set_startup_gate_policy(self, policy):
        self.calls.append(f"set_startup_gate_policy:{policy}")
        if self.active:
            raise AutomationProcessError(
                "Completely stop automation before changing startup gates"
            )
        if policy not in {"auto", "auto_validate", "immediate", "next_run"}:
            raise AutomationProcessError("invalid startup gate policy")
        self.startup_gate_policy = policy
        return self.status()

    def persist_startup_gate_policy(self, policy):
        self.calls.append(f"persist_startup_gate_policy:{policy}")
        if policy not in {"auto", "auto_validate", "immediate", "next_run"}:
            raise AutomationProcessError("invalid startup gate policy")
        self.startup_gate_policy = policy
        return self.status()

    def start(self):
        self.calls.append("start")
        if self.on_start:
            self.on_start()
        if self.fail_start:
            raise AutomationProcessError("simulated start failure")
        self.pid += 1
        self.active = True
        return self.status()

    def stop(self):
        self.calls.append("stop")
        if self.on_stop:
            self.on_stop()
        self.active = False
        return self.status()


def _service(tmp_path, manager=None):
    return ControlSurfaceService(
        repository_root=tmp_path,
        process_manager=manager,
    )


def _write_running_runtime_evidence(tmp_path, *, pid: int) -> Path:
    timestamp = datetime.now().astimezone().replace(microsecond=0)
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[STATUS {timestamp.strftime('%Y-%m-%d %H:%M:%S')}] "
        "State=RUNNING | Wave=123 | Coins/min=1.0T\n",
        encoding="utf-8",
    )
    lock_path = tmp_path / "logs" / "automation-localhost_5555.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": pid,
                "target": "localhost:5555",
                "started_at": timestamp.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return lock_path


def test_runtime_strategy_defaults_from_managed_environment(monkeypatch):
    monkeypatch.setenv("THETOWER_STRATEGY", "tournament")

    assert parse_args([]).strategy == "tournament"


def test_startup_gate_policy_defaults_from_managed_environment(monkeypatch):
    monkeypatch.setenv("THETOWER_STARTUP_GATES", "next_run")

    assert parse_args([]).startup_gates == "next_run"


def test_startup_gate_policy_defaults_to_automatic_attached_validation(monkeypatch):
    monkeypatch.delenv("THETOWER_STARTUP_GATES", raising=False)

    assert parse_args([]).startup_gates == "auto_validate"


def test_systemd_manager_uses_only_the_fixed_named_service(tmp_path):
    active = False
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        nonlocal active
        commands.append(command)
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "timeout": 15.0,
            "check": False,
        }
        if command[2] == "start":
            active = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[2] == "stop":
            active = False
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[2] == "show"
        output = (
            "LoadState=loaded\n"
            f"ActiveState={'active' if active else 'inactive'}\n"
            f"SubState={'running' if active else 'dead'}\n"
            "UnitFileState=enabled\n"
            f"MainPID={4321 if active else 0}\n"
            "ExecMainStatus=0\n"
            f"EnvironmentFiles={tmp_path / 'automation-adb.env'} "
            "(ignore_errors=yes)\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=tmp_path / "automation-adb.env",
    )
    assert not manager.status()["active"]
    assert manager.start()["main_pid"] == 4321
    assert manager.stop()["active"] is False
    assert all(command[-1] == "thetower-automation.service" for command in commands)
    assert commands[1] == [
        "systemctl",
        "--user",
        "start",
        "thetower-automation.service",
    ]


def test_systemd_manager_persists_stopped_adb_target_atomically(tmp_path):
    environment_file = tmp_path / "config" / "automation-adb.env"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
            "UnitFileState=disabled\nMainPID=0\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    assert manager.status()["adb_target"] == "localhost:5555"
    status = manager.set_adb_port(5565)

    assert environment_file.read_text(encoding="utf-8") == (
        "THETOWER_ADB_PORT=5565\n"
        "THETOWER_STRATEGY=farm\n"
        "THETOWER_STARTUP_GATES=auto_validate\n"
    )
    assert stat.S_IMODE(environment_file.stat().st_mode) == 0o600
    assert status["adb_port"] == 5565
    assert status["adb_target"] == "localhost:5565"
    assert status["adb_port_source"] == "environment-file"


def test_systemd_manager_persists_strategy_and_preserves_adb_port(tmp_path):
    environment_file = tmp_path / "config" / "automation-adb.env"
    environment_file.parent.mkdir(parents=True)
    environment_file.write_text(
        "THETOWER_ADB_PORT=5565\nTHETOWER_STRATEGY=farm_t18\n",
        encoding="utf-8",
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
            "UnitFileState=disabled\nMainPID=0\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.set_strategy("tournament")

    assert environment_file.read_text(encoding="utf-8") == (
        "THETOWER_ADB_PORT=5565\n"
        "THETOWER_STRATEGY=tournament\n"
        "THETOWER_STARTUP_GATES=auto_validate\n"
    )
    assert status["adb_port"] == 5565


def test_systemd_manager_can_persist_strategy_while_active(tmp_path):
    environment_file = tmp_path / "config" / "automation-adb.env"
    environment_file.parent.mkdir(parents=True)
    environment_file.write_text(
        "THETOWER_ADB_PORT=5565\nTHETOWER_STRATEGY=farm_t18\n",
        encoding="utf-8",
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=active\nSubState=running\n"
            "UnitFileState=enabled\nMainPID=1234\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.persist_strategy("tournament")

    assert status["active"] is True
    assert status["strategy"] == "tournament"
    assert environment_file.read_text(encoding="utf-8") == (
        "THETOWER_ADB_PORT=5565\n"
        "THETOWER_STRATEGY=tournament\n"
        "THETOWER_STARTUP_GATES=auto_validate\n"
    )


def test_systemd_manager_can_persist_live_handoff_port_while_active(tmp_path):
    environment_file = tmp_path / "automation-adb.env"
    environment_file.write_text(
        "THETOWER_ADB_PORT=5565\nTHETOWER_STRATEGY=tournament\n",
        encoding="utf-8",
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=active\nSubState=running\n"
            "UnitFileState=enabled\nMainPID=1234\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.persist_adb_port(5575)

    assert status["active"] is True
    assert status["adb_port"] == 5575
    assert status["adb_target"] == "localhost:5575"
    assert status["strategy"] == "tournament"
    assert status["strategy_source"] == "environment-file"


def test_systemd_manager_persists_startup_gate_policy_and_other_settings(tmp_path):
    environment_file = tmp_path / "automation-adb.env"
    environment_file.write_text(
        "THETOWER_ADB_PORT=5565\nTHETOWER_STRATEGY=tournament\n",
        encoding="utf-8",
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
            "UnitFileState=enabled\nMainPID=0\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.set_startup_gate_policy("next_run")

    assert environment_file.read_text(encoding="utf-8") == (
        "THETOWER_ADB_PORT=5565\n"
        "THETOWER_STRATEGY=tournament\n"
        "THETOWER_STARTUP_GATES=next_run\n"
    )
    assert status["startup_gate_policy"] == "next_run"
    assert status["startup_gate_policy_source"] == "environment-file"


def test_systemd_manager_can_restore_next_start_policy_while_active(tmp_path):
    environment_file = tmp_path / "automation-adb.env"
    environment_file.write_text(
        "THETOWER_ADB_PORT=5565\n"
        "THETOWER_STRATEGY=tournament\n"
        "THETOWER_STARTUP_GATES=next_run\n",
        encoding="utf-8",
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=active\nSubState=running\n"
            "UnitFileState=enabled\nMainPID=1234\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.persist_startup_gate_policy("immediate")

    assert status["active"] is True
    assert status["startup_gate_policy"] == "immediate"
    assert environment_file.read_text(encoding="utf-8") == (
        "THETOWER_ADB_PORT=5565\n"
        "THETOWER_STRATEGY=tournament\n"
        "THETOWER_STARTUP_GATES=immediate\n"
    )


def test_systemd_manager_rejects_adb_change_while_active(tmp_path):
    environment_file = tmp_path / "automation-adb.env"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=active\nSubState=running\n"
            "UnitFileState=disabled\nMainPID=1234\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    with pytest.raises(AutomationProcessError, match="Completely stop"):
        manager.set_adb_port(5565)


def test_systemd_manager_reports_installed_unit_missing_managed_environment(tmp_path):
    environment_file = tmp_path / "automation-adb.env"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
            "UnitFileState=disabled\nMainPID=0\nExecMainStatus=0\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.status()

    assert status["automation_environment_file_loaded"] is False
    assert "does not load" in status["adb_port_error"]
    with pytest.raises(AutomationProcessError, match="does not load"):
        manager.set_adb_port(5565)


def test_systemd_manager_retains_repeated_environment_files(tmp_path):
    environment_file = tmp_path / "automation-adb.env"
    environment_file.write_text("", encoding="utf-8")
    audit_environment_file = tmp_path / "player-save-audit.env"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=active\nSubState=running\n"
            "UnitFileState=disabled\nMainPID=1234\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n"
            f"EnvironmentFiles={audit_environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.status()

    assert status["automation_environment_file_loaded"] is True
    assert str(environment_file) in status["service_environment_files"]
    assert str(audit_environment_file) in status["service_environment_files"]
    assert status["adb_port_error"] is None
    assert status["strategy_error"] is None
    assert status["startup_gate_policy_error"] is None


def test_checked_in_systemd_unit_loads_managed_automation_environment():
    repository_root = Path(__file__).resolve().parents[1]
    unit = (
        repository_root / "deploy" / "systemd" / "thetower-automation.service"
    ).read_text(encoding="utf-8")

    assert "EnvironmentFile=-%h/.config/thetower/automation-adb.env" in unit
    assert (
        "EnvironmentFile=-%h/.config/thetower/player-save-audit.env" in unit
    )


@pytest.mark.parametrize(
    "name",
    ["", "automation", "../evil.service", "one.service;reboot", "/x.service"],
)
def test_systemd_manager_rejects_non_unit_names(name):
    with pytest.raises(ValueError):
        SystemdAutomationManager(name)


def test_start_running_crosses_new_process_boundary_paused(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    manager.on_start = lambda: (
        service.control_store.read()["state"] == "PAUSED"
        or pytest.fail("service was not paused before process start")
    )

    response = service.apply_process_action(
        {"action": "start", "run_state": "RUNNING"}
    )

    assert manager.calls == ["set_startup_gate_policy:auto_validate", "start"]
    assert service.control_store.read()["state"] == "RUNNING"
    assert response["process_service"]["active"]
    assert response["request"] == {
        "accepted": True,
        "action": "start",
        "startup_gate_policy": "auto_validate",
    }


def test_start_can_attach_current_battle_and_persists_policy_before_start(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    manager.on_start = lambda: (
        manager.startup_gate_policy == "next_run"
        or pytest.fail("startup gate policy was not persisted before start")
    )

    response = service.apply_process_action(
        {
            "action": "start",
            "run_state": "RUNNING",
            "startup_gate_policy": "next_run",
        }
    )

    assert manager.calls == ["set_startup_gate_policy:next_run", "start"]
    assert response["process_service"]["startup_gate_policy"] == "next_run"
    assert response["request"] == {
        "accepted": True,
        "action": "start",
        "startup_gate_policy": "next_run",
    }


def test_start_persists_selected_strategy_before_process_reaches_home(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)

    def assert_selected_strategy_is_authoritative():
        assert manager.strategy == "farm_t19"
        control = service.control_store.read()
        assert control["strategy"] == "farm_t19"
        assert control["strategy_apply_mode"] == "next_boundary"
        assert control["state"] == "PAUSED"

    manager.on_start = assert_selected_strategy_is_authoritative
    response = service.apply_process_action(
        {
            "action": "start",
            "run_state": "RUNNING",
            "startup_gate_policy": "immediate",
            "strategy": "farm_t19",
        }
    )

    assert manager.calls == [
        "set_startup_gate_policy:immediate",
        "set_strategy:farm_t19",
        "start",
    ]
    assert response["process_service"]["strategy"] == "farm_t19"
    assert response["request"] == {
        "accepted": True,
        "action": "start",
        "startup_gate_policy": "immediate",
        "strategy": "farm_t19",
    }


def test_start_with_saved_tournament_creates_fresh_validation_request(tmp_path):
    manager = FakeManager()
    manager.strategy = "tournament"
    service = _service(tmp_path, manager)

    service.apply_process_action({"action": "start", "run_state": "PAUSED"})
    first = service.control_store.status()["exclusive_validation"]
    first_request_id = first["current_request_id"]
    assert first["receipts"][first_request_id]["status"] == "pending"

    service.apply_process_action({"action": "stop"})
    service.apply_process_action({"action": "start", "run_state": "PAUSED"})
    second = service.control_store.status()["exclusive_validation"]
    second_request_id = second["current_request_id"]

    assert second_request_id != first_request_id
    assert second["receipts"][second_request_id]["status"] == "pending"
    assert manager.calls == [
        "set_startup_gate_policy:auto_validate",
        "start",
        "stop",
        "set_startup_gate_policy:auto_validate",
        "start",
    ]


@pytest.mark.parametrize("policy", ["", "later", 1, True])
def test_start_rejects_invalid_startup_gate_policy(tmp_path, policy):
    manager = FakeManager()

    with pytest.raises(ControlSurfaceRequestError):
        _service(tmp_path, manager).apply_process_action(
            {
                "action": "start",
                "run_state": "RUNNING",
                "startup_gate_policy": policy,
            }
        )

    assert manager.calls == []


@pytest.mark.parametrize("strategy", [None, "", "unknown", 123])
def test_start_rejects_invalid_selected_strategy(tmp_path, strategy):
    manager = FakeManager()

    with pytest.raises(ControlSurfaceRequestError):
        _service(tmp_path, manager).apply_process_action(
            {
                "action": "start",
                "run_state": "RUNNING",
                "strategy": strategy,
            }
        )

    assert manager.calls == []


def test_start_rejects_selected_strategy_when_process_is_already_active(tmp_path):
    manager = FakeManager(active=True)

    with pytest.raises(
        ControlSurfaceRequestError,
        match="Completely stop automation",
    ) as exc_info:
        _service(tmp_path, manager).apply_process_action(
            {
                "action": "start",
                "run_state": "RUNNING",
                "strategy": "farm_t19",
            }
        )

    assert exc_info.value.status == 409
    assert manager.calls == []


def test_start_paused_and_complete_stop_preserve_safe_ordering(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    service.apply_process_action({"action": "start", "run_state": "PAUSED"})
    assert service.control_store.read()["state"] == "PAUSED"

    manager.on_stop = lambda: (
        service.control_store.read()["state"] == "STOPPED"
        or pytest.fail("STOPPED was not persisted before systemd stop")
    )
    response = service.apply_process_action({"action": "stop"})

    assert manager.calls == [
        "set_startup_gate_policy:auto_validate",
        "start",
        "stop",
    ]
    assert service.control_store.read()["state"] == "STOPPED"
    assert response["process_service"]["active"] is False


def test_attached_restart_pauses_replaces_and_restores_running_intent(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("RUNNING", source="test")
    lock_path = _write_running_runtime_evidence(tmp_path, pid=manager.pid)
    acknowledgements: list[str] = []
    pause_checks: list[tuple[int, int]] = []
    replacement_checks: list[tuple[int, int]] = []
    service._wait_for_attached_restart_pause = (
        lambda *, previous_pid, log_offset: pause_checks.append(
            (previous_pid, log_offset)
        )
        or {}
    )
    service._wait_for_state_acknowledgement = lambda expected: (
        acknowledgements.append(expected) or {}
    )
    service._wait_for_replacement_runtime = (
        lambda *, replacement_pid, log_offset: replacement_checks.append(
            (replacement_pid, log_offset)
        )
        or {}
    )
    manager.on_stop = lambda: (
        service.control_store.read()["state"] == "PAUSED"
        or pytest.fail("attached restart stopped before persisting PAUSED")
    )
    manager.on_start = lambda: (
        manager.startup_gate_policy == "next_run"
        and service.control_store.read()["state"] == "PAUSED"
        or pytest.fail("replacement did not cross its boundary paused/attached")
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = service.apply_process_action({"action": "restart_attached"})

    assert manager.calls == [
        "stop",
        "set_startup_gate_policy:next_run",
        "start",
        "persist_startup_gate_policy:auto_validate",
    ]
    assert len(pause_checks) == 1
    assert pause_checks[0][0] == manager.pid - 1
    assert pause_checks[0][1] > 0
    assert acknowledgements == ["RUNNING"]
    assert len(replacement_checks) == 1
    assert replacement_checks[0][0] == manager.pid
    assert replacement_checks[0][1] > 0
    assert manager.startup_gate_policy == "auto_validate"
    assert service.control_store.read()["state"] == "RUNNING"
    assert response["request"] == {
        "accepted": True,
        "action": "restart_attached",
        "disposition": "active_battle_reloaded",
        "previous_pid": manager.pid - 1,
        "replacement_pid": manager.pid,
        "restored_state": "RUNNING",
        "startup_gate_policy": "next_run",
    }


def test_attached_restart_failure_restores_policy_and_leaves_pause(tmp_path):
    manager = FakeManager(active=True, fail_start=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("RUNNING", source="test")
    lock_path = _write_running_runtime_evidence(tmp_path, pid=manager.pid)
    service._wait_for_attached_restart_pause = (
        lambda *, previous_pid, log_offset: {}
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ControlSurfaceRequestError) as exc_info:
            service.apply_process_action({"action": "restart_attached"})

    assert exc_info.value.status == 503
    assert manager.calls == [
        "stop",
        "set_startup_gate_policy:next_run",
        "start",
        "set_startup_gate_policy:auto_validate",
    ]
    assert manager.startup_gate_policy == "auto_validate"
    assert service.control_store.read()["state"] == "PAUSED"


def test_attached_restart_restores_unexpired_timed_pause(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    resume_at = time.time() + 600
    service.control_store.set_state(
        "PAUSED",
        resume_at=resume_at,
        source="test",
    )
    lock_path = _write_running_runtime_evidence(tmp_path, pid=manager.pid)
    service._wait_for_attached_restart_pause = (
        lambda *, previous_pid, log_offset: {}
    )
    service._wait_for_replacement_runtime = (
        lambda *, replacement_pid, log_offset: {}
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = service.apply_process_action({"action": "restart_attached"})

    control = service.control_store.read()
    assert control["state"] == "PAUSED"
    assert control["resume_at"] == resume_at
    assert response["request"]["restored_state"] == "PAUSED"


def test_attached_restart_rejects_missing_matching_runtime_owner(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("RUNNING", source="test")
    _write_running_runtime_evidence(tmp_path, pid=manager.pid)

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_process_action({"action": "restart_attached"})

    assert exc_info.value.status == 409
    assert "ADB lock owner" in str(exc_info.value)
    assert manager.calls == []


def test_attached_restart_readiness_verifies_fresh_owner_control_and_status(
    tmp_path,
):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("PAUSED", source="attached-restart")
    lock_path = _write_running_runtime_evidence(tmp_path, pid=manager.pid)
    timestamp = datetime.now().astimezone().replace(microsecond=0)
    timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.write_text(
        f"[INFO {timestamp_text}] [CTRL] State set to PAUSED via control file\n"
        f"[STATUS {timestamp_text}] State=RUNNING/PAUSED | "
        "Wave=123 | Coins/min=1.0T\n",
        encoding="utf-8",
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = service._wait_for_attached_restart_pause(
            previous_pid=manager.pid,
            log_offset=0,
        )

    assert status["observation"]["state"] == "RUNNING"
    assert status["runtime"]["instances"][0]["pid"] == manager.pid


def test_replacement_readiness_verifies_attached_policy_and_first_status(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("PAUSED", source="attached-restart")
    lock_path = _write_running_runtime_evidence(tmp_path, pid=manager.pid)
    timestamp = datetime.now().astimezone().replace(microsecond=0)
    timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.write_text(
        f"[INFO {timestamp_text}] [RUN_INIT] Startup gate policy=next_run\n"
        f"[INFO {timestamp_text}] [CTRL] State set to PAUSED via control file\n"
        f"[STATUS {timestamp_text}] State=RUNNING/PAUSED | "
        "Wave=123 | Coins/min=1.0T\n",
        encoding="utf-8",
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = service._wait_for_replacement_runtime(
            replacement_pid=manager.pid,
            log_offset=0,
        )

    assert status["process_service"]["main_pid"] == manager.pid
    assert status["acknowledgements"]["state"]["value"] == "PAUSED"


def test_attached_restart_pause_rejects_fresh_nonrunning_observation(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("PAUSED", source="attached-restart")
    lock_path = _write_running_runtime_evidence(tmp_path, pid=manager.pid)
    timestamp = datetime.now().astimezone().replace(microsecond=0)
    timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.write_text(
        f"[INFO {timestamp_text}] [CTRL] State set to PAUSED via control file\n"
        f"[STATUS {timestamp_text}] State=HOME_SCREEN/PAUSED | "
        "Wave=— | Coins/min=—\n",
        encoding="utf-8",
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AutomationProcessError, match="HOME_SCREEN"):
            service._wait_for_attached_restart_pause(
                previous_pid=manager.pid,
                log_offset=0,
            )


def test_start_failure_falls_back_to_stopped_intent(tmp_path):
    manager = FakeManager(fail_start=True)
    service = _service(tmp_path, manager)

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_process_action({"action": "start", "run_state": "RUNNING"})

    assert exc_info.value.status == 503
    assert service.control_store.read()["state"] == "STOPPED"


def test_control_surface_configures_adb_port_only_while_stopped(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)

    response = service.apply_process_action(
        {"action": "set_adb_port", "adb_port": 5565}
    )

    assert manager.calls == ["set_adb_port:5565"]
    assert response["process_service"]["adb_target"] == "localhost:5565"
    assert response["request"] == {
        "accepted": True,
        "action": "set_adb_port",
        "adb_port": 5565,
    }

    manager.active = True
    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_process_action(
            {"action": "set_adb_port", "adb_port": 5575}
        )
    assert exc_info.value.status == 409


def test_control_surface_requests_live_adb_handoff_only_after_pause_ack(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.apply_control({"action": "pause"})
    requested_at = service.control_store.read()["state_updated_at"]
    timestamp = datetime.fromisoformat(requested_at).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    with service.action_log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file\n"
        )

    response = service.apply_process_action(
        {"action": "set_adb_port", "adb_port": 5575}
    )

    assert manager.calls == ["persist_adb_port:5575"]
    assert service.control_store.read()["adb_port"] == 5575
    assert response["request"]["adb_port"] == 5575


def test_control_surface_rejects_live_handoff_under_timed_pause(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.apply_control({"action": "pause", "minutes": 15})
    requested_at = service.control_store.read()["state_updated_at"]
    timestamp = datetime.fromisoformat(requested_at).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    with service.action_log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file\n"
        )

    with pytest.raises(ControlSurfaceRequestError, match="Indefinitely pause"):
        service.apply_process_action(
            {"action": "set_adb_port", "adb_port": 5575}
        )

    assert manager.calls == []


def test_control_surface_saves_or_queues_strategy_by_process_state(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)

    response = service.apply_process_action(
        {"action": "set_strategy", "strategy": "tournament"}
    )

    assert manager.calls == ["set_strategy:tournament"]
    assert response["process_service"]["strategy"] == "tournament"
    assert response["request"] == {
        "accepted": True,
        "action": "set_strategy",
        "strategy": "tournament",
        "disposition": "saved",
    }
    assert service.control_store.status()["strategy"] == "tournament"

    manager.active = True
    response = service.apply_process_action(
        {"action": "set_strategy", "strategy": "farm_t18"}
    )

    assert manager.calls[-1] == "persist_strategy:farm_t18"
    assert service.control_store.status()["strategy"] == "farm_t18"
    assert response["request"] == {
        "accepted": True,
        "action": "set_strategy",
        "strategy": "farm_t18",
        "disposition": "queued",
    }

    response = service.apply_process_action(
        {
            "action": "set_strategy",
            "strategy": "farm_t19",
            "apply_to_active_run": True,
        }
    )

    assert manager.calls[-1] == "persist_strategy:farm_t19"
    control = service.control_store.status()
    assert control["strategy"] == "farm_t19"
    assert control["strategy_apply_mode"] == "active_battle"
    assert response["request"] == {
        "accepted": True,
        "action": "set_strategy",
        "strategy": "farm_t19",
        "disposition": "active_battle_requested",
    }


def test_control_surface_rejects_active_battle_adoption_without_runtime(tmp_path):
    manager = FakeManager(active=False)

    with pytest.raises(ControlSurfaceRequestError, match="active automation"):
        _service(tmp_path, manager).apply_process_action(
            {
                "action": "set_strategy",
                "strategy": "farm_t18",
                "apply_to_active_run": True,
            }
        )

    assert manager.calls == []


@pytest.mark.parametrize("strategy", [None, "", "unknown", 123])
def test_control_surface_rejects_invalid_strategy(tmp_path, strategy):
    manager = FakeManager()
    with pytest.raises(ControlSurfaceRequestError):
        _service(tmp_path, manager).apply_process_action(
            {"action": "set_strategy", "strategy": strategy}
        )


def test_control_surface_rejects_adb_change_for_active_unmanaged_runtime(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    lock_path = tmp_path / "logs" / "automation-localhost_5565.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "target": "localhost:5565",
                "started_at": "2026-07-19T17:00:00-07:00",
            }
        ),
        encoding="utf-8",
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ControlSurfaceRequestError) as exc_info:
            service.apply_process_action(
                {"action": "set_adb_port", "adb_port": 5575}
            )

    assert exc_info.value.status == 409
    assert manager.calls == []


@pytest.mark.parametrize("port", [True, 0, 65536, "5565", 55.5])
def test_control_surface_rejects_invalid_adb_port(tmp_path, port):
    manager = FakeManager()
    with pytest.raises(ControlSurfaceRequestError):
        _service(tmp_path, manager).apply_process_action(
            {"action": "set_adb_port", "adb_port": port}
        )
    assert manager.calls == []


def test_process_action_requires_a_configured_manager(tmp_path):
    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        _service(tmp_path).apply_process_action({"action": "start"})
    assert exc_info.value.status == 503
