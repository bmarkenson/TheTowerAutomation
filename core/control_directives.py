"""Persistent automation-control directives shared by CLI and GUI adapters."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence
from uuid import UUID, uuid4

from core.gate_decisions import (
    STARTUP_GATE_CHECK_LABELS,
    VALID_GATE_DECISION_ACTIONS,
)
from core.control_model import (
    BATTLE_INTENTS,
    BATTLE_WORKFLOW_TERMINAL_STATUSES,
    HOME_RETURN_RECONCILIATION_KIND,
    MANUAL_SURRENDER_COLLECTIONS,
    TERMINAL_RETURN_RECONCILIATION_KIND,
    MANUAL_CONTROL_TERMINAL_STATUSES,
    SETUP_CAPTURE_AUTHORITY_OUTCOMES,
    SETUP_CAPTURE_GAME_STATES,
    SETUP_CAPTURE_TERMINAL_STATUSES,
    intent_matches_evidence,
    validate_battle_workflow,
    validate_manual_control,
    validate_manual_terminal_evidence,
    validate_process_restart_handoff,
    validate_save_reconciliation_receipt,
    validate_setup_capture,
    validate_setup_capture_preview,
    validate_workflow_evidence,
)
from core.exclusive_validation import (
    exclusive_validation_definition_for_strategy,
)
from core.emulator_recovery import normalize_emulator_maintenance
from core.dispatch_control_boundary import (
    DispatchControlBoundaryError,
    dispatch_control_boundary,
    dispatch_lock_path_for,
)
from core.strategy_profiles import (
    BUILTIN_STRATEGY_IDS,
    configurable_strategy_ids,
    is_configurable_strategy,
    normalize_strategy_id,
    strategy_profile_directory,
)


VALID_STATES = frozenset({"RUNNING", "PAUSED", "STOPPED"})
VALID_MODES = frozenset({"NEXT_BATTLE", "WAIT", "HOME"})
LEGACY_MODE_ALIASES = {"RETRY": "NEXT_BATTLE"}
DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60
DEFAULT_IDLE_TIMEOUT_STRATEGY = "farm_t19_ad_assist"
TERMINAL_IDLE_TIMEOUT_SCHEMA_VERSION = 1
TERMINAL_IDLE_TIMEOUT_STATUSES = frozenset({"holding", "returning_home"})
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
INTERACTIVE_DEVELOPMENT_LEASE_SCHEMA_VERSION = 1
INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS = 120
INTERACTIVE_DEVELOPMENT_REQUEST_STATES = frozenset(
    {"requested", "release_requested", "terminal"}
)
_MAX_EXCLUSIVE_VALIDATION_RECEIPTS = 12
EMULATOR_LOCATION_SCHEMA_VERSION = 1


def normalize_terminal_idle_timeout(value: object) -> Optional[dict[str, Any]]:
    """Validate one exact-request terminal/Home timeout hold."""

    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") != TERMINAL_IDLE_TIMEOUT_SCHEMA_VERSION:
        return None
    request_id = str(value.get("request_id") or "").strip()
    state_request_id = str(value.get("state_request_id") or "").strip()
    mode_request_id = str(value.get("mode_request_id") or "").strip()
    policy = str(value.get("policy") or "").strip().upper()
    status = str(value.get("status") or "").strip().lower()
    strategy = normalize_strategy_id(value.get("strategy"))
    expires_at = _finite_number(value.get("expires_at"))
    evidence = validate_workflow_evidence(value.get("evidence"))
    if (
        not request_id
        or len(request_id) > 128
        or not state_request_id
        or len(state_request_id) > 128
        or not mode_request_id
        or len(mode_request_id) > 128
        or policy not in {"WAIT", "HOME"}
        or status not in TERMINAL_IDLE_TIMEOUT_STATUSES
        or strategy is None
        or expires_at is None
        or evidence is None
        or evidence.get("game_state")
        not in {"game_over", "tournament_results", "home_new_battle"}
    ):
        return None
    return {
        "schema_version": TERMINAL_IDLE_TIMEOUT_SCHEMA_VERSION,
        "request_id": request_id,
        "state_request_id": state_request_id,
        "mode_request_id": mode_request_id,
        "policy": policy,
        "status": status,
        "strategy": strategy,
        "activated_at": value.get("activated_at"),
        "expires_at": expires_at,
        "evidence": evidence,
    }


def _emulator_location_text(value: object, maximum: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 for character in normalized)
    ):
        return None
    return normalized


def _normalize_emulator_location_identity(
    value: object,
) -> Optional[dict[str, Any]]:
    """Validate one Windows host plus its exact local BlueStacks listener."""

    if not isinstance(value, Mapping):
        return None
    schema_version = value.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or schema_version != EMULATOR_LOCATION_SCHEMA_VERSION
    ):
        return None
    raw_host_id = _emulator_location_text(value.get("host_id"), 64)
    if raw_host_id is None:
        return None
    try:
        host_id = str(UUID(raw_host_id))
    except (TypeError, ValueError):
        return None
    host_name = _emulator_location_text(value.get("host_name"), 128)
    linux_adb_port = _valid_port(value.get("linux_adb_port"))
    listener = value.get("bluestacks_listener")
    if (
        not host_name
        or linux_adb_port is None
        or not isinstance(listener, Mapping)
    ):
        return None
    windows_adb_port = _valid_port(listener.get("adb_port"))
    process_id = listener.get("process_id")
    raw_process_started_at = listener.get("process_started_at")
    raw_executable_path = listener.get("executable_path")
    process_started_at = _emulator_location_text(
        raw_process_started_at,
        64,
    )
    executable_path = _emulator_location_text(raw_executable_path, 1024)
    instance_name = _emulator_location_text(
        listener.get("instance_name"), 64
    )
    if windows_adb_port is None or not instance_name:
        return None
    process_fields_present = any(
        candidate is not None and candidate != ""
        for candidate in (
            process_id,
            raw_process_started_at,
            raw_executable_path,
        )
    )
    if process_fields_present:
        try:
            _timestamp_value(process_started_at)
        except (TypeError, ValueError):
            return None
        if (
            isinstance(process_id, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
            or not executable_path
        ):
            return None
    normalized_listener: dict[str, Any] = {
        "adb_port": windows_adb_port,
        "instance_name": instance_name,
    }
    if process_fields_present:
        normalized_listener.update(
            {
                "process_id": process_id,
                "process_started_at": process_started_at,
                "executable_path": executable_path,
            }
        )
    return {
        "schema_version": EMULATOR_LOCATION_SCHEMA_VERSION,
        "host_id": host_id,
        "host_name": host_name,
        "linux_adb_port": linux_adb_port,
        "bluestacks_listener": normalized_listener,
    }


def normalize_emulator_location(value: object) -> Optional[dict[str, Any]]:
    """Return one durable, exact Windows-emulator selection."""

    normalized = _normalize_emulator_location_identity(value)
    if normalized is None or not isinstance(value, Mapping):
        return None
    request_id = _emulator_location_text(value.get("request_id"), 128)
    selected_at = _emulator_location_text(value.get("selected_at"), 64)
    if (
        not request_id
        or any(
            not character.isascii()
            or not (character.isalnum() or character in "._:-")
            for character in request_id
        )
    ):
        return None
    try:
        _timestamp_value(selected_at)
    except (TypeError, ValueError):
        return None
    return {
        **normalized,
        "request_id": request_id,
        "selected_at": selected_at,
    }


def normalize_emulator_location_request(
    value: object,
) -> Optional[dict[str, Any]]:
    """Return the identity portion supplied by a Windows selection request."""

    return _normalize_emulator_location_identity(value)


def normalize_automation_mode(value: object) -> str:
    """Return one canonical terminal disposition, accepting legacy Retry."""

    normalized = str(value or "").strip().upper().replace("-", "_")
    normalized = LEGACY_MODE_ALIASES.get(normalized, normalized)
    if normalized not in VALID_MODES:
        raise ValueError(
            f"Unsupported automation mode {value!r}; "
            f"expected one of {sorted(VALID_MODES)}"
        )
    return normalized


class ControlDirectiveError(RuntimeError):
    """Raised when authoritative control state cannot be read or persisted."""


class ControlDirectiveStore:
    """Atomically read and update one persistent control file.

    A companion advisory lock serializes writers using this class. The JSON
    file remains the sole authority consumed by the automation runtime.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        strategy_profile_dir: Path | str | None = None,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.write.lock")
        self.dispatch_lock_path = dispatch_lock_path_for(self.path)
        self.strategy_profile_dir = strategy_profile_directory(
            strategy_profile_dir
        )

    def read(self) -> dict[str, Any]:
        """Return a copy of the current mapping; a missing file is empty."""

        with self._lock():
            return self._read_unlocked()

    def status(self, *, now: Optional[float] = None) -> dict[str, Any]:
        """Return normalized state, mode, target, and timed-pause information."""

        data = self.read()
        state = str(data.get("state") or "PAUSED").upper()
        mode = str(data.get("mode") or "NEXT_BATTLE").upper()
        resume_at = _finite_number(data.get("resume_at"))
        emulator_location = normalize_emulator_location(
            data.get("emulator_location")
        )
        current_time = float(now) if now is not None else datetime.now().timestamp()
        remaining_seconds = None
        if resume_at is not None:
            remaining_seconds = max(0, round(resume_at - current_time))
        terminal_idle_timeout = normalize_terminal_idle_timeout(
            data.get("terminal_idle_timeout")
        )
        terminal_idle_remaining_seconds = None
        if terminal_idle_timeout is not None:
            terminal_idle_remaining_seconds = max(
                0,
                round(terminal_idle_timeout["expires_at"] - current_time),
            )
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
            "mode_request_id": data.get("mode_request_id"),
            "idle_timeout_seconds": DEFAULT_IDLE_TIMEOUT_SECONDS,
            "idle_timeout_strategy": DEFAULT_IDLE_TIMEOUT_STRATEGY,
            "terminal_idle_timeout": terminal_idle_timeout,
            "terminal_idle_remaining_seconds": (
                terminal_idle_remaining_seconds
            ),
            "terminal_idle_timeout_error": (
                "terminal idle-timeout directive is malformed"
                if data.get("terminal_idle_timeout") is not None
                and terminal_idle_timeout is None
                else None
            ),
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
            "adb_port_request_id": data.get("adb_port_request_id"),
            "emulator_location": emulator_location,
            "emulator_location_error": (
                "emulator location directive is malformed"
                if data.get("emulator_location") is not None
                and emulator_location is None
                else None
            ),
            "strategy": self._valid_strategy(data.get("strategy")),
            "strategy_apply_mode": _valid_strategy_apply_mode(
                data.get("strategy_apply_mode")
            ),
            "strategy_updated_at": data.get("strategy_updated_at"),
            "strategy_request_id": data.get("strategy_request_id"),
            "strategy_active_battle_identity": (
                _valid_active_battle_identity(
                    data.get("strategy_active_battle_identity")
                )
                if _valid_strategy_apply_mode(
                    data.get("strategy_apply_mode")
                )
                == "active_battle"
                else None
            ),
            "gate_decision": _valid_gate_decision(data.get("gate_decision")),
            "startup_gate_waivers": _valid_startup_gate_waivers(
                data.get("startup_gate_waivers")
            ),
            "exclusive_validation": _valid_exclusive_validation_ledger(
                data.get("exclusive_validation")
            ),
            "interactive_development_lease": (
                _valid_interactive_development_lease(
                    data.get("interactive_development_lease")
                )
            ),
            "interactive_development_lease_error": (
                "interactive development lease directive is malformed"
                if data.get("interactive_development_lease") is not None
                and _valid_interactive_development_lease(
                    data.get("interactive_development_lease")
                )
                is None
                else None
            ),
            "emulator_maintenance": normalize_emulator_maintenance(
                data.get("emulator_maintenance")
            ),
            "emulator_maintenance_error": (
                "emulator maintenance directive is malformed"
                if data.get("emulator_maintenance") is not None
                and normalize_emulator_maintenance(
                    data.get("emulator_maintenance")
                )
                is None
                else None
            ),
            "battle_workflow": validate_battle_workflow(
                data.get("battle_workflow")
            ),
            "battle_workflow_error": (
                "battle workflow directive is malformed"
                if data.get("battle_workflow") is not None
                and validate_battle_workflow(data.get("battle_workflow")) is None
                else None
            ),
            "process_restart_handoff": validate_process_restart_handoff(
                data.get("process_restart_handoff")
            ),
            "process_restart_handoff_error": (
                "process restart handoff is malformed"
                if data.get("process_restart_handoff") is not None
                and validate_process_restart_handoff(
                    data.get("process_restart_handoff")
                )
                is None
                else None
            ),
            "manual_control": validate_manual_control(
                data.get("manual_control")
            ),
            "manual_control_error": (
                "manual control directive is malformed"
                if data.get("manual_control") is not None
                and validate_manual_control(data.get("manual_control")) is None
                else None
            ),
            "setup_capture": validate_setup_capture(data.get("setup_capture")),
            "setup_capture_error": (
                "setup capture directive is malformed"
                if data.get("setup_capture") is not None
                and validate_setup_capture(data.get("setup_capture")) is None
                else None
            ),
            "path": str(self.path),
            "exists": self.path.exists(),
        }

    def ensure_request_identities(self) -> dict[str, str]:
        """Materialize implicit defaults and add exact IDs to legacy fields."""

        fields = (
            ("state", "state_request_id"),
            ("mode", "mode_request_id"),
            ("game_speed_target", "game_speed_target_request_id"),
            ("adb_port", "adb_port_request_id"),
            ("strategy", "strategy_request_id"),
        )
        implicit_defaults: dict[str, object] = {
            # An absent legacy control file has no proven action authority.
            # Materialize a safe Pause and require one explicit Enable rather
            # than manufacturing RUNNING authority during process startup.
            "state": "PAUSED",
            "mode": "NEXT_BATTLE",
            "game_speed_target": MAXIMUM_GAME_SPEED_TARGET,
        }

        def valid_request_id(value: object) -> bool:
            if not isinstance(value, str):
                return False
            normalized = value.strip()
            return bool(
                normalized
                and len(normalized) <= 128
                and all(
                    character.isascii()
                    and (character.isalnum() or character in "._:-")
                    for character in normalized
                )
            )

        with self._dispatch_boundary():
            with self._lock():
                current = self._read_unlocked()
                added: dict[str, str] = {}
                for value_field, identity_field in fields:
                    if value_field not in current:
                        if value_field not in implicit_defaults:
                            continue
                        current[value_field] = implicit_defaults[value_field]
                    elif current.get(value_field) is None:
                        # A present malformed value must reach validation. Do
                        # not silently turn damaged authority into permission.
                        continue

                    identity_present = identity_field in current
                    identity_value = current.get(identity_field)
                    if valid_request_id(identity_value):
                        continue
                    if identity_present and identity_value is not None:
                        # Preserve malformed-present identities so startup
                        # rejects them instead of laundering them into a new
                        # request that appears operator-authored.
                        continue
                    if (
                        value_field == "state"
                        and isinstance(current.get(value_field), str)
                        and str(current[value_field]).strip().upper()
                        == "RUNNING"
                    ):
                        # A legacy RUNNING value without an exact request ID is
                        # not enough authority for a fresh process to act.
                        # Convert it to a new explicit safe Pause; the operator
                        # can then issue a real Enable with its own identity.
                        current[value_field] = "PAUSED"
                        current.pop("resume_at", None)
                    request_id = uuid4().hex
                    current[identity_field] = request_id
                    added[identity_field] = request_id
                if added:
                    self._write_unlocked(current)
                return added

    def request_battle_workflow(
        self,
        intent: str,
        *,
        evidence: Mapping[str, object],
        strategy: Optional[str] = None,
        terminal_idle_timeout_request_id: Optional[str] = None,
        process_restart_handoff_id: Optional[str] = None,
        source: Optional[str] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Persist one exact-evidence-bound Start or Attach intent."""

        normalized_intent = str(intent or "").strip().lower()
        if normalized_intent not in BATTLE_INTENTS:
            raise ValueError("Battle intent must be start_battle or attach_battle")
        normalized_evidence = validate_workflow_evidence(evidence)
        if normalized_evidence is None:
            raise ValueError(
                "Battle workflow requires fresh exact runtime observation evidence"
            )
        if not intent_matches_evidence(normalized_intent, normalized_evidence):
            raise ValueError(
                f"{normalized_intent} does not match observed game state "
                f"{normalized_evidence['game_state']}"
            )
        normalized_strategy = (
            str(strategy).strip().lower() if strategy is not None else None
        )
        normalized_restart_handoff_id = (
            str(process_restart_handoff_id or "").strip() or None
        )
        normalized_terminal_timeout_id = (
            str(terminal_idle_timeout_request_id or "").strip() or None
        )
        if (
            normalized_terminal_timeout_id is not None
            and normalized_intent != "start_battle"
        ):
            raise ValueError(
                "A terminal idle timeout may authorize only Start Battle"
            )
        if (
            normalized_restart_handoff_id is not None
            and normalized_intent != "attach_battle"
        ):
            raise ValueError(
                "A process restart handoff may bind only an Attach workflow"
            )
        if normalized_strategy is not None and not is_configurable_strategy(
            normalized_strategy,
            self.strategy_profile_dir,
            allow_legacy_aliases=False,
        ):
            raise ValueError(
                "Strategy must be one of: "
                + ", ".join(
                    configurable_strategy_ids(self.strategy_profile_dir)
                )
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            terminal_timeout = None
            if normalized_terminal_timeout_id is not None:
                terminal_timeout = normalize_terminal_idle_timeout(
                    data.get("terminal_idle_timeout")
                )
                current_time = (
                    float(now)
                    if now is not None
                    else datetime.now().timestamp()
                )
                if (
                    terminal_timeout is None
                    or terminal_timeout["request_id"]
                    != normalized_terminal_timeout_id
                    or terminal_timeout["expires_at"] > current_time
                    or str(data.get("state") or "").strip().upper()
                    != "RUNNING"
                    or str(data.get("state_request_id") or "").strip()
                    != terminal_timeout["state_request_id"]
                    or str(data.get("mode_request_id") or "").strip()
                    != terminal_timeout["mode_request_id"]
                    or normalized_evidence.get("game_state")
                    != "home_new_battle"
                    or normalized_strategy != terminal_timeout["strategy"]
                ):
                    raise ValueError(
                        "Terminal idle timeout no longer authorizes this Start"
                    )
            raw_current = data.get("battle_workflow")
            current = validate_battle_workflow(raw_current)
            if raw_current is not None and current is None:
                raise ValueError(
                    "Existing battle workflow directive is malformed; it was preserved"
                )
            if (
                current is not None
                and current["status"] not in BATTLE_WORKFLOW_TERMINAL_STATUSES
            ):
                raise ValueError("A battle workflow request is already in progress")
            raw_manual = data.get("manual_control")
            manual = validate_manual_control(raw_manual)
            if raw_manual is not None and manual is None:
                raise ValueError(
                    "Existing manual control directive is malformed; it was preserved"
                )
            if (
                manual is not None
                and manual["status"] not in MANUAL_CONTROL_TERMINAL_STATUSES
            ):
                raise ValueError(
                    "Return control before requesting a battle workflow"
                )
            capture = validate_setup_capture(data.get("setup_capture"))
            if data.get("setup_capture") is not None and capture is None:
                raise ValueError(
                    "Existing setup capture directive is malformed; it was preserved"
                )
            if capture is not None and capture["status"] in {
                "requested",
                "acknowledged",
                "capturing",
            }:
                raise ValueError("Setup capture currently owns device input")
            timestamp = _timestamp_at(now)
            restart_handoff = None
            if normalized_restart_handoff_id is not None:
                restart_handoff = validate_process_restart_handoff(
                    data.get("process_restart_handoff")
                )
                if (
                    restart_handoff is None
                    or restart_handoff["handoff_id"]
                    != normalized_restart_handoff_id
                    or restart_handoff["status"] != "pending"
                    or restart_handoff.get("workflow_id") is not None
                    or restart_handoff["source_evidence"].get("adb_target")
                    != normalized_evidence.get("adb_target")
                ):
                    raise ValueError(
                        "Process restart handoff no longer matches this Attach request"
                    )
            workflow_strategy: Optional[str] = None
            workflow_strategy_request_id: Optional[str] = None
            workflow_strategy_definition_fingerprint: Optional[str] = None
            if normalized_intent == "start_battle":
                effective_strategy = (
                    normalized_strategy
                    or self._valid_strategy(data.get("strategy"))
                )
                if effective_strategy is not None:
                    previous = self._valid_strategy(data.get("strategy"))
                    strategy_request_id = uuid4().hex
                    data["strategy"] = effective_strategy
                    data["strategy_apply_mode"] = "next_boundary"
                    if previous != effective_strategy:
                        data["startup_gate_waivers"] = {}
                    data["strategy_updated_at"] = timestamp
                    data["strategy_request_id"] = strategy_request_id
                    self._replace_exclusive_validation_request(
                        data,
                        strategy=effective_strategy,
                        strategy_request_id=strategy_request_id,
                        timestamp=timestamp,
                        superseded_reason=(
                            "replaced by explicit Start Battle for "
                            f"{effective_strategy}"
                        ),
                    )
                    workflow_strategy = effective_strategy
                    workflow_strategy_request_id = strategy_request_id
            else:
                # Attach adopts the Strategy that the control store has
                # accepted at this exact write boundary.  The caller's value
                # is only a fallback for older/empty stores; it must never
                # override a newer accepted selection observed under the lock.
                accepted_strategy = self._valid_strategy(data.get("strategy"))
                workflow_strategy = accepted_strategy or normalized_strategy
                if accepted_strategy == workflow_strategy:
                    candidate_request_id = str(
                        data.get("strategy_request_id") or ""
                    ).strip()
                    if candidate_request_id:
                        workflow_strategy_request_id = candidate_request_id
                if (
                    workflow_strategy not in {None, "none"}
                    and workflow_strategy_request_id is not None
                ):
                    workflow_strategy_definition_fingerprint = (
                        self._strategy_definition_fingerprint(
                            workflow_strategy
                        )
                    )
            workflow = {
                "schema_version": 1,
                "request_id": uuid4().hex,
                "intent": normalized_intent,
                "status": "requested",
                "requested_at": timestamp,
                "updated_at": timestamp,
                "evidence": normalized_evidence,
                "updated_by": source or "operator",
            }
            if workflow_strategy is not None:
                workflow["strategy"] = workflow_strategy
            if workflow_strategy_request_id is not None:
                workflow["strategy_request_id"] = (
                    workflow_strategy_request_id
                )
            if workflow_strategy_definition_fingerprint is not None:
                workflow["strategy_definition_fingerprint"] = (
                    workflow_strategy_definition_fingerprint
                )
            data["battle_workflow"] = workflow
            if terminal_timeout is not None:
                data["mode"] = "NEXT_BATTLE"
                data["mode_updated_at"] = timestamp
                data["mode_request_id"] = uuid4().hex
                data.pop("terminal_idle_timeout", None)
            if restart_handoff is not None:
                restart_handoff["workflow_id"] = workflow["request_id"]
                restart_handoff["updated_at"] = timestamp
                restart_handoff["updated_by"] = (
                    source or "runtime-process-restart"
                )
                data["process_restart_handoff"] = restart_handoff
            data["updated_at"] = timestamp
            if source:
                data["updated_by"] = source
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["battle_workflow"])

    def transition_battle_workflow(
        self,
        request_id: str,
        status: str,
        *,
        reason: Optional[str] = None,
        acknowledgement: Optional[Mapping[str, object]] = None,
        save_receipt: Optional[Mapping[str, object]] = None,
        configuration: Optional[Mapping[str, object]] = None,
        source: str = "runtime",
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Advance only the matching workflow through an allowed transition."""

        normalized_id = str(request_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        transitions = {
            "requested": {
                "acknowledged",
                "awaiting_enable",
                "validating_save",
                "rejected",
                "interrupted",
                "failed",
            },
            "acknowledged": {
                "awaiting_enable",
                "validating_save",
                "awaiting_configuration",
                "ready",
                "action_dispatched",
                "completed",
                "rejected",
                "interrupted",
                "failed",
            },
            "awaiting_enable": {
                "acknowledged",
                "validating_save",
                "interrupted",
                "rejected",
                "failed",
            },
            "validating_save": {
                "awaiting_configuration",
                "ready",
                "completed",
                "action_dispatched",
                "interrupted",
                "failed",
            },
            "awaiting_configuration": {
                "validating_save",
                "ready",
                "interrupted",
                "failed",
            },
            "ready": {
                "action_dispatched",
                "completed",
                "interrupted",
                "failed",
            },
            "action_dispatched": {
                "ready",
                "completed",
                "interrupted",
                "failed",
            },
        }

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            workflow = validate_battle_workflow(data.get("battle_workflow"))
            if workflow is None or workflow["request_id"] != normalized_id:
                return data
            current_status = str(workflow["status"])
            if current_status == normalized_status:
                return data
            if normalized_status not in transitions.get(current_status, set()):
                raise ValueError(
                    "Battle workflow cannot transition from "
                    f"{current_status} to {normalized_status}"
                )
            if (
                workflow["intent"] == "attach_battle"
                and normalized_status == "ready"
                and validate_save_reconciliation_receipt(
                    save_receipt,
                    expected_kind="running_attachment_reconciliation",
                    expected_workflow_id=normalized_id,
                )
                is None
            ):
                raise ValueError(
                    "Attach cannot become ready without a typed reconciliation receipt"
                )
            timestamp = _timestamp_at(now)
            workflow["status"] = normalized_status
            workflow["updated_at"] = timestamp
            workflow["updated_by"] = source
            if reason:
                workflow["reason"] = _bounded_text(reason, 512)
            if acknowledgement is not None:
                workflow["acknowledgement"] = dict(acknowledgement)
            if save_receipt is not None:
                workflow["save_receipt"] = dict(save_receipt)
            if configuration is not None:
                workflow["configuration"] = dict(configuration)
            if normalized_status in {
                "acknowledged",
                "awaiting_enable",
                "validating_save",
            } and "acknowledged_at" not in workflow:
                workflow["acknowledged_at"] = timestamp
            if normalized_status in BATTLE_WORKFLOW_TERMINAL_STATUSES:
                workflow["completed_at"] = timestamp
            data["battle_workflow"] = workflow
            data["updated_at"] = timestamp
            data["updated_by"] = source
            return data

        saved = self.update(mutate)
        workflow = validate_battle_workflow(saved.get("battle_workflow"))
        if workflow is None or workflow["request_id"] != normalized_id:
            return None
        return workflow

    def request_manual_control(
        self,
        *,
        evidence: Mapping[str, object],
        reason: str = "operator",
        surrender_collection: str = "minimal",
        source: Optional[str] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Request an indefinite Pause before yielding device input."""

        normalized_evidence = validate_workflow_evidence(evidence)
        normalized_reason = str(reason or "").strip().lower()
        normalized_collection = str(
            surrender_collection or ""
        ).strip().lower()
        if normalized_evidence is None:
            raise ValueError(
                "Manual control requires fresh exact runtime observation evidence"
            )
        if normalized_reason not in {"operator", "unexpected_manual_activity"}:
            raise ValueError("Unsupported manual-control reason")
        if normalized_collection not in MANUAL_SURRENDER_COLLECTIONS:
            raise ValueError(
                "Manual surrender collection must be minimal or full"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            if str(data.get("state") or "").strip().upper() == "STOPPED":
                raise ValueError(
                    "Start Automation before requesting manual control"
                )
            raw_manual = data.get("manual_control")
            current = validate_manual_control(raw_manual)
            if raw_manual is not None and current is None:
                raise ValueError(
                    "Existing manual control directive is malformed; it was preserved"
                )
            if (
                current is not None
                and current["status"] not in MANUAL_CONTROL_TERMINAL_STATUSES
            ):
                raise ValueError("Manual control is already in progress")
            capture = validate_setup_capture(data.get("setup_capture"))
            if data.get("setup_capture") is not None and capture is None:
                raise ValueError(
                    "Existing setup capture directive is malformed; it was preserved"
                )
            if capture is not None and capture["status"] in {
                "requested",
                "acknowledged",
                "capturing",
            }:
                raise ValueError("Setup capture currently owns device input")
            timestamp = _timestamp_at(now)
            workflow = validate_battle_workflow(data.get("battle_workflow"))
            if (
                workflow is not None
                and workflow["status"] not in BATTLE_WORKFLOW_TERMINAL_STATUSES
            ):
                workflow.update(
                    {
                        "status": "interrupted",
                        "reason": "manual control took input authority",
                        "updated_at": timestamp,
                        "completed_at": timestamp,
                        "updated_by": source or "operator",
                    }
                )
                data["battle_workflow"] = workflow
            manual = {
                "schema_version": 1,
                "manual_control_id": uuid4().hex,
                "status": "pause_requested",
                "reason": normalized_reason,
                "surrender_collection": normalized_collection,
                "requested_at": timestamp,
                "updated_at": timestamp,
                "starting_evidence": normalized_evidence,
                "refresh_status": "awaiting_pause_acknowledgement",
                "updated_by": source or "operator",
            }
            data["manual_control"] = manual
            data["state"] = "PAUSED"
            data.pop("resume_at", None)
            data.pop("terminal_idle_timeout", None)
            data["state_updated_at"] = timestamp
            data["state_request_id"] = uuid4().hex
            data["updated_at"] = timestamp
            data["updated_by"] = source or "operator"
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["manual_control"])

    def request_setup_capture(
        self,
        *,
        evidence: Mapping[str, object],
        source_manual_control_id: Optional[str] = None,
        source: Optional[str] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Request one runtime-owned forced-save setup projection."""

        normalized_evidence = validate_workflow_evidence(evidence)
        if (
            normalized_evidence is None
            or normalized_evidence.get("game_state")
            not in SETUP_CAPTURE_GAME_STATES
        ):
            raise ValueError(
                "Setup capture requires fresh Home New, Home Resume, or active-battle evidence"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            raw_capture = data.get("setup_capture")
            current = validate_setup_capture(raw_capture)
            if raw_capture is not None and current is None:
                raise ValueError(
                    "Existing setup capture directive is malformed; it was preserved"
                )
            if (
                current is not None
                and current["status"] not in SETUP_CAPTURE_TERMINAL_STATUSES
            ):
                raise ValueError(
                    "Save or cancel the current setup capture before requesting another"
                )
            workflow = validate_battle_workflow(data.get("battle_workflow"))
            if (
                workflow is not None
                and workflow["status"] not in BATTLE_WORKFLOW_TERMINAL_STATUSES
            ):
                raise ValueError(
                    "Complete the current battle workflow before capturing setup"
                )
            manual = validate_manual_control(data.get("manual_control"))
            normalized_manual_id = str(
                source_manual_control_id or ""
            ).strip()
            retained_return_receipt = (
                validate_save_reconciliation_receipt(
                    manual.get("save_receipt"),
                    expected_kind="return_control_reconciliation",
                    expected_workflow_id=normalized_manual_id,
                )
                if normalized_manual_id and manual is not None
                else None
            )
            retained_configuration = (
                retained_return_receipt.get("configuration")
                if isinstance(retained_return_receipt, Mapping)
                else None
            )
            retained_return_source = bool(
                normalized_manual_id
                and manual is not None
                and manual.get("manual_control_id") == normalized_manual_id
                and manual.get("status") == "awaiting_configuration"
                and manual.get("refresh_status") == "trusted_mismatch_paused"
                and isinstance(retained_configuration, Mapping)
                and retained_configuration.get("status") == "partial"
                and bool(retained_configuration.get("unresolved_check_ids"))
            )
            if normalized_manual_id and not retained_return_source:
                raise ValueError(
                    "Setup capture may reuse only the exact retained forced "
                    "save from a trusted-mismatch Return Control workflow"
                )
            if (
                manual is not None
                and manual["status"] not in MANUAL_CONTROL_TERMINAL_STATUSES
                and not retained_return_source
            ):
                raise ValueError(
                    "Return control before capturing the current setup"
                )
            timestamp = _timestamp_at(now)
            capture = {
                "schema_version": 1,
                "request_id": uuid4().hex,
                "status": "requested",
                "requested_at": timestamp,
                "updated_at": timestamp,
                "evidence": normalized_evidence,
                "acquisition_source": (
                    "retained_return_control_refresh"
                    if retained_return_source
                    else "new_setup_capture_refresh"
                ),
                "updated_by": source or "operator",
            }
            if retained_return_source:
                capture["source_manual_control_id"] = normalized_manual_id
            data["setup_capture"] = capture
            data["updated_at"] = timestamp
            data["updated_by"] = source or "operator"
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["setup_capture"])

    def transition_setup_capture(
        self,
        request_id: str,
        status: str,
        *,
        reason: Optional[str] = None,
        acknowledgement: Optional[Mapping[str, object]] = None,
        preview: Optional[Mapping[str, object]] = None,
        saved_result: Optional[Mapping[str, object]] = None,
        authority_outcome: Optional[str] = None,
        source: str = "runtime",
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Advance only the exact current capture through its typed ledger."""

        normalized_id = str(request_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        transitions = {
            "requested": {
                "acknowledged",
                "unavailable",
                "interrupted",
                "failed",
                "cancelled",
            },
            "acknowledged": {
                "capturing",
                "unavailable",
                "interrupted",
                "failed",
                "cancelled",
            },
            "capturing": {
                "ready",
                "unavailable",
                "interrupted",
                "failed",
            },
            "ready": {"saved", "cancelled", "interrupted", "failed"},
        }

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            capture = validate_setup_capture(data.get("setup_capture"))
            if capture is None or capture["request_id"] != normalized_id:
                return data
            current_status = str(capture["status"])
            if current_status == normalized_status:
                return data
            if normalized_status not in transitions.get(current_status, set()):
                raise ValueError(
                    "Setup capture cannot transition from "
                    f"{current_status} to {normalized_status}"
                )
            timestamp = _timestamp_at(now)
            capture["status"] = normalized_status
            capture["updated_at"] = timestamp
            capture["updated_by"] = source
            if reason:
                capture["reason"] = _bounded_text(reason, 512)
            if acknowledgement is not None:
                capture["acknowledgement"] = dict(acknowledgement)
            if preview is not None:
                if normalized_status != "ready":
                    raise ValueError(
                        "A setup capture preview is valid only for the ready transition"
                    )
                normalized_preview = validate_setup_capture_preview(
                    preview,
                    evidence=capture.get("evidence"),
                )
                if normalized_preview is None:
                    raise ValueError(
                        "Setup capture preview lacks exact forced-save workflow evidence"
                    )
                capture["preview"] = normalized_preview
                capture["preview_fingerprint"] = _mapping_fingerprint(
                    normalized_preview
                )
            if saved_result is not None:
                capture["saved_result"] = dict(saved_result)
            if authority_outcome is not None:
                normalized_authority = str(authority_outcome).strip().lower()
                if normalized_authority not in SETUP_CAPTURE_AUTHORITY_OUTCOMES:
                    raise ValueError(
                        "Setup capture authority outcome is invalid"
                    )
                capture["authority_outcome"] = normalized_authority
            if normalized_status in {"acknowledged", "capturing"} and (
                "acknowledged_at" not in capture
            ):
                capture["acknowledged_at"] = timestamp
            if normalized_status in SETUP_CAPTURE_TERMINAL_STATUSES:
                capture["completed_at"] = timestamp
            data["setup_capture"] = capture
            data["updated_at"] = timestamp
            data["updated_by"] = source
            return data

        saved = self.update(mutate)
        capture = validate_setup_capture(saved.get("setup_capture"))
        if capture is None or capture["request_id"] != normalized_id:
            return None
        return capture

    def record_manual_terminal_evidence(
        self,
        manual_control_id: str,
        evidence: Mapping[str, object],
        *,
        source: str = "runtime",
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Persist one exact-run terminal classification without changing input."""

        normalized_id = str(manual_control_id or "").strip()
        normalized_evidence = validate_manual_terminal_evidence(
            evidence,
            expected_workflow_id=normalized_id,
        )
        if normalized_evidence is None:
            raise ValueError("Manual terminal evidence is invalid")

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            manual = validate_manual_control(data.get("manual_control"))
            if manual is None or manual["manual_control_id"] != normalized_id:
                return data
            if manual["status"] in MANUAL_CONTROL_TERMINAL_STATUSES:
                return data
            existing_evidence = manual.get("terminal_evidence")
            if isinstance(existing_evidence, Mapping):
                if existing_evidence.get("status") != "unavailable":
                    return data
                if normalized_evidence.get("status") == "unavailable":
                    return data
            timestamp = _timestamp_at(now)
            manual["terminal_evidence"] = normalized_evidence
            manual["refresh_status"] = (
                "manual_terminal_confirmed"
                if normalized_evidence["status"] != "unavailable"
                else "manual_terminal_evidence_unavailable"
            )
            manual["updated_at"] = timestamp
            manual["updated_by"] = source
            data["manual_control"] = manual
            data["updated_at"] = timestamp
            data["updated_by"] = source
            return data

        saved = self.update(mutate)
        manual = validate_manual_control(saved.get("manual_control"))
        return dict(manual) if manual is not None else None

    def request_return_control(
        self,
        manual_control_id: str,
        *,
        evidence: Mapping[str, object],
        source: Optional[str] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Request reconciliation while preserving the acknowledged Pause."""

        normalized_id = str(manual_control_id or "").strip()
        normalized_evidence = validate_workflow_evidence(evidence)
        if normalized_evidence is None:
            raise ValueError(
                "Return Control requires fresh exact runtime observation evidence"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            manual = validate_manual_control(data.get("manual_control"))
            if manual is None or manual["manual_control_id"] != normalized_id:
                raise ValueError("Manual control request is no longer current")
            if manual["status"] != "active":
                raise ValueError(
                    "Return Control requires acknowledged active manual control"
                )
            if str(data.get("state") or "").upper() != "PAUSED" or data.get(
                "resume_at"
            ) is not None:
                raise ValueError(
                    "Return Control requires the acknowledged indefinite Pause"
                )
            timestamp = _timestamp_at(now)
            manual.update(
                {
                    "status": "return_requested",
                    "return_requested_at": timestamp,
                    "return_evidence": normalized_evidence,
                    "refresh_status": "observation_refresh_requested",
                    "updated_at": timestamp,
                    "updated_by": source or "operator",
                }
            )
            data["manual_control"] = manual
            data["updated_at"] = timestamp
            data["updated_by"] = source or "operator"
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["manual_control"])

    def transition_manual_control(
        self,
        manual_control_id: str,
        status: str,
        *,
        detail: Optional[str] = None,
        refresh_status: Optional[str] = None,
        pause_acknowledgement: Optional[Mapping[str, object]] = None,
        save_receipt: Optional[Mapping[str, object]] = None,
        configuration: Optional[Mapping[str, object]] = None,
        source: str = "runtime",
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Advance only the matching Take/Return Control workflow."""

        normalized_id = str(manual_control_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        transitions = {
            "pause_requested": {"active", "interrupted", "failed"},
            "active": {"return_requested", "interrupted", "failed"},
            "return_requested": {
                "awaiting_enable",
                "reconciling",
                "interrupted",
                "failed",
            },
            "awaiting_enable": {"reconciling", "interrupted", "failed"},
            "reconciling": {
                "awaiting_configuration",
                "awaiting_manual_correction",
                "completed",
                "interrupted",
                "failed",
            },
            "awaiting_configuration": {
                "awaiting_manual_correction",
                "reconciling",
                "completed",
                "interrupted",
                "failed",
            },
            "awaiting_manual_correction": {
                "reconciling",
                "interrupted",
                "failed",
            },
        }

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            manual = validate_manual_control(data.get("manual_control"))
            if manual is None or manual["manual_control_id"] != normalized_id:
                return data
            current_status = str(manual["status"])
            if current_status == normalized_status:
                return data
            if normalized_status not in transitions.get(current_status, set()):
                raise ValueError(
                    "Manual control cannot transition from "
                    f"{current_status} to {normalized_status}"
                )
            if normalized_status == "completed":
                validated_receipt = validate_save_reconciliation_receipt(
                    save_receipt,
                    expected_workflow_id=normalized_id,
                )
                if (
                    validated_receipt is None
                    or validated_receipt.get("kind")
                    not in {
                        "return_control_reconciliation",
                        HOME_RETURN_RECONCILIATION_KIND,
                        TERMINAL_RETURN_RECONCILIATION_KIND,
                    }
                ):
                    raise ValueError(
                        "Return Control cannot complete without a typed reconciliation receipt"
                    )
            timestamp = _timestamp_at(now)
            manual["status"] = normalized_status
            manual["updated_at"] = timestamp
            manual["updated_by"] = source
            if detail:
                manual["detail"] = _bounded_text(detail, 512)
            if refresh_status:
                manual["refresh_status"] = _bounded_text(refresh_status, 128)
            if pause_acknowledgement is not None:
                manual["pause_acknowledgement"] = dict(pause_acknowledgement)
            if save_receipt is not None:
                manual["save_receipt"] = dict(save_receipt)
            if configuration is not None:
                manual["configuration"] = dict(configuration)
            if normalized_status == "active":
                manual["acknowledged_at"] = timestamp
            if normalized_status in MANUAL_CONTROL_TERMINAL_STATUSES:
                manual["completed_at"] = timestamp
            data["manual_control"] = manual
            data["updated_at"] = timestamp
            data["updated_by"] = source
            return data

        saved = self.update(mutate)
        manual = validate_manual_control(saved.get("manual_control"))
        if manual is None or manual["manual_control_id"] != normalized_id:
            return None
        return manual

    def enable_after_return_control(
        self,
        manual_control_id: str,
        *,
        source: Optional[str] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Atomically request Enable and mark Return Control pending refresh."""

        normalized_id = str(manual_control_id or "").strip()

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            manual = validate_manual_control(data.get("manual_control"))
            if manual is None or manual["manual_control_id"] != normalized_id:
                raise ValueError("Manual control request is no longer current")
            if manual["status"] not in {
                "return_requested",
                "awaiting_enable",
                "awaiting_configuration",
                "awaiting_manual_correction",
            }:
                raise ValueError(
                    "Use Return Control before enabling automated actions"
                )
            if (
                manual["status"] == "awaiting_enable"
                and str(data.get("state") or "").upper() == "RUNNING"
                and data.get("resume_at") is None
            ):
                return data
            timestamp = _timestamp_at(now)
            retrying_configuration = manual["status"] in {
                "awaiting_configuration",
                "awaiting_manual_correction",
            }
            retry_status = str(manual["status"])
            manual.update(
                {
                    "status": (
                        retry_status
                        if retrying_configuration
                        else "awaiting_enable"
                    ),
                    "refresh_status": (
                        "configuration_retry_after_enable"
                        if retrying_configuration
                        else "save_refresh_pending_after_enable"
                    ),
                    "updated_at": timestamp,
                    "updated_by": source or "operator",
                }
            )
            data["manual_control"] = manual
            data["state"] = "RUNNING"
            data.pop("resume_at", None)
            data["state_updated_at"] = timestamp
            data["state_request_id"] = uuid4().hex
            data["updated_at"] = timestamp
            data["updated_by"] = source or "operator"
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["manual_control"])

    @staticmethod
    def _interrupt_operator_workflows_data(
        data: dict[str, Any],
        *,
        reason: str,
        source: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """Apply workflow interruption inside an existing atomic update."""

        raw_workflow = data.get("battle_workflow")
        workflow = validate_battle_workflow(raw_workflow)
        if workflow is None and raw_workflow is not None:
            workflow = _interrupted_battle_workflow_from_envelope(
                raw_workflow,
                reason=reason,
                source=source,
                timestamp=timestamp,
            )
            if workflow is not None:
                data["battle_workflow"] = workflow
        if (
            workflow is not None
            and workflow["status"] not in BATTLE_WORKFLOW_TERMINAL_STATUSES
        ):
            workflow.update(
                {
                    "status": "interrupted",
                    "reason": reason,
                    "updated_at": timestamp,
                    "completed_at": timestamp,
                    "updated_by": source,
                }
            )
            data["battle_workflow"] = workflow
        raw_manual = data.get("manual_control")
        manual = validate_manual_control(raw_manual)
        if manual is None and raw_manual is not None:
            manual = _interrupted_manual_control_from_envelope(
                raw_manual,
                reason=reason,
                source=source,
                timestamp=timestamp,
            )
            if manual is not None:
                data["manual_control"] = manual
        if (
            manual is not None
            and manual["status"] not in MANUAL_CONTROL_TERMINAL_STATUSES
        ):
            manual.update(
                {
                    "status": "interrupted",
                    "detail": reason,
                    "updated_at": timestamp,
                    "completed_at": timestamp,
                    "updated_by": source,
                }
            )
            data["manual_control"] = manual
        raw_capture = data.get("setup_capture")
        capture = validate_setup_capture(raw_capture)
        if capture is None and raw_capture is not None:
            capture = _interrupted_setup_capture_from_envelope(
                raw_capture,
                reason=reason,
                source=source,
                timestamp=timestamp,
            )
            if capture is not None:
                data["setup_capture"] = capture
        if capture is not None and capture["status"] in {
            "requested",
            "acknowledged",
            "capturing",
        }:
            capture.update(
                {
                    "status": "interrupted",
                    "reason": reason,
                    "updated_at": timestamp,
                    "completed_at": timestamp,
                    "updated_by": source,
                }
            )
            data["setup_capture"] = capture
        data["updated_at"] = timestamp
        data["updated_by"] = source
        return data

    def interrupt_operator_workflows(
        self,
        reason: str,
        *,
        source: str,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Make unfinished workflow ownership visibly terminal."""

        normalized_reason = _bounded_text(reason, 512)

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            return self._interrupt_operator_workflows_data(
                data,
                reason=normalized_reason,
                source=source,
                timestamp=_timestamp_at(now),
            )

        with self._dispatch_boundary():
            return self.update(mutate)

    def request_interactive_development_lease(
        self,
        *,
        owner_label: str,
        runtime: Mapping[str, object],
        starting_evidence: Mapping[str, object],
        owned_battle_start: bool = False,
        now: Optional[float] = None,
        ttl_seconds: int = INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Persist one cooperative request bound to fresh runtime evidence."""

        normalized_owner = " ".join(str(owner_label or "").split())
        if not normalized_owner:
            raise ValueError("Interactive development owner label is required")
        if len(normalized_owner) > 96:
            raise ValueError(
                "Interactive development owner label must be at most 96 characters"
            )
        normalized_runtime = _valid_interactive_development_runtime(runtime)
        if normalized_runtime is None:
            raise ValueError(
                "Interactive development request requires exact runtime, PID, "
                "and ADB-target evidence"
            )
        normalized_evidence = _valid_interactive_development_evidence(
            starting_evidence
        )
        if normalized_evidence is None:
            raise ValueError(
                "Interactive development request requires starting screen evidence"
            )
        if not isinstance(owned_battle_start, bool):
            raise ValueError(
                "Interactive development owned_battle_start must be a boolean"
            )
        if owned_battle_start and not (
            normalized_evidence.get("screen_state") == "HOME_SCREEN"
            and normalized_evidence.get("home_battle_control") == "NEW_BATTLE"
            and normalized_evidence.get("battle_active") is False
            and type(normalized_evidence.get("target_generation")) is int
            and int(normalized_evidence["target_generation"]) > 0
        ):
            raise ValueError(
                "An owned development battle must be preclaimed from exact "
                "Home New Battle evidence with a target generation"
            )
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ValueError("Interactive development lease TTL must be an integer")
        if not 5 <= ttl_seconds <= 300:
            raise ValueError(
                "Interactive development lease TTL must be between 5 and 300 seconds"
            )
        requested_at = _timestamp_at(now)
        expires_at = _timestamp_at(
            _timestamp_value(requested_at) + ttl_seconds
        )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            raw_current = data.get("interactive_development_lease")
            current = _valid_interactive_development_lease(raw_current)
            if raw_current is not None and current is None:
                raise ValueError(
                    "Existing interactive development lease directive is malformed; "
                    "it was preserved"
                )
            if current is not None and current["request_state"] != "terminal":
                raise ValueError("An interactive development lease request is busy")
            lease = {
                "schema_version": INTERACTIVE_DEVELOPMENT_LEASE_SCHEMA_VERSION,
                "lease_id": uuid4().hex,
                "owner_label": normalized_owner,
                "request_state": "requested",
                "requested_at": requested_at,
                "heartbeat_at": requested_at,
                "expires_at": expires_at,
                "runtime": normalized_runtime,
                "starting_evidence": normalized_evidence,
            }
            if owned_battle_start:
                lease["owned_battle_start"] = True
            data["interactive_development_lease"] = lease
            return data

        saved = self.update(mutate)
        return dict(saved["interactive_development_lease"])

    def request_emulator_maintenance(
        self,
        *,
        reason: str,
        source: str,
        runtime: Mapping[str, object],
        battle_scope: Optional[str],
        host_target: Mapping[str, object],
        initiator: str = "automatic_detector",
        trigger: Optional[Mapping[str, object]] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Persist one exact-runtime BlueStacks restart request."""

        timestamp = _timestamp_at(now)
        candidate = {
            "schema_version": 1,
            "request_id": uuid4().hex,
            "action": "restart_bluestacks",
            "state": "requested",
            "reason": " ".join(str(reason or "").split())[:256],
            "source": " ".join(str(source or "").split())[:64],
            "initiator": " ".join(str(initiator or "").split())[:32].lower(),
            "requested_at": timestamp,
            "updated_at": timestamp,
            "runtime": dict(runtime),
            "battle_scope": str(battle_scope or "").strip() or None,
            "host_target": dict(host_target),
            "trigger": dict(trigger or {}),
        }
        normalized = normalize_emulator_maintenance(candidate)
        if normalized is None or "host_target" not in normalized:
            raise ValueError(
                "Emulator maintenance requires a reason, source, exact runtime, "
                "ADB target, canonical battle identity, and exact Windows target"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            raw_current = data.get("emulator_maintenance")
            current = normalize_emulator_maintenance(raw_current)
            if raw_current is not None and current is None:
                raise ValueError(
                    "Existing emulator maintenance directive is malformed; "
                    "it was preserved"
                )
            if current is not None and current["state"] != "terminal":
                raise ValueError("An emulator maintenance request is busy")
            if str(data.get("state") or "").strip().upper() != "RUNNING":
                raise ValueError(
                    "Emulator maintenance request requires the same Enabled "
                    "control boundary"
                )
            bound_state_request_id = str(
                normalized.get("runtime", {}).get("state_request_id") or ""
            )
            if str(data.get("state_request_id") or "") != bound_state_request_id:
                raise ValueError(
                    "Emulator maintenance control authority changed before "
                    "request creation"
                )
            data["emulator_maintenance"] = normalized
            data["updated_at"] = timestamp
            data["updated_by"] = str(source or "emulator-maintenance")[:64]
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["emulator_maintenance"])

    def acknowledge_emulator_maintenance_host(
        self,
        request_id: str,
        *,
        host_ack: Mapping[str, object],
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Record the exact Windows process identity before host mutation."""

        timestamp = _timestamp_at(now)

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            current = _require_emulator_maintenance(data, request_id)
            if current["state"] == "host_acknowledged":
                if not _same_emulator_host_identity(
                    current.get("host_ack"),
                    host_ack,
                    include_previous=False,
                ):
                    raise ValueError(
                        "Emulator maintenance host acknowledgement conflicts "
                        "with the stored identity"
                    )
                return data
            if current["state"] != "requested":
                raise ValueError(
                    "Emulator maintenance does not accept a host acknowledgement"
                )
            if str(data.get("state") or "").strip().upper() != "RUNNING":
                raise ValueError(
                    "Emulator maintenance host acknowledgement requires the "
                    "same Enabled control boundary"
                )
            bound_state_request_id = str(
                current.get("runtime", {}).get("state_request_id") or ""
            )
            if str(data.get("state_request_id") or "") != bound_state_request_id:
                raise ValueError(
                    "Emulator maintenance control authority changed before "
                    "host acknowledgement"
                )
            host_target = current.get("host_target")
            if host_target is not None and not _same_emulator_host_identity(
                host_target,
                host_ack,
                include_previous=False,
            ):
                raise ValueError(
                    "Emulator maintenance host acknowledgement does not match "
                    "the requested exact process identity"
                )
            candidate = {
                **current,
                "state": "host_acknowledged",
                "updated_at": timestamp,
                "host_ack": dict(host_ack),
            }
            normalized = normalize_emulator_maintenance(candidate)
            if normalized is None:
                raise ValueError("Emulator maintenance host identity is malformed")
            data["emulator_maintenance"] = normalized
            data["updated_at"] = timestamp
            data["updated_by"] = "windows-bluestacks-maintenance"
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["emulator_maintenance"])

    def complete_emulator_maintenance_host(
        self,
        request_id: str,
        *,
        host_completion: Mapping[str, object],
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Record a newly ready process without replaying the host restart."""

        timestamp = _timestamp_at(now)

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            current = _require_emulator_maintenance(data, request_id)
            if current["state"] == "host_restarted":
                if not _same_emulator_host_identity(
                    current.get("host_completion"),
                    host_completion,
                    include_previous=True,
                ):
                    raise ValueError(
                        "Emulator maintenance completion conflicts with the "
                        "stored replacement identity"
                    )
                return data
            if current["state"] != "host_acknowledged":
                raise ValueError(
                    "Emulator maintenance host completion is not expected"
                )
            host_ack = current.get("host_ack") or {}
            completion = dict(host_completion)
            if (
                completion.get("host_id") != host_ack.get("host_id")
                or completion.get("adb_port") != host_ack.get("adb_port")
                or completion.get("executable_path")
                != host_ack.get("executable_path")
                or completion.get("instance_name")
                != host_ack.get("instance_name")
                or completion.get("previous_process_id")
                != host_ack.get("process_id")
                or completion.get("previous_process_started_at")
                != host_ack.get("process_started_at")
            ):
                raise ValueError(
                    "Emulator maintenance completion does not match the "
                    "acknowledged old process"
                )
            if (
                completion.get("process_id") == host_ack.get("process_id")
                and completion.get("process_started_at")
                == host_ack.get("process_started_at")
            ):
                raise ValueError(
                    "Emulator maintenance completion must prove a new process"
                )
            candidate = {
                **current,
                "state": "host_restarted",
                "updated_at": timestamp,
                "host_completion": completion,
            }
            normalized = normalize_emulator_maintenance(candidate)
            if normalized is None:
                raise ValueError(
                    "Emulator maintenance replacement identity is malformed"
                )
            data["emulator_maintenance"] = normalized
            data["updated_at"] = timestamp
            data["updated_by"] = "windows-bluestacks-maintenance"
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["emulator_maintenance"])

    def finish_emulator_maintenance(
        self,
        request_id: str,
        *,
        disposition: str,
        reason: str,
        source: str,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Persist one idempotent terminal maintenance result."""

        timestamp = _timestamp_at(now)
        normalized_disposition = _bounded_text(disposition, 48).lower()
        normalized_reason = " ".join(str(reason or "").split())[:256]
        if not normalized_disposition or not normalized_reason:
            raise ValueError(
                "Emulator maintenance terminal disposition and reason are required"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            current = _require_emulator_maintenance(data, request_id)
            if current["state"] == "terminal":
                return data
            candidate = {
                **current,
                "state": "terminal",
                "updated_at": timestamp,
                "terminal_at": timestamp,
                "terminal_disposition": normalized_disposition,
                "terminal_reason": normalized_reason,
            }
            normalized = normalize_emulator_maintenance(candidate)
            if normalized is None:
                raise ValueError("Emulator maintenance terminal state is malformed")
            data["emulator_maintenance"] = normalized
            data["updated_at"] = timestamp
            data["updated_by"] = str(source or "emulator-maintenance")[:64]
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return dict(saved["emulator_maintenance"])

    def heartbeat_interactive_development_lease(
        self,
        lease_id: str,
        *,
        now: Optional[float] = None,
        ttl_seconds: int = INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Refresh only the matching live request without producing log noise."""

        current_time = datetime.now().timestamp() if now is None else float(now)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ValueError("Interactive development lease TTL must be an integer")
        if not 5 <= ttl_seconds <= 300:
            raise ValueError(
                "Interactive development lease TTL must be between 5 and 300 seconds"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            lease = _require_interactive_development_lease(
                data,
                lease_id=lease_id,
            )
            if lease["request_state"] != "requested":
                raise ValueError(
                    "Interactive development lease no longer accepts heartbeats"
                )
            if current_time >= _timestamp_value(lease["expires_at"]):
                raise ValueError("Interactive development lease has expired")
            lease["heartbeat_at"] = _timestamp_at(current_time)
            lease["expires_at"] = _timestamp_at(current_time + ttl_seconds)
            data["interactive_development_lease"] = lease
            return data

        saved = self.update(mutate)
        return dict(saved["interactive_development_lease"])

    def release_interactive_development_lease(
        self,
        lease_id: str,
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Request a matching normal release; the runtime removes the hold."""

        current_time = datetime.now().timestamp() if now is None else float(now)

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            lease = _require_interactive_development_lease(
                data,
                lease_id=lease_id,
            )
            if lease["request_state"] != "requested":
                raise ValueError(
                    "Interactive development lease is not active for release"
                )
            if current_time >= _timestamp_value(lease["expires_at"]):
                raise ValueError("Interactive development lease has expired")
            lease["request_state"] = "release_requested"
            lease["release_requested_at"] = _timestamp_at(current_time)
            data["interactive_development_lease"] = lease
            return data

        saved = self.update(mutate)
        return dict(saved["interactive_development_lease"])

    def finish_interactive_development_lease(
        self,
        lease_id: str,
        *,
        disposition: str,
        reason: str,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Persist one runtime- or coordinator-proven terminal disposition."""

        normalized_disposition = _bounded_text(disposition, 48).lower()
        normalized_reason = " ".join(str(reason or "").split())[:256]
        if not normalized_disposition or not normalized_reason:
            raise ValueError(
                "Interactive development terminal disposition and reason are required"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            lease = _require_interactive_development_lease(
                data,
                lease_id=lease_id,
            )
            if lease["request_state"] == "terminal":
                return data
            lease["request_state"] = "terminal"
            lease["terminal_at"] = _timestamp_at(now)
            lease["terminal_disposition"] = normalized_disposition
            lease["terminal_reason"] = normalized_reason
            data["interactive_development_lease"] = lease
            return data

        saved = self.update(mutate)
        return dict(saved["interactive_development_lease"])

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
            data.pop("terminal_idle_timeout", None)
            if deadline is not None:
                data["resume_at"] = deadline
            data["updated_at"] = timestamp
            data["state_updated_at"] = timestamp
            data["state_request_id"] = uuid4().hex
            if source:
                data["updated_by"] = source
            return data

        with self._dispatch_boundary():
            return self.update(mutate)

    def set_paused_unless_stopped(
        self,
        *,
        resume_at: Optional[float] = None,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist Pause without ever overriding explicit Stop."""

        deadline = _finite_number(resume_at)
        if resume_at is not None and deadline is None:
            raise ValueError("resume_at must be a finite timestamp")

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            if str(data.get("state") or "").strip().upper() == "STOPPED":
                return data
            timestamp = _updated_at()
            data["state"] = "PAUSED"
            data.pop("resume_at", None)
            data.pop("terminal_idle_timeout", None)
            if deadline is not None:
                data["resume_at"] = deadline
            data["updated_at"] = timestamp
            data["state_updated_at"] = timestamp
            data["state_request_id"] = uuid4().hex
            if source:
                data["updated_by"] = source
            return data

        with self._dispatch_boundary():
            return self.update(mutate)

    def set_state_and_interrupt_operator_workflows(
        self,
        state: str,
        reason: str,
        *,
        source: str,
        restart_handoff_evidence: Optional[Mapping[str, object]] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Atomically stop input authority and retire unfinished workflows."""

        normalized = str(state).strip().upper()
        if normalized not in VALID_STATES:
            raise ValueError(
                f"Unsupported automation state {state!r}; "
                f"expected one of {sorted(VALID_STATES)}"
            )
        normalized_reason = _bounded_text(reason, 512)
        normalized_handoff_evidence = None
        if restart_handoff_evidence is not None:
            if normalized != "STOPPED":
                raise ValueError(
                    "A process restart handoff may be created only while stopping"
                )
            normalized_handoff_evidence = validate_workflow_evidence(
                restart_handoff_evidence
            )
            if (
                normalized_handoff_evidence is None
                or normalized_handoff_evidence.get("game_state")
                != "active_battle"
                or not _valid_active_battle_identity(
                    normalized_handoff_evidence.get(
                        "active_round_identity_fingerprint"
                    )
                )
            ):
                raise ValueError(
                    "Process restart handoff requires exact active-battle identity evidence"
                )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _timestamp_at(now)
            self._interrupt_operator_workflows_data(
                data,
                reason=normalized_reason,
                source=source,
                timestamp=timestamp,
            )
            if normalized_handoff_evidence is not None:
                expected_identity = str(
                    normalized_handoff_evidence[
                        "active_round_identity_fingerprint"
                    ]
                )
                data["process_restart_handoff"] = {
                    "schema_version": 1,
                    "handoff_id": uuid4().hex,
                    "status": "pending",
                    "requested_at": timestamp,
                    "updated_at": timestamp,
                    "resume_state": "RUNNING",
                    "expected_active_round_identity_fingerprint": (
                        expected_identity
                    ),
                    "source_evidence": dict(normalized_handoff_evidence),
                    "updated_by": source,
                }
            elif normalized == "STOPPED":
                handoff = validate_process_restart_handoff(
                    data.get("process_restart_handoff")
                )
                if handoff is not None and handoff["status"] == "pending":
                    handoff.update(
                        {
                            "status": "cancelled",
                            "reason": (
                                "the newer Stop boundary did not retain exact "
                                "active-battle continuity"
                            ),
                            "updated_at": timestamp,
                            "completed_at": timestamp,
                            "updated_by": source,
                        }
                    )
                    data["process_restart_handoff"] = handoff
            data["state"] = normalized
            data.pop("resume_at", None)
            data.pop("terminal_idle_timeout", None)
            data["state_updated_at"] = timestamp
            data["state_request_id"] = uuid4().hex
            data["updated_at"] = timestamp
            data["updated_by"] = source
            return data

        with self._dispatch_boundary():
            return self.update(mutate)

    def finish_process_restart_handoff(
        self,
        handoff_id: str,
        status: str,
        *,
        reason: str,
        workflow_id: Optional[str] = None,
        actual_active_round_identity: Optional[str] = None,
        battle_relation: Optional[str] = None,
        source: str = "runtime",
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Finish only the matching pending active-battle restart handoff."""

        normalized_handoff_id = str(handoff_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        normalized_reason = _bounded_text(reason, 512)
        normalized_workflow_id = (
            str(workflow_id or "").strip() or None
        )
        normalized_actual_identity = (
            _valid_active_battle_identity(actual_active_round_identity)
            if actual_active_round_identity is not None
            else None
        )
        normalized_battle_relation = (
            str(battle_relation or "").strip().lower()
            if battle_relation is not None
            else None
        )
        if (
            not normalized_handoff_id
            or normalized_status not in {"completed", "failed", "cancelled"}
            or not normalized_reason
            or (
                actual_active_round_identity is not None
                and normalized_actual_identity is None
            )
            or (
                normalized_battle_relation is not None
                and normalized_battle_relation
                not in {"same_battle", "later_battle"}
            )
        ):
            raise ValueError("Invalid process restart handoff result")

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            handoff = validate_process_restart_handoff(
                data.get("process_restart_handoff")
            )
            if (
                handoff is None
                or handoff["handoff_id"] != normalized_handoff_id
                or handoff["status"] != "pending"
            ):
                return data
            bound_workflow_id = handoff.get("workflow_id")
            if normalized_workflow_id is not None:
                if (
                    bound_workflow_id is not None
                    and bound_workflow_id != normalized_workflow_id
                ):
                    raise ValueError(
                        "Process restart result names a different Attach workflow"
                    )
                handoff["workflow_id"] = normalized_workflow_id
            if normalized_status == "completed":
                expected_identity = handoff[
                    "expected_active_round_identity_fingerprint"
                ]
                inferred_relation = (
                    "same_battle"
                    if normalized_actual_identity == expected_identity
                    else "later_battle"
                    if normalized_actual_identity is not None
                    else None
                )
                if handoff.get("workflow_id") is None or inferred_relation is None:
                    raise ValueError(
                        "Process restart cannot complete without a force-bound "
                        "active battle identity"
                    )
                if (
                    normalized_battle_relation is not None
                    and normalized_battle_relation != inferred_relation
                ):
                    raise ValueError(
                        "Process restart battle relation contradicts its identities"
                    )
                handoff["battle_relation"] = inferred_relation
            timestamp = _timestamp_at(now)
            handoff.update(
                {
                    "status": normalized_status,
                    "reason": normalized_reason,
                    "updated_at": timestamp,
                    "completed_at": timestamp,
                    "updated_by": source,
                }
            )
            if normalized_actual_identity is not None:
                handoff[
                    "actual_active_round_identity_fingerprint"
                ] = normalized_actual_identity
            data["process_restart_handoff"] = handoff
            return data

        saved = self.update(mutate)
        handoff = validate_process_restart_handoff(
            saved.get("process_restart_handoff")
        )
        if handoff is None or handoff["handoff_id"] != normalized_handoff_id:
            return None
        return handoff

    def set_adb_port(
        self,
        port: int,
        *,
        emulator_location: Optional[Mapping[str, object]] = None,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a validated localhost ADB-port handoff request."""

        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError("ADB port must be an integer")
        if not 1 <= port <= 65535:
            raise ValueError("ADB port must be between 1 and 65535")
        normalized_location = (
            _normalize_emulator_location_identity(emulator_location)
            if emulator_location is not None
            else None
        )
        if emulator_location is not None and normalized_location is None:
            raise ValueError("emulator_location is malformed")
        if (
            normalized_location is not None
            and normalized_location["linux_adb_port"] != port
        ):
            raise ValueError(
                "emulator_location linux_adb_port must match adb_port"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            request_id = uuid4().hex
            data["adb_port"] = port
            data["updated_at"] = timestamp
            data["adb_port_updated_at"] = timestamp
            data["adb_port_request_id"] = request_id
            if normalized_location is not None:
                data["emulator_location"] = {
                    **normalized_location,
                    "request_id": request_id,
                    "selected_at": timestamp,
                }
            else:
                existing = normalize_emulator_location(
                    data.get("emulator_location")
                )
                if (
                    existing is not None
                    and existing.get("linux_adb_port") == port
                ):
                    data["emulator_location"] = {
                        **existing,
                        "request_id": request_id,
                        "selected_at": timestamp,
                    }
                else:
                    data.pop("emulator_location", None)
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def set_mode(self, mode: str, *, source: Optional[str] = None) -> dict[str, Any]:
        """Persist a validated execution mode while preserving state."""

        normalized = normalize_automation_mode(mode)

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            data["mode"] = normalized
            data.pop("terminal_idle_timeout", None)
            data["updated_at"] = timestamp
            data["mode_updated_at"] = timestamp
            data["mode_request_id"] = uuid4().hex
            if source:
                data["updated_by"] = source
            return data

        # A terminal-policy transition and the final guarded input dispatch
        # share one linearization boundary.  Once this write returns, no tap
        # authorized by the previous policy can begin.
        with self._dispatch_boundary():
            return self.update(mutate)

    def activate_terminal_idle_timeout(
        self,
        *,
        evidence: Mapping[str, object],
        timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        strategy: str = DEFAULT_IDLE_TIMEOUT_STRATEGY,
        source: str = "runtime-terminal-idle-timeout",
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Arm one timeout only for the current RUNNING Wait/Home request."""

        normalized_evidence = validate_workflow_evidence(evidence)
        if normalized_evidence is None or normalized_evidence.get(
            "game_state"
        ) not in {"game_over", "tournament_results", "home_new_battle"}:
            raise ValueError(
                "Terminal idle timeout requires fresh terminal or Home New evidence"
            )
        normalized_strategy = str(strategy or "").strip().lower()
        if not is_configurable_strategy(
            normalized_strategy,
            self.strategy_profile_dir,
            allow_legacy_aliases=False,
        ):
            raise ValueError("Terminal idle timeout strategy is unavailable")
        try:
            duration = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("Terminal idle timeout must be numeric") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Terminal idle timeout must be positive")
        current_time = (
            float(now) if now is not None else datetime.now().timestamp()
        )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            state = str(data.get("state") or "").strip().upper()
            policy = normalize_automation_mode(data.get("mode"))
            state_request_id = str(
                data.get("state_request_id") or ""
            ).strip()
            mode_request_id = str(data.get("mode_request_id") or "").strip()
            if (
                state != "RUNNING"
                or policy not in {"WAIT", "HOME"}
                or not state_request_id
                or not mode_request_id
            ):
                data.pop("terminal_idle_timeout", None)
                return data
            existing = normalize_terminal_idle_timeout(
                data.get("terminal_idle_timeout")
            )
            if (
                existing is not None
                and existing["state_request_id"] == state_request_id
                and existing["mode_request_id"] == mode_request_id
                and existing["policy"] == policy
                and existing["strategy"] == normalized_strategy
            ):
                return data
            timestamp = _timestamp_at(current_time)
            data["terminal_idle_timeout"] = {
                "schema_version": TERMINAL_IDLE_TIMEOUT_SCHEMA_VERSION,
                "request_id": uuid4().hex,
                "state_request_id": state_request_id,
                "mode_request_id": mode_request_id,
                "policy": policy,
                "status": "holding",
                "strategy": normalized_strategy,
                "activated_at": timestamp,
                "expires_at": current_time + duration,
                "evidence": normalized_evidence,
            }
            data["updated_at"] = timestamp
            data["updated_by"] = source
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return normalize_terminal_idle_timeout(
            saved.get("terminal_idle_timeout")
        )

    def advance_expired_terminal_idle_timeout_to_home(
        self,
        request_id: str,
        *,
        source: str = "runtime-terminal-idle-timeout",
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Atomically turn an expired terminal Wait into a guarded Home route."""

        expected_request_id = str(request_id or "").strip()
        current_time = (
            float(now) if now is not None else datetime.now().timestamp()
        )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            hold = normalize_terminal_idle_timeout(
                data.get("terminal_idle_timeout")
            )
            if (
                hold is None
                or hold["request_id"] != expected_request_id
                or hold["expires_at"] > current_time
                or str(data.get("state") or "").strip().upper() != "RUNNING"
                or str(data.get("state_request_id") or "").strip()
                != hold["state_request_id"]
                or str(data.get("mode_request_id") or "").strip()
                != hold["mode_request_id"]
            ):
                return data
            timestamp = _timestamp_at(current_time)
            mode_request_id = uuid4().hex
            data["mode"] = "HOME"
            data["mode_updated_at"] = timestamp
            data["mode_request_id"] = mode_request_id
            data["terminal_idle_timeout"] = {
                **hold,
                "mode_request_id": mode_request_id,
                "status": "returning_home",
            }
            data["updated_at"] = timestamp
            data["updated_by"] = source
            return data

        with self._dispatch_boundary():
            saved = self.update(mutate)
        return normalize_terminal_idle_timeout(
            saved.get("terminal_idle_timeout")
        )

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

    def _replace_exclusive_validation_request(
        self,
        data: dict[str, Any],
        *,
        strategy: str,
        strategy_request_id: str,
        timestamp: str,
        superseded_reason: str,
    ) -> None:
        """Replace pending validation through the existing Strategy ledger."""

        try:
            validation_definition = (
                exclusive_validation_definition_for_strategy(strategy)
            )
        except ValueError:
            if strategy in BUILTIN_STRATEGY_IDS:
                raise
            # Constrained custom Farm profiles do not declare an exclusive
            # validation battle. Their plan has already been validated at
            # publication time, possibly through an injected test directory.
            validation_definition = None
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
                        "reason": superseded_reason,
                        "completed_at": timestamp,
                        "updated_at": timestamp,
                    },
                }
        for request_id, receipt in list(receipts.items()):
            if receipt["status"] != "pending":
                continue
            receipts[request_id] = {
                **receipt,
                "status": "result",
                "outcome": "cancelled",
                "reason": superseded_reason,
                "completed_at": timestamp,
                "updated_at": timestamp,
            }
            if validation_definition is None:
                ledger["current_request_id"] = request_id
        if validation_definition is not None:
            validation_request_id = uuid4().hex
            receipts[validation_request_id] = {
                "request_id": validation_request_id,
                "strategy_request_id": strategy_request_id,
                "strategy": strategy,
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
        ledger["receipts"] = _prune_exclusive_validation_receipts(receipts)
        data["exclusive_validation"] = ledger

    def set_strategy(
        self,
        strategy: str,
        *,
        apply_mode: str = "next_boundary",
        active_battle_identity: Optional[str] = None,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a validated runtime strategy request."""

        normalized = str(strategy).strip().lower()
        if not is_configurable_strategy(
            normalized,
            self.strategy_profile_dir,
            allow_legacy_aliases=False,
        ):
            raise ValueError(
                "Strategy must be one of: "
                + ", ".join(
                    configurable_strategy_ids(self.strategy_profile_dir)
                )
            )
        normalized_apply_mode = str(apply_mode or "").strip().lower()
        if normalized_apply_mode not in STRATEGY_APPLY_MODES:
            raise ValueError(
                "Strategy apply mode must be one of: "
                + ", ".join(sorted(STRATEGY_APPLY_MODES))
            )
        normalized_battle_identity = _valid_active_battle_identity(
            active_battle_identity
        )
        if (
            normalized_apply_mode == "active_battle"
            and normalized_battle_identity is None
        ):
            raise ValueError(
                "Active-battle Strategy requests require a canonical battle identity"
            )

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            timestamp = _updated_at()
            previous = self._valid_strategy(data.get("strategy"))
            strategy_request_id = uuid4().hex
            data["strategy"] = normalized
            data["strategy_apply_mode"] = normalized_apply_mode
            if normalized_apply_mode == "active_battle":
                data["strategy_active_battle_identity"] = (
                    normalized_battle_identity
                )
            else:
                data.pop("strategy_active_battle_identity", None)
            if previous != normalized:
                data["startup_gate_waivers"] = {}
            data["updated_at"] = timestamp
            data["strategy_updated_at"] = timestamp
            data["strategy_request_id"] = strategy_request_id
            self._replace_exclusive_validation_request(
                data,
                strategy=normalized,
                strategy_request_id=strategy_request_id,
                timestamp=timestamp,
                superseded_reason=(
                    f"superseded by explicit {normalized} strategy request"
                ),
            )
            if source:
                data["updated_by"] = source
            return data

        return self.update(mutate)

    def defer_strategy_request_to_next_boundary(
        self,
        strategy: str,
        request_id: object,
        *,
        source: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Downshift only the exact current active-battle Strategy request.

        The request identity is retained because this changes only where an
        already accepted selection may be applied. A newer concurrent Strategy
        request always wins the compare-and-set.
        """

        normalized_strategy = str(strategy or "").strip().lower()
        normalized_request_id = str(request_id or "").strip()
        if not is_configurable_strategy(
            normalized_strategy,
            self.strategy_profile_dir,
            allow_legacy_aliases=False,
        ):
            raise ValueError("Strategy request is invalid")
        if not normalized_request_id or len(normalized_request_id) > 128:
            raise ValueError("Strategy request identity is invalid")

        with self._lock():
            current = self._read_unlocked()
            if (
                self._valid_strategy(current.get("strategy"))
                != normalized_strategy
                or str(current.get("strategy_request_id") or "").strip()
                != normalized_request_id
            ):
                return None
            apply_mode = _valid_strategy_apply_mode(
                current.get("strategy_apply_mode")
            )
            if apply_mode == "next_boundary":
                return dict(current)
            timestamp = _updated_at()
            current["strategy_apply_mode"] = "next_boundary"
            current.pop("strategy_active_battle_identity", None)
            current["strategy_updated_at"] = timestamp
            current["updated_at"] = timestamp
            current["updated_by"] = source or "runtime-strategy-deferral"
            self._write_unlocked(current)
            return dict(current)

    def _valid_strategy(self, value: object) -> Optional[str]:
        normalized = str(value or "").strip().lower()
        return normalized if is_configurable_strategy(
            normalized,
            self.strategy_profile_dir,
            allow_legacy_aliases=False,
        ) else None

    def _strategy_definition_fingerprint(
        self,
        strategy_name: str,
    ) -> Optional[str]:
        """Resolve one complete plan while the workflow writer lock is held."""

        try:
            from automation.strategies import get_strategy

            strategy = get_strategy(
                strategy_name,
                profile_directory=self.strategy_profile_dir,
            )
            fingerprint = str(
                strategy.definition_fingerprint() if strategy else ""
            ).strip()
        except Exception:
            return None
        return fingerprint if len(fingerprint) == 64 else None

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
        authority_boundary = (
            self._dispatch_boundary()
            if normalized_decision == "start"
            else nullcontext()
        )
        with authority_boundary, self._lock():
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
            validation_owner = (
                _valid_exclusive_validation_owner(receipt.get("owner"))
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
                or validation_owner is None
                or validation_owner.get("adb_target")
                != normalized_owner.get("adb_target")
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
        observer: Mapping[str, Any],
        reason: str,
    ) -> Optional[dict[str, Any]]:
        """Consume an unclaimed prompt after a fresh manual battle start."""

        normalized_observer = _valid_exclusive_validation_owner(observer)
        if normalized_observer is None:
            raise ValueError(
                "exclusive validation launch observer is incomplete"
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
            validation_owner = (
                _valid_exclusive_validation_owner(receipt.get("owner"))
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
                or validation_owner is None
                or validation_owner.get("adb_target")
                != normalized_observer.get("adb_target")
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
        repair_authority: Optional[Mapping[str, object]] = None,
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
        offers_repair = any(
            option.get("action") == "repair_restart"
            for option in normalized_options
        )
        normalized_repair_authority = (
            validate_workflow_evidence(repair_authority)
            if repair_authority is not None
            else None
        )
        if offers_repair and (
            normalized_repair_authority is None
            or normalized_repair_authority.get("game_state") != "active_battle"
        ):
            raise ValueError(
                "repair Surrender requires exact active-battle authority"
            )
        if not offers_repair and repair_authority is not None:
            raise ValueError(
                "repair authority is valid only when repair Surrender is offered"
            )

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
                and existing.get("repair_authority")
                == normalized_repair_authority
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
            if normalized_repair_authority is not None:
                directive["repair_authority"] = normalized_repair_authority
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
        with self._dispatch_boundary():
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
        with self._dispatch_boundary():
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
    def _dispatch_boundary(self) -> Iterator[None]:
        """Order durable authority writes against device dispatch."""

        try:
            with dispatch_control_boundary(self.dispatch_lock_path):
                yield
        except DispatchControlBoundaryError as exc:
            raise ControlDirectiveError(
                "Unable to acquire the device dispatch/control boundary for "
                f"{self.path}: {exc}"
            ) from exc

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
        normalized = dict(data)
        if "mode" in normalized:
            try:
                normalized["mode"] = normalize_automation_mode(normalized["mode"])
            except ValueError:
                # Preserve unsupported values so the ordinary runtime validation
                # can ignore them without silently rewriting operator input.
                pass
        return normalized

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
    return normalize_strategy_id(value)


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


def _interrupted_battle_workflow_from_envelope(
    value: object,
    *,
    reason: str,
    source: str,
    timestamp: str,
) -> Optional[dict[str, Any]]:
    """Retire a recognizable workflow whose old authority payload is invalid."""

    if not isinstance(value, Mapping):
        return None
    candidate = {
        "schema_version": value.get("schema_version"),
        "request_id": value.get("request_id"),
        "intent": value.get("intent"),
        "status": "interrupted",
        "requested_at": value.get("requested_at"),
        "evidence": value.get("evidence"),
        "updated_at": timestamp,
        "completed_at": timestamp,
        "reason": _bounded_text(
            f"{reason}; prior workflow authority no longer matches the "
            "current schema",
            512,
        ),
        "updated_by": source,
    }
    return validate_battle_workflow(candidate)


def _interrupted_setup_capture_from_envelope(
    value: object,
    *,
    reason: str,
    source: str,
    timestamp: str,
) -> Optional[dict[str, Any]]:
    """Retire a recognizable capture whose old preview is no longer valid."""

    if not isinstance(value, Mapping):
        return None
    candidate: dict[str, Any] = {
        "schema_version": value.get("schema_version"),
        "request_id": value.get("request_id"),
        "status": "interrupted",
        "requested_at": value.get("requested_at"),
        "evidence": value.get("evidence"),
        "acquisition_source": value.get(
            "acquisition_source",
            "new_setup_capture_refresh",
        ),
        "updated_at": timestamp,
        "completed_at": timestamp,
        "reason": _bounded_text(
            f"{reason}; prior setup preview no longer matches the current "
            "schema",
            512,
        ),
        "updated_by": source,
    }
    if value.get("source_manual_control_id") is not None:
        candidate["source_manual_control_id"] = value.get(
            "source_manual_control_id"
        )
    authority_outcome = value.get("authority_outcome")
    if authority_outcome in SETUP_CAPTURE_AUTHORITY_OUTCOMES:
        candidate["authority_outcome"] = authority_outcome
    return validate_setup_capture(candidate)


def _interrupted_manual_control_from_envelope(
    value: object,
    *,
    reason: str,
    source: str,
    timestamp: str,
) -> Optional[dict[str, Any]]:
    """Retire recognizable manual ownership with an obsolete receipt schema."""

    if not isinstance(value, Mapping):
        return None
    candidate: dict[str, Any] = {
        "schema_version": value.get("schema_version"),
        "manual_control_id": value.get("manual_control_id"),
        "status": "interrupted",
        "reason": value.get("reason", "operator"),
        "requested_at": value.get("requested_at"),
        "starting_evidence": value.get("starting_evidence"),
        "updated_at": timestamp,
        "completed_at": timestamp,
        "detail": _bounded_text(
            f"{reason}; prior manual-control authority no longer matches the "
            "current schema",
            512,
        ),
        "updated_by": source,
    }
    surrender_collection = value.get("surrender_collection")
    if surrender_collection in MANUAL_SURRENDER_COLLECTIONS:
        candidate["surrender_collection"] = surrender_collection
    return validate_manual_control(candidate)


def _timestamp_at(value: Optional[float] = None) -> str:
    timestamp = datetime.now().timestamp() if value is None else float(value)
    if not math.isfinite(timestamp):
        raise ValueError("Timestamp must be finite")
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(
        timespec="seconds"
    )


def _timestamp_value(value: object) -> float:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ValueError("Timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.timestamp()


def _valid_interactive_development_runtime(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    runtime_id = _bounded_text(value.get("runtime_id"), 128)
    adb_target = _bounded_text(value.get("adb_target"), 128)
    pid = value.get("pid")
    if (
        not runtime_id
        or not adb_target
        or adb_target == "unknown"
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


def _valid_interactive_development_evidence(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    screen_state = _bounded_text(value.get("screen_state"), 64).upper()
    observed_at = _bounded_text(value.get("observed_at"), 64)
    battle_active = value.get("battle_active")
    battle_scope = _bounded_text(value.get("battle_scope"), 128) or None
    home_battle_control = _bounded_text(
        value.get("home_battle_control"), 64
    ).upper()
    target_generation = value.get("target_generation")
    if not screen_state or not isinstance(battle_active, bool):
        return None
    try:
        _timestamp_value(observed_at)
    except ValueError:
        return None
    if target_generation is not None and (
        type(target_generation) is not int or target_generation < 1
    ):
        return None
    result = {
        "screen_state": screen_state,
        "battle_active": battle_active,
        "battle_scope": battle_scope,
        "observed_at": observed_at,
    }
    if home_battle_control:
        result["home_battle_control"] = home_battle_control
    if target_generation is not None:
        result["target_generation"] = target_generation
    return result


def _valid_interactive_development_lease(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") != INTERACTIVE_DEVELOPMENT_LEASE_SCHEMA_VERSION:
        return None
    lease_id = str(value.get("lease_id") or "").strip().lower()
    owner_label = " ".join(str(value.get("owner_label") or "").split())
    request_state = str(value.get("request_state") or "").strip().lower()
    runtime = _valid_interactive_development_runtime(value.get("runtime"))
    starting_evidence = _valid_interactive_development_evidence(
        value.get("starting_evidence")
    )
    if (
        len(lease_id) != 32
        or any(character not in "0123456789abcdef" for character in lease_id)
        or not owner_label
        or len(owner_label) > 96
        or request_state not in INTERACTIVE_DEVELOPMENT_REQUEST_STATES
        or runtime is None
        or starting_evidence is None
    ):
        return None
    timestamps: dict[str, str] = {}
    for name in ("requested_at", "heartbeat_at", "expires_at"):
        candidate = _bounded_text(value.get(name), 64)
        try:
            _timestamp_value(candidate)
        except ValueError:
            return None
        timestamps[name] = candidate
    if _timestamp_value(timestamps["expires_at"]) < _timestamp_value(
        timestamps["heartbeat_at"]
    ):
        return None
    result: dict[str, Any] = {
        "schema_version": INTERACTIVE_DEVELOPMENT_LEASE_SCHEMA_VERSION,
        "lease_id": lease_id,
        "owner_label": owner_label,
        "request_state": request_state,
        **timestamps,
        "runtime": runtime,
        "starting_evidence": starting_evidence,
    }
    owned_battle_start = value.get("owned_battle_start")
    if owned_battle_start is not None:
        if owned_battle_start is not True or not (
            starting_evidence.get("screen_state") == "HOME_SCREEN"
            and starting_evidence.get("home_battle_control") == "NEW_BATTLE"
            and starting_evidence.get("battle_active") is False
            and type(starting_evidence.get("target_generation")) is int
            and int(starting_evidence["target_generation"]) > 0
        ):
            return None
        result["owned_battle_start"] = True
    release_requested_at = value.get("release_requested_at")
    if release_requested_at is not None:
        normalized = _bounded_text(release_requested_at, 64)
        try:
            _timestamp_value(normalized)
        except ValueError:
            return None
        result["release_requested_at"] = normalized
    if request_state == "release_requested" and "release_requested_at" not in result:
        return None
    if request_state == "terminal":
        terminal_at = _bounded_text(value.get("terminal_at"), 64)
        disposition = _bounded_text(value.get("terminal_disposition"), 48).lower()
        reason = " ".join(str(value.get("terminal_reason") or "").split())[:256]
        try:
            _timestamp_value(terminal_at)
        except ValueError:
            return None
        if not disposition or not reason:
            return None
        result.update(
            {
                "terminal_at": terminal_at,
                "terminal_disposition": disposition,
                "terminal_reason": reason,
            }
        )
    return result


def normalize_interactive_development_lease(
    value: object,
) -> Optional[dict[str, Any]]:
    """Return the bounded schema-1 lease directive or ``None``."""

    return _valid_interactive_development_lease(value)


def _require_interactive_development_lease(
    data: Mapping[str, Any],
    *,
    lease_id: str,
) -> dict[str, Any]:
    lease = _valid_interactive_development_lease(
        data.get("interactive_development_lease")
    )
    if lease is None:
        raise ValueError("No valid interactive development lease request exists")
    if lease["lease_id"] != str(lease_id or "").strip().lower():
        raise ValueError("Interactive development lease ID does not match")
    return lease


def _require_emulator_maintenance(
    data: Mapping[str, object],
    request_id: str,
) -> dict[str, Any]:
    maintenance = normalize_emulator_maintenance(
        data.get("emulator_maintenance")
    )
    if maintenance is None:
        raise ValueError("No valid emulator maintenance request exists")
    normalized_request_id = str(request_id or "").strip().lower()
    if maintenance["request_id"] != normalized_request_id:
        raise ValueError("Emulator maintenance request ID does not match")
    return maintenance


def _same_emulator_host_identity(
    stored: object,
    candidate: Mapping[str, object],
    *,
    include_previous: bool,
) -> bool:
    if not isinstance(stored, Mapping):
        return False
    keys = [
        "host_id",
        "adb_port",
        "process_id",
        "process_started_at",
        "executable_path",
        "instance_name",
    ]
    if include_previous:
        keys.extend(["previous_process_id", "previous_process_started_at"])
    return all(stored.get(key) == candidate.get(key) for key in keys)


def _valid_strategy_apply_mode(value: object) -> str:
    normalized = str(value or "next_boundary").strip().lower()
    return normalized if normalized in STRATEGY_APPLY_MODES else "next_boundary"


def _valid_active_battle_identity(value: object) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        return None
    return normalized


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
    offers_repair = any(
        option.get("action") == "repair_restart" for option in options
    )
    repair_authority = (
        validate_workflow_evidence(value.get("repair_authority"))
        if value.get("repair_authority") is not None
        else None
    )
    if offers_repair != bool(
        repair_authority is not None
        and repair_authority.get("game_state") == "active_battle"
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
    if repair_authority is not None:
        directive["repair_authority"] = repair_authority
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


def _mapping_fingerprint(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _updated_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "ControlDirectiveError",
    "ControlDirectiveStore",
    "EMULATOR_LOCATION_SCHEMA_VERSION",
    "EXCLUSIVE_VALIDATION_OUTCOMES",
    "EXCLUSIVE_VALIDATION_STATUSES",
    "GATE_DECISION_STATUSES",
    "INTERACTIVE_DEVELOPMENT_LEASE_SCHEMA_VERSION",
    "INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS",
    "INTERACTIVE_DEVELOPMENT_REQUEST_STATES",
    "LEGACY_MODE_ALIASES",
    "MAXIMUM_GAME_SPEED_TARGET",
    "VALID_GAME_SPEED_TARGETS",
    "VALID_MODES",
    "VALID_STATES",
    "normalize_automation_mode",
    "normalize_emulator_location",
    "normalize_emulator_location_request",
    "normalize_game_speed_target",
    "normalize_emulator_maintenance",
    "normalize_interactive_development_lease",
]
