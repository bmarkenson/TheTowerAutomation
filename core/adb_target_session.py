"""Process-lifetime ownership and safe handoff of the active ADB target."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4

from core.single_instance import SingleInstanceLock


ADB_TARGET_OPERATION_LOCK = threading.RLock()


@dataclass(frozen=True)
class AdbTargetSnapshot:
    """One process-local exact-target ownership generation."""

    target: str
    generation: int
    owned: bool


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
        self._generation = 0
        self._runtime_id = uuid4().hex

    @property
    def runtime_id(self) -> str:
        """Return the process-lifetime identity bound into held lock metadata."""

        return self._runtime_id

    def bind_runtime_owner(self, runtime_id: str) -> None:
        """Bind this target session and its held lock to the runtime owner."""

        normalized = str(runtime_id).strip()
        if not normalized:
            raise ValueError("runtime_id must not be empty")
        with ADB_TARGET_OPERATION_LOCK:
            self._runtime_id = normalized
            if self._instance_lock is not None:
                self._instance_lock.bind_runtime_owner(
                    runtime_id=normalized,
                    target_generation=self._generation,
                )

    def acquire(self) -> None:
        with ADB_TARGET_OPERATION_LOCK:
            if self._instance_lock is not None:
                return
            instance_lock = self._lock_factory(self.target)
            instance_lock.acquire()
            next_generation = self._generation + 1
            try:
                instance_lock.bind_runtime_owner(
                    runtime_id=self._runtime_id,
                    target_generation=next_generation,
                )
            except Exception:
                instance_lock.release()
                raise
            os.environ["ADB_DEVICE"] = self.target
            self._instance_lock = instance_lock
            self._generation = next_generation

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

            next_generation = self._generation + 1
            try:
                replacement.bind_runtime_owner(
                    runtime_id=self._runtime_id,
                    target_generation=next_generation,
                )
            except Exception:
                if previous_environment is None:
                    os.environ.pop("ADB_DEVICE", None)
                else:
                    os.environ["ADB_DEVICE"] = previous_environment
                replacement.release()
                raise
            current_lock.release()
            self._instance_lock = replacement
            self.target = requested
            self._generation = next_generation
            return True

    def snapshot(self) -> AdbTargetSnapshot:
        """Return the exact target plus a handoff/release generation."""

        with ADB_TARGET_OPERATION_LOCK:
            return AdbTargetSnapshot(
                target=self.target,
                generation=self._generation,
                owned=self._instance_lock is not None,
            )

    def release(self) -> None:
        with ADB_TARGET_OPERATION_LOCK:
            instance_lock = self._instance_lock
            self._instance_lock = None
            if instance_lock is not None:
                instance_lock.release()
                self._generation += 1

    def __enter__(self) -> "AdbTargetSession":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


__all__ = ["ADB_TARGET_OPERATION_LOCK", "AdbTargetSession", "AdbTargetSnapshot"]
