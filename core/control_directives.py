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
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence
from uuid import uuid4

from core.app_setup import CONFIGURABLE_STRATEGIES
from core.gate_decisions import (
    STARTUP_GATE_CHECK_LABELS,
    VALID_GATE_DECISION_ACTIONS,
)


VALID_STATES = frozenset({"RUNNING", "PAUSED", "STOPPED"})
VALID_MODES = frozenset({"RETRY", "WAIT", "HOME"})
GATE_DECISION_STATUSES = frozenset({"pending", "resolved", "consumed"})


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
            "gate_decision": _valid_gate_decision(data.get("gate_decision")),
            "startup_gate_waivers": _valid_startup_gate_waivers(
                data.get("startup_gate_waivers")
            ),
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
            previous = _valid_strategy(data.get("strategy"))
            data["strategy"] = normalized
            if previous != normalized:
                data["startup_gate_waivers"] = {}
            data["updated_at"] = timestamp
            data["strategy_updated_at"] = timestamp
            data["strategy_request_id"] = uuid4().hex
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def publish_gate_decision(
        self,
        *,
        strategy: str,
        phase: str,
        check_id: str,
        reason: str,
        expected: object = None,
        options: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Publish one idempotent operator decision request from the runtime."""

        normalized_options = _valid_gate_options(options)
        if not normalized_options:
            raise ValueError("gate decision requires at least one valid option")
        normalized_strategy = _bounded_text(strategy, 100).lower()
        normalized_phase = _bounded_text(phase, 100).lower()
        normalized_check = _bounded_text(check_id, 100).lower()
        normalized_reason = _bounded_text(reason, 1000)
        if not normalized_strategy or not normalized_phase or not normalized_check:
            raise ValueError("gate decision requires strategy, phase, and check_id")

        with self._lock():
            current = self._read_unlocked()
            existing = _valid_gate_decision(current.get("gate_decision"))
            if (
                existing
                and existing["status"] in {"pending", "resolved"}
                and existing.get("strategy") == normalized_strategy
                and existing.get("phase") == normalized_phase
                and existing.get("check_id") == normalized_check
                and existing.get("reason") == normalized_reason
            ):
                return existing
            timestamp = _updated_at()
            directive: dict[str, Any] = {
                "request_id": uuid4().hex,
                "status": "pending",
                "strategy": normalized_strategy,
                "phase": normalized_phase,
                "check_id": normalized_check,
                "reason": normalized_reason,
                "options": normalized_options,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            expected_text = _bounded_text(expected, 500)
            if expected_text:
                directive["expected"] = expected_text
            current["gate_decision"] = directive
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-gate-decision"
            self._write_unlocked(current)
            return directive

    def resolve_gate_decision(
        self,
        request_id: str,
        decision_id: str,
        *,
        source: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Resolve a pending request with one option offered by the runtime."""

        with self._lock():
            current = self._read_unlocked()
            directive = _valid_gate_decision(current.get("gate_decision"))
            if (
                directive is None
                or directive["request_id"] != str(request_id)
                or directive["status"] != "pending"
            ):
                return None
            normalized_id = str(decision_id or "").strip().lower()
            selected = next(
                (
                    dict(option)
                    for option in directive["options"]
                    if option["id"] == normalized_id
                ),
                None,
            )
            if selected is None:
                raise ValueError(
                    f"gate decision {normalized_id!r} was not offered"
                )
            timestamp = _updated_at()
            directive.update(
                {
                    "status": "resolved",
                    "decision_id": normalized_id,
                    "selected_option": selected,
                    "resolved_at": timestamp,
                    "resolved_by": source,
                    "updated_at": timestamp,
                }
            )
            current["gate_decision"] = directive
            current["updated_at"] = timestamp
            current["updated_by"] = source or "gate-decision"
            self._write_unlocked(current)
            return directive

    def consume_gate_decision(
        self,
        request_id: str,
        *,
        completion_reason: str,
    ) -> Optional[dict[str, Any]]:
        """Mark a matching pending/resolved decision as durably consumed."""

        with self._lock():
            current = self._read_unlocked()
            directive = _valid_gate_decision(current.get("gate_decision"))
            if (
                directive is None
                or directive["request_id"] != str(request_id)
                or directive["status"] not in {"pending", "resolved"}
            ):
                return None
            timestamp = _updated_at()
            directive.update(
                {
                    "status": "consumed",
                    "completion_reason": _bounded_text(completion_reason, 500),
                    "consumed_at": timestamp,
                    "updated_at": timestamp,
                }
            )
            current["gate_decision"] = directive
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-gate-decision"
            self._write_unlocked(current)
            return directive

    def request_startup_gate_waiver(
        self,
        check_id: str,
        *,
        strategy: Optional[str] = None,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Stage one check-specific waiver for the next applicable run."""

        normalized_check = str(check_id or "").strip().lower()
        if normalized_check not in STARTUP_GATE_CHECK_LABELS:
            raise ValueError(
                f"Unsupported startup check {check_id!r}; expected one of "
                + ", ".join(STARTUP_GATE_CHECK_LABELS)
            )
        normalized_strategy = str(strategy or "").strip().lower()
        with self._lock():
            current = self._read_unlocked()
            waivers = _valid_startup_gate_waivers(
                current.get("startup_gate_waivers")
            )
            existing = waivers.get(normalized_check)
            if (
                existing
                and str(existing.get("strategy") or "") == normalized_strategy
            ):
                return existing
            timestamp = _updated_at()
            directive = {
                "request_id": uuid4().hex,
                "check_id": normalized_check,
                "label": STARTUP_GATE_CHECK_LABELS[normalized_check],
                "status": "pending",
                "strategy": normalized_strategy,
                "requested_at": timestamp,
                "requested_by": source,
            }
            waivers[normalized_check] = directive
            current["startup_gate_waivers"] = waivers
            current["updated_at"] = timestamp
            current["updated_by"] = source or "startup-gate-waiver"
            self._write_unlocked(current)
            return directive

    def cancel_startup_gate_waiver(
        self,
        check_id: str,
        *,
        source: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Cancel one still-pending proactive waiver."""

        normalized_check = str(check_id or "").strip().lower()
        with self._lock():
            current = self._read_unlocked()
            waivers = _valid_startup_gate_waivers(
                current.get("startup_gate_waivers")
            )
            removed = waivers.pop(normalized_check, None)
            if removed is None:
                return None
            timestamp = _updated_at()
            current["startup_gate_waivers"] = waivers
            current["updated_at"] = timestamp
            current["updated_by"] = source or "startup-gate-waiver"
            self._write_unlocked(current)
            return removed

    def configure_startup_gate_waivers(
        self,
        check_ids: Sequence[str],
        *,
        strategy: str,
        source: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        """Replace one strategy's staged skips as a single transaction."""

        normalized_strategy = str(strategy or "").strip().lower()
        selected = {str(check_id or "").strip().lower() for check_id in check_ids}
        unsupported = selected - set(STARTUP_GATE_CHECK_LABELS)
        if unsupported:
            raise ValueError(
                "Unsupported startup checks: " + ", ".join(sorted(unsupported))
            )
        with self._lock():
            current = self._read_unlocked()
            waivers = _valid_startup_gate_waivers(
                current.get("startup_gate_waivers")
            )
            existing_for_strategy = {
                check_id: waiver
                for check_id, waiver in waivers.items()
                if str(waiver.get("strategy") or "").strip().lower()
                == normalized_strategy
            }
            waivers = {
                check_id: waiver
                for check_id, waiver in waivers.items()
                if str(waiver.get("strategy") or "").strip().lower()
                != normalized_strategy
            }
            timestamp = _updated_at()
            configured: dict[str, dict[str, Any]] = {}
            for check_id in selected:
                directive = existing_for_strategy.get(check_id) or {
                    "request_id": uuid4().hex,
                    "check_id": check_id,
                    "label": STARTUP_GATE_CHECK_LABELS[check_id],
                    "status": "pending",
                    "strategy": normalized_strategy,
                    "requested_at": timestamp,
                    "requested_by": source,
                }
                waivers[check_id] = directive
                configured[check_id] = directive
            current["startup_gate_waivers"] = waivers
            current["updated_at"] = timestamp
            current["updated_by"] = source or "startup-gate-waiver"
            self._write_unlocked(current)
            return configured

    def claim_startup_gate_waivers(
        self,
        check_ids: Sequence[str],
        *,
        strategy: str,
    ) -> dict[str, dict[str, Any]]:
        """Atomically take pending waivers supported by the active strategy."""

        supported = {
            str(check_id or "").strip().lower()
            for check_id in check_ids
            if str(check_id or "").strip().lower() in STARTUP_GATE_CHECK_LABELS
        }
        if not supported:
            return {}
        with self._lock():
            current = self._read_unlocked()
            waivers = _valid_startup_gate_waivers(
                current.get("startup_gate_waivers")
            )
            claimed: dict[str, dict[str, Any]] = {}
            timestamp = _updated_at()
            for check_id in supported:
                directive = waivers.get(check_id)
                if directive is None:
                    continue
                waiver_strategy = (
                    str(directive.get("strategy") or "").strip().lower()
                )
                active_strategy = str(strategy or "").strip().lower()
                if waiver_strategy and waiver_strategy != active_strategy:
                    continue
                waivers.pop(check_id, None)
                claimed[check_id] = {
                    **directive,
                    "status": "claimed",
                    "strategy": active_strategy,
                    "claimed_at": timestamp,
                }
            if not claimed:
                return {}
            current["startup_gate_waivers"] = waivers
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-startup-gate-waiver"
            self._write_unlocked(current)
            return claimed

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


def _valid_gate_options(value: object) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        option_id = _bounded_text(raw.get("id"), 100).lower()
        label = _bounded_text(raw.get("label"), 300)
        action = _bounded_text(raw.get("action"), 50).lower()
        if (
            not option_id
            or option_id in seen
            or not label
            or action not in VALID_GATE_DECISION_ACTIONS
        ):
            continue
        option = {
            "id": option_id,
            "label": label,
            "action": action,
            "kind": _bounded_text(raw.get("kind"), 50).lower() or "standard",
        }
        description = _bounded_text(raw.get("description"), 500)
        value_text = _bounded_text(raw.get("value"), 300)
        if description:
            option["description"] = description
        if value_text:
            option["value"] = value_text
        options.append(option)
        seen.add(option_id)
    return options


def _valid_gate_decision(value: object) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    request_id = str(value.get("request_id") or "").strip()
    status = str(value.get("status") or "").strip().lower()
    strategy = _bounded_text(value.get("strategy"), 100).lower()
    phase = _bounded_text(value.get("phase"), 100).lower()
    check_id = _bounded_text(value.get("check_id"), 100).lower()
    options = _valid_gate_options(value.get("options"))
    if (
        not request_id
        or status not in GATE_DECISION_STATUSES
        or not strategy
        or not phase
        or not check_id
        or not options
    ):
        return None
    directive = dict(value)
    directive.update(
        request_id=request_id,
        status=status,
        strategy=strategy,
        phase=phase,
        check_id=check_id,
        reason=_bounded_text(value.get("reason"), 1000),
        options=options,
    )
    if status == "resolved":
        decision_id = _bounded_text(value.get("decision_id"), 100).lower()
        selected = next(
            (dict(option) for option in options if option["id"] == decision_id),
            None,
        )
        if selected is None:
            return None
        directive["decision_id"] = decision_id
        directive["selected_option"] = selected
    return directive


def _valid_startup_gate_waivers(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    waivers: dict[str, dict[str, Any]] = {}
    for raw_check, raw in value.items():
        check_id = str(raw_check or "").strip().lower()
        if check_id not in STARTUP_GATE_CHECK_LABELS or not isinstance(raw, Mapping):
            continue
        request_id = _bounded_text(raw.get("request_id"), 100)
        status = _bounded_text(raw.get("status"), 50).lower()
        if not request_id or status != "pending":
            continue
        waiver = dict(raw)
        waiver.update(
            request_id=request_id,
            check_id=check_id,
            label=STARTUP_GATE_CHECK_LABELS[check_id],
            status="pending",
            strategy=_bounded_text(raw.get("strategy"), 100).lower(),
        )
        waivers[check_id] = waiver
    return waivers


def _bounded_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _updated_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "ControlDirectiveError",
    "ControlDirectiveStore",
    "GATE_DECISION_STATUSES",
    "VALID_MODES",
    "VALID_STATES",
]
