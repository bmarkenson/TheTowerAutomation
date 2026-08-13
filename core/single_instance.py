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
        self._metadata: dict[str, object] = {}

    def _write_metadata(self, metadata: dict[str, object]) -> None:
        handle = self._handle
        if handle is None:
            raise RuntimeError("single-instance lock is not acquired")
        handle.seek(0)
        handle.truncate()
        json.dump(metadata, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._metadata = dict(metadata)

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

        metadata: dict[str, object] = {
            "pid": os.getpid(),
            "state": "held",
            "target": self.target,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._handle = handle
        try:
            self._write_metadata(metadata)
        except Exception:
            self._handle = None
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            raise

    def bind_runtime_owner(
        self,
        *,
        runtime_id: str,
        target_generation: int,
    ) -> None:
        """Bind held-lock metadata to one exact runtime target generation."""

        normalized_runtime_id = str(runtime_id).strip()
        if not normalized_runtime_id:
            raise ValueError("runtime_id must not be empty")
        if (
            isinstance(target_generation, bool)
            or not isinstance(target_generation, int)
            or target_generation < 1
        ):
            raise ValueError("target_generation must be a positive integer")
        self._write_metadata(
            {
                **self._metadata,
                "runtime_id": normalized_runtime_id,
                "target_generation": target_generation,
            }
        )

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
                **(
                    {
                        "runtime_id": self._metadata["runtime_id"],
                        "target_generation": self._metadata[
                            "target_generation"
                        ],
                    }
                    if "runtime_id" in self._metadata
                    and "target_generation" in self._metadata
                    else {}
                ),
            }
            handle.seek(0)
            handle.truncate()
            json.dump(metadata, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            self._metadata = dict(metadata)
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
