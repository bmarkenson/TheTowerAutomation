"""Bound, monotonic active-run metrics from shared player-save checkpoints.

The monitor is a pure domain owner.  It consumes only capability-bound,
bounded normalized runtime projections supplied by shared forced, natural, or
passive acquisitions; it never reads a save, sends device input, or grants
action authority.
"""

from __future__ import annotations

import copy
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any, Mapping, Optional

from core.player_save_observation import PlayerSaveObservationContext
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
MAX_ACTIVE_RUN_METRIC_TARGET_HANDOFFS = 16
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ActiveRunMetricMonitor:
    """Track independent cumulative components for one exact active round."""

    def __init__(self) -> None:
        self._context: Optional[PlayerSaveObservationContext] = None
        self._identity: Optional[dict[str, Any]] = None
        self._mapping_id: Optional[str] = None
        self._capability_id: Optional[str] = None
        self._semantic_fingerprint: Optional[str] = None
        self._binding_fingerprint: Optional[str] = None
        self._audit_id: Optional[str] = None
        self._capability_resolution: Optional[str] = None
        self._components: dict[str, dict[str, Any]] = {}
        self._round_conflict_reason: Optional[str] = None
        self._wave_status = "unavailable"
        self._wave_reason = "active_wave_unavailable"
        self._last_saved_wave: Optional[int] = None
        self._terminal_tail_baseline: Optional[dict[str, Any]] = None
        self._terminal_relation_status = "external_structural_report_required"
        self._terminal_relation_reason = ""
        self._terminal_window: Optional[dict[str, Any]] = None
        self._rejections: list[dict[str, Any]] = []
        self._pending_target_handoff: Optional[dict[str, Any]] = None
        self._target_handoffs: list[dict[str, Any]] = []
        self._target_epoch = 0

    def bind_context(
        self,
        context: PlayerSaveObservationContext,
        *,
        new_activity: bool = False,
    ) -> bool:
        """Bind an owned activity and reset only at its explicit boundary."""

        if (
            not isinstance(context, PlayerSaveObservationContext)
            or not context.valid()
        ):
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
            and current.active_round_identity_fingerprint
            != context.active_round_identity_fingerprint
            and current.target_binding == context.target_binding
        ):
            self._reset_evidence()
            self._context = context
            return True
        reason = (
            "target_binding_changed"
            if current.target_binding != context.target_binding
            else "runtime_or_battle_identity_changed"
        )
        self._reject(reason, component="binding")
        return False

    def continue_same_battle_at_target(
        self,
        context: PlayerSaveObservationContext,
        *,
        acquisition: PlayerSaveAcquisitionBundle,
    ) -> bool:
        """Continue retained metrics after one forced same-battle handoff.

        Ordinary observations cannot move the monitor between targets.  The
        runtime may call this boundary only with the already-acquired forced
        destination save whose active identity proved ``SAME_BATTLE``.
        """

        if (
            not isinstance(context, PlayerSaveObservationContext)
            or not context.valid()
        ):
            return False
        if not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
            and acquisition.complete
            and acquisition.binding == context.target_binding
            and acquisition.snapshot is not None
        ):
            self._reject(
                "same_battle_target_handoff_requires_forced_acquisition",
                component="binding",
                acquisition=(
                    acquisition
                    if isinstance(acquisition, PlayerSaveAcquisitionBundle)
                    else None
                ),
            )
            return False
        runtime = getattr(acquisition.snapshot, "runtime_save", None)
        identity = getattr(runtime, "active_round_identity", None)
        if not (
            isinstance(runtime, NormalizedRuntimeSave)
            and runtime.round_active is True
            and identity is not None
            and identity.fingerprint
            == context.active_round_identity_fingerprint
        ):
            self._reject(
                "same_battle_target_handoff_identity_unverified",
                component="binding",
                acquisition=acquisition,
            )
            return False

        current = self._context
        if current is None:
            self._context = context
            return True
        if (
            current.runtime_session_id != context.runtime_session_id
            or current.active_round_identity_fingerprint
            != context.active_round_identity_fingerprint
        ):
            self._reject(
                "same_battle_target_handoff_context_changed",
                component="binding",
                acquisition=acquisition,
            )
            return False
        if current.target_binding == context.target_binding:
            self._context = context
            return True

        transition = {
            "source_target_binding_fingerprint": (
                current.target_binding.fingerprint
            ),
            "destination_target_binding_fingerprint": (
                context.target_binding.fingerprint
            ),
            "active_round_identity_fingerprint": (
                context.active_round_identity_fingerprint
            ),
            "verified_at": acquisition.captured_at.isoformat(),
            "status": "verified_checkpoint_pending",
            "interval_continuity": None,
            "target_epoch": self._target_epoch + 1,
        }
        self._context = context
        self._pending_target_handoff = transition
        self._target_handoffs.append(transition)
        if len(self._target_handoffs) > MAX_ACTIVE_RUN_METRIC_TARGET_HANDOFFS:
            del self._target_handoffs[
                :-MAX_ACTIVE_RUN_METRIC_TARGET_HANDOFFS
            ]
        return True

    def observe_bundle(
        self,
        acquisition: PlayerSaveAcquisitionBundle,
        *,
        context: PlayerSaveObservationContext,
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
                capabilities=getattr(snapshot, "capabilities", {}),
                acquisition=acquisition,
                context=context,
            )
        except (TypeError, ValueError) as exc:
            self._reject(_safe_reason(exc), component="runtime")
            return "rejected_runtime"

    def latest_summary(
        self,
        context: Optional[PlayerSaveObservationContext],
    ) -> Optional[dict[str, Any]]:
        """Return a detached compact current checkpoint for operator status."""

        if (
            context is None
            or context != self._context
            or self._identity is None
            or self._pending_target_handoff is not None
        ):
            return None
        target_fingerprint = context.target_binding.fingerprint
        latest_by_component = {
            name: latest
            for name, state in self._components.items()
            if (
                latest := _latest_target_sample(
                    state.get("samples") or [],
                    target_fingerprint,
                )
            )
        }
        if not latest_by_component:
            return None
        economy = self._components.get("economy")
        economy_sample = (
            _latest_target_sample(
                economy.get("samples") or [],
                target_fingerprint,
            )
            if economy
            else None
        )
        sample = max(
            latest_by_component.values(),
            key=lambda item: _timestamp(item["captured_at"]),
        )
        return {
            "captured_at": sample["captured_at"],
            "save_revision": sample["save_revision"],
            "saved_wave": sample["saved_wave"],
            "source_fingerprint": sample["source_fingerprint"],
            "target_binding_fingerprint": sample.get(
                "target_binding_fingerprint"
            ),
            "whole_run": copy.deepcopy(
                economy_sample.get("whole_run") if economy_sample else None
            ),
            "average": copy.deepcopy(
                (economy_sample.get("derived") or {})
                if economy_sample
                else {}
            ),
            "interval": copy.deepcopy(
                economy_sample.get("interval") if economy_sample else None
            ),
            "components": {
                name: {
                    "status": self._components[name].get("status"),
                    "reason": self._components[name].get("reason"),
                    "latest": copy.deepcopy(latest),
                }
                for name, latest in sorted(latest_by_component.items())
            },
        }

    def performance_evidence(
        self,
        context: Optional[PlayerSaveObservationContext],
        *,
        limit: int = 3,
    ) -> Optional[dict[str, Any]]:
        """Return bounded save-backed intervals for passive health assessment.

        This is deliberately an observation-only projection.  It carries the
        exact save-mapping semantics that produced the rates, but no input or
        lifecycle authority.
        """

        if context is None or context != self._context or self._identity is None:
            return None
        economy = self._components.get("economy")
        if not isinstance(economy, Mapping):
            return None
        target_fingerprint = context.target_binding.fingerprint
        bounded_limit = max(1, min(int(limit), 12))
        samples = [
            {
                "captured_at": sample.get("captured_at"),
                "save_revision": sample.get("save_revision"),
                "saved_wave": sample.get("saved_wave"),
                "interval": copy.deepcopy(sample.get("interval")),
            }
            for sample in economy.get("samples") or ()
            if isinstance(sample, Mapping)
            and sample.get("target_binding_fingerprint")
            == target_fingerprint
            and sample.get("interval_target_binding") == "same_target"
            and isinstance(sample.get("interval"), Mapping)
        ][-bounded_limit:]
        return {
            "schema_version": ACTIVE_RUN_METRIC_TIMELINE_SCHEMA_VERSION,
            "status": str(economy.get("status") or "unavailable"),
            "reason": str(economy.get("reason") or ""),
            "mapping_id": self._mapping_id,
            "semantic_fingerprint": self._semantic_fingerprint,
            "capability_resolution": self._capability_resolution,
            "checkpoints": samples,
        }

    def terminal_evidence(
        self,
        *,
        context: Optional[PlayerSaveObservationContext],
        terminal_save_report: Any,
    ) -> dict[str, Any]:
        """Return retained checkpoints reconciled to a bound terminal report."""

        context_matches = context is not None and context == self._context
        reconciliations: dict[str, Any] = {}
        terminal_report_reason = "terminal_context_unbound"
        terminal_report_available = False
        terminal_values: dict[str, Decimal] = {}
        terminal_claim_issues: dict[str, str] = {}
        terminal_wave: Optional[int] = None
        observed_terminal_wave: Optional[int] = None
        terminal_wave_status = "unavailable"
        terminal_wave_reason = "terminal_wave_unavailable"
        expected_terminal_claims = {
            str(definition["terminal_source"]): definition
            for state in self._components.values()
            for definition in (state.get("definitions") or {}).values()
            if definition.get("terminal_source")
        }
        if context_matches and self._terminal_window is None:
            terminal_report_reason = "terminal_checkpoint_window_unbound"
        elif context_matches:
            try:
                (
                    terminal_values,
                    terminal_wave,
                    terminal_claim_issues,
                    terminal_wave_reason,
                ) = _terminal_values(
                    terminal_save_report,
                    expected_capability_id=self._capability_id,
                    expected_semantic_fingerprint=(
                        self._semantic_fingerprint
                    ),
                    expected_binding_fingerprint=self._binding_fingerprint,
                    expected_terminal_window=self._terminal_window,
                    expected_claims=expected_terminal_claims,
                )
            except (TypeError, ValueError) as exc:
                terminal_report_reason = _safe_reason(exc)
            else:
                terminal_report_available = True
                terminal_report_reason = ""
                observed_terminal_wave = terminal_wave
                if terminal_wave_reason:
                    terminal_wave_status = "unavailable"
                    terminal_wave = None
                elif terminal_wave is None:
                    terminal_wave_status = "unavailable"
                    terminal_wave_reason = "terminal_wave_unavailable"
                elif (
                    self._last_saved_wave is not None
                    and terminal_wave < self._last_saved_wave
                ):
                    terminal_wave_status = "conflict"
                    terminal_wave_reason = "terminal_wave_regressed"
                    terminal_wave = None
                else:
                    terminal_wave_status = "observed"
                    terminal_wave_reason = ""

        rate_terminal_wave = (
            terminal_wave if self._wave_status == "observed" else None
        )
        terminal_rate_clock_reason = (
            self._terminal_rate_clock_reason(
                terminal_values,
                terminal_claim_issues,
            )
            if terminal_report_available
            else "terminal_rate_clock_unavailable"
        )
        for component_name, state in self._components.items():
            reconciliation = self._reconcile_component(
                component_name,
                state,
                terminal_values=terminal_values,
                terminal_claim_issues=terminal_claim_issues,
                terminal_wave=rate_terminal_wave,
                terminal_rate_clock_reason=terminal_rate_clock_reason,
                terminal_available=terminal_report_available,
            )
            if (
                component_name == "economy"
                and terminal_report_available
                and reconciliation.get("status") == "reconciled"
                and (
                    self._wave_status != "observed"
                    or terminal_wave_status != "observed"
                )
            ):
                reconciliation["status"] = "partial"
                reconciliation["reason"] = (
                    self._wave_reason
                    if self._wave_status != "observed"
                    else terminal_wave_reason
                )
            reconciliations[component_name] = reconciliation
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

        terminal_relation_status = self._terminal_relation_status
        terminal_relation_reason = self._terminal_relation_reason
        if (
            self._capability_resolution != "semantic_forward_revision"
            and terminal_report_available
        ):
            terminal_relation_status = "bound"
            terminal_relation_reason = ""

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
            "capability_id": self._capability_id,
            "semantic_fingerprint": self._semantic_fingerprint,
            "binding_fingerprint": self._binding_fingerprint,
            "audit_id": self._audit_id,
            "capability_resolution": self._capability_resolution,
            "active_round_identity": copy.deepcopy(self._identity),
            "terminal_relation": {
                "status": terminal_relation_status,
                "reason": terminal_relation_reason,
            },
            "wave_claim": {
                "status": self._wave_status,
                "reason": self._wave_reason,
                "latest_value": self._last_saved_wave,
            },
            "components": component_payloads,
            "latest": self.latest_summary(context),
            "terminal": {
                "status": terminal_status,
                "reason": terminal_reason,
                "window": copy.deepcopy(self._terminal_window),
                "wave_claim": {
                    "status": terminal_wave_status,
                    "reason": terminal_wave_reason,
                    "value": observed_terminal_wave,
                },
                "components": reconciliations,
            },
            "round_conflict_reason": self._round_conflict_reason,
            "target_handoffs": copy.deepcopy(self._target_handoffs),
            "rejections": copy.deepcopy(self._rejections),
        }

    def _observe_runtime(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        game_version: int,
        capabilities: Mapping[str, Any],
        acquisition: PlayerSaveAcquisitionBundle,
        context: PlayerSaveObservationContext,
    ) -> str:
        captured_at = _timestamp(runtime.capture.get("captured_at"))
        source_sha256 = str(runtime.capture.get("source_sha256") or "")
        if _SHA256_RE.fullmatch(source_sha256) is None:
            raise ValueError("runtime_capture_fingerprint_invalid")
        if (
            runtime.save_revision is not None
            and (type(runtime.save_revision) is not int or runtime.save_revision < 0)
        ):
            raise ValueError("runtime_save_revision_invalid")
        if runtime.current_wave is not None and (
            type(runtime.current_wave) is not int or runtime.current_wave < 0
        ):
            raise ValueError("runtime_checkpoint_identity_invalid")
        if type(runtime.round_active) is not bool:
            raise ValueError("runtime_round_state_unavailable")

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
        capability = capabilities.get(tallies.capability_id)
        if (
            capability is None
            or getattr(capability, "semantic_fingerprint", None)
            != tallies.semantic_fingerprint
            or getattr(capability, "binding_fingerprint", None)
            != tallies.binding_fingerprint
            or getattr(capability, "status", None) not in {"observed", "partial"}
        ):
            self._reject("active_tally_capability_unavailable", component="mapping")
            return "rejected_active_tallies"
        if self._capability_id is not None and (
            self._capability_id != tallies.capability_id
            or self._semantic_fingerprint != tallies.semantic_fingerprint
            or self._binding_fingerprint != tallies.binding_fingerprint
        ):
            self._round_conflict_reason = "active_tally_contract_changed"
            self._reject(
                self._round_conflict_reason,
                component="mapping",
                acquisition=acquisition,
            )
            return "rejected_mapping"
        if self._audit_id is not None and self._audit_id != tallies.audit_id:
            self._round_conflict_reason = "active_tally_audit_changed"
            self._reject(
                self._round_conflict_reason,
                component="mapping",
                acquisition=acquisition,
            )
            return "rejected_mapping"
        capability_resolution = str(
            getattr(capability, "resolution", "") or ""
        )
        if (
            self._capability_resolution is not None
            and capability_resolution != self._capability_resolution
        ):
            self._round_conflict_reason = "active_tally_resolution_changed"
            self._reject(
                self._round_conflict_reason,
                component="mapping",
                acquisition=acquisition,
            )
            return "rejected_mapping"

        self._identity = identity_payload
        self._mapping_id = runtime.mapping_id
        self._capability_id = tallies.capability_id
        self._semantic_fingerprint = tallies.semantic_fingerprint
        self._binding_fingerprint = tallies.binding_fingerprint
        self._audit_id = tallies.audit_id
        self._capability_resolution = capability_resolution
        if capability_resolution == "semantic_forward_revision":
            self._observe_semantic_terminal_tail_baseline(runtime)
        checkpoint_wave = runtime.current_wave
        pending_handoff = self._pending_target_handoff
        handoff_checkpoint = bool(
            isinstance(pending_handoff, Mapping)
            and pending_handoff.get(
                "destination_target_binding_fingerprint"
            )
            == context.target_binding.fingerprint
        )
        target_epoch = self._target_epoch
        restart_interval = False
        if handoff_checkpoint:
            pending_epoch = pending_handoff.get("target_epoch")
            if type(pending_epoch) is int:
                target_epoch = pending_epoch
            restart_interval = self._handoff_checkpoint_requires_interval_restart(
                tallies,
                checkpoint_wave=checkpoint_wave,
            )
        if self._wave_status == "conflict":
            checkpoint_wave = None
        elif checkpoint_wave is None:
            self._wave_status = "unavailable"
            self._wave_reason = (
                runtime.current_wave_reason or "active_wave_unavailable"
            )
        elif restart_interval:
            self._wave_status = "observed"
            self._wave_reason = ""
            self._last_saved_wave = checkpoint_wave
        elif (
            self._last_saved_wave is not None
            and checkpoint_wave < self._last_saved_wave
        ):
            self._wave_status = "conflict"
            self._wave_reason = "saved_wave_regressed"
            checkpoint_wave = None
            self._reject(self._wave_reason, component="wave")
        else:
            self._wave_status = "observed"
            self._wave_reason = ""
            self._last_saved_wave = checkpoint_wave
        accepted = 0
        unavailable = 0 if self._wave_status == "observed" else 1
        ignored = 0
        conflicted = 0
        components = sorted(
            tallies.components,
            key=lambda item: (item.name != "economy", item.name),
        )
        current_real_time: Optional[Decimal] = None
        for component in components:
            if component.status not in {"observed", "partial", "unavailable"}:
                unavailable += 1
                self._record_component_unavailable(
                    component.name,
                    component.reason or "component_unavailable",
                )
                continue
            if component.status != "observed":
                unavailable += 1
            raw_real_time = (
                None
                if component.name == "economy"
                and self._rate_clock_conflicted()
                else (
                    _active_real_time_seconds(tallies)
                    if component.name == "economy"
                    else current_real_time
                )
            )
            disposition = self._observe_component(
                component,
                captured_at=captured_at,
                save_revision=runtime.save_revision,
                saved_wave=checkpoint_wave,
                source_sha256=source_sha256,
                real_time_seconds=raw_real_time,
                target_binding_fingerprint=(
                    context.target_binding.fingerprint
                ),
                restart_interval=restart_interval,
                target_epoch=target_epoch,
                cross_target_interval=(
                    handoff_checkpoint and not restart_interval
                ),
            )
            if component.name == "economy":
                current_real_time = (
                    None
                    if self._rate_clock_conflicted()
                    else self._current_sample_real_time(source_sha256)
                )
            if disposition == "accepted":
                accepted += 1
            elif disposition == "ignored":
                ignored += 1
            elif disposition == "conflict":
                conflicted += 1
        if accepted:
            disposition = (
                "accepted_checkpoint"
                if not unavailable
                else "accepted_partial_checkpoint"
            )
        elif conflicted:
            disposition = "no_component_checkpoint_accepted"
        elif ignored and not unavailable:
            disposition = "ignored_duplicate_checkpoint"
        else:
            disposition = "no_component_checkpoint_accepted"
        if handoff_checkpoint and disposition in {
            "accepted_checkpoint",
            "accepted_partial_checkpoint",
            "ignored_duplicate_checkpoint",
        }:
            pending_handoff["status"] = "checkpoint_observed"
            pending_handoff["interval_continuity"] = (
                "restarted_after_destination_rollback"
                if restart_interval
                else "continued"
            )
            pending_handoff["checkpoint_source_fingerprint"] = (
                source_sha256
            )
            pending_handoff["checkpoint_save_revision"] = (
                runtime.save_revision
            )
            pending_handoff["checkpoint_wave"] = checkpoint_wave
            self._target_epoch = target_epoch
            self._pending_target_handoff = None
        return disposition

    def _handoff_checkpoint_requires_interval_restart(
        self,
        tallies: ActiveRunTalliesSnapshot,
        *,
        checkpoint_wave: Optional[int],
    ) -> bool:
        """Detect an expected destination rollback without poisoning rates."""

        if (
            checkpoint_wave is not None
            and self._last_saved_wave is not None
            and checkpoint_wave < self._last_saved_wave
        ):
            return True
        for component in tallies.components:
            state = self._components.get(component.name)
            samples = state.get("samples") if isinstance(state, Mapping) else None
            if not isinstance(samples, list) or not samples:
                continue
            _, metrics, _, _, _ = _component_projection(component)
            for metric_name, value in metrics.items():
                prior = _latest_metric_sample(samples, metric_name)
                if (
                    prior is not None
                    and _decimal(value)
                    < _decimal(prior["metrics"][metric_name])
                ):
                    return True
        return False

    def _observe_inactive(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        captured_at: datetime,
        source_sha256: str,
        acquisition: PlayerSaveAcquisitionBundle,
        context: PlayerSaveObservationContext,
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
            or boundary.active_round_identity_fingerprint
            != context.active_round_identity_fingerprint
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
            or tallies.capability_id != self._capability_id
            or tallies.semantic_fingerprint != self._semantic_fingerprint
            or tallies.binding_fingerprint != self._binding_fingerprint
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
        if self._capability_resolution == "semantic_forward_revision":
            terminal_relation_reason = self._bind_semantic_terminal_tail(
                runtime,
                expected_tournament=(
                    boundary.kind
                    is PlayerSaveBoundaryKind.TOURNAMENT_RESULTS
                ),
            )
            if terminal_relation_reason:
                self._reject(
                    terminal_relation_reason,
                    component="terminal_relation",
                    acquisition=acquisition,
                )
                return "terminal_inactive_observed_without_causal_tail"
        self._terminal_window = {
            "captured_at": captured_at.isoformat(),
            "save_revision": runtime.save_revision,
            "source_fingerprint": source_sha256,
            "boundary_kind": boundary.kind.value,
            "acquisition": acquisition.redacted_provenance(),
        }
        return "terminal_inactive_observed"

    def _observe_semantic_terminal_tail_baseline(
        self,
        runtime: NormalizedRuntimeSave,
    ) -> None:
        projection, reason = _active_tally_terminal_tail(runtime)
        if projection is None:
            if self._terminal_tail_baseline is None:
                self._terminal_relation_status = "unavailable"
                self._terminal_relation_reason = reason
            return
        if self._terminal_relation_status == "conflict":
            return
        if self._terminal_tail_baseline is None:
            self._terminal_tail_baseline = projection
            self._terminal_relation_status = "baseline_observed"
            self._terminal_relation_reason = ""
            return
        if projection != self._terminal_tail_baseline:
            self._terminal_relation_status = "conflict"
            self._terminal_relation_reason = (
                "active_terminal_history_tail_changed"
            )
            self._reject(
                self._terminal_relation_reason,
                component="terminal_relation",
            )

    def _bind_semantic_terminal_tail(
        self,
        runtime: NormalizedRuntimeSave,
        *,
        expected_tournament: bool,
    ) -> str:
        baseline = self._terminal_tail_baseline
        if self._terminal_relation_status == "conflict":
            return self._terminal_relation_reason
        if baseline is None:
            self._terminal_relation_status = "unavailable"
            self._terminal_relation_reason = (
                "active_terminal_history_baseline_unavailable"
            )
            return self._terminal_relation_reason
        latest, reason = _active_tally_terminal_tail(runtime)
        if latest is None:
            self._terminal_relation_status = "unavailable"
            self._terminal_relation_reason = reason
            return reason
        valid_count = (
            latest["entry_count"] == baseline["entry_count"] + 1
            if baseline["entry_count"] < baseline["capacity"]
            else latest["entry_count"] == baseline["capacity"]
        )
        if (
            latest["mapping_id"] != baseline["mapping_id"]
            or latest["capacity"] != baseline["capacity"]
            or latest["fingerprint"] == baseline["fingerprint"]
            or latest["is_tournament"] is not expected_tournament
            or not valid_count
        ):
            self._terminal_relation_status = "unavailable"
            self._terminal_relation_reason = (
                "terminal_capability_tail_transition_invalid"
            )
            return self._terminal_relation_reason
        self._terminal_relation_status = "bound"
        self._terminal_relation_reason = ""
        return ""

    def _observe_component(
        self,
        component: RuntimeTallyComponent,
        *,
        captured_at: datetime,
        save_revision: int,
        saved_wave: Optional[int],
        source_sha256: str,
        real_time_seconds: Optional[Decimal],
        target_binding_fingerprint: str,
        restart_interval: bool,
        target_epoch: int,
        cross_target_interval: bool,
    ) -> str:
        state = self._components.setdefault(
            component.name,
            {
                "status": "observed",
                "reason": "",
                "definitions": {},
                "derived_definitions": {},
                "samples": [],
                "metric_conflicts": {},
                "unavailable_claims": {},
                "unavailable_observations": [],
            },
        )
        if state.get("status") == "conflict":
            return "ignored"
        (
            definitions,
            metrics,
            derived,
            derived_definitions,
            unavailable_claims,
        ) = _component_projection(component)
        for metric_name, definition in definitions.items():
            prior_definition = state["definitions"].get(metric_name)
            if prior_definition is not None and prior_definition != definition:
                self._metric_conflict(
                    component.name,
                    metric_name,
                    "metric_definition_changed",
                    state,
                )
                metrics.pop(metric_name, None)
                continue
            state["definitions"][metric_name] = definition
            state["unavailable_claims"].pop(metric_name, None)
        for derived_name, definition in derived_definitions.items():
            prior_definition = state["derived_definitions"].get(derived_name)
            if prior_definition is not None and prior_definition != definition:
                state["unavailable_claims"][f"derived.{derived_name}"] = (
                    "derived_definition_changed"
                )
                derived.pop(derived_name, None)
                continue
            state["derived_definitions"][derived_name] = definition
            state["unavailable_claims"].pop(
                f"derived.{derived_name}",
                None,
            )
        state["unavailable_claims"].update(unavailable_claims)
        for reason in unavailable_claims.values():
            observations = state["unavailable_observations"]
            observations.append(reason)
            if len(observations) > MAX_ACTIVE_RUN_METRIC_REJECTIONS:
                del observations[:-MAX_ACTIVE_RUN_METRIC_REJECTIONS]

        conflicted_metrics = set(state["metric_conflicts"])
        for metric_name in conflicted_metrics:
            metrics.pop(metric_name, None)
        _drop_dependent_derived(
            derived,
            derived_definitions,
            conflicted_metrics,
        )
        if not metrics:
            state["status"] = "unavailable" if not state["samples"] else "partial"
            state["reason"] = "no_nonconflicting_metric_claims"
            return "ignored"
        sample = {
            "captured_at": captured_at.isoformat(),
            "save_revision": save_revision,
            "saved_wave": saved_wave,
            "source_fingerprint": source_sha256,
            "target_binding_fingerprint": target_binding_fingerprint,
            "interval_boundary": (
                "same_battle_target_rollback" if restart_interval else None
            ),
            "target_epoch": target_epoch,
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
            same_source_handoff = False
            if sample["source_fingerprint"] == prior["source_fingerprint"]:
                differing = [
                    key
                    for key, value in metrics.items()
                    if key in prior["metrics"] and prior["metrics"][key] != value
                ]
                for metric_name in differing:
                    self._metric_conflict(
                        component.name,
                        metric_name,
                        "same_source_projection_changed",
                        state,
                    )
                    metrics.pop(metric_name, None)
                _drop_dependent_derived(
                    derived,
                    derived_definitions,
                    set(differing),
                )
                if differing:
                    return "conflict"
                if not cross_target_interval:
                    return "ignored"
                if (
                    save_revision != prior.get("save_revision")
                    or metrics != prior.get("metrics")
                    or derived != prior.get("derived")
                    or saved_wave != prior.get("saved_wave")
                    or sample["real_time_seconds"]
                    != prior.get("real_time_seconds")
                ):
                    return "ignored"
                same_source_handoff = True
            if same_source_handoff:
                sample["captured_at"] = prior["captured_at"]
            elif captured_at <= _timestamp(prior["captured_at"]):
                self._component_conflict(
                    component.name,
                    "newer_revision_capture_time_regressed",
                    state,
                )
                return "conflict"
            if not restart_interval and not same_source_handoff:
                interval_samples = (
                    samples
                    if cross_target_interval
                    else _target_epoch_samples(
                        samples,
                        target_epoch,
                    )
                )
                regressed: list[str] = []
                for key, value in tuple(metrics.items()):
                    prior_metric_sample = _latest_metric_sample(
                        interval_samples,
                        key,
                    )
                    if (
                        prior_metric_sample is not None
                        and _decimal(value)
                        < _decimal(prior_metric_sample["metrics"][key])
                    ):
                        regressed.append(key)
                        self._metric_conflict(
                            component.name,
                            key,
                            "monotonic_metric_regressed",
                            state,
                        )
                        metrics.pop(key, None)
                _drop_dependent_derived(
                    derived,
                    derived_definitions,
                    set(regressed),
                )
                if "real_time_seconds" in regressed:
                    sample["real_time_seconds"] = None
                if not metrics:
                    state["status"] = "partial"
                    state["reason"] = "all_current_metric_claims_conflicted"
                    return "conflict"
                sample["interval"] = (
                    _economy_interval(interval_samples, sample)
                    if component.name == "economy"
                    else _component_interval(interval_samples, sample)
                )
            if same_source_handoff:
                sample["interval"] = copy.deepcopy(prior.get("interval"))
        if sample["interval"] is not None:
            sample["interval_target_binding"] = (
                "same_battle_handoff"
                if cross_target_interval
                else "same_target"
            )
        else:
            sample["interval_target_binding"] = None
        sample["whole_run"] = (
            _economy_whole_run(sample)
            if component.name == "economy"
            else _component_whole_run(sample)
        )
        samples.append(sample)
        if len(samples) > MAX_ACTIVE_RUN_METRIC_SAMPLES:
            del samples[: len(samples) - MAX_ACTIVE_RUN_METRIC_SAMPLES]
        if state["metric_conflicts"] or state["unavailable_claims"]:
            state["status"] = "partial"
            state["reason"] = "one_or_more_metric_claims_unavailable"
        else:
            state["status"] = "observed"
            state["reason"] = ""
        return "accepted"

    def _current_sample_real_time(
        self,
        source_sha256: str,
    ) -> Optional[Decimal]:
        economy = self._components.get("economy") or {}
        samples = economy.get("samples") or []
        if not samples or samples[-1].get("source_fingerprint") != source_sha256:
            return None
        value = samples[-1].get("real_time_seconds")
        return _decimal(value) if value is not None else None

    def _rate_clock_conflicted(self) -> bool:
        economy = self._components.get("economy") or {}
        return "real_time_seconds" in (
            economy.get("metric_conflicts") or {}
        )

    def _terminal_rate_clock_reason(
        self,
        terminal_values: Mapping[str, Decimal],
        terminal_claim_issues: Mapping[str, str],
    ) -> str:
        if self._rate_clock_conflicted():
            return "shared_rate_clock_conflict:real_time_seconds"
        terminal_real = terminal_values.get("realTime")
        if terminal_real is None:
            issue = terminal_claim_issues.get("realTime")
            return (
                f"terminal_rate_clock_unavailable:{issue}"
                if issue
                else "terminal_rate_clock_unavailable"
            )
        economy = self._components.get("economy") or {}
        samples = _target_epoch_samples(
            economy.get("samples") or [],
            self._target_epoch,
        )
        prior = _latest_metric_sample(
            samples,
            "real_time_seconds",
        )
        if (
            prior is not None
            and terminal_real
            < _decimal(prior["metrics"]["real_time_seconds"])
        ):
            return "terminal_rate_clock_regressed"
        return ""

    def _record_component_unavailable(self, name: str, reason: str) -> None:
        state = self._components.setdefault(
            name,
            {
                "status": "unavailable",
                "reason": reason,
                "definitions": {},
                "derived_definitions": {},
                "samples": [],
                "metric_conflicts": {},
                "unavailable_claims": {},
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

    def _metric_conflict(
        self,
        component_name: str,
        metric_name: str,
        reason: str,
        state: dict[str, Any],
    ) -> None:
        normalized = f"{reason}:{metric_name}"
        state.setdefault("metric_conflicts", {})[metric_name] = normalized
        state["status"] = "partial"
        state["reason"] = "one_or_more_metric_claims_conflicted"
        self._reject(normalized, component=component_name)

    def _reconcile_component(
        self,
        name: str,
        state: Mapping[str, Any],
        *,
        terminal_values: Mapping[str, Decimal],
        terminal_claim_issues: Mapping[str, str],
        terminal_wave: Optional[int],
        terminal_rate_clock_reason: str,
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
        current_samples = _target_epoch_samples(
            samples,
            self._target_epoch,
        )
        definitions = state.get("definitions") or {}
        matched: dict[str, str] = {}
        missing: list[str] = []
        claim_issues: dict[str, str] = {}
        conflicts: dict[str, str] = dict(state.get("metric_conflicts") or {})
        for metric_name, definition in definitions.items():
            if metric_name in conflicts:
                continue
            terminal_source = definition.get("terminal_source")
            if not terminal_source:
                continue
            latest_metric_sample = _latest_metric_sample(
                current_samples,
                metric_name,
            )
            if latest_metric_sample is None:
                missing.append(metric_name)
                continue
            terminal_value = terminal_values.get(str(terminal_source))
            if terminal_value is None:
                missing.append(metric_name)
                issue = terminal_claim_issues.get(str(terminal_source))
                if issue:
                    claim_issues[metric_name] = issue
                continue
            if terminal_value < _decimal(
                latest_metric_sample["metrics"][metric_name]
            ):
                conflicts[metric_name] = "terminal_metric_regressed"
                continue
            matched[metric_name] = _decimal_text(terminal_value)
        status = "reconciled"
        reason = ""
        if missing or conflicts:
            status = "partial" if matched else "unavailable"
            reason_parts = []
            if conflicts:
                reason_parts.append(
                    "terminal_metric_conflict:"
                    + ",".join(sorted(conflicts))
                )
            if missing:
                reason_parts.append(
                    "terminal_metric_missing:" + ",".join(sorted(missing))
                )
            reason = ";".join(reason_parts)
        if terminal_rate_clock_reason and matched:
            status = "partial"
            reason = (
                f"{reason};{terminal_rate_clock_reason}"
                if reason
                else terminal_rate_clock_reason
            )
        payload: dict[str, Any] = {
            "status": status,
            "reason": reason,
            "matched": matched,
            "missing": sorted(missing),
            "conflicts": conflicts,
            "claim_issues": claim_issues,
        }
        if (
            "realTime" in terminal_values
            and "real_time_seconds" not in conflicts
            and not terminal_rate_clock_reason
        ):
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
                    and metric_name not in conflicts
                },
            }
            payload["whole_run"] = (
                _economy_whole_run(terminal_sample)
                if name == "economy"
                else _component_whole_run(terminal_sample)
            )
            payload["tail_interval"] = (
                _terminal_component_interval(
                    current_samples,
                    terminal_sample,
                    economy=name == "economy",
                )
            )
        return payload

    def _bind_observation_context(self, context: Any) -> bool:
        if not isinstance(context, PlayerSaveObservationContext) or not context.valid():
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
        self._capability_id = None
        self._semantic_fingerprint = None
        self._binding_fingerprint = None
        self._audit_id = None
        self._capability_resolution = None
        self._components = {}
        self._round_conflict_reason = None
        self._wave_status = "unavailable"
        self._wave_reason = "active_wave_unavailable"
        self._last_saved_wave = None
        self._terminal_tail_baseline = None
        self._terminal_relation_status = "external_structural_report_required"
        self._terminal_relation_reason = ""
        self._terminal_window = None
        self._rejections = []
        self._pending_target_handoff = None
        self._target_handoffs = []
        self._target_epoch = 0


def _active_tally_terminal_tail(
    runtime: NormalizedRuntimeSave,
) -> tuple[Optional[dict[str, Any]], str]:
    """Return only the causal tail fields declared by the tally capability."""

    tail = runtime.battle_history_tail
    identity = getattr(tail, "terminal_identity", None)
    reason = str(
        getattr(tail, "terminal_identity_reason", "")
        or "terminal_capability_tail_identity_unavailable"
    )
    if identity is None and not getattr(
        tail,
        "terminal_empty_baseline",
        False,
    ):
        return None, reason
    entry_count = getattr(tail, "entry_count", None)
    capacity = getattr(tail, "capacity", None)
    fingerprint = str(
        getattr(tail, "terminal_tail_fingerprint", None)
        or getattr(identity, "fingerprint", "")
        or ""
    )
    mapping_id = str(
        getattr(tail, "terminal_mapping_id", None)
        or getattr(identity, "mapping_id", "")
        or ""
    )
    is_empty = bool(getattr(tail, "terminal_empty_baseline", False))
    is_tournament = (
        getattr(identity, "is_tournament", None)
        if identity is not None
        else None
    )
    if (
        type(entry_count) is not int
        or type(capacity) is not int
        or not 0 <= entry_count <= capacity
        or (entry_count == 0) is not is_empty
        or (entry_count > 0 and identity is None)
        or mapping_id != runtime.mapping_id
        or _SHA256_RE.fullmatch(fingerprint) is None
        or (entry_count > 0 and type(is_tournament) is not bool)
    ):
        return None, "terminal_capability_tail_identity_invalid"
    return (
        {
            "mapping_id": mapping_id,
            "fingerprint": fingerprint,
            "entry_count": entry_count,
            "capacity": capacity,
            "is_tournament": is_tournament,
        },
        "",
    )


def _component_projection(
    component: RuntimeTallyComponent,
) -> tuple[
    dict[str, Any],
    dict[str, str],
    dict[str, str],
    dict[str, Any],
    dict[str, str],
]:
    definitions: dict[str, Any] = {}
    metrics: dict[str, str] = {}
    source_to_metric: dict[str, str] = {}
    for name, definition in component.claim_definitions:
        definitions[name] = {
            "unit": definition.unit,
            "source_fields": list(definition.source_fields),
            "terminal_source": definition.terminal_source,
            "monotonic": True,
            "semantic_id": definition.semantic_id,
            "semantic_fingerprint": definition.semantic_fingerprint,
        }
        for source_field in definition.source_fields:
            source_to_metric[source_field] = name
    for name, metric in component.metrics:
        value = _decimal(metric.value_decimal)
        definitions.setdefault(
            name,
            {
                "unit": metric.unit,
                "source_fields": list(metric.source_fields),
                "terminal_source": metric.terminal_source,
                "monotonic": True,
                "semantic_id": metric.semantic_id,
                "semantic_fingerprint": metric.semantic_fingerprint,
            },
        )
        metrics[name] = _decimal_text(value)
        for source_field in metric.source_fields:
            source_to_metric[source_field] = name
    derived: dict[str, str] = {}
    derived_definitions: dict[str, Any] = {
        name: {
            "unit": definition.unit,
            "source_fields": list(definition.source_fields),
            "dependencies": list(definition.dependencies),
            "semantic_id": definition.semantic_id,
            "semantic_fingerprint": definition.semantic_fingerprint,
        }
        for name, definition in component.derived_claim_definitions
    }
    for name, metric in component.derived:
        derived[name] = _decimal_text(_decimal(metric.value_decimal))
        derived_definitions.setdefault(
            name,
            {
                "unit": metric.unit,
                "source_fields": list(metric.source_fields),
                "dependencies": [
                    source_to_metric[source]
                    for source in metric.source_fields
                    if source in source_to_metric
                ],
                "semantic_id": metric.semantic_id,
                "semantic_fingerprint": metric.semantic_fingerprint,
            },
        )
    return (
        definitions,
        metrics,
        derived,
        derived_definitions,
        {name: reason for name, reason in component.unavailable},
    )


def _component_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": state.get("status"),
        "reason": state.get("reason"),
        "metric_definitions": copy.deepcopy(state.get("definitions") or {}),
        "derived_definitions": copy.deepcopy(
            state.get("derived_definitions") or {}
        ),
        "metric_conflicts": copy.deepcopy(
            state.get("metric_conflicts") or {}
        ),
        "unavailable_claims": copy.deepcopy(
            state.get("unavailable_claims") or {}
        ),
        "samples": copy.deepcopy(state.get("samples") or []),
        "unavailable_observations": list(
            state.get("unavailable_observations") or ()
        ),
    }


def _active_real_time_seconds(
    tallies: ActiveRunTalliesSnapshot,
) -> Optional[Decimal]:
    for component in tallies.components:
        if component.name != "economy" or component.status not in {
            "observed",
            "partial",
        }:
            continue
        for metric_name, metric in component.metrics:
            if metric_name == "real_time_seconds":
                return _decimal(metric.value_decimal)
    return None


def _latest_metric_sample(
    samples: list[Mapping[str, Any]],
    metric_name: str,
) -> Optional[Mapping[str, Any]]:
    for sample in reversed(samples):
        metrics = sample.get("metrics")
        if isinstance(metrics, Mapping) and metric_name in metrics:
            return sample
    return None


def _latest_target_sample(
    samples: list[Mapping[str, Any]],
    target_binding_fingerprint: str,
) -> Optional[Mapping[str, Any]]:
    for sample in reversed(samples):
        if (
            sample.get("target_binding_fingerprint")
            == target_binding_fingerprint
        ):
            return sample
    return None


def _target_epoch_samples(
    samples: list[Mapping[str, Any]],
    target_epoch: int,
) -> list[Mapping[str, Any]]:
    """Return checkpoints acquired within one exact target epoch."""

    return [
        sample
        for sample in samples
        if sample.get("target_epoch") == target_epoch
    ]


def _drop_dependent_derived(
    derived: dict[str, str],
    definitions: Mapping[str, Any],
    blocked_metrics: set[str],
) -> None:
    if not blocked_metrics:
        return
    for name, definition in definitions.items():
        dependencies = (
            definition.get("dependencies")
            if isinstance(definition, Mapping)
            else None
        )
        if isinstance(dependencies, list) and blocked_metrics.intersection(
            str(item) for item in dependencies
        ):
            derived.pop(name, None)


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
    saved_wave = sample.get("saved_wave")
    if not isinstance(metrics, Mapping) or "real_time_seconds" not in metrics:
        return None
    real_seconds = _decimal(metrics["real_time_seconds"])
    if real_seconds <= 0:
        return None
    payload: dict[str, Any] = {
        "real_time_seconds": _decimal_text(real_seconds),
    }
    with localcontext() as context:
        context.prec = 50
        if type(saved_wave) is int and saved_wave >= 0:
            waves = Decimal(saved_wave)
            payload["waves"] = _decimal_text(waves)
            payload["waves_per_hour"] = _decimal_text(
                waves * Decimal(3600) / real_seconds
            )
        for metric_name, rate_name in (
            ("coins_earned", "coins_per_hour"),
            ("cells_earned", "cells_per_hour"),
            ("cash_earned", "cash_per_hour"),
        ):
            if metric_name not in metrics:
                continue
            value = _decimal(metrics[metric_name])
            payload[metric_name] = _decimal_text(value)
            payload[rate_name] = _decimal_text(
                value * Decimal(3600) / real_seconds
            )
        if "game_time_seconds" in metrics:
            game_time = _decimal(metrics["game_time_seconds"])
            payload["game_time_seconds"] = _decimal_text(game_time)
            payload["effective_game_speed"] = _decimal_text(
                game_time / real_seconds
            )
    return payload


def _component_interval(
    prior_samples: list[Mapping[str, Any]],
    current: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    current_real = current.get("real_time_seconds")
    current_metrics = current.get("metrics") or {}
    if current_real is None or not isinstance(current_metrics, Mapping):
        return None
    deltas: dict[str, Decimal] = {}
    elapsed: dict[str, Decimal] = {}
    for name, current_value in current_metrics.items():
        prior = _latest_timed_metric_sample(prior_samples, str(name))
        if prior is None:
            continue
        delta_real = _decimal(current_real) - _decimal(
            prior["real_time_seconds"]
        )
        value = _decimal(current_value) - _decimal(prior["metrics"][name])
        if delta_real > 0 and value >= 0:
            deltas[str(name)] = value
            elapsed[str(name)] = delta_real
    if not deltas:
        return None
    with localcontext() as context:
        context.prec = 50
        rates = {
            name: value * Decimal(3600) / elapsed[name]
            for name, value in deltas.items()
        }
    payload = {
        "deltas": {
            name: _decimal_text(value) for name, value in deltas.items()
        },
        "per_hour": {
            name: _decimal_text(value) for name, value in rates.items()
        },
    }
    _attach_elapsed_times(payload, elapsed)
    return payload


def _economy_interval(
    prior_samples: list[Mapping[str, Any]],
    current: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    current_metrics = current.get("metrics") or {}
    if not isinstance(current_metrics, Mapping) or "real_time_seconds" not in current_metrics:
        return None
    current_real = _decimal(current_metrics["real_time_seconds"])
    deltas: dict[str, Decimal] = {}
    elapsed: dict[str, Decimal] = {}
    for name, current_value in current_metrics.items():
        if name == "real_time_seconds":
            continue
        prior = _latest_timed_metric_sample(prior_samples, str(name))
        if prior is None:
            continue
        delta_real = current_real - _decimal(prior["real_time_seconds"])
        value = _decimal(current_value) - _decimal(prior["metrics"][name])
        if delta_real > 0 and value >= 0:
            deltas[str(name)] = value
            elapsed[str(name)] = delta_real
    current_wave = current.get("saved_wave")
    wave_prior = next(
        (
            sample
            for sample in reversed(prior_samples)
            if sample.get("real_time_seconds") is not None
            and type(sample.get("saved_wave")) is int
        ),
        None,
    )
    payload: dict[str, Any] = {}
    if wave_prior is not None and type(current_wave) is int:
        wave_elapsed = current_real - _decimal(wave_prior["real_time_seconds"])
        delta_wave = Decimal(
            current_wave - int(wave_prior["saved_wave"])
        )
        if wave_elapsed > 0 and delta_wave >= 0:
            payload["waves"] = _decimal_text(delta_wave)
            elapsed["waves"] = wave_elapsed
    with localcontext() as context:
        context.prec = 50
        if "waves" in payload:
            payload["waves_per_hour"] = _decimal_text(
                _decimal(payload["waves"])
                * Decimal(3600)
                / elapsed["waves"]
            )
        for metric_name, rate_name in (
            ("coins_earned", "coins_per_hour"),
            ("cells_earned", "cells_per_hour"),
            ("cash_earned", "cash_per_hour"),
        ):
            if metric_name not in deltas:
                continue
            payload[metric_name] = _decimal_text(deltas[metric_name])
            payload[rate_name] = _decimal_text(
                deltas[metric_name] * Decimal(3600) / elapsed[metric_name]
            )
        if "game_time_seconds" in deltas:
            payload["game_time_seconds"] = _decimal_text(
                deltas["game_time_seconds"]
            )
            payload["effective_game_speed"] = _decimal_text(
                deltas["game_time_seconds"]
                / elapsed["game_time_seconds"]
            )
    if not payload:
        return None
    _attach_elapsed_times(payload, elapsed)
    return payload


def _attach_elapsed_times(
    payload: dict[str, Any],
    elapsed: Mapping[str, Decimal],
) -> None:
    values = {_decimal_text(value) for value in elapsed.values()}
    if len(values) == 1:
        payload["real_time_seconds"] = next(iter(values))
    elif values:
        payload["real_time_seconds_by_metric"] = {
            name: _decimal_text(value) for name, value in sorted(elapsed.items())
        }


def _latest_timed_metric_sample(
    samples: list[Mapping[str, Any]],
    metric_name: str,
) -> Optional[Mapping[str, Any]]:
    for sample in reversed(samples):
        metrics = sample.get("metrics")
        if (
            isinstance(metrics, Mapping)
            and metric_name in metrics
            and sample.get("real_time_seconds") is not None
        ):
            return sample
    return None


def _terminal_component_interval(
    samples: list[Mapping[str, Any]],
    terminal: Mapping[str, Any],
    *,
    economy: bool,
) -> Optional[dict[str, Any]]:
    """Calculate each tail rate from that metric's latest valid checkpoint."""

    terminal_metrics = terminal.get("metrics")
    terminal_real = terminal.get("real_time_seconds")
    if not isinstance(terminal_metrics, Mapping) or terminal_real is None:
        return None
    terminal_real_decimal = _decimal(terminal_real)
    deltas: dict[str, str] = {}
    rates: dict[str, str] = {}
    baselines: dict[str, str] = {}
    for metric_name, terminal_value in terminal_metrics.items():
        if metric_name == "real_time_seconds":
            continue
        prior = _latest_timed_metric_sample(samples, str(metric_name))
        if prior is None or prior.get("real_time_seconds") is None:
            continue
        delta_real = terminal_real_decimal - _decimal(
            prior["real_time_seconds"]
        )
        delta_value = _decimal(terminal_value) - _decimal(
            prior["metrics"][metric_name]
        )
        if delta_real <= 0 or delta_value < 0:
            continue
        deltas[str(metric_name)] = _decimal_text(delta_value)
        baselines[str(metric_name)] = _decimal_text(delta_real)
        with localcontext() as context:
            context.prec = 50
            rates[str(metric_name)] = _decimal_text(
                delta_value * Decimal(3600) / delta_real
            )
    if not rates:
        return None
    payload: dict[str, Any] = {
        "deltas": deltas,
        "real_time_seconds_by_metric": baselines,
        "per_hour": rates,
    }
    if economy:
        for metric_name, rate_name in (
            ("coins_earned", "coins_per_hour"),
            ("cells_earned", "cells_per_hour"),
            ("cash_earned", "cash_per_hour"),
        ):
            if metric_name in rates:
                payload[rate_name] = rates[metric_name]
        if "game_time_seconds" in deltas:
            delta_real = _decimal(baselines["game_time_seconds"])
            payload["effective_game_speed"] = _decimal_text(
                _decimal(deltas["game_time_seconds"]) / delta_real
            )
        terminal_wave = terminal.get("saved_wave")
        wave_prior = next(
            (
                sample
                for sample in reversed(samples)
                if sample.get("real_time_seconds") is not None
                and type(sample.get("saved_wave")) is int
            ),
            None,
        )
        if wave_prior is not None and type(terminal_wave) is int:
            delta_real = terminal_real_decimal - _decimal(
                wave_prior["real_time_seconds"]
            )
            delta_wave = Decimal(
                terminal_wave - int(wave_prior["saved_wave"])
            )
            if delta_real > 0 and delta_wave >= 0:
                with localcontext() as context:
                    context.prec = 50
                    payload["waves"] = _decimal_text(delta_wave)
                    payload["waves_per_hour"] = _decimal_text(
                        delta_wave * Decimal(3600) / delta_real
                    )
    return payload


def _terminal_values(
    report: Any,
    *,
    expected_capability_id: Optional[str],
    expected_semantic_fingerprint: Optional[str],
    expected_binding_fingerprint: Optional[str],
    expected_terminal_window: Any,
    expected_claims: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Decimal], Optional[int], dict[str, str], str]:
    if (
        not isinstance(report, Mapping)
        or report.get("schema_version") != 1
    ):
        raise ValueError("terminal_save_report_unavailable")
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
    terminal_claims = report.get("terminal_metric_claims")
    if (
        not isinstance(terminal_claims, Mapping)
        or terminal_claims.get("status") not in {"observed", "partial"}
        or terminal_claims.get("capability_id") != expected_capability_id
        or terminal_claims.get("semantic_fingerprint")
        != expected_semantic_fingerprint
        or terminal_claims.get("binding_fingerprint")
        != expected_binding_fingerprint
    ):
        raise ValueError("terminal_metric_capability_unavailable")
    wave = terminal_claims.get("saved_wave")
    wave_reason = ""
    if wave is not None and (type(wave) is not int or wave < 0):
        wave = None
        wave_reason = "terminal_wave_invalid"
    values: dict[str, Decimal] = {}
    issues: dict[str, str] = {}
    claims = terminal_claims.get("claims")
    if not isinstance(claims, Mapping):
        raise ValueError("terminal_metric_claims_unavailable")
    for source, claim in claims.items():
        if not isinstance(source, str) or source not in expected_claims:
            continue
        expected = expected_claims[source]
        if not isinstance(claim, Mapping) or claim.get("status") != "observed":
            issues[source] = "terminal_claim_unavailable"
            continue
        if (
            claim.get("semantic_id") != expected.get("semantic_id")
            or claim.get("semantic_fingerprint")
            != expected.get("semantic_fingerprint")
            or claim.get("unit") != expected.get("unit")
        ):
            issues[source] = "terminal_claim_contract_mismatch"
            continue
        try:
            values[source] = _decimal(claim.get("value_decimal"))
        except (TypeError, ValueError):
            issues[source] = "terminal_claim_value_invalid"
    unavailable = terminal_claims.get("unavailable")
    if isinstance(unavailable, Mapping):
        for source, reason in unavailable.items():
            if isinstance(source, str) and source in expected_claims:
                issues.setdefault(source, _safe_reason(reason))
    return values, wave, issues, wave_reason


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
