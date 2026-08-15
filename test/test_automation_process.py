from __future__ import annotations

import fcntl
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import stat
import threading
import time

import pytest

from core.action_authority import (
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
)
from core.automation_process import (
    AutomationProcessError,
    SystemdAutomationManager,
)
from core.app_setup import parse_args
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService
from tools.control_surface_server import _persistent_adb_target_provider


def _emulator_location(*, linux_port: int = 5555) -> dict[str, object]:
    return {
        "schema_version": 1,
        "host_id": "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686",
        "host_name": "WORKSTATION-B",
        "linux_adb_port": linux_port,
        "bluestacks_listener": {
            "adb_port": 5565,
            "instance_name": "Nougat32",
        },
    }


class FakeManager:
    def __init__(
        self,
        *,
        active: bool = False,
        fail_start: bool = False,
        adb_connection_owner_error: str | None = None,
    ) -> None:
        self.active = active
        self.fail_start = fail_start
        self.adb_port = 5555
        self.strategy = "farm"
        self.startup_gate_policy = "auto_validate"
        self.pid = os.getpid()
        self.calls: list[str] = []
        self.on_start = None
        self.on_stop = None
        self.adb_connection_owner_error = adb_connection_owner_error

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
            "adb_connection_owner": "control-surface",
            "adb_connection_owner_configured": (
                self.adb_connection_owner_error is None
            ),
            "adb_connection_owner_error": self.adb_connection_owner_error,
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

    def adb_connection_target(self):
        return f"localhost:{self.adb_port}"

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
        if policy not in {
            "operator",
            "auto",
            "auto_validate",
            "immediate",
            "next_run",
        }:
            raise AutomationProcessError("invalid startup gate policy")
        self.startup_gate_policy = policy
        return self.status()

    def persist_startup_gate_policy(self, policy):
        self.calls.append(f"persist_startup_gate_policy:{policy}")
        if policy not in {
            "operator",
            "auto",
            "auto_validate",
            "immediate",
            "next_run",
        }:
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


class FakePersistentAdbConnectionManager:
    def __init__(self) -> None:
        self.calls: list[tuple[object, bool]] = []
        self.target = None
        self.on_ensure = None

    def ensure_target(self, target, *, force=False):
        self.calls.append((target, force))
        if self.on_ensure:
            self.on_ensure()
        self.target = target
        return True

    def status(self):
        return {
            "owner": "control-surface",
            "target": self.target,
            "state": "device" if self.target else "unknown",
            "connected": True if self.target else None,
            "failures": 0,
            "warning_active": False,
            "retry_in_seconds": 0.0,
            "last_checked_at": None,
            "error": None,
        }


def _service(tmp_path, manager=None, adb_connection_manager=None):
    return ControlSurfaceService(
        repository_root=tmp_path,
        process_manager=manager,
        adb_connection_manager=adb_connection_manager,
    )


def _write_running_runtime_evidence(
    tmp_path,
    *,
    pid: int,
    control: dict[str, object],
    primary_state: str = "RUNNING",
    startup_gate_policy: str = "next_run",
) -> Path:
    timestamp = datetime.now().astimezone().replace(microsecond=0)
    runtime_id = f"runtime-{pid}"
    target_generation = 1
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
                "runtime_id": runtime_id,
                "target": "localhost:5555",
                "target_generation": target_generation,
                "state": "held",
                "started_at": timestamp.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=control.get("state") == "PAUSED",
        active_battle=primary_state == "RUNNING",
        battle_scope="attached-restart",
        primary_state=primary_state,
    )
    acknowledgements: dict[str, object] = {
        "schema_version": 1,
        "runtime_id": runtime_id,
    }
    request_id = str(control.get("state_request_id") or "")
    if request_id:
        acknowledgements["state"] = {
            "value": str(control.get("state") or ""),
            "request_id": request_id,
            "acknowledged_at": timestamp.astimezone(timezone.utc).isoformat(),
        }
    publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner={
            "runtime_id": runtime_id,
            "pid": pid,
            "adb_target": "localhost:5555",
            "target_generation": target_generation,
        },
    )
    assert publisher.publish(
        authority.snapshot(now=timestamp.timestamp()),
        runtime_active=True,
        now=timestamp.timestamp(),
        acknowledgements=acknowledgements,
        control_model={
            "schema_version": 1,
            "startup_gate_policy": startup_gate_policy,
            "observation": {
                "schema_version": 1,
                "observation_id": f"{runtime_id}:1",
                "observed_at": timestamp.isoformat(),
                "primary_state": primary_state,
                "home_battle_control": "UNKNOWN",
                "game_state": (
                    "active_battle" if primary_state == "RUNNING" else "unknown"
                ),
                "active_battle": primary_state == "RUNNING",
                "activity_scope_run_id": "attached-restart",
                "target_generation": target_generation,
                "active_round_identity_fingerprint": (
                    "a" * 64 if primary_state == "RUNNING" else None
                ),
            },
            "battle_lifecycle": {
                "active_battle_adopted": primary_state == "RUNNING",
            },
            "strategy_scope": {
                "startup_default": "farm_t18",
                "active_battle": (
                    "farm_t18" if primary_state == "RUNNING" else None
                ),
                "pending_next_boundary": None,
                "pending_active_battle": None,
            },
        },
    )
    return lock_path


def _write_pending_restart_handoff(
    service: ControlSurfaceService,
) -> dict[str, object]:
    timestamp = datetime.now().astimezone().replace(microsecond=0)
    source = {
        "schema_version": 1,
        "runtime_id": "stopped-runtime",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "observation_id": "stopped-runtime:1",
        "observed_at": timestamp.isoformat(),
        "primary_state": "RUNNING",
        "home_battle_control": "UNKNOWN",
        "game_state": "active_battle",
        "active_battle": True,
        "activity_scope_run_id": "owned-battle",
        "target_generation": 1,
        "active_round_identity_fingerprint": "a" * 64,
    }
    service.control_store.set_state_and_interrupt_operator_workflows(
        "STOPPED",
        "replace runtime",
        source="test-stop",
        restart_handoff_evidence=source,
    )
    return source


def test_runtime_strategy_defaults_from_managed_environment(monkeypatch):
    monkeypatch.setenv("THETOWER_STRATEGY", "tournament")

    assert parse_args([]).strategy == "tournament"


def test_startup_gate_policy_defaults_from_managed_environment(monkeypatch):
    monkeypatch.setenv("THETOWER_STARTUP_GATES", "next_run")

    assert parse_args([]).startup_gates == "next_run"


def test_startup_gate_policy_defaults_to_automatic_attached_validation(monkeypatch):
    monkeypatch.delenv("THETOWER_STARTUP_GATES", raising=False)

    assert parse_args([]).startup_gates == "auto_validate"


def test_runtime_adb_connection_owner_defaults_from_managed_environment(monkeypatch):
    monkeypatch.setenv("THETOWER_ADB_CONNECTION_OWNER", "control-surface")

    assert parse_args([]).adb_connection_owner == "control-surface"


def test_control_service_target_provider_requires_the_installed_owner():
    provider = _persistent_adb_target_provider(FakeManager())
    assert provider() == "localhost:5555"

    outdated_manager = FakeManager(
        adb_connection_owner_error="runtime still owns reconnects"
    )
    provider = _persistent_adb_target_provider(outdated_manager)
    with pytest.raises(AutomationProcessError, match="runtime still owns"):
        provider()

    outdated_manager.adb_connection_owner_error = None
    assert provider() == "localhost:5555"


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
            "Environment=PYTHONUNBUFFERED=1\n"
            "Environment=THETOWER_ADB_CONNECTION_OWNER=control-surface\n"
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
    assert status["adb_connection_owner_configured"] is True


def test_systemd_manager_verifies_control_surface_connection_ownership(tmp_path):
    environment_file = tmp_path / "automation-adb.env"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
            "UnitFileState=enabled\nMainPID=0\nExecMainStatus=0\n"
            "Environment=PYTHONUNBUFFERED=1 "
            "THETOWER_ADB_CONNECTION_OWNER=control-surface\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        runner=runner,
        adb_environment_file=environment_file,
    )

    status = manager.status()

    assert status["adb_connection_owner"] == "control-surface"
    assert status["adb_connection_owner_configured"] is True
    assert status["adb_connection_owner_error"] is None


def test_systemd_manager_reports_outdated_runtime_connection_ownership(tmp_path):
    environment_file = tmp_path / "automation-adb.env"

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

    status = manager.status()

    assert status["adb_connection_owner"] == "runtime"
    assert status["adb_connection_owner_configured"] is False
    assert "reinstall deploy/systemd" in status["adb_connection_owner_error"]


def test_checked_in_systemd_unit_loads_managed_automation_environment():
    repository_root = Path(__file__).resolve().parents[1]
    unit = (
        repository_root / "deploy" / "systemd" / "thetower-automation.service"
    ).read_text(encoding="utf-8")

    assert "EnvironmentFile=-%h/.config/thetower/automation-adb.env" in unit
    assert (
        "EnvironmentFile=-%h/.config/thetower/player-save-audit.env" in unit
    )
    assert "Environment=THETOWER_ADB_CONNECTION_OWNER=control-surface" in unit


@pytest.mark.parametrize(
    "name",
    ["", "automation", "../evil.service", "one.service;reboot", "/x.service"],
)
def test_systemd_manager_rejects_non_unit_names(name):
    with pytest.raises(ValueError):
        SystemdAutomationManager(name)


def test_start_automation_crosses_new_process_boundary_paused(tmp_path):
    manager = FakeManager()
    connection_manager = FakePersistentAdbConnectionManager()
    service = _service(tmp_path, manager, connection_manager)
    manager.on_start = lambda: (
        service.control_store.read()["state"] == "PAUSED"
        or pytest.fail("service was not paused before process start")
    )

    response = service.apply_process_action({"action": "start"})

    assert manager.calls == ["set_startup_gate_policy:operator", "start"]
    assert connection_manager.calls == [("localhost:5555", True)]
    assert service.control_store.read()["state"] == "PAUSED"
    assert response["process_service"]["active"]
    assert response["request"] == {
        "accepted": True,
        "action": "start",
        "disposition": "completed",
        "action_authority": "paused",
    }
    assert response["adb_connection"]["state"] == "device"


def test_repeated_process_start_and_stop_are_visible_no_ops(tmp_path):
    live_manager = FakeManager(active=True)
    live_service = _service(tmp_path / "live", live_manager)

    started = live_service.apply_process_action({"action": "start"})

    assert started["request"]["disposition"] == "no_op"
    assert started["request"]["action_authority"] == "unknown"
    assert live_manager.calls == []

    stopped_manager = FakeManager(active=False)
    stopped_service = _service(tmp_path / "stopped", stopped_manager)

    stopped = stopped_service.apply_process_action({"action": "stop"})

    assert stopped["request"]["disposition"] == "no_op"
    assert stopped_manager.calls == []


def test_start_rejects_an_installed_runtime_that_still_owns_reconnects(tmp_path):
    manager = FakeManager(
        adb_connection_owner_error="installed runtime still owns ADB reconnects"
    )
    connection_manager = FakePersistentAdbConnectionManager()

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        _service(tmp_path, manager, connection_manager).apply_process_action(
            {"action": "start"}
        )

    assert exc_info.value.status == 503
    assert exc_info.value.code == "persistent_adb_owner_not_configured"
    assert manager.calls == []
    assert connection_manager.calls == []


def test_start_rejects_obsolete_authority_and_attachment_parameters(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_process_action(
            {
                "action": "start",
                "run_state": "RUNNING",
                "startup_gate_policy": "next_run",
            }
        )

    assert exc_info.value.status == 409
    assert exc_info.value.code == "obsolete_start_parameters"
    assert manager.calls == []


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
            "strategy": "farm_t19",
        }
    )

    assert manager.calls == [
        "set_startup_gate_policy:operator",
        "set_strategy:farm_t19",
        "start",
    ]
    assert response["process_service"]["strategy"] == "farm_t19"
    assert response["request"] == {
        "accepted": True,
        "action": "start",
        "disposition": "completed",
        "action_authority": "paused",
        "strategy": "farm_t19",
    }


def test_start_with_saved_tournament_does_not_authorize_a_battle(tmp_path):
    manager = FakeManager()
    manager.strategy = "tournament"
    service = _service(tmp_path, manager)

    service.apply_process_action({"action": "start"})
    first = service.control_store.status()["exclusive_validation"]
    assert first["current_request_id"] is None
    assert first["receipts"] == {}

    service.apply_process_action({"action": "stop"})
    service.apply_process_action({"action": "start"})
    second = service.control_store.status()["exclusive_validation"]
    assert second["current_request_id"] is None
    assert second["receipts"] == {}
    assert manager.calls == [
        "set_startup_gate_policy:operator",
        "start",
        "stop",
        "set_startup_gate_policy:operator",
        "start",
    ]


@pytest.mark.parametrize("policy", ["", "later", "auto_validate", 1, True])
def test_start_rejects_any_client_selected_startup_gate_policy(tmp_path, policy):
    manager = FakeManager()

    with pytest.raises(ControlSurfaceRequestError):
        _service(tmp_path, manager).apply_process_action(
            {
                "action": "start",
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
                "strategy": "farm_t19",
            }
        )

    assert exc_info.value.status == 409
    assert manager.calls == []


def test_start_paused_and_complete_stop_preserve_safe_ordering(tmp_path):
    manager = FakeManager()
    connection_manager = FakePersistentAdbConnectionManager()
    service = _service(tmp_path, manager, connection_manager)
    service.apply_process_action({"action": "start"})
    assert service.control_store.read()["state"] == "PAUSED"
    connection_manager.calls.clear()

    manager.on_stop = lambda: (
        service.control_store.read()["state"] == "STOPPED"
        or pytest.fail("STOPPED was not persisted before systemd stop")
    )
    connection_manager.on_ensure = lambda: (
        not manager.active
        or pytest.fail("ADB registration refreshed before systemd stopped")
    )
    response = service.apply_process_action({"action": "stop"})

    assert manager.calls == [
        "set_startup_gate_policy:operator",
        "start",
        "stop",
    ]
    assert connection_manager.calls == [("localhost:5555", True)]
    assert service.control_store.read()["state"] == "STOPPED"
    assert response["process_service"]["active"] is False


def test_process_stop_retains_any_exact_owned_battle_for_next_start(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    control = service.control_store.set_state("RUNNING", source="test")
    lock_path = _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=control,
        startup_gate_policy="operator",
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = service.apply_process_action({"action": "stop"})

    handoff = service.control_store.status()["process_restart_handoff"]
    assert handoff["status"] == "pending"
    assert handoff["expected_active_round_identity_fingerprint"] == "a" * 64
    assert handoff["source_evidence"]["game_state"] == "active_battle"
    assert response["request"]["reattach_on_start"] is True
    assert response["request"]["restart_handoff_id"] == handoff["handoff_id"]
    assert manager.calls == ["stop"]


def test_process_start_uses_pending_handoff_then_restores_operator_policy(
    tmp_path,
    monkeypatch,
):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    source = _write_pending_restart_handoff(service)

    monkeypatch.setattr(
        service,
        "_wait_for_replacement_runtime",
        lambda **_kwargs: {},
    )

    def complete_handoff(*, replacement_pid, handoff_id, expected_identity):
        replacement = {
            **source,
            "runtime_id": "replacement-runtime",
            "pid": replacement_pid,
            "observation_id": "replacement-runtime:1",
        }
        workflow = service.control_store.request_battle_workflow(
            "attach_battle",
            evidence=replacement,
            process_restart_handoff_id=handoff_id,
            source="test-replacement",
        )
        service.control_store.finish_process_restart_handoff(
            handoff_id,
            "completed",
            reason="same battle force-validated",
            workflow_id=workflow["request_id"],
            actual_active_round_identity=expected_identity,
        )
        return {}

    monkeypatch.setattr(
        service,
        "_wait_for_same_battle_reattachment",
        complete_handoff,
    )

    response = service.apply_process_action(
        {"action": "start", "strategy": "tournament"}
    )

    assert manager.calls == [
        "set_startup_gate_policy:next_run",
        "set_strategy:tournament",
        "start",
        "persist_startup_gate_policy:operator",
    ]
    assert manager.startup_gate_policy == "operator"
    assert service.control_store.status()["state"] == "RUNNING"
    assert response["request"]["disposition"] == "same_battle_reattached"
    assert response["request"]["battle_identity"] == "a" * 64
    assert response["request"]["strategy"] == "tournament"


def test_failed_same_battle_start_leaves_replacement_paused(
    tmp_path,
    monkeypatch,
):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    _write_pending_restart_handoff(service)
    monkeypatch.setattr(
        service,
        "_wait_for_replacement_runtime",
        lambda **_kwargs: {},
    )

    def reject_different_battle(**_kwargs):
        raise AutomationProcessError("replacement proved a different battle")

    monkeypatch.setattr(
        service,
        "_wait_for_same_battle_reattachment",
        reject_different_battle,
    )

    with pytest.raises(ControlSurfaceRequestError, match="different battle"):
        service.apply_process_action({"action": "start"})

    assert manager.active is True
    assert manager.startup_gate_policy == "operator"
    assert service.control_store.status()["state"] == "PAUSED"


def test_changed_target_cancels_handoff_and_starts_paused(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)
    _write_pending_restart_handoff(service)
    manager.adb_port = 5565

    response = service.apply_process_action({"action": "start"})

    assert manager.calls == ["set_startup_gate_policy:operator", "start"]
    assert response["request"]["disposition"] == "completed"
    assert response["request"]["action_authority"] == "paused"
    handoff = service.control_store.status()["process_restart_handoff"]
    assert handoff["status"] == "cancelled"
    assert "different ADB target" in handoff["reason"]


def test_process_stop_linearizes_after_an_inflight_enable(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("PAUSED", source="test")
    enable_snapshot_ready = threading.Event()
    release_enable = threading.Event()
    original_status = service.status
    enable_status_calls = 0
    failures = []

    def blocking_status(*args, **kwargs):
        nonlocal enable_status_calls
        result = original_status(*args, **kwargs)
        if threading.current_thread().name == "enable-request":
            enable_status_calls += 1
            if enable_status_calls == 1:
                enable_snapshot_ready.set()
                assert release_enable.wait(timeout=2)
        return result

    service.status = blocking_status

    def enable():
        try:
            service.apply_control({"action": "enable"})
        except Exception as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    def stop():
        try:
            service.apply_process_action({"action": "stop"})
        except Exception as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    enable_thread = threading.Thread(target=enable, name="enable-request")
    stop_thread = threading.Thread(target=stop, name="stop-request")
    enable_thread.start()
    assert enable_snapshot_ready.wait(timeout=2)
    stop_thread.start()
    release_enable.set()
    enable_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not enable_thread.is_alive()
    assert not stop_thread.is_alive()
    assert failures == []
    assert service.control_store.status()["state"] == "STOPPED"
    assert manager.active is False


def test_take_manual_cannot_weaken_stop_while_process_exit_is_pending(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    running = service.control_store.set_state("RUNNING", source="test")
    _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=running,
    )
    stop_entered = threading.Event()
    release_stop = threading.Event()
    manual_completed = threading.Event()
    stop_failures = []
    manual_failures = []

    def block_stop():
        stop_entered.set()
        assert release_stop.wait(timeout=2)

    manager.on_stop = block_stop

    def stop():
        try:
            service.apply_process_action({"action": "stop"})
        except Exception as exc:  # pragma: no cover - surfaced below
            stop_failures.append(exc)

    def take_manual():
        try:
            service.apply_control({"action": "take_manual_control"})
        except Exception as exc:
            manual_failures.append(exc)
        finally:
            manual_completed.set()

    stop_thread = threading.Thread(target=stop)
    manual_thread = threading.Thread(target=take_manual)
    stop_thread.start()
    assert stop_entered.wait(timeout=2)
    assert service.control_store.status()["state"] == "STOPPED"
    manual_thread.start()
    assert not manual_completed.wait(timeout=0.05)

    release_stop.set()
    stop_thread.join(timeout=2)
    manual_thread.join(timeout=2)

    assert not stop_thread.is_alive()
    assert not manual_thread.is_alive()
    assert stop_failures == []
    assert len(manual_failures) == 1
    assert isinstance(manual_failures[0], ControlSurfaceRequestError)
    assert manual_failures[0].code == "process_stopping"
    assert service.control_store.status()["state"] == "STOPPED"
    assert manager.active is False


def test_one_step_attached_restart_routes_to_durable_stop_start(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.control_store.set_state("RUNNING", source="test")

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_process_action({"action": "restart_attached"})

    assert exc_info.value.status == 409
    assert exc_info.value.code == "stop_start_required"
    assert "automatically reattached" in str(exc_info.value)
    assert manager.calls == []


def test_attached_restart_readiness_verifies_fresh_owner_control_and_status(
    tmp_path,
):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    control = service.control_store.set_state(
        "PAUSED",
        source="attached-restart",
    )
    lock_path = _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=control,
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = service._wait_for_attached_restart_pause(
            previous_pid=manager.pid,
        )

    assert status["observation"]["state"] == "RUNNING"
    assert status["runtime"]["instances"][0]["pid"] == manager.pid


def test_replacement_readiness_verifies_attached_policy_and_first_status(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    control = service.control_store.set_state(
        "PAUSED",
        source="attached-restart",
    )
    lock_path = _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=control,
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = service._wait_for_replacement_runtime(
            replacement_pid=manager.pid,
        )

    assert status["process_service"]["main_pid"] == manager.pid
    assert status["acknowledgements"]["state"]["value"] == "PAUSED"


def test_attached_restart_pause_rejects_fresh_nonrunning_observation(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    control = service.control_store.set_state(
        "PAUSED",
        source="attached-restart",
    )
    lock_path = _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=control,
        primary_state="HOME_SCREEN",
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AutomationProcessError, match="HOME_SCREEN"):
            service._wait_for_attached_restart_pause(
                previous_pid=manager.pid,
            )


def test_start_failure_falls_back_to_stopped_intent(tmp_path):
    manager = FakeManager(fail_start=True)
    service = _service(tmp_path, manager)

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_process_action({"action": "start"})

    assert exc_info.value.status == 503
    assert service.control_store.read()["state"] == "STOPPED"


def test_control_surface_configures_adb_port_only_while_stopped(tmp_path):
    manager = FakeManager()
    connection_manager = FakePersistentAdbConnectionManager()
    service = _service(tmp_path, manager, connection_manager)

    response = service.apply_process_action(
        {"action": "set_adb_port", "adb_port": 5565}
    )

    assert manager.calls == ["set_adb_port:5565"]
    assert connection_manager.calls == [("localhost:5565", True)]
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
    connection_manager = FakePersistentAdbConnectionManager()
    service = _service(tmp_path, manager, connection_manager)
    service.apply_control({"action": "pause"})
    control = service.control_store.read()
    lock_path = _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=control,
    )
    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = service.apply_process_action(
            {"action": "set_adb_port", "adb_port": 5575}
        )

    assert manager.calls == ["persist_adb_port:5575"]
    assert connection_manager.calls == [("localhost:5575", True)]
    assert service.control_store.read()["adb_port"] == 5575
    assert response["request"]["adb_port"] == 5575


def test_control_surface_records_explicit_windows_emulator_host(tmp_path):
    manager = FakeManager()
    service = _service(tmp_path, manager)

    response = service.apply_process_action(
        {
            "action": "set_adb_port",
            "adb_port": 5555,
            "emulator_location": _emulator_location(),
        }
    )

    assert manager.calls == ["set_adb_port:5555"]
    assert response["request"]["emulator_location"]["host_name"] == (
        "WORKSTATION-B"
    )
    control = service.control_store.status()
    assert control["emulator_location"]["host_id"] == (
        "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686"
    )
    assert control["emulator_location"]["request_id"] == (
        control["adb_port_request_id"]
    )


def test_control_surface_live_same_port_host_handoff_requires_pause_ack(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    request = {
        "action": "set_adb_port",
        "adb_port": 5555,
        "emulator_location": _emulator_location(),
    }

    with pytest.raises(ControlSurfaceRequestError, match="Indefinitely pause"):
        service.apply_process_action(request)

    service.apply_control({"action": "pause"})
    control = service.control_store.read()
    lock_path = _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=control,
    )
    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = service.apply_process_action(request)

    assert manager.calls == ["persist_adb_port:5555"]
    assert response["request"]["emulator_location"]["host_name"] == (
        "WORKSTATION-B"
    )


def test_control_surface_rejects_mismatched_emulator_host_port(tmp_path):
    with pytest.raises(
        ControlSurfaceRequestError,
        match="linux_adb_port must match adb_port",
    ):
        _service(tmp_path, FakeManager()).apply_process_action(
            {
                "action": "set_adb_port",
                "adb_port": 5575,
                "emulator_location": _emulator_location(linux_port=5555),
            }
        )


def test_control_surface_rejects_live_handoff_under_timed_pause(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    service.apply_control({"action": "pause", "minutes": 15})

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

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_process_action(
            {
                "action": "set_strategy",
                "strategy": "farm_t19",
                "apply_to_active_run": True,
            }
        )

    assert exc_info.value.code == "fresh_observation_unavailable"
    assert manager.calls[-1] == "persist_strategy:farm_t18"
    control = service.control_store.status()
    assert control["strategy"] == "farm_t18"
    assert control["strategy_apply_mode"] == "next_boundary"


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


def test_control_surface_binds_active_strategy_to_forced_battle_identity(tmp_path):
    manager = FakeManager(active=True)
    service = _service(tmp_path, manager)
    control = service.control_store.set_state("RUNNING", source="test")
    lock_path = _write_running_runtime_evidence(
        tmp_path,
        pid=manager.pid,
        control=control,
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = service.apply_process_action(
            {
                "action": "set_strategy",
                "strategy": "farm_t18",
                "apply_to_active_run": True,
            }
        )

    assert response["request"]["disposition"] == "active_battle_requested"
    assert service.control_store.status()[
        "strategy_active_battle_identity"
    ] == "a" * 64


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
