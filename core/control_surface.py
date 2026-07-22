"""Read-only runtime views and guarded control mutations for a remote GUI."""

from __future__ import annotations

from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any, Mapping, Optional, Sequence

from core.app_setup import CONFIGURABLE_STRATEGIES, STARTUP_GATE_POLICIES
from core.automation_process import AutomationProcessError, SystemdAutomationManager
from core.battle_classification import classification_for_record
from core.control_directives import ControlDirectiveError, ControlDirectiveStore
from core.gate_decisions import startup_gate_context_for_strategy


MAX_PAUSE_MINUTES = 7 * 24 * 60
DEFAULT_STALE_AFTER_SECONDS = 180
# Advance this when a newer Windows client must reload the resident service,
# and advance that client's MinimumServerRevision in the same change.
CONTROL_SURFACE_REVISION = 2
CONTROL_SURFACE_CAPABILITIES = (
    "active_battle_strategy_adoption",
    "explicit_strategy_disposition",
)
_BATTLE_ID_RE = re.compile(r"(?:Battle|Tournament)\d{8}T\d{6}[+-]\d{4}")
_LOG_RE = re.compile(
    r"^\[(?P<level>[A-Z_]+) (?P<timestamp>[^\]]+)] (?P<message>.*)$"
)
_LOG_LEVEL_RE = re.compile(r"[A-Z_]+")
_STATUS_RE = re.compile(
    r"^State=(?P<state>[^|]+?)\s*\|\s*"
    r"Wave=(?P<wave>[^|]+?)\s*\|\s*"
    r"Coins/min=(?P<coins>[^|]+?)\s*\|\s*"
    r"Menu=(?P<menu>[^|]+?)\s*\|\s*"
    r"Secondary=\[(?P<secondary>.*?)]\s*\|\s*"
    r"Overlays=\[(?P<overlays>.*?)]\s*$"
)
_STATUS_SUMMARY_RE = re.compile(
    r"^State=(?P<state>[^|]+?)\s*\|\s*"
    r"Wave=(?P<wave>[^|]+?)\s*\|\s*"
    r"Coins/min=(?P<coins>[^|]+?)\s*$"
)
_STATUS_DETAIL_PREFIX = "[STATUS_DETAIL] "
_STATE_ACK_RE = re.compile(
    r"^\[CTRL] State set to (?P<value>RUNNING|PAUSED|STOPPED) via control file$"
)
_MODE_ACK_RE = re.compile(
    r"^\[CTRL] Mode set to (?P<value>RETRY|WAIT|HOME) via control file$"
)
_ADB_TARGET_ACK_RE = re.compile(
    r"^\[CTRL] ADB target set to (?P<value>localhost:(?:[1-9]\d{0,4})) via control file$"
)
_STRATEGY_ACK_RE = re.compile(
    r"^\[CTRL] Strategy set to (?P<value>"
    + "|".join(re.escape(value) for value in CONFIGURABLE_STRATEGIES)
    + r") via control file$"
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
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        process_manager: Optional[SystemdAutomationManager] = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.control_path = self._resolve_path(control_file)
        self.action_log = self._resolve_path(action_log)
        self.battles_dir = self._resolve_path(battles_dir)
        self.tournaments_dir = self._resolve_path(tournaments_dir)
        self.stale_after_seconds = max(1, int(stale_after_seconds))
        self.control_store = ControlDirectiveStore(self.control_path)
        self.process_manager = process_manager
        self._process_action_lock = threading.Lock()

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
                "adb_port": None,
                "resume_at": None,
                "remaining_seconds": None,
                "updated_at": None,
                "adb_port_updated_at": None,
                "strategy": None,
                "strategy_apply_mode": "next_boundary",
                "strategy_updated_at": None,
                "strategy_request_id": None,
                "gate_decision": None,
                "startup_gate_waivers": {},
                "exists": self.control_path.exists(),
            }
        control["path"] = self._display_path(self.control_path)
        if control_error:
            control["error"] = control_error

        lines = _tail_lines(self.action_log, max_bytes=262_144)
        observation = self._latest_observation(lines, now=current_time)
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
            "runtime": runtime,
            "process_service": process_service,
        }

    def apply_control(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one allowlisted control-file mutation and return fresh status."""

        if not isinstance(request, Mapping):
            raise ControlSurfaceRequestError("Request body must be a JSON object")
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
                    "action must be pause, resume, stop, mode, resolve_gate, "
                    "or configure_run"
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
            before = manager.status()
            requested_gate_policy = request.get("startup_gate_policy")
            if requested_gate_policy is not None:
                if not isinstance(requested_gate_policy, str):
                    raise ControlSurfaceRequestError(
                        "startup_gate_policy must be immediate or next_run"
                    )
                requested_gate_policy = requested_gate_policy.strip().lower()
                if requested_gate_policy not in STARTUP_GATE_POLICIES:
                    raise ControlSurfaceRequestError(
                        "startup_gate_policy must be immediate or next_run"
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
                    if requested_gate_policy is not None:
                        manager.set_startup_gate_policy(requested_gate_policy)
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
            except (AutomationProcessError, ControlDirectiveError) as exc:
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
            gate_description = (
                requested_gate_policy
                or before.get("startup_gate_policy")
                or "immediate"
            )
            audit = (
                f"Started automation service with state {requested_state} "
                f"and startup gates {gate_description}"
            )
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
                        strategy.strip().lower(),
                        apply_mode=apply_mode,
                        source="control-surface-strategy",
                    )
                except (AutomationProcessError, ControlDirectiveError) as exc:
                    self._append_audit(f"Failed to configure strategy: {exc}")
                    raise ControlSurfaceRequestError(str(exc), status=409) from exc
                strategy = strategy.strip().lower()
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
        if action == "start" and requested_gate_policy is not None:
            response["request"]["startup_gate_policy"] = requested_gate_policy
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

    def battles(self, *, limit: int = 25) -> dict[str, Any]:
        """Return newest completed-battle summaries without OCR source bulk."""

        requested_limit = max(1, min(int(limit), 100))
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
        }

    def battle(self, battle_id: str) -> dict[str, Any]:
        """Return one full battle record after strict identifier validation."""

        if not _BATTLE_ID_RE.fullmatch(str(battle_id)):
            raise ControlSurfaceRequestError("Invalid battle id", status=404)
        directory = (
            self.tournaments_dir
            if str(battle_id).startswith("Tournament")
            else self.battles_dir
        )
        path = directory / f"{battle_id}.json"
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

    def activity(
        self,
        *,
        limit: int = 80,
        levels: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """Return recent structured log lines for diagnostics."""

        requested_limit = max(1, min(int(limit), 250))
        parsed = [
            entry
            for line in _tail_lines(self.action_log, max_bytes=262_144)
            if (entry := _parse_log_line(line)) is not None
        ]
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
        return {
            "items": parsed[-requested_limit:],
            "available_levels": available_levels,
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

    def _latest_observation(
        self,
        lines: Sequence[str],
        *,
        now: float,
    ) -> Optional[dict[str, Any]]:
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
            return {
                "state": state_label.split("/", 1)[0],
                "paused": state_label.endswith("/PAUSED"),
                "state_label": state_label,
                "wave": int(wave_text) if wave_text.isdigit() else None,
                "coins_per_minute": _none_if_dash(match.group("coins")),
                "menu": _none_if_dash(detail_match.group("menu"))
                if detail_match
                else None,
                "secondary": _split_status_list(detail_match.group("secondary"))
                if detail_match
                else [],
                "overlays": _split_status_list(detail_match.group("overlays"))
                if detail_match
                else [],
                "observed_at": observed_at.isoformat(timespec="seconds")
                if observed_at
                else entry["timestamp"],
                "age_seconds": age_seconds,
                "stale": age_seconds is None
                or age_seconds > self.stale_after_seconds,
            }
        return None

    def _latest_acknowledgements(
        self,
        lines: Sequence[str],
        control: Mapping[str, Any],
    ) -> dict[str, Any]:
        state_ack = None
        mode_ack = None
        adb_target_ack = None
        strategy_ack = None
        state_updated_at = control.get("state_updated_at")
        mode_updated_at = control.get("mode_updated_at")
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
                and (control.get("adb_port") is None or adb_target_ack is not None)
                and (control.get("strategy") is None or strategy_ack is not None)
            ):
                break
        return {
            "state": state_ack,
            "mode": mode_ack,
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
                "started_at": metadata.get("started_at"),
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
        "tier": integer("battle_report", "tier"),
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


def _tail_lines(path: Path, *, max_bytes: int) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


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
    "DEFAULT_STALE_AFTER_SECONDS",
    "MAX_PAUSE_MINUTES",
]
