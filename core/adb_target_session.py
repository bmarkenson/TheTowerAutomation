"""Process-lifetime ownership and safe handoff of the active ADB target."""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from core.single_instance import SingleInstanceLock


ADB_TARGET_OPERATION_LOCK = threading.RLock()


class AdbTargetSession:
    """Own one target lock and migrate it without restarting the runtime."""

    def __init__(
        self,
        target: str,
        *,
        lock_factory: Callable[[str], SingleInstanceLock] = SingleInstanceLock,
    ) -> None:
        self.target = str(target)
        self._lock_factory = lock_factory
        self._instance_lock: Optional[SingleInstanceLock] = None

    def acquire(self) -> None:
        with ADB_TARGET_OPERATION_LOCK:
            if self._instance_lock is not None:
                return
            instance_lock = self._lock_factory(self.target)
            instance_lock.acquire()
            os.environ["ADB_DEVICE"] = self.target
            self._instance_lock = instance_lock

    def handoff(self, target: str, *, validate: Callable[[], bool]) -> bool:
        """Adopt ``target`` only after exclusive ownership and validation."""

        requested = str(target)
        with ADB_TARGET_OPERATION_LOCK:
            current_lock = self._instance_lock
            if current_lock is None:
                raise RuntimeError("ADB target session is not acquired")
            if requested == self.target:
                os.environ["ADB_DEVICE"] = requested
                return True

            replacement = self._lock_factory(requested)
            replacement.acquire()
            previous_environment = os.environ.get("ADB_DEVICE")
            os.environ["ADB_DEVICE"] = requested
            try:
                valid = bool(validate())
            except Exception:
                valid = False
            if not valid:
                if previous_environment is None:
                    os.environ.pop("ADB_DEVICE", None)
                else:
                    os.environ["ADB_DEVICE"] = previous_environment
                replacement.release()
                return False

            current_lock.release()
            self._instance_lock = replacement
            self.target = requested
            return True

    def release(self) -> None:
        with ADB_TARGET_OPERATION_LOCK:
            instance_lock = self._instance_lock
            self._instance_lock = None
            if instance_lock is not None:
                instance_lock.release()

    def __enter__(self) -> "AdbTargetSession":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


__all__ = ["ADB_TARGET_OPERATION_LOCK", "AdbTargetSession"]
