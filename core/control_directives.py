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
from core.exclusive_validation import (
    exclusive_validation_definition_for_strategy,
)


VALID_STATES = frozenset({"RUNNING", "PAUSED", "STOPPED"})
VALID_MODES = frozenset({"RETRY", "WAIT", "HOME"})
VALID_GAME_SPEED_TARGETS = tuple(
    [step / 2 for step in range(13)] + [6.3]
)
MAXIMUM_GAME_SPEED_TARGET = 6.3
STRATEGY_APPLY_MODES = frozenset({"next_boundary", "active_battle"})
GATE_DECISION_STATUSES = frozenset({"pending", "resolved", "consumed"})
EXCLUSIVE_VALIDATION_STATUSES = frozenset(
    {"pending", "claimed", "running", "cleanup", "result"}
)
EXCLUSIVE_VALIDATION_OUTCOMES = frozenset({"ready", "failed", "cancelled"})
EXCLUSIVE_VALIDATION_LAUNCH_STATUSES = frozenset(
    {
        "awaiting_operator",
        "requested",
        "claimed",
        "started",
        "cancelled",
        "failed",
    }
)
_MAX_EXCLUSIVE_VALIDATION_RECEIPTS = 12


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
            "state_request_id": data.get("state_request_id"),
            "mode_updated_at": data.get("mode_updated_at"),
            "game_speed_target": _valid_game_speed_target(
                data.get("game_speed_target")
            ),
            "game_speed_target_updated_at": data.get(
                "game_speed_target_updated_at"
            ),
            "game_speed_target_request_id": data.get(
                "game_speed_target_request_id"
            ),
            "adb_port_updated_at": data.get("adb_port_updated_at"),
            "strategy": _valid_strategy(data.get("strategy")),
            "strategy_apply_mode": _valid_strategy_apply_mode(
                data.get("strategy_apply_mode")
            ),
            "strategy_updated_at": data.get("strategy_updated_at"),
            "strategy_request_id": data.get("strategy_request_id"),
            "gate_decision": _valid_gate_decision(data.get("gate_decision")),
            "startup_gate_waivers": _valid_startup_gate_waivers(
                data.get("startup_gate_waivers")
            ),
            "exclusive_validation": _valid_exclusive_validation_ledger(
                data.get("exclusive_validation")
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
            data["state_request_id"] = uuid4().hex
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

    def set_game_speed_target(
        self,
        target: object,
        *,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist one exact target or the x6.3 maximum-available target."""

        normalized = normalize_game_speed_target(target)

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            data["game_speed_target"] = normalized
            data["updated_at"] = timestamp
            data["game_speed_target_updated_at"] = timestamp
            data["game_speed_target_request_id"] = uuid4().hex
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def set_strategy(
        self,
        strategy: str,
        *,
        apply_mode: str = "next_boundary",
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a validated runtime strategy request."""

        normalized = str(strategy).strip().lower()
        if normalized not in CONFIGURABLE_STRATEGIES:
            raise ValueError(
                "Strategy must be one of: " + ", ".join(CONFIGURABLE_STRATEGIES)
            )
        normalized_apply_mode = str(apply_mode or "").strip().lower()
        if normalized_apply_mode not in STRATEGY_APPLY_MODES:
            raise ValueError(
                "Strategy apply mode must be one of: "
                + ", ".join(sorted(STRATEGY_APPLY_MODES))
            )
        validation_definition = (
            exclusive_validation_definition_for_strategy(normalized)
        )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            previous = _valid_strategy(data.get("strategy"))
            strategy_request_id = uuid4().hex
            data["strategy"] = normalized
            data["strategy_apply_mode"] = normalized_apply_mode
            if previous != normalized:
                data["startup_gate_waivers"] = {}
            data["updated_at"] = timestamp
            data["strategy_updated_at"] = timestamp
            data["strategy_request_id"] = strategy_request_id
            ledger = _valid_exclusive_validation_ledger(
                data.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            for request_id, receipt in list(receipts.items()):
                launch = receipt.get("launch")
                if (
                    isinstance(launch, Mapping)
                    and launch.get("status") in {"awaiting_operator", "requested"}
                ):
                    receipts[request_id] = {
                        **receipt,
                        "launch": {
                            **dict(launch),
                            "status": "cancelled",
                            "reason": (
                                f"superseded by explicit {normalized} "
                                "strategy request"
                            ),
                            "completed_at": timestamp,
                            "updated_at": timestamp,
                        },
                    }
            if validation_definition is not None:
                for request_id, receipt in list(receipts.items()):
                    if receipt["status"] != "pending":
                        continue
                    receipts[request_id] = {
                        **receipt,
                        "status": "result",
                        "outcome": "cancelled",
                        "reason": (
                            f"superseded by explicit {normalized} strategy request"
                        ),
                        "completed_at": timestamp,
                        "updated_at": timestamp,
                    }
                validation_request_id = uuid4().hex
                receipts[validation_request_id] = {
                    "request_id": validation_request_id,
                    "strategy_request_id": strategy_request_id,
                    "strategy": normalized,
                    "configuration_fingerprint": (
                        validation_definition.configuration_fingerprint
                    ),
                    "battle_kind": validation_definition.battle_kind,
                    "status": "pending",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                if validation_definition.operator_launch is not None:
                    launch = validation_definition.operator_launch
                    receipts[validation_request_id]["launch_policy"] = {
                        "kind": launch.kind,
                        "timeout_seconds": launch.timeout_seconds,
                        "prompt_title": launch.prompt_title,
                        "prompt_message": launch.prompt_message,
                        "reminder": launch.reminder,
                    }
                ledger["current_request_id"] = validation_request_id
            else:
                for request_id, receipt in list(receipts.items()):
                    if receipt["status"] != "pending":
                        continue
                    receipts[request_id] = {
                        **receipt,
                        "status": "result",
                        "outcome": "cancelled",
                        "reason": (
                            f"superseded by explicit {normalized} strategy request"
                        ),
                        "completed_at": timestamp,
                        "updated_at": timestamp,
                    }
                    ledger["current_request_id"] = request_id
            ledger["receipts"] = _prune_exclusive_validation_receipts(receipts)
            data["exclusive_validation"] = ledger
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def claim_exclusive_validation(
        self,
        *,
        strategy_request_id: str,
        configuration_fingerprint: str,
        owner: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Optional[dict[str, Any]]:
        """Atomically own one pending validation before its Battle tap."""

        normalized_owner = _valid_exclusive_validation_owner(owner)
        if normalized_owner is None:
            raise ValueError("exclusive validation owner is incomplete")
        request_identity = _bounded_text(strategy_request_id, 100)
        fingerprint = _bounded_text(configuration_fingerprint, 100)
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("exclusive validation timeout must be numeric") from exc
        if not request_identity or len(fingerprint) != 64:
            raise ValueError(
                "exclusive validation requires a strategy request and fingerprint"
            )
        if not 30.0 <= timeout <= 900.0:
            raise ValueError(
                "exclusive validation timeout must be between 30 and 900 seconds"
            )

        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = next(
                (
                    dict(candidate)
                    for candidate in receipts.values()
                    if candidate["strategy_request_id"] == request_identity
                ),
                None,
            )
            if (
                receipt is None
                or receipt["status"] != "pending"
                or receipt["configuration_fingerprint"] != fingerprint
            ):
                return None
            if any(
                candidate["status"] in {"claimed", "running", "cleanup"}
                for candidate in receipts.values()
                if candidate["request_id"] != receipt["request_id"]
            ):
                return None
            timestamp = _updated_at()
            now = datetime.now().timestamp()
            receipt.update(
                {
                    "status": "claimed",
                    "owner": normalized_owner,
                    "claimed_at": timestamp,
                    "deadline_at": now + timeout,
                    "updated_at": timestamp,
                }
            )
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            if ledger.get("current_request_id") not in receipts:
                ledger["current_request_id"] = receipt["request_id"]
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation"
            self._write_unlocked(current)
            return dict(receipt)

    def mark_exclusive_validation_running(
        self,
        request_id: str,
        *,
        owner: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Record the owned NEW_BATTLE transition after fresh RUNNING evidence."""

        return self._transition_owned_exclusive_validation(
            request_id,
            owner=owner,
            expected_status="claimed",
            status="running",
            fields={"started_at": _updated_at()},
        )

    def begin_exclusive_validation_cleanup(
        self,
        request_id: str,
        *,
        owner: Mapping[str, Any],
        outcome: str,
        reason: str,
    ) -> Optional[dict[str, Any]]:
        """Commit the intended result before the owned Surrender sequence."""

        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in {"ready", "failed"}:
            raise ValueError("validation cleanup outcome must be ready or failed")
        return self._transition_owned_exclusive_validation(
            request_id,
            owner=owner,
            expected_status="running",
            status="cleanup",
            fields={
                "pending_outcome": normalized_outcome,
                "pending_reason": _bounded_text(reason, 1000),
                "cleanup_started_at": _updated_at(),
            },
        )

    def finish_exclusive_validation(
        self,
        request_id: str,
        *,
        outcome: str,
        reason: str,
        owner: Optional[Mapping[str, Any]] = None,
        allowed_statuses: Sequence[str] = ("cleanup",),
    ) -> Optional[dict[str, Any]]:
        """Persist a conclusive result without broadening battle ownership."""

        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in EXCLUSIVE_VALIDATION_OUTCOMES:
            raise ValueError(
                "exclusive validation outcome must be ready, failed, or cancelled"
            )
        allowed = {
            str(status or "").strip().lower()
            for status in allowed_statuses
            if str(status or "").strip().lower()
            in EXCLUSIVE_VALIDATION_STATUSES
        }
        if not allowed:
            raise ValueError("exclusive validation result requires an allowed status")
        normalized_owner = (
            _valid_exclusive_validation_owner(owner)
            if owner is not None
            else None
        )
        if owner is not None and normalized_owner is None:
            raise ValueError("exclusive validation owner is incomplete")

        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            if receipt is None or receipt["status"] not in allowed:
                return None
            if receipt["status"] != "pending":
                if normalized_owner is None or receipt.get("owner") != normalized_owner:
                    return None
            timestamp = _updated_at()
            completed = {
                **receipt,
                "status": "result",
                "outcome": normalized_outcome,
                "reason": _bounded_text(reason, 1000),
                "completed_at": timestamp,
                "updated_at": timestamp,
            }
            completed.pop("pending_outcome", None)
            completed.pop("pending_reason", None)
            if normalized_outcome == "ready":
                launch_policy = _valid_exclusive_validation_launch_policy(
                    completed.get("launch_policy")
                )
                if launch_policy is not None:
                    completed["launch"] = {
                        "status": "awaiting_operator",
                        "updated_at": timestamp,
                    }
            else:
                completed.pop("launch", None)
            receipts[completed["request_id"]] = completed
            ledger["receipts"] = _prune_exclusive_validation_receipts(receipts)
            if ledger.get("current_request_id") not in ledger["receipts"]:
                ledger["current_request_id"] = completed["request_id"]
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation"
            self._write_unlocked(current)
            return dict(completed)

    def resolve_exclusive_validation_launch(
        self,
        request_id: str,
        decision: str,
        *,
        source: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Persist one explicit Start or Cancel decision for a ready receipt."""

        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"start", "cancel"}:
            raise ValueError(
                "exclusive validation launch decision must be start or cancel"
            )
        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            if (
                receipt is None
                or ledger.get("current_request_id") != str(request_id)
                or receipt.get("status") != "result"
                or receipt.get("outcome") != "ready"
                or receipt.get("strategy") != _valid_strategy(
                    current.get("strategy")
                )
                or receipt.get("strategy_request_id")
                != _bounded_text(current.get("strategy_request_id"), 100)
            ):
                return None
            definition = exclusive_validation_definition_for_strategy(
                str(receipt["strategy"])
            )
            if (
                definition is None
                or definition.operator_launch is None
                or receipt.get("configuration_fingerprint")
                != definition.configuration_fingerprint
            ):
                return None
            launch = _valid_exclusive_validation_launch(receipt.get("launch"))
            if launch is None or launch.get("status") != "awaiting_operator":
                return None
            timestamp = _updated_at()
            if normalized_decision == "start":
                launch = {
                    **launch,
                    "status": "requested",
                    "launch_request_id": uuid4().hex,
                    "requested_at": timestamp,
                    "updated_at": timestamp,
                }
            else:
                launch = {
                    **launch,
                    "status": "cancelled",
                    "reason": (
                        "operator cancelled automatic Tournament launch"
                    ),
                    "completed_at": timestamp,
                    "updated_at": timestamp,
                }
            if source:
                launch["updated_by"] = source
            receipt = {**receipt, "launch": launch, "updated_at": timestamp}
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            if source:
                current["updated_by"] = source
            self._write_unlocked(current)
            return dict(receipt)

    def claim_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        configuration_fingerprint: str,
        owner: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Atomically consume one Start decision before its first device tap."""

        normalized_owner = _valid_exclusive_validation_owner(owner)
        if normalized_owner is None:
            raise ValueError("exclusive validation launch owner is incomplete")
        fingerprint = _bounded_text(configuration_fingerprint, 100)
        if len(fingerprint) != 64:
            raise ValueError(
                "exclusive validation launch requires a configuration fingerprint"
            )
        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            launch = (
                _valid_exclusive_validation_launch(receipt.get("launch"))
                if receipt
                else None
            )
            policy = (
                _valid_exclusive_validation_launch_policy(
                    receipt.get("launch_policy")
                )
                if receipt
                else None
            )
            if (
                receipt is None
                or ledger.get("current_request_id") != str(request_id)
                or receipt.get("status") != "result"
                or receipt.get("outcome") != "ready"
                or receipt.get("configuration_fingerprint") != fingerprint
                or receipt.get("strategy") != _valid_strategy(
                    current.get("strategy")
                )
                or receipt.get("strategy_request_id")
                != _bounded_text(current.get("strategy_request_id"), 100)
                or launch is None
                or launch.get("status") != "requested"
                or policy is None
            ):
                return None
            timestamp = _updated_at()
            launch = {
                **launch,
                "status": "claimed",
                "owner": normalized_owner,
                "claimed_at": timestamp,
                "deadline_at": (
                    datetime.now().timestamp()
                    + float(policy["timeout_seconds"])
                ),
                "updated_at": timestamp,
            }
            receipt = {**receipt, "launch": launch, "updated_at": timestamp}
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation-launch"
            self._write_unlocked(current)
            return dict(receipt)

    def finish_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        owner: Mapping[str, Any],
        outcome: str,
        reason: str,
    ) -> Optional[dict[str, Any]]:
        """Finish only the launch claimed by the same live runtime owner."""

        normalized_owner = _valid_exclusive_validation_owner(owner)
        if normalized_owner is None:
            raise ValueError("exclusive validation launch owner is incomplete")
        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in {"started", "failed"}:
            raise ValueError(
                "exclusive validation launch outcome must be started or failed"
            )
        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            launch = (
                _valid_exclusive_validation_launch(receipt.get("launch"))
                if receipt
                else None
            )
            if (
                receipt is None
                or launch is None
                or launch.get("status") != "claimed"
                or launch.get("owner") != normalized_owner
            ):
                return None
            timestamp = _updated_at()
            launch = {
                **launch,
                "status": normalized_outcome,
                "reason": _bounded_text(reason, 1000),
                "completed_at": timestamp,
                "updated_at": timestamp,
            }
            receipt = {**receipt, "launch": launch, "updated_at": timestamp}
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation-launch"
            self._write_unlocked(current)
            return dict(receipt)

    def record_manual_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        reason: str,
    ) -> Optional[dict[str, Any]]:
        """Consume an unclaimed prompt after a fresh manual battle start."""

        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            launch = (
                _valid_exclusive_validation_launch(receipt.get("launch"))
                if receipt
                else None
            )
            if (
                receipt is None
                or launch is None
                or launch.get("status") not in {
                    "awaiting_operator",
                    "requested",
                }
            ):
                return None
            timestamp = _updated_at()
            launch = {
                **launch,
                "status": "started",
                "reason": _bounded_text(reason, 1000),
                "started_by": "manual_observation",
                "completed_at": timestamp,
                "updated_at": timestamp,
            }
            receipt = {**receipt, "launch": launch, "updated_at": timestamp}
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-manual-tournament-observation"
            self._write_unlocked(current)
            return dict(receipt)

    def fail_unclaimed_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        reason: str,
    ) -> Optional[dict[str, Any]]:
        """Fail a launch that has not acquired device-input ownership."""

        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            launch = (
                _valid_exclusive_validation_launch(receipt.get("launch"))
                if receipt
                else None
            )
            if (
                receipt is None
                or launch is None
                or launch.get("status") not in {
                    "awaiting_operator",
                    "requested",
                }
            ):
                return None
            timestamp = _updated_at()
            launch = {
                **launch,
                "status": "failed",
                "reason": _bounded_text(reason, 1000),
                "completed_at": timestamp,
                "updated_at": timestamp,
            }
            receipt = {**receipt, "launch": launch, "updated_at": timestamp}
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation-launch"
            self._write_unlocked(current)
            return dict(receipt)

    def fail_orphaned_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        current_owner: Mapping[str, Any],
        reason: str,
    ) -> Optional[dict[str, Any]]:
        """Fail a prior runtime's claimed launch without sending more input."""

        normalized_owner = _valid_exclusive_validation_owner(current_owner)
        if normalized_owner is None:
            raise ValueError("exclusive validation launch owner is incomplete")
        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            launch = (
                _valid_exclusive_validation_launch(receipt.get("launch"))
                if receipt
                else None
            )
            if (
                receipt is None
                or launch is None
                or launch.get("status") != "claimed"
                or launch.get("owner") == normalized_owner
            ):
                return None
            timestamp = _updated_at()
            launch = {
                **launch,
                "status": "failed",
                "reason": _bounded_text(reason, 1000),
                "completed_at": timestamp,
                "updated_at": timestamp,
            }
            receipt = {**receipt, "launch": launch, "updated_at": timestamp}
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation-launch-orphan"
            self._write_unlocked(current)
            return dict(receipt)

    def fail_orphaned_exclusive_validation(
        self,
        request_id: str,
        *,
        current_owner: Mapping[str, Any],
        reason: str,
    ) -> Optional[dict[str, Any]]:
        """Fail a prior runtime's active receipt without touching its battle."""

        normalized_owner = _valid_exclusive_validation_owner(current_owner)
        if normalized_owner is None:
            raise ValueError("exclusive validation owner is incomplete")
        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            if (
                receipt is None
                or receipt["status"] not in {"claimed", "running", "cleanup"}
                or receipt.get("owner") == normalized_owner
            ):
                return None
            timestamp = _updated_at()
            failed = {
                **receipt,
                "status": "result",
                "outcome": "failed",
                "reason": _bounded_text(reason, 1000),
                "completed_at": timestamp,
                "updated_at": timestamp,
            }
            failed.pop("pending_outcome", None)
            failed.pop("pending_reason", None)
            receipts[failed["request_id"]] = failed
            ledger["receipts"] = _prune_exclusive_validation_receipts(receipts)
            if ledger.get("current_request_id") not in ledger["receipts"]:
                ledger["current_request_id"] = failed["request_id"]
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation-orphan"
            self._write_unlocked(current)
            return dict(failed)

    def _transition_owned_exclusive_validation(
        self,
        request_id: str,
        *,
        owner: Mapping[str, Any],
        expected_status: str,
        status: str,
        fields: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        normalized_owner = _valid_exclusive_validation_owner(owner)
        if normalized_owner is None:
            raise ValueError("exclusive validation owner is incomplete")
        with self._lock():
            current = self._read_unlocked()
            ledger = _valid_exclusive_validation_ledger(
                current.get("exclusive_validation")
            )
            receipts = dict(ledger["receipts"])
            receipt = receipts.get(str(request_id))
            if (
                receipt is None
                or receipt["status"] != expected_status
                or receipt.get("owner") != normalized_owner
            ):
                return None
            timestamp = _updated_at()
            receipt = {
                **receipt,
                **dict(fields),
                "status": status,
                "updated_at": timestamp,
            }
            receipts[receipt["request_id"]] = receipt
            ledger["receipts"] = receipts
            if ledger.get("current_request_id") not in receipts:
                ledger["current_request_id"] = receipt["request_id"]
            current["exclusive_validation"] = ledger
            current["updated_at"] = timestamp
            current["updated_by"] = "runtime-exclusive-validation"
            self._write_unlocked(current)
            return dict(receipt)

    def publish_gate_decision(
        self,
        *,
        strategy: str,
        phase: str,
        check_id: str,
        reason: str,
        expected: object = None,
        options: Sequence[Mapping[str, Any]],
        blocking: bool = True,
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
                and bool(existing.get("blocking", True)) == bool(blocking)
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
                "blocking": bool(blocking),
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
            current["state_request_id"] = uuid4().hex
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


def normalize_game_speed_target(value: object) -> float:
    """Return one supported game-speed target or raise ``ValueError``."""

    if isinstance(value, bool):
        raise ValueError("Game-speed target must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Game-speed target must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError("Game-speed target must be finite")
    for target in VALID_GAME_SPEED_TARGETS:
        if math.isclose(parsed, target, abs_tol=1e-9):
            return target
    choices = ", ".join(f"{target:.1f}" for target in VALID_GAME_SPEED_TARGETS)
    raise ValueError(f"Unsupported game-speed target {value!r}; expected {choices}")


def _valid_game_speed_target(value: object) -> float:
    try:
        return normalize_game_speed_target(value)
    except ValueError:
        return MAXIMUM_GAME_SPEED_TARGET


def _valid_strategy_apply_mode(value: object) -> str:
    normalized = str(value or "next_boundary").strip().lower()
    return normalized if normalized in STRATEGY_APPLY_MODES else "next_boundary"


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
        blocking=value.get("blocking") is not False,
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


def _valid_exclusive_validation_owner(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    runtime_id = _bounded_text(value.get("runtime_id"), 100)
    adb_target = _bounded_text(value.get("adb_target"), 200)
    pid = value.get("pid")
    if (
        not runtime_id
        or not adb_target
        or isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
    ):
        return None
    return {
        "runtime_id": runtime_id,
        "pid": pid,
        "adb_target": adb_target,
    }


def _valid_exclusive_validation_launch_policy(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    kind = _bounded_text(value.get("kind"), 100).lower()
    timeout_seconds = _finite_number(value.get("timeout_seconds"))
    prompt_title = _bounded_text(value.get("prompt_title"), 200)
    prompt_message = _bounded_text(value.get("prompt_message"), 1000)
    reminder = _bounded_text(value.get("reminder"), 1000)
    if (
        kind != "tournament_battle"
        or timeout_seconds is None
        or not 30.0 <= timeout_seconds <= 120.0
        or not prompt_title
        or not prompt_message
        or not reminder
    ):
        return None
    return {
        "kind": kind,
        "timeout_seconds": timeout_seconds,
        "prompt_title": prompt_title,
        "prompt_message": prompt_message,
        "reminder": reminder,
    }


def _valid_exclusive_validation_launch(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    status = _bounded_text(value.get("status"), 50).lower()
    if status not in EXCLUSIVE_VALIDATION_LAUNCH_STATUSES:
        return None
    launch = dict(value)
    launch["status"] = status
    if status in {"requested", "claimed"}:
        launch_request_id = _bounded_text(
            value.get("launch_request_id"), 100
        )
        if not launch_request_id:
            return None
        launch["launch_request_id"] = launch_request_id
    if status == "claimed":
        owner = _valid_exclusive_validation_owner(value.get("owner"))
        deadline_at = _finite_number(value.get("deadline_at"))
        if owner is None or deadline_at is None:
            return None
        launch["owner"] = owner
        launch["deadline_at"] = deadline_at
    if status in {"started", "cancelled", "failed"}:
        launch["reason"] = _bounded_text(value.get("reason"), 1000)
    return launch


def _valid_exclusive_validation_receipt(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    request_id = _bounded_text(value.get("request_id"), 100)
    strategy_request_id = _bounded_text(value.get("strategy_request_id"), 100)
    strategy = _valid_strategy(value.get("strategy"))
    fingerprint = _bounded_text(value.get("configuration_fingerprint"), 100)
    battle_kind = _bounded_text(value.get("battle_kind"), 100).lower()
    status = _bounded_text(value.get("status"), 50).lower()
    if (
        not request_id
        or not strategy_request_id
        or strategy is None
        or len(fingerprint) != 64
        or battle_kind != "ordinary_new_battle"
        or status not in EXCLUSIVE_VALIDATION_STATUSES
    ):
        return None
    receipt = dict(value)
    receipt.update(
        {
            "request_id": request_id,
            "strategy_request_id": strategy_request_id,
            "strategy": strategy,
            "configuration_fingerprint": fingerprint,
            "battle_kind": battle_kind,
            "status": status,
        }
    )
    launch_policy = _valid_exclusive_validation_launch_policy(
        value.get("launch_policy")
    )
    if launch_policy is not None:
        receipt["launch_policy"] = launch_policy
    if status in {"claimed", "running", "cleanup"}:
        owner = _valid_exclusive_validation_owner(value.get("owner"))
        deadline_at = _finite_number(value.get("deadline_at"))
        if owner is None or deadline_at is None:
            return None
        receipt["owner"] = owner
        receipt["deadline_at"] = deadline_at
    if status == "cleanup":
        pending_outcome = _bounded_text(
            value.get("pending_outcome"), 50
        ).lower()
        if pending_outcome not in {"ready", "failed"}:
            return None
        receipt["pending_outcome"] = pending_outcome
        receipt["pending_reason"] = _bounded_text(
            value.get("pending_reason"), 1000
        )
    if status == "result":
        outcome = _bounded_text(value.get("outcome"), 50).lower()
        if outcome not in EXCLUSIVE_VALIDATION_OUTCOMES:
            return None
        receipt["outcome"] = outcome
        receipt["reason"] = _bounded_text(value.get("reason"), 1000)
        launch = _valid_exclusive_validation_launch(value.get("launch"))
        if (
            outcome == "ready"
            and launch_policy is not None
            and launch is not None
        ):
            receipt["launch"] = launch
    return receipt


def _valid_exclusive_validation_ledger(value: object) -> dict[str, Any]:
    receipts: dict[str, dict[str, Any]] = {}
    current_request_id = ""
    if isinstance(value, Mapping):
        raw_receipts = value.get("receipts")
        if isinstance(raw_receipts, Mapping):
            for raw_id, raw_receipt in raw_receipts.items():
                receipt = _valid_exclusive_validation_receipt(raw_receipt)
                if receipt is None or receipt["request_id"] != str(raw_id):
                    continue
                receipts[receipt["request_id"]] = receipt
        candidate = _bounded_text(value.get("current_request_id"), 100)
        if candidate in receipts:
            current_request_id = candidate
    if not current_request_id and receipts:
        current_request_id = max(
            receipts,
            key=lambda request_id: str(
                receipts[request_id].get("updated_at")
                or receipts[request_id].get("created_at")
                or ""
            ),
        )
    return {
        "schema_version": 1,
        "current_request_id": current_request_id or None,
        "receipts": receipts,
    }


def _prune_exclusive_validation_receipts(
    receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    copied = {
        str(request_id): dict(receipt)
        for request_id, receipt in receipts.items()
    }
    if len(copied) <= _MAX_EXCLUSIVE_VALIDATION_RECEIPTS:
        return copied
    active = {
        request_id: receipt
        for request_id, receipt in copied.items()
        if receipt.get("status") != "result"
    }
    completed = sorted(
        (
            (request_id, receipt)
            for request_id, receipt in copied.items()
            if receipt.get("status") == "result"
        ),
        key=lambda item: str(
            item[1].get("completed_at")
            or item[1].get("updated_at")
            or ""
        ),
        reverse=True,
    )
    remaining = max(0, _MAX_EXCLUSIVE_VALIDATION_RECEIPTS - len(active))
    return {
        **active,
        **dict(completed[:remaining]),
    }


def _bounded_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _updated_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "ControlDirectiveError",
    "ControlDirectiveStore",
    "EXCLUSIVE_VALIDATION_OUTCOMES",
    "EXCLUSIVE_VALIDATION_STATUSES",
    "GATE_DECISION_STATUSES",
    "MAXIMUM_GAME_SPEED_TARGET",
    "VALID_GAME_SPEED_TARGETS",
    "VALID_MODES",
    "VALID_STATES",
    "normalize_game_speed_target",
]
