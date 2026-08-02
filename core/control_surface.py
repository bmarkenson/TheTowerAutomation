"""Read-only runtime views and guarded control mutations for a remote GUI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Mapping, Optional, Sequence

from core.app_setup import (
    DEFAULT_STARTUP_GATE_POLICY,
    STARTUP_GATE_POLICIES,
)
from core.automation_process import AutomationProcessError, SystemdAutomationManager
from core.battle_classification import (
    classification_for_record,
    observed_tier_for_record,
)
from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    MAXIMUM_GAME_SPEED_TARGET,
)
from core.gate_decisions import startup_gate_context_for_strategy
from core.exclusive_validation import (
    exclusive_validation_definition_for_strategy,
)
from core.host_performance import (
    DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS,
    HostPerformancePayloadError,
    HostPerformanceStorageError,
    HostPerformanceStore,
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
# Advance this when a newer Windows client must reload the resident service,
# and advance that client's MinimumServerRevision in the same change.
CONTROL_SURFACE_REVISION = 21
CONTROL_SURFACE_CAPABILITIES = (
    "active_battle_strategy_adoption",
    "advisory_preflight_decisions",
    "attached_automation_restart",
    "automatic_battle_attachment",
    "completed_battle_discard",
    "current_run_activity_scope",
    "exclusive_strategy_validation_status",
    "explicit_strategy_disposition",
    "game_speed_target",
    "host_performance_gpu_v1",
    "host_performance_telemetry_v1",
    "observed_game_speed",
    "selected_strategy_process_start",
    "strategy_authoring_profile_lifecycle_v1",
    "strategy_authoring_specialized_editors_v1",
    "strategy_authoring_v1",
    "strategy_profile_catalog_v1",
    "strategy_profile_editor_v2",
    "tournament_launch_confirmation",
)
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
_STATE_ACK_RE = re.compile(
    r"^\[CTRL] State set to (?P<value>RUNNING|PAUSED|STOPPED) via control file$"
)
_MODE_ACK_RE = re.compile(
    r"^\[CTRL] Mode set to (?P<value>RETRY|WAIT|HOME) via control file$"
)
_GAME_SPEED_TARGET_ACK_RE = re.compile(
    r"^\[CTRL] Game speed target set to "
    r"(?P<value>x(?:[0-5]\.[05]|6\.[03])) via control file$"
)
_ADB_TARGET_ACK_RE = re.compile(
    r"^\[CTRL] ADB target set to (?P<value>localhost:(?:[1-9]\d{0,4})) via control file$"
)
_STRATEGY_ACK_RE = re.compile(
    r"^\[CTRL] Strategy set to "
    r"(?P<value>[a-z][a-z0-9_]{2,47}) via control file$"
)


class ControlSurfaceRequestError(ValueError):
    """A rejected GUI request with an HTTP-friendly status code."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


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
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        process_manager: Optional[SystemdAutomationManager] = None,
        strategy_profile_dir: Path | str = "config/strategies/custom",
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.control_path = self._resolve_path(control_file)
        self.action_log = self._resolve_path(action_log)
        self.activity_scope_path = (
            self.action_log.with_name(DEFAULT_ACTIVITY_SCOPE_FILENAME)
            if activity_scope_file is None
            else self._resolve_path(activity_scope_file)
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
        self.profile_store = StrategyProfileStore(
            profile_directory=self.strategy_profile_dir,
        )
        self.control_store = ControlDirectiveStore(
            self.control_path,
            strategy_profile_dir=self.strategy_profile_dir,
        )
        self.process_manager = process_manager
        self._process_action_lock = threading.Lock()
        self._battle_mutation_lock = threading.RLock()

    def strategy_profiles(self) -> dict[str, Any]:
        """Return the constrained profile-editor catalog."""

        return self.profile_store.catalog()

    def strategy_authoring(self) -> dict[str, Any]:
        """Return the additive sparse Base/Strategy authoring catalog."""

        try:
            return self.profile_store.authoring_catalog()
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
                "adb_port": None,
                "resume_at": None,
                "remaining_seconds": None,
                "updated_at": None,
                "state_request_id": None,
                "adb_port_updated_at": None,
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
        acknowledgements = self._latest_acknowledgements(lines, control)
        runtime = self._runtime_evidence()
        process_service = (
            self.process_manager.status() if self.process_manager is not None else None
        )
        control["startup_gate_context"] = self._startup_gate_context(
            control,
            process_service,
        )
        healthy = bool(runtime["active"] and observation and not observation["stale"])
        current_run = self._load_activity_scope()

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
            "runtime": runtime,
            "process_service": process_service,
        }

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
            raise ControlSurfaceRequestError(str(exc)) from exc
        except HostPerformanceStorageError as exc:
            raise ControlSurfaceRequestError(
                f"Unable to persist host-performance telemetry: {exc}",
                status=503,
            ) from exc

    def apply_control(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one allowlisted control-file mutation and return fresh status."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
        with self._process_action_lock:
            return self._apply_control_locked(request)

    def _apply_control_locked(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply a control mutation outside any process replacement window."""

        action = str(request.get("action") or "").strip().lower()
        try:
            if action == "pause":
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
                self.control_store.set_state(
                    "PAUSED",
                    resume_at=resume_at,
                    source="control-surface",
                )
                audit = f"Requested PAUSED {description}"
            elif action == "resume":
                self.control_store.set_state("RUNNING", source="control-surface")
                audit = "Requested RUNNING"
            elif action == "stop":
                self.control_store.set_state("STOPPED", source="control-surface")
                audit = "Requested STOPPED"
            elif action == "mode":
                mode = str(request.get("mode") or "").strip().upper()
                self.control_store.set_mode(mode, source="control-surface")
                audit = f"Requested mode {mode}"
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
                            "Resume automation before starting Tournament",
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
                    "action must be pause, resume, stop, mode, game_speed, "
                    "resolve_gate, resolve_tournament_launch, or configure_run"
                )
        except ControlDirectiveError as exc:
            raise ControlSurfaceRequestError(str(exc), status=409) from exc
        except ValueError as exc:
            if isinstance(exc, ControlSurfaceRequestError):
                raise
            raise ControlSurfaceRequestError(str(exc)) from exc

        audit_warning = self._append_audit(audit)
        response = self.status()
        response["request"] = {"accepted": True, "action": action}
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
        if action == "start":
            requested_state = str(request.get("run_state") or "PAUSED").upper()
            if requested_state not in {"PAUSED", "RUNNING"}:
                raise ControlSurfaceRequestError(
                    "run_state must be PAUSED or RUNNING"
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
            if before.get("active") and requested_strategy is not None:
                raise ControlSurfaceRequestError(
                    "Completely stop automation before starting with a selected "
                    "strategy",
                    status=409,
                )
            requested_gate_policy = request.get(
                "startup_gate_policy",
                DEFAULT_STARTUP_GATE_POLICY,
            )
            if not isinstance(requested_gate_policy, str):
                raise ControlSurfaceRequestError(
                    "startup_gate_policy must be one of: "
                    + ", ".join(STARTUP_GATE_POLICIES)
                )
            requested_gate_policy = requested_gate_policy.strip().lower()
            if requested_gate_policy not in STARTUP_GATE_POLICIES:
                raise ControlSurfaceRequestError(
                    "startup_gate_policy must be one of: "
                    + ", ".join(STARTUP_GATE_POLICIES)
                )
            if before.get("active") and (
                before.get("startup_gate_policy") != requested_gate_policy
            ):
                raise ControlSurfaceRequestError(
                    "Completely stop automation before changing startup gates",
                    status=409,
                )
            try:
                if not before.get("active"):
                    manager.set_startup_gate_policy(requested_gate_policy)
                    if requested_strategy is not None:
                        manager.set_strategy(requested_strategy)
                        self.control_store.set_strategy(
                            requested_strategy,
                            apply_mode="next_boundary",
                            source="control-surface-process-start",
                        )
                    else:
                        effective_strategy = str(
                            before.get("strategy") or ""
                        ).strip().lower()
                        if (
                            self.profile_store.has_strategy(effective_strategy)
                            and exclusive_validation_definition_for_strategy(
                                effective_strategy
                            )
                            is not None
                        ):
                            # Pressing Start is an explicit authorization even
                            # when a fallback client reuses the saved strategy.
                            self.control_store.set_strategy(
                                effective_strategy,
                                apply_mode="next_boundary",
                                source="control-surface-process-start",
                            )
                    # A new process always crosses its startup boundary paused.
                    # RUNNING is persisted only after systemd proves it active.
                    self.control_store.set_state(
                        "PAUSED", source="control-surface-process-start"
                    )
                    manager.start()
                self.control_store.set_state(
                    requested_state,
                    source="control-surface-process-start",
                )
            except (
                AutomationProcessError,
                ControlDirectiveError,
                ValueError,
            ) as exc:
                after = manager.status()
                if not after.get("active"):
                    try:
                        self.control_store.set_state(
                            "STOPPED", source="control-surface-start-failure"
                        )
                    except ControlDirectiveError:
                        pass
                self._append_audit(f"Failed to start service: {exc}")
                raise ControlSurfaceRequestError(str(exc), status=503) from exc
            gate_description = requested_gate_policy
            audit = (
                f"Started automation service with state {requested_state} "
                f"and startup gates {gate_description}"
            )
            if requested_strategy is not None:
                audit += f" using selected strategy {requested_strategy}"
        elif action == "stop":
            try:
                # Persist intent before systemd signals the process so any live
                # loop that observes the transition stops dispatching actions.
                self.control_store.set_state(
                    "STOPPED", source="control-surface-process-stop"
                )
                manager.stop()
            except (AutomationProcessError, ControlDirectiveError) as exc:
                self._append_audit(f"Failed to stop service cleanly: {exc}")
                raise ControlSurfaceRequestError(str(exc), status=503) from exc
            audit = "Stopped automation service"
        elif action == "restart_attached":
            restart = self._restart_attached_automation(manager)
            audit = (
                "Reloaded automation for the active battle "
                f"(PID {restart['previous_pid']} -> {restart['replacement_pid']}); "
                f"restored {restart['restored_state']}"
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
                "action must be start, stop, restart_attached, set_adb_port, "
                "or set_strategy"
            )

        audit_warning = self._append_audit(audit)
        response = self.status()
        response["request"] = {"accepted": True, "action": action}
        if action == "start":
            response["request"]["startup_gate_policy"] = requested_gate_policy
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
            pause_log_offset = _file_size(self.action_log)
            self.control_store.set_state(
                "PAUSED",
                source="control-surface-attached-restart",
            )
            self._wait_for_attached_restart_pause(
                previous_pid=previous_pid,
                log_offset=pause_log_offset,
            )

            log_offset = _file_size(self.action_log)
            manager.stop()
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
                log_offset=log_offset,
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
        log_offset: int,
    ) -> dict[str, Any]:
        """Require post-request Pause acknowledgement and fresh RUNNING proof."""

        deadline = time.monotonic() + ATTACHED_RESTART_TIMEOUT_SECONDS
        last_missing: list[str] = []
        while True:
            status = self.status()
            process = status.get("process_service") or {}
            runtime = status.get("runtime") or {}
            observation = status.get("observation") or {}
            appended = _lines_from_offset(self.action_log, log_offset)
            parsed = [entry for line in appended if (entry := _parse_log_line(line))]
            pause_indices = [
                index
                for index, entry in enumerate(parsed)
                if _STATE_ACK_RE.fullmatch(entry["message"])
                and entry["message"].endswith("PAUSED via control file")
            ]
            pause_consumed = bool(pause_indices)
            fresh_status = bool(
                pause_indices
                and any(
                    index > pause_indices[-1] and entry["level"] == "STATUS"
                    for index, entry in enumerate(parsed)
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
            elif observation.get("state") != "RUNNING":
                raise AutomationProcessError(
                    "Attached restart paused safely but the fresh runtime "
                    f"observation was {observation.get('state') or 'UNKNOWN'}, "
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
        log_offset: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + ATTACHED_RESTART_TIMEOUT_SECONDS
        last_missing: list[str] = []
        while True:
            status = self.status()
            process = status.get("process_service") or {}
            runtime = status.get("runtime") or {}
            appended = _lines_from_offset(self.action_log, log_offset)
            parsed = [entry for line in appended if (entry := _parse_log_line(line))]
            matching_owner = any(
                instance.get("active") and instance.get("pid") == replacement_pid
                for instance in runtime.get("instances", [])
            )
            policy_indices = [
                index
                for index, entry in enumerate(parsed)
                if "[RUN_INIT] Startup gate policy=next_run" in entry["message"]
            ]
            pause_indices = [
                index
                for index, entry in enumerate(parsed)
                if _STATE_ACK_RE.fullmatch(entry["message"])
                and entry["message"].endswith("PAUSED via control file")
                and policy_indices
                and index > policy_indices[-1]
            ]
            policy_loaded = bool(policy_indices)
            pause_consumed = bool(pause_indices)
            first_status = bool(
                pause_indices
                and any(
                    index > pause_indices[-1] and entry["level"] == "STATUS"
                    for index, entry in enumerate(parsed)
                )
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
                last_missing.append("next_run startup log")
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
            for path in paths:
                try:
                    record = self._load_completed_battle_path(path)
                    items.append(_battle_summary(record))
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    errors.append({"file": path.name, "error": str(exc)})
        items.sort(key=_battle_sort_key, reverse=True)
        return {
            "items": items[:requested_limit],
            "total": len(paths),
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
        lines: Sequence[str],
        control: Mapping[str, Any],
    ) -> dict[str, Any]:
        state_ack = None
        mode_ack = None
        game_speed_target_ack = None
        adb_target_ack = None
        strategy_ack = None
        state_updated_at = control.get("state_updated_at")
        mode_updated_at = control.get("mode_updated_at")
        game_speed_target_updated_at = control.get(
            "game_speed_target_updated_at"
        )
        adb_port_updated_at = control.get("adb_port_updated_at")
        strategy_updated_at = control.get("strategy_updated_at")
        legacy_updated_at = (
            control.get("updated_at")
            if (
                state_updated_at is None
                and mode_updated_at is None
                and adb_port_updated_at is None
            )
            else None
        )
        for line in reversed(lines):
            entry = _parse_log_line(line)
            if not entry:
                continue
            if state_ack is None and (match := _STATE_ACK_RE.fullmatch(entry["message"])):
                state_ack = _ack_entry(
                    entry,
                    match.group("value"),
                    control.get("state"),
                    state_updated_at or legacy_updated_at,
                )
            if mode_ack is None and (match := _MODE_ACK_RE.fullmatch(entry["message"])):
                mode_ack = _ack_entry(
                    entry,
                    match.group("value"),
                    control.get("mode"),
                    mode_updated_at or legacy_updated_at,
                )
            if game_speed_target_ack is None and (
                match := _GAME_SPEED_TARGET_ACK_RE.fullmatch(entry["message"])
            ):
                target = control.get("game_speed_target")
                expected_target = (
                    f"x{target:.1f}"
                    if isinstance(target, (int, float))
                    and not isinstance(target, bool)
                    else None
                )
                game_speed_target_ack = _ack_entry(
                    entry,
                    match.group("value"),
                    expected_target,
                    game_speed_target_updated_at,
                )
            if adb_target_ack is None and (
                match := _ADB_TARGET_ACK_RE.fullmatch(entry["message"])
            ):
                expected_port = control.get("adb_port")
                expected_target = (
                    f"localhost:{expected_port}"
                    if isinstance(expected_port, int)
                    else None
                )
                adb_target_ack = _ack_entry(
                    entry,
                    match.group("value"),
                    expected_target,
                    adb_port_updated_at,
                )
            if strategy_ack is None and (
                match := _STRATEGY_ACK_RE.fullmatch(entry["message"])
            ):
                strategy_ack = _ack_entry(
                    entry,
                    match.group("value"),
                    control.get("strategy"),
                    strategy_updated_at,
                )
            if (
                state_ack is not None
                and mode_ack is not None
                and game_speed_target_ack is not None
                and (control.get("adb_port") is None or adb_target_ack is not None)
                and (control.get("strategy") is None or strategy_ack is not None)
            ):
                break
        return {
            "state": state_ack,
            "mode": mode_ack,
            "game_speed_target": game_speed_target_ack,
            "adb_target": adb_target_ack,
            "strategy": strategy_ack,
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
            item = {
                "file": path.name,
                "pid": pid if isinstance(pid, int) else None,
                "target": metadata.get("target"),
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

    def _append_audit(self, message: str) -> Optional[str]:
        entry = (
            f"[ACTION {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"[CONTROL_SURFACE] {message}\n"
        )
        try:
            self.action_log.parent.mkdir(parents=True, exist_ok=True)
            with self.action_log.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        except OSError as exc:
            return f"Control changed, but audit logging failed: {exc}"
        return None


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


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _lines_from_offset(path: Path, offset: int) -> list[str]:
    """Read complete log lines appended after a captured byte offset."""

    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(offset if 0 <= offset <= size else 0)
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


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


def _ack_entry(
    entry: Mapping[str, str],
    value: str,
    expected_value: object,
    expected_updated_at: object,
) -> dict[str, Any]:
    acknowledged_at = _parse_timestamp(entry.get("timestamp"))
    requested_at = _parse_timestamp(expected_updated_at)
    is_current = value == expected_value
    if requested_at is not None:
        is_current = bool(
            is_current
            and acknowledged_at is not None
            and acknowledged_at >= requested_at
        )
    return {
        "value": value,
        "at": acknowledged_at.isoformat(timespec="seconds")
        if acknowledged_at
        else entry.get("timestamp"),
        "acknowledges_current": is_current,
    }


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
