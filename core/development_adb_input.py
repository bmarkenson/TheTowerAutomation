"""Lease-aware, exact-target ADB input for bounded development actions.

This module is intentionally development-side.  It consumes the production
control surface's composite status, performs one bounded exact-target geometry
capture, revalidates the same lease, and then attempts one tap or swipe without
automatic retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np

from core.screen_geometry import (
    CANONICAL_SCREEN_SIZE,
    ScreenSize,
    canonical_to_device_point,
    record_device_screen_size,
)
from core.ss_capture import normalize_device_screenshot
from utils.logger import (
    log_action_intent,
    log_input,
    log_result,
    new_operation_id,
)


DEFAULT_STATUS_URL = "http://127.0.0.1:8787/api/v1/status"
DEFAULT_HTTP_TIMEOUT_SECONDS = 3.0
DEFAULT_ADB_READ_TIMEOUT_SECONDS = 10.0
DEFAULT_ADB_INPUT_TIMEOUT_SECONDS = 5.0
MAX_STATUS_RESPONSE_BYTES = 1024 * 1024
MAX_SWIPE_DURATION_MS = 5000
SWIPE_COMPLETION_MARGIN_SECONDS = 2.0
MAX_ADB_INPUT_TIMEOUT_SECONDS = max(
    DEFAULT_ADB_INPUT_TIMEOUT_SECONDS,
    MAX_SWIPE_DURATION_MS / 1000 + SWIPE_COMPLETION_MARGIN_SECONDS,
)
SERVER_TIME_PRECISION_MARGIN_SECONDS = 1.0
STATUS_RESPONSE_DISPATCH_MARGIN_SECONDS = 1.0
LEASE_WINDOW_MARGIN_SECONDS = (
    SERVER_TIME_PRECISION_MARGIN_SECONDS
    + STATUS_RESPONSE_DISPATCH_MARGIN_SECONDS
)

EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_REJECTED = 3
EXIT_ADB_FAILURE = 4
EXIT_AUDIT_FAILURE = 5

_LEASE_ID_RE = re.compile(r"[0-9a-f]{32}")


class LeaseStatusError(RuntimeError):
    """The composite status did not prove current development input authority."""


class AdbReadError(RuntimeError):
    """The bounded exact-target geometry read failed."""


class AdbInputError(RuntimeError):
    """One ADB input attempt failed or has an uncertain outcome."""

    def __init__(self, message: str, *, outcome: str) -> None:
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class DevelopmentInputRequest:
    """One canonical-coordinate tap or swipe."""

    action: str
    coordinates: tuple[float, ...]
    duration_ms: Optional[int] = None

    @classmethod
    def tap(cls, x: float, y: float) -> "DevelopmentInputRequest":
        return cls("tap", (x, y))

    @classmethod
    def swipe(
        cls,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration_ms: int,
    ) -> "DevelopmentInputRequest":
        return cls("swipe", (x1, y1, x2, y2), duration_ms=duration_ms)

    def validate(self) -> None:
        expected_count = 2 if self.action == "tap" else 4
        if self.action not in {"tap", "swipe"}:
            raise ValueError("action must be tap or swipe")
        if len(self.coordinates) != expected_count:
            raise ValueError(
                f"{self.action} requires {expected_count} canonical coordinates"
            )
        canonical_width, canonical_height = CANONICAL_SCREEN_SIZE
        for index, value in enumerate(self.coordinates):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("canonical coordinates must be finite numbers")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("canonical coordinates must be finite numbers")
            limit = canonical_width if index % 2 == 0 else canonical_height
            axis = "x" if index % 2 == 0 else "y"
            if not 0 <= numeric < limit:
                raise ValueError(
                    f"canonical {axis} coordinate {numeric:g} is outside "
                    f"[0, {limit})"
                )
        if self.action == "tap":
            if self.duration_ms is not None:
                raise ValueError("tap does not accept a swipe duration")
            return
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or not 1 <= self.duration_ms <= MAX_SWIPE_DURATION_MS
        ):
            raise ValueError(
                "swipe duration must be an integer between 1 and "
                f"{MAX_SWIPE_DURATION_MS} milliseconds"
            )

    def canonical_detail(self) -> str:
        if self.action == "tap" and len(self.coordinates) == 2:
            return f"canonical={_point_text(self.coordinates)}"
        if self.action == "swipe" and len(self.coordinates) == 4:
            return (
                f"canonical={_point_text(self.coordinates[:2])}->"
                f"{_point_text(self.coordinates[2:])} "
                f"duration_ms={self.duration_ms!r}"
            )
        return (
            f"canonical={self.coordinates!r} duration_ms={self.duration_ms!r}"
        )

    def input_timeout_seconds(self) -> float:
        """Return the bounded timeout reserved for this one ADB subprocess."""

        if self.action != "swipe" or self.duration_ms is None:
            return DEFAULT_ADB_INPUT_TIMEOUT_SECONDS
        return max(
            DEFAULT_ADB_INPUT_TIMEOUT_SECONDS,
            self.duration_ms / 1000 + SWIPE_COMPLETION_MARGIN_SECONDS,
        )

    def minimum_remaining_lease_seconds(self) -> float:
        """Return the server-reported window required immediately before input."""

        return self.input_timeout_seconds() + LEASE_WINDOW_MARGIN_SECONDS

    def mapped_input_arguments(self, *, target: str) -> tuple[list[str], str]:
        if self.action == "tap":
            point = canonical_to_device_point(
                self.coordinates[0],
                self.coordinates[1],
                device_id=target,
            )
            return ["input", "tap", str(point[0]), str(point[1])], _point_text(point)

        start = canonical_to_device_point(
            self.coordinates[0],
            self.coordinates[1],
            device_id=target,
        )
        end = canonical_to_device_point(
            self.coordinates[2],
            self.coordinates[3],
            device_id=target,
        )
        return (
            [
                "input",
                "swipe",
                str(start[0]),
                str(start[1]),
                str(end[0]),
                str(end[1]),
                str(self.duration_ms),
            ],
            f"{_point_text(start)}->{_point_text(end)}",
        )


@dataclass(frozen=True)
class LeaseAuthority:
    """Validated status fields binding one helper invocation to production."""

    lease_id: str
    runtime_id: str
    runtime_pid: int
    adb_target: str
    expires_at: float
    server_time: float

    @property
    def binding(self) -> tuple[str, str, int, str, float]:
        return (
            self.lease_id,
            self.runtime_id,
            self.runtime_pid,
            self.adb_target,
            self.expires_at,
        )

    @property
    def remaining_seconds(self) -> float:
        return self.expires_at - self.server_time


@dataclass(frozen=True)
class InputExecutionResult:
    """Deterministic CLI-facing outcome for one invocation."""

    exit_code: int
    message: str
    input_attempted: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == EXIT_SUCCESS


class AdbBoundary(Protocol):
    """Injected exact-target ADB boundary."""

    def acquire_geometry(self, target: str) -> ScreenSize:
        ...

    def run_input(
        self,
        target: str,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> None:
        ...


class AuditBoundary(Protocol):
    """Injected action-log boundary."""

    def intent(
        self,
        request: DevelopmentInputRequest,
        *,
        lease_id: str,
        operation_id: str,
    ) -> None:
        ...

    def input_attempt(
        self,
        request: DevelopmentInputRequest,
        *,
        authority: LeaseAuthority,
        mapped_coordinates: str,
        outcome: str,
    ) -> None:
        ...

    def result(
        self,
        request: DevelopmentInputRequest,
        *,
        operation_id: str,
        disposition: str,
        detail: str,
    ) -> None:
        ...


class ActionLogAudit:
    """Write helper audit entries to one explicitly selected action log."""

    def __init__(self, path: Path | str) -> None:
        selected = Path(path)
        if not selected.is_absolute():
            raise ValueError("action-log path must be absolute")
        self.path = str(selected)

    def intent(
        self,
        request: DevelopmentInputRequest,
        *,
        lease_id: str,
        operation_id: str,
    ) -> None:
        display_lease_id = " ".join(str(lease_id or "").split())[:128]
        log_action_intent(
            f"Development ADB {request.action}",
            reason="verify the active production lease before one bounded input",
            detail=f"lease_id={display_lease_id} {request.canonical_detail()}",
            operation_id=operation_id,
            primary_path=self.path,
        )

    def input_attempt(
        self,
        request: DevelopmentInputRequest,
        *,
        authority: LeaseAuthority,
        mapped_coordinates: str,
        outcome: str,
    ) -> None:
        log_input(
            f"Development ADB {request.action} attempted",
            detail=(
                f"lease_id={authority.lease_id} target={authority.adb_target} "
                f"{request.canonical_detail()} device={mapped_coordinates} "
                f"outcome={outcome}"
            ),
            primary_path=self.path,
        )

    def result(
        self,
        request: DevelopmentInputRequest,
        *,
        operation_id: str,
        disposition: str,
        detail: str,
    ) -> None:
        log_result(
            f"Development ADB {request.action} {disposition}",
            detail=detail,
            operation_id=operation_id,
            primary_path=self.path,
        )


class SubprocessAdbBoundary:
    """One-read/one-input ADB implementation with explicit target and timeout."""

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        read_timeout_seconds: float = DEFAULT_ADB_READ_TIMEOUT_SECONDS,
    ) -> None:
        self._run = run
        self.read_timeout_seconds = _positive_timeout(
            read_timeout_seconds,
            "ADB read timeout",
        )

    def acquire_geometry(self, target: str) -> ScreenSize:
        command = ["adb", "-s", _exact_target(target), "exec-out", "screencap", "-p"]
        try:
            completed = self._run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self.read_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbReadError("exact-target screenshot timed out") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbReadError(
                f"exact-target screenshot failed: {_concise_exception(exc)}"
            ) from exc
        if completed.returncode != 0:
            raise AdbReadError(
                "exact-target screenshot returned nonzero status"
                + _stderr_suffix(completed.stderr)
            )
        payload = completed.stdout
        if not isinstance(payload, bytes) or not payload:
            raise AdbReadError("exact-target screenshot returned no PNG data")
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AdbReadError("exact-target screenshot response is not PNG")
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise AdbReadError("exact-target screenshot PNG could not be decoded")
        native_height, native_width = image.shape[:2]
        try:
            normalized = normalize_device_screenshot(image, device_id=target)
        except ValueError as exc:
            raise AdbReadError(str(exc)) from exc
        if normalized is None:
            raise AdbReadError("exact-target screenshot is incomplete")
        return native_width, native_height

    def run_input(
        self,
        target: str,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> None:
        normalized_arguments = [str(value) for value in arguments]
        if normalized_arguments[:2] not in (["input", "tap"], ["input", "swipe"]):
            raise ValueError("ADB boundary accepts only one tap or swipe")
        timeout = _bounded_input_timeout(timeout_seconds)
        command = ["adb", "-s", _exact_target(target), "shell", *normalized_arguments]
        try:
            completed = self._run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbInputError(
                "ADB input timed out; the result is uncertain and will not be retried",
                outcome="timeout",
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbInputError(
                "ADB input raised an exception and will not be retried: "
                f"{_concise_exception(exc)}",
                outcome="exception",
            ) from exc
        if completed.returncode != 0:
            raise AdbInputError(
                "ADB input returned nonzero status and will not be retried"
                + _stderr_suffix(completed.stderr),
                outcome="nonzero",
            )


def fetch_control_status(
    *,
    url: str = DEFAULT_STATUS_URL,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urlopen,
) -> Mapping[str, Any]:
    """Fetch one bounded JSON status response from the loopback control service."""

    timeout = _positive_timeout(timeout_seconds, "HTTP timeout")
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", None)
            if status_code is None and hasattr(response, "getcode"):
                status_code = response.getcode()
            if status_code != 200:
                raise LeaseStatusError(
                    f"control status returned HTTP {status_code}"
                )
            payload = response.read(MAX_STATUS_RESPONSE_BYTES + 1)
    except LeaseStatusError:
        raise
    except HTTPError as exc:
        raise LeaseStatusError(
            f"control status returned HTTP {exc.code}"
        ) from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise LeaseStatusError(
            f"control status is unavailable: {_concise_exception(exc)}"
        ) from exc
    if len(payload) > MAX_STATUS_RESPONSE_BYTES:
        raise LeaseStatusError("control status response is too large")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LeaseStatusError("control status response is malformed JSON") from exc
    if not isinstance(decoded, Mapping):
        raise LeaseStatusError("control status response must be a JSON object")
    return decoded


def validate_active_lease_status(
    status: Mapping[str, Any],
    *,
    lease_id: str,
) -> LeaseAuthority:
    """Consume the composite decision and validate command-binding fields."""

    if not isinstance(status, Mapping):
        raise LeaseStatusError("control status response is malformed")
    normalized_lease_id = _normalize_lease_id(lease_id)
    if status.get("api_version") != 1:
        raise LeaseStatusError("control status API version is missing or unsupported")
    capabilities = status.get("capabilities")
    if not isinstance(capabilities, list) or "interactive_development_lease_v1" not in capabilities:
        raise LeaseStatusError(
            "control status does not advertise interactive_development_lease_v1"
        )

    control = _required_mapping(status.get("control"), "operator control")
    control_state = control.get("state")
    if control_state != "RUNNING":
        raise LeaseStatusError(
            f"operator control is {control_state or 'unknown'}; RUNNING is required"
        )

    lease = _required_mapping(
        status.get("interactive_development_lease"),
        "interactive development lease status",
    )
    if lease.get("schema_version") != 1:
        raise LeaseStatusError("interactive development lease status is malformed")
    request = _required_mapping(lease.get("request"), "lease request")
    if request.get("schema_version") != 1:
        raise LeaseStatusError("lease request is malformed")
    request_lease_id = _status_lease_id(request.get("lease_id"), "lease request")
    if request_lease_id != normalized_lease_id:
        raise LeaseStatusError("supplied lease ID does not match the active request")
    request_state = request.get("request_state")
    if request_state != "requested":
        reason = request.get("terminal_reason") if request_state == "terminal" else None
        suffix = f": {reason}" if reason else ""
        raise LeaseStatusError(
            f"lease request state is {request_state or 'missing'}{suffix}"
        )
    request_runtime = _runtime_identity(request.get("runtime"), "lease request")

    acknowledgement = _required_mapping(
        lease.get("runtime_acknowledgement"),
        "runtime acknowledgement",
    )
    if acknowledgement.get("schema_version") != 1:
        raise LeaseStatusError("runtime acknowledgement is malformed")
    acknowledgement_lease_id = _status_lease_id(
        acknowledgement.get("lease_id"),
        "runtime acknowledgement",
    )
    if acknowledgement_lease_id != normalized_lease_id:
        raise LeaseStatusError("runtime acknowledgement names a different lease")
    if acknowledgement.get("state") != "active":
        raise LeaseStatusError(
            "runtime acknowledgement is "
            f"{acknowledgement.get('state') or 'missing'}, not active"
        )
    acknowledgement_runtime = _runtime_identity(
        acknowledgement.get("runtime"),
        "runtime acknowledgement",
    )
    if acknowledgement_runtime != request_runtime:
        raise LeaseStatusError(
            "request and runtime acknowledgement ownership differ"
        )
    request_expiry = _timestamp(request.get("expires_at"), "lease expires_at")
    acknowledgement_expiry = _timestamp(
        acknowledgement.get("expires_at"),
        "runtime acknowledgement expires_at",
    )
    if acknowledgement_expiry != request_expiry:
        raise LeaseStatusError(
            "request and runtime acknowledgement expiry windows differ"
        )
    server_timestamp = _timestamp(status.get("server_time"), "server_time")
    if server_timestamp >= request_expiry:
        raise LeaseStatusError("the server-evaluated heartbeat deadline has expired")

    if lease.get("active") is not True:
        reason = str(lease.get("reason") or "the composite lease is inactive")
        raise LeaseStatusError(f"composite lease is inactive: {reason}")

    return LeaseAuthority(
        lease_id=normalized_lease_id,
        runtime_id=request_runtime[0],
        runtime_pid=request_runtime[1],
        adb_target=request_runtime[2],
        expires_at=request_expiry,
        server_time=server_timestamp,
    )


def execute_development_input(
    request: DevelopmentInputRequest,
    *,
    lease_id: str,
    status_reader: Callable[[], Mapping[str, Any]],
    adb: AdbBoundary,
    audit: AuditBoundary,
) -> InputExecutionResult:
    """Attempt one guarded input and return without replaying any ADB failure."""

    operation_id = new_operation_id()
    try:
        audit.intent(
            request,
            lease_id=str(lease_id or ""),
            operation_id=operation_id,
        )
    except Exception as exc:
        return InputExecutionResult(
            EXIT_AUDIT_FAILURE,
            "Rejected before input: unable to write the required ACTION intent "
            f"record ({_concise_exception(exc)})",
        )

    try:
        normalized_lease_id = _normalize_lease_id(lease_id)
    except LeaseStatusError as exc:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_REJECTED,
            disposition="rejected",
            detail=str(exc),
            message=f"Rejected: {exc}",
        )

    try:
        request.validate()
    except ValueError as exc:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_REJECTED,
            disposition="rejected",
            detail=str(exc),
            message=f"Rejected: {exc}",
        )
    input_timeout_seconds = request.input_timeout_seconds()
    minimum_remaining_lease_seconds = request.minimum_remaining_lease_seconds()

    try:
        initial_status = status_reader()
        initial_authority = validate_active_lease_status(
            initial_status,
            lease_id=normalized_lease_id,
        )
    except (LeaseStatusError, OSError, ValueError) as exc:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_REJECTED,
            disposition="rejected",
            detail=str(exc),
            message=f"Rejected: {exc}",
        )
    except Exception as exc:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_REJECTED,
            disposition="rejected",
            detail=f"status read failed: {_concise_exception(exc)}",
            message=(
                "Rejected: control status could not be read ("
                f"{_concise_exception(exc)})"
            ),
        )

    try:
        native_width, native_height = adb.acquire_geometry(
            initial_authority.adb_target
        )
        record_device_screen_size(
            native_width,
            native_height,
            device_id=initial_authority.adb_target,
        )
    except Exception as exc:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_ADB_FAILURE,
            disposition="failed",
            detail=f"geometry acquisition failed: {_concise_exception(exc)}",
            message=(
                "Failed before input: unable to establish exact-target geometry "
                f"({_concise_exception(exc)})"
            ),
        )

    input_arguments, mapped_coordinates = request.mapped_input_arguments(
        target=initial_authority.adb_target
    )
    try:
        final_status = status_reader()
        final_authority = validate_active_lease_status(
            final_status,
            lease_id=normalized_lease_id,
        )
        if final_authority.binding != initial_authority.binding:
            raise LeaseStatusError(
                "lease, runtime, exact ADB target, or acknowledged expiry window "
                "changed during geometry acquisition"
            )
        if final_authority.remaining_seconds < minimum_remaining_lease_seconds:
            raise LeaseStatusError(
                "lease window has only "
                f"{_seconds_text(final_authority.remaining_seconds)} remaining; "
                f"{_seconds_text(minimum_remaining_lease_seconds)} are required for "
                "the bounded input timeout plus timing margin. Heartbeat the "
                "lease, wait for the newly acknowledged current window, then retry"
            )
    except (LeaseStatusError, OSError, ValueError) as exc:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_REJECTED,
            disposition="rejected",
            detail=str(exc),
            message=f"Rejected after geometry read: {exc}",
        )
    except Exception as exc:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_REJECTED,
            disposition="rejected",
            detail=f"final status read failed: {_concise_exception(exc)}",
            message=(
                "Rejected after geometry read: control status could not be read ("
                f"{_concise_exception(exc)})"
            ),
        )

    input_error: Optional[AdbInputError] = None
    try:
        adb.run_input(
            final_authority.adb_target,
            input_arguments,
            timeout_seconds=input_timeout_seconds,
        )
        input_outcome = "completed"
    except AdbInputError as exc:
        input_error = exc
        input_outcome = exc.outcome
    except subprocess.TimeoutExpired as exc:
        input_error = AdbInputError(
            "ADB input timed out; the result is uncertain and will not be retried",
            outcome="timeout",
        )
        input_error.__cause__ = exc
        input_outcome = "timeout"
    except Exception as exc:
        input_error = AdbInputError(
            "ADB input raised an exception and will not be retried: "
            f"{_concise_exception(exc)}",
            outcome="exception",
        )
        input_error.__cause__ = exc
        input_outcome = "exception"

    try:
        audit.input_attempt(
            request,
            authority=final_authority,
            mapped_coordinates=mapped_coordinates,
            outcome=input_outcome,
        )
    except Exception as exc:
        detail = (
            f"input_outcome={input_outcome}; INPUT audit failed: "
            f"{_concise_exception(exc)}"
        )
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_AUDIT_FAILURE,
            disposition="failed",
            detail=detail,
            message=(
                "ADB input was attempted, but its INPUT audit record failed; "
                "do not repeat the action automatically"
            ),
            input_attempted=True,
        )

    if input_error is not None:
        return _finish(
            audit,
            request,
            operation_id=operation_id,
            exit_code=EXIT_ADB_FAILURE,
            disposition="failed",
            detail=str(input_error),
            message=f"Failed: {input_error}",
            input_attempted=True,
        )

    return _finish(
        audit,
        request,
        operation_id=operation_id,
        exit_code=EXIT_SUCCESS,
        disposition="completed",
        detail=(
            f"lease_id={final_authority.lease_id} "
            f"target={final_authority.adb_target} device={mapped_coordinates}"
        ),
        message=(
            f"Completed one {request.action} on {final_authority.adb_target} "
            f"at {mapped_coordinates}."
        ),
        input_attempted=True,
    )


def _finish(
    audit: AuditBoundary,
    request: DevelopmentInputRequest,
    *,
    operation_id: str,
    exit_code: int,
    disposition: str,
    detail: str,
    message: str,
    input_attempted: bool = False,
) -> InputExecutionResult:
    try:
        audit.result(
            request,
            operation_id=operation_id,
            disposition=disposition,
            detail=detail,
        )
    except Exception as exc:
        suffix = (
            "; do not repeat the action automatically"
            if input_attempted
            else ""
        )
        return InputExecutionResult(
            EXIT_AUDIT_FAILURE,
            "Required RESULT audit record failed after "
            f"{disposition}: {_concise_exception(exc)}{suffix}",
            input_attempted=input_attempted,
        )
    return InputExecutionResult(
        exit_code,
        message,
        input_attempted=input_attempted,
    )


def _required_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LeaseStatusError(f"{label} is missing or malformed")
    return value


def _runtime_identity(value: object, label: str) -> tuple[str, int, str]:
    runtime = _required_mapping(value, f"{label} runtime ownership")
    runtime_id = str(runtime.get("runtime_id") or "").strip()
    pid = runtime.get("pid")
    target = str(runtime.get("adb_target") or "").strip()
    if not runtime_id or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise LeaseStatusError(f"{label} runtime identity is malformed")
    if not target or target == "unknown":
        raise LeaseStatusError(f"{label} exact ADB target is missing")
    return runtime_id, pid, target


def _normalize_lease_id(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if _LEASE_ID_RE.fullmatch(normalized) is None:
        raise LeaseStatusError("lease ID must be 32 hexadecimal characters")
    return normalized


def _status_lease_id(value: object, label: str) -> str:
    try:
        return _normalize_lease_id(value)
    except LeaseStatusError as exc:
        raise LeaseStatusError(f"{label} lease ID is malformed") from exc


def _timestamp(value: object, label: str) -> float:
    if not isinstance(value, str) or not value.strip():
        raise LeaseStatusError(f"{label} is missing or malformed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LeaseStatusError(f"{label} is missing or malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LeaseStatusError(f"{label} must include a timezone")
    try:
        return parsed.timestamp()
    except (OverflowError, OSError) as exc:
        raise LeaseStatusError(f"{label} is missing or malformed") from exc


def _positive_timeout(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite positive number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite positive number") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return numeric


def _bounded_input_timeout(value: object) -> float:
    timeout = _positive_timeout(value, "ADB input timeout")
    if timeout > MAX_ADB_INPUT_TIMEOUT_SECONDS:
        raise ValueError(
            "ADB input timeout must not exceed "
            f"{MAX_ADB_INPUT_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


def _exact_target(target: object) -> str:
    normalized = str(target or "").strip()
    if not normalized or normalized == "unknown":
        raise ValueError("exact ADB target must not be empty")
    return normalized


def _point_text(values: Sequence[object]) -> str:
    return "(" + ",".join(_number_text(value) for value in values) + ")"


def _seconds_text(value: float) -> str:
    unit = "second" if value == 1 else "seconds"
    return f"{value:g} {unit}"


def _number_text(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return f"{value:g}"
    return str(value)


def _stderr_suffix(stderr: object) -> str:
    if isinstance(stderr, bytes):
        detail = stderr.decode("utf-8", errors="replace").strip()
    else:
        detail = str(stderr or "").strip()
    if not detail:
        return ""
    return f": {detail[:240]}"


def _concise_exception(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    return (detail or exc.__class__.__name__)[:240]


__all__ = [
    "ActionLogAudit",
    "AdbInputError",
    "AdbReadError",
    "DEFAULT_STATUS_URL",
    "DevelopmentInputRequest",
    "EXIT_ADB_FAILURE",
    "EXIT_AUDIT_FAILURE",
    "EXIT_REJECTED",
    "EXIT_SUCCESS",
    "EXIT_USAGE",
    "InputExecutionResult",
    "LeaseAuthority",
    "LeaseStatusError",
    "LEASE_WINDOW_MARGIN_SECONDS",
    "MAX_ADB_INPUT_TIMEOUT_SECONDS",
    "MAX_SWIPE_DURATION_MS",
    "SubprocessAdbBoundary",
    "execute_development_input",
    "fetch_control_status",
    "validate_active_lease_status",
]
