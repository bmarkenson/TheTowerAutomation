"""Read-only runtime views and guarded control mutations for a remote GUI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from typing import Any, Mapping, Optional, Sequence

from core.adb_connection import PersistentAdbConnectionManager
from core.app_setup import (
    DEFAULT_STARTUP_GATE_POLICY,
    STARTUP_GATE_POLICIES,
)
from core.automation_process import AutomationProcessError, SystemdAutomationManager
from core.battle_classification import (
    classification_for_record,
    observed_tier_for_record,
)
from core.battle_stats import included_in_default_history
from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS,
    MAXIMUM_GAME_SPEED_TARGET,
    normalize_automation_mode,
)
from core.control_model import (
    BATTLE_WORKFLOW_TERMINAL_STATUSES,
    MANUAL_CONTROL_TERMINAL_STATUSES,
    SETUP_CAPTURE_GAME_STATES,
    SETUP_CAPTURE_TERMINAL_STATUSES,
    validate_battle_workflow,
    validate_manual_control,
    validate_observation,
    validate_setup_capture,
    workflow_evidence_from_authority,
)
from core.gate_decisions import startup_gate_context_for_strategy
from core.exclusive_validation import (
    exclusive_validation_definition_for_strategy,
)
from core.emulator_degradation import (
    AUTOMATIC_RESTART_COOLDOWN,
    assess_emulator_degradation,
    load_comparable_battles,
)
from core.emulator_recovery import (
    normalize_emulator_maintenance,
    normalize_runtime_recovery_ack,
)
from core.host_performance import (
    DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS,
    HostPerformancePayloadError,
    HostPerformanceStorageError,
    HostPerformanceStore,
)
from core.module_presets import ModulePresetConflictError, ModulePresetError
from core.player_save_setup_capture import (
    SetupCaptureError,
    module_preset_source_from_capture,
)
from core.player_save import confirmed_local_mapping_status
from core.player_save_confirmed_local_mapping import ConfirmedLocalMappingStore
from core.player_save_mapping_candidates import AppendOnlyMappingCandidateStore
from core.player_save_mapping_develop_integration import (
    SAVE_MAPPING_INTEGRATION_CAPABILITY,
    SAVE_MAPPING_REVIEW_STATUS_CAPABILITY,
    SaveMappingIntegrationError,
    SaveMappingIntegrationManager,
)
from core.strategy_profiles import (
    STRATEGY_AUTHORING_OPERATIONS,
    StrategyProfileConflictError,
    StrategyProfileError,
    StrategyProfileStore,
    normalize_strategy_id,
)
from utils.logger import DEFAULT_ACTIVITY_SCOPE_FILENAME


MAX_PAUSE_MINUTES = 7 * 24 * 60
DEFAULT_STALE_AFTER_SECONDS = 180
EMULATOR_DEGRADATION_CACHE_SECONDS = 60.0
# Advance this when a newer Windows client must reload the resident service,
# and advance that client's MinimumServerRevision in the same change.
CONTROL_SURFACE_REVISION = 40
CONTROL_SURFACE_CAPABILITIES = (
    "active_battle_strategy_adoption",
    "advisory_preflight_decisions",
    "better_control_model_v1",
    "better_control_model_v2",
    "completed_battle_discard",
    "confirmed_local_mapping_status_v2",
    "current_battle_perks_v1",
    "current_run_activity_scope",
    "exclusive_strategy_validation_status",
    "explicit_strategy_disposition",
    "game_speed_target",
    "host_performance_gpu_v1",
    "host_performance_process_attribution_v1",
    "host_performance_telemetry_v1",
    "bluestacks_maintenance_v1",
    "interactive_development_lease_v1",
    "managed_custom_module_presets_v1",
    "observed_game_speed",
    "persistent_adb_connection_v1",
    "runtime_control_acknowledgements_v1",
    "save_backed_setup_capture_v1",
    "save_backed_setup_capture_v2",
    SAVE_MAPPING_REVIEW_STATUS_CAPABILITY,
    SAVE_MAPPING_INTEGRATION_CAPABILITY,
    "selected_strategy_process_start",
    "strategy_action_gate_v1",
    "strategy_aware_attach_v1",
    "strategy_authoring_profile_lifecycle_v1",
    "strategy_authoring_local_loadout_editors_v1",
    "strategy_authoring_preset_local_copy_v1",
    "strategy_authoring_specialized_editors_v1",
    "strategy_authoring_v1",
    "strategy_profile_catalog_v1",
    "strategy_profile_editor_v2",
    "strategy_revision_history_v1",
    "terminal_dispositions_v2",
    "tournament_launch_confirmation",
)
CURRENT_BATTLE_PERKS_SCHEMA_VERSION = 1
MAX_PERK_TIMELINE_STATUS_BYTES = 2 * 1024 * 1024
MAX_CURRENT_BATTLE_PERK_ITEMS = 64
ATTACHED_RESTART_TIMEOUT_SECONDS = 20.0
ATTACHED_RESTART_POLL_SECONDS = 0.25
DEFAULT_DISCARDED_BATTLE_RETENTION_DAYS = 30
_BATTLE_ID_RE = re.compile(r"(?:Battle|Tournament)\d{8}T\d{6}[+-]\d{4}")
_LOG_RE = re.compile(
    r"^\[(?P<level>[A-Z_]+) (?P<timestamp>[^\]]+)] (?P<message>.*)$"
)
_LOG_LEVEL_RE = re.compile(r"[A-Z_]+")
_OPERATION_DETAIL_RE = re.compile(
    r"(?:^|\s)\[OPERATION] id=(?P<operation_id>[A-Za-z0-9._:-]{1,128})$"
)
_ACTIVITY_DATA_RE = re.compile(
    r"(?:^|\s)\[ACTIVITY_DATA] (?P<payload>\{.*\})"
    r"(?:\s+\[OPERATION] id=[A-Za-z0-9._:-]{1,128})?$"
)
_PERK_SELECTION_COUNT_RE = re.compile(
    r"^\[PERK_TIMELINE] result=recorded\b.*"
    r"\bselection_count=(?P<count>\d+)\b"
)
_OPERATIONAL_ACTIVITY_LEVELS = frozenset(
    {"ACTION", "RESULT", "WARN", "ERROR", "FAIL"}
)
_ACTIVITY_CURSOR_RE = re.compile(r"(?P<source>\d+:\d+)@(?P<offset>\d+)")
_STATUS_RE = re.compile(
    r"^State=(?P<state>[^|]+?)\s*\|\s*"
    r"Wave=(?P<wave>[^|]+?)\s*\|\s*"
    r"Coins/min=(?P<coins>[^|]+?)"
    r"(?:\s*\|\s*Speed=(?P<speed>[^|]+?))?\s*\|\s*"
    r"Menu=(?P<menu>[^|]+?)\s*\|\s*"
    r"Secondary=\[(?P<secondary>.*?)]\s*\|\s*"
    r"Overlays=\[(?P<overlays>.*?)]\s*$"
)
_STATUS_SUMMARY_RE = re.compile(
    r"^State=(?P<state>[^|]+?)\s*\|\s*"
    r"Wave=(?P<wave>[^|]+?)\s*\|\s*"
    r"Coins/min=(?P<coins>[^|]+?)"
    r"(?:\s*\|\s*Speed=(?P<speed>[^|]+?))?\s*$"
)
_STATUS_DETAIL_PREFIX = "[STATUS_DETAIL] "
_CONTROL_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class ControlSurfaceRequestError(ValueError):
    """A rejected GUI request with an HTTP-friendly status code."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = dict(details or {})


class ControlSurfaceService:
    """Expose a narrow, transport-independent control-surface model."""

    def __init__(
        self,
        *,
        repository_root: Path | str,
        control_file: Path | str = "logs/automation_ctl.json",
        action_log: Path | str = "logs/actions.log",
        battles_dir: Path | str = "logs/battles",
        tournaments_dir: Path | str = "logs/tournaments",
        discarded_battles_dir: Path | str = "logs/discarded_battles",
        discarded_battle_retention_days: int = (
            DEFAULT_DISCARDED_BATTLE_RETENTION_DAYS
        ),
        host_performance_db: Path | str = "logs/host_performance.sqlite3",
        host_performance_retention_days: int = (
            DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS
        ),
        activity_scope_file: Path | str | None = None,
        strategy_action_gate_file: Path | str = (
            "logs/strategy_action_gate.json"
        ),
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        process_manager: Optional[SystemdAutomationManager] = None,
        adb_connection_manager: Optional[PersistentAdbConnectionManager] = None,
        strategy_profile_dir: Path | str = "config/strategies/custom",
        module_preset_dir: Path | str = "config/loadouts/custom/modules",
        confirmed_local_mapping_dir: Path | str = (
            "config/player_save_versions/local"
        ),
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.control_path = self._resolve_path(control_file)
        self.perk_timeline_path = self.control_path.with_name(
            f"{self.control_path.stem}.perk_timeline_state.json"
        )
        self.action_log = self._resolve_path(action_log)
        self.activity_scope_path = (
            self.action_log.with_name(DEFAULT_ACTIVITY_SCOPE_FILENAME)
            if activity_scope_file is None
            else self._resolve_path(activity_scope_file)
        )
        self.strategy_action_gate_path = self._resolve_path(
            strategy_action_gate_file
        )
        self.battles_dir = self._resolve_path(battles_dir)
        self.tournaments_dir = self._resolve_path(tournaments_dir)
        self.discarded_battles_dir = self._resolve_path(discarded_battles_dir)
        self.discarded_battle_retention_days = max(
            1,
            int(discarded_battle_retention_days),
        )
        self.host_performance_store = HostPerformanceStore(
            self._resolve_path(host_performance_db),
            retention_days=host_performance_retention_days,
        )
        self.stale_after_seconds = max(1, int(stale_after_seconds))
        self.strategy_profile_dir = self._resolve_path(strategy_profile_dir)
        self.module_preset_dir = self._resolve_path(module_preset_dir)
        self.confirmed_local_mapping_store = ConfirmedLocalMappingStore(
            self._resolve_path(confirmed_local_mapping_dir)
        )
        self.mapping_candidate_store = AppendOnlyMappingCandidateStore(
            self._resolve_path(
                "logs/player_save_mapping_candidates/receipts-v2.jsonl"
            )
        )
        self.save_mapping_integration_manager = SaveMappingIntegrationManager(
            repository_root=self.repository_root,
            candidate_store=self.mapping_candidate_store,
        )
        self.profile_store = StrategyProfileStore(
            profile_directory=self.strategy_profile_dir,
            module_preset_directory=self.module_preset_dir,
            audit_callback=self._append_audit,
        )
        self.control_store = ControlDirectiveStore(
            self.control_path,
            strategy_profile_dir=self.strategy_profile_dir,
        )
        self.process_manager = process_manager
        self.adb_connection_manager = adb_connection_manager
        self._process_action_lock = threading.Lock()
        self._control_mutation_lock = threading.Lock()
        self._battle_mutation_lock = threading.RLock()
        self._emulator_degradation_cache_lock = threading.Lock()
        self._emulator_degradation_cache: Optional[
            tuple[float, tuple[str, str], dict[str, Any]]
        ] = None
        self._emulator_battle_history_cache: Optional[
            tuple[tuple[int, int], list[dict[str, Any]]]
        ] = None

    def strategy_profiles(self) -> dict[str, Any]:
        """Return the constrained profile-editor catalog."""

        return self.profile_store.catalog()

    def save_mapping_integration(self) -> dict[str, Any]:
        """Return review candidates and fixed develop eligibility."""

        return self.save_mapping_integration_manager.catalog()

    def apply_save_mapping_integration(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Review or commit one exact mapping proposal directly to develop."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        operation = str(request.get("operation") or "").strip().lower()
        required = {
            "review": {"operation", "candidate_record_id"},
            "integrate": {
                "operation",
                "candidate_record_id",
                "reviewed_proposal_fingerprint",
            },
        }
        if operation not in required:
            raise ControlSurfaceRequestError("operation must be review or integrate")
        if set(request) != required[operation]:
            raise ControlSurfaceRequestError(
                f"{operation} accepts exactly: "
                + ", ".join(sorted(required[operation]))
            )
        candidate_record_id = request.get("candidate_record_id")
        if not isinstance(candidate_record_id, str) or re.fullmatch(
            r"[0-9a-f]{64}", candidate_record_id
        ) is None:
            raise ControlSurfaceRequestError(
                "candidate_record_id must be exactly 64 lowercase hexadecimal characters"
            )
        try:
            if operation == "review":
                return self.save_mapping_integration_manager.review(
                    candidate_record_id=candidate_record_id,
                )

            reviewed_fingerprint = request.get(
                "reviewed_proposal_fingerprint"
            )
            if not isinstance(reviewed_fingerprint, str) or re.fullmatch(
                r"[0-9a-f]{64}", reviewed_fingerprint
            ) is None:
                raise ControlSurfaceRequestError(
                    "reviewed_proposal_fingerprint must be exactly 64 lowercase "
                    "hexadecimal characters"
                )
            record_prefix = str(candidate_record_id or "")[:12]
            fingerprint_prefix = str(reviewed_fingerprint or "")[:12]
            operation_id = (
                f"save-mapping-{record_prefix}-{fingerprint_prefix}-"
                f"{secrets.token_hex(6)}"
            )
            action_warning = self._append_audit(
                "Integrating reviewed canonical save mapping into develop "
                f"candidate={record_prefix} "
                f"review={fingerprint_prefix} "
                f"[OPERATION] id={operation_id}",
                level="ACTION",
            )
            if action_warning:
                raise ControlSurfaceRequestError(
                    "Canonical integration audit could not be written; nothing was changed.",
                    status=503,
                    code="mapping_integration_audit_unavailable",
                )
            try:
                result = self.save_mapping_integration_manager.integrate(
                    candidate_record_id=candidate_record_id,
                    reviewed_proposal_fingerprint=reviewed_fingerprint,
                )
            except SaveMappingIntegrationError as exc:
                disposition = _save_mapping_integration_disposition(exc.code)
                self._append_audit(
                    "Canonical save mapping develop integration "
                    f"disposition={disposition} code={exc.code} "
                    f"[OPERATION] id={operation_id}",
                    level="RESULT",
                )
                raise
            except Exception as exc:
                self._append_audit(
                    "Canonical save mapping develop integration "
                    "disposition=unconfirmed code=unexpected_failure "
                    f"[OPERATION] id={operation_id}",
                    level="RESULT",
                )
                raise ControlSurfaceRequestError(
                    "Canonical develop integration failed unexpectedly; inspect "
                    "main, develop, and the durable transaction before continuing.",
                    status=500,
                    code="mapping_integration_unexpected_failure",
                ) from exc
            result_warning = self._append_audit(
                "Canonical save mapping develop integration "
                "disposition=committed_to_develop "
                f"candidate={record_prefix} commit={result.get('integration_commit')} "
                f"committed=true promoted={str(bool(result.get('promoted'))).lower()} "
                "mapping_invariants=passed "
                f"[OPERATION] id={operation_id}",
                level="RESULT",
            )
            if result_warning:
                result["warning"] = result_warning
            return result
        except SaveMappingIntegrationError as exc:
            raise ControlSurfaceRequestError(
                str(exc),
                status=_save_mapping_integration_error_status(exc.code),
                code=exc.code,
            ) from exc

    def strategy_authoring(self) -> dict[str, Any]:
        """Return the additive sparse Base/Strategy authoring catalog."""

        try:
            return self.profile_store.authoring_catalog()
        except StrategyProfileError as exc:
            raise ControlSurfaceRequestError(str(exc)) from exc

    def setup_capture(self) -> dict[str, Any]:
        """Return the current safe capture ledger and save-as-new catalogs."""

        current = self.status()
        model = current.get("control_model") or {}
        return {
            "schema_version": 1,
            "server_revision": CONTROL_SURFACE_REVISION,
            "capability": "save_backed_setup_capture_v2",
            "capture": model.get("setup_capture"),
            "availability": (model.get("actions") or {}).get(
                "capture_current_setup"
            ),
            "captured_drafts": self.profile_store.captured_strategy_draft_catalog(),
            "module_presets": self.profile_store.module_preset_store.catalog(),
            "bases": self.profile_store.base_store.catalog(),
        }

    def captured_setup_draft(self, strategy_id: object) -> dict[str, Any]:
        """Return one durable captured source for the ordinary editor."""

        try:
            draft = self.profile_store.captured_strategy_draft(strategy_id)
        except StrategyProfileConflictError as exc:
            raise ControlSurfaceRequestError(str(exc), status=409) from exc
        except StrategyProfileError as exc:
            raise ControlSurfaceRequestError(str(exc), status=404) from exc
        return {
            "schema_version": 1,
            "server_revision": CONTROL_SURFACE_REVISION,
            "capability": "save_backed_setup_capture_v2",
            "draft": draft,
        }

    def apply_setup_capture(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Request, save, or cancel one runtime-owned setup capture."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        with self._process_action_lock:
            operation = str(request.get("operation") or "").strip().lower()
            if operation == "request":
                if set(request) != {"operation"}:
                    raise ControlSurfaceRequestError(
                        "Setup capture request accepts only operation=request"
                    )
                current = self.status()
                availability = (
                    current.get("control_model", {})
                    .get("actions", {})
                    .get("capture_current_setup", {})
                )
                if availability.get("available") is not True:
                    raise ControlSurfaceRequestError(
                        str(
                            availability.get("reason")
                            or "Capture current setup is unavailable"
                        ),
                        status=409,
                        code=str(availability.get("code") or "unavailable"),
                    )
                evidence = current.get("control_model", {}).get(
                    "workflow_evidence"
                )
                manual_control = current.get("control_model", {}).get(
                    "manual_control"
                )
                source_manual_control_id = (
                    str(manual_control.get("manual_control_id") or "").strip()
                    if availability.get("code")
                    == "available_from_return_control"
                    and isinstance(manual_control, Mapping)
                    else None
                )
                try:
                    capture = self.control_store.request_setup_capture(
                        evidence=evidence,
                        source_manual_control_id=source_manual_control_id,
                        source="control-surface",
                    )
                except (ControlDirectiveError, ValueError) as exc:
                    raise ControlSurfaceRequestError(
                        str(exc), status=409
                    ) from exc
                audit = (
                    "Requested Capture current setup from the exact retained "
                    "Return Control forced save; no new refresh, cached evidence, "
                    "Strategy, or preset was selected"
                    if source_manual_control_id
                    else "Requested save-backed Capture current setup; no cached "
                    "evidence, Strategy, or preset was selected"
                )
                disposition = "requested"
            elif operation == "cancel":
                if set(request) != {"operation", "request_id"}:
                    raise ControlSurfaceRequestError(
                        "Setup capture cancellation requires operation and request_id"
                    )
                capture = validate_setup_capture(
                    self.control_store.status().get("setup_capture")
                )
                request_id = str(request.get("request_id") or "").strip()
                if capture is None or capture.get("request_id") != request_id:
                    raise ControlSurfaceRequestError(
                        "Setup capture is no longer current", status=409
                    )
                if capture.get("status") == "capturing":
                    raise ControlSurfaceRequestError(
                        "Setup capture cannot be cancelled while source restoration is in progress",
                        status=409,
                        code="capture_in_progress",
                    )
                if capture.get("status") in SETUP_CAPTURE_TERMINAL_STATUSES:
                    disposition = "no_op"
                else:
                    capture = self.control_store.transition_setup_capture(
                        request_id,
                        "cancelled",
                        reason="operator cancelled the capture review",
                        source="control-surface",
                    )
                    if capture is None:
                        raise ControlSurfaceRequestError(
                            "Setup capture changed before cancellation",
                            status=409,
                        )
                    disposition = "completed"
                audit = "Cancelled setup capture review"
            elif operation == "review":
                capture = validate_setup_capture(
                    self.control_store.status().get("setup_capture")
                )
                if capture is None or capture.get("status") != "ready":
                    raise ControlSurfaceRequestError(
                        "A runtime-issued ready capture is required before review",
                        status=409,
                        code="capture_not_ready",
                    )
                allowed = {
                    "operation",
                    "request_id",
                    "expected_preview_fingerprint",
                    "kind",
                    "id",
                    "display_name",
                    "tier",
                    "base",
                }
                if set(request) - allowed or not {
                    "operation",
                    "request_id",
                    "expected_preview_fingerprint",
                    "kind",
                    "id",
                    "display_name",
                    "tier",
                } <= set(request):
                    raise ControlSurfaceRequestError(
                        "Strategy capture review requires operation, request_id, "
                        "expected_preview_fingerprint, kind, id, display_name, "
                        "and tier; base is optional"
                    )
                if str(request.get("kind") or "").strip().lower() != (
                    "strategy_draft"
                ):
                    raise ControlSurfaceRequestError(
                        "Only Strategy drafts have a captured-versus-Base review"
                    )
                request_id = str(request.get("request_id") or "").strip()
                if request_id != capture.get("request_id"):
                    raise ControlSurfaceRequestError(
                        "Setup capture is no longer current", status=409
                    )
                expected_fingerprint = str(
                    request.get("expected_preview_fingerprint") or ""
                ).strip()
                if (
                    not expected_fingerprint
                    or expected_fingerprint
                    != capture.get("preview_fingerprint")
                ):
                    raise ControlSurfaceRequestError(
                        "Capture preview changed after review; refresh before continuing",
                        status=409,
                        code="capture_preview_changed",
                    )
                try:
                    review_result = (
                        self.profile_store.review_captured_strategy_draft(
                            capture.get("preview"),
                            strategy_id=request.get("id"),
                            display_name=request.get("display_name"),
                            tier=request.get("tier"),
                            base=request.get("base"),
                            expected_capture_fingerprint=expected_fingerprint,
                        )
                    )
                except StrategyProfileConflictError as exc:
                    raise ControlSurfaceRequestError(
                        str(exc), status=409
                    ) from exc
                except StrategyProfileError as exc:
                    raise ControlSurfaceRequestError(str(exc)) from exc
                disposition = "reviewed"
                audit = (
                    "Reviewed captured Strategy draft differences without "
                    "saving, publishing, selecting, queueing, or applying it"
                )
            elif operation == "save":
                capture = validate_setup_capture(
                    self.control_store.status().get("setup_capture")
                )
                if capture is None or capture.get("status") != "ready":
                    raise ControlSurfaceRequestError(
                        "A runtime-issued ready capture is required before saving",
                        status=409,
                        code="capture_not_ready",
                    )
                request_id = str(request.get("request_id") or "").strip()
                if request_id != capture.get("request_id"):
                    raise ControlSurfaceRequestError(
                        "Setup capture is no longer current", status=409
                    )
                expected_fingerprint = str(
                    request.get("expected_preview_fingerprint") or ""
                ).strip()
                if (
                    not expected_fingerprint
                    or expected_fingerprint
                    != capture.get("preview_fingerprint")
                ):
                    raise ControlSurfaceRequestError(
                        "Capture preview changed after review; refresh before saving",
                        status=409,
                        code="capture_preview_changed",
                    )
                preview = capture.get("preview")
                if not isinstance(preview, Mapping):
                    raise ControlSurfaceRequestError(
                        "Runtime capture preview is invalid",
                        status=409,
                        code="capture_preview_invalid",
                    )
                save_kind = str(request.get("kind") or "").strip().lower()
                identifier = request.get("id")
                display_name = request.get("display_name")
                try:
                    if save_kind == "module_preset":
                        if set(request) != {
                            "operation",
                            "request_id",
                            "expected_preview_fingerprint",
                            "kind",
                            "id",
                            "display_name",
                        }:
                            raise ControlSurfaceRequestError(
                                "Module capture save requires operation, request_id, "
                                "expected_preview_fingerprint, kind, id, and display_name"
                            )
                        source = module_preset_source_from_capture(preview)
                        artifact = self.profile_store.create_module_preset(
                            identifier,
                            display_name,
                            source,
                        )
                        saved_result = {
                            "kind": save_kind,
                            "id": artifact["id"],
                            "display_name": artifact["display_name"],
                            "artifact_disposition": "created",
                            "selected": False,
                            "activated": False,
                            "queued": False,
                            "applied": False,
                        }
                    elif save_kind == "strategy_draft":
                        allowed = {
                            "operation",
                            "request_id",
                            "expected_preview_fingerprint",
                            "expected_review_fingerprint",
                            "kind",
                            "id",
                            "display_name",
                            "tier",
                            "base",
                        }
                        if set(request) - allowed or not {
                            "operation",
                            "request_id",
                            "expected_preview_fingerprint",
                            "expected_review_fingerprint",
                            "kind",
                            "id",
                            "display_name",
                            "tier",
                        } <= set(request):
                            raise ControlSurfaceRequestError(
                                "Strategy draft capture save requires operation, "
                                "request_id, expected_preview_fingerprint, kind, "
                                "id, display_name, tier, and the exact reviewed "
                                "difference fingerprint; base is optional"
                            )
                        reviewed_fingerprint = str(
                            request.get("expected_review_fingerprint") or ""
                        ).strip()
                        if not reviewed_fingerprint:
                            raise ControlSurfaceRequestError(
                                "Review captured-versus-Base differences before saving",
                                status=409,
                                code="capture_review_required",
                            )
                        artifact_disposition = "created"
                        try:
                            artifact = self.profile_store.save_captured_strategy_draft(
                                preview,
                                strategy_id=identifier,
                                display_name=display_name,
                                tier=request.get("tier"),
                                base=request.get("base"),
                                expected_capture_fingerprint=expected_fingerprint,
                                expected_review_fingerprint=reviewed_fingerprint,
                            )
                        except StrategyProfileConflictError as conflict:
                            # Only a draft that embeds this exact capture and
                            # reviewed difference can recover an artifact whose
                            # control-ledger receipt failed after atomic create.
                            # Module presets do not embed that provenance and
                            # therefore deliberately have no analogous shortcut.
                            try:
                                existing = (
                                    self.profile_store.captured_strategy_draft(
                                        identifier
                                    )
                                )
                                expected_review = (
                                    self.profile_store.review_captured_strategy_draft(
                                        preview,
                                        strategy_id=identifier,
                                        display_name=display_name,
                                        tier=request.get("tier"),
                                        base=request.get("base"),
                                        expected_capture_fingerprint=(
                                            expected_fingerprint
                                        ),
                                    )
                                )
                            except StrategyProfileError:
                                raise conflict
                            existing_review = existing.get("review") or {}
                            if not (
                                existing.get("capture_fingerprint")
                                == expected_fingerprint
                                and existing.get("source")
                                == expected_review["source"]
                                and existing_review.get("captured_vs_base")
                                == expected_review["captured_vs_base"]
                                and existing_review.get("unresolved")
                                == expected_review["unresolved"]
                                and existing_review.get("review_fingerprint")
                                == reviewed_fingerprint
                                == expected_review["review_fingerprint"]
                            ):
                                raise conflict
                            artifact = existing
                            artifact_disposition = "recovered_existing"
                        saved_result = {
                            "kind": save_kind,
                            "id": artifact["id"],
                            "display_name": artifact["source"]["display_name"],
                            "fingerprint": artifact["draft_fingerprint"],
                            "artifact_disposition": artifact_disposition,
                            "published": False,
                            "selected": False,
                            "activated": False,
                            "queued": False,
                            "applied": False,
                        }
                    else:
                        raise ControlSurfaceRequestError(
                            "kind must be module_preset or strategy_draft"
                        )
                except ControlSurfaceRequestError:
                    raise
                except ModulePresetConflictError as exc:
                    raise ControlSurfaceRequestError(
                        str(exc), status=409, code=exc.code
                    ) from exc
                except ModulePresetError as exc:
                    raise ControlSurfaceRequestError(
                        str(exc), code=exc.code
                    ) from exc
                except StrategyProfileConflictError as exc:
                    raise ControlSurfaceRequestError(
                        str(exc), status=409
                    ) from exc
                except (SetupCaptureError, StrategyProfileError) as exc:
                    raise ControlSurfaceRequestError(str(exc)) from exc
                try:
                    capture = self.control_store.transition_setup_capture(
                        request_id,
                        "saved",
                        reason=(
                            "capture saved through its existing Linux owner; "
                            "runtime selection and action authority are unchanged"
                        ),
                        saved_result=saved_result,
                        source="control-surface",
                    )
                except (ControlDirectiveError, ValueError) as exc:
                    raise ControlSurfaceRequestError(
                        "Capture artifact is durable but its completion receipt "
                        f"could not be recorded: {exc}",
                        status=503,
                        code="capture_receipt_write_failed",
                    ) from exc
                if capture is None:
                    raise ControlSurfaceRequestError(
                        "Capture artifact is durable but the workflow changed before acknowledgement",
                        status=409,
                        code="capture_receipt_interrupted",
                    )
                disposition = "completed"
                audit = (
                    f"Saved captured {save_kind.replace('_', ' ')} "
                    f"{saved_result['id']} without selecting or applying it"
                )
            else:
                raise ControlSurfaceRequestError(
                    "operation must be request, review, save, or cancel"
                )

            audit_warning = self._append_audit(
                audit,
                # The runtime emits the single operation-ID-correlated ACTION
                # immediately before any capture lifecycle input.  Recording
                # the asynchronous request as a second ACTION would split one
                # input workflow into two intents.
                level="INFO" if operation == "request" else "ACTION",
            )
            response = self.setup_capture()
            response["request"] = {
                "accepted": True,
                "operation": operation,
                "request_id": capture.get("request_id") if capture else None,
                "disposition": disposition,
            }
            if operation == "save":
                response["request"]["saved_result"] = saved_result
            if operation == "review":
                response["review"] = review_result
            if audit_warning:
                response["request"]["warning"] = audit_warning
            return response

    def strategy_history(
        self,
        strategy_id: object = None,
    ) -> dict[str, Any]:
        """Return immutable custom-Strategy revision summaries."""

        try:
            return self.profile_store.history_catalog(strategy_id)
        except StrategyProfileConflictError as exc:
            raise ControlSurfaceRequestError(str(exc), status=409) from exc
        except StrategyProfileError as exc:
            raise ControlSurfaceRequestError(str(exc)) from exc

    def strategy_revision(
        self,
        strategy_id: object,
        logical_version: object,
    ) -> dict[str, Any]:
        """Return one retained Strategy revision without its generated plan."""

        try:
            return self.profile_store.history_revision(
                strategy_id,
                logical_version,
            )
        except StrategyProfileConflictError as exc:
            raise ControlSurfaceRequestError(str(exc), status=409) from exc
        except StrategyProfileError as exc:
            raise ControlSurfaceRequestError(str(exc)) from exc

    def apply_strategy_authoring(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate, publish, or preview one authoring operation."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        operation = str(request.get("operation") or "").strip().lower()
        if operation not in STRATEGY_AUTHORING_OPERATIONS:
            raise ControlSurfaceRequestError(
                "operation must be one of: "
                + ", ".join(STRATEGY_AUTHORING_OPERATIONS)
            )
        try:
            if operation == "validate_base":
                response = self.profile_store.validate_base(request.get("source"))
            elif operation == "publish_base":
                response = self.profile_store.publish_authoring_base(
                    request.get("source"),
                    expected_latest_fingerprint=request.get(
                        "expected_latest_fingerprint"
                    ),
                )
            elif operation == "validate_strategy":
                response = self.profile_store.validate_authoring_strategy(
                    request.get("source")
                )
            elif operation == "publish_strategy":
                response = self.profile_store.publish_authoring_strategy(
                    request.get("source"),
                    expected_source_fingerprint=request.get(
                        "expected_source_fingerprint"
                    ),
                    reviewed_rebase_fingerprint=request.get(
                        "reviewed_rebase_fingerprint"
                    ),
                )
            elif operation == "preview_rebase":
                response = self.profile_store.preview_rebase(
                    request.get("source"),
                    request.get("target_base"),
                )
            elif operation == "materialize_loadout_preset":
                if set(request) != {
                    "operation",
                    "setting_id",
                    "preset",
                    "expected_catalog_fingerprint",
                }:
                    raise StrategyProfileError(
                        "Preset materialization requires exactly operation, "
                        "setting_id, preset, and expected_catalog_fingerprint"
                    )
                materialization = self.profile_store.materialize_loadout_preset(
                    request.get("setting_id"),
                    request.get("preset"),
                    request.get("expected_catalog_fingerprint"),
                )
                response = {
                    "valid": True,
                    "published": False,
                    "materialization": materialization,
                    "publication_activates_strategy": False,
                }
            elif operation == "create_module_preset":
                if set(request) != {
                    "operation",
                    "id",
                    "display_name",
                    "source",
                }:
                    raise ModulePresetError(
                        "Module preset creation requires exactly operation, id, "
                        "display_name, and source",
                        code="invalid_module_preset_request",
                        field="request",
                    )
                preset = self.profile_store.create_module_preset(
                    request.get("id"),
                    request.get("display_name"),
                    request.get("source"),
                )
                response = {
                    "valid": True,
                    "published": False,
                    "preset": preset,
                    "publication_activates_strategy": False,
                }
            elif operation == "compare_strategy_revision":
                response = self.profile_store.compare_strategy_revision(
                    request.get("strategy_id"),
                    request.get("logical_version"),
                )
            elif operation == "preview_restore_strategy":
                if "expected_revision_fingerprint" not in request or (
                    "expected_latest_source_fingerprint" not in request
                ):
                    raise StrategyProfileError(
                        "Restore review requires the selected revision fingerprint "
                        "and the currently opened latest source fingerprint"
                    )
                response = self.profile_store.compare_strategy_revision(
                    request.get("strategy_id"),
                    request.get("logical_version"),
                    expected_revision_fingerprint=request.get(
                        "expected_revision_fingerprint"
                    ),
                    expected_latest_source_fingerprint=request.get(
                        "expected_latest_source_fingerprint"
                    ),
                    require_optimistic_state=True,
                )
            elif operation == "publish_restore_strategy":
                required = {
                    "expected_revision_fingerprint",
                    "expected_latest_source_fingerprint",
                    "reviewed_restore_fingerprint",
                }
                if not required.issubset(request):
                    raise StrategyProfileError(
                        "Restore publication requires revision, latest, and "
                        "review fingerprints"
                    )
                response = self.profile_store.publish_restore_strategy(
                    request.get("strategy_id"),
                    request.get("logical_version"),
                    expected_revision_fingerprint=request.get(
                        "expected_revision_fingerprint"
                    ),
                    expected_latest_source_fingerprint=request.get(
                        "expected_latest_source_fingerprint"
                    ),
                    reviewed_restore_fingerprint=request.get(
                        "reviewed_restore_fingerprint"
                    ),
                )
            else:
                raw_identifier = request.get("strategy_id")
                identifier = normalize_strategy_id(raw_identifier)
                if (
                    identifier is None
                    or str(raw_identifier or "").strip() != identifier
                ):
                    raise StrategyProfileError(
                        "strategy_id must use 3-48 lowercase letters, digits, "
                        "or underscores and start with a letter"
                    )
                try:
                    selected_strategy = self.control_store.status().get(
                        "strategy"
                    )
                except ControlDirectiveError as exc:
                    raise ControlSurfaceRequestError(
                        "Unable to verify the selected Strategy before "
                        f"deletion: {exc}",
                        status=409,
                    ) from exc
                if selected_strategy == identifier:
                    raise ControlSurfaceRequestError(
                        f"Strategy {identifier!r} is currently selected; "
                        "select another Strategy before deleting it. No "
                        "control state was changed.",
                        status=409,
                    )
                retirement = self.profile_store.retire_strategy(
                    identifier,
                    expected_source_fingerprint=request.get(
                        "expected_source_fingerprint"
                    ),
                )
                response = {
                    "valid": True,
                    "published": False,
                    "retired": True,
                    "retirement": retirement,
                }
        except ModulePresetConflictError as exc:
            raise ControlSurfaceRequestError(
                str(exc),
                status=409,
                code=exc.code,
                details={"field": exc.field} if exc.field else None,
            ) from exc
        except ModulePresetError as exc:
            raise ControlSurfaceRequestError(
                str(exc),
                status=400,
                code=exc.code,
                details={"field": exc.field} if exc.field else None,
            ) from exc
        except StrategyProfileConflictError as exc:
            raise ControlSurfaceRequestError(str(exc), status=409) from exc
        except StrategyProfileError as exc:
            raise ControlSurfaceRequestError(str(exc), status=400) from exc

        response["operation"] = operation
        if operation == "publish_base":
            source = response.get("source") or {}
            audit_warning = self._append_audit(
                "Published strategy Base "
                f"{source.get('id')} immutable revision {source.get('revision')}"
            )
            response["catalog"] = self.profile_store.authoring_catalog()
            if audit_warning:
                response["warning"] = audit_warning
        elif operation == "publish_strategy":
            profile = response.get("profile") or {}
            audit_warning = self._append_audit(
                "Published Strategy "
                f"{profile.get('id')} version {profile.get('version')}; "
                "activation unchanged"
            )
            response["catalog"] = self.profile_store.authoring_catalog()
            if audit_warning:
                response["warning"] = audit_warning
        elif operation == "retire_strategy":
            retirement = response["retirement"]
            audit_warning = self._append_audit(
                "Retired Strategy "
                f"{retirement.get('id')} version "
                f"{retirement.get('version')} into the recoverable archive; "
                "selection and activation unchanged"
            )
            response["catalog"] = self.profile_store.authoring_catalog()
            if audit_warning:
                response["warning"] = audit_warning
        elif operation == "publish_restore_strategy":
            restored_from = response.get("restored_from") or {}
            profile = response.get("profile") or {}
            warnings = [
                self._append_audit(
                    "Accepted reviewed restore for Strategy "
                    f"{profile.get('id')} from immutable version "
                    f"{restored_from.get('logical_version')}; selection and "
                    "activation unchanged"
                ),
                self._append_audit(
                    "Published restored Strategy "
                    f"{profile.get('id')} as new latest version "
                    f"{profile.get('version')}; selection and activation unchanged"
                ),
            ]
            response["catalog"] = self.profile_store.authoring_catalog()
            response["history"] = self.profile_store.history_catalog(
                profile.get("id")
            )
            audit_warnings = [item for item in warnings if item]
            if audit_warnings:
                response["warning"] = "; ".join(audit_warnings)
        elif operation == "create_module_preset":
            preset = response["preset"]
            source = request.get("source")
            source_kind = (
                "selected preset"
                if isinstance(source, Mapping) and set(source) == {"preset"}
                else "profile-local definition"
            )
            audit_warning = self._append_audit(
                "Created immutable custom Module preset "
                f"{preset.get('id')} from {source_kind}; Base/Strategy "
                "publication, selection, and activation unchanged"
            )
            response["catalog"] = self.profile_store.authoring_catalog()
            if audit_warning:
                response["warning"] = audit_warning
        return response

    def apply_strategy_profile(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate or atomically publish one custom Farm profile."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        action = str(request.get("action") or "").strip().lower()
        if action not in {"validate", "publish"}:
            raise ControlSurfaceRequestError(
                "action must be validate or publish"
            )
        try:
            if action == "validate":
                response = self.profile_store.validate(request.get("profile"))
                response.pop("source", None)
                response.pop("plan", None)
            else:
                response = self.profile_store.publish(
                    request.get("profile"),
                    expected_source_fingerprint=request.get(
                        "expected_source_fingerprint"
                    ),
                )
        except StrategyProfileConflictError as exc:
            raise ControlSurfaceRequestError(str(exc), status=409) from exc
        except StrategyProfileError as exc:
            raise ControlSurfaceRequestError(str(exc)) from exc

        response["action"] = action
        if action == "publish":
            profile = response.get("profile") or {}
            audit_warning = self._append_audit(
                "Published custom strategy profile "
                f"{profile.get('id')} version {profile.get('version')}"
            )
            response["catalog"] = self.profile_store.catalog()
            if audit_warning:
                response["warning"] = audit_warning
        return response

    def status(self, *, now: Optional[float] = None) -> dict[str, Any]:
        """Return operator intent, observed heartbeat, and process evidence."""

        current_time = datetime.now().timestamp() if now is None else float(now)
        control_error = None
        try:
            control = self.control_store.status(now=current_time)
        except ControlDirectiveError as exc:
            control_error = str(exc)
            control = {
                "state": "UNKNOWN",
                "mode": "UNKNOWN",
                "game_speed_target": MAXIMUM_GAME_SPEED_TARGET,
                "game_speed_target_updated_at": None,
                "game_speed_target_request_id": None,
                "adb_port": None,
                "resume_at": None,
                "remaining_seconds": None,
                "updated_at": None,
                "state_updated_at": None,
                "state_request_id": None,
                "mode_updated_at": None,
                "mode_request_id": None,
                "adb_port_updated_at": None,
                "adb_port_request_id": None,
                "strategy": None,
                "strategy_apply_mode": "next_boundary",
                "strategy_updated_at": None,
                "strategy_request_id": None,
                "gate_decision": None,
                "startup_gate_waivers": {},
                "exclusive_validation": {
                    "schema_version": 1,
                    "current_request_id": None,
                    "receipts": {},
                },
                "interactive_development_lease": None,
                "interactive_development_lease_error": None,
                "emulator_maintenance": None,
                "emulator_maintenance_error": None,
                "battle_workflow": None,
                "battle_workflow_error": None,
                "manual_control": None,
                "manual_control_error": None,
                "setup_capture": None,
                "setup_capture_error": None,
                "exists": self.control_path.exists(),
            }
        control["path"] = self._display_path(self.control_path)
        if control_error:
            control["error"] = control_error

        lines = _tail_lines(self.action_log, max_bytes=262_144)
        observations = self._status_observations(lines, now=current_time)
        observation = observations[0] if observations else None
        prior_transition = None
        if observation is not None:
            prior_transition = next(
                (
                    candidate
                    for candidate in observations[1:]
                    if candidate["state_label"] != observation["state_label"]
                ),
                None,
            )
        runtime = self._runtime_evidence()
        strategy_action_gate = self._strategy_action_gate_status(
            now=current_time,
            runtime=runtime,
        )
        current_run = self._load_activity_scope()
        acknowledgements = self._latest_acknowledgements(
            strategy_action_gate,
            control,
        )
        interactive_development_lease = (
            self._interactive_development_lease_status(
                control=control,
                runtime_authority=strategy_action_gate,
                now=current_time,
            )
        )
        host_maintenance = self._host_maintenance_status(
            control=control,
            runtime_authority=strategy_action_gate,
        )
        emulator_degradation = self._emulator_degradation_status(
            control=control,
            runtime_authority=strategy_action_gate,
            current_run=current_run,
            host_maintenance=host_maintenance,
            now=current_time,
        )
        process_service = (
            self.process_manager.status() if self.process_manager is not None else None
        )
        adb_connection = (
            self.adb_connection_manager.status()
            if self.adb_connection_manager is not None
            else None
        )
        control["startup_gate_context"] = self._startup_gate_context(
            control,
            process_service,
        )
        healthy = bool(runtime["active"] and observation and not observation["stale"])
        current_battle_perks = self._current_battle_perks(current_run)
        confirmed_local_mappings = confirmed_local_mapping_status(
            store=self.confirmed_local_mapping_store,
            candidate_store=self.mapping_candidate_store,
            repository_root=self.repository_root,
            candidate_status=(
                self.save_mapping_integration_manager.status()
            ),
        )
        control_model = self._better_control_model_status(
            control=control,
            acknowledgements=acknowledgements,
            runtime=runtime,
            process_service=process_service,
            runtime_authority=strategy_action_gate,
            now=current_time,
        )

        return {
            "api_version": 1,
            "server_revision": CONTROL_SURFACE_REVISION,
            "capabilities": list(CONTROL_SURFACE_CAPABILITIES),
            "server_time": datetime.fromtimestamp(current_time).astimezone().isoformat(
                timespec="seconds"
            ),
            "healthy": healthy,
            "control": control,
            "acknowledgements": acknowledgements,
            "observation": observation,
            "prior_transition": prior_transition,
            "current_run": (
                {
                    "run_id": current_run["run_id"],
                    "started_at": current_run["started_at"],
                }
                if current_run is not None
                else None
            ),
            "current_battle_perks": current_battle_perks,
            "confirmed_local_mappings": confirmed_local_mappings,
            "strategy_action_gate": strategy_action_gate,
            "interactive_development_lease": interactive_development_lease,
            "host_maintenance": host_maintenance,
            "emulator_degradation": emulator_degradation,
            "control_model": control_model,
            "runtime": runtime,
            "process_service": process_service,
            "adb_connection": adb_connection,
        }

    def _host_maintenance_status(
        self,
        *,
        control: Mapping[str, Any],
        runtime_authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Join a durable host request to its fresh runtime-owned hold proof."""

        request = control.get("emulator_maintenance")
        request = dict(request) if isinstance(request, Mapping) else None
        control_model = runtime_authority.get("control_model")
        acknowledgement = normalize_runtime_recovery_ack(
            control_model.get("emulator_maintenance")
            if isinstance(control_model, Mapping)
            else None
        )
        fresh = bool(
            acknowledgement is not None
            and runtime_authority.get("available") is True
            and runtime_authority.get("stale") is False
            and runtime_authority.get("owner_matches_exact_runtime") is True
        )
        matching = bool(
            request is not None
            and acknowledgement is not None
            and acknowledgement.get("request_id") == request.get("request_id")
            and acknowledgement.get("runtime") == request.get("runtime")
            and acknowledgement.get("battle_scope")
            == request.get("battle_scope")
        )
        holds = runtime_authority.get("holds")
        hold_installed = any(
            isinstance(item, Mapping)
            and item.get("hold") == "emulator_maintenance"
            for item in (holds if isinstance(holds, list) else [])
        )
        host_restart_authorized = bool(
            request is not None
            and request.get("state") == "requested"
            and fresh
            and matching
            and hold_installed
            and acknowledgement.get("state") == "host_restart_authorized"
        )
        if control.get("emulator_maintenance_error"):
            reason = str(control["emulator_maintenance_error"])
        elif request is None:
            reason = "no BlueStacks maintenance is requested"
        elif request.get("state") == "terminal":
            reason = str(
                request.get("terminal_reason") or "maintenance is terminal"
            )
        elif not fresh:
            reason = "fresh runtime maintenance acknowledgement is unavailable"
        elif not matching:
            reason = "runtime maintenance ownership does not match the request"
        elif not hold_installed:
            reason = "the suppressive emulator-maintenance hold is not installed"
        elif host_restart_authorized:
            reason = "the runtime authorized the exact Windows host restart"
        else:
            reason = str(
                acknowledgement.get("reason")
                if acknowledgement is not None
                else "runtime recovery is pending"
            )
        return {
            "schema_version": 1,
            "request": request,
            "runtime_acknowledgement": acknowledgement,
            "acknowledgement_fresh": fresh,
            "owner_matches_request": matching,
            "hold_installed": hold_installed,
            "host_restart_authorized": host_restart_authorized,
            "active": bool(
                request is not None and request.get("state") != "terminal"
            ),
            "exclude_from_degradation": bool(
                acknowledgement is not None
                and acknowledgement.get("exclude_from_degradation") is True
            ),
            "reason": reason,
        }

    def _emulator_degradation_status(
        self,
        *,
        control: Mapping[str, Any],
        runtime_authority: Mapping[str, Any],
        current_run: Optional[Mapping[str, Any]],
        host_maintenance: Mapping[str, Any],
        now: float,
    ) -> dict[str, Any]:
        """Return a conservative, side-effect-free automatic-restart decision."""

        assessed_at = datetime.fromtimestamp(now, tz=timezone.utc)
        control_model = runtime_authority.get("control_model")
        strategy_scope = (
            control_model.get("strategy_scope")
            if isinstance(control_model, Mapping)
            else None
        )
        current_strategy = (
            str(strategy_scope.get("active_battle") or "").strip().lower()
            if isinstance(strategy_scope, Mapping)
            else ""
        )
        run_id = (
            str(current_run.get("run_id") or "").strip()
            if isinstance(current_run, Mapping)
            else ""
        )
        if not run_id:
            return {
                "schema_version": 1,
                "assessed_at": assessed_at.isoformat(timespec="seconds"),
                "status": "ineligible",
                "automatic_ready": False,
                "reason": "an active runtime battle scope is required",
                "current_run_id": None,
                "current_strategy": current_strategy or None,
                "candidate_battle_ids": [],
                "baseline_battle_ids": [],
            }
        cache_key = (run_id, current_strategy)
        with self._emulator_degradation_cache_lock:
            cached = self._emulator_degradation_cache
            if (
                cached is not None
                and cached[1] == cache_key
                and 0 <= now - cached[0] < EMULATOR_DEGRADATION_CACHE_SECONDS
            ):
                assessment = dict(cached[2])
            else:
                try:
                    try:
                        battle_dir_stat = self.battles_dir.stat()
                        battle_signature = (
                            battle_dir_stat.st_mtime_ns,
                            battle_dir_stat.st_size,
                        )
                    except FileNotFoundError:
                        battle_signature = (-1, -1)
                    battle_cache = self._emulator_battle_history_cache
                    if (
                        battle_cache is not None
                        and battle_cache[0] == battle_signature
                    ):
                        battles = list(battle_cache[1])
                    else:
                        battles = load_comparable_battles(self.battles_dir)
                        self._emulator_battle_history_cache = (
                            battle_signature,
                            list(battles),
                        )
                    aggregates = (
                        self.host_performance_store.recent_session_aggregates(
                            current_run_id=run_id,
                            since=assessed_at - timedelta(hours=12),
                        )
                    )
                except (OSError, HostPerformanceStorageError) as exc:
                    return {
                        "schema_version": 1,
                        "assessed_at": assessed_at.isoformat(timespec="seconds"),
                        "status": "telemetry_unavailable",
                        "automatic_ready": False,
                        "reason": f"degradation evidence is unavailable: {exc}",
                        "current_run_id": run_id,
                        "current_strategy": current_strategy or None,
                        "candidate_battle_ids": [],
                        "baseline_battle_ids": [],
                    }
                assessment = assess_emulator_degradation(
                    battles,
                    aggregates,
                    current_strategy=current_strategy,
                    current_run_id=run_id,
                    assessed_at=assessed_at,
                )
                self._emulator_degradation_cache = (
                    now,
                    cache_key,
                    dict(assessment),
                )
        assessment["cooldown_seconds"] = int(
            AUTOMATIC_RESTART_COOLDOWN.total_seconds()
        )

        def suppress(status: str, reason: str) -> dict[str, Any]:
            return {
                **assessment,
                "status": status,
                "automatic_ready": False,
                "reason": reason,
            }

        if control.get("emulator_maintenance_error"):
            return suppress(
                "maintenance_state_invalid",
                str(control.get("emulator_maintenance_error")),
            )
        durable = host_maintenance.get("request")
        if isinstance(durable, Mapping):
            if durable.get("state") != "terminal":
                return suppress(
                    "maintenance_active",
                    "a BlueStacks maintenance request is already active",
                )
            if str(durable.get("battle_scope") or "") == run_id:
                return suppress(
                    "already_recovered_this_battle",
                    "automatic recovery is limited to once per battle",
                )
            try:
                terminal_at = datetime.fromisoformat(
                    str(durable.get("terminal_at") or "")
                )
                if terminal_at.tzinfo is None:
                    terminal_at = terminal_at.replace(tzinfo=timezone.utc)
                remaining = AUTOMATIC_RESTART_COOLDOWN - (
                    assessed_at - terminal_at.astimezone(timezone.utc)
                )
            except (TypeError, ValueError):
                remaining = timedelta(0)
            if remaining > timedelta(0):
                blocked = suppress(
                    "cooldown",
                    "the eight-hour automatic-restart cooldown is active",
                )
                blocked["cooldown_remaining_seconds"] = int(
                    remaining.total_seconds()
                )
                return blocked
        if assessment.get("automatic_ready") is not True:
            return assessment
        if control.get("state") != "RUNNING":
            return suppress(
                "control_not_running",
                "Automation must be Enabled before automatic recovery",
            )
        holds = runtime_authority.get("holds")
        strategy_authority = runtime_authority.get("strategy_action_authority")
        lifecycle_authority = runtime_authority.get("lifecycle_action_authority")
        runtime_ready = bool(
            runtime_authority.get("available") is True
            and runtime_authority.get("stale") is False
            and runtime_authority.get("owner_matches_exact_runtime") is True
            and runtime_authority.get("active_battle") is True
            and str(runtime_authority.get("runtime_battle_scope") or "")
            == run_id
            and str(runtime_authority.get("primary_state") or "").upper()
            == "RUNNING"
            and isinstance(runtime_authority.get("owner"), Mapping)
            and not (holds if isinstance(holds, list) else [])
            and isinstance(strategy_authority, Mapping)
            and strategy_authority.get("allowed") is True
            and isinstance(lifecycle_authority, Mapping)
            and lifecycle_authority.get("allowed") is True
        )
        if not runtime_ready:
            return suppress(
                "runtime_not_ready",
                "fresh unheld RUNNING battle authority is required",
            )
        return assessment

    def apply_host_maintenance(
        self,
        request: Mapping[str, Any],
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Serialize request creation and host transitions with control writes."""

        with self._control_mutation_lock:
            return self._apply_host_maintenance_locked(request, now=now)

    def _apply_host_maintenance_locked(
        self,
        request: Mapping[str, Any],
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Request maintenance or apply one exact Windows host transition."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        operation = str(request.get("operation") or "").strip().lower()
        request_id = str(request.get("request_id") or "").strip().lower()
        if operation not in {"request", "acknowledge", "complete", "fail"}:
            raise ControlSurfaceRequestError(
                "operation must be request, acknowledge, complete, or fail"
            )
        if operation == "request":
            return self._request_emulator_maintenance(now=now)
        if not request_id:
            raise ControlSurfaceRequestError("request_id is required")
        current = self.status(now=now)
        maintenance = current.get("host_maintenance") or {}
        durable = maintenance.get("request")
        if not isinstance(durable, Mapping) or durable.get("request_id") != request_id:
            raise ControlSurfaceRequestError(
                "Host maintenance request ID does not match",
                status=409,
                code="maintenance_request_mismatch",
            )
        observed_at = datetime.fromtimestamp(
            datetime.now().timestamp() if now is None else float(now)
        ).astimezone().isoformat(timespec="seconds")
        host_ack = {
            "host_id": request.get("host_id"),
            "adb_port": request.get("adb_port"),
            "process_id": request.get("process_id"),
            "process_started_at": request.get("process_started_at"),
            "executable_path": request.get("executable_path"),
            "instance_name": request.get("instance_name"),
            "observed_at": observed_at,
        }
        host_completion = {
            **host_ack,
            "previous_process_id": request.get("previous_process_id"),
            "previous_process_started_at": request.get(
                "previous_process_started_at"
            ),
        }
        try:
            if operation == "acknowledge":
                if durable.get("state") == "host_acknowledged" and (
                    _same_host_transition(
                        durable.get("host_ack"),
                        host_ack,
                        include_previous=False,
                    )
                ):
                    saved = dict(durable)
                elif maintenance.get("host_restart_authorized") is not True:
                    raise ControlSurfaceRequestError(
                        str(
                            maintenance.get("reason")
                            or "the runtime has not authorized host mutation"
                        ),
                        status=409,
                        code="maintenance_not_authorized",
                    )
                else:
                    saved = (
                        self.control_store.acknowledge_emulator_maintenance_host(
                            request_id,
                            host_ack=host_ack,
                            now=now,
                        )
                    )
            elif operation == "complete":
                if durable.get("state") == "host_restarted" and (
                    _same_host_transition(
                        durable.get("host_completion"),
                        host_completion,
                        include_previous=True,
                    )
                ):
                    saved = dict(durable)
                else:
                    saved = self.control_store.complete_emulator_maintenance_host(
                        request_id,
                        host_completion=host_completion,
                        now=now,
                    )
            else:
                failure_reason = " ".join(
                    str(request.get("reason") or "").split()
                )[:256]
                if not failure_reason:
                    raise ControlSurfaceRequestError(
                        "fail requires a bounded reason"
                    )
                if durable.get("state") not in {"requested", "terminal"}:
                    raise ControlSurfaceRequestError(
                        "Host failure cannot release recovery after the exact "
                        "process acknowledgement; source restoration must be "
                        "reconciled first",
                        status=409,
                        code="maintenance_reconciliation_required",
                    )
                saved = (
                    dict(durable)
                    if durable.get("state") == "terminal"
                    else self.control_store.finish_emulator_maintenance(
                        request_id,
                        disposition="host_failed",
                        reason=failure_reason,
                        source="windows-bluestacks-maintenance",
                        now=now,
                    )
                )
        except ControlSurfaceRequestError:
            raise
        except (ControlDirectiveError, ValueError) as exc:
            raise ControlSurfaceRequestError(
                str(exc),
                status=409,
                code="maintenance_conflict",
            ) from exc
        response = self.status(now=now)
        response["request"] = {
            "accepted": True,
            "action": "host_maintenance",
            "disposition": operation,
            "request_id": request_id,
        }
        response["host_maintenance"]["request"] = saved
        return response

    def _request_emulator_maintenance(
        self,
        *,
        now: Optional[float],
    ) -> dict[str, Any]:
        """Create one detector-authorized request bound to the live runtime."""

        current = self.status(now=now)
        assessment = current.get("emulator_degradation") or {}
        if assessment.get("automatic_ready") is not True:
            raise ControlSurfaceRequestError(
                str(
                    assessment.get("reason")
                    or "automatic BlueStacks recovery is not ready"
                ),
                status=409,
                code="emulator_degradation_not_ready",
            )
        authority = current.get("strategy_action_gate") or {}
        owner = authority.get("owner")
        current_run = current.get("current_run")
        if not isinstance(owner, Mapping) or not isinstance(
            current_run, Mapping
        ):
            raise ControlSurfaceRequestError(
                "fresh runtime ownership and battle scope are required",
                status=409,
                code="maintenance_runtime_unavailable",
            )
        host = assessment.get("host_evidence")
        trigger = {
            "schema_version": assessment.get("schema_version"),
            "assessed_at": assessment.get("assessed_at"),
            "candidate_battle_ids": assessment.get("candidate_battle_ids", []),
            "baseline_battle_ids": assessment.get("baseline_battle_ids", []),
            "candidate_cph_ratio": assessment.get("candidate_cph_ratio"),
            "individual_cph_ratios": assessment.get(
                "individual_cph_ratios", []
            ),
            "effective_game_speed_ratio": assessment.get(
                "effective_game_speed_ratio"
            ),
            "host_sample_count": (
                host.get("sample_count") if isinstance(host, Mapping) else None
            ),
            "handle_ratio": (
                host.get("handle_ratio") if isinstance(host, Mapping) else None
            ),
            "handle_delta": (
                host.get("handle_delta") if isinstance(host, Mapping) else None
            ),
        }
        runtime = {
            "runtime_id": str(owner.get("runtime_id") or ""),
            "pid": owner.get("pid"),
            "adb_target": str(owner.get("adb_target") or ""),
            "target_generation": owner.get("target_generation"),
            "state_request_id": current.get("control", {}).get(
                "state_request_id"
            ),
        }
        try:
            saved = self.control_store.request_emulator_maintenance(
                reason=str(assessment.get("reason") or "degradation detected"),
                source="windows-control-surface",
                runtime=runtime,
                battle_scope=str(current_run.get("run_id") or ""),
                trigger=trigger,
                now=now,
            )
        except (ControlDirectiveError, ValueError) as exc:
            raise ControlSurfaceRequestError(
                str(exc),
                status=409,
                code="maintenance_conflict",
            ) from exc
        response = self.status(now=now)
        response["request"] = {
            "accepted": True,
            "action": "host_maintenance",
            "disposition": "requested",
            "request_id": saved["request_id"],
        }
        response["host_maintenance"]["request"] = saved
        return response

    def apply_interactive_development_lease(
        self,
        request: Mapping[str, Any],
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Apply one request, heartbeat, or release lease operation."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        current_time = datetime.now().timestamp() if now is None else float(now)
        operation = str(request.get("operation") or "").strip().lower()
        with self._process_action_lock:
            try:
                if operation == "request":
                    owner_label = " ".join(
                        str(request.get("owner_label") or "").split()
                    )
                    if not owner_label:
                        raise ControlSurfaceRequestError(
                            "request requires owner_label"
                        )
                    current = self.status(now=current_time)
                    current_lease = current["interactive_development_lease"]
                    requested = current_lease.get("request")
                    acknowledgement = current_lease.get(
                        "runtime_acknowledgement"
                    )
                    if (
                        isinstance(requested, Mapping)
                        and requested.get("request_state") != "terminal"
                    ) or (
                        isinstance(acknowledgement, Mapping)
                        and acknowledgement.get("state")
                        in {
                            "pending",
                            "active",
                            "release_pending",
                            "release_blocked",
                            "expiry_pending",
                            "termination_blocked",
                        }
                    ):
                        raise ControlSurfaceRequestError(
                            "An interactive development lease request is busy",
                            status=409,
                            code="busy",
                        )
                    authority = current.get("strategy_action_gate") or {}
                    owner = authority.get("owner")
                    if (
                        authority.get("available") is not True
                        or authority.get("stale") is True
                        or authority.get("owner_matches_active_runtime") is not True
                        or not isinstance(owner, Mapping)
                    ):
                        raise ControlSurfaceRequestError(
                            "Interactive development requires fresh structured "
                            "runtime ownership evidence",
                            status=409,
                        )
                    if current["control"].get("state") != "RUNNING":
                        raise ControlSurfaceRequestError(
                            "Interactive development requires operator control RUNNING",
                            status=409,
                        )
                    screen_state = str(
                        authority.get("primary_state") or "UNKNOWN"
                    ).upper()
                    if screen_state in {
                        "UNKNOWN",
                        "GAME_OVER",
                        "TOURNAMENT_RESULTS",
                    }:
                        raise ControlSurfaceRequestError(
                            "Interactive development requires a fresh non-terminal "
                            "starting screen",
                            status=409,
                        )
                    lease = self.control_store.request_interactive_development_lease(
                        owner_label=owner_label,
                        runtime=owner,
                        starting_evidence={
                            "screen_state": screen_state,
                            "battle_active": authority.get("active_battle") is True,
                            "battle_scope": authority.get(
                                "runtime_battle_scope"
                            ),
                            "observed_at": authority.get("observed_at"),
                        },
                        now=current_time,
                        ttl_seconds=INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS,
                    )
                    warning = self._append_audit(
                        "Interactive development lease requested: "
                        f"owner={lease['owner_label']} lease={lease['lease_id']} "
                        f"runtime={lease['runtime']['runtime_id']} "
                        f"pid={lease['runtime']['pid']} "
                        f"target={lease['runtime']['adb_target']}"
                    )
                elif operation == "heartbeat":
                    lease_id = str(request.get("lease_id") or "").strip().lower()
                    if not lease_id:
                        raise ControlSurfaceRequestError(
                            "heartbeat requires lease_id"
                        )
                    self._require_fresh_interactive_development_runtime(
                        lease_id,
                        now=current_time,
                    )
                    lease = (
                        self.control_store.heartbeat_interactive_development_lease(
                            lease_id,
                            now=current_time,
                            ttl_seconds=(
                                INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS
                            ),
                        )
                    )
                    warning = None
                elif operation == "release":
                    lease_id = str(request.get("lease_id") or "").strip().lower()
                    if not lease_id:
                        raise ControlSurfaceRequestError("release requires lease_id")
                    lease = self.control_store.release_interactive_development_lease(
                        lease_id,
                        now=current_time,
                    )
                    warning = self._append_audit(
                        "Interactive development lease release requested: "
                        f"lease={lease['lease_id']} owner={lease['owner_label']}",
                        level="INFO",
                    )
                else:
                    raise ControlSurfaceRequestError(
                        "operation must be request, heartbeat, or release"
                    )
            except ControlDirectiveError as exc:
                raise ControlSurfaceRequestError(str(exc), status=409) from exc
            except ValueError as exc:
                if isinstance(exc, ControlSurfaceRequestError):
                    raise
                message = str(exc)
                status = 409 if any(
                    marker in message.lower()
                    for marker in (
                        "busy",
                        "does not match",
                        "expired",
                        "no longer",
                        "no valid",
                    )
                ) else 400
                raise ControlSurfaceRequestError(message, status=status) from exc

            response = self.status(now=current_time)
            response["operation"] = {
                "accepted": True,
                "operation": operation,
                "lease_id": lease["lease_id"],
            }
            if warning:
                response["operation"]["warning"] = warning
            return response

    def _require_fresh_interactive_development_runtime(
        self,
        lease_id: str,
        *,
        now: float,
    ) -> None:
        """Reject a heartbeat unless the request still names the live owner."""

        current = self.status(now=now)
        lease_status = current.get("interactive_development_lease") or {}
        lease = lease_status.get("request")
        if not isinstance(lease, Mapping):
            raise ControlSurfaceRequestError(
                "No valid interactive development lease request exists",
                status=409,
            )
        if lease.get("lease_id") != lease_id:
            raise ControlSurfaceRequestError(
                "Interactive development lease ID does not match",
                status=409,
            )
        authority = current.get("strategy_action_gate") or {}
        owner = authority.get("owner")
        if (
            current.get("control", {}).get("state") != "RUNNING"
            or authority.get("available") is not True
            or authority.get("stale") is True
            or authority.get("owner_matches_active_runtime") is not True
            or not isinstance(owner, Mapping)
            or dict(lease.get("runtime") or {})
            != {
                "runtime_id": str(owner.get("runtime_id") or ""),
                "pid": owner.get("pid"),
                "adb_target": str(owner.get("adb_target") or ""),
            }
        ):
            raise ControlSurfaceRequestError(
                "Interactive development runtime ownership is no longer fresh "
                "or matching",
                status=409,
            )

    def publish_host_performance(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist idempotent Windows host-performance aggregates."""

        current_run = self._load_activity_scope()
        try:
            return self.host_performance_store.publish(
                request,
                server_run_id=(
                    current_run["run_id"]
                    if current_run is not None
                    else None
                ),
            )
        except HostPerformancePayloadError as exc:
            aggregate_index = exc.aggregate_index
            raise ControlSurfaceRequestError(
                str(exc),
                code=(
                    "invalid_host_performance_aggregate"
                    if aggregate_index is not None
                    else "invalid_host_performance_request"
                ),
                details=(
                    {"aggregate_index": aggregate_index}
                    if aggregate_index is not None
                    else None
                ),
            ) from exc
        except HostPerformanceStorageError as exc:
            raise ControlSurfaceRequestError(
                f"Unable to persist host-performance telemetry: {exc}",
                status=503,
            ) from exc

    def apply_control(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one allowlisted control-file mutation and return fresh status."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        with self._control_mutation_lock:
            return self._apply_control_locked(request)

    def _apply_control_locked(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply a control mutation outside any process replacement window."""

        requested_action = str(request.get("action") or "").strip().lower()
        # Retain the pre-revision-28 API name for non-GUI coordination
        # consumers. New clients present this authority transition as Enable.
        action = "enable" if requested_action == "resume" else requested_action
        disposition = "requested"
        try:
            if action in {"start_battle", "attach_battle"}:
                current = self.status()
                availability = (
                    current.get("control_model", {})
                    .get("actions", {})
                    .get(action, {})
                )
                if availability.get("available") is not True:
                    raise ControlSurfaceRequestError(
                        str(availability.get("reason") or "workflow unavailable"),
                        status=409,
                        code=str(availability.get("code") or "unavailable"),
                        details={
                            "action": action,
                            "observation": current.get("control_model", {}).get(
                                "observation"
                            ),
                        },
                    )
                evidence = current.get("control_model", {}).get(
                    "workflow_evidence"
                )
                workflow = self.control_store.request_battle_workflow(
                    action,
                    evidence=evidence,
                    strategy=(
                        current.get("control_model", {})
                        .get("strategy_scope", {})
                        .get("startup_default")
                    ),
                    source="control-surface",
                )
                audit = (
                    "Requested explicit Start Battle intent"
                    if action == "start_battle"
                    else "Requested explicit Attach to Battle intent"
                )
            elif action == "take_manual_control":
                current = self.status()
                availability = (
                    current.get("control_model", {})
                    .get("actions", {})
                    .get(action, {})
                )
                if availability.get("available") is not True:
                    if availability.get("code") == "manual_control_active":
                        disposition = "no_op"
                        audit = "Manual control is already requested or active"
                    else:
                        raise ControlSurfaceRequestError(
                            str(availability.get("reason") or "workflow unavailable"),
                            status=409,
                            code=str(availability.get("code") or "unavailable"),
                        )
                else:
                    surrender_collection = str(
                        request.get(
                            "manual_surrender_collection",
                            "minimal",
                        )
                    ).strip().lower()
                    if surrender_collection not in {"minimal", "full"}:
                        raise ControlSurfaceRequestError(
                            "manual_surrender_collection must be minimal or full"
                        )
                    manual_control = self.control_store.request_manual_control(
                        evidence=current["control_model"]["workflow_evidence"],
                        surrender_collection=surrender_collection,
                        source="control-surface",
                    )
                    audit = (
                        "Requested Take Manual Control with an indefinite Pause; "
                        f"manual Surrender collection={surrender_collection}"
                    )
            elif action == "return_control":
                current = self.status()
                availability = (
                    current.get("control_model", {})
                    .get("actions", {})
                    .get(action, {})
                )
                manual = current.get("control_model", {}).get("manual_control")
                if availability.get("available") is not True:
                    raise ControlSurfaceRequestError(
                        str(availability.get("reason") or "workflow unavailable"),
                        status=409,
                        code=str(availability.get("code") or "unavailable"),
                    )
                manual_control = self.control_store.request_return_control(
                    str(manual.get("manual_control_id") or ""),
                    evidence=current["control_model"]["workflow_evidence"],
                    source="control-surface",
                )
                audit = (
                    "Requested Return Control reconciliation; automation remains Paused"
                )
            elif action == "pause":
                runtime_live = bool(self._runtime_evidence().get("active"))
                if not runtime_live and self.process_manager is not None:
                    runtime_live = bool(self.process_manager.status().get("active"))
                if not runtime_live:
                    raise ControlSurfaceRequestError(
                        "Start Automation before pausing action authority",
                        status=409,
                        code="process_stopped",
                    )
                minutes = request.get("minutes")
                resume_at = None
                description = "indefinitely"
                if minutes is not None:
                    if isinstance(minutes, bool):
                        raise ControlSurfaceRequestError("minutes must be a number")
                    try:
                        parsed_minutes = float(minutes)
                    except (TypeError, ValueError) as exc:
                        raise ControlSurfaceRequestError(
                            "minutes must be a number"
                        ) from exc
                    if (
                        not math.isfinite(parsed_minutes)
                        or parsed_minutes <= 0
                        or parsed_minutes > MAX_PAUSE_MINUTES
                    ):
                        raise ControlSurfaceRequestError(
                            f"minutes must be greater than 0 and no more than "
                            f"{MAX_PAUSE_MINUTES}"
                        )
                    resume_at = datetime.now().timestamp() + (parsed_minutes * 60)
                    description = f"for {parsed_minutes:g} minutes"
                try:
                    saved = self.control_store.set_paused_unless_stopped(
                        resume_at=resume_at,
                        source="control-surface",
                    )
                except ControlDirectiveError as exc:
                    raise ControlSurfaceRequestError(
                        str(exc), status=409, code="control_invalid"
                    ) from exc
                if str(saved.get("state") or "").upper() == "STOPPED":
                    disposition = "no_op"
                    audit = "Automation is already STOPPED; Pause did not override Stop"
                else:
                    audit = f"Requested PAUSED {description}"
            elif action == "enable":
                current = self.status()
                effective_authority = str(
                    current.get("control_model", {})
                    .get("action_authority", {})
                    .get("effective")
                    or "unknown"
                ).lower()
                availability = (
                    current.get("control_model", {})
                    .get("actions", {})
                    .get("enable", {})
                )
                if availability.get("available") is not True:
                    raise ControlSurfaceRequestError(
                        str(
                            availability.get("reason")
                            or "Automation Enable is unavailable"
                        ),
                        status=409,
                        code=str(availability.get("code") or "unavailable"),
                    )
                manual_error = current.get("control", {}).get(
                    "manual_control_error"
                )
                if manual_error:
                    raise ControlSurfaceRequestError(
                        str(manual_error),
                        status=409,
                        code="manual_control_invalid",
                    )
                state_ack = current.get("acknowledgements", {}).get("state")
                durable_state = str(
                    current.get("control", {}).get("state") or ""
                ).upper()
                runtime_requires_fresh_enable = bool(
                    durable_state == "RUNNING"
                    and effective_authority != "enabled"
                )
                manual = current.get("control_model", {}).get("manual_control")
                if (
                    isinstance(manual, Mapping)
                    and manual.get("status")
                    not in MANUAL_CONTROL_TERMINAL_STATUSES
                ):
                    if manual.get("status") not in {
                        "return_requested",
                        "awaiting_enable",
                        "reconciling",
                        "awaiting_configuration",
                        "awaiting_manual_correction",
                    }:
                        raise ControlSurfaceRequestError(
                            "Use Return Control before enabling automated actions",
                            status=409,
                            code="return_control_required",
                        )
                    enable_already_requested = bool(
                        manual.get("status") == "awaiting_enable"
                        and str(
                            current.get("control", {}).get("state") or ""
                        ).upper()
                        == "RUNNING"
                        and current.get("control", {}).get("resume_at") is None
                        and not runtime_requires_fresh_enable
                    )
                    if enable_already_requested:
                        disposition = "pending"
                        manual_control = dict(manual)
                        audit = "Automation Enable is already pending acknowledgement"
                    else:
                        retrying_configuration = manual.get("status") in {
                            "awaiting_configuration",
                            "awaiting_manual_correction",
                        }
                        if manual.get("status") == "reconciling":
                            if durable_state == "RUNNING":
                                if runtime_requires_fresh_enable:
                                    self.control_store.set_state(
                                        "RUNNING",
                                        source="control-surface",
                                    )
                                    audit = (
                                        "Requested a fresh Automation Enable "
                                        "while Return Control reporting remains "
                                        "pending"
                                    )
                                else:
                                    disposition = "no_op"
                                    audit = (
                                        "Automation is already Enabled; Return "
                                        "Control reporting remains pending"
                                    )
                            else:
                                self.control_store.set_state(
                                    "RUNNING",
                                    source="control-surface",
                                )
                                audit = (
                                    "Requested Automation Enabled while Return "
                                    "Control reporting remains pending"
                                )
                            manual_control = dict(manual)
                        elif (
                            runtime_requires_fresh_enable
                            and manual.get("status") == "awaiting_enable"
                            and str(
                                current.get("control", {}).get("state") or ""
                            ).upper()
                            == "RUNNING"
                        ):
                            self.control_store.set_state(
                                "RUNNING",
                                source="control-surface",
                            )
                            manual_control = dict(manual)
                            audit = (
                                "Requested a fresh Automation Enable after "
                                "the runtime remained safely Paused during "
                                "Return Control"
                            )
                        else:
                            manual_control = (
                                self.control_store.enable_after_return_control(
                                    str(manual.get("manual_control_id") or ""),
                                    source="control-surface",
                                )
                            )
                            audit = (
                                "Requested Automation Enabled for a new Return "
                                "Control configuration refresh"
                                if retrying_configuration
                                else "Requested Automation Enabled; Return Control "
                                "must refresh save evidence before ordinary input"
                            )
                else:
                    if current["control"].get("state") == "RUNNING":
                        if runtime_requires_fresh_enable:
                            self.control_store.set_state(
                                "RUNNING",
                                source="control-surface",
                            )
                            audit = (
                                "Requested a fresh Automation Enable after "
                                "the runtime remained safely Paused"
                            )
                        elif (
                            isinstance(state_ack, Mapping)
                            and state_ack.get("acknowledges_current") is True
                        ):
                            disposition = "no_op"
                            audit = "Automation actions are already Enabled"
                        else:
                            disposition = "pending"
                            audit = (
                                "Automation Enable is awaiting runtime "
                                "acknowledgement"
                            )
                    else:
                        self.control_store.set_state(
                            "RUNNING", source="control-surface"
                        )
                        audit = "Requested Automation Enabled"
            elif action == "stop":
                current = self.status()
                state_ack = current.get("acknowledgements", {}).get("state")
                if (
                    current["control"].get("state") == "STOPPED"
                    and isinstance(state_ack, Mapping)
                    and state_ack.get("acknowledges_current") is True
                ):
                    disposition = "no_op"
                    audit = "Automation input is already STOPPED"
                else:
                    self.control_store.set_state_and_interrupt_operator_workflows(
                        "STOPPED",
                        "legacy STOPPED authority directive requested",
                        source="control-surface",
                    )
                    audit = (
                        "Requested legacy STOPPED authority directive; process "
                        "lifecycle is unchanged"
                    )
            elif action in {"terminal_policy", "mode"}:
                raw_policy = request.get(
                    "policy" if action == "terminal_policy" else "mode"
                )
                policy_aliases = {
                    "continue_automatically": "NEXT_BATTLE",
                    "next_battle": "NEXT_BATTLE",
                    "wait": "WAIT",
                    "return_or_stay_home": "HOME",
                    "stay_home": "HOME",
                    "home": "HOME",
                }
                raw_text = str(raw_policy or "").strip()
                mode = policy_aliases.get(raw_text.lower(), raw_text.upper())
                current = self.status()
                mode_ack = current.get("acknowledgements", {}).get("mode")
                normalized_mode = normalize_automation_mode(mode)
                process_live = bool(
                    current.get("control_model", {})
                    .get("process", {})
                    .get("live")
                )
                if (
                    current["control"].get("exists") is True
                    and current["control"].get("mode") == normalized_mode
                ):
                    if (
                        not process_live
                        or (
                            isinstance(mode_ack, Mapping)
                            and mode_ack.get("acknowledges_current") is True
                        )
                    ):
                        disposition = "no_op"
                    else:
                        disposition = "pending"
                    saved = current["control"]
                else:
                    saved = self.control_store.set_mode(
                        mode,
                        source="control-surface",
                    )
                audit = f"Set When this battle ends to {saved['mode']}"
            elif action == "game_speed":
                game_speed_target = request.get("target")
                saved = self.control_store.set_game_speed_target(
                    game_speed_target,
                    source="control-surface",
                )
                saved_target = saved["game_speed_target"]
                audit = f"Requested game speed target x{saved_target:.1f}"
            elif action == "resolve_gate":
                request_id = str(request.get("request_id") or "").strip()
                decision_id = str(request.get("decision_id") or "").strip().lower()
                if not request_id or not decision_id:
                    raise ControlSurfaceRequestError(
                        "resolve_gate requires request_id and decision_id"
                    )
                directive = self.control_store.resolve_gate_decision(
                    request_id,
                    decision_id,
                    source="control-surface",
                )
                if directive is None:
                    raise ControlSurfaceRequestError(
                        "Gate decision is no longer pending",
                        status=409,
                    )
                audit = (
                    f"Resolved startup gate {directive['check_id']} with "
                    f"{directive['decision_id']} ({directive['request_id']})"
                )
            elif action == "resolve_tournament_launch":
                request_id = str(request.get("request_id") or "").strip()
                decision = str(request.get("decision") or "").strip().lower()
                if not request_id or decision not in {"start", "cancel"}:
                    raise ControlSurfaceRequestError(
                        "resolve_tournament_launch requires request_id and "
                        "decision start or cancel"
                    )
                if decision == "start":
                    current_status = self.status()
                    runtime = current_status.get("runtime") or {}
                    process = current_status.get("process_service") or {}
                    observation = current_status.get("observation")
                    process_active = bool(
                        runtime.get("active") or process.get("active")
                    )
                    observed_state = (
                        str(observation.get("state_label") or "")
                        if isinstance(observation, Mapping)
                        else ""
                    )
                    observation_fresh = bool(
                        isinstance(observation, Mapping)
                        and not observation.get("stale")
                    )
                    if not process_active:
                        raise ControlSurfaceRequestError(
                            "Start Tournament requires an active automation runtime",
                            status=409,
                        )
                    if current_status["control"].get("state") != "RUNNING":
                        raise ControlSurfaceRequestError(
                            "Enable Automation before starting Tournament",
                            status=409,
                        )
                    if (
                        not observation_fresh
                        or observed_state
                        not in {"HOME_SCREEN", "TOURNAMENT_SCREEN"}
                    ):
                        raise ControlSurfaceRequestError(
                            "Start Tournament requires fresh Home or Tournament "
                            "entry evidence",
                            status=409,
                        )
                receipt = (
                    self.control_store.resolve_exclusive_validation_launch(
                        request_id,
                        decision,
                        source="control-surface",
                    )
                )
                if receipt is None:
                    raise ControlSurfaceRequestError(
                        "Tournament launch decision is no longer pending",
                        status=409,
                    )
                audit = (
                    f"Tournament launch {decision} selected "
                    f"({receipt['request_id']})"
                )
            elif action == "configure_run":
                raw_checks = request.get("skip_checks")
                if not isinstance(raw_checks, list):
                    raise ControlSurfaceRequestError(
                        "configure_run requires a skip_checks array"
                    )
                skip_checks = {
                    str(check_id or "").strip().lower()
                    for check_id in raw_checks
                }
                current = self.status()
                process_service = current.get("process_service") or {}
                process_active = bool(
                    current.get("runtime", {}).get("active")
                    or process_service.get("active")
                )
                if (
                    process_active
                    and current["control"].get("state") != "PAUSED"
                ):
                    raise ControlSurfaceRequestError(
                        "Pause automation before configuring the run",
                        status=409,
                    )
                context = current["control"].get("startup_gate_context") or {}
                allowed = {
                    str(check.get("id") or "")
                    for check in context.get("checks") or []
                    if isinstance(check, Mapping)
                }
                unsupported = skip_checks - allowed
                if unsupported:
                    raise ControlSurfaceRequestError(
                        "Checks are not configurable for strategy "
                        f"{context.get('strategy') or 'none'}: "
                        + ", ".join(sorted(unsupported))
                    )
                configured = self.control_store.configure_startup_gate_waivers(
                    sorted(skip_checks),
                    strategy=str(context.get("strategy") or ""),
                    source="control-surface",
                )
                audit = (
                    f"Configured next {context.get('strategy') or 'strategy'} run: "
                    + (
                        "skip " + ", ".join(sorted(configured))
                        if configured
                        else "strategy defaults"
                    )
                )
            else:
                raise ControlSurfaceRequestError(
                    "action must be pause, enable, start_battle, attach_battle, "
                    "take_manual_control, return_control, terminal_policy, "
                    "game_speed, resolve_gate, resolve_tournament_launch, or "
                    "configure_run (resume and stop remain compatibility aliases)"
                )
        except ControlDirectiveError as exc:
            raise ControlSurfaceRequestError(str(exc), status=409) from exc
        except ValueError as exc:
            if isinstance(exc, ControlSurfaceRequestError):
                raise
            raise ControlSurfaceRequestError(str(exc)) from exc

        audit_warning = self._append_audit(audit)
        response = self.status()
        response["request"] = {"accepted": True, "action": requested_action}
        if requested_action != action:
            response["request"]["canonical_action"] = action
        if action in {
            "start_battle",
            "attach_battle",
            "take_manual_control",
            "return_control",
            "pause",
            "enable",
            "stop",
            "terminal_policy",
        } or disposition == "no_op":
            response["request"]["disposition"] = disposition
        if action in {"start_battle", "attach_battle"}:
            response["request"]["request_id"] = workflow["request_id"]
        elif action in {
            "take_manual_control",
            "return_control",
            "enable",
        } and "manual_control" in locals():
            response["request"]["manual_control_id"] = manual_control[
                "manual_control_id"
            ]
        if action == "resolve_gate":
            response["request"]["request_id"] = directive["request_id"]
            response["request"]["decision_id"] = directive["decision_id"]
        elif action == "resolve_tournament_launch":
            response["request"]["request_id"] = receipt["request_id"]
            response["request"]["decision_id"] = decision
        elif action == "configure_run":
            response["request"]["skip_checks"] = sorted(configured)
        if audit_warning:
            response["request"]["warning"] = audit_warning
        return response

    @staticmethod
    def _startup_gate_context(
        control: Mapping[str, Any],
        process_service: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        strategy = str(control.get("strategy") or "").strip().lower()
        if not strategy and isinstance(process_service, Mapping):
            strategy = str(process_service.get("strategy") or "").strip().lower()
        strategy = strategy or "farm"
        try:
            return startup_gate_context_for_strategy(strategy)
        except (OSError, TypeError, ValueError):
            return {"strategy": strategy, "checks": []}

    def apply_process_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Start or stop the configured automation service at a safe boundary."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        with self._process_action_lock:
            return self._apply_process_action_locked(request)

    def _apply_process_action_locked(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        manager = self.process_manager
        if manager is None:
            raise ControlSurfaceRequestError(
                "Automation process management is not configured",
                status=503,
            )

        action = str(request.get("action") or "").strip().lower()
        disposition = "completed"
        if action == "start":
            obsolete = {
                name
                for name in ("run_state", "startup_gate_policy")
                if name in request
            }
            if obsolete:
                raise ControlSurfaceRequestError(
                    "Start Automation no longer selects action authority or a "
                    "battle workflow; remove " + ", ".join(sorted(obsolete)),
                    status=409,
                    code="obsolete_start_parameters",
                )
            requested_strategy = None
            if "strategy" in request:
                strategy = request.get("strategy")
                if not isinstance(strategy, str) or not strategy.strip():
                    raise ControlSurfaceRequestError(
                        "strategy must be a non-empty string"
                    )
                requested_strategy = strategy.strip().lower()
                if not self.profile_store.has_strategy(requested_strategy):
                    raise ControlSurfaceRequestError(
                        "Strategy must be one of: "
                        + ", ".join(self.profile_store.strategy_ids())
                    )
            before = manager.status()
            if (
                not before.get("active")
                and before.get("adb_connection_owner_error")
            ):
                raise ControlSurfaceRequestError(
                    str(before["adb_connection_owner_error"]),
                    status=503,
                    code="persistent_adb_owner_not_configured",
                )
            if before.get("active") and requested_strategy is not None:
                raise ControlSurfaceRequestError(
                    "Completely stop automation before starting with a selected "
                    "strategy",
                    status=409,
                )
            requested_gate_policy = "operator"
            try:
                if before.get("active"):
                    disposition = "no_op"
                else:
                    manager.set_startup_gate_policy(requested_gate_policy)
                    if requested_strategy is not None:
                        manager.set_strategy(requested_strategy)
                        self.control_store.set_strategy(
                            requested_strategy,
                            apply_mode="next_boundary",
                            source="control-surface-process-start",
                        )
                    with self._control_mutation_lock:
                        self.control_store.set_state_and_interrupt_operator_workflows(
                            "PAUSED",
                            "a new automation process boundary started",
                            source="control-surface-process-start",
                        )
                    if self.adb_connection_manager is not None:
                        self.adb_connection_manager.ensure_target(
                            before.get("adb_target"),
                            force=True,
                        )
                    manager.start()
            except (
                AutomationProcessError,
                ControlDirectiveError,
                ValueError,
            ) as exc:
                after = manager.status()
                if not after.get("active"):
                    try:
                        with self._control_mutation_lock:
                            self.control_store.set_state(
                                "STOPPED",
                                source="control-surface-start-failure",
                            )
                    except ControlDirectiveError:
                        pass
                self._append_audit(f"Failed to start service: {exc}")
                raise ControlSurfaceRequestError(str(exc), status=503) from exc
            audit = (
                "Automation service is already live"
                if disposition == "no_op"
                else "Started automation service Paused for explicit battle intent"
            )
            if requested_strategy is not None:
                audit += f" using selected strategy {requested_strategy}"
        elif action == "stop":
            before = manager.status()
            try:
                if not before.get("active"):
                    disposition = "no_op"
                else:
                    # Persist intent before systemd signals the process so any live
                    # loop that observes the transition stops dispatching actions.
                    with self._control_mutation_lock:
                        self.control_store.set_state_and_interrupt_operator_workflows(
                            "STOPPED",
                            "automation process stopped",
                            source="control-surface-process-stop",
                        )
                        stopped = manager.stop()
                    if self.adb_connection_manager is not None:
                        self.adb_connection_manager.ensure_target(
                            stopped.get("adb_target"),
                            force=True,
                        )
            except (AutomationProcessError, ControlDirectiveError) as exc:
                self._append_audit(f"Failed to stop service cleanly: {exc}")
                raise ControlSurfaceRequestError(str(exc), status=503) from exc
            audit = (
                "Automation service is already stopped"
                if disposition == "no_op"
                else "Stopped automation service"
            )
        elif action == "restart_attached":
            raise ControlSurfaceRequestError(
                "Attached restart is no longer an implicit workflow; Stop and "
                "Start Automation, then use explicit Attach to Battle",
                status=409,
                code="explicit_attach_required",
            )
        elif action in {"set_adb_port", "set_strategy"}:
            runtime_active = self._runtime_evidence()["active"]
            manager_status = manager.status()
            process_active = bool(runtime_active or manager_status.get("active"))
            if action == "set_adb_port":
                adb_port = request.get("adb_port")
                if isinstance(adb_port, bool) or not isinstance(adb_port, int):
                    raise ControlSurfaceRequestError("adb_port must be an integer")
                if not 1 <= adb_port <= 65535:
                    raise ControlSurfaceRequestError(
                        "adb_port must be between 1 and 65535"
                    )
                try:
                    if process_active:
                        live_status = self.status()
                        state_ack = live_status["acknowledgements"].get("state")
                        if (
                            live_status["control"].get("state") != "PAUSED"
                            or live_status["control"].get("resume_at") is not None
                            or not state_ack
                            or not state_ack.get("acknowledges_current")
                        ):
                            raise ControlSurfaceRequestError(
                                "Indefinitely pause automation and wait for the "
                                "runtime to acknowledge PAUSED before changing "
                                "its live ADB port",
                                status=409,
                            )
                        manager.persist_adb_port(adb_port)
                    else:
                        manager.set_adb_port(adb_port)
                    if self.adb_connection_manager is not None:
                        self.adb_connection_manager.ensure_target(
                            f"localhost:{adb_port}",
                            force=True,
                        )
                    self.control_store.set_adb_port(
                        adb_port,
                        source="control-surface-adb-handoff",
                    )
                except ControlSurfaceRequestError:
                    raise
                except (AutomationProcessError, ControlDirectiveError) as exc:
                    self._append_audit(f"Failed to configure ADB port: {exc}")
                    raise ControlSurfaceRequestError(str(exc), status=409) from exc
                audit = (
                    f"Requested paused live ADB target handoff to localhost:{adb_port}"
                    if process_active
                    else f"Configured automation ADB target localhost:{adb_port}"
                )
            else:
                strategy = request.get("strategy")
                if not isinstance(strategy, str) or not strategy.strip():
                    raise ControlSurfaceRequestError(
                        "strategy must be a non-empty string"
                    )
                strategy = strategy.strip().lower()
                if not self.profile_store.has_strategy(strategy):
                    raise ControlSurfaceRequestError(
                        "Strategy must be one of: "
                        + ", ".join(self.profile_store.strategy_ids())
                    )
                apply_to_active_run = request.get("apply_to_active_run", False)
                if not isinstance(apply_to_active_run, bool):
                    raise ControlSurfaceRequestError(
                        "apply_to_active_run must be a boolean"
                    )
                if apply_to_active_run and not process_active:
                    raise ControlSurfaceRequestError(
                        "apply_to_active_run requires an active automation runtime",
                        status=409,
                    )
                if apply_to_active_run:
                    current = self.status()
                    availability = (
                        current.get("control_model", {})
                        .get("actions", {})
                        .get("manage_active_battle", {})
                    )
                    if availability.get("available") is not True:
                        raise ControlSurfaceRequestError(
                            str(
                                availability.get("reason")
                                or "Manage this battle is unavailable"
                            ),
                            status=409,
                            code=str(
                                availability.get("code")
                                or "active_battle_unavailable"
                            ),
                        )
                apply_mode = (
                    "active_battle" if apply_to_active_run else "next_boundary"
                )
                try:
                    if process_active:
                        manager.persist_strategy(strategy)
                    else:
                        manager.set_strategy(strategy)
                    self.control_store.set_strategy(
                        strategy,
                        apply_mode=apply_mode,
                        source="control-surface-strategy",
                    )
                except (
                    AutomationProcessError,
                    ControlDirectiveError,
                    ValueError,
                ) as exc:
                    self._append_audit(f"Failed to configure strategy: {exc}")
                    raise ControlSurfaceRequestError(str(exc), status=409) from exc
                if apply_to_active_run:
                    audit = f"Requested strategy {strategy} for the active battle"
                elif process_active:
                    audit = f"Queued strategy {strategy} for the next run boundary"
                else:
                    audit = f"Configured next-start strategy {strategy}"
        else:
            raise ControlSurfaceRequestError(
                "action must be start, stop, set_adb_port, or set_strategy"
            )

        audit_warning = self._append_audit(audit)
        response = self.status()
        response["request"] = {"accepted": True, "action": action}
        if action in {"start", "stop"} or disposition == "no_op":
            response["request"]["disposition"] = disposition
        if action == "start":
            response["request"]["action_authority"] = (
                response.get("control_model", {})
                .get("action_authority", {})
                .get("effective", "unknown")
                if disposition == "no_op"
                else "paused"
            )
        if action == "start" and requested_strategy is not None:
            response["request"]["strategy"] = requested_strategy
        elif action == "restart_attached":
            response["request"].update(restart)
        elif action == "set_adb_port":
            response["request"]["adb_port"] = adb_port
        elif action == "set_strategy":
            response["request"]["strategy"] = strategy
            if apply_to_active_run:
                response["request"]["disposition"] = "active_battle_requested"
            else:
                response["request"]["disposition"] = (
                    "queued" if process_active else "saved"
                )
        if audit_warning:
            response["request"]["warning"] = audit_warning
        return response

    def _restart_attached_automation(
        self,
        manager: SystemdAutomationManager,
    ) -> dict[str, Any]:
        """Replace the managed runtime while attaching to its active battle."""

        before = self.status()
        process = before.get("process_service") or {}
        previous_pid = process.get("main_pid")
        if not process.get("available") or not process.get("active"):
            raise ControlSurfaceRequestError(
                "Attached restart requires an active managed automation service",
                status=409,
            )
        if not isinstance(previous_pid, int) or previous_pid <= 0:
            raise ControlSurfaceRequestError(
                "Attached restart requires an authoritative systemd MainPID",
                status=409,
            )

        runtime = before.get("runtime") or {}
        matching_owner = next(
            (
                instance
                for instance in runtime.get("instances", [])
                if instance.get("active") and instance.get("pid") == previous_pid
            ),
            None,
        )
        if matching_owner is None:
            raise ControlSurfaceRequestError(
                "Attached restart requires the active ADB lock owner to match "
                "the systemd MainPID",
                status=409,
            )

        control = before.get("control") or {}
        original_state = str(control.get("state") or "").upper()
        if original_state not in {"RUNNING", "PAUSED"}:
            raise ControlSurfaceRequestError(
                "Attached restart requires control state RUNNING or PAUSED",
                status=409,
            )
        original_resume_at = control.get("resume_at")
        original_policy = str(process.get("startup_gate_policy") or "").lower()
        if process.get("startup_gate_policy_error"):
            raise ControlSurfaceRequestError(
                str(process["startup_gate_policy_error"]),
                status=409,
            )
        if original_policy not in STARTUP_GATE_POLICIES:
            raise ControlSurfaceRequestError(
                "Attached restart cannot determine the configured startup-gate policy",
                status=409,
            )

        try:
            previous_observation_id = str(
                (
                    (before.get("control_model") or {}).get("observation")
                    or {}
                ).get("observation_id")
                or ""
            )
            self.control_store.set_state(
                "PAUSED",
                source="control-surface-attached-restart",
            )
            self._wait_for_attached_restart_pause(
                previous_pid=previous_pid,
                previous_observation_id=previous_observation_id,
            )

            stopped = manager.stop()
            if self.adb_connection_manager is not None:
                self.adb_connection_manager.ensure_target(
                    stopped.get("adb_target"),
                    force=True,
                )
            manager.set_startup_gate_policy("next_run")
            try:
                started = manager.start()
            except AutomationProcessError as exc:
                restore_error = self._restore_startup_gate_policy(
                    manager,
                    original_policy,
                )
                detail = (
                    "; additionally failed restoring startup gates: "
                    f"{restore_error}"
                    if restore_error
                    else ""
                )
                raise AutomationProcessError(f"{exc}{detail}") from exc

            replacement_pid = started.get("main_pid")
            restore_error = self._restore_startup_gate_policy(
                manager,
                original_policy,
            )
            if restore_error:
                raise AutomationProcessError(
                    "Replacement started paused, but the configured startup-gate "
                    f"policy could not be restored: {restore_error}"
                )
            if (
                not isinstance(replacement_pid, int)
                or replacement_pid <= 0
                or replacement_pid == previous_pid
            ):
                raise AutomationProcessError(
                    "systemd did not report a distinct replacement MainPID"
                )

            self._wait_for_replacement_runtime(
                replacement_pid=replacement_pid,
            )

            restored_state = original_state
            if original_state == "PAUSED" and original_resume_at is not None:
                if float(original_resume_at) > time.time():
                    self.control_store.set_state(
                        "PAUSED",
                        resume_at=float(original_resume_at),
                        source="control-surface-attached-restart",
                    )
                else:
                    restored_state = "RUNNING"
                    self.control_store.set_state(
                        "RUNNING",
                        source="control-surface-attached-restart",
                    )
                    self._wait_for_state_acknowledgement("RUNNING")
            elif original_state == "RUNNING":
                self.control_store.set_state(
                    "RUNNING",
                    source="control-surface-attached-restart",
                )
                self._wait_for_state_acknowledgement("RUNNING")

            return {
                "disposition": "active_battle_reloaded",
                "previous_pid": previous_pid,
                "replacement_pid": replacement_pid,
                "restored_state": restored_state,
                "startup_gate_policy": "next_run",
            }
        except (AutomationProcessError, ControlDirectiveError, ValueError) as exc:
            try:
                self.control_store.set_state(
                    "PAUSED",
                    source="control-surface-attached-restart-failure",
                )
            except (ControlDirectiveError, ValueError):
                pass
            self._append_audit(f"Attached automation restart failed: {exc}")
            raise ControlSurfaceRequestError(str(exc), status=503) from exc

    @staticmethod
    def _restore_startup_gate_policy(
        manager: SystemdAutomationManager,
        policy: str,
    ) -> Optional[str]:
        try:
            if manager.status().get("active"):
                manager.persist_startup_gate_policy(policy)
            else:
                manager.set_startup_gate_policy(policy)
        except AutomationProcessError as exc:
            return str(exc)
        return None

    def _wait_for_state_acknowledgement(self, expected: str) -> dict[str, Any]:
        deadline = time.monotonic() + ATTACHED_RESTART_TIMEOUT_SECONDS
        while True:
            status = self.status()
            acknowledgement = status.get("acknowledgements", {}).get("state") or {}
            if (
                acknowledgement.get("acknowledges_current") is True
                and acknowledgement.get("value") == expected
            ):
                return status
            if time.monotonic() >= deadline:
                raise AutomationProcessError(
                    f"Timed out waiting for runtime to acknowledge {expected}"
                )
            time.sleep(ATTACHED_RESTART_POLL_SECONDS)

    def _wait_for_attached_restart_pause(
        self,
        *,
        previous_pid: int,
        previous_observation_id: str = "",
    ) -> dict[str, Any]:
        """Require an exact Pause receipt and a later runtime observation."""

        deadline = time.monotonic() + ATTACHED_RESTART_TIMEOUT_SECONDS
        last_missing: list[str] = []
        while True:
            status = self.status()
            process = status.get("process_service") or {}
            runtime = status.get("runtime") or {}
            acknowledgement = (
                (status.get("acknowledgements") or {}).get("state") or {}
            )
            observation = (
                (status.get("control_model") or {}).get("observation") or {}
            )
            pause_consumed = bool(
                acknowledgement.get("acknowledges_current") is True
                and acknowledgement.get("value") == "PAUSED"
            )
            observation_id = str(observation.get("observation_id") or "")
            fresh_status = bool(
                observation.get("available") is True
                and observation_id
                and (
                    not previous_observation_id
                    or observation_id != previous_observation_id
                )
            )
            matching_owner = any(
                instance.get("active") and instance.get("pid") == previous_pid
                for instance in runtime.get("instances", [])
            )
            last_missing = []
            if not (
                process.get("active") and process.get("main_pid") == previous_pid
            ):
                last_missing.append("original MainPID")
            if not matching_owner:
                last_missing.append("original ADB lock")
            if not pause_consumed:
                last_missing.append("PAUSED control acknowledgement")
            if not fresh_status:
                last_missing.append("post-request status observation")
            elif observation.get("primary_state") != "RUNNING":
                raise AutomationProcessError(
                    "Attached restart paused safely but the fresh runtime "
                    "observation was "
                    f"{observation.get('primary_state') or 'UNKNOWN'}, "
                    "not RUNNING"
                )
            if not last_missing:
                return status
            if time.monotonic() >= deadline:
                raise AutomationProcessError(
                    "Attached restart remained paused but readiness verification "
                    "timed out waiting for: " + ", ".join(last_missing)
                )
            time.sleep(ATTACHED_RESTART_POLL_SECONDS)

    def _wait_for_replacement_runtime(
        self,
        *,
        replacement_pid: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + ATTACHED_RESTART_TIMEOUT_SECONDS
        last_missing: list[str] = []
        while True:
            status = self.status()
            process = status.get("process_service") or {}
            runtime = status.get("runtime") or {}
            acknowledgement = (
                (status.get("acknowledgements") or {}).get("state") or {}
            )
            control_model = status.get("control_model") or {}
            observation = control_model.get("observation") or {}
            matching_owner = any(
                instance.get("active") and instance.get("pid") == replacement_pid
                for instance in runtime.get("instances", [])
            )
            policy_loaded = (
                control_model.get("startup_gate_policy") == "next_run"
            )
            pause_consumed = bool(
                acknowledgement.get("acknowledges_current") is True
                and acknowledgement.get("value") == "PAUSED"
            )
            first_status = bool(
                observation.get("available") is True
                and observation.get("observation_id")
            )
            last_missing = []
            if not (
                process.get("active")
                and process.get("main_pid") == replacement_pid
            ):
                last_missing.append("replacement MainPID")
            if not matching_owner:
                last_missing.append("replacement ADB lock")
            if not policy_loaded:
                last_missing.append("next_run startup policy acknowledgement")
            if not pause_consumed:
                last_missing.append("PAUSED control acknowledgement")
            if not first_status:
                last_missing.append("first status observation")
            if not last_missing:
                return status
            if time.monotonic() >= deadline:
                raise AutomationProcessError(
                    "Replacement remained paused but readiness verification timed "
                    "out waiting for: " + ", ".join(last_missing)
                )
            time.sleep(ATTACHED_RESTART_POLL_SECONDS)

    def battles(self, *, limit: int = 25) -> dict[str, Any]:
        """Return newest completed-battle summaries without OCR source bulk."""

        requested_limit = max(1, min(int(limit), 100))
        with self._battle_mutation_lock:
            purged = self._purge_expired_discarded_battles()
            paths = list(self.battles_dir.glob("Battle*.json"))
            paths.extend(self.tournaments_dir.glob("Tournament*.json"))
            items: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            excluded_nonrepresentative = 0
            for path in paths:
                try:
                    record = self._load_completed_battle_path(path)
                    if not included_in_default_history(record):
                        excluded_nonrepresentative += 1
                        continue
                    items.append(_battle_summary(record))
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    errors.append({"file": path.name, "error": str(exc)})
        items.sort(key=_battle_sort_key, reverse=True)
        return {
            "items": items[:requested_limit],
            "total": len(paths) - excluded_nonrepresentative,
            "source_total": len(paths),
            "excluded_nonrepresentative": excluded_nonrepresentative,
            "errors": errors,
            "discarded_purged": purged,
        }

    def battle(self, battle_id: str) -> dict[str, Any]:
        """Return one full battle record after strict identifier validation."""

        if not _BATTLE_ID_RE.fullmatch(str(battle_id)):
            raise ControlSurfaceRequestError("Invalid battle id", status=404)
        with self._battle_mutation_lock:
            path = self._completed_battle_directory(battle_id) / f"{battle_id}.json"
            if not path.is_file():
                raise ControlSurfaceRequestError("Battle not found", status=404)
            try:
                record = self._load_completed_battle_path(path)
                classification = classification_for_record(record)
                record.setdefault("battle_type", classification["type"])
                record.setdefault("battle_type_analysis", classification)
                return record
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ControlSurfaceRequestError(
                    f"Battle record is unreadable: {exc}", status=500
                ) from exc

    def discard_battle(
        self,
        battle_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Move one exact completed-record pair into expiring quarantine."""

        if not _BATTLE_ID_RE.fullmatch(str(battle_id)):
            raise ControlSurfaceRequestError("Invalid battle id", status=404)
        discarded_at = _utc_datetime(now)
        purge_after = discarded_at + timedelta(
            days=self.discarded_battle_retention_days
        )
        with self._battle_mutation_lock:
            self._purge_expired_discarded_battles(now=discarded_at)
            source_directory = self._completed_battle_directory(battle_id)
            json_path = source_directory / f"{battle_id}.json"
            markdown_path = source_directory / f"{battle_id}.md"
            if not json_path.is_file():
                raise ControlSurfaceRequestError("Battle not found", status=404)
            try:
                self._load_completed_battle_path(json_path)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ControlSurfaceRequestError(
                    f"Battle record is unreadable: {exc}",
                    status=500,
                ) from exc

            package_name = (
                discarded_at.strftime("%Y%m%dT%H%M%S%fZ")
                + "__"
                + str(battle_id)
            )
            package = self.discarded_battles_dir / package_name
            try:
                package.mkdir(parents=True, exist_ok=False)
            except OSError as exc:
                raise ControlSurfaceRequestError(
                    f"Unable to create discard quarantine: {exc}",
                    status=500,
                ) from exc

            moved: list[tuple[Path, Path]] = []
            try:
                for source in (json_path, markdown_path):
                    if not source.is_file():
                        continue
                    destination = package / source.name
                    os.replace(source, destination)
                    moved.append((source, destination))
                metadata = {
                    "battle_id": str(battle_id),
                    "discarded_at": discarded_at.isoformat(),
                    "purge_after": purge_after.isoformat(),
                    "source_directory": self._display_path(source_directory),
                    "files": [destination.name for _, destination in moved],
                }
                (package / "discard.json").write_text(
                    json.dumps(metadata, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                for source, destination in reversed(moved):
                    try:
                        if destination.exists() and not source.exists():
                            os.replace(destination, source)
                    except OSError:
                        pass
                try:
                    package.rmdir()
                except OSError:
                    pass
                raise ControlSurfaceRequestError(
                    f"Unable to quarantine battle record: {exc}",
                    status=500,
                ) from exc

            return {
                "battle_id": str(battle_id),
                "discarded_at": discarded_at.isoformat(),
                "purge_after": purge_after.isoformat(),
                "quarantine_path": self._display_path(package),
                "files": [destination.name for _, destination in moved],
            }

    def purge_expired_discarded_battles(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> int:
        """Permanently delete quarantine packages whose deadline has passed."""

        with self._battle_mutation_lock:
            return self._purge_expired_discarded_battles(
                now=_utc_datetime(now),
            )

    def _purge_expired_discarded_battles(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> int:
        current_time = _utc_datetime(now)
        root = self.discarded_battles_dir
        if not root.is_dir() or root.is_symlink():
            return 0
        resolved_root = root.resolve()
        purged = 0
        for package in root.iterdir():
            if package.is_symlink() or not package.is_dir():
                continue
            try:
                if package.resolve().parent != resolved_root:
                    continue
                metadata = json.loads(
                    (package / "discard.json").read_text(encoding="utf-8")
                )
                battle_id = str(metadata.get("battle_id") or "")
                if (
                    not _BATTLE_ID_RE.fullmatch(battle_id)
                    or not package.name.endswith(f"__{battle_id}")
                ):
                    continue
                purge_after = _utc_datetime(
                    datetime.fromisoformat(str(metadata["purge_after"]))
                )
                if purge_after > current_time:
                    continue
                shutil.rmtree(package)
                purged += 1
            except (
                KeyError,
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                # Malformed or partially moved packages fail closed for review.
                continue
        return purged

    def _completed_battle_directory(self, battle_id: str) -> Path:
        return (
            self.tournaments_dir
            if str(battle_id).startswith("Tournament")
            else self.battles_dir
        )

    def activity(
        self,
        *,
        limit: int = 80,
        levels: Optional[Sequence[str]] = None,
        scope: str = "all",
        after: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return recent structured log lines for diagnostics."""

        requested_limit = max(1, min(int(limit), 250))
        line_records, source_file_id, source_end_offset = (
            _tail_line_records_with_source(
                self.action_log,
                max_bytes=262_144,
            )
        )
        parsed_records = [
            (entry, offset)
            for line, offset in line_records
            if (entry := _parse_log_line(line)) is not None
        ]
        parsed_records = _attach_operation_ids(parsed_records)

        normalized_scope = str(scope or "all").strip().lower()
        if normalized_scope not in {"all", "current_run"}:
            raise ControlSurfaceRequestError(
                f"Invalid activity scope: {scope!r}"
            )
        scope_metadata = (
            self._load_activity_scope()
            if normalized_scope == "current_run"
            else None
        )
        if scope_metadata is not None:
            scope_source = scope_metadata["source_file_id"]
            if source_file_id is not None and scope_source == source_file_id:
                start_offset = int(scope_metadata["start_offset"])
                parsed_records = [
                    (entry, offset)
                    for entry, offset in parsed_records
                    if offset >= start_offset
                ]
            else:
                started_at = _parse_timestamp(scope_metadata["started_at"])
                if started_at is not None:
                    started_at = started_at.replace(microsecond=0)
                    parsed_records = [
                        (entry, offset)
                        for entry, offset in parsed_records
                        if (
                            (entry_time := _parse_timestamp(entry["timestamp"]))
                            is not None
                            and entry_time >= started_at
                        )
                    ]

        if after:
            cursor_match = _ACTIVITY_CURSOR_RE.fullmatch(str(after).strip())
            if cursor_match is None:
                raise ControlSurfaceRequestError(
                    f"Invalid activity cursor: {after!r}"
                )
            if cursor_match.group("source") == source_file_id:
                after_offset = int(cursor_match.group("offset"))
                parsed_records = [
                    (entry, offset)
                    for entry, offset in parsed_records
                    if offset >= after_offset
                ]

        parsed = [entry for entry, _ in parsed_records]
        available_levels = sorted({entry["level"] for entry in parsed})
        selected_levels: set[str] = set()
        for level in levels or ():
            normalized = str(level).strip().upper()
            if not _LOG_LEVEL_RE.fullmatch(normalized):
                raise ControlSurfaceRequestError(
                    f"Invalid activity level: {level!r}"
                )
            selected_levels.add(normalized)
        if selected_levels:
            parsed = [
                entry for entry in parsed if entry["level"] in selected_levels
            ]
        if selected_levels == _OPERATIONAL_ACTIVITY_LEVELS:
            parsed = _collapse_completed_operations(parsed)
        return {
            "items": parsed[-requested_limit:],
            "available_levels": available_levels,
            "source_file_id": source_file_id,
            "end_cursor": (
                f"{source_file_id}@{source_end_offset}"
                if source_file_id is not None
                else None
            ),
            "scope": normalized_scope,
            "scope_available": (
                normalized_scope != "current_run"
                or scope_metadata is not None
            ),
            "scope_id": (
                scope_metadata["run_id"]
                if scope_metadata is not None
                else None
            ),
            "scope_started_at": (
                scope_metadata["started_at"]
                if scope_metadata is not None
                else None
            ),
        }

    def _load_activity_scope(self) -> Optional[dict[str, Any]]:
        try:
            with self.activity_scope_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        if payload.get("schema_version") != 1:
            return None
        if payload.get("scope") != "current_run":
            return None
        run_id = str(payload.get("run_id") or "").strip()
        started_at = str(payload.get("started_at") or "").strip()
        source_file_id = str(payload.get("source_file_id") or "").strip()
        try:
            start_offset = int(payload.get("start_offset"))
        except (TypeError, ValueError):
            return None
        if (
            not run_id
            or _parse_timestamp(started_at) is None
            or not re.fullmatch(r"\d+:\d+", source_file_id)
            or start_offset < 0
        ):
            return None
        return {
            "run_id": run_id,
            "started_at": started_at,
            "source_file_id": source_file_id,
            "start_offset": start_offset,
        }

    def _current_battle_perks(
        self,
        current_run: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Load the runtime-owned, scope-bound Perk presentation checkpoint."""

        if current_run is None:
            return _unavailable_current_battle_perks(
                status="unavailable",
                reason="current_run_unavailable",
            )
        try:
            if self.perk_timeline_path.stat().st_size > (
                MAX_PERK_TIMELINE_STATUS_BYTES
            ):
                return _unavailable_current_battle_perks(
                    status="unavailable",
                    reason="timeline_checkpoint_too_large",
                )
            with self.perk_timeline_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return _unavailable_current_battle_perks(
                status="awaiting_save_checkpoint",
                reason="timeline_checkpoint_unavailable",
            )
        except (OSError, json.JSONDecodeError):
            return _unavailable_current_battle_perks(
                status="unavailable",
                reason="timeline_checkpoint_invalid",
            )

        if not isinstance(payload, Mapping):
            return _unavailable_current_battle_perks(
                status="unavailable",
                reason="timeline_checkpoint_invalid",
            )
        if (
            payload.get("schema_version") != 3
            or str(payload.get("activity_scope_run_id") or "").strip()
            != str(current_run.get("run_id") or "").strip()
        ):
            return _unavailable_current_battle_perks(
                status="awaiting_save_checkpoint",
                reason="current_run_checkpoint_unavailable",
            )
        try:
            return _normalize_current_battle_perks(payload.get("current_perks"))
        except (TypeError, ValueError):
            return _unavailable_current_battle_perks(
                status="unavailable",
                reason="current_perks_projection_invalid",
            )

    def _resolve_path(self, path: Path | str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        return candidate.resolve()

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repository_root))
        except ValueError:
            return str(path)

    def _load_completed_battle_path(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        allowed_directories = {
            self.battles_dir.resolve(),
            self.tournaments_dir.resolve(),
        }
        if resolved.parent not in allowed_directories:
            raise ValueError("Battle path leaves the configured records directory")
        with resolved.open("r", encoding="utf-8") as handle:
            record = json.load(handle)
        if not isinstance(record, dict):
            raise ValueError("Battle record must be a JSON object")
        record_id = record.get("battle_id") or record.get("tournament_id")
        if str(record_id) != resolved.stem:
            raise ValueError("Battle id does not match its filename")
        return record

    def _status_observations(
        self,
        lines: Sequence[str],
        *,
        now: float,
    ) -> list[dict[str, Any]]:
        details_by_timestamp: dict[str, re.Match[str]] = {}
        for line in lines:
            entry = _parse_log_line(line)
            if (
                not entry
                or entry["level"] != "DEBUG"
                or not entry["message"].startswith(_STATUS_DETAIL_PREFIX)
            ):
                continue
            detail_match = _STATUS_RE.fullmatch(
                entry["message"][len(_STATUS_DETAIL_PREFIX) :]
            )
            if detail_match:
                details_by_timestamp[entry["timestamp"]] = detail_match

        observations: list[dict[str, Any]] = []
        for line in reversed(lines):
            entry = _parse_log_line(line)
            if not entry or entry["level"] != "STATUS":
                continue
            match = _STATUS_RE.fullmatch(entry["message"])
            detail_match = match
            if match is None:
                match = _STATUS_SUMMARY_RE.fullmatch(entry["message"])
                detail_match = details_by_timestamp.get(entry["timestamp"])
            if match is None:
                continue
            observed_at = _parse_timestamp(entry["timestamp"])
            age_seconds = None
            if observed_at is not None:
                age_seconds = max(0, round(now - observed_at.timestamp()))
            wave_text = match.group("wave").strip()
            state_label = match.group("state").strip()
            speed_text = (match.group("speed") or "").strip()
            speed_match = re.fullmatch(
                r"x(?P<value>(?:[0-9]|1[0-9]|20)(?:\.\d+)?)",
                speed_text,
                re.IGNORECASE,
            )
            observations.append(
                {
                    "state": state_label.split("/", 1)[0],
                    "paused": state_label.endswith("/PAUSED"),
                    "state_label": state_label,
                    "wave": int(wave_text) if wave_text.isdigit() else None,
                    "coins_per_minute": _none_if_dash(match.group("coins")),
                    "game_speed": (
                        float(speed_match.group("value"))
                        if speed_match is not None
                        else None
                    ),
                    "menu": _none_if_dash(detail_match.group("menu"))
                    if detail_match
                    else None,
                    "secondary": _split_status_list(
                        detail_match.group("secondary")
                    )
                    if detail_match
                    else [],
                    "overlays": _split_status_list(
                        detail_match.group("overlays")
                    )
                    if detail_match
                    else [],
                    "observed_at": observed_at.isoformat(timespec="seconds")
                    if observed_at
                    else entry["timestamp"],
                    "age_seconds": age_seconds,
                    "stale": age_seconds is None
                    or age_seconds > self.stale_after_seconds,
                }
            )
        return observations

    def _latest_acknowledgements(
        self,
        runtime_authority: Mapping[str, Any],
        control: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project only fresh, exact runtime-owned directive receipts."""

        published = runtime_authority.get("acknowledgements")
        if not isinstance(published, Mapping):
            published = {}

        def receipt(
            field: str,
            expected_value: object,
            expected_request_id: object,
        ) -> Optional[dict[str, Any]]:
            candidate = published.get(field)
            if not isinstance(candidate, Mapping):
                return None
            value = str(candidate.get("value") or "").strip()
            request_id = str(candidate.get("request_id") or "").strip()
            acknowledged_at = _parse_timestamp(
                candidate.get("acknowledged_at")
            )
            if (
                not value
                or not _CONTROL_REQUEST_ID_RE.fullmatch(request_id)
                or acknowledged_at is None
            ):
                return None
            requested_id = str(expected_request_id or "").strip()
            return {
                "value": value,
                "at": acknowledged_at.isoformat(timespec="seconds"),
                "request_id": request_id,
                "acknowledges_current": bool(
                    requested_id
                    and request_id == requested_id
                    and value == expected_value
                ),
            }

        target = control.get("game_speed_target")
        expected_speed = (
            f"x{target:.1f}"
            if isinstance(target, (int, float))
            and not isinstance(target, bool)
            else None
        )
        expected_port = control.get("adb_port")
        expected_adb_target = (
            f"localhost:{expected_port}"
            if isinstance(expected_port, int)
            and not isinstance(expected_port, bool)
            else None
        )
        return {
            "state": receipt(
                "state",
                control.get("state"),
                control.get("state_request_id"),
            ),
            "mode": receipt(
                "mode",
                control.get("mode"),
                control.get("mode_request_id"),
            ),
            "game_speed_target": receipt(
                "game_speed_target",
                expected_speed,
                control.get("game_speed_target_request_id"),
            ),
            "adb_target": receipt(
                "adb_target",
                expected_adb_target,
                control.get("adb_port_request_id"),
            ),
            "strategy": receipt(
                "strategy",
                control.get("strategy"),
                control.get("strategy_request_id"),
            ),
        }

    def _strategy_action_gate_status(
        self,
        *,
        now: float,
        runtime: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Read the fresh runtime-owned gate channel without log inference."""

        def unavailable(reason: str, *, error: Optional[str] = None) -> dict[str, Any]:
            response: dict[str, Any] = {
                "schema_version": 1,
                "available": False,
                "active": False,
                "stale": True,
                "age_seconds": None,
                "observed_at": None,
                "runtime_active": False,
                "owner": None,
                "owner_matches_active_runtime": False,
                "owner_matches_exact_runtime": False,
                "gate_id": None,
                "strategy": None,
                "battle_scope": None,
                "source": None,
                "phase": None,
                "failed_check_ids": [],
                "reason": reason,
                "activated_at": None,
                "updated_at": None,
                "global_pause": False,
                "runtime_stopped": False,
                "active_battle": False,
                "runtime_battle_scope": None,
                "primary_state": "UNKNOWN",
                "holds": [],
                "observation_authority": {
                    "action_class": "observation",
                    "allowed": True,
                    "reason": "observation authority is independent of input",
                    "collector": None,
                    "owner": None,
                },
                "auxiliary_collection_authority": {
                    "action_class": "auxiliary_collection",
                    "allowed": False,
                    "reason": reason,
                    "collector": None,
                    "owner": None,
                },
                "allowed_auxiliary_collectors": [],
                "strategy_action_authority": {
                    "action_class": "strategy_action",
                    "allowed": False,
                    "reason": reason,
                    "collector": None,
                    "owner": None,
                },
                "lifecycle_action_authority": {
                    "action_class": "lifecycle_action",
                    "allowed": False,
                    "reason": reason,
                    "collector": None,
                    "owner": None,
                },
                "auxiliary_route": None,
                "interactive_development_lease": None,
                "control_model": None,
                "acknowledgements": None,
                "path": self._display_path(self.strategy_action_gate_path),
            }
            if error:
                response["error"] = error
            return response

        try:
            raw = self.strategy_action_gate_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return unavailable("no runtime-owned Strategy Gate snapshot exists")
        except OSError as exc:
            return unavailable(
                "the runtime-owned Strategy Gate snapshot could not be read",
                error=str(exc),
            )
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            return unavailable(
                "the runtime-owned Strategy Gate snapshot is malformed",
                error=str(exc),
            )
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            return unavailable(
                "the runtime-owned Strategy Gate snapshot has an unsupported schema"
            )
        owner = payload.get("owner")
        if not isinstance(owner, Mapping):
            return unavailable(
                "the runtime-owned Strategy Gate snapshot has no valid owner"
            )
        try:
            owner_pid = int(owner.get("pid"))
        except (TypeError, ValueError):
            return unavailable(
                "the runtime-owned Strategy Gate snapshot has no valid owner PID"
            )
        owner_target = str(owner.get("adb_target") or "").strip()
        owner_runtime_id = str(owner.get("runtime_id") or "").strip()
        raw_target_generation = owner.get("target_generation")
        owner_target_generation = (
            raw_target_generation
            if isinstance(raw_target_generation, int)
            and not isinstance(raw_target_generation, bool)
            and raw_target_generation >= 1
            else None
        )
        exact_owner_declared = bool(
            owner_runtime_id and owner_target_generation is not None
        )
        observed_at = str(payload.get("observed_at") or "").strip()
        try:
            observed_timestamp = datetime.fromisoformat(observed_at).timestamp()
        except ValueError:
            return unavailable(
                "the runtime-owned Strategy Gate snapshot has no valid timestamp"
            )
        age_seconds = max(0, int(float(now) - observed_timestamp))
        try:
            publisher_stale_after = max(
                1,
                int(payload.get("stale_after_seconds") or 0),
            )
        except (TypeError, ValueError):
            publisher_stale_after = self.stale_after_seconds
        stale_after = min(self.stale_after_seconds, publisher_stale_after)
        instances = runtime.get("instances")
        owner_matches = any(
            isinstance(instance, Mapping)
            and instance.get("active") is True
            and instance.get("pid") == owner_pid
            and (
                not owner_target
                or owner_target == "unknown"
                or str(instance.get("target") or "") == owner_target
            )
            and (
                not exact_owner_declared
                or (
                    str(instance.get("runtime_id") or "")
                    == owner_runtime_id
                    and instance.get("target_generation")
                    == owner_target_generation
                )
            )
            for instance in (instances if isinstance(instances, list) else [])
        )
        owner_matches_exact_runtime = bool(
            exact_owner_declared and owner_matches
        )
        runtime_active = payload.get("runtime_active") is True
        stale = bool(
            age_seconds > stale_after
            or not runtime_active
            or not owner_matches
        )

        def authority_field(name: str, action_class: str) -> dict[str, Any]:
            candidate = payload.get(name)
            if not isinstance(candidate, Mapping):
                return {
                    "action_class": action_class,
                    "allowed": False,
                    "reason": "authority field is missing",
                    "collector": None,
                    "owner": None,
                }
            return {
                "action_class": str(
                    candidate.get("action_class") or action_class
                ),
                "allowed": candidate.get("allowed") is True,
                "reason": str(candidate.get("reason") or ""),
                "collector": candidate.get("collector"),
                "owner": candidate.get("owner"),
            }

        failed_checks = payload.get("failed_check_ids")
        allowed_collectors = payload.get("allowed_auxiliary_collectors")
        holds = payload.get("holds")
        interactive_development_lease = (
            self._runtime_interactive_development_acknowledgement(
                payload.get("interactive_development_lease")
            )
        )
        runtime_control_model = payload.get("control_model")
        if not (
            isinstance(runtime_control_model, Mapping)
            and runtime_control_model.get("schema_version") == 1
            and (
                runtime_control_model.get("observation") is None
                or validate_observation(
                    runtime_control_model.get("observation")
                )
                is not None
            )
        ):
            runtime_control_model = None
        elif runtime_control_model.get("observation") is not None:
            normalized_observation = validate_observation(
                runtime_control_model.get("observation")
            )
            if (
                exact_owner_declared
                and isinstance(normalized_observation, Mapping)
                and normalized_observation.get("target_generation")
                != owner_target_generation
            ):
                normalized_observation = None
            runtime_control_model = {
                **dict(runtime_control_model),
                "observation": normalized_observation,
            }
        runtime_acknowledgements = payload.get("acknowledgements")
        if not (
            not stale
            and owner_matches_exact_runtime
            and isinstance(runtime_acknowledgements, Mapping)
            and runtime_acknowledgements.get("schema_version") == 1
        ):
            runtime_acknowledgements = None
        return {
            "schema_version": 1,
            "available": True,
            "active": payload.get("active") is True,
            "stale": stale,
            "age_seconds": age_seconds,
            "observed_at": observed_at,
            "stale_after_seconds": stale_after,
            "runtime_active": runtime_active,
            "owner": {
                "runtime_id": owner_runtime_id,
                "pid": owner_pid,
                "adb_target": owner_target or "unknown",
                "target_generation": owner_target_generation,
            },
            "owner_matches_active_runtime": owner_matches,
            "owner_matches_exact_runtime": owner_matches_exact_runtime,
            "gate_id": payload.get("gate_id"),
            "strategy": payload.get("strategy"),
            "battle_scope": payload.get("battle_scope"),
            "source": payload.get("source"),
            "phase": payload.get("phase"),
            "failed_check_ids": [
                str(value)
                for value in (
                    failed_checks if isinstance(failed_checks, list) else []
                )
                if str(value).strip()
            ],
            "reason": str(payload.get("reason") or ""),
            "activated_at": payload.get("activated_at"),
            "updated_at": payload.get("updated_at"),
            "global_pause": payload.get("global_pause") is True,
            "runtime_stopped": payload.get("runtime_stopped") is True,
            "active_battle": payload.get("active_battle") is True,
            "runtime_battle_scope": payload.get("runtime_battle_scope"),
            "primary_state": str(payload.get("primary_state") or "UNKNOWN"),
            "holds": holds if isinstance(holds, list) else [],
            "observation_authority": authority_field(
                "observation_authority",
                "observation",
            ),
            "auxiliary_collection_authority": authority_field(
                "auxiliary_collection_authority",
                "auxiliary_collection",
            ),
            "allowed_auxiliary_collectors": [
                str(value)
                for value in (
                    allowed_collectors
                    if isinstance(allowed_collectors, list)
                    else []
                )
                if str(value).strip()
            ],
            "strategy_action_authority": authority_field(
                "strategy_action_authority",
                "strategy_action",
            ),
            "lifecycle_action_authority": authority_field(
                "lifecycle_action_authority",
                "lifecycle_action",
            ),
            "auxiliary_route": (
                payload.get("auxiliary_route")
                if isinstance(payload.get("auxiliary_route"), Mapping)
                else None
            ),
            "interactive_development_lease": interactive_development_lease,
            "control_model": (
                dict(runtime_control_model)
                if isinstance(runtime_control_model, Mapping)
                else None
            ),
            "acknowledgements": (
                dict(runtime_acknowledgements)
                if isinstance(runtime_acknowledgements, Mapping)
                else None
            ),
            "path": self._display_path(self.strategy_action_gate_path),
        }

    @staticmethod
    def _runtime_interactive_development_acknowledgement(
        value: object,
    ) -> Optional[dict[str, Any]]:
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            return None
        lease_id = str(value.get("lease_id") or "").strip().lower()
        state = str(value.get("state") or "").strip().lower()
        owner_label = " ".join(str(value.get("owner_label") or "").split())
        runtime = value.get("runtime")
        if (
            len(lease_id) != 32
            or any(character not in "0123456789abcdef" for character in lease_id)
            or state
            not in {
                "pending",
                "active",
                "release_pending",
                "release_blocked",
                "expiry_pending",
                "termination_blocked",
                "terminal",
            }
            or not owner_label
            or not isinstance(runtime, Mapping)
        ):
            return None
        try:
            runtime_pid = int(runtime.get("pid"))
        except (TypeError, ValueError):
            return None
        runtime_owner = {
            "runtime_id": str(runtime.get("runtime_id") or ""),
            "pid": runtime_pid,
            "adb_target": str(runtime.get("adb_target") or ""),
        }
        if (
            not runtime_owner["runtime_id"]
            or runtime_owner["pid"] <= 0
            or not runtime_owner["adb_target"]
            or runtime_owner["adb_target"] == "unknown"
        ):
            return None
        response: dict[str, Any] = {
            "schema_version": 1,
            "lease_id": lease_id,
            "owner_label": owner_label[:96],
            "state": state,
            "runtime": runtime_owner,
        }
        for name in (
            "requested_at",
            "heartbeat_at",
            "expires_at",
            "hold_installed_at",
            "acknowledged_at",
            "activated_at",
            "release_requested_at",
            "updated_at",
            "terminal_at",
            "terminal_disposition",
            "terminal_reason",
            "reason",
        ):
            if value.get(name) is not None:
                response[name] = str(value[name])[:256]
        for name in ("starting_evidence", "terminal_evidence"):
            evidence = value.get(name)
            if isinstance(evidence, Mapping):
                response[name] = {
                    "screen_state": str(
                        evidence.get("screen_state") or "UNKNOWN"
                    ).upper()[:64],
                    "battle_active": evidence.get("battle_active") is True,
                    "battle_scope": (
                        str(evidence.get("battle_scope"))[:128]
                        if evidence.get("battle_scope") is not None
                        else None
                    ),
                    "observed_at": str(evidence.get("observed_at") or "")[:64],
                }
        if state == "active" and not response.get("acknowledged_at"):
            return None
        if state == "terminal" and not response.get("terminal_at"):
            return None
        return response

    def _interactive_development_lease_status(
        self,
        *,
        control: Mapping[str, Any],
        runtime_authority: Mapping[str, Any],
        now: float,
    ) -> dict[str, Any]:
        """Keep the cooperative request distinct from runtime acknowledgement."""

        request = control.get("interactive_development_lease")
        request = dict(request) if isinstance(request, Mapping) else None
        acknowledgement = runtime_authority.get(
            "interactive_development_lease"
        )
        acknowledgement = (
            dict(acknowledgement)
            if isinstance(acknowledgement, Mapping)
            else None
        )
        request_expired = False
        if request is not None:
            try:
                request_expired = now >= datetime.fromisoformat(
                    str(request.get("expires_at") or "")
                ).timestamp()
            except ValueError:
                request_expired = True
        fresh_acknowledgement = bool(
            acknowledgement is not None
            and runtime_authority.get("available") is True
            and runtime_authority.get("stale") is False
            and runtime_authority.get("owner_matches_active_runtime") is True
        )
        owner = runtime_authority.get("owner")
        owner_matches_request = bool(
            request is not None
            and isinstance(owner, Mapping)
            and request.get("runtime")
            == {
                "runtime_id": str(owner.get("runtime_id") or ""),
                "pid": owner.get("pid"),
                "adb_target": str(owner.get("adb_target") or ""),
            }
        )
        acknowledgement_matches_request = bool(
            request is not None
            and acknowledgement is not None
            and acknowledgement.get("lease_id") == request.get("lease_id")
            and acknowledgement.get("runtime") == request.get("runtime")
        )
        holds = runtime_authority.get("holds")
        external_hold_installed = any(
            isinstance(item, Mapping)
            and item.get("hold") == "external_development"
            for item in (holds if isinstance(holds, list) else [])
        )
        suppressive_authority = bool(
            runtime_authority.get("observation_authority", {}).get("allowed")
            is True
            and runtime_authority.get(
                "auxiliary_collection_authority", {}
            ).get("allowed")
            is False
            and runtime_authority.get("strategy_action_authority", {}).get(
                "allowed"
            )
            is False
            and runtime_authority.get("lifecycle_action_authority", {}).get(
                "allowed"
            )
            is False
            and not runtime_authority.get("allowed_auxiliary_collectors")
        )
        active = bool(
            request is not None
            and request.get("request_state") == "requested"
            and not request_expired
            and control.get("state") == "RUNNING"
            and fresh_acknowledgement
            and owner_matches_request
            and acknowledgement_matches_request
            and acknowledgement.get("state") == "active"
            and external_hold_installed
            and suppressive_authority
        )
        if control.get("interactive_development_lease_error"):
            reason = str(control["interactive_development_lease_error"])
        elif request is None:
            reason = "no interactive development lease is requested"
        elif request.get("request_state") == "terminal":
            reason = str(
                request.get("terminal_reason") or "the lease is terminal"
            )
        elif control.get("state") != "RUNNING":
            reason = "operator Pause or Stop takes precedence"
        elif request_expired:
            reason = "the heartbeat deadline has expired"
        elif not fresh_acknowledgement:
            reason = "fresh runtime acknowledgement is unavailable"
        elif not owner_matches_request or not acknowledgement_matches_request:
            reason = "runtime acknowledgement ownership does not match the request"
        elif acknowledgement.get("state") != "active":
            reason = str(
                acknowledgement.get("reason")
                or f"runtime acknowledgement is {acknowledgement.get('state')}"
            )
        elif not external_hold_installed or not suppressive_authority:
            reason = "runtime acknowledgement does not prove the suppressive hold"
        else:
            reason = "the matching production runtime acknowledged the lease"
        return {
            "schema_version": 1,
            "request": request,
            "runtime_acknowledgement": acknowledgement,
            "request_expired": request_expired,
            "acknowledgement_fresh": fresh_acknowledgement,
            "owner_matches_request": owner_matches_request,
            "external_hold_installed": external_hold_installed,
            "active": active,
            "reason": reason,
        }

    def _better_control_model_status(
        self,
        *,
        control: Mapping[str, Any],
        acknowledgements: Mapping[str, Any],
        runtime: Mapping[str, Any],
        process_service: Optional[Mapping[str, Any]],
        runtime_authority: Mapping[str, Any],
        now: float,
    ) -> dict[str, Any]:
        """Project the five independent operator-control dimensions."""

        process = (
            process_service if isinstance(process_service, Mapping) else {}
        )
        process_live = bool(runtime.get("active") or process.get("active"))
        process_available = bool(
            runtime.get("active")
            or not process
            or process.get("available") is True
        )
        if process_live:
            process_state = "live"
        elif process_available:
            process_state = "stopped"
        else:
            process_state = "unavailable"

        state_request = str(control.get("state") or "UNKNOWN").upper()
        state_ack = acknowledgements.get("state")
        state_ack_current = bool(
            isinstance(state_ack, Mapping)
            and state_ack.get("acknowledges_current") is True
        )
        if not process_live:
            effective_authority = "unavailable"
        elif runtime_authority.get("stale") is True:
            effective_authority = "unknown"
        elif runtime_authority.get("global_pause") is True:
            effective_authority = "paused"
        elif state_request == "RUNNING" and state_ack_current:
            effective_authority = "enabled"
        elif state_request == "STOPPED" and state_ack_current:
            effective_authority = "stopped"
        else:
            effective_authority = "pending"

        control_error = str(control.get("error") or "")
        evidence = workflow_evidence_from_authority(runtime_authority)
        runtime_observation = None
        runtime_lifecycle: Mapping[str, Any] = {}
        runtime_strategy_scope: Mapping[str, Any] = {}
        runtime_catastrophic_pause_hold: Mapping[str, Any] = {}
        runtime_startup_gate_policy: Optional[str] = None
        runtime_model = runtime_authority.get("control_model")
        if isinstance(runtime_model, Mapping):
            startup_gate_policy = str(
                runtime_model.get("startup_gate_policy") or ""
            ).strip().lower()
            if startup_gate_policy in STARTUP_GATE_POLICIES:
                runtime_startup_gate_policy = startup_gate_policy
            runtime_observation = validate_observation(
                runtime_model.get("observation")
            )
            raw_lifecycle = runtime_model.get("battle_lifecycle")
            if isinstance(raw_lifecycle, Mapping):
                runtime_lifecycle = raw_lifecycle
            raw_strategy_scope = runtime_model.get("strategy_scope")
            if isinstance(raw_strategy_scope, Mapping):
                runtime_strategy_scope = raw_strategy_scope
            raw_catastrophic_hold = runtime_model.get(
                "catastrophic_pause_hold"
            )
            if isinstance(raw_catastrophic_hold, Mapping):
                runtime_catastrophic_pause_hold = raw_catastrophic_hold
        observation_age_seconds: Optional[int] = None
        if runtime_observation is not None:
            observed_at = _parse_timestamp(runtime_observation.get("observed_at"))
            if observed_at is None:
                evidence = None
            else:
                observation_age_seconds = max(
                    0,
                    int(float(now) - observed_at.timestamp()),
                )
                if observation_age_seconds > self.stale_after_seconds:
                    evidence = None
        if evidence is not None:
            observation = {
                **runtime_observation,
                "freshness": "fresh",
                "available": True,
                "reason": "fresh exact runtime-owned observation",
                "age_seconds": observation_age_seconds,
            }
        else:
            nested_observation_stale = bool(
                runtime_observation is not None
                and observation_age_seconds is not None
                and observation_age_seconds > self.stale_after_seconds
            )
            observation = {
                **(runtime_observation or {}),
                "game_state": (
                    (runtime_observation or {}).get("game_state") or "unknown"
                ),
                "freshness": (
                    "stale"
                    if runtime_authority.get("available") is True
                    else "unavailable"
                ),
                "available": False,
                "age_seconds": observation_age_seconds,
                "reason": (
                    "runtime observation is stale even though the authority "
                    "heartbeat is current"
                    if nested_observation_stale
                    else str(
                        runtime_authority.get("reason")
                        or "fresh exact runtime observation is unavailable"
                    )
                ),
            }

        workflow = validate_battle_workflow(control.get("battle_workflow"))
        manual = validate_manual_control(control.get("manual_control"))
        setup_capture = validate_setup_capture(control.get("setup_capture"))
        workflow_error = str(control.get("battle_workflow_error") or "")
        manual_error = str(control.get("manual_control_error") or "")
        setup_capture_error = str(
            control.get("setup_capture_error") or ""
        )
        workflow_busy = bool(
            workflow is not None
            and workflow.get("status") not in BATTLE_WORKFLOW_TERMINAL_STATUSES
        )
        manual_busy = bool(
            manual is not None
            and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
        )
        setup_capture_busy = bool(
            setup_capture is not None
            and setup_capture.get("status")
            not in SETUP_CAPTURE_TERMINAL_STATUSES
        )
        setup_capture_input_busy = bool(
            setup_capture is not None
            and setup_capture.get("status")
            in {"requested", "acknowledged", "capturing"}
        )
        emulator_maintenance = normalize_emulator_maintenance(
            control.get("emulator_maintenance")
        )
        maintenance_busy = bool(
            emulator_maintenance is not None
            and emulator_maintenance.get("state") != "terminal"
        )

        def action_availability(
            action_key: str,
            label: str,
            allowed_states: set[str],
        ) -> dict[str, Any]:
            game_state = str(observation.get("game_state") or "unknown")
            if control_error:
                return {
                    "available": False,
                    "code": "control_invalid",
                    "reason": control_error,
                }
            if maintenance_busy:
                return {
                    "available": False,
                    "code": "emulator_maintenance_active",
                    "reason": (
                        "BlueStacks recovery currently owns all game input"
                    ),
                }
            if not process_live:
                return {
                    "available": False,
                    "code": "process_stopped",
                    "reason": "Start Automation before requesting this workflow",
                }
            if evidence is None:
                return {
                    "available": False,
                    "code": "fresh_observation_unavailable",
                    "reason": "fresh runtime-owned game observation is required",
                }
            if workflow_error:
                return {
                    "available": False,
                    "code": "battle_workflow_invalid",
                    "reason": workflow_error,
                }
            if manual_error:
                return {
                    "available": False,
                    "code": "manual_control_invalid",
                    "reason": manual_error,
                }
            if setup_capture_error:
                return {
                    "available": False,
                    "code": "setup_capture_invalid",
                    "reason": setup_capture_error,
                }
            if setup_capture_input_busy:
                return {
                    "available": False,
                    "code": "setup_capture_active",
                    "reason": "save-backed setup capture currently owns device input",
                }
            if manual_busy:
                return {
                    "available": False,
                    "code": "manual_control_active",
                    "reason": "Return Control before requesting a battle workflow",
                }
            if workflow_busy:
                return {
                    "available": False,
                    "code": "workflow_busy",
                    "reason": "another battle workflow request is in progress",
                }
            if game_state not in allowed_states:
                return {
                    "available": False,
                    "code": "intent_mismatch",
                    "reason": (
                        f"{label} is unavailable for observed game state "
                        f"{game_state}"
                    ),
                }
            if action_key == "attach_battle" and (
                type(evidence.get("target_generation")) is not int
                or int(evidence["target_generation"]) < 1
                or not str(evidence.get("activity_scope_run_id") or "").strip()
            ):
                return {
                    "available": False,
                    "code": "exact_attachment_binding_unavailable",
                    "reason": (
                        "Attach requires an exact target generation and active "
                        "activity scope"
                    ),
                }
            if (
                action_key == "attach_battle"
                and runtime_lifecycle.get("active_battle_adopted") is True
            ):
                return {
                    "available": False,
                    "code": "battle_already_adopted",
                    "reason": (
                        "automation already owns this battle; use Take/Return "
                        "Control for an operator handoff"
                    ),
                }
            return {
                "available": True,
                "code": "available",
                "reason": (
                    "Attach will use the accepted selected Strategy; a battle "
                    "that is incompatible or cannot be verified will continue "
                    "observation-only and degraded"
                    if action_key == "attach_battle"
                    else "the explicit intent matches fresh observation"
                ),
            }

        take_manual_available = bool(
            not control_error
            and process_live
            and state_request != "STOPPED"
            and evidence is not None
            and not manual_busy
            and not manual_error
            and not setup_capture_error
            and not setup_capture_input_busy
        )
        take_manual = {
            "available": take_manual_available,
            "code": (
                "available"
                if take_manual_available
                else (
                    "control_invalid"
                    if control_error
                    else (
                        "process_stopping"
                        if state_request == "STOPPED"
                        else (
                            "manual_control_invalid"
                            if manual_error
                            else (
                                "manual_control_active"
                                if manual_busy
                                else (
                                    "setup_capture_active"
                                    if setup_capture_input_busy
                                    else (
                                        "fresh_observation_unavailable"
                                        if process_live
                                        else "process_stopped"
                                    )
                                )
                            )
                        )
                    )
                )
            ),
            "reason": (
                "requests an acknowledged indefinite Pause before yielding input"
                if take_manual_available
                else (
                    control_error
                    if control_error
                    else (
                        "Start Automation before requesting manual control"
                        if state_request == "STOPPED"
                        else (
                            manual_error
                            if manual_error
                            else (
                                "manual control is already in progress"
                                if manual_busy
                                else (
                                    "save-backed setup capture currently owns device input"
                                    if setup_capture_input_busy
                                    else "fresh live observation is required"
                                )
                            )
                        )
                    )
                )
            ),
        }
        indefinite_pause_acknowledged = bool(
            state_request == "PAUSED"
            and control.get("resume_at") is None
            and effective_authority == "paused"
            and (
                state_ack_current
                or (
                    isinstance(manual, Mapping)
                    and isinstance(
                        manual.get("pause_acknowledgement"), Mapping
                    )
                )
            )
        )
        return_game_state = str(observation.get("game_state") or "unknown")
        return_binding_available = bool(
            evidence is not None
            and type(evidence.get("target_generation")) is int
            and int(evidence["target_generation"]) >= 1
            and str(evidence.get("activity_scope_run_id") or "").strip()
        )
        return_boundary_available = return_game_state in {
            "home_new_battle",
            "home_resume_battle",
            "active_battle",
            "game_over",
        }
        return_control_available = bool(
            not control_error
            and not manual_error
            and manual is not None
            and manual.get("status") == "active"
            and evidence is not None
            and indefinite_pause_acknowledged
            and return_binding_available
            and return_boundary_available
        )
        return_control = {
            "available": return_control_available,
            "code": (
                "available"
                if return_control_available
                else (
                    "control_invalid"
                    if control_error
                    else (
                        "manual_control_invalid"
                        if manual_error
                        else (
                            "pause_not_acknowledged"
                            if manual is not None
                            and manual.get("status") == "active"
                            and not indefinite_pause_acknowledged
                            else (
                                "exact_return_binding_unavailable"
                                if manual is not None
                                and manual.get("status") == "active"
                                and not return_binding_available
                                else (
                                    "return_boundary_unavailable"
                                    if manual is not None
                                    and manual.get("status") == "active"
                                    and not return_boundary_available
                                    else "manual_control_not_acknowledged"
                                )
                            )
                        )
                    )
                )
            ),
            "reason": (
                "refresh observation and reconcile while Pause remains acknowledged"
                if return_control_available
                else (
                    control_error
                    if control_error
                    else (
                        manual_error
                        if manual_error
                        else (
                            "the indefinite Pause must still be acknowledged"
                            if manual is not None
                            and manual.get("status") == "active"
                            and not indefinite_pause_acknowledged
                            else (
                                "Return Control requires an exact target generation "
                                "and current activity scope"
                                if manual is not None
                                and manual.get("status") == "active"
                                and not return_binding_available
                                else (
                                    "Return Control has no save-backed reconciliation "
                                    f"path for observed game state {return_game_state}"
                                    if manual is not None
                                    and manual.get("status") == "active"
                                    and not return_boundary_available
                                    else "acknowledged active manual control and fresh observation are required"
                                )
                            )
                        )
                    )
                )
            ),
        }

        configured_strategy = str(
            control.get("strategy") or process.get("strategy") or "none"
        )
        strategy_ack = acknowledgements.get("strategy")
        strategy_ack_current = bool(
            isinstance(strategy_ack, Mapping)
            and strategy_ack.get("acknowledges_current") is True
        )
        runtime_scope_current = bool(
            runtime_authority.get("available") is True
            and runtime_authority.get("stale") is False
            and runtime_authority.get("owner_matches_active_runtime") is True
        )
        authoritative_runtime_scope = bool(
            runtime_scope_current
            and runtime_authority.get("owner_matches_exact_runtime") is True
            and "startup_default" in runtime_strategy_scope
            and "pending_next_boundary" in runtime_strategy_scope
        )
        runtime_startup_strategy = str(
            runtime_strategy_scope.get("startup_default") or ""
        ).strip().lower()
        runtime_active_strategy = str(
            runtime_strategy_scope.get("active_battle") or ""
        ).strip().lower()
        active_strategy = (
            runtime_active_strategy
            if runtime_scope_current
            and runtime_lifecycle.get("active_battle_adopted") is True
            and runtime_active_strategy
            else None
        )
        startup_strategy = (
            runtime_startup_strategy
            if authoritative_runtime_scope and runtime_startup_strategy
            else str(process.get("strategy") or configured_strategy)
        )
        if authoritative_runtime_scope:
            pending_strategy = (
                str(
                    runtime_strategy_scope.get("pending_next_boundary")
                    or ""
                ).strip().lower()
                or None
            )
            pending_active_strategy = (
                str(
                    runtime_strategy_scope.get("pending_active_battle")
                    or ""
                ).strip().lower()
                or None
            )
        else:
            pending_strategy = (
                configured_strategy
                if control.get("strategy_apply_mode") == "next_boundary"
                and (
                    not strategy_ack_current
                    or active_strategy != configured_strategy
                )
                else None
            )
            pending_active_strategy = (
                configured_strategy
                if control.get("strategy_apply_mode") == "active_battle"
                and not strategy_ack_current
                else None
            )
        mode = str(control.get("mode") or "NEXT_BATTLE").upper()
        terminal_labels = {
            "NEXT_BATTLE": "continue_automatically",
            "WAIT": "wait",
            "HOME": "return_or_stay_home",
        }
        mode_ack = acknowledgements.get("mode")
        terminal_policy_status = "selected"
        terminal_policy_reason = "the selected policy applies at a supported terminal route"
        if (
            observation.get("game_state") == "tournament_results"
        ):
            terminal_policy_status = (
                "retained" if mode == "WAIT" else "selected"
            )
            terminal_policy_reason = (
                "Tournament Results remains visible under the wait policy"
                if mode == "WAIT"
                else "saved Tournament Results will use the verified OK-to-Home route"
            )

        manual_terminal = (
            manual.get("terminal_evidence")
            if isinstance(manual, Mapping)
            and isinstance(manual.get("terminal_evidence"), Mapping)
            else None
        )
        terminal_evidence_unavailable = bool(
            manual_busy
            and isinstance(manual_terminal, Mapping)
            and manual_terminal.get("status") == "unavailable"
        )
        enable_available = bool(
            not control_error
            and process_live
            and state_request != "STOPPED"
            and not manual_error
            and not terminal_evidence_unavailable
            and (
                not manual_busy
                or manual.get("status")
                in {
                    "return_requested",
                    "awaiting_enable",
                    "reconciling",
                    "awaiting_configuration",
                    "awaiting_manual_correction",
                }
            )
        )
        if control_error:
            enable_code = "control_invalid"
            enable_reason = control_error
        elif not process_live:
            enable_code = "process_stopped"
            enable_reason = "Start Automation before enabling actions"
        elif state_request == "STOPPED":
            enable_code = "process_stopping"
            enable_reason = "Start Automation before enabling actions"
        elif manual_error:
            enable_code = "manual_control_invalid"
            enable_reason = manual_error
        elif terminal_evidence_unavailable:
            enable_code = "manual_terminal_evidence_unavailable"
            enable_reason = (
                "manual terminal evidence is ambiguous; Automation remains "
                "Paused and terminal UI input is not authorized"
            )
        elif manual_busy and manual.get("status") not in {
            "return_requested",
            "awaiting_enable",
            "reconciling",
            "awaiting_configuration",
            "awaiting_manual_correction",
        }:
            enable_code = "return_control_required"
            enable_reason = "Use Return Control before enabling automated actions"
        elif (
            isinstance(manual, Mapping)
            and manual.get("status") == "awaiting_manual_correction"
        ):
            enable_code = "available"
            enable_reason = (
                "after making the reported manual correction, explicitly "
                "Enable to request a new forced save check"
            )
        else:
            enable_code = "available"
            enable_reason = "explicitly permit guarded actions"

        pause_available = bool(
            not control_error
            and process_live
        )
        if control_error:
            pause_code = "control_invalid"
            pause_reason = control_error
        elif not process_live:
            pause_code = "process_stopped"
            pause_reason = "Start Automation before pausing action authority"
        else:
            pause_code = "available"
            pause_reason = (
                "request zero automated device input; Pause remains available "
                "during every workflow"
            )

        manage_active_battle_available = bool(
            not control_error
            and process_live
            and evidence is not None
            and observation.get("game_state") == "active_battle"
            and runtime_lifecycle.get("active_battle_adopted") is True
            and runtime_lifecycle.get("awaiting_initial_intent") is not True
            and not manual_busy
            and not workflow_busy
            and not setup_capture_input_busy
        )
        if control_error:
            manage_active_battle_code = "control_invalid"
            manage_active_battle_reason = control_error
        elif not process_live:
            manage_active_battle_code = "process_stopped"
            manage_active_battle_reason = "Start Automation first"
        elif evidence is None:
            manage_active_battle_code = "fresh_observation_unavailable"
            manage_active_battle_reason = (
                "fresh exact active-battle observation is required"
            )
        elif observation.get("game_state") != "active_battle":
            manage_active_battle_code = "intent_mismatch"
            manage_active_battle_reason = (
                "Manage this battle is available only for a verified active battle"
            )
        elif (
            runtime_lifecycle.get("active_battle_adopted") is not True
            or runtime_lifecycle.get("awaiting_initial_intent") is True
        ):
            manage_active_battle_code = "attach_required"
            manage_active_battle_reason = (
                "Attach to Battle and complete save validation before applying "
                "a Strategy"
            )
        elif manual_busy:
            manage_active_battle_code = "manual_control_active"
            manage_active_battle_reason = "Return Control first"
        elif workflow_busy:
            manage_active_battle_code = "workflow_busy"
            manage_active_battle_reason = (
                "the current battle workflow must complete first"
            )
        elif setup_capture_input_busy:
            manage_active_battle_code = "setup_capture_active"
            manage_active_battle_reason = (
                "save-backed setup capture currently owns device input"
            )
        else:
            manage_active_battle_code = "available"
            manage_active_battle_reason = (
                "explicitly adopt the selected Strategy for this battle; "
                "Surrender is not authorized"
            )

        capture_game_state = str(observation.get("game_state") or "unknown")
        capture_binding_available = bool(
            evidence is not None
            and type(evidence.get("target_generation")) is int
            and int(evidence["target_generation"]) > 0
            and str(evidence.get("activity_scope_run_id") or "").strip()
        )
        manual_save_receipt = (
            manual.get("save_receipt")
            if isinstance(manual, Mapping)
            and isinstance(manual.get("save_receipt"), Mapping)
            else None
        )
        retained_return_capture_available = bool(
            manual is not None
            and manual.get("status") == "awaiting_configuration"
            and manual.get("refresh_status") == "trusted_mismatch_paused"
            and isinstance(manual_save_receipt, Mapping)
            and manual_save_receipt.get("kind")
            == "return_control_reconciliation"
            and manual_save_receipt.get("workflow_id")
            == manual.get("manual_control_id")
            and isinstance(
                manual_save_receipt.get("configuration"), Mapping
            )
            and manual_save_receipt["configuration"].get("status")
            == "partial"
            and bool(
                manual_save_receipt["configuration"].get(
                    "unresolved_check_ids"
                )
            )
            and effective_authority == "paused"
            and capture_game_state == "active_battle"
            and capture_binding_available
        )
        capture_available = bool(
            not control_error
            and process_live
            and evidence is not None
            and (
                effective_authority == "enabled"
                or retained_return_capture_available
            )
            and capture_game_state in SETUP_CAPTURE_GAME_STATES
            and not workflow_busy
            and (not manual_busy or retained_return_capture_available)
            and not setup_capture_busy
            and not workflow_error
            and not manual_error
            and not setup_capture_error
            and capture_binding_available
        )
        if control_error:
            capture_code, capture_reason = "control_invalid", control_error
        elif not process_live:
            capture_code, capture_reason = (
                "process_stopped",
                "Start Automation before capturing current setup",
            )
        elif evidence is None:
            capture_code, capture_reason = (
                "fresh_observation_unavailable",
                "fresh exact runtime-owned observation is required",
            )
        elif workflow_error:
            capture_code, capture_reason = (
                "battle_workflow_invalid",
                workflow_error,
            )
        elif manual_error:
            capture_code, capture_reason = (
                "manual_control_invalid",
                manual_error,
            )
        elif capture_game_state not in SETUP_CAPTURE_GAME_STATES:
            capture_code, capture_reason = (
                "capture_boundary_unavailable",
                "Capture requires verified Home New, Home Resume, or active battle",
            )
        elif workflow_busy:
            capture_code, capture_reason = (
                "workflow_busy",
                "complete the current battle workflow before capturing setup",
            )
        elif manual_busy and not retained_return_capture_available:
            capture_code, capture_reason = (
                "manual_control_active",
                "Return Control before capturing current setup",
            )
        elif setup_capture_busy:
            capture_code, capture_reason = (
                "capture_review_pending",
                "save or cancel the current capture before requesting another",
            )
        elif setup_capture_error:
            capture_code, capture_reason = (
                "setup_capture_invalid",
                setup_capture_error,
            )
        elif not capture_binding_available:
            capture_code, capture_reason = (
                "exact_capture_binding_unavailable",
                "Capture requires an exact target generation and activity scope",
            )
        elif retained_return_capture_available:
            capture_code, capture_reason = (
                "available_from_return_control",
                "review the exact retained forced Return Control save without "
                "new device input; this does not resolve or resume Return Control",
            )
        elif effective_authority == "paused":
            capture_code, capture_reason = (
                "automation_paused",
                "Automation Paused blocks a new forced save refresh; cached evidence will not be used",
            )
        elif effective_authority != "enabled":
            capture_code, capture_reason = (
                "action_authority_pending",
                "Automation Enabled must be acknowledged before forced save refresh",
            )
        else:
            capture_code, capture_reason = (
                "available",
                "request a new forced save and review without applying anything",
            )

        if maintenance_busy:
            maintenance_unavailable = {
                "available": False,
                "code": "emulator_maintenance_active",
                "reason": "BlueStacks recovery currently owns all game input",
            }
            take_manual = dict(maintenance_unavailable)
            return_control = dict(maintenance_unavailable)
            manage_active_battle_available = False
            manage_active_battle_code = "emulator_maintenance_active"
            manage_active_battle_reason = maintenance_unavailable["reason"]
            capture_available = False
            capture_code = "emulator_maintenance_active"
            capture_reason = maintenance_unavailable["reason"]

        return {
            "schema_version": 1,
            "startup_gate_policy": runtime_startup_gate_policy,
            "process": {
                "state": process_state,
                "live": process_live,
                "available": process_available,
            },
            "action_authority": {
                "requested": {
                    "state": (
                        "enabled"
                        if state_request == "RUNNING"
                        else state_request.lower()
                    ),
                    "request_id": control.get("state_request_id"),
                    "requested_at": control.get("state_updated_at"),
                },
                "acknowledgement": (
                    dict(state_ack) if isinstance(state_ack, Mapping) else None
                ),
                "acknowledged": state_ack_current,
                "effective": effective_authority,
                "observation_continues_while_paused": True,
                "catastrophic_hold": bool(
                    runtime_catastrophic_pause_hold.get("active") is True
                ),
                "catastrophic_hold_reason": (
                    str(runtime_catastrophic_pause_hold.get("reason") or "")
                    or None
                ),
                "meaning": (
                    "Paused means zero automated device input; observation may continue. "
                    "Enabled permits guarded actions and does not assert that the game is RUNNING."
                ),
            },
            "observation": observation,
            "strategy_scope": {
                "startup_default": startup_strategy,
                "active_battle": active_strategy,
                "pending_next_boundary": pending_strategy,
                "pending_active_battle": pending_active_strategy,
                "request_id": control.get("strategy_request_id"),
                "observation_only": bool(
                    runtime_strategy_scope.get("observation_only") is True
                ),
                "degradation": (
                    dict(runtime_strategy_scope["degradation"])
                    if isinstance(
                        runtime_strategy_scope.get("degradation"),
                        Mapping,
                    )
                    else None
                ),
            },
            "when_battle_ends": {
                "value": terminal_labels.get(mode, "unknown"),
                "compatibility_value": mode,
                "request_id": control.get("mode_request_id"),
                "requested_at": control.get("mode_updated_at"),
                "status": terminal_policy_status,
                "reason": terminal_policy_reason,
                "acknowledgement": (
                    dict(mode_ack) if isinstance(mode_ack, Mapping) else None
                ),
                "acknowledged": bool(
                    isinstance(mode_ack, Mapping)
                    and mode_ack.get("acknowledges_current") is True
                ),
                "meaning": "future terminal policy; never an immediate battle action",
            },
            "home_behavior": {
                "meaning": (
                    "Home observation does not change automation action authority. "
                    "Starting or resuming requires a matching explicit battle "
                    "intent; the only automatic Home start is an exact, "
                    "unconsumed continuation from a completed terminal route."
                ),
                "explicit_intent_required": bool(
                    runtime_lifecycle.get("explicit_home_intent_required")
                    is True
                ),
                "terminal_continuation": (
                    dict(runtime_lifecycle["terminal_home_continuation"])
                    if isinstance(
                        runtime_lifecycle.get(
                            "terminal_home_continuation"
                        ),
                        Mapping,
                    )
                    else {"pending": False}
                ),
            },
            "battle_workflow": workflow,
            "manual_control": manual,
            "setup_capture": setup_capture,
            "workflow_evidence": evidence,
            "actions": {
                "start_battle": action_availability(
                    "start_battle", "Start Battle", {"home_new_battle"}
                ),
                "attach_battle": action_availability(
                    "attach_battle",
                    "Attach to Battle",
                    {"home_resume_battle", "active_battle"},
                ),
                "take_manual_control": take_manual,
                "return_control": return_control,
                "pause": {
                    "available": pause_available,
                    "code": pause_code,
                    "reason": pause_reason,
                },
                "enable": {
                    "available": enable_available,
                    "code": enable_code,
                    "reason": enable_reason,
                },
                "manage_active_battle": {
                    "available": manage_active_battle_available,
                    "code": manage_active_battle_code,
                    "reason": manage_active_battle_reason,
                },
                "capture_current_setup": {
                    "available": capture_available,
                    "code": capture_code,
                    "reason": capture_reason,
                },
            },
        }

    def _runtime_evidence(self) -> dict[str, Any]:
        instances: list[dict[str, Any]] = []
        for path in sorted(self.control_path.parent.glob("automation-*.lock")):
            metadata: dict[str, Any] = {}
            error = None
            try:
                with path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    metadata = loaded
                else:
                    error = "lock metadata is not a JSON object"
            except (OSError, json.JSONDecodeError) as exc:
                error = str(exc)
            held = _is_lock_held(path)
            pid = metadata.get("pid")
            alive = _pid_alive(pid)
            runtime_id = str(metadata.get("runtime_id") or "").strip()
            raw_target_generation = metadata.get("target_generation")
            target_generation = (
                raw_target_generation
                if isinstance(raw_target_generation, int)
                and not isinstance(raw_target_generation, bool)
                and raw_target_generation >= 1
                else None
            )
            item = {
                "file": path.name,
                "pid": pid if isinstance(pid, int) else None,
                "target": metadata.get("target"),
                "runtime_id": runtime_id or None,
                "target_generation": target_generation,
                "metadata_state": metadata.get("state"),
                "started_at": metadata.get("started_at"),
                "released_at": metadata.get("released_at"),
                "lock_held": held,
                "pid_alive": alive,
                "active": held is True and alive is True,
            }
            if error:
                item["error"] = error
            instances.append(item)
        instances.sort(key=lambda item: (not item["active"], item["file"]))
        return {
            "active": any(item["active"] for item in instances),
            "instances": instances,
        }

    def _append_audit(
        self,
        message: str,
        *,
        level: str = "ACTION",
    ) -> Optional[str]:
        normalized_level = str(level or "ACTION").strip().upper()
        if normalized_level not in {"ACTION", "INFO", "RESULT", "WARN"}:
            normalized_level = "ACTION"
        entry = (
            f"[{normalized_level} "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"[CONTROL_SURFACE] {message}\n"
        )
        try:
            self.action_log.parent.mkdir(parents=True, exist_ok=True)
            with self.action_log.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        except OSError as exc:
            return f"Control changed, but audit logging failed: {exc}"
        return None


def _save_mapping_integration_error_status(code: str) -> int:
    if code in {
        "mapping_candidate_record_not_found",
    }:
        return 404
    if code in {
        "git_inspection_failed",
        "integration_lock_unavailable",
        "commit_state_uncertain",
        "transaction_write_failed",
        "legacy_transaction_recovery_required",
        "decode_receipt_store_invalid",
        "decode_receipt_conflict",
        "canonical_status_unavailable",
    }:
        return 503
    return 409


def _save_mapping_integration_disposition(code: str) -> str:
    if code in {
        "commit_state_uncertain",
        "transaction_write_failed",
        "transaction_recovery_required",
        "legacy_transaction_recovery_required",
        "decode_receipt_conflict",
        "canonical_status_unavailable",
    }:
        return "unconfirmed"
    return "failed"


def _battle_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    more_stats = record.get("more_stats") or record.get("detailed_stats")
    sections = more_stats.get("sections", []) if isinstance(more_stats, Mapping) else []
    rows = {
        (str(section.get("key")), str(row.get("key"))): row
        for section in sections
        if isinstance(section, Mapping)
        for row in section.get("rows", [])
        if isinstance(row, Mapping)
    }

    def row(section: str, key: str) -> Mapping[str, Any]:
        value = rows.get((section, key), {})
        return value if isinstance(value, Mapping) else {}

    def raw(section: str, key: str) -> Any:
        value = row(section, key)
        return value.get("value_raw") if value else None

    def integer(section: str, key: str) -> Optional[int]:
        value = row(section, key)
        parsed = value.get("value") if value else None
        if isinstance(parsed, int) and not isinstance(parsed, bool):
            return parsed
        raw_value = value.get("value_raw") if value else None
        try:
            return int(str(raw_value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    quality = record.get("quality", {})
    run_configuration = record.get("run_configuration", {})
    derived = record.get("derived", {})
    summary = record.get("summary", {})
    summary_fields = summary.get("fields", {}) if isinstance(summary, Mapping) else {}
    classification = classification_for_record(record)

    def summary_raw(key: str) -> Any:
        field = summary_fields.get(key, {}) if isinstance(summary_fields, Mapping) else {}
        if not isinstance(field, Mapping):
            return None
        return field.get("raw", field.get("value"))

    def summary_integer(key: str) -> Optional[int]:
        field = summary_fields.get(key, {}) if isinstance(summary_fields, Mapping) else {}
        value = field.get("value") if isinstance(field, Mapping) else None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {
        "battle_id": record.get("battle_id") or record.get("tournament_id"),
        "captured_at": record.get("captured_at"),
        "strategy": record.get("strategy"),
        "battle_type": classification["type"],
        "battle_type_label": classification["label"],
        "battle_type_confidence": classification["confidence"],
        "profile": run_configuration.get("profile")
        if isinstance(run_configuration, Mapping)
        else None,
        "tier": (
            integer("battle_report", "tier")
            or observed_tier_for_record(record)
        ),
        "wave": integer("battle_report", "wave") or summary_integer("wave"),
        "killed_by": raw("battle_report", "killed_by") or summary_raw("killed_by"),
        "league": summary_raw("league"),
        "rank": summary_integer("rank"),
        "game_time": raw("battle_report", "game_time"),
        "real_time": raw("battle_report", "real_time"),
        "coins_earned": raw("battle_report", "coins_earned") or summary_raw("coins_earned"),
        "coins_per_hour": raw("battle_report", "coins_per_hour"),
        "cells_earned": raw("battle_report", "cells_earned"),
        "cells_per_hour": raw("battle_report", "cells_per_hour"),
        "derived": dict(derived) if isinstance(derived, Mapping) else {},
        "quality": {
            "valid": quality.get("valid") if isinstance(quality, Mapping) else None,
            "warnings": list(quality.get("warnings", []))
            if isinstance(quality, Mapping)
            else [],
        },
    }


def _battle_sort_key(item: Mapping[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(item.get("captured_at"))).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _same_host_transition(
    stored: object,
    candidate: Mapping[str, object],
    *,
    include_previous: bool,
) -> bool:
    if not isinstance(stored, Mapping):
        return False
    keys = ["host_id", "adb_port", "process_id", "process_started_at"]
    if include_previous:
        keys.extend(["previous_process_id", "previous_process_started_at"])
    return all(stored.get(key) == candidate.get(key) for key in keys)


def _utc_datetime(value: Optional[datetime]) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _tail_lines(path: Path, *, max_bytes: int) -> list[str]:
    lines, _ = _tail_lines_with_source(path, max_bytes=max_bytes)
    return lines


def _tail_lines_with_source(
    path: Path,
    *,
    max_bytes: int,
) -> tuple[list[str], Optional[str]]:
    records, source_file_id, _ = _tail_line_records_with_source(
        path,
        max_bytes=max_bytes,
    )
    return [line for line, _ in records], source_file_id


def _tail_line_records_with_source(
    path: Path,
    *,
    max_bytes: int,
) -> tuple[list[tuple[str, int]], Optional[str], int]:
    try:
        with path.open("rb") as handle:
            source = os.fstat(handle.fileno())
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start_offset = max(0, size - max_bytes)
            handle.seek(start_offset)
            data = handle.read()
    except OSError:
        return [], None, 0
    if start_offset:
        newline = data.find(b"\n")
        if newline < 0:
            return [], f"{source.st_dev}:{source.st_ino}", size
        start_offset += newline + 1
        data = data[newline + 1 :]
    records: list[tuple[str, int]] = []
    cursor = start_offset
    for raw_line in data.splitlines(keepends=True):
        records.append(
            (
                raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace"),
                cursor,
            )
        )
        cursor += len(raw_line)
    return records, f"{source.st_dev}:{source.st_ino}", size


def _attach_operation_ids(
    records: Sequence[tuple[dict[str, str], int]],
) -> list[tuple[dict[str, Any], int]]:
    """Attach paired DEBUG activity metadata to its semantic summary."""

    annotated: list[tuple[dict[str, Any], int]] = [
        (dict(entry), offset) for entry, offset in records
    ]
    for index in range(1, len(annotated)):
        detail, _detail_offset = annotated[index]
        if detail["level"] != "DEBUG":
            continue
        summary, summary_offset = annotated[index - 1]
        if (
            summary["level"] not in {"ACTION", "RESULT"}
            or summary["timestamp"] != detail["timestamp"]
        ):
            continue
        operation_match = _OPERATION_DETAIL_RE.search(detail["message"])
        if operation_match is not None:
            summary["operation_id"] = operation_match.group("operation_id")
        summary.update(
            _activity_display_metadata(
                summary["message"],
                detail["message"],
            )
        )
        annotated[index - 1] = (summary, summary_offset)
    return annotated


def _activity_display_metadata(
    summary: str,
    detail: str,
) -> dict[str, Any]:
    """Build optional compact and expanded presentation data."""

    prefix = summary.partition(" — ")[0]
    activity_match = _ACTIVITY_DATA_RE.search(detail)
    if activity_match is not None:
        try:
            payload = json.loads(activity_match.group("payload"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, Mapping) and (
            payload.get("kind") == "perk_selection_bundle"
        ):
            raw_items = payload.get("items")
            items = []
            if isinstance(raw_items, Sequence) and not isinstance(
                raw_items,
                (str, bytes),
            ):
                for raw_item in raw_items[:64]:
                    if not isinstance(raw_item, Mapping):
                        continue
                    label = " ".join(
                        str(raw_item.get("label") or "").split()
                    )[:500]
                    alias = " ".join(
                        str(raw_item.get("alias") or "").split()
                    )[:80]
                    if label:
                        items.append(
                            {
                                "alias": alias or label,
                                "label": label,
                            }
                        )
            if len(items) > 1:
                aliases = ", ".join(item["alias"] for item in items)
                return {
                    "activity_kind": "perk_selection_bundle",
                    "display_message": (
                        f"{prefix} — {len(items)} Perks: {aliases}"
                    ),
                    "detail_items": items,
                }

    count_match = _PERK_SELECTION_COUNT_RE.search(detail)
    if count_match is not None:
        count = int(count_match.group("count"))
        if count > 1:
            return {
                "activity_kind": "perk_selection_bundle",
                "display_message": f"{prefix} — {count} Perks",
            }
    return {}


def _collapse_completed_operations(
    entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Fold correlated completed pairs for the Operational audience only."""

    collapsed: list[Optional[dict[str, Any]]] = []
    pending_actions: dict[str, int] = {}
    for source in entries:
        entry = dict(source)
        operation_id = str(entry.get("operation_id") or "")
        if entry.get("level") == "ACTION" and operation_id:
            pending_actions[operation_id] = len(collapsed)
            collapsed.append(entry)
            continue
        if entry.get("level") == "RESULT" and operation_id:
            action_index = pending_actions.pop(operation_id, None)
            if action_index is not None:
                action = collapsed[action_index]
                collapsed[action_index] = None
                if action is not None:
                    entry["collapsed_action"] = action["message"]
                    entry["collapsed"] = True
        collapsed.append(entry)
    return [entry for entry in collapsed if entry is not None]


def _unavailable_current_battle_perks(
    *,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": CURRENT_BATTLE_PERKS_SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "source": "perk_timeline_checkpoint",
        "order_semantics": "most_recent_selection_first",
        "captured_at": None,
        "saved_wave": None,
        "picked_count": 0,
        "unique_count": 0,
        "items": [],
    }


def _normalize_current_battle_perks(value: object) -> dict[str, Any]:
    """Validate the additive presentation written by the runtime owner."""

    if not isinstance(value, Mapping):
        raise TypeError("current Perks presentation must be an object")
    status = str(value.get("status") or "")
    reason = str(value.get("reason") or "")
    source = str(value.get("source") or "")
    order_semantics = str(value.get("order_semantics") or "")
    if (
        value.get("schema_version") != CURRENT_BATTLE_PERKS_SCHEMA_VERSION
        or status not in {"available", "awaiting_save_checkpoint"}
        or len(reason) > 128
        or source != "monitor_validated_player_save_perk_prefix"
        or order_semantics != "most_recent_selection_first"
    ):
        raise ValueError("current Perks presentation metadata is invalid")

    raw_items = value.get("items")
    if (
        not isinstance(raw_items, Sequence)
        or isinstance(raw_items, (str, bytes, bytearray))
        or len(raw_items) > MAX_CURRENT_BATTLE_PERK_ITEMS
    ):
        raise ValueError("current Perks presentation items are invalid")
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    previous_sequence: Optional[int] = None
    previous_wave: Optional[int] = None
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ValueError("current Perk item is invalid")
        perk_key = str(raw_item.get("perk_key") or "")
        label = " ".join(str(raw_item.get("label") or "").split())
        level = raw_item.get("level")
        last_wave = raw_item.get("last_selected_wave")
        last_sequence = raw_item.get("last_selected_sequence")
        if (
            re.fullmatch(r"[a-z][a-z0-9_]{0,95}", perk_key) is None
            or perk_key in seen_keys
            or not label
            or len(label) > 160
            or type(level) is not int
            or level < 1
            or type(last_wave) is not int
            or last_wave < 0
            or type(last_sequence) is not int
            or last_sequence < 1
            or (
                previous_sequence is not None
                and last_sequence >= previous_sequence
            )
            or (previous_wave is not None and last_wave > previous_wave)
        ):
            raise ValueError("current Perk item is invalid")
        seen_keys.add(perk_key)
        previous_sequence = last_sequence
        previous_wave = last_wave
        items.append(
            {
                "perk_key": perk_key,
                "label": label,
                "level": level,
                "last_selected_wave": last_wave,
                "last_selected_sequence": last_sequence,
            }
        )

    captured_at = value.get("captured_at")
    saved_wave = value.get("saved_wave")
    picked_count = value.get("picked_count")
    unique_count = value.get("unique_count")
    if status == "awaiting_save_checkpoint":
        if (
            reason != "save_checkpoint_unavailable"
            or captured_at is not None
            or saved_wave is not None
            or type(picked_count) is not int
            or picked_count != 0
            or type(unique_count) is not int
            or unique_count != 0
            or items
        ):
            raise ValueError("awaiting current Perks presentation is invalid")
    else:
        if not isinstance(captured_at, str):
            raise ValueError("current Perks capture timestamp is invalid")
        try:
            parsed_capture = datetime.fromisoformat(
                captured_at.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "current Perks capture timestamp is invalid"
            ) from exc
        if (
            parsed_capture.tzinfo is None
            or reason
            or type(saved_wave) is not int
            or saved_wave < 0
            or type(picked_count) is not int
            or picked_count < 0
            or type(unique_count) is not int
            or unique_count != len(items)
            or picked_count < unique_count
            or sum(item["level"] for item in items) != picked_count
            or (
                bool(items)
                and items[0]["last_selected_sequence"] != picked_count
            )
            or any(item["level"] > picked_count for item in items)
            or any(
                item["last_selected_sequence"] > picked_count
                or item["last_selected_wave"] > saved_wave
                for item in items
            )
        ):
            raise ValueError("available current Perks presentation is invalid")

    return {
        "schema_version": CURRENT_BATTLE_PERKS_SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "source": source,
        "order_semantics": order_semantics,
        "captured_at": captured_at,
        "saved_wave": saved_wave,
        "picked_count": picked_count,
        "unique_count": unique_count,
        "items": items,
    }


def _parse_log_line(line: str) -> Optional[dict[str, str]]:
    match = _LOG_RE.fullmatch(line.strip())
    if not match:
        return None
    return match.groupdict()


def _parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _none_if_dash(value: str) -> Optional[str]:
    normalized = value.strip()
    return None if normalized in {"", "—", "-"} else normalized


def _split_status_list(value: str) -> list[str]:
    normalized = value.strip()
    if normalized in {"", "—", "-"}:
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _is_lock_held(path: Path) -> Optional[bool]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
    except OSError:
        return None


def _pid_alive(pid: object) -> Optional[bool]:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


__all__ = [
    "CONTROL_SURFACE_CAPABILITIES",
    "CONTROL_SURFACE_REVISION",
    "ControlSurfaceRequestError",
    "ControlSurfaceService",
    "DEFAULT_DISCARDED_BATTLE_RETENTION_DAYS",
    "DEFAULT_STALE_AFTER_SECONDS",
    "MAX_PAUSE_MINUTES",
]
