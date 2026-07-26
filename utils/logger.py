# utils/logger.py
from __future__ import annotations

"""Minimal logging helpers shared across the automation runtime."""

from datetime import datetime
import os
import tempfile
import threading
from typing import Optional, Sequence


_MISSION_LOG_PATH: Optional[str] = None
DEFAULT_ACTION_LOG_PATH = os.path.join("logs", "actions.log")
DEFAULT_ACTION_LOG_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_ACTION_LOG_BACKUP_COUNT = 5
ACTION_LOG_MAX_BYTES_ENV = "TOWER_ACTION_LOG_MAX_BYTES"
ACTION_LOG_BACKUP_COUNT_ENV = "TOWER_ACTION_LOG_BACKUP_COUNT"
_WRITE_LOCK = threading.Lock()


def get_action_log_path() -> str:
    """Return the primary log path, honoring test/tool isolation overrides."""

    return os.getenv("TOWER_ACTION_LOG_PATH") or DEFAULT_ACTION_LOG_PATH


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


def log_action(
    summary: str,
    *,
    detail: Optional[str] = None,
    extra_path: Optional[str] = None,
    console: Optional[bool] = None,
) -> None:
    """Log an operator-facing action with optional paired diagnostic detail."""

    if detail is None:
        log(summary, "ACTION", extra_path=extra_path, console=console)
        return
    _paired_log(
        summary,
        "ACTION",
        detail,
        extra_path=extra_path,
        console=console,
    )


def log_action_intent(
    purpose: str,
    *,
    reason: str,
    detail: Optional[str] = None,
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
        extra_path=extra_path,
        console=console,
    )


def log_status(msg: str, *, detail: Optional[str] = None) -> None:
    """Log an operator status heartbeat with optional diagnostic detail."""

    if detail is None:
        log(msg, "STATUS")
        return
    _paired_log(msg, "STATUS", detail)
