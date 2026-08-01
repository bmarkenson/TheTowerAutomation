# utils/logger.py
from __future__ import annotations

"""Minimal logging helpers shared across the automation runtime."""

from datetime import datetime
import json
import os
import re
import tempfile
import threading
from typing import Mapping, Optional, Sequence
import uuid


_MISSION_LOG_PATH: Optional[str] = None
DEFAULT_ACTION_LOG_PATH = os.path.join("logs", "actions.log")
DEFAULT_ACTIVITY_SCOPE_FILENAME = "activity_scope.json"
DEFAULT_ACTION_LOG_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_ACTION_LOG_BACKUP_COUNT = 5
ACTION_LOG_MAX_BYTES_ENV = "TOWER_ACTION_LOG_MAX_BYTES"
ACTION_LOG_BACKUP_COUNT_ENV = "TOWER_ACTION_LOG_BACKUP_COUNT"
_WRITE_LOCK = threading.Lock()
_OPERATION_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def new_operation_id() -> str:
    """Return an opaque identifier for one ACTION/RESULT workflow pair."""

    return uuid.uuid4().hex


def _operation_detail(
    detail: Optional[str],
    operation_id: Optional[str],
) -> Optional[str]:
    """Attach correlation metadata to diagnostic detail, not summary text."""

    if operation_id is None:
        return detail
    normalized = str(operation_id).strip()
    if not _OPERATION_ID_RE.fullmatch(normalized):
        raise ValueError("operation_id contains unsupported characters")
    marker = f"[OPERATION] id={normalized}"
    return f"{detail} {marker}" if detail else marker


def get_action_log_path() -> str:
    """Return the primary log path, honoring test/tool isolation overrides."""

    return os.getenv("TOWER_ACTION_LOG_PATH") or DEFAULT_ACTION_LOG_PATH


def get_activity_scope_path() -> str:
    """Return the run-scope ledger stored beside the primary action log."""

    return os.path.join(
        os.path.dirname(get_action_log_path()) or ".",
        DEFAULT_ACTIVITY_SCOPE_FILENAME,
    )


def ensure_activity_scope(*, reason: str) -> Optional[dict[str, object]]:
    """Return the current activity scope, creating it only when absent."""

    normalized_reason = _normalize_activity_scope_reason(reason)
    existing_scope = _load_activity_scope()
    if existing_scope is not None:
        return existing_scope
    return start_activity_scope(reason=normalized_reason)


def get_activity_scope() -> Optional[dict[str, object]]:
    """Return a validated copy of the persisted current-run scope."""

    return _load_activity_scope()


def capture_activity_boundary() -> Optional[dict[str, object]]:
    """Capture the current action-log position for a possible later scope."""

    primary_path = get_action_log_path()
    captured_at = datetime.now().astimezone()
    with _WRITE_LOCK:
        try:
            os.makedirs(os.path.dirname(primary_path) or ".", exist_ok=True)
            with open(primary_path, "ab"):
                pass
            source = os.stat(primary_path)
        except OSError:
            return None
    return {
        "started_at": captured_at.isoformat(timespec="microseconds"),
        "source_file_id": f"{source.st_dev}:{source.st_ino}",
        "start_offset": int(source.st_size),
    }


def start_activity_scope(
    *,
    reason: str,
    boundary: Optional[Mapping[str, object]] = None,
) -> Optional[dict[str, object]]:
    """Start one explicit current-run activity scope without risking runtime work."""

    return _start_activity_scope(reason=reason, boundary=boundary)


def start_retry_activity_scope() -> Optional[dict[str, object]]:
    """Start a Retry-owned scope whose new History baseline is still pending."""

    current_scope = _load_activity_scope()
    previous_completed_battle = _scope_completed_battle(current_scope)
    return _start_activity_scope(
        reason="game_over_retry",
        extra_payload={
            "pending_latest_completed_battle": {
                "schema_version": 1,
                "previous_completed_battle": previous_completed_battle,
            }
        },
    )


def _start_activity_scope(
    *,
    reason: str,
    boundary: Optional[Mapping[str, object]] = None,
    extra_payload: Optional[Mapping[str, object]] = None,
) -> Optional[dict[str, object]]:
    """Persist one scope, optionally with internally owned lifecycle metadata."""

    normalized_reason = _normalize_activity_scope_reason(reason)

    primary_path = get_action_log_path()
    scope_path = get_activity_scope_path()
    captured_boundary = _validated_activity_boundary(boundary)
    started_at = datetime.now().astimezone()
    payload: dict[str, object] = {
        "schema_version": 1,
        "scope": "current_run",
        "run_id": uuid.uuid4().hex,
        "started_at": (
            str(captured_boundary["started_at"])
            if captured_boundary is not None
            else started_at.isoformat(timespec="microseconds")
        ),
        "reason": normalized_reason,
        "source_file_id": (
            str(captured_boundary["source_file_id"])
            if captured_boundary is not None
            else None
        ),
        "start_offset": (
            int(captured_boundary["start_offset"])
            if captured_boundary is not None
            else 0
        ),
    }
    if extra_payload:
        payload.update(dict(extra_payload))
    write_error: Optional[OSError] = None
    with _WRITE_LOCK:
        try:
            os.makedirs(os.path.dirname(primary_path) or ".", exist_ok=True)
            with open(primary_path, "ab"):
                pass
            if captured_boundary is None:
                source = os.stat(primary_path)
                payload["source_file_id"] = f"{source.st_dev}:{source.st_ino}"
                payload["start_offset"] = int(source.st_size)
            _write_json_atomic(scope_path, payload)
        except OSError as exc:
            write_error = exc

    if write_error is not None:
        log(
            "[RUN_SCOPE] Unable to persist the current-run activity boundary: "
            f"{write_error}",
            "WARN",
        )
        return None

    log(
        "[RUN_SCOPE] Current run activity started "
        f"reason={normalized_reason} id={payload['run_id']}",
        "INFO",
    )
    return payload


def record_activity_scope_battle_history(
    *,
    run_id: str,
    latest_completed_battle: Mapping[str, object],
) -> Optional[dict[str, object]]:
    """Attach one copied Battle History identity to the matching run scope."""

    expected_run_id = str(run_id or "").strip()
    fingerprint = str(
        latest_completed_battle.get("fingerprint") or ""
    ).strip()
    if not expected_run_id:
        raise ValueError("Activity scope run ID must not be empty")
    if not fingerprint:
        raise ValueError("Battle History fingerprint must not be empty")

    scope_path = get_activity_scope_path()
    with _WRITE_LOCK:
        payload = _load_activity_scope()
        if (
            payload is None
            or str(payload.get("run_id") or "") != expected_run_id
        ):
            return None
        payload["latest_completed_battle"] = dict(latest_completed_battle)
        payload.pop("pending_latest_completed_battle", None)
        try:
            _write_json_atomic(scope_path, payload)
        except OSError:
            return None
    return dict(payload)


def record_activity_scope_session_preflight(
    *,
    run_id: str,
    strategy: str,
    configuration_fingerprint: str,
) -> Optional[dict[str, object]]:
    """Attach a completed session-check receipt to the matching run scope."""

    expected_run_id = str(run_id or "").strip()
    normalized_strategy = str(strategy or "").strip()
    normalized_fingerprint = str(configuration_fingerprint or "").strip()
    if not expected_run_id:
        raise ValueError("Activity scope run ID must not be empty")
    if not normalized_strategy:
        raise ValueError("Session preflight strategy must not be empty")
    if not normalized_fingerprint:
        raise ValueError("Session preflight fingerprint must not be empty")

    scope_path = get_activity_scope_path()
    with _WRITE_LOCK:
        payload = _load_activity_scope()
        if (
            payload is None
            or str(payload.get("run_id") or "") != expected_run_id
        ):
            return None
        payload["session_preflight"] = {
            "schema_version": 1,
            "status": "completed",
            "strategy": normalized_strategy,
            "configuration_fingerprint": normalized_fingerprint,
            "completed_at": datetime.now().astimezone().isoformat(
                timespec="microseconds"
            ),
        }
        try:
            _write_json_atomic(scope_path, payload)
        except OSError:
            return None
    return dict(payload)


def _scope_completed_battle(
    scope: Optional[Mapping[str, object]],
) -> Optional[dict[str, object]]:
    """Return the last authoritative History identity across Retry scopes."""

    if scope is None:
        return None
    raw = scope.get("latest_completed_battle")
    if not isinstance(raw, Mapping):
        pending = scope.get("pending_latest_completed_battle")
        if isinstance(pending, Mapping):
            raw = pending.get("previous_completed_battle")
    if not isinstance(raw, Mapping):
        return None
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint:
        return None
    return dict(raw)


def _normalize_activity_scope_reason(reason: str) -> str:
    normalized_reason = "_".join(str(reason or "").strip().lower().split())
    if not normalized_reason:
        raise ValueError("Activity scope reason must not be empty")
    return normalized_reason


def _validated_activity_boundary(
    boundary: Optional[Mapping[str, object]],
) -> Optional[dict[str, object]]:
    if boundary is None:
        return None
    started_at = str(boundary.get("started_at") or "").strip()
    source_file_id = str(boundary.get("source_file_id") or "").strip()
    source_parts = source_file_id.split(":")
    try:
        datetime.fromisoformat(started_at)
        start_offset = int(boundary.get("start_offset"))
    except (TypeError, ValueError):
        raise ValueError("Invalid activity scope boundary") from None
    if (
        len(source_parts) != 2
        or not all(part.isdigit() for part in source_parts)
        or start_offset < 0
    ):
        raise ValueError("Invalid activity scope boundary")
    return {
        "started_at": started_at,
        "source_file_id": source_file_id,
        "start_offset": start_offset,
    }


def _load_activity_scope() -> Optional[dict[str, object]]:
    try:
        with open(get_activity_scope_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != 1:
        return None
    if payload.get("scope") != "current_run":
        return None

    run_id = str(payload.get("run_id") or "").strip()
    started_at = str(payload.get("started_at") or "").strip()
    source_file_id = str(payload.get("source_file_id") or "").strip()
    source_parts = source_file_id.split(":")
    try:
        datetime.fromisoformat(started_at)
        start_offset = int(payload.get("start_offset"))
    except (TypeError, ValueError):
        return None
    if (
        not run_id
        or len(source_parts) != 2
        or not all(part.isdigit() for part in source_parts)
        or start_offset < 0
    ):
        return None
    return dict(payload)


def _write_json_atomic(path: str, payload: dict[str, object]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = temporary.name
    try:
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _parse_console_levels() -> set[str]:
    """Return the set of log levels that should be echoed to stdout."""
    env_levels = os.getenv("TOWER_CONSOLE_LEVELS")
    if env_levels:
        parsed = {part.strip().upper() for part in env_levels.split(",") if part.strip()}
        if parsed:
            return parsed
    return {"STATUS", "ERROR"}


_CONSOLE_LEVELS = _parse_console_levels()


def _should_print_to_console(level: str, msg: str) -> bool:
    """Decide whether a log entry should also be emitted to stdout."""
    normalized = level.upper() if level else "INFO"
    if normalized in _CONSOLE_LEVELS:
        return True
    # Fallback for legacy callers that still embed a [STATUS] tag in the message.
    return "[STATUS]" in msg and "STATUS" in _CONSOLE_LEVELS


def set_mission_log_path(path: Optional[str]) -> None:
    """Configure an optional secondary log file for mission/strategy logs."""
    global _MISSION_LOG_PATH
    _MISSION_LOG_PATH = path if path else None


def _write_entries(entries: Sequence[str], *, extra_path: Optional[str] = None) -> None:
    """Append one atomic group of entries to the primary and optional logs."""

    if not entries:
        return
    text = "".join(f"{entry}\n" for entry in entries)
    encoded_size = len(text.encode("utf-8"))
    primary_path = get_action_log_path()
    with _WRITE_LOCK:
        os.makedirs(os.path.dirname(primary_path) or ".", exist_ok=True)
        _rotate_log_if_needed(primary_path, incoming_bytes=encoded_size)
        with open(primary_path, "a", encoding="utf-8") as f:
            f.write(text)
        if extra_path:
            os.makedirs(os.path.dirname(extra_path) or ".", exist_ok=True)
            _rotate_log_if_needed(extra_path, incoming_bytes=encoded_size)
            with open(extra_path, "a", encoding="utf-8") as extra:
                extra.write(text)


def _rotate_log_if_needed(path: str, *, incoming_bytes: int) -> None:
    """Bound a log and its numbered backups before appending one entry group."""

    try:
        current_size = os.path.getsize(path)
    except FileNotFoundError:
        return
    max_bytes = _positive_environment_integer(
        ACTION_LOG_MAX_BYTES_ENV,
        DEFAULT_ACTION_LOG_MAX_BYTES,
    )
    if current_size <= 0 or current_size + max(0, incoming_bytes) <= max_bytes:
        return

    backup_count = _nonnegative_environment_integer(
        ACTION_LOG_BACKUP_COUNT_ENV,
        DEFAULT_ACTION_LOG_BACKUP_COUNT,
    )
    if backup_count <= 0:
        os.unlink(path)
        return

    for index in range(backup_count, 1, -1):
        previous = f"{path}.{index - 1}"
        if os.path.exists(previous):
            os.replace(previous, f"{path}.{index}")

    first_backup = f"{path}.1"
    if current_size <= max_bytes:
        os.replace(path, first_backup)
        return

    _archive_log_tail(path, first_backup, max_bytes=max_bytes)
    os.unlink(path)


def _archive_log_tail(path: str, destination: str, *, max_bytes: int) -> None:
    """Atomically retain only complete recent lines from an oversized log."""

    current_size = os.path.getsize(path)
    offset = max(0, current_size - max_bytes)
    with open(path, "rb") as source:
        source.seek(offset)
        payload = source.read()
    if offset:
        newline = payload.find(b"\n")
        payload = payload[newline + 1 :] if newline >= 0 else b""

    directory = os.path.dirname(destination) or "."
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=directory,
        prefix=f".{os.path.basename(destination)}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = temporary.name
    try:
        os.replace(temporary_path, destination)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _positive_environment_integer(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        parsed = int(raw)
    except ValueError:
        return int(default)
    return parsed if parsed > 0 else int(default)


def _nonnegative_environment_integer(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        parsed = int(raw)
    except ValueError:
        return int(default)
    return parsed if parsed >= 0 else int(default)


def _write_entry(entry: str, *, extra_path: Optional[str] = None) -> None:
    """Append a single log entry to the primary log and optional extra path."""

    _write_entries([entry], extra_path=extra_path)


def _paired_log(
    summary: str,
    summary_level: str,
    detail: str,
    *,
    extra_path: Optional[str] = None,
    console: Optional[bool] = None,
) -> None:
    """Write an operator summary and its diagnostic detail as one log group."""

    normalized_level = summary_level.upper()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entries = [
        f"[{normalized_level} {timestamp}] {summary}",
        f"[DEBUG {timestamp}] {detail}",
    ]
    emit_console = (
        _should_print_to_console(normalized_level, summary)
        if console is None
        else console
    )
    if emit_console:
        print(entries[0])
    _write_entries(entries, extra_path=extra_path)


def log(
    msg: str,
    level: str = "INFO",
    *,
    extra_path: Optional[str] = None,
    console: Optional[bool] = None,
) -> None:
    """
    Write a timestamped log entry to the primary file log and optionally stdout.

    Args:
        msg (str): The log message text.
        level (str, optional): Log level label (e.g., "INFO", "ERROR"). Defaults to "INFO".
        extra_path (str, optional): Secondary log path to append to in addition to the default log.
        console (bool, optional): Force console emission; default determines based on configured levels.

    Side effects:
        - Prints to stdout when allowed for the provided log level.
        - Creates the primary log directory if missing.
        - Appends to ``TOWER_ACTION_LOG_PATH`` when set, otherwise
          ``logs/actions.log``.

    Raises:
        OSError: If unable to create the primary directory or write the log file.
    """
    normalized_level = level.upper() if level else "INFO"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{normalized_level} {timestamp}] {msg}"
    emit_console = _should_print_to_console(normalized_level, msg) if console is None else console
    if emit_console:
        print(entry)

    _write_entry(entry, extra_path=extra_path)


def log_mission(msg: str, level: str = "INFO") -> None:
    """Log mission/strategy messages to the main log and optional mission log."""
    log(msg, level, extra_path=_MISSION_LOG_PATH)


def _log_summary(
    summary: str,
    level: str,
    *,
    detail: Optional[str],
    operation_id: Optional[str],
    extra_path: Optional[str],
    console: Optional[bool],
) -> None:
    """Write one semantic summary with optional paired diagnostic evidence."""

    detail = _operation_detail(detail, operation_id)
    if detail is None:
        log(summary, level, extra_path=extra_path, console=console)
        return
    _paired_log(
        summary,
        level,
        detail,
        extra_path=extra_path,
        console=console,
    )


def log_action(
    summary: str,
    *,
    detail: Optional[str] = None,
    operation_id: Optional[str] = None,
    extra_path: Optional[str] = None,
    console: Optional[bool] = None,
) -> None:
    """Log one operator-facing workflow action with optional diagnostic detail."""

    _log_summary(
        summary,
        "ACTION",
        detail=detail,
        operation_id=operation_id,
        extra_path=extra_path,
        console=console,
    )


def log_result(
    summary: str,
    *,
    detail: Optional[str] = None,
    operation_id: Optional[str] = None,
    extra_path: Optional[str] = None,
    console: Optional[bool] = None,
) -> None:
    """Log one terminal workflow outcome with optional diagnostic detail."""

    _log_summary(
        summary,
        "RESULT",
        detail=detail,
        operation_id=operation_id,
        extra_path=extra_path,
        console=console,
    )


def log_input(
    summary: str,
    *,
    detail: Optional[str] = None,
    extra_path: Optional[str] = None,
    console: Optional[bool] = None,
) -> None:
    """Log one device input with optional paired dispatch evidence."""

    _log_summary(
        summary,
        "INPUT",
        detail=detail,
        operation_id=None,
        extra_path=extra_path,
        console=console,
    )


def log_action_intent(
    purpose: str,
    *,
    reason: str,
    detail: Optional[str] = None,
    operation_id: Optional[str] = None,
    extra_path: Optional[str] = None,
    console: Optional[bool] = None,
) -> None:
    """Log one human-readable header before a guarded action sequence."""

    normalized_purpose = " ".join(str(purpose or "").split())
    normalized_reason = " ".join(str(reason or "").split())
    if not normalized_purpose:
        raise ValueError("Action intent purpose must not be empty")
    if not normalized_reason:
        raise ValueError("Action intent reason must not be empty")
    log_action(
        f"{normalized_purpose} — {normalized_reason}",
        detail=detail,
        operation_id=operation_id,
        extra_path=extra_path,
        console=console,
    )


def log_status(msg: str, *, detail: Optional[str] = None) -> None:
    """Log an operator status heartbeat with optional diagnostic detail."""

    if detail is None:
        log(msg, "STATUS")
        return
    _paired_log(msg, "STATUS", detail)
