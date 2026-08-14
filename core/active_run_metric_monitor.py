"""Bound, monotonic active-run metrics from shared player-save checkpoints.

The monitor is a pure domain owner.  It consumes only the exact-version,
bounded normalized runtime projection supplied by the normal passive save
scheduler; it never reads a save, sends device input, or grants action
authority.
"""

from __future__ import annotations

import copy
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any, Mapping, Optional

from core.perk_save_monitor import PerkSaveMonitorContext
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
)
from core.runtime_save import (
    ActiveRunTalliesSnapshot,
    NormalizedRuntimeSave,
    RuntimeTallyComponent,
)


ACTIVE_RUN_METRIC_TIMELINE_SCHEMA_VERSION = 1
MAX_ACTIVE_RUN_METRIC_SAMPLES = 384
MAX_ACTIVE_RUN_METRIC_REJECTIONS = 16
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ActiveRunMetricMonitor:
    """Track independent cumulative components for one exact active round."""

    def __init__(self) -> None:
        self._context: Optional[PerkSaveMonitorContext] = None
        self._identity: Optional[dict[str, Any]] = None
        self._mapping_id: Optional[str] = None
        self._audit_id: Optional[str] = None
        self._components: dict[str, dict[str, Any]] = {}
        self._round_conflict_reason: Optional[str] = None
        self._terminal_window: Optional[dict[str, Any]] = None
        self._rejections: list[dict[str, Any]] = []

    def bind_context(
        self,
        context: PerkSaveMonitorContext,
        *,
        new_activity: bool = False,
    ) -> bool:
        """Bind an owned activity and reset only at its explicit boundary."""

        if not isinstance(context, PerkSaveMonitorContext) or not context.valid():
            return False
        current = self._context
        if current is None:
            self._context = context
            return True
        if current == context:
            return True
        if (
            new_activity
            and current.runtime_session_id == context.runtime_session_id
            and current.activity_scope_id != context.activity_scope_id
            and current.target_binding == context.target_binding
        ):
            self._reset_evidence()
            self._context = context
            return True
        reason = (
            "target_binding_changed"
            if current.target_binding != context.target_binding
            else "runtime_or_activity_binding_changed"
        )
        self._reject(reason, component="binding")
        return False

    def observe_bundle(
        self,
        acquisition: PlayerSaveAcquisitionBundle,
        *,
        context: PerkSaveMonitorContext,
    ) -> str:
        """Consume one scheduler-owned acquisition without another save read."""

        if not self._bind_observation_context(context):
            return "rejected_binding"
        if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
            self._reject("typed_acquisition_required", component="acquisition")
            return "rejected_acquisition"
        if acquisition.binding != context.target_binding:
            self._reject(
                "acquisition_target_binding_mismatch",
                component="binding",
                acquisition=acquisition,
            )
            return "rejected_binding"
        if (
            acquisition.status is not PlayerSaveAcquisitionStatus.COMPLETE
            or not acquisition.complete
            or acquisition.snapshot is None
        ):
            self._reject(
                acquisition.reason,
                component="acquisition",
                acquisition=acquisition,
            )
            return "rejected_acquisition"
        if acquisition.acquisition_type not in {
            PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
            PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
            PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        }:
            self._reject(
                "unsupported_acquisition_type",
                component="acquisition",
                acquisition=acquisition,
            )
            return "rejected_acquisition"

        snapshot = acquisition.snapshot
        runtime = getattr(snapshot, "runtime_save", None)
        mapping_id = getattr(snapshot, "mapping_id", None)
        game_version = getattr(snapshot, "game_version", None)
        if (
            not isinstance(runtime, NormalizedRuntimeSave)
            or not isinstance(mapping_id, str)
            or mapping_id != runtime.mapping_id
            or type(game_version) is not int
            or getattr(snapshot, "shape_valid", None) is not True
            or getattr(snapshot, "mapping_supported", None) is not True
        ):
            self._reject("runtime_projection_unavailable", component="mapping")
            return "rejected_runtime_projection"
        try:
            return self._observe_runtime(
                runtime,
                game_version=game_version,
                acquisition=acquisition,
                context=context,
            )
        except (TypeError, ValueError) as exc:
            self._reject(_safe_reason(exc), component="runtime")
            return "rejected_runtime"

    def latest_summary(
        self,
        context: Optional[PerkSaveMonitorContext],
    ) -> Optional[dict[str, Any]]:
        """Return a detached compact current checkpoint for operator status."""

        if context is None or context != self._context or self._identity is None:
            return None
        economy = self._components.get("economy")
        if not economy or not economy.get("samples"):
            return None
        sample = economy["samples"][-1]
        return {
            "captured_at": sample["captured_at"],
            "save_revision": sample["save_revision"],
            "saved_wave": sample["saved_wave"],
            "whole_run": copy.deepcopy(sample.get("whole_run")),
            "average": copy.deepcopy(sample.get("derived") or {}),
            "interval": copy.deepcopy(sample.get("interval")),
        }

    def terminal_evidence(
        self,
        *,
        context: Optional[PerkSaveMonitorContext],
        terminal_save_report: Any,
    ) -> dict[str, Any]:
        """Return retained checkpoints reconciled to a bound terminal report."""

        context_matches = context is not None and context == self._context
        reconciliations: dict[str, Any] = {}
        terminal_report_reason = "terminal_context_unbound"
        terminal_report_available = False
        terminal_values: dict[str, Decimal] = {}
        terminal_wave: Optional[int] = None
        if context_matches and self._terminal_window is None:
            terminal_report_reason = "terminal_checkpoint_window_unbound"
        elif context_matches:
            try:
                terminal_values, terminal_wave = _terminal_values(
                    terminal_save_report,
                    expected_mapping_id=self._mapping_id,
                    expected_terminal_window=self._terminal_window,
                )
            except (TypeError, ValueError) as exc:
                terminal_report_reason = _safe_reason(exc)
            else:
                terminal_report_available = True
                terminal_report_reason = ""

        for component_name, state in self._components.items():
            reconciliations[component_name] = self._reconcile_component(
                component_name,
                state,
                terminal_values=terminal_values,
                terminal_wave=terminal_wave,
                terminal_available=terminal_report_available,
            )
        component_payloads = {
            name: _component_evidence(state)
            for name, state in self._components.items()
        }
        component_problems = [
            name
            for name, payload in reconciliations.items()
            if payload.get("status") != "reconciled"
        ]
        sample_count = sum(
            len(state.get("samples") or ()) for state in self._components.values()
        )
        terminal_conflicts = [
            name
            for name, payload in reconciliations.items()
            if payload.get("status") == "conflict"
        ]
        if not context_matches:
            terminal_status = "unavailable"
            terminal_reason = "terminal_context_unbound"
        elif not terminal_report_available:
            terminal_status = "unavailable"
            terminal_reason = terminal_report_reason
        elif not sample_count:
            terminal_status = "unavailable"
            terminal_reason = "active_run_checkpoints_unavailable"
        elif terminal_conflicts:
            terminal_status = "conflict"
            terminal_reason = "terminal_component_conflict:" + ",".join(
                sorted(terminal_conflicts)
            )
        elif component_problems:
            terminal_status = "partial"
            terminal_reason = "one_or_more_components_not_terminal_reconciled"
        else:
            terminal_status = "reconciled"
            terminal_reason = ""

        if not context_matches:
            status = "unavailable"
            reason = "terminal_context_unbound"
        elif self._round_conflict_reason:
            status = "conflict"
            reason = self._round_conflict_reason
        elif not sample_count:
            status = "unavailable"
            reason = "active_run_checkpoints_unavailable"
        elif not terminal_report_available:
            status = "retained_checkpoints"
            reason = terminal_report_reason
        elif component_problems:
            status = "partial"
            reason = "one_or_more_components_not_terminal_reconciled"
        elif terminal_status == "reconciled":
            status = "complete"
            reason = ""
        else:
            status = "retained_checkpoints"
            reason = terminal_reason

        return {
            "schema_version": ACTIVE_RUN_METRIC_TIMELINE_SCHEMA_VERSION,
            "status": status,
            "reason": reason,
            "source": "shared_player_save_active_run_metric_monitor",
            "ui_action_authority": False,
            "context_status": "bound" if context_matches else "unbound",
            "binding": (
                self._context.redacted() if self._context is not None else None
            ),
            "mapping_id": self._mapping_id,
            "audit_id": self._audit_id,
            "active_round_identity": copy.deepcopy(self._identity),
            "components": component_payloads,
            "latest": self.latest_summary(context),
            "terminal": {
                "status": terminal_status,
                "reason": terminal_reason,
                "window": copy.deepcopy(self._terminal_window),
                "components": reconciliations,
            },
            "round_conflict_reason": self._round_conflict_reason,
            "rejections": copy.deepcopy(self._rejections),
        }

    def _observe_runtime(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        game_version: int,
        acquisition: PlayerSaveAcquisitionBundle,
        context: PerkSaveMonitorContext,
    ) -> str:
        captured_at = _timestamp(runtime.capture.get("captured_at"))
        source_sha256 = str(runtime.capture.get("source_sha256") or "")
        if _SHA256_RE.fullmatch(source_sha256) is None:
            raise ValueError("runtime_capture_fingerprint_invalid")
        if runtime.save_revision < 0 or runtime.current_wave < 0:
            raise ValueError("runtime_checkpoint_identity_invalid")

        if not runtime.round_active:
            return self._observe_inactive(
                runtime,
                captured_at=captured_at,
                source_sha256=source_sha256,
                acquisition=acquisition,
                context=context,
            )
        if acquisition.acquisition_type is PlayerSaveAcquisitionType.NATURAL_BOUNDARY:
            self._reject(
                "active_projection_at_terminal_boundary",
                component="boundary",
                acquisition=acquisition,
            )
            return "rejected_terminal_active_projection"

        identity = runtime.active_round_identity
        if identity is None or identity.game_version != game_version:
            self._reject("active_identity_unavailable", component="identity")
            return "rejected_identity"
        identity_payload = identity.as_dict()
        if _SHA256_RE.fullmatch(identity.fingerprint) is None:
            self._reject("active_identity_invalid", component="identity")
            return "rejected_identity"
        if self._identity is not None and self._identity != identity_payload:
            self._round_conflict_reason = "active_round_identity_changed"
            self._reject(
                self._round_conflict_reason,
                component="identity",
                acquisition=acquisition,
            )
            return "rejected_identity"
        if self._mapping_id is not None and self._mapping_id != runtime.mapping_id:
            self._round_conflict_reason = "runtime_mapping_changed"
            self._reject(
                self._round_conflict_reason,
                component="mapping",
                acquisition=acquisition,
            )
            return "rejected_mapping"

        tallies = runtime.active_tallies
        if not isinstance(tallies, ActiveRunTalliesSnapshot):
            self._reject(
                runtime.active_tallies_reason or "active_tallies_unavailable",
                component="mapping",
            )
            return "rejected_active_tallies"
        if tallies.state != "active_round" or tallies.evidence_level != "cross_channel":
            self._reject("active_tally_authority_invalid", component="mapping")
            return "rejected_active_tallies"
        if self._audit_id is not None and self._audit_id != tallies.audit_id:
            self._round_conflict_reason = "active_tally_audit_changed"
            self._reject(
                self._round_conflict_reason,
                component="mapping",
                acquisition=acquisition,
            )
            return "rejected_mapping"

        self._identity = identity_payload
        self._mapping_id = runtime.mapping_id
        self._audit_id = tallies.audit_id
        real_time_seconds = _active_real_time_seconds(tallies)
        accepted = 0
        unavailable = 0
        ignored = 0
        for component in tallies.components:
            if component.status != "observed":
                unavailable += 1
                self._record_component_unavailable(
                    component.name,
                    component.reason or "component_unavailable",
                )
                continue
            disposition = self._observe_component(
                component,
                captured_at=captured_at,
                save_revision=runtime.save_revision,
                saved_wave=runtime.current_wave,
                source_sha256=source_sha256,
                real_time_seconds=real_time_seconds,
            )
            if disposition == "accepted":
                accepted += 1
            elif disposition == "ignored":
                ignored += 1
        if accepted:
            return (
                "accepted_checkpoint"
                if not unavailable
                else "accepted_partial_checkpoint"
            )
        if ignored and not unavailable:
            return "ignored_duplicate_checkpoint"
        return "no_component_checkpoint_accepted"

    def _observe_inactive(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        captured_at: datetime,
        source_sha256: str,
        acquisition: PlayerSaveAcquisitionBundle,
        context: PerkSaveMonitorContext,
    ) -> str:
        """Accept only the exact natural boundary after retained active data."""

        boundary = acquisition.boundary
        if (
            acquisition.acquisition_type
            is not PlayerSaveAcquisitionType.NATURAL_BOUNDARY
            or boundary is None
            or boundary.kind
            not in {
                PlayerSaveBoundaryKind.GAME_OVER,
                PlayerSaveBoundaryKind.TOURNAMENT_RESULTS,
            }
            or boundary.runtime_session_id != context.runtime_session_id
            or boundary.activity_scope_id != context.activity_scope_id
            or boundary.observed_at >= acquisition.acquisition_started_at
        ):
            self._reject(
                "inactive_projection_requires_bound_natural_boundary",
                component="boundary",
                acquisition=acquisition,
            )
            return "rejected_unbound_terminal_boundary"
        if runtime.active_round_identity is not None:
            self._reject(
                "inactive_projection_has_identity",
                component="identity",
                acquisition=acquisition,
            )
            return "rejected_inactive_identity"
        if self._identity is None or not any(
            state.get("samples") for state in self._components.values()
        ):
            self._reject(
                "terminal_without_active_metric_checkpoint",
                component="binding",
                acquisition=acquisition,
            )
            return "rejected_terminal_without_checkpoint"
        if runtime.mapping_id != self._mapping_id:
            self._reject(
                "terminal_runtime_mapping_changed",
                component="mapping",
                acquisition=acquisition,
            )
            return "rejected_terminal_mapping"
        tallies = runtime.active_tallies
        if (
            not isinstance(tallies, ActiveRunTalliesSnapshot)
            or tallies.status != "not_applicable"
            or tallies.reason != "round_inactive"
            or tallies.state != "inactive_round"
            or tallies.audit_id != self._audit_id
            or tallies.evidence_level != "cross_channel"
        ):
            self._reject(
                "terminal_active_tally_projection_changed",
                component="mapping",
                acquisition=acquisition,
            )
            return "rejected_terminal_tallies"
        latest_capture = max(
            _timestamp(sample["captured_at"])
            for state in self._components.values()
            for sample in state.get("samples") or ()
        )
        if captured_at <= latest_capture:
            self._reject(
                "terminal_capture_did_not_follow_active_checkpoint",
                component="boundary",
                acquisition=acquisition,
            )
            return "rejected_terminal_capture_order"
        self._terminal_window = {
            "captured_at": captured_at.isoformat(),
            "save_revision": runtime.save_revision,
            "source_fingerprint": source_sha256,
            "boundary_kind": boundary.kind.value,
            "acquisition": acquisition.redacted_provenance(),
        }
        return "terminal_inactive_observed"

    def _observe_component(
        self,
        component: RuntimeTallyComponent,
        *,
        captured_at: datetime,
        save_revision: int,
        saved_wave: int,
        source_sha256: str,
        real_time_seconds: Optional[Decimal],
    ) -> str:
        state = self._components.setdefault(
            component.name,
            {
                "status": "observed",
                "reason": "",
                "definitions": {},
                "samples": [],
                "unavailable_observations": [],
            },
        )
        if state.get("status") == "conflict":
            return "ignored"
        definitions, metrics, derived = _component_projection(component)
        if state["definitions"] and state["definitions"] != definitions:
            self._component_conflict(
                component.name,
                "metric_definition_changed",
                state,
            )
            return "conflict"
        state["definitions"] = definitions
        sample = {
            "captured_at": captured_at.isoformat(),
            "save_revision": save_revision,
            "saved_wave": saved_wave,
            "source_fingerprint": source_sha256,
            "real_time_seconds": (
                _decimal_text(real_time_seconds)
                if real_time_seconds is not None
                else None
            ),
            "metrics": metrics,
            "derived": derived,
            "whole_run": None,
            "interval": None,
        }
        sample["whole_run"] = (
            _economy_whole_run(sample)
            if component.name == "economy"
            else _component_whole_run(sample)
        )
        samples = state["samples"]
        if samples:
            prior = samples[-1]
            if sample["source_fingerprint"] == prior["source_fingerprint"]:
                if (
                    sample["saved_wave"] == prior["saved_wave"]
                    and sample["metrics"] == prior["metrics"]
                    and sample["derived"] == prior["derived"]
                    and sample["whole_run"] == prior.get("whole_run")
                    and sample["real_time_seconds"]
                    == prior.get("real_time_seconds")
                ):
                    return "ignored"
                self._component_conflict(
                    component.name,
                    "same_source_projection_changed",
                    state,
                )
                return "conflict"
            if captured_at <= _timestamp(prior["captured_at"]):
                self._component_conflict(
                    component.name,
                    "newer_revision_capture_time_regressed",
                    state,
                )
                return "conflict"
            if saved_wave < prior["saved_wave"]:
                self._component_conflict(
                    component.name,
                    "saved_wave_regressed",
                    state,
                )
                return "conflict"
            regressed = [
                key
                for key, value in metrics.items()
                if key in prior["metrics"]
                and _decimal(value) < _decimal(prior["metrics"][key])
            ]
            if regressed:
                self._component_conflict(
                    component.name,
                    "monotonic_metric_regressed:" + ",".join(sorted(regressed)),
                    state,
                )
                return "conflict"
            sample["interval"] = (
                _economy_interval(prior, sample)
                if component.name == "economy"
                else _component_interval(prior, sample)
            )
        samples.append(sample)
        if len(samples) > MAX_ACTIVE_RUN_METRIC_SAMPLES:
            del samples[: len(samples) - MAX_ACTIVE_RUN_METRIC_SAMPLES]
        state["status"] = "observed"
        state["reason"] = ""
        return "accepted"

    def _record_component_unavailable(self, name: str, reason: str) -> None:
        state = self._components.setdefault(
            name,
            {
                "status": "unavailable",
                "reason": reason,
                "definitions": {},
                "samples": [],
                "unavailable_observations": [],
            },
        )
        observations = state.setdefault("unavailable_observations", [])
        observations.append(reason)
        if len(observations) > MAX_ACTIVE_RUN_METRIC_REJECTIONS:
            del observations[:-MAX_ACTIVE_RUN_METRIC_REJECTIONS]
        if state.get("status") != "conflict" and not state.get("samples"):
            state["status"] = "unavailable"
            state["reason"] = reason

    def _component_conflict(
        self,
        name: str,
        reason: str,
        state: dict[str, Any],
    ) -> None:
        state["status"] = "conflict"
        state["reason"] = reason
        self._reject(reason, component=name)

    def _reconcile_component(
        self,
        name: str,
        state: Mapping[str, Any],
        *,
        terminal_values: Mapping[str, Decimal],
        terminal_wave: Optional[int],
        terminal_available: bool,
    ) -> dict[str, Any]:
        samples = state.get("samples") or []
        if state.get("status") == "conflict":
            return {
                "status": "conflict",
                "reason": state.get("reason") or "component_conflict",
            }
        if not samples:
            return {
                "status": "unavailable",
                "reason": "component_checkpoint_unavailable",
            }
        if not terminal_available:
            return {
                "status": "unavailable",
                "reason": "terminal_report_unavailable",
            }
        latest = samples[-1]
        definitions = state.get("definitions") or {}
        matched: dict[str, str] = {}
        missing: list[str] = []
        regressed: list[str] = []
        for metric_name, definition in definitions.items():
            terminal_source = definition.get("terminal_source")
            if not terminal_source:
                continue
            terminal_value = terminal_values.get(str(terminal_source))
            if terminal_value is None:
                missing.append(metric_name)
                continue
            if terminal_value < _decimal(latest["metrics"][metric_name]):
                regressed.append(metric_name)
                continue
            matched[metric_name] = _decimal_text(terminal_value)
        if regressed:
            return {
                "status": "conflict",
                "reason": "terminal_metric_regressed:" + ",".join(regressed),
                "matched": matched,
                "missing": missing,
            }
        if missing:
            return {
                "status": "partial",
                "reason": "terminal_metric_missing:" + ",".join(missing),
                "matched": matched,
                "missing": missing,
            }
        payload: dict[str, Any] = {
            "status": "reconciled",
            "reason": "",
            "matched": matched,
            "missing": [],
        }
        if terminal_wave is not None and "realTime" in terminal_values:
            terminal_sample = {
                "saved_wave": terminal_wave,
                "real_time_seconds": _decimal_text(
                    terminal_values["realTime"]
                ),
                "metrics": {
                    metric_name: _decimal_text(terminal_values[terminal_source])
                    for metric_name, definition in definitions.items()
                    if (terminal_source := definition.get("terminal_source"))
                    in terminal_values
                },
            }
            payload["whole_run"] = (
                _economy_whole_run(terminal_sample)
                if name == "economy"
                else _component_whole_run(terminal_sample)
            )
            payload["tail_interval"] = (
                _economy_interval(latest, terminal_sample)
                if name == "economy"
                else _component_interval(latest, terminal_sample)
            )
        return payload

    def _bind_observation_context(self, context: Any) -> bool:
        if not isinstance(context, PerkSaveMonitorContext) or not context.valid():
            return False
        if self._context is None:
            self._context = context
            return True
        if self._context == context:
            return True
        self._reject("observation_context_changed", component="binding")
        return False

    def _reject(
        self,
        reason: Any,
        *,
        component: str,
        acquisition: Optional[PlayerSaveAcquisitionBundle] = None,
    ) -> None:
        item: dict[str, Any] = {
            "reason": _safe_reason(reason),
            "component": str(component or "unknown"),
        }
        if isinstance(acquisition, PlayerSaveAcquisitionBundle):
            item["acquisition"] = acquisition.redacted_provenance()
        self._rejections.append(item)
        if len(self._rejections) > MAX_ACTIVE_RUN_METRIC_REJECTIONS:
            del self._rejections[:-MAX_ACTIVE_RUN_METRIC_REJECTIONS]

    def _reset_evidence(self) -> None:
        self._identity = None
        self._mapping_id = None
        self._audit_id = None
        self._components = {}
        self._round_conflict_reason = None
        self._terminal_window = None
        self._rejections = []


def _component_projection(
    component: RuntimeTallyComponent,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    definitions: dict[str, Any] = {}
    metrics: dict[str, str] = {}
    for name, metric in component.metrics:
        value = _decimal(metric.value_decimal)
        definitions[name] = {
            "unit": metric.unit,
            "source_fields": list(metric.source_fields),
            "terminal_source": metric.terminal_source,
            "monotonic": True,
        }
        metrics[name] = _decimal_text(value)
    derived = {
        name: _decimal_text(_decimal(metric.value_decimal))
        for name, metric in component.derived
    }
    if not metrics:
        raise ValueError(f"active_tally_component_empty:{component.name}")
    return definitions, metrics, derived


def _component_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": state.get("status"),
        "reason": state.get("reason"),
        "metric_definitions": copy.deepcopy(state.get("definitions") or {}),
        "samples": copy.deepcopy(state.get("samples") or []),
        "unavailable_observations": list(
            state.get("unavailable_observations") or ()
        ),
    }


def _active_real_time_seconds(
    tallies: ActiveRunTalliesSnapshot,
) -> Optional[Decimal]:
    for component in tallies.components:
        if component.name != "economy" or component.status != "observed":
            continue
        for metric_name, metric in component.metrics:
            if metric_name == "real_time_seconds":
                return _decimal(metric.value_decimal)
    return None


def _component_whole_run(
    sample: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Calculate cumulative per-hour rates without changing component status."""

    real_time = sample.get("real_time_seconds")
    metrics = sample.get("metrics") or {}
    if real_time is None or not isinstance(metrics, Mapping) or not metrics:
        return None
    real_seconds = _decimal(real_time)
    if real_seconds <= 0:
        return None
    with localcontext() as context:
        context.prec = 50
        rates = {
            str(name): _decimal(value) * Decimal(3600) / real_seconds
            for name, value in metrics.items()
        }
    return {
        "real_time_seconds": _decimal_text(real_seconds),
        "per_hour": {
            name: _decimal_text(value) for name, value in rates.items()
        },
    }


def _economy_whole_run(
    sample: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Calculate realized cumulative rates directly from save totals."""

    metrics = sample.get("metrics") or {}
    required = {
        "real_time_seconds",
        "game_time_seconds",
        "coins_earned",
        "cells_earned",
        "cash_earned",
    }
    saved_wave = sample.get("saved_wave")
    if (
        not isinstance(metrics, Mapping)
        or not required <= set(metrics)
        or type(saved_wave) is not int
        or saved_wave < 0
    ):
        return None
    values = {name: _decimal(metrics[name]) for name in required}
    real_seconds = values["real_time_seconds"]
    if real_seconds <= 0:
        return None
    waves = Decimal(saved_wave)
    with localcontext() as context:
        context.prec = 50
        rates = {
            "coins_per_hour": (
                values["coins_earned"] * Decimal(3600) / real_seconds
            ),
            "cells_per_hour": (
                values["cells_earned"] * Decimal(3600) / real_seconds
            ),
            "cash_per_hour": (
                values["cash_earned"] * Decimal(3600) / real_seconds
            ),
            "waves_per_hour": waves * Decimal(3600) / real_seconds,
            "effective_game_speed": (
                values["game_time_seconds"] / real_seconds
            ),
        }
    return {
        "real_time_seconds": _decimal_text(real_seconds),
        "game_time_seconds": _decimal_text(values["game_time_seconds"]),
        "waves": _decimal_text(waves),
        "coins_earned": _decimal_text(values["coins_earned"]),
        "cells_earned": _decimal_text(values["cells_earned"]),
        "cash_earned": _decimal_text(values["cash_earned"]),
        **{name: _decimal_text(value) for name, value in rates.items()},
    }


def _component_interval(
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    prior_real = prior.get("real_time_seconds")
    current_real = current.get("real_time_seconds")
    prior_metrics = prior.get("metrics") or {}
    current_metrics = current.get("metrics") or {}
    if prior_real is None or current_real is None or not prior_metrics:
        return None
    delta_real = _decimal(current_real) - _decimal(prior_real)
    if delta_real <= 0 or not set(prior_metrics) <= set(current_metrics):
        return None
    deltas = {
        name: _decimal(current_metrics[name]) - _decimal(value)
        for name, value in prior_metrics.items()
    }
    if any(value < 0 for value in deltas.values()):
        return None
    with localcontext() as context:
        context.prec = 50
        rates = {
            name: value * Decimal(3600) / delta_real
            for name, value in deltas.items()
        }
    return {
        "real_time_seconds": _decimal_text(delta_real),
        "deltas": {
            name: _decimal_text(value) for name, value in deltas.items()
        },
        "per_hour": {
            name: _decimal_text(value) for name, value in rates.items()
        },
    }


def _economy_interval(
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    prior_metrics = prior.get("metrics") or {}
    current_metrics = current.get("metrics") or {}
    required = {
        "real_time_seconds",
        "game_time_seconds",
        "coins_earned",
        "cells_earned",
        "cash_earned",
    }
    if not required <= set(prior_metrics) or not required <= set(current_metrics):
        return None
    deltas = {
        name: _decimal(current_metrics[name]) - _decimal(prior_metrics[name])
        for name in required
    }
    delta_real = deltas["real_time_seconds"]
    delta_wave = Decimal(
        int(current.get("saved_wave") or 0) - int(prior.get("saved_wave") or 0)
    )
    if delta_real <= 0 or delta_wave < 0 or any(value < 0 for value in deltas.values()):
        return None
    with localcontext() as context:
        context.prec = 50
        rates = {
            "coins_per_hour": deltas["coins_earned"] * Decimal(3600) / delta_real,
            "cells_per_hour": deltas["cells_earned"] * Decimal(3600) / delta_real,
            "cash_per_hour": deltas["cash_earned"] * Decimal(3600) / delta_real,
            "waves_per_hour": delta_wave * Decimal(3600) / delta_real,
            "effective_game_speed": deltas["game_time_seconds"] / delta_real,
        }
    return {
        "real_time_seconds": _decimal_text(delta_real),
        "game_time_seconds": _decimal_text(deltas["game_time_seconds"]),
        "waves": _decimal_text(delta_wave),
        "coins_earned": _decimal_text(deltas["coins_earned"]),
        "cells_earned": _decimal_text(deltas["cells_earned"]),
        "cash_earned": _decimal_text(deltas["cash_earned"]),
        **{name: _decimal_text(value) for name, value in rates.items()},
    }


def _terminal_values(
    report: Any,
    *,
    expected_mapping_id: Optional[str],
    expected_terminal_window: Any,
) -> tuple[dict[str, Decimal], int]:
    if (
        not isinstance(report, Mapping)
        or report.get("schema_version") != 1
        or report.get("status") != "complete"
        or report.get("complete") is not True
        or (report.get("ui_fallback") or {}).get("required") is not False
    ):
        raise ValueError("terminal_save_report_unavailable")
    if report.get("mapping_id") != expected_mapping_id:
        raise ValueError("terminal_mapping_changed")
    capture = report.get("capture")
    if (
        not isinstance(expected_terminal_window, Mapping)
        or not isinstance(capture, Mapping)
        or report.get("terminal_state")
        != expected_terminal_window.get("boundary_kind")
        or capture.get("captured_at")
        != expected_terminal_window.get("captured_at")
        or capture.get("save_revision")
        != expected_terminal_window.get("save_revision")
        or capture.get("source_fingerprint")
        != expected_terminal_window.get("source_fingerprint")
        or capture.get("acquisition")
        != expected_terminal_window.get("acquisition")
    ):
        raise ValueError("terminal_report_provenance_mismatch")
    completed = report.get("completed_entry")
    identity = completed.get("identity") if isinstance(completed, Mapping) else None
    more_stats = completed.get("more_stats") if isinstance(completed, Mapping) else None
    if not isinstance(identity, Mapping) or not isinstance(more_stats, Mapping):
        raise ValueError("terminal_completed_entry_unavailable")
    wave = identity.get("wave")
    if type(wave) is not int or wave < 0:
        raise ValueError("terminal_wave_invalid")
    values: dict[str, Decimal] = {}
    sections = more_stats.get("sections")
    if not isinstance(sections, list):
        raise ValueError("terminal_more_stats_unavailable")
    for section in sections:
        rows = section.get("rows") if isinstance(section, Mapping) else None
        if not isinstance(rows, list):
            raise ValueError("terminal_more_stats_changed_shape")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("terminal_more_stats_changed_shape")
            source_fields = row.get("source_fields")
            if (
                row.get("derivation") != "direct"
                or not isinstance(source_fields, list)
                or len(source_fields) != 1
                or row.get("value_decimal") is None
            ):
                continue
            source = str(source_fields[0])
            value = _decimal(row["value_decimal"])
            prior = values.get(source)
            if prior is not None and prior != value:
                raise ValueError("terminal_duplicate_source_conflict")
            values[source] = value
    values.setdefault("gameTime", _decimal(identity.get("game_time_seconds")))
    values.setdefault("realTime", _decimal(identity.get("real_time_seconds")))
    return values, wave


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("metric_decimal_invalid") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("metric_decimal_invalid")
    return result


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("runtime_capture_time_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("runtime_capture_time_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("runtime_capture_time_invalid")
    return parsed


def _safe_reason(value: Any) -> str:
    reason = re.sub(r"[^a-zA-Z0-9_:,.-]+", "_", str(value or "unknown"))
    return reason[:240] or "unknown"


__all__ = [
    "ACTIVE_RUN_METRIC_TIMELINE_SCHEMA_VERSION",
    "ActiveRunMetricMonitor",
]
