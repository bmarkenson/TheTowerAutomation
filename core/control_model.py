"""Typed Better Control Model state and evidence validation.

This module is deliberately free of transport and device I/O.  The control
surface projects these values for operators, while the runtime revalidates the
same evidence before it grants any battle-workflow authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional


CONTROL_MODEL_SCHEMA_VERSION = 1
BATTLE_WORKFLOW_SCHEMA_VERSION = 1
MANUAL_CONTROL_SCHEMA_VERSION = 1

BATTLE_INTENTS = frozenset({"start_battle", "attach_battle"})
BATTLE_WORKFLOW_STATUSES = frozenset(
    {
        "requested",
        "acknowledged",
        "awaiting_enable",
        "validating_save",
        "awaiting_configuration",
        "ready",
        "action_dispatched",
        "completed",
        "rejected",
        "interrupted",
        "failed",
        "cancelled",
    }
)
BATTLE_WORKFLOW_TERMINAL_STATUSES = frozenset(
    {"completed", "rejected", "interrupted", "failed", "cancelled"}
)
MANUAL_CONTROL_STATUSES = frozenset(
    {
        "pause_requested",
        "active",
        "return_requested",
        "awaiting_enable",
        "reconciling",
        "completed",
        "interrupted",
        "failed",
    }
)
MANUAL_CONTROL_TERMINAL_STATUSES = frozenset(
    {"completed", "interrupted", "failed"}
)
OBSERVED_GAME_STATES = frozenset(
    {
        "home_new_battle",
        "home_resume_battle",
        "active_battle",
        "game_over",
        "tournament_results",
        "unknown",
    }
)
PRIMARY_STATES = frozenset(
    {
        "HOME",
        "HOME_SCREEN",
        "RUNNING",
        "GAME_OVER",
        "TOURNAMENT_RESULTS",
        "WORKSHOP",
        "UNKNOWN",
    }
)
HOME_BATTLE_CONTROLS = frozenset(
    {"NEW_BATTLE", "RESUME_BATTLE", "UNKNOWN"}
)


def observed_game_state(
    primary_state: object,
    home_battle_control: object,
    *,
    active_battle: bool,
) -> str:
    """Classify the observable game boundary without inferring an action."""

    state = str(primary_state or "UNKNOWN").strip().upper()
    control = str(home_battle_control or "UNKNOWN").strip().upper()
    if state in {"HOME", "HOME_SCREEN"}:
        if control == "NEW_BATTLE":
            return "home_new_battle"
        if control == "RESUME_BATTLE":
            return "home_resume_battle"
        return "unknown"
    if state == "RUNNING" and active_battle:
        return "active_battle"
    if state == "GAME_OVER":
        return "game_over"
    if state == "TOURNAMENT_RESULTS":
        return "tournament_results"
    return "unknown"


def validate_observation(value: object) -> Optional[dict[str, Any]]:
    """Return one strict runtime observation or ``None`` when malformed."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    observation_id = _bounded(value.get("observation_id"), 128)
    observed_at = _aware_timestamp(value.get("observed_at"))
    primary_state = str(value.get("primary_state") or "UNKNOWN").upper()
    home_control = str(
        value.get("home_battle_control") or "UNKNOWN"
    ).upper()
    if (
        not observation_id
        or observed_at is None
        or primary_state not in PRIMARY_STATES
        or home_control not in HOME_BATTLE_CONTROLS
        or type(value.get("active_battle")) is not bool
    ):
        return None
    active_battle = bool(value["active_battle"])
    classification = observed_game_state(
        primary_state,
        home_control,
        active_battle=active_battle,
    )
    declared = str(value.get("game_state") or classification).strip().lower()
    if declared != classification or declared not in OBSERVED_GAME_STATES:
        return None
    target_generation = value.get("target_generation")
    if target_generation is not None and (
        type(target_generation) is not int or target_generation < 0
    ):
        return None
    scope = _optional_bounded(value.get("activity_scope_run_id"), 128)
    if value.get("activity_scope_run_id") is not None and scope is None:
        return None
    return {
        "schema_version": 1,
        "observation_id": observation_id,
        "observed_at": observed_at,
        "primary_state": primary_state,
        "home_battle_control": home_control,
        "game_state": classification,
        "active_battle": active_battle,
        "activity_scope_run_id": scope,
        "target_generation": target_generation,
    }


def workflow_evidence_from_authority(
    authority: object,
) -> Optional[dict[str, Any]]:
    """Bind fresh observation evidence to its exact live runtime owner."""

    if not isinstance(authority, Mapping):
        return None
    if not (
        authority.get("available") is True
        and authority.get("stale") is False
        and authority.get("runtime_active") is True
        and authority.get("owner_matches_active_runtime") is True
    ):
        return None
    owner = authority.get("owner")
    runtime_model = authority.get("control_model")
    if not isinstance(owner, Mapping) or not isinstance(runtime_model, Mapping):
        return None
    observation = validate_observation(runtime_model.get("observation"))
    if observation is None:
        return None
    runtime_id = _bounded(owner.get("runtime_id"), 128)
    adb_target = _bounded(owner.get("adb_target"), 128)
    try:
        pid = int(owner.get("pid"))
    except (TypeError, ValueError):
        return None
    if not runtime_id or pid <= 0 or not adb_target or adb_target == "unknown":
        return None
    return {
        "schema_version": 1,
        "runtime_id": runtime_id,
        "pid": pid,
        "adb_target": adb_target,
        **observation,
    }


def validate_workflow_evidence(value: object) -> Optional[dict[str, Any]]:
    """Validate the exact evidence persisted with a workflow request."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    runtime_id = _bounded(value.get("runtime_id"), 128)
    adb_target = _bounded(value.get("adb_target"), 128)
    try:
        pid = int(value.get("pid"))
    except (TypeError, ValueError):
        return None
    observation = validate_observation(value)
    if (
        not runtime_id
        or pid <= 0
        or not adb_target
        or adb_target == "unknown"
        or observation is None
    ):
        return None
    return {
        "schema_version": 1,
        "runtime_id": runtime_id,
        "pid": pid,
        "adb_target": adb_target,
        **observation,
    }


def validate_battle_workflow(value: object) -> Optional[dict[str, Any]]:
    """Return a normalized durable battle-workflow request."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    request_id = _bounded(value.get("request_id"), 64)
    intent = str(value.get("intent") or "").strip().lower()
    status = str(value.get("status") or "").strip().lower()
    requested_at = _aware_timestamp(value.get("requested_at"))
    evidence = validate_workflow_evidence(value.get("evidence"))
    if (
        not request_id
        or intent not in BATTLE_INTENTS
        or status not in BATTLE_WORKFLOW_STATUSES
        or requested_at is None
        or evidence is None
    ):
        return None
    result: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "intent": intent,
        "status": status,
        "requested_at": requested_at,
        "evidence": evidence,
    }
    _copy_optional_fields(
        value,
        result,
        timestamps=("updated_at", "acknowledged_at", "completed_at"),
        text=("reason", "updated_by"),
        mappings=("acknowledgement", "save_receipt", "configuration"),
    )
    return result


def validate_manual_control(value: object) -> Optional[dict[str, Any]]:
    """Return one normalized Take/Return Control ledger entry."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    workflow_id = _bounded(value.get("manual_control_id"), 64)
    status = str(value.get("status") or "").strip().lower()
    reason = str(value.get("reason") or "operator").strip().lower()
    requested_at = _aware_timestamp(value.get("requested_at"))
    evidence = validate_workflow_evidence(value.get("starting_evidence"))
    if (
        not workflow_id
        or status not in MANUAL_CONTROL_STATUSES
        or reason not in {"operator", "unexpected_manual_activity"}
        or requested_at is None
        or evidence is None
    ):
        return None
    result: dict[str, Any] = {
        "schema_version": 1,
        "manual_control_id": workflow_id,
        "status": status,
        "reason": reason,
        "requested_at": requested_at,
        "starting_evidence": evidence,
    }
    _copy_optional_fields(
        value,
        result,
        timestamps=(
            "updated_at",
            "acknowledged_at",
            "return_requested_at",
            "completed_at",
        ),
        text=("detail", "updated_by", "refresh_status"),
        mappings=(
            "pause_acknowledgement",
            "return_evidence",
            "save_receipt",
            "configuration",
        ),
    )
    if "return_evidence" in result:
        normalized_return = validate_workflow_evidence(result["return_evidence"])
        if normalized_return is None:
            return None
        result["return_evidence"] = normalized_return
    return result


def intent_matches_evidence(intent: str, evidence: Mapping[str, Any]) -> bool:
    """Return whether an explicit intent matches the observed boundary."""

    game_state = str(evidence.get("game_state") or "")
    if intent == "start_battle":
        return game_state == "home_new_battle"
    if intent == "attach_battle":
        return game_state in {"home_resume_battle", "active_battle"}
    return False


def _copy_optional_fields(
    source: Mapping[str, Any],
    target: dict[str, Any],
    *,
    timestamps: tuple[str, ...],
    text: tuple[str, ...],
    mappings: tuple[str, ...],
) -> None:
    for name in timestamps:
        if source.get(name) is None:
            continue
        normalized = _aware_timestamp(source.get(name))
        if normalized is not None:
            target[name] = normalized
    for name in text:
        if source.get(name) is None:
            continue
        normalized = _bounded(source.get(name), 512)
        if normalized:
            target[name] = normalized
    for name in mappings:
        if isinstance(source.get(name), Mapping):
            target[name] = dict(source[name])


def _aware_timestamp(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat(timespec="seconds")


def _bounded(value: object, limit: int) -> Optional[str]:
    text = " ".join(str(value or "").split())
    if not text or len(text) > limit:
        return None
    return text


def _optional_bounded(value: object, limit: int) -> Optional[str]:
    if value is None:
        return None
    return _bounded(value, limit)


__all__ = [
    "BATTLE_INTENTS",
    "BATTLE_WORKFLOW_SCHEMA_VERSION",
    "BATTLE_WORKFLOW_STATUSES",
    "BATTLE_WORKFLOW_TERMINAL_STATUSES",
    "CONTROL_MODEL_SCHEMA_VERSION",
    "MANUAL_CONTROL_SCHEMA_VERSION",
    "MANUAL_CONTROL_STATUSES",
    "MANUAL_CONTROL_TERMINAL_STATUSES",
    "intent_matches_evidence",
    "observed_game_state",
    "validate_battle_workflow",
    "validate_manual_control",
    "validate_observation",
    "validate_workflow_evidence",
    "workflow_evidence_from_authority",
]
