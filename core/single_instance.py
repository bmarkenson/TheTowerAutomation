"""Advisory process lock preventing competing runtimes for one ADB target."""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class InstanceAlreadyRunning(RuntimeError):
    """Raised when another runtime holds the target's process lock."""


def lock_path_for_target(target: str) -> Path:
    """Return the stable repository lock path for an ADB target."""

    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(target)).strip("._")
    return PROJECT_ROOT / "logs" / f"automation-{slug or 'default'}.lock"


class SingleInstanceLock:
    """Hold a non-blocking OS lock for the lifetime of an automation process."""

    def __init__(self, target: str, path: Optional[Path] = None) -> None:
        self.target = str(target)
        self.path = Path(path) if path is not None else lock_path_for_target(self.target)
        self._handle: Optional[IO[str]] = None

    def acquire(self) -> None:
        if self._handle is not None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "owner metadata unavailable"
            handle.close()
            raise InstanceAlreadyRunning(
                f"Automation is already running for {self.target}: {owner}"
            ) from exc

        metadata = {
            "pid": os.getpid(),
            "state": "held",
            "target": self.target,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        json.dump(metadata, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            metadata = {
                "pid": None,
                "released_at": datetime.now(timezone.utc).isoformat(),
                "state": "released",
                "target": self.target,
            }
            handle.seek(0)
            handle.truncate()
            json.dump(metadata, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


__all__ = [
    "InstanceAlreadyRunning",
    "SingleInstanceLock",
    "lock_path_for_target",
]
