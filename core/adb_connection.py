"""Shared ADB connectivity state and bounded reconnect scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import subprocess
import threading
import time
from typing import Callable, Optional, Sequence

from core.adb_utils import resolve_adb_device
from utils.logger import log, log_result


ADB_CONNECTION_WARNING_AFTER_FAILURES = 3
ADB_CONNECTION_WARNING_REPEAT_S = 5 * 60.0
ADB_RECONNECT_DELAYS_S = (1.0, 2.0, 5.0, 10.0, 30.0)
ADB_CONNECTION_COMMAND_TIMEOUT_S = 5.0


def _current_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class AdbConnectionSnapshot:
    """Read-only connection state for one ADB target."""

    target: str
    connected: Optional[bool]
    failures: int
    warning_active: bool
    retry_in_s: float


@dataclass
class _TargetConnectionState:
    connected: Optional[bool] = None
    failures: int = 0
    outage_started_at: Optional[float] = None
    warning_active: bool = False
    last_warning_at: Optional[float] = None
    next_attempt_at: float = 0.0


def _adb_is_connected(target: str) -> bool:
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            check=False,
            timeout=ADB_CONNECTION_COMMAND_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or not result.stdout:
        return False
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == target:
            return parts[1].lower() == "device"
    return False


def _adb_connect(target: str) -> bool:
    """Refresh one TCP transport and report only the connect command's hint.

    The caller must verify the resulting target state independently.  ADB can
    say ``already connected`` while retaining an ``offline`` transport after
    the emulator behind a still-open SSH forward has stopped.
    """

    if not target or ":" not in target:
        return False
    try:
        subprocess.run(
            ["adb", "disconnect", target],
            capture_output=True,
            text=True,
            check=False,
            timeout=ADB_CONNECTION_COMMAND_TIMEOUT_S,
        )
        result = subprocess.run(
            ["adb", "connect", target],
            capture_output=True,
            text=True,
            check=False,
            timeout=ADB_CONNECTION_COMMAND_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = ((result.stdout or "") + (result.stderr or "")).lower()
    return "connected to" in output or "already connected to" in output


class AdbConnectionCoordinator:
    """Serialize reconnects and retain an independent schedule per target."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        is_connected: Callable[[str], bool] = _adb_is_connected,
        connect: Callable[[str], bool] = _adb_connect,
        reconnect_delays_s: Sequence[float] = ADB_RECONNECT_DELAYS_S,
        warning_after_failures: int = ADB_CONNECTION_WARNING_AFTER_FAILURES,
        warning_repeat_s: float = ADB_CONNECTION_WARNING_REPEAT_S,
        manage_connections: bool = True,
        recovery_requires_capture: bool = True,
        emit_events: bool = True,
        unavailable_impact: str = "automation inputs remain suspended",
    ) -> None:
        normalized_delays = tuple(
            max(0.0, float(delay)) for delay in reconnect_delays_s
        )
        if not normalized_delays:
            raise ValueError("At least one ADB reconnect delay is required")
        self._clock = clock
        self._is_connected = is_connected
        self._connect = connect
        self._reconnect_delays_s = normalized_delays
        self._warning_after_failures = max(1, int(warning_after_failures))
        self._warning_repeat_s = max(0.0, float(warning_repeat_s))
        self._manage_connections = bool(manage_connections)
        self._recovery_requires_capture = bool(recovery_requires_capture)
        self._emit_events = bool(emit_events)
        self._unavailable_impact = str(unavailable_impact).strip() or (
            "automation inputs remain suspended"
        )
        self._lock = threading.Lock()
        self._targets: dict[str, _TargetConnectionState] = {}

    @staticmethod
    def _target(target: Optional[str]) -> str:
        return str(target or resolve_adb_device() or "").strip()

    def capture_allowed(self, *, target: Optional[str] = None) -> bool:
        """Return False while a target is known to be disconnected."""

        selected = self._target(target)
        if not selected:
            return True
        with self._lock:
            state = self._targets.get(selected)
            return state is None or state.connected is not False

    def ensure_connected(
        self,
        *,
        target: Optional[str] = None,
        force: bool = False,
    ) -> bool:
        """Run at most one due connection attempt across concurrent callers."""

        selected = self._target(target)
        if not selected:
            return True
        with self._lock:
            now = self._clock()
            state = self._targets.setdefault(
                selected,
                _TargetConnectionState(),
            )
            if (
                state.connected is False
                and not force
                and now < state.next_attempt_at
            ):
                return False

            if self._is_connected(selected):
                if state.failures and not self._recovery_requires_capture:
                    self._record_device_recovery(selected, state, now=now)
                else:
                    state.connected = True
                    state.next_attempt_at = 0.0
                return True

            # Command output is not connection authority: ADB may report
            # "already connected" for an offline/stale TCP transport.  Judge
            # every refresh by a new exact-target ``device`` observation.
            if self._manage_connections:
                self._connect(selected)
                if self._is_connected(selected):
                    if state.failures and not self._recovery_requires_capture:
                        self._record_device_recovery(selected, state, now=now)
                    else:
                        state.connected = True
                        state.next_attempt_at = 0.0
                    return True

            self._record_failure(selected, state, now=now)
            return False

    def record_capture_success(self, *, target: Optional[str] = None) -> None:
        """Accept a successful screenshot as current connection evidence."""

        selected = self._target(target)
        if not selected:
            return
        with self._lock:
            now = self._clock()
            state = self._targets.setdefault(
                selected,
                _TargetConnectionState(),
            )
            self._record_capture_recovery(selected, state, now=now)

    def snapshot(self, *, target: Optional[str] = None) -> AdbConnectionSnapshot:
        """Return current scheduling state without attempting ADB work."""

        selected = self._target(target)
        with self._lock:
            now = self._clock()
            state = self._targets.get(selected, _TargetConnectionState())
            return AdbConnectionSnapshot(
                target=selected,
                connected=state.connected,
                failures=state.failures,
                warning_active=state.warning_active,
                retry_in_s=max(0.0, state.next_attempt_at - now),
            )

    def _record_failure(
        self,
        target: str,
        state: _TargetConnectionState,
        *,
        now: float,
    ) -> None:
        if not state.failures:
            state.outage_started_at = now
        state.connected = False
        state.failures += 1
        delay_index = min(state.failures - 1, len(self._reconnect_delays_s) - 1)
        state.next_attempt_at = now + self._reconnect_delays_s[delay_index]

        if state.failures < self._warning_after_failures:
            return
        warning_due = (
            not state.warning_active
            or state.last_warning_at is None
            or now - state.last_warning_at >= self._warning_repeat_s
        )
        state.warning_active = True
        if not self._emit_events:
            return
        if not warning_due:
            return
        qualifier = (
            "remains unavailable"
            if state.last_warning_at is not None
            else "is unavailable"
        )
        log(
            f"[ADB] Target {target} {qualifier} after {state.failures} "
            f"reconnect attempts; {self._unavailable_impact} while bounded "
            "retries continue",
            "WARN",
        )
        state.last_warning_at = now

    @staticmethod
    def _clear_failure_state(state: _TargetConnectionState) -> None:
        state.connected = True
        state.failures = 0
        state.outage_started_at = None
        state.warning_active = False
        state.last_warning_at = None
        state.next_attempt_at = 0.0

    def _record_capture_recovery(
        self,
        target: str,
        state: _TargetConnectionState,
        *,
        now: float,
    ) -> None:
        failures = state.failures
        warning_was_active = state.warning_active
        outage_started_at = state.outage_started_at
        self._clear_failure_state(state)
        if not failures:
            return
        if not self._emit_events:
            return
        if warning_was_active:
            duration_s = (
                max(0.0, now - outage_started_at)
                if outage_started_at is not None
                else 0.0
            )
            log_result(
                f"[ADB] Connection recovered — {target} available after "
                f"{failures} failed reconnect attempts over {duration_s:.0f}s"
            )
            return
        log(
            f"[ADB] Target {target} recovered after {failures} transient "
            "reconnect attempt(s)",
            "DEBUG",
        )

    def _record_device_recovery(
        self,
        target: str,
        state: _TargetConnectionState,
        *,
        now: float,
    ) -> None:
        """Record target registration recovery without claiming frame health."""

        failures = state.failures
        warning_was_active = state.warning_active
        outage_started_at = state.outage_started_at
        self._clear_failure_state(state)
        if not failures or not self._emit_events:
            return
        if warning_was_active:
            duration_s = (
                max(0.0, now - outage_started_at)
                if outage_started_at is not None
                else 0.0
            )
            log_result(
                f"[ADB] Connection registration recovered — {target} returned "
                f"device after {failures} failed reconnect attempts over "
                f"{duration_s:.0f}s"
            )
            return
        log(
            f"[ADB] Target registration for {target} recovered after {failures} "
            "transient reconnect attempt(s)",
            "DEBUG",
        )


def _normalize_local_tcp_target(value: object) -> str:
    """Validate the one loopback TCP target shape managed by TheTower."""

    target = str(value or "").strip()
    host, separator, raw_port = target.rpartition(":")
    if separator != ":" or host != "localhost":
        raise ValueError("ADB target must use localhost:PORT")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("ADB target port must be an integer") from exc
    if not 1 <= port <= 65535 or raw_port != str(port):
        raise ValueError("ADB target port must be between 1 and 65535")
    return f"localhost:{port}"


class PersistentAdbConnectionManager:
    """Keep one configured TCP target registered outside automation lifetime."""

    def __init__(
        self,
        target_provider: Callable[[], str],
        *,
        coordinator: Optional[AdbConnectionCoordinator] = None,
        poll_interval_s: float = 2.0,
        timestamp: Callable[[], str] = _current_timestamp,
    ) -> None:
        self._target_provider = target_provider
        self._coordinator = coordinator or AdbConnectionCoordinator(
            recovery_requires_capture=False,
            unavailable_impact=(
                "ADB-dependent reads and automation inputs remain unavailable"
            ),
        )
        self._poll_interval_s = max(0.1, float(poll_interval_s))
        self._timestamp = timestamp
        self._lock = threading.Lock()
        self._target: Optional[str] = None
        self._snapshot: Optional[AdbConnectionSnapshot] = None
        self._target_error: Optional[str] = None
        self._last_checked_at: Optional[str] = None

    def ensure_configured_target(self, *, force: bool = False) -> bool:
        """Resolve and refresh the persisted target without guessing a default."""

        try:
            target = _normalize_local_tcp_target(self._target_provider())
        except Exception as exc:
            self._record_target_error(str(exc) or type(exc).__name__)
            return False
        return self.ensure_target(target, force=force)

    def ensure_target(self, target: object, *, force: bool = False) -> bool:
        """Refresh an already validated configuration target immediately."""

        try:
            selected = _normalize_local_tcp_target(target)
        except ValueError as exc:
            self._record_target_error(str(exc))
            return False

        recovered_configuration = False
        with self._lock:
            recovered_configuration = self._target_error is not None
            if selected != self._target:
                self._snapshot = None
                self._last_checked_at = None
            self._target = selected
            self._target_error = None
        if recovered_configuration:
            log_result(
                f"[ADB] Connection configuration recovered — monitoring {selected}"
            )

        connected = self._coordinator.ensure_connected(
            target=selected,
            force=force,
        )
        snapshot = self._coordinator.snapshot(target=selected)
        with self._lock:
            if self._target == selected:
                self._snapshot = snapshot
                self._last_checked_at = self._timestamp()
        return connected

    def status(self) -> dict[str, object]:
        """Return the latest exact-target registration state without ADB work."""

        with self._lock:
            target = self._target
            snapshot = self._snapshot
            target_error = self._target_error
            last_checked_at = self._last_checked_at
        connected = (
            snapshot.connected
            if snapshot is not None and target_error is None
            else None
        )
        state = (
            "configuration_error"
            if target_error is not None
            else "device"
            if connected is True
            else "unavailable"
            if connected is False
            else "unknown"
        )
        return {
            "owner": "control-surface",
            "target": target,
            "state": state,
            "connected": connected,
            "failures": snapshot.failures if snapshot is not None else 0,
            "warning_active": (
                snapshot.warning_active if snapshot is not None else False
            ),
            "retry_in_seconds": (
                round(snapshot.retry_in_s, 3) if snapshot is not None else 0.0
            ),
            "last_checked_at": last_checked_at,
            "error": target_error,
        }

    def run(self, stop_event: threading.Event) -> None:
        """Maintain registration until the owning control service stops."""

        while not stop_event.is_set():
            self.ensure_configured_target()
            if stop_event.wait(self._poll_interval_s):
                return

    def _record_target_error(self, detail: str) -> None:
        normalized = str(detail).strip() or "unknown target configuration error"
        should_log = False
        with self._lock:
            if normalized != self._target_error:
                should_log = True
            self._target = None
            self._snapshot = None
            self._target_error = normalized
            self._last_checked_at = self._timestamp()
        if should_log:
            log(
                f"[ADB] Persistent connection target is unavailable: {normalized}",
                "WARN",
            )


DEFAULT_ADB_CONNECTION_COORDINATOR = AdbConnectionCoordinator()


__all__ = [
    "ADB_CONNECTION_WARNING_AFTER_FAILURES",
    "ADB_CONNECTION_WARNING_REPEAT_S",
    "ADB_RECONNECT_DELAYS_S",
    "AdbConnectionCoordinator",
    "AdbConnectionSnapshot",
    "DEFAULT_ADB_CONNECTION_COORDINATOR",
    "PersistentAdbConnectionManager",
]
