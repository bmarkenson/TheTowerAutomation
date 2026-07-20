"""Named systemd user-service control for the automation process."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from typing import Any, Callable, Optional, Sequence

from core.app_setup import (
    ADB_PORT_ENVIRONMENT_VARIABLE,
    CONFIGURABLE_STRATEGIES,
    DEFAULT_ADB_PORT,
    DEFAULT_STARTUP_GATE_POLICY,
    DEFAULT_STRATEGY,
    STARTUP_GATE_POLICIES,
    STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE,
    STRATEGY_ENVIRONMENT_VARIABLE,
)


DEFAULT_AUTOMATION_SERVICE = "thetower-automation.service"
DEFAULT_ADB_ENVIRONMENT_FILE = (
    Path.home() / ".config" / "thetower" / "automation-adb.env"
)
_SERVICE_NAME_RE = re.compile(r"[A-Za-z0-9_.@-]+\.service")
_SHOW_PROPERTIES = (
    "LoadState,ActiveState,SubState,UnitFileState,MainPID,ExecMainStatus,"
    "EnvironmentFiles"
)
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class AutomationProcessError(RuntimeError):
    """Raised when the named service cannot complete a lifecycle operation."""


class SystemdAutomationManager:
    """Start and stop one fixed systemd user service without PID authority."""

    def __init__(
        self,
        service_name: str = DEFAULT_AUTOMATION_SERVICE,
        *,
        adb_environment_file: Path | str | None = DEFAULT_ADB_ENVIRONMENT_FILE,
        runner: CommandRunner = subprocess.run,
        timeout_seconds: float = 15.0,
    ) -> None:
        normalized = str(service_name).strip()
        if not _SERVICE_NAME_RE.fullmatch(normalized):
            raise ValueError(
                "automation service must be a simple .service unit name"
            )
        self.service_name = normalized
        self.adb_environment_file = (
            Path(adb_environment_file).expanduser()
            if adb_environment_file is not None
            else None
        )
        self._runner = runner
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._operation_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        """Return structured systemd evidence without changing service state."""

        command = [
            "systemctl",
            "--user",
            "show",
            "--no-page",
            f"--property={_SHOW_PROPERTIES}",
            self.service_name,
        ]
        try:
            result = self._execute(command)
        except AutomationProcessError as exc:
            status = self._unavailable_status(str(exc))
            status.update(self._automation_configuration_status())
            return status

        properties = _parse_properties(result.stdout)
        load_state = properties.get("LoadState")
        active_state = properties.get("ActiveState")
        main_pid = _positive_int(properties.get("MainPID"))
        available = result.returncode == 0 and load_state not in {None, "not-found"}
        error = None
        if not available:
            error = (result.stderr or result.stdout or "systemd unit unavailable").strip()
        status = {
            "manager": "systemd-user",
            "service": self.service_name,
            "available": available,
            "load_state": load_state,
            "active_state": active_state,
            "sub_state": properties.get("SubState"),
            "unit_file_state": properties.get("UnitFileState"),
            "main_pid": main_pid,
            "exit_status": _integer(properties.get("ExecMainStatus")),
            "active": active_state in {"active", "activating"},
            "error": error,
        }
        status.update(
            self._automation_configuration_status(
                properties.get("EnvironmentFiles"),
                verify_service_configuration=available,
            )
        )
        return status

    def set_adb_port(self, port: object) -> dict[str, Any]:
        """Persist the ADB port for the next start of an inactive service."""

        return self._persist_adb_port(port, require_inactive=True)

    def persist_adb_port(self, port: object) -> dict[str, Any]:
        """Persist a port that a paused live runtime will also adopt."""

        return self._persist_adb_port(port, require_inactive=False)

    def _persist_adb_port(
        self,
        port: object,
        *,
        require_inactive: bool,
    ) -> dict[str, Any]:
        if isinstance(port, bool) or not isinstance(port, int):
            raise AutomationProcessError("ADB port must be an integer")
        if not 1 <= port <= 65535:
            raise AutomationProcessError("ADB port must be between 1 and 65535")

        with self._operation_lock:
            before = self.status()
            if not before["available"]:
                raise AutomationProcessError(
                    f"Cannot configure ADB port while {self.service_name} is unavailable"
                )
            if require_inactive and before["active"]:
                raise AutomationProcessError(
                    "Completely stop automation before changing the ADB port"
                )
            if self.adb_environment_file is None:
                raise AutomationProcessError(
                    "ADB port persistence is not configured for this service manager"
                )
            strategy = str(before.get("strategy") or DEFAULT_STRATEGY)
            if before.get("strategy_error"):
                raise AutomationProcessError(str(before["strategy_error"]))
            startup_gate_policy = str(
                before.get("startup_gate_policy") or DEFAULT_STARTUP_GATE_POLICY
            )
            if before.get("startup_gate_policy_error"):
                raise AutomationProcessError(
                    str(before["startup_gate_policy_error"])
                )
            self._write_automation_environment_file(
                port,
                strategy,
                startup_gate_policy,
            )
            after = self.status()
            if after.get("adb_port") != port or after.get("adb_port_error"):
                raise AutomationProcessError(
                    after.get("adb_port_error")
                    or "The persisted ADB port could not be verified"
                )
            return after

    def set_strategy(self, strategy: object) -> dict[str, Any]:
        """Persist a validated strategy for the next inactive-service start."""

        normalized = str(strategy).strip().lower()
        if normalized not in CONFIGURABLE_STRATEGIES:
            raise AutomationProcessError(
                "Strategy must be one of: " + ", ".join(CONFIGURABLE_STRATEGIES)
            )

        with self._operation_lock:
            before = self.status()
            if not before["available"]:
                raise AutomationProcessError(
                    f"Cannot configure strategy while {self.service_name} is unavailable"
                )
            if before["active"]:
                raise AutomationProcessError(
                    "Completely stop automation before changing the strategy"
                )
            if self.adb_environment_file is None:
                raise AutomationProcessError(
                    "Strategy persistence is not configured for this service manager"
                )
            if before.get("adb_port_error"):
                raise AutomationProcessError(str(before["adb_port_error"]))
            if before.get("startup_gate_policy_error"):
                raise AutomationProcessError(
                    str(before["startup_gate_policy_error"])
                )
            self._write_automation_environment_file(
                int(before.get("adb_port") or DEFAULT_ADB_PORT),
                normalized,
                str(
                    before.get("startup_gate_policy")
                    or DEFAULT_STARTUP_GATE_POLICY
                ),
            )
            after = self.status()
            if after.get("strategy") != normalized or after.get("strategy_error"):
                raise AutomationProcessError(
                    after.get("strategy_error")
                    or "The persisted strategy could not be verified"
                )
            return after

    def set_startup_gate_policy(self, policy: object) -> dict[str, Any]:
        """Persist how the next process treats its first observed battle."""

        normalized = str(policy).strip().lower()
        if normalized not in STARTUP_GATE_POLICIES:
            raise AutomationProcessError(
                "Startup gate policy must be one of: "
                + ", ".join(STARTUP_GATE_POLICIES)
            )

        with self._operation_lock:
            before = self.status()
            if not before["available"]:
                raise AutomationProcessError(
                    "Cannot configure startup gates while "
                    f"{self.service_name} is unavailable"
                )
            if before["active"]:
                raise AutomationProcessError(
                    "Completely stop automation before changing startup gates"
                )
            if self.adb_environment_file is None:
                raise AutomationProcessError(
                    "Startup-gate persistence is not configured for this "
                    "service manager"
                )
            if before.get("adb_port_error"):
                raise AutomationProcessError(str(before["adb_port_error"]))
            if before.get("strategy_error"):
                raise AutomationProcessError(str(before["strategy_error"]))
            self._write_automation_environment_file(
                int(before.get("adb_port") or DEFAULT_ADB_PORT),
                str(before.get("strategy") or DEFAULT_STRATEGY),
                normalized,
            )
            after = self.status()
            if (
                after.get("startup_gate_policy") != normalized
                or after.get("startup_gate_policy_error")
            ):
                raise AutomationProcessError(
                    after.get("startup_gate_policy_error")
                    or "The startup-gate policy could not be verified"
                )
            return after

    def start(self) -> dict[str, Any]:
        """Start the fixed service and require active systemd evidence."""

        with self._operation_lock:
            result = self._execute(
                ["systemctl", "--user", "start", self.service_name]
            )
            if result.returncode != 0:
                raise AutomationProcessError(
                    _command_error("start", result)
                )
            status = self.status()
            if not status["available"] or not status["active"]:
                raise AutomationProcessError(
                    f"systemd accepted start but {self.service_name} is not active: "
                    f"{status.get('active_state') or status.get('error') or 'unknown'}"
                )
            return status

    def stop(self) -> dict[str, Any]:
        """Stop the fixed service and require inactive systemd evidence."""

        with self._operation_lock:
            result = self._execute(
                ["systemctl", "--user", "stop", self.service_name]
            )
            if result.returncode != 0:
                raise AutomationProcessError(
                    _command_error("stop", result)
                )
            status = self.status()
            if status["available"] and status["active"]:
                raise AutomationProcessError(
                    f"systemd accepted stop but {self.service_name} remains "
                    f"{status.get('active_state') or 'active'}"
                )
            return status

    def _execute(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                list(command),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AutomationProcessError("systemctl is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise AutomationProcessError(
                f"systemctl timed out after {self.timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise AutomationProcessError(f"systemctl failed: {exc}") from exc

    def _automation_configuration_status(
        self,
        service_environment_files: Optional[str] = None,
        *,
        verify_service_configuration: bool = False,
    ) -> dict[str, Any]:
        path = self.adb_environment_file
        environment_files = str(service_environment_files or "").strip()
        environment_file_loaded = (
            str(path) in environment_files
            if path is not None and verify_service_configuration
            else None
        )
        status = {
            "adb_port": DEFAULT_ADB_PORT,
            "adb_target": f"localhost:{DEFAULT_ADB_PORT}",
            "adb_port_source": "default",
            "adb_environment_file": str(path) if path is not None else None,
            "service_environment_files": environment_files or None,
            "automation_environment_file_loaded": environment_file_loaded,
            "adb_port_error": None,
            "strategy": DEFAULT_STRATEGY,
            "strategy_source": "default",
            "strategy_environment_file": str(path) if path is not None else None,
            "strategy_error": None,
            "strategy_options": list(CONFIGURABLE_STRATEGIES),
            "startup_gate_policy": DEFAULT_STARTUP_GATE_POLICY,
            "startup_gate_policy_source": "default",
            "startup_gate_policy_options": list(STARTUP_GATE_POLICIES),
            "startup_gate_policy_error": None,
        }
        if path is not None and environment_file_loaded is False:
            configuration_error = (
                f"Installed {self.service_name} does not load {path}; reinstall "
                "deploy/systemd/thetower-automation.service and run "
                "systemctl --user daemon-reload"
            )
            status["adb_port_error"] = configuration_error
            status["strategy_error"] = configuration_error
            status["startup_gate_policy_error"] = configuration_error
        if path is None or not path.exists():
            return status

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            status["adb_port_error"] = f"Unable to read {path}: {exc}"
            status["strategy_error"] = f"Unable to read {path}: {exc}"
            status["startup_gate_policy_error"] = (
                f"Unable to read {path}: {exc}"
            )
            return status

        adb_assignments = [
            line.split("=", 1)[1].strip()
            for line in lines
            if line.strip().startswith(f"{ADB_PORT_ENVIRONMENT_VARIABLE}=")
        ]
        strategy_assignments = [
            line.split("=", 1)[1].strip().lower()
            for line in lines
            if line.strip().startswith(f"{STRATEGY_ENVIRONMENT_VARIABLE}=")
        ]
        startup_gate_assignments = [
            line.split("=", 1)[1].strip().lower()
            for line in lines
            if line.strip().startswith(
                f"{STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE}="
            )
        ]
        if len(adb_assignments) > 1:
            status["adb_port_error"] = (
                f"{path} must contain at most one "
                f"{ADB_PORT_ENVIRONMENT_VARIABLE}=PORT assignment"
            )
        elif adb_assignments:
            try:
                port = int(adb_assignments[0])
            except ValueError:
                status["adb_port_error"] = (
                    f"{ADB_PORT_ENVIRONMENT_VARIABLE} must be an integer"
                )
            else:
                if not 1 <= port <= 65535:
                    status["adb_port_error"] = (
                        f"{ADB_PORT_ENVIRONMENT_VARIABLE} must be between 1 and 65535"
                    )
                else:
                    status.update(
                        {
                            "adb_port": port,
                            "adb_target": f"localhost:{port}",
                            "adb_port_source": "environment-file",
                        }
                    )

        if len(strategy_assignments) > 1:
            status["strategy_error"] = (
                f"{path} must contain at most one "
                f"{STRATEGY_ENVIRONMENT_VARIABLE}=NAME assignment"
            )
        elif strategy_assignments:
            strategy = strategy_assignments[0]
            if strategy not in {*CONFIGURABLE_STRATEGIES, DEFAULT_STRATEGY}:
                status["strategy_error"] = (
                    f"{STRATEGY_ENVIRONMENT_VARIABLE} must be one of: "
                    + ", ".join(CONFIGURABLE_STRATEGIES)
                )
            else:
                status.update(
                    {
                        "strategy": strategy,
                        "strategy_source": "environment-file",
                    }
                )
        if len(startup_gate_assignments) > 1:
            status["startup_gate_policy_error"] = (
                f"{path} must contain at most one "
                f"{STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE}=POLICY assignment"
            )
        elif startup_gate_assignments:
            policy = startup_gate_assignments[0]
            if policy not in STARTUP_GATE_POLICIES:
                status["startup_gate_policy_error"] = (
                    f"{STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE} must be one of: "
                    + ", ".join(STARTUP_GATE_POLICIES)
                )
            else:
                status.update(
                    {
                        "startup_gate_policy": policy,
                        "startup_gate_policy_source": "environment-file",
                    }
                )
        return status

    def _write_automation_environment_file(
        self,
        port: int,
        strategy: str,
        startup_gate_policy: str,
    ) -> None:
        path = self.adb_environment_file
        if path is None:  # Guarded by public methods; keeps typing explicit.
            raise AutomationProcessError(
                "Automation environment file is unavailable"
            )
        temp_name: Optional[str] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(f"{ADB_PORT_ENVIRONMENT_VARIABLE}={port}\n")
                handle.write(f"{STRATEGY_ENVIRONMENT_VARIABLE}={strategy}\n")
                handle.write(
                    f"{STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE}="
                    f"{startup_gate_policy}\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
        except OSError as exc:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise AutomationProcessError(
                f"Unable to persist automation configuration in {path}: {exc}"
            ) from exc

    def _unavailable_status(self, error: str) -> dict[str, Any]:
        return {
            "manager": "systemd-user",
            "service": self.service_name,
            "available": False,
            "load_state": None,
            "active_state": None,
            "sub_state": None,
            "unit_file_state": None,
            "main_pid": None,
            "exit_status": None,
            "active": False,
            "error": error,
        }


def _parse_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in (output or "").splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            properties[key] = value
    return properties


def _integer(value: object) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _positive_int(value: object) -> Optional[int]:
    parsed = _integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def _command_error(action: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "unknown systemctl error").strip()
    return f"Unable to {action} automation service: {detail}"


__all__ = [
    "AutomationProcessError",
    "DEFAULT_ADB_ENVIRONMENT_FILE",
    "DEFAULT_AUTOMATION_SERVICE",
    "SystemdAutomationManager",
]
