"""Typed Better Control Model state and evidence validation.

This module is deliberately free of transport and device I/O.  The control
surface projects these values for operators, while the runtime revalidates the
same evidence before it grants any battle-workflow authority.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Optional

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_temporal import RunningAttachmentTemporalBinding
from core.strategy_authoring import FARM_SETTING_REGISTRY


CONTROL_MODEL_SCHEMA_VERSION = 1
BATTLE_WORKFLOW_SCHEMA_VERSION = 1
MANUAL_CONTROL_SCHEMA_VERSION = 1
SETUP_CAPTURE_SCHEMA_VERSION = 1
SAVE_RECONCILIATION_RECEIPT_SCHEMA_VERSION = 1
PROCESS_RESTART_HANDOFF_SCHEMA_VERSION = 1
PROCESS_RESTART_HANDOFF_STATUSES = frozenset(
    {"pending", "completed", "failed", "cancelled"}
)

RUNNING_SAVE_RECONCILIATION_KINDS = frozenset(
    {
        "running_attachment_reconciliation",
        "return_control_reconciliation",
    }
)
HOME_RETURN_RECONCILIATION_KIND = "return_control_home_reconciliation"
TERMINAL_RETURN_RECONCILIATION_KIND = (
    "return_control_terminal_reconciliation"
)
MANUAL_SURRENDER_COLLECTIONS = frozenset({"minimal", "full"})
SAVE_RECONCILIATION_DISPOSITIONS = frozenset(
    {
        "attachment_baseline",
        "same_battle",
        "later_battle",
    }
)
SAVE_RECONCILIATION_CONFIGURATION_STATUSES = frozenset(
    {"observation_only", "complete", "partial"}
)
RUNNING_UI_FALLBACK_SOURCE = "battle_history_ui"
HOME_UI_FALLBACK_SOURCE = "home_configuration_ui"
TERMINAL_UI_FALLBACK_SOURCE = "terminal_stats_ui"
UI_RECONCILIATION_SOURCES = frozenset(
    {
        RUNNING_UI_FALLBACK_SOURCE,
        HOME_UI_FALLBACK_SOURCE,
        TERMINAL_UI_FALLBACK_SOURCE,
    }
)
_REPORT_SCOPE_UNAVAILABLE = "report-scope-unavailable"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

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
        "awaiting_configuration",
        "awaiting_manual_correction",
        "completed",
        "interrupted",
        "failed",
    }
)
MANUAL_CONTROL_TERMINAL_STATUSES = frozenset(
    {"completed", "interrupted", "failed"}
)
SETUP_CAPTURE_STATUSES = frozenset(
    {
        "requested",
        "acknowledged",
        "capturing",
        "ready",
        "saved",
        "unavailable",
        "interrupted",
        "failed",
        "cancelled",
    }
)
SETUP_CAPTURE_TERMINAL_STATUSES = frozenset(
    {"saved", "unavailable", "interrupted", "failed", "cancelled"}
)
SETUP_CAPTURE_AUTHORITY_OUTCOMES = frozenset(
    {
        "continuity_gated",
        "preserved",
        "paused_for_safety",
        "unchanged_paused",
    }
)
SETUP_CAPTURE_GAME_STATES = frozenset(
    {"home_new_battle", "home_resume_battle", "active_battle"}
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
    # Activity scope is log/report segmentation only.  Malformed optional
    # metadata must never discard otherwise valid runtime authority evidence.
    scope = _optional_bounded(value.get("activity_scope_run_id"), 128)
    active_round_identity = value.get(
        "active_round_identity_fingerprint"
    )
    if active_round_identity is not None and not _sha256(
        active_round_identity
    ):
        return None
    normalized = {
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
    if active_round_identity is not None:
        normalized["active_round_identity_fingerprint"] = str(
            active_round_identity
        )
    return normalized


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


def validate_process_restart_handoff(
    value: object,
) -> Optional[dict[str, Any]]:
    """Return one same-battle process-restart attachment handoff.

    The old runtime's evidence is retained only as the expected identity and
    target for a fresh Attach workflow.  It is never itself replayable input
    authority in the replacement process.
    """

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    handoff_id = _bounded(value.get("handoff_id"), 64)
    status = str(value.get("status") or "").strip().lower()
    requested_at = _aware_timestamp(value.get("requested_at"))
    expected_identity = value.get(
        "expected_active_round_identity_fingerprint"
    )
    source_evidence = validate_workflow_evidence(
        value.get("source_evidence")
    )
    if (
        handoff_id is None
        or status not in PROCESS_RESTART_HANDOFF_STATUSES
        or requested_at is None
        or not _sha256(expected_identity)
        or source_evidence is None
        or source_evidence.get("game_state") != "active_battle"
        or source_evidence.get("active_round_identity_fingerprint")
        != expected_identity
        or value.get("resume_state") != "RUNNING"
    ):
        return None
    result: dict[str, Any] = {
        "schema_version": PROCESS_RESTART_HANDOFF_SCHEMA_VERSION,
        "handoff_id": handoff_id,
        "status": status,
        "requested_at": requested_at,
        "resume_state": "RUNNING",
        "expected_active_round_identity_fingerprint": str(
            expected_identity
        ),
        "source_evidence": source_evidence,
    }
    workflow_id = _optional_bounded(value.get("workflow_id"), 64)
    if value.get("workflow_id") is not None and workflow_id is None:
        return None
    if workflow_id is not None:
        result["workflow_id"] = workflow_id
    actual_identity = value.get("actual_active_round_identity_fingerprint")
    if actual_identity is not None:
        if not _sha256(actual_identity):
            return None
        result["actual_active_round_identity_fingerprint"] = str(
            actual_identity
        )
    _copy_optional_fields(
        value,
        result,
        timestamps=("updated_at", "completed_at"),
        text=("reason", "updated_by"),
        mappings=(),
    )
    if status == "pending":
        if "completed_at" in result or actual_identity is not None:
            return None
    else:
        if "completed_at" not in result or not result.get("reason"):
            return None
    if status == "completed" and (
        workflow_id is None or actual_identity != expected_identity
    ):
        return None
    return result


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
    if value.get("strategy") is not None:
        strategy = _bounded(value.get("strategy"), 100)
        if strategy is None:
            return None
        result["strategy"] = strategy.strip().lower()
    if value.get("strategy_request_id") is not None:
        strategy_request_id = _bounded(
            value.get("strategy_request_id"),
            100,
        )
        if strategy_request_id is None or "strategy" not in result:
            return None
        result["strategy_request_id"] = strategy_request_id
    if value.get("strategy_definition_fingerprint") is not None:
        strategy_fingerprint = _bounded(
            value.get("strategy_definition_fingerprint"),
            100,
        )
        if (
            not _sha256(strategy_fingerprint)
            or result.get("strategy") in {None, "none"}
            or "strategy_request_id" not in result
        ):
            return None
        result["strategy_definition_fingerprint"] = strategy_fingerprint
    _copy_optional_fields(
        value,
        result,
        timestamps=("updated_at", "acknowledged_at", "completed_at"),
        text=("reason", "updated_by"),
        mappings=("acknowledgement", "save_receipt", "configuration"),
    )
    if intent == "attach_battle" and status in {"ready", "completed"}:
        receipt = validate_save_reconciliation_receipt(
            result.get("save_receipt"),
            expected_kind="running_attachment_reconciliation",
            expected_workflow_id=request_id,
        )
        if receipt is not None:
            result["save_receipt"] = receipt
        elif status == "ready":
            return None
        else:
            configuration = result.get("configuration")
            if not (
                isinstance(configuration, Mapping)
                and configuration.get("schema_version") == 1
                and configuration.get("stage") == "completed"
                and configuration.get("reporting_status") == "unavailable"
                and configuration.get("attachment_mode") == "observation_only"
                and configuration.get("degraded") is True
            ):
                return None
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
        text=(
            "detail",
            "updated_by",
            "refresh_status",
            "surrender_collection",
        ),
        mappings=(
            "pause_acknowledgement",
            "return_evidence",
            "save_receipt",
            "configuration",
            "terminal_evidence",
        ),
    )
    collection = str(result.get("surrender_collection") or "minimal").lower()
    if collection not in MANUAL_SURRENDER_COLLECTIONS:
        return None
    result["surrender_collection"] = collection
    if "return_evidence" in result:
        normalized_return = validate_workflow_evidence(result["return_evidence"])
        if normalized_return is None:
            return None
        result["return_evidence"] = normalized_return
    if "terminal_evidence" in result:
        terminal_evidence = validate_manual_terminal_evidence(
            result["terminal_evidence"],
            expected_workflow_id=workflow_id,
        )
        if terminal_evidence is None:
            return None
        result["terminal_evidence"] = terminal_evidence
    if status == "completed":
        receipt = validate_save_reconciliation_receipt(
            result.get("save_receipt"),
            expected_workflow_id=workflow_id,
        )
        if receipt is None or receipt.get("kind") not in {
            "return_control_reconciliation",
            HOME_RETURN_RECONCILIATION_KIND,
            TERMINAL_RETURN_RECONCILIATION_KIND,
        }:
            return None
        result["save_receipt"] = receipt
    return result


def validate_setup_capture_preview(
    value: object,
    *,
    evidence: object = None,
) -> Optional[dict[str, Any]]:
    """Validate one exact runtime-issued, forced-save authoring projection."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "status",
        "mapping_id",
        "mapping_maturity",
        "effective_mapping_fingerprint",
        "captured_at",
        "acquisition",
        "settings",
        "captured_check_ids",
        "unresolved",
        "publication_activates_strategy",
        "saving_activates_strategy",
        "workflow_binding",
        "capture_origin",
    }:
        return None
    mapping_id = _bounded(value.get("mapping_id"), 128)
    effective_mapping_fingerprint = value.get(
        "effective_mapping_fingerprint"
    )
    maturity = str(value.get("mapping_maturity") or "").strip().lower()
    captured_at = _aware_timestamp(value.get("captured_at"))
    acquisition = _validated_forced_acquisition_provenance(
        value.get("acquisition")
    )
    settings = value.get("settings")
    unresolved = value.get("unresolved")
    if (
        value.get("schema_version") != SETUP_CAPTURE_SCHEMA_VERSION
        or value.get("status") not in {"complete", "partial"}
        or mapping_id is None
        or maturity not in {"candidate", "validated"}
        or not _sha256(effective_mapping_fingerprint)
        or captured_at is None
        or acquisition is None
        or acquisition["timing"]["captured_at"] != captured_at
        or not isinstance(settings, Mapping)
        or not settings
        or len(settings) > len(FARM_SETTING_REGISTRY)
        or not isinstance(unresolved, list)
        or len(unresolved) > 64
        or value.get("publication_activates_strategy") is not False
        or value.get("saving_activates_strategy") is not False
    ):
        return None
    try:
        canonical_settings = {
            str(setting_id): FARM_SETTING_REGISTRY[str(setting_id)].normalizer(
                setting_value
            )
            for setting_id, setting_value in settings.items()
        }
        json.dumps(canonical_settings)
    except (KeyError, TypeError, ValueError):
        return None
    if canonical_settings != dict(settings):
        return None
    try:
        captured_check_ids = _check_id_list(value.get("captured_check_ids", ()))
    except (TypeError, ValueError):
        return None
    if list(value.get("captured_check_ids") or []) != captured_check_ids:
        return None

    normalized_unresolved: list[dict[str, Any]] = []
    unresolved_ids: set[str] = set()
    for item in unresolved:
        if not isinstance(item, Mapping):
            return None
        required = {
            "setting_id",
            "display_name",
            "source_check_ids",
            "status",
            "reason",
        }
        if not required <= set(item) or set(item) - (required | {"observed_value"}):
            return None
        setting_id = _bounded(item.get("setting_id"), 128)
        display_name = _bounded(item.get("display_name"), 128)
        reason = _bounded(item.get("reason"), 512)
        status = str(item.get("status") or "").strip().lower()
        try:
            source_check_ids = _check_id_list(item.get("source_check_ids", ()))
            if "observed_value" in item:
                json.dumps(item.get("observed_value"))
        except (TypeError, ValueError):
            return None
        if (
            setting_id is None
            or display_name is None
            or reason is None
            or not source_check_ids
            or status not in {
                "unresolved",
                "unsupported_authoring_value",
                "observed_not_authorable",
            }
            or setting_id in unresolved_ids
            or setting_id in canonical_settings
        ):
            return None
        unresolved_ids.add(setting_id)
        normalized_item: dict[str, Any] = {
            "setting_id": setting_id,
            "display_name": display_name,
            "source_check_ids": source_check_ids,
            "status": status,
            "reason": reason,
        }
        if "observed_value" in item:
            normalized_item["observed_value"] = item.get("observed_value")
        normalized_unresolved.append(normalized_item)
    if normalized_unresolved != unresolved:
        return None
    if (value.get("status") == "complete") != (not normalized_unresolved):
        return None

    binding = value.get("workflow_binding")
    if not isinstance(binding, Mapping) or set(binding) != {
        "schema_version",
        "game_state",
        "runtime_session_fingerprint",
        "activity_scope_fingerprint",
        "target_generation_fingerprint",
        "active_round_identity_fingerprint",
    }:
        return None
    game_state = str(binding.get("game_state") or "").strip().lower()
    active_round = binding.get("active_round_identity_fingerprint")
    if (
        binding.get("schema_version") != 1
        or game_state not in SETUP_CAPTURE_GAME_STATES
        or not _sha256(binding.get("runtime_session_fingerprint"))
        or not _sha256(binding.get("activity_scope_fingerprint"))
        or not _sha256(binding.get("target_generation_fingerprint"))
        or binding.get("target_generation_fingerprint")
        != acquisition["binding_fingerprint"]
        or (
            game_state == "home_new_battle"
            and active_round is not None
        )
        or (
            game_state in {"active_battle", "home_resume_battle"}
            and not _sha256(active_round)
        )
    ):
        return None
    normalized_evidence = (
        validate_workflow_evidence(evidence)
        if evidence is not None
        else None
    )
    if evidence is not None and normalized_evidence is None:
        return None
    if normalized_evidence is not None:
        try:
            expected_target = PlayerSaveTargetBinding(
                normalized_evidence["adb_target"],
                int(normalized_evidence.get("target_generation") or 0),
            )
        except (TypeError, ValueError):
            return None
        if (
            game_state != normalized_evidence["game_state"]
            or binding["target_generation_fingerprint"]
            != expected_target.fingerprint
            or binding["runtime_session_fingerprint"]
            != _fingerprint(
                "setup-capture-runtime",
                normalized_evidence["runtime_id"],
            )
        ):
            return None
    origin = value.get("capture_origin")
    if not isinstance(origin, Mapping) or set(origin) != {
        "schema_version",
        "acquisition_source",
        "source_manual_control_fingerprint",
    }:
        return None
    acquisition_source = str(
        origin.get("acquisition_source") or ""
    ).strip().lower()
    source_manual_fingerprint = origin.get(
        "source_manual_control_fingerprint"
    )
    if (
        origin.get("schema_version") != 1
        or acquisition_source
        not in {
            "new_setup_capture_refresh",
            "retained_return_control_refresh",
        }
        or (
            acquisition_source == "new_setup_capture_refresh"
            and source_manual_fingerprint is not None
        )
        or (
            acquisition_source == "retained_return_control_refresh"
            and not _sha256(source_manual_fingerprint)
        )
    ):
        return None
    return {
        "schema_version": SETUP_CAPTURE_SCHEMA_VERSION,
        "status": str(value["status"]),
        "mapping_id": mapping_id,
        "mapping_maturity": maturity,
        "effective_mapping_fingerprint": str(
            effective_mapping_fingerprint
        ),
        "captured_at": captured_at,
        "acquisition": acquisition,
        "settings": canonical_settings,
        "captured_check_ids": captured_check_ids,
        "unresolved": normalized_unresolved,
        "publication_activates_strategy": False,
        "saving_activates_strategy": False,
        "workflow_binding": {
            "schema_version": 1,
            "game_state": game_state,
            "runtime_session_fingerprint": str(
                binding["runtime_session_fingerprint"]
            ),
            "activity_scope_fingerprint": str(
                binding["activity_scope_fingerprint"]
            ),
            "target_generation_fingerprint": str(
                binding["target_generation_fingerprint"]
            ),
            "active_round_identity_fingerprint": (
                str(active_round) if active_round is not None else None
            ),
        },
        "capture_origin": {
            "schema_version": 1,
            "acquisition_source": acquisition_source,
            "source_manual_control_fingerprint": (
                str(source_manual_fingerprint)
                if source_manual_fingerprint is not None
                else None
            ),
        },
    }


def validate_setup_capture(value: object) -> Optional[dict[str, Any]]:
    """Return one normalized save-backed setup-capture ledger entry."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    request_id = _bounded(value.get("request_id"), 64)
    status = str(value.get("status") or "").strip().lower()
    requested_at = _aware_timestamp(value.get("requested_at"))
    evidence = validate_workflow_evidence(value.get("evidence"))
    acquisition_source = str(
        value.get("acquisition_source") or "new_setup_capture_refresh"
    ).strip().lower()
    source_manual_control_id = _optional_bounded(
        value.get("source_manual_control_id"),
        64,
    )
    authority_outcome = str(
        value.get("authority_outcome") or ""
    ).strip().lower()
    if (
        request_id is None
        or status not in SETUP_CAPTURE_STATUSES
        or requested_at is None
        or evidence is None
        or evidence.get("game_state") not in SETUP_CAPTURE_GAME_STATES
        or acquisition_source
        not in {
            "new_setup_capture_refresh",
            "retained_return_control_refresh",
        }
        or (
            acquisition_source == "retained_return_control_refresh"
            and source_manual_control_id is None
        )
        or (
            acquisition_source == "new_setup_capture_refresh"
            and value.get("source_manual_control_id") is not None
        )
        or (
            authority_outcome
            and authority_outcome not in SETUP_CAPTURE_AUTHORITY_OUTCOMES
        )
    ):
        return None
    result: dict[str, Any] = {
        "schema_version": SETUP_CAPTURE_SCHEMA_VERSION,
        "request_id": request_id,
        "status": status,
        "requested_at": requested_at,
        "evidence": evidence,
        "acquisition_source": acquisition_source,
    }
    if source_manual_control_id is not None:
        result["source_manual_control_id"] = source_manual_control_id
    if authority_outcome:
        result["authority_outcome"] = authority_outcome
    _copy_optional_fields(
        value,
        result,
        timestamps=("updated_at", "acknowledged_at", "completed_at"),
        text=("reason", "updated_by", "preview_fingerprint"),
        mappings=("acknowledgement", "preview", "saved_result"),
    )
    if status in {"ready", "saved"}:
        preview = validate_setup_capture_preview(
            result.get("preview"),
            evidence=evidence,
        )
        preview_fingerprint = str(result.get("preview_fingerprint") or "")
        if (
            preview is None
            or not _sha256(preview_fingerprint)
            or preview_fingerprint != _mapping_fingerprint(preview)
        ):
            return None
        result["preview"] = preview
    if status == "saved" and not isinstance(result.get("saved_result"), Mapping):
        return None
    return result


def validate_manual_terminal_evidence(
    value: object,
    *,
    expected_workflow_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Validate one exact-run manual terminal classification."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    status = str(value.get("status") or "").strip().lower()
    observation_id = _bounded(value.get("observation_id"), 128)
    scope_fingerprint = value.get("activity_scope_fingerprint")
    reason = _bounded(value.get("reason"), 256) or ""
    if (
        status not in {"confirmed_surrender", "confirmed_other", "unavailable"}
        or observation_id is None
        or not _sha256(scope_fingerprint)
    ):
        return None
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "observation_id": observation_id,
        "activity_scope_fingerprint": str(scope_fingerprint),
        "reason": reason,
    }
    if status == "unavailable":
        if value.get("receipt") is not None:
            return None
        return result
    receipt = validate_save_reconciliation_receipt(
        value.get("receipt"),
        expected_kind=TERMINAL_RETURN_RECONCILIATION_KIND,
        expected_workflow_id=expected_workflow_id,
        expected_observation_id=observation_id,
    )
    if receipt is None:
        return None
    is_surrender = receipt["terminal"]["surrendered"] is True
    if (status == "confirmed_surrender") != is_surrender:
        return None
    result["receipt"] = receipt
    battle_id = _optional_bounded(value.get("battle_id"), 96)
    if value.get("battle_id") is not None and battle_id is None:
        return None
    if battle_id is not None:
        result["battle_id"] = battle_id
    return result


def build_running_save_reconciliation_receipt(
    *,
    kind: str,
    workflow_id: str,
    observation_id: str,
    acquisition: PlayerSaveAcquisitionBundle,
    temporal_binding: RunningAttachmentTemporalBinding,
    disposition: str,
    resolved_check_ids: Iterable[str] = (),
    unresolved_check_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build the persisted report for one process-local typed save decision.

    The returned map is deliberately redacted.  Runtime callers must still
    retain and revalidate the typed acquisition and temporal binding before
    granting authority; this receipt is durable evidence, not a replay token.
    """

    normalized_kind = str(kind or "").strip().lower()
    normalized_workflow_id = _bounded(workflow_id, 64)
    normalized_observation_id = _bounded(observation_id, 128)
    normalized_disposition = str(disposition or "").strip().lower()
    if (
        normalized_kind not in RUNNING_SAVE_RECONCILIATION_KINDS
        or normalized_workflow_id is None
        or normalized_observation_id is None
        or normalized_disposition not in SAVE_RECONCILIATION_DISPOSITIONS
        or not isinstance(acquisition, PlayerSaveAcquisitionBundle)
        or not acquisition.complete
        or acquisition.acquisition_type
        is not PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        or not isinstance(temporal_binding, RunningAttachmentTemporalBinding)
        or not temporal_binding.final
        or acquisition.binding != temporal_binding.target_binding
        or acquisition.captured_at is None
        or acquisition.captured_at.isoformat() != temporal_binding.captured_at
    ):
        raise ValueError(
            "running save reconciliation requires complete, final typed evidence"
        )
    resolved = _check_id_list(resolved_check_ids)
    unresolved = _check_id_list(unresolved_check_ids)
    if set(resolved).intersection(unresolved):
        raise ValueError("resolved and unresolved save checks must be disjoint")
    configuration_status = (
        "partial" if unresolved else "complete" if resolved else "observation_only"
    )
    temporal_provenance = temporal_binding.redacted()
    receipt = {
        "schema_version": SAVE_RECONCILIATION_RECEIPT_SCHEMA_VERSION,
        "kind": normalized_kind,
        "workflow_id": normalized_workflow_id,
        "observation_id": normalized_observation_id,
        "acquisition": acquisition.redacted_provenance(),
        "temporal": temporal_provenance,
        "continuity": {
            "status": "battle_identity_bound",
            "disposition": normalized_disposition,
            "battle_identity_fingerprint": temporal_provenance[
                "round_identity"
            ],
        },
        "configuration": {
            "status": configuration_status,
            "resolved_check_ids": resolved,
            "unresolved_check_ids": unresolved,
        },
    }
    normalized = validate_save_reconciliation_receipt(
        receipt,
        expected_kind=normalized_kind,
        expected_workflow_id=normalized_workflow_id,
        expected_observation_id=normalized_observation_id,
    )
    if normalized is None:  # pragma: no cover - builder and validator share schema
        raise ValueError("built save reconciliation receipt is invalid")
    return normalized


def build_running_ui_reconciliation_receipt(
    *,
    kind: str,
    workflow_id: str,
    observation_id: str,
    evidence: Mapping[str, object],
    disposition: str,
    reason: str,
    fallback_complete: bool,
    resolved_check_ids: Iterable[str] = (),
    unresolved_check_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a bound receipt for the established running UI fallback.

    This receipt is durable reporting evidence only. Runtime authority still
    depends on the matching process-local workflow claim and fresh live
    evidence; the receipt cannot be replayed after a restart.
    """

    normalized_kind = str(kind or "").strip().lower()
    normalized_workflow_id = _bounded(workflow_id, 64)
    normalized_observation_id = _bounded(observation_id, 128)
    normalized_disposition = str(disposition or "").strip().lower()
    normalized_evidence = validate_workflow_evidence(evidence)
    if (
        normalized_kind not in RUNNING_SAVE_RECONCILIATION_KINDS
        or normalized_workflow_id is None
        or normalized_observation_id is None
        or normalized_disposition not in SAVE_RECONCILIATION_DISPOSITIONS
        or normalized_evidence is None
        or normalized_evidence.get("game_state") != "active_battle"
        or not _sha256(
            normalized_evidence.get(
                "active_round_identity_fingerprint"
            )
        )
    ):
        raise ValueError(
            "running UI reconciliation requires exact active-battle evidence"
        )
    resolved = _check_id_list(resolved_check_ids)
    unresolved = _check_id_list(unresolved_check_ids)
    if set(resolved).intersection(unresolved):
        raise ValueError("resolved and unresolved UI checks must be disjoint")
    configuration_status = (
        "partial" if unresolved else "complete" if resolved else "observation_only"
    )
    ui_fallback = _build_ui_fallback_provenance(
        normalized_evidence,
        source=RUNNING_UI_FALLBACK_SOURCE,
        reason=reason,
        complete=fallback_complete,
    )
    receipt = {
        "schema_version": SAVE_RECONCILIATION_RECEIPT_SCHEMA_VERSION,
        "kind": normalized_kind,
        "workflow_id": normalized_workflow_id,
        "observation_id": normalized_observation_id,
        "ui_fallback": ui_fallback,
        "continuity": {
            "status": "battle_identity_bound",
            "disposition": normalized_disposition,
            "battle_identity_fingerprint": ui_fallback[
                "active_round_identity_fingerprint"
            ],
        },
        "configuration": {
            "status": configuration_status,
            "resolved_check_ids": resolved,
            "unresolved_check_ids": unresolved,
        },
    }
    normalized = validate_save_reconciliation_receipt(
        receipt,
        expected_kind=normalized_kind,
        expected_workflow_id=normalized_workflow_id,
        expected_observation_id=normalized_observation_id,
    )
    if normalized is None:  # pragma: no cover - builder and validator share schema
        raise ValueError("built running UI reconciliation receipt is invalid")
    return normalized


def validate_save_reconciliation_receipt(
    value: object,
    *,
    expected_kind: Optional[str] = None,
    expected_workflow_id: Optional[str] = None,
    expected_observation_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Validate one redacted save or supported-UI workflow receipt."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    kind = str(value.get("kind") or "").strip().lower()
    workflow_id = _bounded(value.get("workflow_id"), 64)
    observation_id = _bounded(value.get("observation_id"), 128)
    if (
        kind not in {
            *RUNNING_SAVE_RECONCILIATION_KINDS,
            HOME_RETURN_RECONCILIATION_KIND,
            TERMINAL_RETURN_RECONCILIATION_KIND,
        }
        or workflow_id is None
        or observation_id is None
        or (expected_kind is not None and kind != str(expected_kind))
        or (
            expected_workflow_id is not None
            and workflow_id != str(expected_workflow_id)
        )
        or (
            expected_observation_id is not None
            and observation_id != str(expected_observation_id)
        )
    ):
        return None
    ui_fallback = _validated_ui_fallback_provenance(
        value.get("ui_fallback")
    )
    if kind == TERMINAL_RETURN_RECONCILIATION_KIND:
        acquisition = _validated_natural_terminal_acquisition_provenance(
            value.get("acquisition")
        )
        if (acquisition is None) == (ui_fallback is None):
            return None
        return _validated_terminal_return_receipt(
            value,
            kind=kind,
            workflow_id=workflow_id,
            observation_id=observation_id,
            acquisition=acquisition,
            ui_fallback=ui_fallback,
        )
    acquisition = _validated_forced_acquisition_provenance(
        value.get("acquisition")
    )
    if (acquisition is None) == (ui_fallback is None):
        return None
    if kind == HOME_RETURN_RECONCILIATION_KIND:
        return _validated_home_return_receipt(
            value,
            kind=kind,
            workflow_id=workflow_id,
            observation_id=observation_id,
            acquisition=acquisition,
            ui_fallback=ui_fallback,
        )
    temporal = (
        _validated_running_temporal_provenance(value.get("temporal"))
        if acquisition is not None
        else None
    )
    continuity = value.get("continuity")
    configuration = value.get("configuration")
    if (
        not isinstance(continuity, Mapping)
        or continuity.get("status") != "battle_identity_bound"
        or str(continuity.get("disposition") or "")
        not in SAVE_RECONCILIATION_DISPOSITIONS
        or not _sha256(
            continuity.get("battle_identity_fingerprint")
        )
        or not isinstance(configuration, Mapping)
        or str(configuration.get("status") or "")
        not in SAVE_RECONCILIATION_CONFIGURATION_STATUSES
    ):
        return None
    if acquisition is not None:
        if (
            temporal is None
            or acquisition["binding_fingerprint"]
            != temporal["target_generation"]
            or acquisition["timing"]["captured_at"] != temporal["captured_at"]
            or continuity.get("battle_identity_fingerprint")
            != temporal.get("round_identity")
        ):
            return None
    elif (
        value.get("temporal") is not None
        or ui_fallback is None
        or ui_fallback.get("source") != RUNNING_UI_FALLBACK_SOURCE
        or continuity.get("battle_identity_fingerprint")
        != ui_fallback.get("active_round_identity_fingerprint")
    ):
        return None
    try:
        resolved = _check_id_list(configuration.get("resolved_check_ids", ()))
        unresolved = _check_id_list(
            configuration.get("unresolved_check_ids", ())
        )
    except (TypeError, ValueError):
        return None
    status = str(configuration["status"])
    if (
        set(resolved).intersection(unresolved)
        or (status == "observation_only" and (resolved or unresolved))
        or (status == "complete" and (not resolved or unresolved))
        or (status == "partial" and not unresolved)
    ):
        return None
    result = {
        "schema_version": 1,
        "kind": kind,
        "workflow_id": workflow_id,
        "observation_id": observation_id,
        "continuity": {
            "status": "battle_identity_bound",
            "disposition": str(continuity["disposition"]),
            "battle_identity_fingerprint": str(
                continuity["battle_identity_fingerprint"]
            ),
        },
        "configuration": {
            "status": status,
            "resolved_check_ids": resolved,
            "unresolved_check_ids": unresolved,
        },
    }
    if acquisition is not None:
        result["acquisition"] = acquisition
        result["temporal"] = temporal
    else:
        result["ui_fallback"] = ui_fallback
    return result


def build_home_return_reconciliation_receipt(
    *,
    workflow_id: str,
    observation_id: str,
    activity_scope_id: str,
    acquisition: PlayerSaveAcquisitionBundle,
    expected_binding: PlayerSaveTargetBinding,
    resolved_check_ids: Iterable[str] = (),
    unresolved_check_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a forced-save Return receipt at verified Home/New Battle."""

    normalized_workflow_id = _bounded(workflow_id, 64)
    normalized_observation_id = _bounded(observation_id, 128)
    normalized_scope = (
        _bounded(activity_scope_id, 128) or _REPORT_SCOPE_UNAVAILABLE
    )
    if (
        normalized_workflow_id is None
        or normalized_observation_id is None
        or not isinstance(acquisition, PlayerSaveAcquisitionBundle)
        or not acquisition.complete
        or acquisition.acquisition_type
        is not PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        or acquisition.binding is None
        or not isinstance(expected_binding, PlayerSaveTargetBinding)
        or acquisition.binding != expected_binding
    ):
        raise ValueError(
            "Home Return reconciliation requires a complete forced save"
        )
    resolved = _check_id_list(resolved_check_ids)
    unresolved = _check_id_list(unresolved_check_ids)
    if set(resolved).intersection(unresolved):
        raise ValueError("resolved and unresolved save checks must be disjoint")
    configuration_status = (
        "partial" if unresolved else "complete" if resolved else "observation_only"
    )
    receipt = {
        "schema_version": 1,
        "kind": HOME_RETURN_RECONCILIATION_KIND,
        "workflow_id": normalized_workflow_id,
        "observation_id": normalized_observation_id,
        "acquisition": acquisition.redacted_provenance(),
        "home_boundary": {
            "status": "verified_new_battle",
            "activity_scope_fingerprint": _fingerprint(
                "control-workflow-scope",
                normalized_scope,
            ),
            "target_binding_fingerprint": acquisition.binding.fingerprint,
        },
        "configuration": {
            "status": configuration_status,
            "resolved_check_ids": resolved,
            "unresolved_check_ids": unresolved,
        },
    }
    normalized = validate_save_reconciliation_receipt(
        receipt,
        expected_kind=HOME_RETURN_RECONCILIATION_KIND,
        expected_workflow_id=normalized_workflow_id,
        expected_observation_id=normalized_observation_id,
    )
    if normalized is None:  # pragma: no cover
        raise ValueError("built Home Return receipt is invalid")
    return normalized


def build_home_ui_reconciliation_receipt(
    *,
    workflow_id: str,
    observation_id: str,
    evidence: Mapping[str, object],
    reason: str,
    resolved_check_ids: Iterable[str] = (),
    unresolved_check_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a Home Return receipt from the verified configuration UI route."""

    normalized_workflow_id = _bounded(workflow_id, 64)
    normalized_observation_id = _bounded(observation_id, 128)
    normalized_evidence = validate_workflow_evidence(evidence)
    if (
        normalized_workflow_id is None
        or normalized_observation_id is None
        or normalized_evidence is None
        or normalized_evidence.get("game_state") != "home_new_battle"
    ):
        raise ValueError(
            "Home UI reconciliation requires exact New Battle evidence"
        )
    resolved = _check_id_list(resolved_check_ids)
    unresolved = _check_id_list(unresolved_check_ids)
    if set(resolved).intersection(unresolved):
        raise ValueError("resolved and unresolved UI checks must be disjoint")
    configuration_status = (
        "partial" if unresolved else "complete" if resolved else "observation_only"
    )
    ui_fallback = _build_ui_fallback_provenance(
        normalized_evidence,
        source=HOME_UI_FALLBACK_SOURCE,
        reason=reason,
        complete=True,
    )
    receipt = {
        "schema_version": SAVE_RECONCILIATION_RECEIPT_SCHEMA_VERSION,
        "kind": HOME_RETURN_RECONCILIATION_KIND,
        "workflow_id": normalized_workflow_id,
        "observation_id": normalized_observation_id,
        "ui_fallback": ui_fallback,
        "home_boundary": {
            "status": "verified_new_battle",
            "activity_scope_fingerprint": ui_fallback[
                "activity_scope_fingerprint"
            ],
            "target_binding_fingerprint": ui_fallback[
                "target_binding_fingerprint"
            ],
        },
        "configuration": {
            "status": configuration_status,
            "resolved_check_ids": resolved,
            "unresolved_check_ids": unresolved,
        },
    }
    normalized = validate_save_reconciliation_receipt(
        receipt,
        expected_kind=HOME_RETURN_RECONCILIATION_KIND,
        expected_workflow_id=normalized_workflow_id,
        expected_observation_id=normalized_observation_id,
    )
    if normalized is None:  # pragma: no cover
        raise ValueError("built Home UI reconciliation receipt is invalid")
    return normalized


def _validated_home_return_receipt(
    value: Mapping[str, Any],
    *,
    kind: str,
    workflow_id: str,
    observation_id: str,
    acquisition: Optional[dict[str, Any]],
    ui_fallback: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    boundary = value.get("home_boundary")
    configuration = value.get("configuration")
    if (
        not isinstance(boundary, Mapping)
        or boundary.get("status") != "verified_new_battle"
        or not _sha256(boundary.get("activity_scope_fingerprint"))
        or not isinstance(configuration, Mapping)
        or str(configuration.get("status") or "")
        not in SAVE_RECONCILIATION_CONFIGURATION_STATUSES
    ):
        return None
    if acquisition is not None:
        if (
            ui_fallback is not None
            or boundary.get("target_binding_fingerprint")
            != acquisition.get("binding_fingerprint")
        ):
            return None
    elif (
        ui_fallback is None
        or ui_fallback.get("source") != HOME_UI_FALLBACK_SOURCE
        or boundary.get("target_binding_fingerprint")
        != ui_fallback.get("target_binding_fingerprint")
        or boundary.get("activity_scope_fingerprint")
        != ui_fallback.get("activity_scope_fingerprint")
    ):
        return None
    try:
        resolved = _check_id_list(configuration.get("resolved_check_ids", ()))
        unresolved = _check_id_list(
            configuration.get("unresolved_check_ids", ())
        )
    except (TypeError, ValueError):
        return None
    status = str(configuration["status"])
    if (
        set(resolved).intersection(unresolved)
        or (status == "observation_only" and (resolved or unresolved))
        or (status == "complete" and (not resolved or unresolved))
        or (status == "partial" and not unresolved)
    ):
        return None
    result = {
        "schema_version": 1,
        "kind": kind,
        "workflow_id": workflow_id,
        "observation_id": observation_id,
        "home_boundary": {
            "status": "verified_new_battle",
            "activity_scope_fingerprint": str(
                boundary["activity_scope_fingerprint"]
            ),
            "target_binding_fingerprint": str(
                boundary["target_binding_fingerprint"]
            ),
        },
        "configuration": {
            "status": status,
            "resolved_check_ids": resolved,
            "unresolved_check_ids": unresolved,
        },
    }
    if acquisition is not None:
        result["acquisition"] = acquisition
    else:
        result["ui_fallback"] = ui_fallback
    return result


def build_terminal_return_reconciliation_receipt(
    *,
    workflow_id: str,
    observation_id: str,
    activity_scope_id: str,
    acquisition: PlayerSaveAcquisitionBundle,
    runtime_session_id: str,
    expected_binding: PlayerSaveTargetBinding,
    killed_by: str,
    collection: str,
) -> dict[str, Any]:
    """Build a Return receipt from one causally bound natural Game Over save."""

    # Retained for schema/API compatibility only.  The natural acquisition's
    # operation, runtime session, target binding, and boundary kind establish
    # authority; a log segment never decides whether the receipt is usable.
    del activity_scope_id
    normalized_workflow_id = _bounded(workflow_id, 64)
    normalized_observation_id = _bounded(observation_id, 128)
    normalized_runtime_session = _bounded(runtime_session_id, 128)
    normalized_killed_by = _bounded(killed_by, 128)
    normalized_collection = str(collection or "").strip().lower()
    if (
        normalized_workflow_id is None
        or normalized_observation_id is None
        or normalized_runtime_session is None
        or normalized_killed_by is None
        or normalized_collection not in MANUAL_SURRENDER_COLLECTIONS
        or not isinstance(acquisition, PlayerSaveAcquisitionBundle)
        or not acquisition.complete
        or acquisition.acquisition_type
        is not PlayerSaveAcquisitionType.NATURAL_BOUNDARY
        or acquisition.boundary is None
        or acquisition.boundary.kind.value != "GAME_OVER"
        or acquisition.boundary.runtime_session_id
        != normalized_runtime_session
        or not isinstance(expected_binding, PlayerSaveTargetBinding)
        or acquisition.binding != expected_binding
    ):
        raise ValueError(
            "terminal Return reconciliation requires a bound natural Game Over save"
        )
    acquisition_provenance = acquisition.redacted_provenance()
    report_scope_fingerprint = acquisition_provenance["boundary"].get(
        "activity_scope"
    )
    if not _sha256(report_scope_fingerprint):
        report_scope_fingerprint = _fingerprint(
            "control-workflow-scope",
            _REPORT_SCOPE_UNAVAILABLE,
        )
    receipt = {
        "schema_version": 1,
        "kind": TERMINAL_RETURN_RECONCILIATION_KIND,
        "workflow_id": normalized_workflow_id,
        "observation_id": normalized_observation_id,
        "acquisition": acquisition_provenance,
        "terminal": {
            "status": "confirmed",
            "activity_scope_fingerprint": report_scope_fingerprint,
            "runtime_session_fingerprint": acquisition_provenance[
                "boundary"
            ]["runtime_session"],
            "killed_by": normalized_killed_by,
            "surrendered": normalized_killed_by.lower() == "surrender",
            "collection": normalized_collection,
        },
    }
    normalized = validate_save_reconciliation_receipt(
        receipt,
        expected_kind=TERMINAL_RETURN_RECONCILIATION_KIND,
        expected_workflow_id=normalized_workflow_id,
        expected_observation_id=normalized_observation_id,
    )
    if normalized is None:  # pragma: no cover
        raise ValueError("built terminal Return receipt is invalid")
    return normalized


def build_terminal_ui_reconciliation_receipt(
    *,
    workflow_id: str,
    observation_id: str,
    evidence: Mapping[str, object],
    killed_by: str,
    reason: str,
) -> dict[str, Any]:
    """Build a Return receipt after the supported terminal UI collector."""

    normalized_workflow_id = _bounded(workflow_id, 64)
    normalized_observation_id = _bounded(observation_id, 128)
    normalized_evidence = validate_workflow_evidence(evidence)
    normalized_killed_by = _bounded(killed_by, 128)
    if (
        normalized_workflow_id is None
        or normalized_observation_id is None
        or normalized_evidence is None
        or normalized_evidence.get("game_state") != "game_over"
        or normalized_killed_by is None
    ):
        raise ValueError(
            "terminal UI reconciliation requires exact Game Over evidence"
        )
    ui_fallback = _build_ui_fallback_provenance(
        normalized_evidence,
        source=TERMINAL_UI_FALLBACK_SOURCE,
        reason=reason,
        complete=True,
    )
    receipt = {
        "schema_version": SAVE_RECONCILIATION_RECEIPT_SCHEMA_VERSION,
        "kind": TERMINAL_RETURN_RECONCILIATION_KIND,
        "workflow_id": normalized_workflow_id,
        "observation_id": normalized_observation_id,
        "ui_fallback": ui_fallback,
        "terminal": {
            "status": "confirmed",
            "activity_scope_fingerprint": ui_fallback[
                "activity_scope_fingerprint"
            ],
            "runtime_session_fingerprint": ui_fallback[
                "runtime_session_fingerprint"
            ],
            "killed_by": normalized_killed_by,
            "surrendered": normalized_killed_by.lower() == "surrender",
            "collection": "full",
        },
    }
    normalized = validate_save_reconciliation_receipt(
        receipt,
        expected_kind=TERMINAL_RETURN_RECONCILIATION_KIND,
        expected_workflow_id=normalized_workflow_id,
        expected_observation_id=normalized_observation_id,
    )
    if normalized is None:  # pragma: no cover
        raise ValueError("built terminal UI Return receipt is invalid")
    return normalized


def _validated_terminal_return_receipt(
    value: Mapping[str, Any],
    *,
    kind: str,
    workflow_id: str,
    observation_id: str,
    acquisition: Optional[dict[str, Any]],
    ui_fallback: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    terminal = value.get("terminal")
    if (
        not isinstance(terminal, Mapping)
        or terminal.get("status") != "confirmed"
        or not _sha256(terminal.get("activity_scope_fingerprint"))
        or not _sha256(terminal.get("runtime_session_fingerprint"))
    ):
        return None
    if acquisition is not None:
        if (
            ui_fallback is not None
            or terminal.get("activity_scope_fingerprint")
            != acquisition["boundary"].get("activity_scope")
            or terminal.get("runtime_session_fingerprint")
            != acquisition["boundary"].get("runtime_session")
        ):
            return None
    elif (
        ui_fallback is None
        or ui_fallback.get("source") != TERMINAL_UI_FALLBACK_SOURCE
        or terminal.get("activity_scope_fingerprint")
        != ui_fallback.get("activity_scope_fingerprint")
        or terminal.get("runtime_session_fingerprint")
        != ui_fallback.get("runtime_session_fingerprint")
    ):
        return None
    killed_by = _bounded(terminal.get("killed_by"), 128)
    collection = str(terminal.get("collection") or "").strip().lower()
    if (
        killed_by is None
        or collection not in MANUAL_SURRENDER_COLLECTIONS
        or type(terminal.get("surrendered")) is not bool
        or terminal.get("surrendered") != (killed_by.lower() == "surrender")
    ):
        return None
    result = {
        "schema_version": 1,
        "kind": kind,
        "workflow_id": workflow_id,
        "observation_id": observation_id,
        "terminal": {
            "status": "confirmed",
            "activity_scope_fingerprint": str(
                terminal["activity_scope_fingerprint"]
            ),
            "runtime_session_fingerprint": str(
                terminal["runtime_session_fingerprint"]
            ),
            "killed_by": killed_by,
            "surrendered": bool(terminal["surrendered"]),
            "collection": collection,
        },
    }
    if acquisition is not None:
        result["acquisition"] = acquisition
    else:
        result["ui_fallback"] = ui_fallback
    return result


def _validated_natural_terminal_acquisition_provenance(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    timing = value.get("timing")
    boundary = value.get("boundary")
    if (
        value.get("type") != PlayerSaveAcquisitionType.NATURAL_BOUNDARY.value
        or value.get("status") != "complete"
        or value.get("transport_stable") is not True
        or not _sha256(value.get("binding_fingerprint"))
        or not isinstance(timing, Mapping)
        or not isinstance(boundary, Mapping)
        or boundary.get("kind") != "GAME_OVER"
        or _aware_timestamp(boundary.get("observed_at")) is None
        or not _sha256(boundary.get("runtime_session"))
        or not _sha256(boundary.get("active_round_identity"))
    ):
        return None
    started = _aware_timestamp(timing.get("started_at"))
    captured = _aware_timestamp(timing.get("captured_at"))
    completed = _aware_timestamp(timing.get("completed_at"))
    if started is None or captured is None or completed is None:
        return None
    if not (
        datetime.fromisoformat(started)
        <= datetime.fromisoformat(captured)
        <= datetime.fromisoformat(completed)
    ):
        return None
    return {
        "schema_version": 1,
        "type": PlayerSaveAcquisitionType.NATURAL_BOUNDARY.value,
        "status": "complete",
        "reason": _bounded(value.get("reason"), 256) or "complete",
        "binding_fingerprint": str(value["binding_fingerprint"]),
        "transport_stable": True,
        "timing": {
            "started_at": started,
            "captured_at": captured,
            "completed_at": completed,
        },
        "boundary": {
            "kind": "GAME_OVER",
            "observed_at": _aware_timestamp(boundary["observed_at"]),
            "runtime_session": str(boundary["runtime_session"]),
            "activity_scope": (
                str(boundary.get("activity_scope"))
                if _sha256(boundary.get("activity_scope"))
                else _fingerprint(
                    "control-workflow-scope",
                    _REPORT_SCOPE_UNAVAILABLE,
                )
            ),
            "active_round_identity": str(
                boundary["active_round_identity"]
            ),
        },
    }


def _validated_forced_acquisition_provenance(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    timing = value.get("timing")
    if (
        value.get("type") != PlayerSaveAcquisitionType.FORCED_SERIALIZATION.value
        or value.get("status") != "complete"
        or value.get("transport_stable") is not True
        or value.get("boundary") is not None
        or not _sha256(value.get("binding_fingerprint"))
        or not isinstance(timing, Mapping)
    ):
        return None
    started = _aware_timestamp(timing.get("started_at"))
    captured = _aware_timestamp(timing.get("captured_at"))
    completed = _aware_timestamp(timing.get("completed_at"))
    if started is None or captured is None or completed is None:
        return None
    if not (
        datetime.fromisoformat(started)
        <= datetime.fromisoformat(captured)
        <= datetime.fromisoformat(completed)
    ):
        return None
    return {
        "schema_version": 1,
        "type": PlayerSaveAcquisitionType.FORCED_SERIALIZATION.value,
        "status": "complete",
        "reason": _bounded(value.get("reason"), 256) or "complete",
        "binding_fingerprint": str(value["binding_fingerprint"]),
        "transport_stable": True,
        "timing": {
            "started_at": started,
            "captured_at": captured,
            "completed_at": completed,
        },
        "boundary": None,
    }


def _validated_running_temporal_provenance(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    mapping_id = _bounded(value.get("mapping_id"), 128)
    effective_mapping_fingerprint = value.get(
        "effective_mapping_fingerprint"
    )
    captured_at = _aware_timestamp(value.get("captured_at"))
    if (
        mapping_id is None
        or not _sha256(effective_mapping_fingerprint)
        or captured_at is None
        or value.get("acquisition_type")
        != PlayerSaveAcquisitionType.FORCED_SERIALIZATION.value
        or not _sha256(value.get("target_generation"))
        or not _sha256(value.get("runtime_session"))
        or not _sha256(value.get("source_activity_scope"))
        or not _sha256(value.get("activity_scope"))
        or not _sha256(value.get("round_identity"))
        or not _sha256(value.get("claim_fingerprint"))
    ):
        return None
    return {
        "schema_version": 1,
        "mapping_id": mapping_id,
        "effective_mapping_fingerprint": str(
            effective_mapping_fingerprint
        ),
        "runtime_session": str(value["runtime_session"]),
        "source_activity_scope": str(value["source_activity_scope"]),
        "target_generation": str(value["target_generation"]),
        "activity_scope": str(value["activity_scope"]),
        "round_identity": str(value["round_identity"]),
        "captured_at": captured_at,
        "acquisition_type": PlayerSaveAcquisitionType.FORCED_SERIALIZATION.value,
        "claim_fingerprint": str(value["claim_fingerprint"]),
    }


def _build_ui_fallback_provenance(
    evidence: Mapping[str, Any],
    *,
    source: str,
    reason: str,
    complete: bool,
) -> dict[str, Any]:
    normalized_source = str(source or "").strip().lower()
    normalized_reason = _bounded(reason, 256) or "save_evidence_unavailable"
    if normalized_source not in UI_RECONCILIATION_SOURCES:
        raise ValueError("unsupported UI reconciliation source")
    try:
        target_binding = PlayerSaveTargetBinding(
            str(evidence.get("adb_target") or ""),
            int(evidence.get("target_generation")),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("UI reconciliation target binding is unavailable") from exc
    scope_id = (
        _bounded(evidence.get("activity_scope_run_id"), 128)
        or _REPORT_SCOPE_UNAVAILABLE
    )
    runtime_id = _bounded(evidence.get("runtime_id"), 128)
    if runtime_id is None:
        raise ValueError("UI reconciliation runtime is unavailable")
    active_round_identity = evidence.get(
        "active_round_identity_fingerprint"
    )
    if active_round_identity is not None and not _sha256(
        active_round_identity
    ):
        raise ValueError("UI reconciliation battle identity is invalid")
    result = {
        "status": "complete" if complete else "degraded",
        "source": normalized_source,
        "reason": normalized_reason,
        "runtime_session_fingerprint": _fingerprint(
            "control-workflow-runtime",
            runtime_id,
        ),
        "activity_scope_fingerprint": _fingerprint(
            "control-workflow-scope",
            scope_id,
        ),
        "target_binding_fingerprint": target_binding.fingerprint,
    }
    if active_round_identity is not None:
        result["active_round_identity_fingerprint"] = str(
            active_round_identity
        )
    return result


def _validated_ui_fallback_provenance(
    value: object,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    status = str(value.get("status") or "").strip().lower()
    source = str(value.get("source") or "").strip().lower()
    reason = _bounded(value.get("reason"), 256)
    if (
        status not in {"complete", "degraded"}
        or source not in UI_RECONCILIATION_SOURCES
        or reason is None
        or not _sha256(value.get("runtime_session_fingerprint"))
        or not _sha256(value.get("activity_scope_fingerprint"))
        or not _sha256(value.get("target_binding_fingerprint"))
        or (
            value.get("active_round_identity_fingerprint") is not None
            and not _sha256(
                value.get("active_round_identity_fingerprint")
            )
        )
    ):
        return None
    result = {
        "status": status,
        "source": source,
        "reason": reason,
        "runtime_session_fingerprint": str(
            value["runtime_session_fingerprint"]
        ),
        "activity_scope_fingerprint": str(
            value["activity_scope_fingerprint"]
        ),
        "target_binding_fingerprint": str(
            value["target_binding_fingerprint"]
        ),
    }
    if value.get("active_round_identity_fingerprint") is not None:
        result["active_round_identity_fingerprint"] = str(
            value["active_round_identity_fingerprint"]
        )
    return result


def ui_reconciliation_receipt_matches_evidence(
    receipt: object,
    evidence: object,
) -> bool:
    """Return whether a UI receipt still matches exact live workflow evidence."""

    normalized_receipt = validate_save_reconciliation_receipt(receipt)
    normalized_evidence = validate_workflow_evidence(evidence)
    if normalized_receipt is None or normalized_evidence is None:
        return False
    ui_fallback = normalized_receipt.get("ui_fallback")
    if not isinstance(ui_fallback, Mapping):
        return False
    expected_states = {
        RUNNING_UI_FALLBACK_SOURCE: "active_battle",
        HOME_UI_FALLBACK_SOURCE: "home_new_battle",
        TERMINAL_UI_FALLBACK_SOURCE: "game_over",
    }
    source = str(ui_fallback.get("source") or "")
    if normalized_evidence.get("game_state") != expected_states.get(source):
        return False
    try:
        expected = _build_ui_fallback_provenance(
            normalized_evidence,
            source=source,
            reason=str(ui_fallback.get("reason") or ""),
            complete=ui_fallback.get("status") == "complete",
        )
    except ValueError:
        return False
    # ``observation_id`` is receipt provenance, not a lease: the runtime emits
    # a new observation ID on every heartbeat while a UI workflow can span
    # several heartbeats. Process-local callers separately retain the original
    # claim and PID; runtime session and target generation prove the refreshed
    # observation still belongs to the operation. Activity scope is retained
    # only as receipt provenance and cannot invalidate the operation.
    return all(
        ui_fallback.get(field) == expected.get(field)
        for field in (
            "runtime_session_fingerprint",
            "target_binding_fingerprint",
        )
    )


def _check_id_list(values: Iterable[object]) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise TypeError("save check ids must be an iterable of strings")
    normalized: list[str] = []
    for value in values:
        item = _bounded(value, 128)
        if item is None:
            raise ValueError("save check id is invalid")
        if item not in normalized:
            normalized.append(item)
        if len(normalized) > 64:
            raise ValueError("too many save check ids")
    return sorted(normalized)


def _sha256(value: object) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "")))


def _fingerprint(label: str, value: str) -> str:
    return hashlib.sha256(
        f"thetower-{label}-v1\0{value}".encode("utf-8")
    ).hexdigest()


def _mapping_fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


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
    "HOME_UI_FALLBACK_SOURCE",
    "HOME_RETURN_RECONCILIATION_KIND",
    "MANUAL_CONTROL_SCHEMA_VERSION",
    "MANUAL_SURRENDER_COLLECTIONS",
    "MANUAL_CONTROL_STATUSES",
    "MANUAL_CONTROL_TERMINAL_STATUSES",
    "PROCESS_RESTART_HANDOFF_SCHEMA_VERSION",
    "PROCESS_RESTART_HANDOFF_STATUSES",
    "RUNNING_SAVE_RECONCILIATION_KINDS",
    "RUNNING_UI_FALLBACK_SOURCE",
    "SAVE_RECONCILIATION_RECEIPT_SCHEMA_VERSION",
    "SETUP_CAPTURE_AUTHORITY_OUTCOMES",
    "SETUP_CAPTURE_GAME_STATES",
    "SETUP_CAPTURE_SCHEMA_VERSION",
    "SETUP_CAPTURE_STATUSES",
    "SETUP_CAPTURE_TERMINAL_STATUSES",
    "TERMINAL_RETURN_RECONCILIATION_KIND",
    "TERMINAL_UI_FALLBACK_SOURCE",
    "UI_RECONCILIATION_SOURCES",
    "build_home_ui_reconciliation_receipt",
    "build_home_return_reconciliation_receipt",
    "build_running_ui_reconciliation_receipt",
    "build_running_save_reconciliation_receipt",
    "build_terminal_ui_reconciliation_receipt",
    "build_terminal_return_reconciliation_receipt",
    "intent_matches_evidence",
    "observed_game_state",
    "validate_battle_workflow",
    "validate_manual_control",
    "validate_manual_terminal_evidence",
    "validate_observation",
    "validate_process_restart_handoff",
    "validate_save_reconciliation_receipt",
    "validate_setup_capture",
    "validate_setup_capture_preview",
    "validate_workflow_evidence",
    "ui_reconciliation_receipt_matches_evidence",
    "workflow_evidence_from_authority",
]
