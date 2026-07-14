# utils/logger.py
from __future__ import annotations

"""Minimal logging helpers shared across the automation runtime."""

from datetime import datetime
import os
from typing import Optional


_MISSION_LOG_PATH: Optional[str] = None
DEFAULT_ACTION_LOG_PATH = os.path.join("logs", "actions.log")


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


def _write_entry(entry: str, *, extra_path: Optional[str] = None) -> None:
    """Append a log entry to the primary log and optional extra path."""
    primary_path = get_action_log_path()
    os.makedirs(os.path.dirname(primary_path) or ".", exist_ok=True)
    with open(primary_path, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    if extra_path:
        os.makedirs(os.path.dirname(extra_path) or ".", exist_ok=True)
        with open(extra_path, "a", encoding="utf-8") as extra:
            extra.write(entry + "\n")


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


def log_status(msg: str) -> None:
    """Helper for status updates that should appear on the console."""
    log(msg, "STATUS")
