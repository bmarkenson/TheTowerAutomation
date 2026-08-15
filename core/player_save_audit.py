"""Opt-in, observation-only player-save audit projection.

The collector is deliberately outside every action and lifecycle authority
path. Normal App runtime gives its daemon worker the same typed bundles used by
other consumers. The collector has no acquisition or cadence path. Only a
compact allowlisted projection crosses the acquisition boundary; raw save
bytes, decoded roots, profile evidence, and completed-history rows are never
retained by this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import queue
import re
import threading
import time
from typing import Any, Optional
from uuid import uuid4

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
)
from core.perk_id_resolver import (
    normalize_timeline_mapping_batch,
    resolve_runtime_perk_ids,
)
from core.runtime_save import (
    NormalizedRuntimeSave,
    runtime_with_perk_id_overrides,
)
from utils.logger import log


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAYER_SAVE_AUDIT_RECEIPT_PATH = (
    ROOT / "logs" / "player_save_audit" / "receipts-v1.jsonl"
)
DEFAULT_PLAYER_SAVE_AUDIT_MANIFEST_PATH = (
    ROOT / "config" / "player_save_audit" / "data_9_game_1073.json"
)
# The manifest names the semantic authority origin. Compatible runtime mappings
# are accepted through its audit-matrix capability, not this filename/version.
PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_VERSION = 1
PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_ID = "thetower.player_save_natural_boundary_audit.v1"
PLAYER_SAVE_AUDIT_ID = "V1073-RUNTIME-013"
MAX_AUDIT_PERK_PICKS = 512
MAX_RECEIPT_BYTES = 128 * 1024
_WARNING_REMINDER_SECONDS = 15 * 60.0
_QUEUE_CAPACITY = 128
_MAX_PERK_MAPPING_BATCHES = 512
_MAX_PERK_MAPPING_BATCHES_PER_COMMAND = 64
_BOUNDARY_REASON_CODES = {
    "HOME_NEW_BATTLE": "home_new_battle",
    "RUNNING": "first_running_observation",
    "GAME_OVER": "game_over",
    "TOURNAMENT_RESULTS": "tournament_results",
}
_TERMINAL_BOUNDARIES = {"GAME_OVER", "TOURNAMENT_RESULTS"}
_SAFE_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,95}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_VISUAL_ABILITIES = {"demon_mode", "nuke", "second_wind"}
_VISUAL_SOURCES = {"button_disappearance", "active_status_icon"}
_FORBIDDEN_RECEIPT_KEY_PARTS = (
    "account",
    "decoded",
    "device_file",
    "exception",
    "more_stats",
    "ocr",
    "pixel",
    "player_id",
    "playerid",
    "profile",
    "raw",
    "root",
    "screenshot",
    "user_name",
    "username",
)
_COMMON_RECEIPT_KEYS = {
    "schema_version",
    "schema_id",
    "record_type",
    "record_id",
    "sequence",
    "recorded_at",
    "runtime_session_id",
    "collector_session_id",
}
_RECORD_PAYLOAD_KEYS = {
    "collector_session": {"manifest", "configuration", "authority"},
    "boundary_observation": {"boundary", "authority"},
    "save_observation": {
        "mapping",
        "capture",
        "target",
        "request",
        "round",
        "perks",
        "history_tail",
        "timing",
        "audit_outcomes",
        "authority",
    },
    "audit_outcome": {"outcome", "request", "target", "authority"},
    "visual_event": {"visual_event", "authority"},
    "normalized_component": {"component", "authority"},
}


class PlayerSaveAuditError(ValueError):
    """The audit manifest or normalized evidence is unsafe or malformed."""


@dataclass(frozen=True)
class AuditComponentSpec:
    """One independently gated normalized receipt component."""

    enabled: bool
    schema_version: Optional[int]
    audit_ids: tuple[str, ...]
    fields: Mapping[str, Any]
    unavailable_reason: str


@dataclass(frozen=True)
class PlayerSaveAuditManifest:
    """Audit-capability authority origin and optional-component gate."""

    schema_version: int
    manifest_id: str
    mapping_id: str
    audit_matrix_id: str
    game_version: int
    audit_ids: tuple[str, ...]
    receipt_schema_id: str
    components: Mapping[str, AuditComponentSpec]

    def component_statuses(self) -> dict[str, str]:
        return {
            name: "enabled" if spec.enabled else "unavailable"
            for name, spec in sorted(self.components.items())
        }


@dataclass(frozen=True)
class AuditRequest:
    """Passive reason and timing attached to one stable-save observation."""

    reasons: tuple[str, ...]
    requested_at: datetime
    boundary_label: Optional[str] = None
    boundary_observed_at: Optional[datetime] = None


@dataclass(frozen=True)
class AuditSaveObservation:
    """Strict allowlisted projection returned by the acquisition worker."""

    mapping_id: str
    audit_matrix_id: str
    game_version: int
    captured_at: datetime
    source_fingerprint: str
    save_revision: int
    round_active: bool
    saved_wave: int
    active_identity: Optional[Mapping[str, Any]]
    perks: Mapping[str, Any]
    history_tail: Mapping[str, Any]
    target_fingerprint: str
    acquisition_started_at: datetime
    acquisition_completed_at: datetime


@dataclass(frozen=True)
class _BoundaryCommand:
    request: AuditRequest


@dataclass(frozen=True)
class _VisualCommand:
    events: tuple[Mapping[str, Any], ...]
    rejected_count: int


@dataclass(frozen=True)
class _ComponentCommand:
    name: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class _OutcomeCommand:
    code: str
    component: str


@dataclass(frozen=True)
class _PerkMappingCommand:
    batches: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class _PerkMappingResetCommand:
    pass


@dataclass(frozen=True)
class _ExternalAcquisitionCommand:
    acquisition: PlayerSaveAcquisitionBundle
    request: AuditRequest


@dataclass(frozen=True)
class _StopCommand:
    pass


def load_player_save_audit_manifest(
    path: Path | str = DEFAULT_PLAYER_SAVE_AUDIT_MANIFEST_PATH,
) -> PlayerSaveAuditManifest:
    """Load and strictly validate the normalized audit-capability manifest."""

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlayerSaveAuditError("audit_manifest_unavailable") from exc
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "manifest_id",
        "mapping_id",
        "audit_matrix_id",
        "game_version",
        "audit_ids",
        "receipt_schema_id",
        "components",
    }:
        raise PlayerSaveAuditError("audit_manifest_changed_shape")
    if payload.get("schema_version") != 1:
        raise PlayerSaveAuditError("audit_manifest_schema_unsupported")

    manifest_id = _safe_id(payload.get("manifest_id"), "manifest_id")
    mapping_id = _safe_id(payload.get("mapping_id"), "mapping_id")
    audit_matrix_id = _safe_id(
        payload.get("audit_matrix_id"),
        "audit_matrix_id",
    )
    game_version = _nonnegative_int(payload.get("game_version"), "game_version")
    receipt_schema_id = _safe_id(
        payload.get("receipt_schema_id"),
        "receipt_schema_id",
    )
    if receipt_schema_id != PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_ID:
        raise PlayerSaveAuditError("audit_receipt_schema_mismatch")
    audit_ids = _safe_ids(payload.get("audit_ids"), "audit_ids")
    if PLAYER_SAVE_AUDIT_ID not in audit_ids:
        raise PlayerSaveAuditError("collector_audit_id_missing")

    raw_components = payload.get("components")
    if not isinstance(raw_components, Mapping) or not raw_components:
        raise PlayerSaveAuditError("audit_components_unavailable")
    components: dict[str, AuditComponentSpec] = {}
    for raw_name, raw_spec in raw_components.items():
        name = _safe_code(raw_name, "component_name")
        if not isinstance(raw_spec, Mapping) or set(raw_spec) != {
            "enabled",
            "schema_version",
            "audit_ids",
            "fields",
            "unavailable_reason",
        }:
            raise PlayerSaveAuditError("audit_component_changed_shape")
        if type(raw_spec.get("enabled")) is not bool:
            raise PlayerSaveAuditError("audit_component_enabled_changed_type")
        raw_schema = raw_spec.get("schema_version")
        schema_version = (
            _positive_int(raw_schema, "component_schema_version")
            if raw_schema is not None
            else None
        )
        enabled = bool(raw_spec["enabled"])
        if enabled and schema_version is None:
            raise PlayerSaveAuditError("enabled_component_schema_missing")
        fields = raw_spec.get("fields")
        if not isinstance(fields, Mapping):
            raise PlayerSaveAuditError("audit_component_fields_changed_shape")
        normalized_fields: dict[str, Any] = {}
        for raw_field, raw_rule in fields.items():
            field = _safe_code(raw_field, "component_field")
            if not isinstance(raw_rule, Mapping):
                raise PlayerSaveAuditError("audit_component_field_rule_changed_shape")
            normalized_fields[field] = dict(raw_rule)
        unavailable_reason = _safe_code(
            raw_spec.get("unavailable_reason") or "not_applicable",
            "component_unavailable_reason",
        )
        components[name] = AuditComponentSpec(
            enabled=enabled,
            schema_version=schema_version,
            audit_ids=_safe_ids(raw_spec.get("audit_ids"), "component_audit_ids"),
            fields=normalized_fields,
            unavailable_reason=unavailable_reason,
        )

    for required in (
        "core_runtime",
        "perk_id_calibration",
        "visual_activation_events",
        "survival_checkpoints",
    ):
        if required not in components:
            raise PlayerSaveAuditError("required_audit_component_missing")
    if not components["core_runtime"].enabled:
        raise PlayerSaveAuditError("core_audit_component_disabled")
    survival = components["survival_checkpoints"]
    if survival.enabled and not survival.fields:
        raise PlayerSaveAuditError("promoted_survival_component_fields_missing")

    return PlayerSaveAuditManifest(
        schema_version=1,
        manifest_id=manifest_id,
        mapping_id=mapping_id,
        audit_matrix_id=audit_matrix_id,
        game_version=game_version,
        audit_ids=audit_ids,
        receipt_schema_id=receipt_schema_id,
        components=components,
    )


class AppendOnlyAuditReceiptWriter:
    """Append canonical privacy-checked JSONL records without rewriting history."""

    def __init__(
        self,
        path: Path | str = DEFAULT_PLAYER_SAVE_AUDIT_RECEIPT_PATH,
    ) -> None:
        self.path = Path(path)

    def append(self, record: Mapping[str, Any]) -> None:
        _validate_receipt(record)
        rendered = (
            json.dumps(
                record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(rendered) > MAX_RECEIPT_BYTES:
            raise PlayerSaveAuditError("audit_receipt_exceeds_size_bound")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(rendered):
                written = os.write(descriptor, rendered[offset:])
                if written <= 0:
                    raise OSError("append returned no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class PlayerSaveAuditStateMachine:
    """Fail-closed, session-local reconciliation of normalized observations."""

    def __init__(
        self,
        manifest: PlayerSaveAuditManifest,
        *,
        receipt_sink: Callable[[Mapping[str, Any]], Any],
        runtime_session_id: Optional[str] = None,
        collector_session_id: Optional[str] = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        write_failure_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        self.manifest = manifest
        self._receipt_sink = receipt_sink
        self.runtime_session_id = runtime_session_id or str(uuid4())
        self.collector_session_id = collector_session_id or str(uuid4())
        self._now_fn = now_fn
        self._write_failure_fn = write_failure_fn
        self._sequence = 0
        self._seen_observations: set[tuple[int, str]] = set()
        self._seen_observation_order: list[tuple[int, str]] = []
        self._mapping_context: Optional[tuple[str, str, int]] = None
        self._seen_mapping_context_discontinuities: set[
            tuple[str, str, int, int, str]
        ] = set()
        self._seen_mapping_context_discontinuity_order: list[
            tuple[str, str, int, int, str]
        ] = []
        self._baseline: Optional[dict[str, Any]] = None
        self._active_identity_fingerprint: Optional[str] = None
        self._active_identity_discontinuous = False
        self._last_active_revision: Optional[int] = None
        self._last_active_wave: Optional[int] = None
        self._last_active_source_fingerprint: Optional[str] = None
        self._last_terminal_revision: Optional[int] = None
        self._last_terminal_source_fingerprint: Optional[str] = None
        self._last_perk_picks: Optional[tuple[tuple[Any, ...], ...]] = None
        self._last_complete_perks: Optional[dict[str, Any]] = None
        self._first_clearing_recorded = False
        self._terminal_candidate_fingerprint: Optional[str] = None
        self._pending_retry_round: Optional[dict[str, Any]] = None

    def start_session(self) -> None:
        """Append the explicit process-local collector session identity."""

        self._emit(
            "collector_session",
            manifest={
                "manifest_id": self.manifest.manifest_id,
                "mapping_id": self.manifest.mapping_id,
                "audit_matrix_id": self.manifest.audit_matrix_id,
                "game_version": self.manifest.game_version,
                "audit_ids": list(self.manifest.audit_ids),
                "components": self.manifest.component_statuses(),
            },
            configuration={
                "enabled": True,
                "acquisition_policy": "shared_bundles_only",
                "stable_read_policy": "provided_typed_bundle",
                "session_restore_policy": "never_restore_prior_session",
            },
            authority=_observation_only_authority(),
        )

    def record_boundary(self, request: AuditRequest) -> None:
        label = _boundary_label(request.boundary_label)
        if label is None or request.boundary_observed_at is None:
            return
        self._emit(
            "boundary_observation",
            boundary={
                "label": label,
                "observed_at": _utc_iso(request.boundary_observed_at),
                "reason_code": _BOUNDARY_REASON_CODES[label],
                "save_acquisition_requested": False,
            },
            authority=_observation_only_authority(),
        )

    def record_outcome(
        self,
        code: str,
        *,
        request: Optional[AuditRequest] = None,
        target_fingerprint: Optional[str] = None,
        component: str = "acquisition",
    ) -> None:
        safe_code = _safe_code(code, "outcome_code")
        payload: dict[str, Any] = {
            "outcome": {
                "code": safe_code,
                "component": _safe_code(component, "outcome_component"),
                "disposition": "fail_closed_observation_only",
            },
            "request": _request_payload(request),
            "target": (
                {"fingerprint": _sha256(target_fingerprint, "target_fingerprint")}
                if target_fingerprint is not None
                else None
            ),
            "authority": _observation_only_authority(),
        }
        self._emit("audit_outcome", **payload)

    def observe_save(
        self,
        observation: AuditSaveObservation,
        request: AuditRequest,
    ) -> bool:
        """Reconcile and append one stable normalized save observation."""

        try:
            normalized = _validated_observation(observation, self.manifest)
        except PlayerSaveAuditError:
            self.record_outcome(
                "malformed_normalized_evidence",
                request=request,
                target_fingerprint=(
                    observation.target_fingerprint
                    if _is_sha256(observation.target_fingerprint)
                    else None
                ),
                component="normalization",
            )
            return False

        mapping_context = (
            normalized["mapping_id"],
            normalized["audit_matrix_id"],
            normalized["game_version"],
        )
        if self._mapping_context is None:
            self._mapping_context = mapping_context
        elif mapping_context != self._mapping_context:
            discontinuity_key = (
                *mapping_context,
                normalized["save_revision"],
                normalized["source_fingerprint"],
            )
            if discontinuity_key not in self._seen_mapping_context_discontinuities:
                self._remember_mapping_context_discontinuity(discontinuity_key)
                self.record_outcome(
                    "mapping_context_discontinuity",
                    request=request,
                    target_fingerprint=normalized["target_fingerprint"],
                    component="normalization",
                )
            return False

        boundary = _boundary_label(request.boundary_label)
        duplicate_key = (
            normalized["save_revision"],
            normalized["source_fingerprint"],
        )
        if duplicate_key in self._seen_observations:
            if boundary == "HOME_NEW_BATTLE":
                self._reset_round_state()
                if not normalized["round_active"]:
                    self._baseline = _baseline_from_tail(normalized["history_tail"])
            return False
        self._remember_observation(duplicate_key)

        outcomes: list[dict[str, str]] = []
        is_home_baseline = boundary == "HOME_NEW_BATTLE"
        is_terminal = boundary in _TERMINAL_BOUNDARIES
        if is_home_baseline:
            self._reset_round_state()

        history_payload = dict(normalized["history_tail"])
        if history_payload["structural_status"] == "unavailable":
            outcomes.append(
                _outcome("history_tail_component_unavailable", "history_tail")
            )
        if history_payload["semantic_completed_entry"]["status"] == "unavailable":
            outcomes.append(
                _outcome(
                    "semantic_completed_entry_unavailable",
                    "history_tail",
                )
            )
        baseline_comparison = {"status": "not_evaluated"}
        if is_home_baseline:
            if normalized["round_active"]:
                outcomes.append(
                    _outcome("home_baseline_requires_inactive_save", "round")
                )
                baseline_comparison = {"status": "baseline_rejected_active_save"}
            else:
                baseline = _baseline_from_tail(history_payload)
                if baseline is None:
                    outcomes.append(
                        _outcome("home_baseline_tail_unavailable", "history_tail")
                    )
                    baseline_comparison = {"status": "baseline_unavailable"}
                else:
                    self._baseline = baseline
                    baseline_comparison = {
                        "status": "inactive_home_baseline_recorded",
                        "fingerprint": baseline["fingerprint"],
                        "entry_count": baseline["entry_count"],
                        "capacity": baseline["capacity"],
                    }

        if normalized["round_active"] and boundary == "RUNNING":
            retry_comparison = self._begin_proven_retry_round(
                normalized,
                request=request,
            )
            if retry_comparison is not None:
                baseline_comparison = retry_comparison

        round_payload: dict[str, Any] = {
            "active": normalized["round_active"],
            "saved_wave": normalized["saved_wave"],
            "identity": normalized["active_identity"],
            "identity_status": "inactive_projection",
            "attachment_status": "not_assessed_observation_only",
        }
        accepted_active = False
        if normalized["round_active"]:
            identity = normalized["active_identity"]
            if identity is None:
                round_payload["identity_status"] = "fail_closed_missing_identity"
                outcomes.append(_outcome("active_identity_unavailable", "round"))
            elif is_home_baseline:
                round_payload["identity_status"] = "fail_closed_home_save_active"
            elif self._active_identity_fingerprint is None:
                self._active_identity_fingerprint = identity["fingerprint"]
                self._last_active_revision = normalized["save_revision"]
                self._last_active_wave = normalized["saved_wave"]
                self._last_active_source_fingerprint = normalized["source_fingerprint"]
                round_payload["identity_status"] = "first_naturally_serialized_identity"
                accepted_active = True
                if self._baseline is None:
                    outcomes.append(
                        _outcome("pre_round_baseline_unavailable", "history_tail")
                    )
            elif identity["fingerprint"] != self._active_identity_fingerprint:
                self._active_identity_discontinuous = True
                round_payload["identity_status"] = "fail_closed_identity_discontinuity"
                outcomes.append(_outcome("active_identity_discontinuity", "round"))
            else:
                accepted_active, identity_status, identity_outcomes = (
                    self._accept_same_identity_revision(normalized)
                )
                round_payload["identity_status"] = identity_status
                outcomes.extend(identity_outcomes)

        terminal_revision_valid = True
        if is_terminal and not normalized["round_active"]:
            prior_revision = (
                self._last_terminal_revision
                if self._last_terminal_revision is not None
                else self._last_active_revision
            )
            prior_source = (
                self._last_terminal_source_fingerprint
                if self._last_terminal_source_fingerprint is not None
                else self._last_active_source_fingerprint
            )
            if prior_revision is None:
                terminal_revision_valid = False
                outcomes.append(
                    _outcome("terminal_without_session_active_identity", "round")
                )
            elif normalized["save_revision"] <= prior_revision:
                terminal_revision_valid = False
                outcomes.append(_outcome("terminal_revision_not_newer", "capture"))
            elif normalized["source_fingerprint"] == prior_source:
                terminal_revision_valid = False
                outcomes.append(
                    _outcome("terminal_revision_source_inconsistent", "capture")
                )
            else:
                self._last_terminal_revision = normalized["save_revision"]
                self._last_terminal_source_fingerprint = normalized[
                    "source_fingerprint"
                ]

        perks_payload = self._reconcile_perks(
            normalized,
            accepted_active=accepted_active,
            is_terminal=is_terminal,
            terminal_revision_valid=terminal_revision_valid,
            outcomes=outcomes,
        )

        if is_terminal:
            baseline_comparison = self._compare_terminal_tail(
                history_payload,
                round_active=normalized["round_active"],
                terminal_revision_valid=terminal_revision_valid,
                outcomes=outcomes,
            )
            self._retain_retry_round_baseline(
                normalized,
                request=request,
                baseline_comparison=baseline_comparison,
            )
        history_payload["baseline_comparison"] = baseline_comparison

        self._emit(
            "save_observation",
            mapping={
                "manifest_id": self.manifest.manifest_id,
                "mapping_id": normalized["mapping_id"],
                "audit_matrix_id": normalized["audit_matrix_id"],
                "audit_ids": list(self.manifest.audit_ids),
                "game_version": normalized["game_version"],
            },
            capture={
                "captured_at": _utc_iso(normalized["captured_at"]),
                "save_revision": normalized["save_revision"],
                "source_fingerprint": normalized["source_fingerprint"],
            },
            target={"fingerprint": normalized["target_fingerprint"]},
            request=_request_payload(request),
            round=round_payload,
            perks=perks_payload,
            history_tail=history_payload,
            timing=_timing_payload(normalized, request),
            audit_outcomes=outcomes,
            authority=_observation_only_authority(),
        )
        return True

    def record_visual_events(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> int:
        """Append only strict metadata from already-confirmed tracker events."""

        normalized_events: list[dict[str, Any]] = []
        rejected_count = 0
        for event in events:
            normalized = _normalize_visual_event(event)
            if normalized is None:
                rejected_count += 1
            else:
                normalized_events.append(normalized)
        return self._record_normalized_visual_events(
            normalized_events,
            rejected_count=rejected_count,
        )

    def _record_normalized_visual_events(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        rejected_count: int,
    ) -> int:
        """Write visual projections that have already crossed the allowlist."""

        component = self.manifest.components["visual_activation_events"]
        if not component.enabled:
            self.record_outcome(
                "visual_component_disabled",
                component="visual_activation_events",
            )
            return 0
        if rejected_count:
            self.record_outcome(
                "visual_event_rejected",
                component="visual_activation_events",
            )
        recorded = 0
        for normalized in events:
            self._emit(
                "visual_event",
                visual_event={
                    "component_schema_version": component.schema_version,
                    "audit_ids": list(component.audit_ids),
                    **normalized,
                },
                authority=_observation_only_authority(),
            )
            recorded += 1
        return recorded

    def record_normalized_component(
        self,
        name: str,
        payload: Mapping[str, Any],
    ) -> bool:
        """Record a future promoted component only through its manifest schema."""

        try:
            normalized_name = _safe_code(name, "component_name")
        except PlayerSaveAuditError:
            self.record_outcome(
                "normalized_component_rejected",
                component="optional_component",
            )
            return False
        component = self.manifest.components.get(normalized_name)
        if component is None:
            self.record_outcome(
                "normalized_component_unknown",
                component="optional_component",
            )
            return False
        if normalized_name in {
            "core_runtime",
            "perk_id_calibration",
            "visual_activation_events",
        }:
            self.record_outcome(
                "normalized_component_reserved",
                component="optional_component",
            )
            return False
        if not component.enabled:
            self.record_outcome(
                component.unavailable_reason,
                component=normalized_name,
            )
            return False
        try:
            normalized = _normalize_manifest_component(payload, component)
        except PlayerSaveAuditError:
            self.record_outcome(
                "normalized_component_rejected",
                component=normalized_name,
            )
            return False
        return self._emit(
            "normalized_component",
            component={
                "name": normalized_name,
                "schema_version": component.schema_version,
                "audit_ids": list(component.audit_ids),
                "evidence": normalized,
            },
            authority=_observation_only_authority(),
        )

    def _record_perk_id_mapping(self, payload: Mapping[str, Any]) -> bool:
        """Record one internally resolved ID without exposing a public bypass."""

        component = self.manifest.components["perk_id_calibration"]
        if not component.enabled:
            self.record_outcome(
                component.unavailable_reason,
                component="perk_id_calibration",
            )
            return False
        try:
            normalized = _normalize_manifest_component(payload, component)
        except PlayerSaveAuditError:
            self.record_outcome(
                "perk_id_mapping_evidence_rejected",
                component="perk_id_calibration",
            )
            return False
        return self._emit(
            "normalized_component",
            component={
                "name": "perk_id_calibration",
                "schema_version": component.schema_version,
                "audit_ids": list(component.audit_ids),
                "evidence": normalized,
            },
            authority=_observation_only_authority(),
        )

    def _accept_same_identity_revision(
        self,
        normalized: Mapping[str, Any],
    ) -> tuple[bool, str, list[dict[str, str]]]:
        outcomes: list[dict[str, str]] = []
        revision = normalized["save_revision"]
        wave = normalized["saved_wave"]
        if self._last_active_revision is None or self._last_active_wave is None:
            outcomes.append(_outcome("active_revision_state_unavailable", "capture"))
            return False, "fail_closed_revision_state_unavailable", outcomes
        if revision <= self._last_active_revision:
            outcomes.append(_outcome("save_revision_regression", "capture"))
            return False, "fail_closed_revision_not_newer", outcomes
        if wave < self._last_active_wave:
            outcomes.append(_outcome("saved_wave_regression", "round"))
            return False, "fail_closed_saved_wave_regression", outcomes
        if normalized["source_fingerprint"] == self._last_active_source_fingerprint:
            outcomes.append(_outcome("revision_source_inconsistent", "capture"))
            return False, "fail_closed_revision_source_inconsistent", outcomes
        self._last_active_revision = revision
        self._last_active_wave = wave
        self._last_active_source_fingerprint = normalized["source_fingerprint"]
        return True, "same_identity_newer_revision", outcomes

    def _reconcile_perks(
        self,
        normalized: Mapping[str, Any],
        *,
        accepted_active: bool,
        is_terminal: bool,
        terminal_revision_valid: bool,
        outcomes: list[dict[str, str]],
    ) -> dict[str, Any]:
        perks = normalized["perks"]
        payload: dict[str, Any] = {
            "status": perks["status"],
            "reason_code": perks["reason_code"],
            "state": perks.get("state"),
            "picked_count": perks.get("picked_count"),
            "fingerprint": perks.get("fingerprint"),
            "progression": {"status": "not_evaluated", "delta": []},
            "last_complete_same_identity": None,
        }
        if perks["status"] != "observed":
            outcomes.append(_outcome("perk_component_unavailable", "perks"))
            payload["progression"] = {
                "status": "fail_closed_component_unavailable",
                "delta": [],
            }
            return payload

        if normalized["round_active"]:
            if perks.get("state") != "active_round":
                outcomes.append(_outcome("active_perk_state_malformed", "perks"))
                payload["progression"] = {
                    "status": "fail_closed_state_malformed",
                    "delta": [],
                }
                return payload
            if not accepted_active:
                payload["progression"] = {
                    "status": "rejected_with_round_observation",
                    "delta": [],
                }
                return payload
            picks = tuple(_perk_pick_tuple(item) for item in perks["picks"])
            if self._last_perk_picks is None:
                delta = list(perks["picks"])
                progression_status = "initial_complete_checkpoint"
            elif picks[: len(self._last_perk_picks)] != self._last_perk_picks:
                outcomes.append(_outcome("perk_progression_non_prefix", "perks"))
                payload["progression"] = {
                    "status": "fail_closed_non_prefix",
                    "prior_picked_count": len(self._last_perk_picks),
                    "delta": [],
                }
                return payload
            else:
                delta = list(perks["picks"][len(self._last_perk_picks) :])
                progression_status = (
                    "complete_prefix_delta" if delta else "complete_unchanged_prefix"
                )
            prior_count = len(self._last_perk_picks or ())
            self._last_perk_picks = picks
            self._last_complete_perks = {
                "save_revision": normalized["save_revision"],
                "saved_wave": normalized["saved_wave"],
                "picked_count": perks["picked_count"],
                "fingerprint": perks["fingerprint"],
            }
            payload["progression"] = {
                "status": progression_status,
                "prior_picked_count": prior_count,
                "saved_wave": normalized["saved_wave"],
                "delta": delta,
            }
            return payload

        if not is_terminal:
            return payload
        if not terminal_revision_valid:
            payload["progression"] = {
                "status": "terminal_revision_rejected",
                "delta": [],
            }
            return payload
        if perks.get("state") == "cleared":
            if self._active_identity_fingerprint is None:
                payload["progression"] = {
                    "status": "cleared_without_session_round",
                    "delta": [],
                }
                return payload
            payload["last_complete_same_identity"] = (
                dict(self._last_complete_perks)
                if self._last_complete_perks is not None
                else None
            )
            if not self._first_clearing_recorded:
                self._first_clearing_recorded = True
                payload["progression"] = {
                    "status": "first_cleared_projection",
                    "delta": [],
                }
            else:
                payload["progression"] = {
                    "status": "subsequent_cleared_projection",
                    "delta": [],
                }
            if self._last_complete_perks is None:
                outcomes.append(
                    _outcome("last_complete_same_identity_perks_unavailable", "perks")
                )
            return payload
        if perks.get("state") == "post_round_retained":
            payload["progression"] = {
                "status": "post_round_projection_not_merged",
                "delta": [],
            }
        else:
            outcomes.append(_outcome("terminal_perk_state_malformed", "perks"))
            payload["progression"] = {
                "status": "fail_closed_state_malformed",
                "delta": [],
            }
        return payload

    def _compare_terminal_tail(
        self,
        history: Mapping[str, Any],
        *,
        round_active: bool,
        terminal_revision_valid: bool,
        outcomes: list[dict[str, str]],
    ) -> dict[str, Any]:
        if round_active:
            return {"status": "terminal_save_still_active"}
        if not terminal_revision_valid:
            return {"status": "session_round_unavailable"}
        if self._active_identity_discontinuous:
            outcomes.append(_outcome("terminal_identity_continuity_failed", "round"))
            return {"status": "identity_continuity_failed"}
        if self._baseline is None:
            outcomes.append(_outcome("terminal_baseline_unavailable", "history_tail"))
            return {"status": "baseline_unavailable"}
        if history["structural_status"] not in {"observed", "empty"}:
            outcomes.append(_outcome("terminal_tail_unavailable", "history_tail"))
            return {"status": "tail_unavailable"}
        if history["capacity"] != self._baseline["capacity"]:
            outcomes.append(_outcome("terminal_tail_capacity_changed", "history_tail"))
            return {"status": "capacity_changed"}

        current_fingerprint = history["fingerprint"]
        if current_fingerprint == self._baseline["fingerprint"]:
            outcomes.append(_outcome("terminal_tail_unchanged", "history_tail"))
            return {
                "status": "unchanged",
                "baseline_fingerprint": self._baseline["fingerprint"],
            }
        baseline_count = self._baseline["entry_count"]
        current_count = history["entry_count"]
        capacity = history["capacity"]
        rollover = baseline_count == capacity and current_count == capacity
        expected_count = capacity if baseline_count >= capacity else baseline_count + 1
        if current_count != expected_count:
            outcomes.append(
                _outcome("terminal_tail_count_transition_invalid", "history_tail")
            )
            return {
                "status": "fail_closed_count_transition",
                "baseline_entry_count": baseline_count,
                "observed_entry_count": current_count,
                "capacity": capacity,
            }
        if self._terminal_candidate_fingerprint == current_fingerprint:
            status = "candidate_tail_change_reobserved"
        else:
            status = "candidate_tail_change"
            self._terminal_candidate_fingerprint = current_fingerprint
        return {
            "status": status,
            "candidate_only": True,
            "baseline_fingerprint": self._baseline["fingerprint"],
            "observed_fingerprint": current_fingerprint,
            "baseline_entry_count": baseline_count,
            "observed_entry_count": current_count,
            "capacity": capacity,
            "capacity_rollover": rollover,
            "semantic_completed_entry_status": history["semantic_completed_entry"][
                "status"
            ],
        }

    def _retain_retry_round_baseline(
        self,
        normalized: Mapping[str, Any],
        *,
        request: AuditRequest,
        baseline_comparison: Mapping[str, Any],
    ) -> None:
        """Retain one session-bound terminal tail for a possible direct Retry."""

        if _boundary_label(request.boundary_label) != "GAME_OVER":
            return
        terminal_evidence_status = baseline_comparison.get("status")
        if terminal_evidence_status not in {
            "baseline_unavailable",
            "candidate_tail_change",
            "candidate_tail_change_reobserved",
        }:
            return
        boundary_at = request.boundary_observed_at
        if boundary_at is None:
            return
        baseline = _baseline_from_tail(normalized["history_tail"])
        if baseline is None:
            return
        self._pending_retry_round = {
            "baseline": baseline,
            "target_fingerprint": normalized["target_fingerprint"],
            "terminal_save_revision": normalized["save_revision"],
            "terminal_source_fingerprint": normalized["source_fingerprint"],
            "terminal_boundary_observed_at": boundary_at,
            "terminal_evidence_status": terminal_evidence_status,
        }

    def _begin_proven_retry_round(
        self,
        normalized: Mapping[str, Any],
        *,
        request: AuditRequest,
    ) -> Optional[dict[str, Any]]:
        """Reset round-local state only across a proven terminal -> Retry edge."""

        pending = self._pending_retry_round
        identity = normalized["active_identity"]
        running_at = request.boundary_observed_at
        if pending is None or identity is None or running_at is None:
            return None
        if running_at <= pending["terminal_boundary_observed_at"]:
            return None
        if normalized["target_fingerprint"] != pending["target_fingerprint"]:
            return None
        if normalized["save_revision"] <= pending["terminal_save_revision"]:
            return None
        if normalized["source_fingerprint"] == pending["terminal_source_fingerprint"]:
            return None
        if identity["fingerprint"] == self._active_identity_fingerprint:
            return None

        observed_baseline = _baseline_from_tail(normalized["history_tail"])
        if observed_baseline != pending["baseline"]:
            return None

        baseline = dict(pending["baseline"])
        terminal_save_revision = pending["terminal_save_revision"]
        terminal_evidence_status = pending["terminal_evidence_status"]
        self._reset_round_state()
        self._baseline = baseline
        return {
            "status": "terminal_retry_baseline_carried",
            "fingerprint": baseline["fingerprint"],
            "entry_count": baseline["entry_count"],
            "capacity": baseline["capacity"],
            "terminal_save_revision": terminal_save_revision,
            "terminal_evidence_status": terminal_evidence_status,
            "transition": "passive_game_over_to_running",
        }

    def _remember_observation(
        self,
        key: tuple[int, str],
    ) -> None:
        self._seen_observations.add(key)
        self._seen_observation_order.append(key)
        if len(self._seen_observation_order) > 4096:
            expired = self._seen_observation_order.pop(0)
            self._seen_observations.discard(expired)

    def _remember_mapping_context_discontinuity(
        self,
        key: tuple[str, str, int, int, str],
    ) -> None:
        self._seen_mapping_context_discontinuities.add(key)
        self._seen_mapping_context_discontinuity_order.append(key)
        if len(self._seen_mapping_context_discontinuity_order) > 4096:
            expired = self._seen_mapping_context_discontinuity_order.pop(0)
            self._seen_mapping_context_discontinuities.discard(expired)

    def _reset_round_state(self) -> None:
        self._baseline = None
        self._active_identity_fingerprint = None
        self._active_identity_discontinuous = False
        self._last_active_revision = None
        self._last_active_wave = None
        self._last_active_source_fingerprint = None
        self._last_terminal_revision = None
        self._last_terminal_source_fingerprint = None
        self._last_perk_picks = None
        self._last_complete_perks = None
        self._first_clearing_recorded = False
        self._terminal_candidate_fingerprint = None
        self._pending_retry_round = None

    def _emit(self, record_type: str, **payload: Any) -> bool:
        self._sequence += 1
        record = {
            "schema_version": PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_VERSION,
            "schema_id": PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_ID,
            "record_type": record_type,
            "record_id": f"{self.collector_session_id}:{self._sequence}",
            "sequence": self._sequence,
            "recorded_at": _utc_iso(self._now_fn()),
            "runtime_session_id": self.runtime_session_id,
            "collector_session_id": self.collector_session_id,
            **payload,
        }
        try:
            _validate_receipt(record)
            self._receipt_sink(record)
            return True
        except Exception:
            if self._write_failure_fn is not None:
                try:
                    self._write_failure_fn()
                except Exception:
                    pass
            return False


class PlayerSaveAuditCollector:
    """Nonblocking audit facade backed by one bounded projection worker."""

    def __init__(
        self,
        *,
        enabled: bool,
        receipt_path: Path | str = DEFAULT_PLAYER_SAVE_AUDIT_RECEIPT_PATH,
        manifest_path: Path | str = DEFAULT_PLAYER_SAVE_AUDIT_MANIFEST_PATH,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = bool(enabled)
        self._now_fn = now_fn
        self._monotonic_fn = monotonic_fn
        self._commands: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_CAPACITY)
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        self._boundary_lock = threading.Lock()
        self._last_boundary_label: Optional[str] = None
        self._last_boundary_observed_at: Optional[datetime] = None
        self._warning_times: dict[str, float] = {}
        self._active_failure_code: Optional[str] = None
        self._state_machine: Optional[PlayerSaveAuditStateMachine] = None
        self._perk_mapping_batches: list[Mapping[str, Any]] = []
        self._perk_id_overrides: dict[int, str] = {}
        self._perk_mapping_scope: Optional[tuple[str, int]] = None
        if not self.enabled:
            return

        try:
            manifest = load_player_save_audit_manifest(manifest_path)
            writer = AppendOnlyAuditReceiptWriter(receipt_path)
            self._state_machine = PlayerSaveAuditStateMachine(
                manifest,
                receipt_sink=writer.append,
                now_fn=now_fn,
                write_failure_fn=self._receipt_write_failed,
            )
        except Exception:
            self.enabled = False
            self._rate_limited_warning("collector_initialization_failed")
            return

        self._thread = threading.Thread(
            target=self._worker,
            name="player-save-audit",
            daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            self.enabled = False
            self._rate_limited_warning("collector_worker_start_failed")

    def observe_screen(
        self,
        detection: Mapping[str, Any],
        *,
        observed_at: Optional[datetime] = None,
    ) -> None:
        """Queue one changed passive boundary; never return a dispatch decision."""

        if not self.enabled or self._closed:
            return
        label = _boundary_from_detection(detection)
        if label is None:
            return
        when = _aware_datetime(observed_at or self._now_fn(), "observed_at")
        with self._boundary_lock:
            if label == self._last_boundary_label:
                return
            self._last_boundary_label = label
            self._last_boundary_observed_at = when
        request = AuditRequest(
            reasons=(_BOUNDARY_REASON_CODES[label],),
            requested_at=when,
            boundary_label=label,
            boundary_observed_at=when,
        )
        self._enqueue(_BoundaryCommand(request))

    def observe_acquisition(
        self,
        acquisition: PlayerSaveAcquisitionBundle,
        *,
        reason_code: str = "shared_bundle",
        requested_at: Optional[datetime] = None,
    ) -> None:
        """Queue one already-acquired shared bundle for audit projection."""

        if not self.enabled or self._closed:
            return
        if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
            self._enqueue(_OutcomeCommand("typed_acquisition_required", "acquisition"))
            return
        reason = _safe_code(reason_code, "request_reason")
        with self._boundary_lock:
            label = self._last_boundary_label
            boundary_at = self._last_boundary_observed_at
        request = AuditRequest(
            reasons=(reason,),
            requested_at=_aware_datetime(
                requested_at or acquisition.acquisition_started_at,
                "requested_at",
            ),
            boundary_label=label,
            boundary_observed_at=boundary_at,
        )
        self._enqueue(_ExternalAcquisitionCommand(acquisition, request))

    def observe_visual_events(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        if not self.enabled or self._closed or not events:
            return
        normalized_events: list[dict[str, Any]] = []
        rejected_count = 0
        for event in events:
            if not isinstance(event, Mapping):
                rejected_count += 1
                continue
            normalized = _normalize_visual_event(event)
            if normalized is None:
                rejected_count += 1
            else:
                normalized_events.append(normalized)
        if normalized_events or rejected_count:
            self._enqueue(_VisualCommand(tuple(normalized_events), rejected_count))

    def observe_perk_mapping_evidence(
        self,
        batches: Sequence[Mapping[str, Any]],
    ) -> None:
        """Queue only exact, privacy-safe UI batches for passive correlation."""

        if not self.enabled or self._closed or not batches:
            return
        if isinstance(batches, (str, bytes, bytearray)):
            self._enqueue(
                _OutcomeCommand(
                    "perk_id_mapping_evidence_rejected",
                    "perk_id_calibration",
                )
            )
            return
        normalized: list[dict[str, Any]] = []
        rejected = False
        for index, batch in enumerate(batches):
            if index >= _MAX_PERK_MAPPING_BATCHES_PER_COMMAND:
                rejected = True
                break
            safe_batch = normalize_timeline_mapping_batch(batch)
            if safe_batch is None:
                rejected = True
                continue
            normalized.append(safe_batch)
        if rejected:
            self._enqueue(
                _OutcomeCommand(
                    "perk_id_mapping_evidence_rejected",
                    "perk_id_calibration",
                )
            )
        if normalized:
            self._enqueue(_PerkMappingCommand(tuple(normalized)))

    def reset_perk_mapping_evidence(self) -> None:
        """End one UI/save correlation window without discarding learned IDs."""

        if not self.enabled or self._closed:
            return
        self._enqueue(_PerkMappingResetCommand())

    def observe_normalized_component(
        self,
        name: str,
        payload: Mapping[str, Any],
    ) -> None:
        if not self.enabled or self._closed:
            return
        try:
            safe_name = _safe_code(name, "component_name")
        except PlayerSaveAuditError:
            self._enqueue(
                _OutcomeCommand(
                    "normalized_component_rejected",
                    "optional_component",
                )
            )
            return
        state = self._state_machine
        component = state.manifest.components.get(safe_name) if state else None
        if component is None:
            self._enqueue(
                _OutcomeCommand(
                    "normalized_component_unknown",
                    "optional_component",
                )
            )
            return
        if safe_name in {
            "core_runtime",
            "perk_id_calibration",
            "visual_activation_events",
        }:
            self._enqueue(
                _OutcomeCommand(
                    "normalized_component_reserved",
                    "optional_component",
                )
            )
            return
        if not component.enabled:
            self._enqueue(_OutcomeCommand(component.unavailable_reason, safe_name))
            return
        try:
            normalized = _normalize_manifest_component(payload, component)
        except PlayerSaveAuditError:
            self._enqueue(_OutcomeCommand("normalized_component_rejected", safe_name))
            return
        self._enqueue(_ComponentCommand(safe_name, normalized))

    def close(self, *, wait: bool = False, timeout: float = 1.0) -> None:
        """Stop scheduling work; App shutdown never waits on an active ADB read."""

        if self._closed:
            return
        self._closed = True
        if self.enabled:
            self._enqueue(_StopCommand(), allow_closed=True)
        thread = self._thread
        if wait and thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))

    def wait_until_idle(self, timeout: float = 2.0) -> bool:
        """Test/inspection helper that never participates in App dispatch."""

        deadline = self._monotonic_fn() + max(0.0, float(timeout))
        while self._monotonic_fn() < deadline:
            if self._commands.unfinished_tasks == 0:
                return True
            time.sleep(0.005)
        return self._commands.unfinished_tasks == 0

    def _worker(self) -> None:
        state = self._state_machine
        if state is None:
            return
        state.start_session()
        log(
            "[PLAYER_SAVE_AUDIT] Observation-only collector enabled; "
            "source=shared bundles",
            "INFO",
        )
        while True:
            command = self._commands.get()

            try:
                if isinstance(command, _StopCommand):
                    return
                if isinstance(command, _BoundaryCommand):
                    if _starts_perk_mapping_round(command.request):
                        self._perk_mapping_batches.clear()
                    state.record_boundary(command.request)
                elif isinstance(command, _ExternalAcquisitionCommand):
                    self._process_acquisition(
                        command.request,
                        command.acquisition,
                    )
                elif isinstance(command, _VisualCommand):
                    state._record_normalized_visual_events(
                        command.events,
                        rejected_count=command.rejected_count,
                    )
                elif isinstance(command, _ComponentCommand):
                    state.record_normalized_component(
                        command.name,
                        command.payload,
                    )
                elif isinstance(command, _OutcomeCommand):
                    state.record_outcome(
                        command.code,
                        component=command.component,
                    )
                elif isinstance(command, _PerkMappingCommand):
                    self._append_perk_mapping_batches(command.batches, state)
                elif isinstance(command, _PerkMappingResetCommand):
                    self._perk_mapping_batches.clear()
            except Exception:
                self._rate_limited_warning("collector_worker_failed")
            finally:
                self._commands.task_done()

    def _process_acquisition(
        self,
        request: AuditRequest,
        acquisition: PlayerSaveAcquisitionBundle,
    ) -> None:
        state = self._state_machine
        if state is None:
            return
        binding = acquisition.binding
        target_fingerprint = (
            binding.fingerprint if binding is not None else "unavailable"
        )
        if acquisition.status in {
            PlayerSaveAcquisitionStatus.BINDING_REJECTED,
            PlayerSaveAcquisitionStatus.BINDING_LOST,
        }:
            self._sync_perk_mapping_scope(
                binding.private_key if binding is not None else None
            )
            state.record_outcome(
                "target_handoff_discarded",
                request=request,
                target_fingerprint=target_fingerprint,
                component="acquisition",
            )
            log(
                "[PLAYER_SAVE_AUDIT] Discarded a stable observation because "
                "the live ADB target ownership changed",
                "INFO",
            )
            return
        if binding is None:
            state.record_outcome("exact_target_unavailable", request=request)
            self._rate_limited_warning("exact_target_unavailable")
            return
        self._sync_perk_mapping_scope(binding.private_key)
        if not acquisition.complete or acquisition.snapshot is None:
            if acquisition.reason == "stable_read_unavailable":
                code = "stable_read_unavailable"
                component = "acquisition"
            elif acquisition.reason in {
                "decoder_unavailable",
                "save_mapping_unavailable",
            }:
                code = "decoder_unavailable"
                component = "decoder"
            else:
                code = "collector_acquisition_failed"
                component = "acquisition"
            state.record_outcome(
                code,
                request=request,
                target_fingerprint=target_fingerprint,
                component=component,
            )
            self._rate_limited_warning(code)
            return

        snapshot = acquisition.snapshot
        try:
            runtime = snapshot.runtime_save
            mapping_supported = snapshot.mapping_supported
            shape_valid = snapshot.shape_valid
            game_version = snapshot.game_version
        except Exception:
            state.record_outcome(
                "runtime_projection_unavailable",
                request=request,
                target_fingerprint=target_fingerprint,
                component="decoder",
            )
            self._rate_limited_warning("runtime_projection_unavailable")
            return
        if runtime is None:
            if not mapping_supported:
                code = "unsupported_runtime_mapping"
            elif not shape_valid:
                code = "save_shape_unavailable"
            else:
                code = "runtime_projection_unavailable"
            state.record_outcome(
                code,
                request=request,
                target_fingerprint=target_fingerprint,
                component="decoder",
            )
            self._rate_limited_warning(code)
            return
        resolution = None
        mapping_context_matches = bool(
            game_version == state.manifest.game_version
            and runtime.mapping_id == state.manifest.mapping_id
            and runtime.audit_matrix_id == state.manifest.audit_matrix_id
        )
        if mapping_context_matches:
            try:
                resolution = resolve_runtime_perk_ids(
                    runtime,
                    self._perk_mapping_batches,
                    self._perk_id_overrides,
                )
            except Exception:
                state.record_outcome(
                    "perk_id_mapping_resolution_failed",
                    request=request,
                    target_fingerprint=target_fingerprint,
                    component="perk_id_calibration",
                )
        if resolution is not None:
            accepted_overrides = dict(resolution.overrides)
            for evidence in resolution.learned:
                accepted_overrides.pop(evidence.perk_id, None)
            for evidence in resolution.learned:
                if state._record_perk_id_mapping(
                    evidence.component_payload(game_version=game_version)
                ):
                    accepted_overrides[evidence.perk_id] = evidence.perk_key
            if resolution.conflicts:
                state.record_outcome(
                    "perk_id_mapping_conflict",
                    request=request,
                    target_fingerprint=target_fingerprint,
                    component="perk_id_calibration",
                )
            self._perk_id_overrides = dict(sorted(accepted_overrides.items()))
            runtime = runtime_with_perk_id_overrides(
                runtime,
                self._perk_id_overrides,
            )

        observation = _audit_observation_from_runtime(
            runtime,
            game_version=game_version,
            target_fingerprint=target_fingerprint,
            acquisition_started_at=acquisition.acquisition_started_at,
            acquisition_completed_at=acquisition.acquisition_completed_at,
        )
        state.observe_save(observation, request)
        if self._active_failure_code is not None:
            log(
                "[PLAYER_SAVE_AUDIT] Stable observation recovered; passive "
                "collection continues",
                "INFO",
            )
            self._active_failure_code = None

    def _sync_perk_mapping_scope(
        self,
        scope: Optional[tuple[str, int]],
    ) -> None:
        if scope == self._perk_mapping_scope:
            return
        self._perk_mapping_scope = scope
        self._perk_mapping_batches.clear()
        self._perk_id_overrides.clear()

    def _append_perk_mapping_batches(
        self,
        batches: Sequence[Mapping[str, Any]],
        state: PlayerSaveAuditStateMachine,
    ) -> None:
        self._perk_mapping_batches.extend(batches)
        overflow = len(self._perk_mapping_batches) - _MAX_PERK_MAPPING_BATCHES
        if overflow <= 0:
            return
        del self._perk_mapping_batches[:overflow]
        state.record_outcome(
            "perk_id_mapping_evidence_truncated",
            component="perk_id_calibration",
        )

    def _enqueue(self, command: Any, *, allow_closed: bool = False) -> None:
        if (self._closed and not allow_closed) or not self.enabled:
            return
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            self._rate_limited_warning("collector_queue_full")

    def _receipt_write_failed(self) -> None:
        self._rate_limited_warning("receipt_write_failed")

    def _rate_limited_warning(self, code: str) -> None:
        safe_code = _safe_code(code, "warning_code")
        now = self._monotonic_fn()
        last = self._warning_times.get(safe_code)
        if last is None or now - last >= _WARNING_REMINDER_SECONDS:
            log(
                "[PLAYER_SAVE_AUDIT] Passive collector degradation="
                f"{safe_code}; normal automation is unaffected",
                "WARN",
            )
            self._warning_times[safe_code] = now
        else:
            log(
                f"[PLAYER_SAVE_AUDIT] Receipt retry outcome={safe_code}",
                "DEBUG",
            )
        self._active_failure_code = safe_code


def _audit_observation_from_runtime(
    runtime: NormalizedRuntimeSave,
    *,
    game_version: Optional[int],
    target_fingerprint: str,
    acquisition_started_at: datetime,
    acquisition_completed_at: datetime,
) -> AuditSaveObservation:
    if type(game_version) is not int or game_version < 0:
        raise PlayerSaveAuditError("game_version_unavailable")
    capture = runtime.capture
    captured_at = _parse_aware_timestamp(capture.get("captured_at"), "captured_at")
    identity = runtime.active_round_identity
    active_identity = (
        {
            "fingerprint": _sha256(identity.fingerprint, "identity_fingerprint"),
            "game_version": _nonnegative_int(
                identity.game_version,
                "identity_game_version",
            ),
            "tier": _nonnegative_int(identity.current_tier, "identity_tier"),
            "per_tier_counter": _nonnegative_int(
                identity.rounds_started_this_tier,
                "identity_per_tier_counter",
            ),
            "seed": _nonnegative_int(identity.round_seed, "identity_seed"),
        }
        if identity is not None
        else None
    )

    if runtime.perks is None:
        perks: dict[str, Any] = {
            "status": "unavailable",
            "reason_code": _reason_code(runtime.perks_reason),
            "state": None,
            "picked_count": None,
            "fingerprint": None,
            "picks": [],
        }
    elif len(runtime.perks.picks) > MAX_AUDIT_PERK_PICKS:
        perks = {
            "status": "unavailable",
            "reason_code": "perk_pick_count_exceeds_audit_bound",
            "state": None,
            "picked_count": None,
            "fingerprint": None,
            "picks": [],
        }
    else:
        perks = {
            "status": "observed",
            "reason_code": "available",
            "state": _safe_code(runtime.perks.state, "perk_state"),
            "picked_count": _nonnegative_int(
                runtime.perks.picked_count,
                "perk_picked_count",
            ),
            "fingerprint": _sha256(
                runtime.perks.fingerprint,
                "perk_fingerprint",
            ),
            "picks": [
                {
                    "sequence": _positive_int(pick.sequence, "perk_sequence"),
                    "saved_wave": _nonnegative_int(pick.wave, "perk_saved_wave"),
                    "perk_id": _nonnegative_int(pick.perk_id, "perk_id"),
                    "perk_key": _safe_code(pick.perk_key, "perk_key"),
                    "level_after": _positive_int(
                        pick.level_after,
                        "perk_level_after",
                    ),
                }
                for pick in runtime.perks.picks
            ],
        }

    tail = runtime.battle_history_tail
    newest_identity = None
    if tail.identity is not None:
        date = tail.identity.battle_date
        newest_identity = {
            "battle_date": {
                "kind_id": _nonnegative_int(date.get("kind_id"), "date_kind_id"),
                "kind": _safe_code(date.get("kind"), "date_kind"),
                "ticks": _decimal_digits(date.get("ticks"), "date_ticks"),
                "clock_time": _bounded_text(
                    date.get("clock_time"),
                    "date_clock_time",
                    64,
                ),
                "clock_basis": _safe_code(
                    date.get("clock_basis"),
                    "date_clock_basis",
                ),
                "submicrosecond_100ns": _nonnegative_int(
                    date.get("submicrosecond_100ns"),
                    "date_submicrosecond",
                ),
            },
            "tier": _positive_int(tail.identity.tier, "history_tier"),
            "wave": _positive_int(tail.identity.wave, "history_wave"),
            "game_time_seconds": _nonnegative_finite_number(
                tail.identity.game_time_seconds,
                "history_game_time",
            ),
            "real_time_seconds": _nonnegative_finite_number(
                tail.identity.real_time_seconds,
                "history_real_time",
            ),
            "killed_by_id": _nonnegative_int(
                tail.identity.killed_by_id,
                "history_killed_by_id",
            ),
            "is_tournament": _strict_bool(
                tail.identity.is_tournament,
                "history_is_tournament",
            ),
        }
    history_tail = {
        "structural_status": _safe_code(
            tail.structural_status,
            "tail_structural_status",
        ),
        "structural_reason_code": _reason_code(tail.structural_reason),
        "entry_count": _nonnegative_int(tail.entry_count, "tail_entry_count"),
        "capacity": _positive_int(tail.capacity, "tail_capacity"),
        "fingerprint": (
            _sha256(tail.structural_fingerprint, "tail_fingerprint")
            if tail.structural_fingerprint is not None
            else None
        ),
        "newest_identity": newest_identity,
        "semantic_completed_entry": {
            "status": _safe_code(
                tail.completed_entry_status,
                "semantic_entry_status",
            ),
            "reason_code": _reason_code(tail.completed_entry_reason),
            "fingerprint": (
                _sha256(
                    tail.completed_entry_fingerprint,
                    "semantic_entry_fingerprint",
                )
                if tail.completed_entry_fingerprint is not None
                else None
            ),
        },
    }
    return AuditSaveObservation(
        mapping_id=_safe_id(runtime.mapping_id, "mapping_id"),
        audit_matrix_id=_safe_id(runtime.audit_matrix_id, "audit_matrix_id"),
        game_version=game_version,
        captured_at=captured_at,
        source_fingerprint=_sha256(
            capture.get("source_sha256"),
            "source_fingerprint",
        ),
        save_revision=_nonnegative_int(runtime.save_revision, "save_revision"),
        round_active=_strict_bool(runtime.round_active, "round_active"),
        saved_wave=_nonnegative_int(runtime.current_wave, "saved_wave"),
        active_identity=active_identity,
        perks=perks,
        history_tail=history_tail,
        target_fingerprint=_sha256(target_fingerprint, "target_fingerprint"),
        acquisition_started_at=_aware_datetime(
            acquisition_started_at,
            "acquisition_started_at",
        ),
        acquisition_completed_at=_aware_datetime(
            acquisition_completed_at,
            "acquisition_completed_at",
        ),
    )


def _validated_observation(
    observation: AuditSaveObservation,
    manifest: PlayerSaveAuditManifest,
) -> dict[str, Any]:
    mapping_id = _safe_id(observation.mapping_id, "mapping_id")
    audit_matrix_id = _safe_id(observation.audit_matrix_id, "audit_matrix_id")
    if audit_matrix_id != manifest.audit_matrix_id:
        raise PlayerSaveAuditError("observation_audit_matrix_mismatch")
    game_version = _nonnegative_int(observation.game_version, "game_version")
    round_active = _strict_bool(observation.round_active, "round_active")
    identity = _normalize_active_identity(observation.active_identity)
    if identity is not None and identity["game_version"] != game_version:
        raise PlayerSaveAuditError("active_identity_game_version_mismatch")
    if round_active and identity is None:
        raise PlayerSaveAuditError("active_observation_missing_identity")
    if not round_active and identity is not None:
        raise PlayerSaveAuditError("inactive_observation_has_identity")
    perks = _normalize_perks_payload(observation.perks)
    history = _normalize_history_payload(observation.history_tail)
    return {
        "mapping_id": mapping_id,
        "audit_matrix_id": audit_matrix_id,
        "game_version": game_version,
        "captured_at": _aware_datetime(observation.captured_at, "captured_at"),
        "source_fingerprint": _sha256(
            observation.source_fingerprint,
            "source_fingerprint",
        ),
        "save_revision": _nonnegative_int(
            observation.save_revision,
            "save_revision",
        ),
        "round_active": round_active,
        "saved_wave": _nonnegative_int(observation.saved_wave, "saved_wave"),
        "active_identity": identity,
        "perks": perks,
        "history_tail": history,
        "target_fingerprint": _sha256(
            observation.target_fingerprint,
            "target_fingerprint",
        ),
        "acquisition_started_at": _aware_datetime(
            observation.acquisition_started_at,
            "acquisition_started_at",
        ),
        "acquisition_completed_at": _aware_datetime(
            observation.acquisition_completed_at,
            "acquisition_completed_at",
        ),
    }


def _normalize_active_identity(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PlayerSaveAuditError("active_identity_changed_shape")
    return {
        "fingerprint": _sha256(value.get("fingerprint"), "identity_fingerprint"),
        "game_version": _nonnegative_int(
            value.get("game_version"),
            "identity_game_version",
        ),
        "tier": _nonnegative_int(value.get("tier"), "identity_tier"),
        "per_tier_counter": _nonnegative_int(
            value.get("per_tier_counter"),
            "identity_per_tier_counter",
        ),
        "seed": _nonnegative_int(value.get("seed"), "identity_seed"),
    }


def _normalize_perks_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlayerSaveAuditError("perk_component_changed_shape")
    status = _safe_code(value.get("status"), "perk_status")
    reason_code = _reason_code(value.get("reason_code"))
    if status != "observed":
        return {
            "status": "unavailable",
            "reason_code": reason_code,
            "state": None,
            "picked_count": None,
            "fingerprint": None,
            "picks": [],
        }
    state = _safe_code(value.get("state"), "perk_state")
    picked_count = _nonnegative_int(value.get("picked_count"), "perk_count")
    fingerprint = _sha256(value.get("fingerprint"), "perk_fingerprint")
    raw_picks = value.get("picks")
    if not isinstance(raw_picks, Sequence) or isinstance(
        raw_picks,
        (str, bytes, bytearray),
    ):
        raise PlayerSaveAuditError("perk_picks_changed_shape")
    if len(raw_picks) > MAX_AUDIT_PERK_PICKS or len(raw_picks) != picked_count:
        raise PlayerSaveAuditError("perk_pick_count_invalid")
    picks: list[dict[str, Any]] = []
    for index, raw_pick in enumerate(raw_picks, start=1):
        if not isinstance(raw_pick, Mapping):
            raise PlayerSaveAuditError("perk_pick_changed_shape")
        pick = {
            "sequence": _positive_int(raw_pick.get("sequence"), "perk_sequence"),
            "saved_wave": _nonnegative_int(
                raw_pick.get("saved_wave"),
                "perk_saved_wave",
            ),
            "perk_id": _nonnegative_int(raw_pick.get("perk_id"), "perk_id"),
            "perk_key": _safe_code(raw_pick.get("perk_key"), "perk_key"),
            "level_after": _positive_int(
                raw_pick.get("level_after"),
                "perk_level_after",
            ),
        }
        if pick["sequence"] != index:
            raise PlayerSaveAuditError("perk_sequence_not_contiguous")
        picks.append(pick)
    return {
        "status": status,
        "reason_code": reason_code,
        "state": state,
        "picked_count": picked_count,
        "fingerprint": fingerprint,
        "picks": picks,
    }


def _normalize_history_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlayerSaveAuditError("history_tail_changed_shape")
    status = _safe_code(value.get("structural_status"), "tail_status")
    if status not in {"observed", "empty", "unavailable"}:
        raise PlayerSaveAuditError("tail_status_unsupported")
    entry_count = _nonnegative_int(value.get("entry_count"), "tail_entry_count")
    capacity = _positive_int(value.get("capacity"), "tail_capacity")
    if status in {"observed", "empty"} and entry_count > capacity:
        raise PlayerSaveAuditError("tail_count_exceeds_capacity")
    fingerprint = value.get("fingerprint")
    normalized_fingerprint = (
        _sha256(fingerprint, "tail_fingerprint") if fingerprint is not None else None
    )
    newest = value.get("newest_identity")
    normalized_newest = _normalize_newest_identity(newest)
    if status == "observed" and (
        normalized_fingerprint is None or normalized_newest is None
    ):
        raise PlayerSaveAuditError("observed_tail_identity_unavailable")
    if status == "empty" and (
        entry_count != 0
        or normalized_fingerprint is not None
        or normalized_newest is not None
    ):
        raise PlayerSaveAuditError("empty_tail_contains_identity")
    if status == "unavailable" and (
        normalized_fingerprint is not None or normalized_newest is not None
    ):
        raise PlayerSaveAuditError("unavailable_tail_contains_identity")
    semantic = value.get("semantic_completed_entry")
    if not isinstance(semantic, Mapping):
        raise PlayerSaveAuditError("semantic_tail_changed_shape")
    semantic_status = _safe_code(semantic.get("status"), "semantic_status")
    if semantic_status not in {"observed", "unavailable", "not_applicable"}:
        raise PlayerSaveAuditError("semantic_status_unsupported")
    semantic_fingerprint = semantic.get("fingerprint")
    normalized_semantic_fingerprint = (
        _sha256(semantic_fingerprint, "semantic_fingerprint")
        if semantic_fingerprint is not None
        else None
    )
    if semantic_status == "observed" and normalized_semantic_fingerprint is None:
        raise PlayerSaveAuditError("semantic_fingerprint_unavailable")
    if semantic_status != "observed" and normalized_semantic_fingerprint is not None:
        raise PlayerSaveAuditError("unavailable_semantic_fingerprint_present")
    return {
        "structural_status": status,
        "structural_reason_code": _reason_code(value.get("structural_reason_code")),
        "entry_count": entry_count,
        "capacity": capacity,
        "fingerprint": normalized_fingerprint,
        "newest_identity": normalized_newest,
        "semantic_completed_entry": {
            "status": semantic_status,
            "reason_code": _reason_code(semantic.get("reason_code")),
            "fingerprint": normalized_semantic_fingerprint,
        },
    }


def _normalize_newest_identity(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PlayerSaveAuditError("newest_identity_changed_shape")
    battle_date = value.get("battle_date")
    if not isinstance(battle_date, Mapping):
        raise PlayerSaveAuditError("newest_battle_date_changed_shape")
    return {
        "battle_date": {
            "kind_id": _nonnegative_int(
                battle_date.get("kind_id"),
                "date_kind_id",
            ),
            "kind": _safe_code(battle_date.get("kind"), "date_kind"),
            "ticks": _decimal_digits(battle_date.get("ticks"), "date_ticks"),
            "clock_time": _bounded_text(
                battle_date.get("clock_time"),
                "date_clock_time",
                64,
            ),
            "clock_basis": _safe_code(
                battle_date.get("clock_basis"),
                "date_clock_basis",
            ),
            "submicrosecond_100ns": _nonnegative_int(
                battle_date.get("submicrosecond_100ns"),
                "date_submicrosecond",
            ),
        },
        "tier": _positive_int(value.get("tier"), "history_tier"),
        "wave": _positive_int(value.get("wave"), "history_wave"),
        "game_time_seconds": _nonnegative_finite_number(
            value.get("game_time_seconds"),
            "history_game_time",
        ),
        "real_time_seconds": _nonnegative_finite_number(
            value.get("real_time_seconds"),
            "history_real_time",
        ),
        "killed_by_id": _nonnegative_int(
            value.get("killed_by_id"),
            "history_killed_by_id",
        ),
        "is_tournament": _strict_bool(
            value.get("is_tournament"),
            "history_is_tournament",
        ),
    }


def _normalize_visual_event(event: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    try:
        ability = _safe_code(event.get("ability"), "visual_ability")
        source = _safe_code(event.get("detection_source"), "visual_source")
        if ability not in _VISUAL_ABILITIES or source not in _VISUAL_SOURCES:
            return None
        sequence = _positive_int(event.get("sequence"), "visual_sequence")
        approximate_wave = event.get("approximate_wave")
        wave_value = (
            _nonnegative_int(approximate_wave, "visual_wave")
            if approximate_wave is not None
            else None
        )
        wave_confidence = _bounded_confidence(
            event.get("wave_confidence"),
            "visual_wave_confidence",
        )
        detected_at = _optional_timestamp(event.get("detected_at"))
        confirmed_at = _optional_timestamp(event.get("confirmed_at"))
        wave_observed_at = _optional_timestamp(event.get("wave_observed_at"))
        confirmation: dict[str, Any] = {
            "frames": _positive_int(
                event.get("confirmation_frames"),
                "visual_confirmation_frames",
            )
        }
        for source_key, receipt_key in (
            ("presence_confidence", "presence_confidence"),
            ("absence_confidence", "absence_confidence"),
            ("active_icon_confidence", "active_icon_confidence"),
        ):
            if event.get(source_key) is not None:
                confirmation[receipt_key] = _unit_confidence(
                    event.get(source_key),
                    source_key,
                )
        normalized: dict[str, Any] = {
            "ability": ability,
            "sequence": sequence,
            "detection_source": source,
            "detected_at": detected_at,
            "confirmed_at": confirmed_at,
            "wave": {
                "approximate_visual_observation": wave_value,
                "confidence_percent": wave_confidence,
                "observed_at": wave_observed_at,
                "semantics": "approximate_not_exact_activation_wave",
            },
            "confirmation": confirmation,
        }
        evidence_path = _relative_evidence_path(event.get("evidence_image"))
        if evidence_path is not None:
            normalized["evidence_image_reference"] = evidence_path
        return normalized
    except PlayerSaveAuditError:
        return None


def _normalize_manifest_component(
    payload: Mapping[str, Any],
    component: AuditComponentSpec,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != set(component.fields):
        raise PlayerSaveAuditError("normalized_component_changed_shape")
    normalized: dict[str, Any] = {}
    for field, rule in component.fields.items():
        kind = _safe_code(rule.get("type"), "component_field_type")
        value = payload.get(field)
        if kind == "nonnegative_int":
            normalized[field] = _nonnegative_int(value, field)
        elif kind == "positive_int":
            normalized[field] = _positive_int(value, field)
        elif kind == "sha256":
            normalized[field] = _sha256(value, field)
        elif kind == "boolean":
            normalized[field] = _strict_bool(value, field)
        elif kind == "safe_code":
            normalized[field] = _safe_code(value, field)
        elif kind == "enum":
            allowed = rule.get("values")
            if not isinstance(allowed, Sequence) or isinstance(
                allowed,
                (str, bytes, bytearray),
            ):
                raise PlayerSaveAuditError("component_enum_changed_shape")
            normalized_value = _safe_code(value, field)
            if normalized_value not in {_safe_code(item, field) for item in allowed}:
                raise PlayerSaveAuditError("component_enum_value_unavailable")
            normalized[field] = normalized_value
        else:
            raise PlayerSaveAuditError("component_field_type_unsupported")
    return normalized


def _baseline_from_tail(history: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    if history["structural_status"] not in {"observed", "empty"}:
        return None
    return {
        "fingerprint": history["fingerprint"],
        "entry_count": history["entry_count"],
        "capacity": history["capacity"],
    }


def _request_payload(request: Optional[AuditRequest]) -> Optional[dict[str, Any]]:
    if request is None:
        return None
    reasons = sorted(
        {_safe_code(reason, "request_reason") for reason in request.reasons}
    )
    return {
        "reason_codes": reasons,
        "requested_at": _utc_iso(request.requested_at),
        "boundary_label": _boundary_label(request.boundary_label),
        "boundary_observed_at": (
            _utc_iso(request.boundary_observed_at)
            if request.boundary_observed_at is not None
            else None
        ),
    }


def _timing_payload(
    observation: Mapping[str, Any],
    request: AuditRequest,
) -> dict[str, Any]:
    captured_at = observation["captured_at"]
    request_delta = max(
        0.0,
        (captured_at - request.requested_at).total_seconds() * 1000.0,
    )
    boundary_delta = (
        max(
            0.0,
            (captured_at - request.boundary_observed_at).total_seconds() * 1000.0,
        )
        if request.boundary_observed_at is not None
        else None
    )
    acquisition_delta = max(
        0.0,
        (
            observation["acquisition_completed_at"]
            - observation["acquisition_started_at"]
        ).total_seconds()
        * 1000.0,
    )
    return {
        "request_to_stable_observation_ms": round(request_delta, 3),
        "boundary_to_stable_observation_ms": (
            round(boundary_delta, 3) if boundary_delta is not None else None
        ),
        "bounded_acquisition_elapsed_ms": round(acquisition_delta, 3),
        "semantics": "observation_latency_only_not_game_write_time",
    }


def _boundary_from_detection(detection: Mapping[str, Any]) -> Optional[str]:
    state = str(detection.get("state") or "").strip().upper()
    if state in {"HOME", "HOME_SCREEN"}:
        control = str(detection.get("home_battle_control") or "").strip().upper()
        return "HOME_NEW_BATTLE" if control == "NEW_BATTLE" else None
    if state in {"RUNNING", "GAME_OVER", "TOURNAMENT_RESULTS"}:
        return state
    return None


def _starts_perk_mapping_round(request: AuditRequest) -> bool:
    return bool(
        {"home_new_battle", "first_running_observation"}
        & set(request.reasons)
    )


def _boundary_label(value: Any) -> Optional[str]:
    if value is None:
        return None
    label = str(value).strip().upper()
    return label if label in _BOUNDARY_REASON_CODES else None


def _observation_only_authority() -> dict[str, Any]:
    return {
        "observation_only": True,
        "input": False,
        "navigation": False,
        "dispatch": False,
        "lifecycle": False,
        "strategy_facts": False,
        "attachment": False,
        "record_construction": False,
        "ui_suppression": False,
    }


def _outcome(code: str, component: str) -> dict[str, str]:
    return {
        "code": _safe_code(code, "outcome_code"),
        "component": _safe_code(component, "outcome_component"),
        "disposition": "fail_closed",
    }


def _perk_pick_tuple(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value["sequence"],
        value["saved_wave"],
        value["perk_id"],
        value["perk_key"],
        value["level_after"],
    )


def _reason_code(value: Any) -> str:
    text = str(value or "available").strip().lower()
    text = text.split(":", 1)[0]
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not normalized:
        normalized = "available"
    return _safe_code(normalized[:96], "reason_code")


def _safe_ids(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise PlayerSaveAuditError(f"{field}_changed_shape")
    return tuple(_safe_id(item, field) for item in value)


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if _SAFE_ID_RE.fullmatch(text) is None:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return text


def _safe_code(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if _SAFE_CODE_RE.fullmatch(text) is None:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return text


def _sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if _SHA256_RE.fullmatch(text) is None:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return text


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return value


def _positive_int(value: Any, field: str) -> int:
    numeric = _nonnegative_int(value, field)
    if numeric < 1:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return numeric


def _strict_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return value


def _nonnegative_finite_number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlayerSaveAuditError(f"{field}_invalid")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return value


def _bounded_confidence(value: Any, field: str) -> float:
    numeric = float(_nonnegative_finite_number(value, field))
    if numeric > 100.0:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return round(numeric, 3)


def _unit_confidence(value: Any, field: str) -> float:
    numeric = float(_nonnegative_finite_number(value, field))
    if numeric > 1.0:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return round(numeric, 3)


def _decimal_digits(value: Any, field: str) -> str:
    text = str(value or "")
    if re.fullmatch(r"[0-9]{1,32}", text) is None:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return text


def _bounded_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise PlayerSaveAuditError(f"{field}_invalid")
    if any(ord(character) < 32 for character in value):
        raise PlayerSaveAuditError(f"{field}_invalid")
    return value


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PlayerSaveAuditError(f"{field}_invalid")
    return value.astimezone(timezone.utc)


def _parse_aware_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise PlayerSaveAuditError(f"{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PlayerSaveAuditError(f"{field}_invalid") from exc
    return _aware_datetime(parsed, field)


def _optional_timestamp(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return _utc_iso(_parse_aware_timestamp(value, "visual_timestamp"))


def _utc_iso(value: datetime) -> str:
    return _aware_datetime(value, "timestamp").isoformat(timespec="milliseconds")


def _relative_evidence_path(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or "\\" in value or len(value) > 240:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    if path.parts[:2] != ("screenshots", "matches") or path.suffix.lower() not in {
        ".png",
        ".jpg",
        ".jpeg",
    }:
        return None
    return path.as_posix()


def _validate_receipt(record: Mapping[str, Any]) -> None:
    if not isinstance(record, Mapping):
        raise PlayerSaveAuditError("audit_receipt_changed_shape")
    record_type = record.get("record_type")
    if record_type not in _RECORD_PAYLOAD_KEYS:
        raise PlayerSaveAuditError("audit_receipt_type_unsupported")
    allowed = _COMMON_RECEIPT_KEYS | _RECORD_PAYLOAD_KEYS[str(record_type)]
    if set(record) != allowed:
        raise PlayerSaveAuditError("audit_receipt_top_level_changed_shape")
    if record.get("schema_version") != PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_VERSION:
        raise PlayerSaveAuditError("audit_receipt_schema_changed")
    if record.get("schema_id") != PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_ID:
        raise PlayerSaveAuditError("audit_receipt_schema_id_changed")
    _safe_id(record.get("record_id"), "record_id")
    _safe_id(record.get("runtime_session_id"), "runtime_session_id")
    _safe_id(record.get("collector_session_id"), "collector_session_id")
    _positive_int(record.get("sequence"), "receipt_sequence")
    _parse_aware_timestamp(record.get("recorded_at"), "recorded_at")
    _validate_receipt_tree(record)


def _validate_receipt_tree(value: Any, *, key: Optional[str] = None) -> None:
    if key is not None:
        normalized_key = str(key).strip().lower()
        if any(part in normalized_key for part in _FORBIDDEN_RECEIPT_KEY_PARTS):
            raise PlayerSaveAuditError("audit_receipt_forbidden_key")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PlayerSaveAuditError("audit_receipt_nonfinite_number")
        return
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                raise PlayerSaveAuditError("audit_receipt_nonstring_key")
            _validate_receipt_tree(child_value, key=child_key)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _validate_receipt_tree(child)
        return
    raise PlayerSaveAuditError("audit_receipt_value_type_unsupported")


__all__ = [
    "AppendOnlyAuditReceiptWriter",
    "AuditRequest",
    "AuditSaveObservation",
    "DEFAULT_PLAYER_SAVE_AUDIT_MANIFEST_PATH",
    "DEFAULT_PLAYER_SAVE_AUDIT_RECEIPT_PATH",
    "PLAYER_SAVE_AUDIT_ID",
    "PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_ID",
    "PLAYER_SAVE_AUDIT_RECEIPT_SCHEMA_VERSION",
    "PlayerSaveAuditCollector",
    "PlayerSaveAuditError",
    "PlayerSaveAuditManifest",
    "PlayerSaveAuditStateMachine",
    "load_player_save_audit_manifest",
]
