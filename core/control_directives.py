"""Persistent automation-control directives shared by CLI and GUI adapters."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterator, Mapping, Optional
from uuid import uuid4

from core.app_setup import CONFIGURABLE_STRATEGIES


VALID_STATES = frozenset({"RUNNING", "PAUSED", "STOPPED"})
VALID_MODES = frozenset({"RETRY", "WAIT", "HOME"})


class ControlDirectiveError(RuntimeError):
    """Raised when authoritative control state cannot be read or persisted."""


class ControlDirectiveStore:
    """Atomically read and update one persistent control file.

    A companion advisory lock serializes writers using this class. The JSON
    file remains the sole authority consumed by the automation runtime.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.write.lock")

    def read(self) -> dict[str, Any]:
        """Return a copy of the current mapping; a missing file is empty."""

        with self._lock():
            return self._read_unlocked()

    def status(self, *, now: Optional[float] = None) -> dict[str, Any]:
        """Return normalized state, mode, target, and timed-pause information."""

        data = self.read()
        state = str(data.get("state") or "RUNNING").upper()
        mode = str(data.get("mode") or "RETRY").upper()
        resume_at = _finite_number(data.get("resume_at"))
        current_time = float(now) if now is not None else datetime.now().timestamp()
        remaining_seconds = None
        if resume_at is not None:
            remaining_seconds = max(0, round(resume_at - current_time))
        return {
            "state": state,
            "mode": mode,
            "adb_port": _valid_port(data.get("adb_port")),
            "resume_at": resume_at,
            "remaining_seconds": remaining_seconds,
            "updated_at": data.get("updated_at"),
            "updated_by": data.get("updated_by"),
            "state_updated_at": data.get("state_updated_at"),
            "mode_updated_at": data.get("mode_updated_at"),
            "adb_port_updated_at": data.get("adb_port_updated_at"),
            "strategy": _valid_strategy(data.get("strategy")),
            "strategy_updated_at": data.get("strategy_updated_at"),
            "strategy_request_id": data.get("strategy_request_id"),
            "path": str(self.path),
            "exists": self.path.exists(),
        }

    def set_state(
        self,
        state: str,
        *,
        resume_at: Optional[float] = None,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a validated run state while preserving unrelated fields."""

        normalized = str(state).strip().upper()
        if normalized not in VALID_STATES:
            raise ValueError(
                f"Unsupported automation state {state!r}; "
                f"expected one of {sorted(VALID_STATES)}"
            )
        deadline = _finite_number(resume_at)
        if resume_at is not None and deadline is None:
            raise ValueError("resume_at must be a finite timestamp")
        if normalized != "PAUSED" and deadline is not None:
            raise ValueError("resume_at is only valid for PAUSED")

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            data["state"] = normalized
            data.pop("resume_at", None)
            if deadline is not None:
                data["resume_at"] = deadline
            data["updated_at"] = timestamp
            data["state_updated_at"] = timestamp
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def set_adb_port(
        self,
        port: int,
        *,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a validated localhost ADB-port handoff request."""

        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError("ADB port must be an integer")
        if not 1 <= port <= 65535:
            raise ValueError("ADB port must be between 1 and 65535")

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            data["adb_port"] = port
            data["updated_at"] = timestamp
            data["adb_port_updated_at"] = timestamp
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def set_mode(self, mode: str, *, source: Optional[str] = None) -> dict[str, Any]:
        """Persist a validated execution mode while preserving state."""

        normalized = str(mode).strip().upper()
        if normalized not in VALID_MODES:
            raise ValueError(
                f"Unsupported automation mode {mode!r}; "
                f"expected one of {sorted(VALID_MODES)}"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            data["mode"] = normalized
            data["updated_at"] = timestamp
            data["mode_updated_at"] = timestamp
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def set_strategy(
        self,
        strategy: str,
        *,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a validated runtime strategy request."""

        normalized = str(strategy).strip().lower()
        if normalized not in CONFIGURABLE_STRATEGIES:
            raise ValueError(
                "Strategy must be one of: " + ", ".join(CONFIGURABLE_STRATEGIES)
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            data["strategy"] = normalized
            data["updated_at"] = timestamp
            data["strategy_updated_at"] = timestamp
            data["strategy_request_id"] = uuid4().hex
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def replace(self, directives: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically replace the mapping after validating its shape."""

        replacement = dict(directives)
        with self._lock():
            self._write_unlocked(replacement)
        return replacement

    def update(
        self,
        mutator: Callable[[dict[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Run a read-modify-write transaction under the writer lock."""

        with self._lock():
            current = self._read_unlocked()
            updated = dict(mutator(dict(current)))
            self._write_unlocked(updated)
            return updated

    def resume_expired_pause(
        self,
        *,
        expected_resume_at: float,
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Resume only if the same timed pause remains expired.

        Revalidating under the writer lock ensures a concurrent pause extension
        or replacement with an indefinite pause wins over a stale deadline.
        """

        current_time = datetime.now().timestamp() if now is None else float(now)
        with self._lock():
            current = self._read_unlocked()
            state = str(current.get("state") or "").upper()
            deadline = _finite_number(current.get("resume_at"))
            if (
                state != "PAUSED"
                or deadline != float(expected_resume_at)
                or current_time < deadline
            ):
                return None
            current["state"] = "RUNNING"
            current.pop("resume_at", None)
            timestamp = _updated_at()
            current["updated_at"] = timestamp
            current["state_updated_at"] = timestamp
            current["updated_by"] = "timed-pause-expiry"
            self._write_unlocked(current)
            return current

    @contextmanager
    def _lock(self) -> Iterator[None]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            raise ControlDirectiveError(
                f"Unable to lock control file {self.path}: {exc}"
            ) from exc

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlDirectiveError(
                f"Unable to read control file {self.path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ControlDirectiveError(
                f"Control file {self.path} must contain a JSON object"
            )
        return dict(data)

    def _write_unlocked(self, data: Mapping[str, Any]) -> None:
        temp_name: Optional[str] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(dict(data), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except (OSError, TypeError, ValueError) as exc:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise ControlDirectiveError(
                f"Unable to write control file {self.path}: {exc}"
            ) from exc


def _finite_number(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _valid_port(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 65535 else None


def _valid_strategy(value: object) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in CONFIGURABLE_STRATEGIES else None


def _updated_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "ControlDirectiveError",
    "ControlDirectiveStore",
    "VALID_MODES",
    "VALID_STATES",
]
