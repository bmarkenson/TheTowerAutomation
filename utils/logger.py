# utils/logger.py
from __future__ import annotations

"""Minimal logging helpers shared across the automation runtime."""

from datetime import datetime
import os
from typing import Optional


_MISSION_LOG_PATH: Optional[str] = None


def set_mission_log_path(path: Optional[str]) -> None:
    """Configure an optional secondary log file for mission/strategy logs."""
    global _MISSION_LOG_PATH
    _MISSION_LOG_PATH = path if path else None


def _write_entry(entry: str, *, extra_path: Optional[str] = None) -> None:
    """Append a log entry to the primary log and optional extra path."""
    os.makedirs("logs", exist_ok=True)
    with open("logs/actions.log", "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    if extra_path:
        os.makedirs(os.path.dirname(extra_path) or ".", exist_ok=True)
        with open(extra_path, "a", encoding="utf-8") as extra:
            extra.write(entry + "\n")


def log(msg: str, level: str = "INFO", *, extra_path: Optional[str] = None) -> None:
    """
    Write a timestamped log entry to stdout and append to logs/actions.log.

    Args:
        msg (str): The log message text.
        level (str, optional): Log level label (e.g., "INFO", "ERROR"). Defaults to "INFO".

    Side effects:
        - Prints to stdout.
        - Creates logs/ directory if missing.
        - Appends entry to logs/actions.log.

    Raises:
        OSError: If unable to create logs/ directory or write to the log file.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{level} {timestamp}] {msg}"
    print(entry)

    _write_entry(entry, extra_path=extra_path)


def log_mission(msg: str, level: str = "INFO") -> None:
    """Log mission/strategy messages to the main log and optional mission log."""
    log(msg, level, extra_path=_MISSION_LOG_PATH)
