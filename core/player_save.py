"""Read, normalize, and reconcile The Tower ``playerInfo.dat`` snapshots.

The save is an independent observation channel.  It may replace a mapped UI
read only after an exact mapping or a declared additive revision-compatibility
gate supplies validated authority.  Structurally changed, stale, incomplete,
or mismatched saves always route the check back through the existing UI
implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
import re
import time
from typing import Any, Optional

from core.runtime_save import (
    NormalizedRuntimeSave,
    RuntimeSaveNormalizationError,
    active_tally_contract_fingerprints,
    normalize_runtime_save,
)
from core.profile_progression import (
    ProfileProgressionError,
    normalize_profile_progression,
)
from core.read_only_data import deep_freeze, deep_thaw
from core.tournament_conditions import derive_tournament_conditions_from_save
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)
from core.player_save_confirmed_local_mapping import (
    ConfirmedLocalMappingError,
    ConfirmedLocalMappingStore,
    active_confirmations,
)
from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    PlayerSaveMappingCandidateError,
    canonical_mapping_set_fingerprint,
    fingerprint_json,
    mapping_candidate_review_status,
    pending_mapping_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
PLAYER_SAVE_MAPPING_DIR = ROOT / "config" / "player_save_versions"
PLAYER_SAVE_DEVICE_PATH = (
    "/sdcard/Android/data/"
    "com.TechTreeGames.TheTower/files/playerInfo.dat"
)
MAX_PLAYER_SAVE_BYTES = 512 * 1024
MAX_DECOMPRESSED_SAVE_BYTES = 4 * 1024 * 1024
SNAPSHOT_SCHEMA_VERSION = 7
RAW_FIELD_MANIFEST_SCHEMA_VERSION = 1
REVISION_COMPATIBILITY_SCHEMA_VERSION = 1
RUNTIME_SAVE_EXTENSION_SCHEMA_VERSION = 1
RAW_FIELD_DISPOSITION_NAMES = frozenset(
    {
        "structural",
        "automation_gating",
        "profile_observation",
        "runtime_observation",
        "private",
        "ignored_with_reason",
        "unknown",
    }
)
SAVE_ACCEPTED_DISPOSITIONS = frozenset({"save_match", "save_observation"})
SAVE_MISMATCH_DISPOSITION = "save_mismatch"
SAVE_UI_REQUIRED_DISPOSITION = "ui_required"


class PlayerSaveError(RuntimeError):
    """Base error for the read-only player-save channel."""


class PlayerSaveDecodeError(PlayerSaveError):
    """The container or NRBF payload could not be decoded safely."""


class PlayerSavePullError(PlayerSaveError):
    """A stable device copy could not be obtained."""


@dataclass(frozen=True)
class SaveCheckEvidence:
    """One profile-facing value derived from a resolved save mapping."""

    check_id: str
    status: str
    value: Any
    source_fields: tuple[str, ...]
    complete: bool = True
    reason: str = ""
    authority: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_fields", tuple(self.source_fields))
        object.__setattr__(self, "value", deep_freeze(self.value))
        object.__setattr__(self, "authority", deep_freeze(self.authority))
        object.__setattr__(self, "diagnostics", deep_freeze(self.diagnostics))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "value": deep_thaw(self.value),
            "source_fields": list(self.source_fields),
            "complete": self.complete,
            "reason": self.reason,
            "authority": deep_thaw(self.authority),
            "diagnostics": deep_thaw(self.diagnostics),
        }


@dataclass(frozen=True)
class PlayerSaveCapabilityEvidence:
    """One declared semantic contract resolved for this decoded document."""

    capability_id: str
    status: str
    reason: str
    semantic_fingerprint: str
    binding_fingerprint: str
    authority_id: str
    provider_mapping_id: str
    resolution: str
    forward_policy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "semantic_fingerprint": self.semantic_fingerprint,
            "binding_fingerprint": self.binding_fingerprint,
            "authority_id": self.authority_id,
            "provider_mapping_id": self.provider_mapping_id,
            "resolution": self.resolution,
            "forward_policy": self.forward_policy,
        }


@dataclass(frozen=True)
class PlayerSaveSnapshot:
    """Privacy-safe normalized projection of one decoded save."""

    captured_at: str
    source_name: str
    source_sha256: str
    source_size: int
    container: str
    decompressed_size: int
    root_class: str
    field_count: int
    data_version: Optional[int]
    game_version: Optional[int]
    save_revision: Optional[int]
    mapping_id: Optional[str]
    mapping_maturity: Optional[str]
    validated_checks: tuple[str, ...]
    shape_valid: bool
    warnings: tuple[str, ...]
    profile_summary: Mapping[str, Any]
    checks: Mapping[str, SaveCheckEvidence]
    runtime_save: Optional[NormalizedRuntimeSave]
    manifest_status: str = "unavailable"
    manifest_warnings: tuple[str, ...] = ()
    capabilities: Mapping[str, PlayerSaveCapabilityEvidence] = field(
        default_factory=dict
    )
    profile_progression: Mapping[str, Any] = field(default_factory=dict)
    mapping_resolution: str = "exact"
    mapping_authority_id: Optional[str] = None
    mapping_structural_id: Optional[str] = None
    mapping_semantic_fingerprint: Optional[str] = None
    canonical_mapping_fingerprint: Optional[str] = None
    effective_mapping_fingerprint: Optional[str] = None
    confirmed_local_mappings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "validated_checks", tuple(self.validated_checks))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(
            self,
            "manifest_warnings",
            tuple(self.manifest_warnings),
        )
        for name in (
            "profile_summary",
            "checks",
            "capabilities",
            "profile_progression",
            "confirmed_local_mappings",
        ):
            object.__setattr__(self, name, deep_freeze(getattr(self, name)))

    @property
    def mapping_supported(self) -> bool:
        return self.mapping_id is not None

    def capability(
        self,
        capability_id: str,
    ) -> Optional[PlayerSaveCapabilityEvidence]:
        """Return one normalized semantic capability without exposing raw data."""

        return self.capabilities.get(str(capability_id))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "captured_at": self.captured_at,
            "source": {
                "name": self.source_name,
                "sha256": self.source_sha256,
                "size": self.source_size,
                "container": self.container,
                "decompressed_size": self.decompressed_size,
            },
            "identity": {
                "root_class": self.root_class,
                "field_count": self.field_count,
                "data_version": self.data_version,
                "game_version": self.game_version,
                "save_revision": self.save_revision,
            },
            "mapping": {
                "supported": self.mapping_supported,
                "id": self.mapping_id,
                "maturity": self.mapping_maturity,
                "validated_checks": list(self.validated_checks),
                "shape_valid": self.shape_valid,
                "manifest_status": self.manifest_status,
                "manifest_warnings": list(self.manifest_warnings),
                "resolution": self.mapping_resolution,
                "authority_id": self.mapping_authority_id,
                "structural_id": self.mapping_structural_id,
                "semantic_fingerprint": self.mapping_semantic_fingerprint,
                "canonical_fingerprint": self.canonical_mapping_fingerprint,
                "effective_fingerprint": self.effective_mapping_fingerprint,
                "confirmed_local": deep_thaw(self.confirmed_local_mappings),
            },
            "warnings": list(self.warnings),
            "profile_summary": deep_thaw(self.profile_summary),
            "profile_progression": deep_thaw(self.profile_progression),
            "checks": {
                check_id: evidence.as_dict()
                for check_id, evidence in sorted(self.checks.items())
            },
            "capabilities": {
                capability_id: capability.as_dict()
                for capability_id, capability in sorted(
                    self.capabilities.items()
                )
            },
            "runtime_save": (
                self.runtime_save.as_dict()
                if self.runtime_save is not None
                else None
            ),
        }


@dataclass(frozen=True)
class _MappingResolution:
    mapping: Optional[dict[str, Any]]
    resolution: str
    shape_warnings: tuple[str, ...] = ()
    manifest_warnings: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    authority_mapping_id: Optional[str] = None
    structural_mapping_id: Optional[str] = None


def _empty_confirmed_local_projection() -> dict[str, Any]:
    return {
        "available": True,
        "generation": 0,
        "document_fingerprint": None,
        "applied_event_ids": [],
        "blocked_checks": [],
        "items": [],
    }


class PlayerSaveParser:
    """Stateless application API for one decode and all allowlisted projections.

    The parser never retains raw bytes or the decoded NRBF root.  Runtime code
    injects one instance into the shared acquisition owner, which then fans the
    read-only normalized snapshot out to every consumer.
    """

    def parse_bytes(
        self,
        payload: bytes,
        *,
        source_name: str = "playerInfo.dat",
        captured_at: Optional[datetime] = None,
    ) -> PlayerSaveSnapshot:
        return _parse_player_save_bytes(
            payload,
            source_name=source_name,
            captured_at=captured_at,
        )

    def parse_file(
        self,
        path: Path | str,
        *,
        captured_at: Optional[datetime] = None,
    ) -> PlayerSaveSnapshot:
        source = Path(path)
        return self.parse_bytes(
            source.read_bytes(),
            source_name=source.name,
            captured_at=captured_at,
        )


_DEFAULT_PLAYER_SAVE_PARSER = PlayerSaveParser()


def read_player_save_file(path: Path | str) -> PlayerSaveSnapshot:
    """Compatibility wrapper around the shared parser API for local files."""

    return _DEFAULT_PLAYER_SAVE_PARSER.parse_file(path)


def decode_player_save_bytes(
    payload: bytes,
    *,
    source_name: str = "playerInfo.dat",
    captured_at: Optional[datetime] = None,
) -> PlayerSaveSnapshot:
    """Compatibility wrapper around :class:`PlayerSaveParser`."""

    return _DEFAULT_PLAYER_SAVE_PARSER.parse_bytes(
        payload,
        source_name=source_name,
        captured_at=captured_at,
    )


def _parse_player_save_bytes(
    payload: bytes,
    *,
    source_name: str = "playerInfo.dat",
    captured_at: Optional[datetime] = None,
) -> PlayerSaveSnapshot:
    """Decode one save into a redacted semantic document."""

    if not isinstance(payload, bytes):
        raise TypeError("player save payload must be bytes")
    if not payload:
        raise PlayerSaveDecodeError("player save is empty")
    if len(payload) > MAX_PLAYER_SAVE_BYTES:
        raise PlayerSaveDecodeError(
            f"player save exceeds {MAX_PLAYER_SAVE_BYTES} bytes"
        )

    digest = hashlib.sha256(payload).hexdigest()
    container = "gzip+nrbf" if payload[:2] == b"\x1f\x8b" else "nrbf"
    try:
        raw = gzip.decompress(payload) if container == "gzip+nrbf" else payload
    except (EOFError, OSError) as exc:
        raise PlayerSaveDecodeError(f"invalid gzip player save: {exc}") from exc
    if len(raw) > MAX_DECOMPRESSED_SAVE_BYTES:
        raise PlayerSaveDecodeError(
            "decompressed player save exceeds "
            f"{MAX_DECOMPRESSED_SAVE_BYTES} bytes"
        )

    try:
        import nrbf
    except ImportError as exc:  # pragma: no cover - environment guard
        raise PlayerSaveDecodeError(
            "player save decoding requires the nrbf==0.1.2 player-save "
            "dependency; bootstrap the complete development environment"
        ) from exc
    try:
        decoded = nrbf.loads(raw)
    except Exception as exc:
        raise PlayerSaveDecodeError(f"invalid NRBF player save: {exc}") from exc
    if not isinstance(decoded, Mapping):
        raise PlayerSaveDecodeError("decoded player save root is not an object")

    stamp = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root_class = str(decoded.get("__class__") or "")
    data_version = _optional_int(decoded.get("dataVersion"))
    game_version = _optional_int(decoded.get("versionNumber"))
    save_revision = _optional_int(decoded.get("saveRevision"))
    mapping_resolution = _resolve_mapping(
        decoded,
        data_version=data_version,
        game_version=game_version,
    )
    mapping = (
        deepcopy(mapping_resolution.mapping)
        if mapping_resolution.mapping is not None
        else None
    )
    warnings = list(mapping_resolution.warnings)
    if mapping is None:
        if not warnings:
            warnings.append(
                "No exact or structurally compatible player-save mapping exists "
                f"for dataVersion={data_version}, "
                f"versionNumber={game_version}; all save-backed consumers "
                "require UI fallback."
            )
        return PlayerSaveSnapshot(
            captured_at=stamp.isoformat(),
            source_name=Path(source_name).name,
            source_sha256=digest,
            source_size=len(payload),
            container=container,
            decompressed_size=len(raw),
            root_class=root_class,
            field_count=len(decoded),
            data_version=data_version,
            game_version=game_version,
            save_revision=save_revision,
            mapping_id=None,
            mapping_maturity=None,
            validated_checks=(),
            shape_valid=False,
            warnings=tuple(warnings),
            profile_summary={},
            checks={},
            runtime_save=None,
            mapping_resolution=mapping_resolution.resolution,
            mapping_authority_id=(
                mapping_resolution.authority_mapping_id
            ),
            mapping_structural_id=(
                mapping_resolution.structural_mapping_id
            ),
        )

    shape_warnings = list(mapping_resolution.shape_warnings)
    manifest_warnings = list(mapping_resolution.manifest_warnings)
    warnings.extend(shape_warnings)
    warnings.extend(manifest_warnings)
    shape_valid = not shape_warnings
    manifest_status = "exact" if not manifest_warnings else "drifted"
    mapping_semantic_fingerprint = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id=mapping_resolution.authority_mapping_id,
        structural_mapping_id=mapping_resolution.structural_mapping_id,
    )
    canonical_mapping_fingerprint = canonical_mapping_set_fingerprint(
        {
            str(candidate["mapping_id"]): candidate
            for candidate in _load_mappings()
        },
        authority_mapping_id=mapping_resolution.authority_mapping_id,
        structural_mapping_id=mapping_resolution.structural_mapping_id,
    )
    canonical_module_info_indices = deepcopy(
        mapping.get("module_info_indices")
    )
    semantic_capabilities_only = bool(
        mapping.get("semantic_capabilities_only")
    )
    confirmed_local = _empty_confirmed_local_projection()
    if shape_valid and not semantic_capabilities_only:
        mapping, confirmed_local, local_warnings = (
            _apply_confirmed_local_mappings(
                mapping,
                data_version=data_version,
                game_version=game_version,
                root_class=root_class,
                mapping_resolution=mapping_resolution.resolution,
                authority_mapping_id=mapping_resolution.authority_mapping_id,
                structural_mapping_id=mapping_resolution.structural_mapping_id,
                dependency_fingerprint=mapping_semantic_fingerprint,
            )
        )
        warnings.extend(local_warnings)
    effective_mapping_fingerprint = (
        _effective_mapping_fingerprint(
            mapping,
            canonical_dependency_fingerprint=mapping_semantic_fingerprint,
            mapping_resolution=mapping_resolution.resolution,
            authority_mapping_id=mapping_resolution.authority_mapping_id,
            structural_mapping_id=mapping_resolution.structural_mapping_id,
            confirmed_local=confirmed_local,
        )
        if shape_valid
        else None
    )
    checks: dict[str, SaveCheckEvidence] = {}
    profile_summary: dict[str, Any] = {}
    profile_progression: dict[str, Any] = {}
    runtime_save: Optional[NormalizedRuntimeSave] = None
    if shape_valid:
        if not semantic_capabilities_only:
            checks = _build_checks(
                decoded,
                mapping,
                captured_at=stamp,
                canonical_module_info_indices=canonical_module_info_indices,
            )
            for blocked_check in confirmed_local["blocked_checks"]:
                if blocked_check == "modules":
                    checks[blocked_check] = _unmapped_module_evidence(
                        ("moduleEquipped", "assistModuleSlots"),
                        "confirmed local module mapping requires UI fallback",
                        diagnostics={
                            "confirmed_local_mapping": dict(confirmed_local),
                        },
                    )
            profile_summary = _build_profile_summary(decoded, mapping)
        capture = {
            "captured_at": stamp.isoformat(),
            "source_name": Path(source_name).name,
            "source_sha256": digest,
            "source_size": len(payload),
            "container": container,
            "decompressed_size": len(raw),
        }
        try:
            profile_progression = normalize_profile_progression(
                decoded,
                mapping,
                capture=capture,
            )
        except ProfileProgressionError as exc:
            warnings.append(
                "The selected profile progression projection failed "
                f"closed: {exc}. Completed-run progression will be marked "
                "unavailable."
            )
        try:
            runtime_save = normalize_runtime_save(
                decoded,
                mapping,
                capture=capture,
            )
        except RuntimeSaveNormalizationError as exc:
            warnings.append(
                "The selected runtime projection failed closed: "
                f"{exc}. Runtime save evidence requires UI fallback."
            )
        if runtime_save is not None and not semantic_capabilities_only:
            checks["battle_history_killed_by"] = (
                _battle_history_killed_by_evidence(runtime_save, mapping)
            )
    else:
        warnings.append(
            "The selected mapping's structural signature changed; "
            "all configuration checks require UI fallback."
        )

    maturity = str(mapping.get("maturity") or "candidate")
    validated_checks = tuple(
        str(check_id) for check_id in mapping.get("validated_checks") or ()
    )
    if maturity != "validated":
        warnings.append(
            f"Mapping {mapping['mapping_id']} is {maturity}; only explicitly "
            "validated checks may use fresh save evidence without a full UI "
            "audit."
        )
    capabilities = _build_capability_evidence(
        mapping,
        mapping_resolution=mapping_resolution,
        runtime_save=runtime_save,
    )
    return PlayerSaveSnapshot(
        captured_at=stamp.isoformat(),
        source_name=Path(source_name).name,
        source_sha256=digest,
        source_size=len(payload),
        container=container,
        decompressed_size=len(raw),
        root_class=root_class,
        field_count=len(decoded),
        data_version=data_version,
        game_version=game_version,
        save_revision=save_revision,
        mapping_id=str(mapping["mapping_id"]),
        mapping_maturity=maturity,
        validated_checks=validated_checks,
        shape_valid=shape_valid,
        warnings=tuple(warnings),
        profile_summary=profile_summary,
        checks=checks,
        runtime_save=runtime_save,
        manifest_status=manifest_status,
        manifest_warnings=tuple(manifest_warnings),
        capabilities=capabilities,
        profile_progression=profile_progression,
        mapping_resolution=mapping_resolution.resolution,
        mapping_authority_id=mapping_resolution.authority_mapping_id,
        mapping_structural_id=mapping_resolution.structural_mapping_id,
        mapping_semantic_fingerprint=mapping_semantic_fingerprint,
        canonical_mapping_fingerprint=canonical_mapping_fingerprint,
        effective_mapping_fingerprint=effective_mapping_fingerprint,
        confirmed_local_mappings=confirmed_local,
    )


def _build_capability_evidence(
    mapping: Mapping[str, Any],
    *,
    mapping_resolution: _MappingResolution,
    runtime_save: Optional[NormalizedRuntimeSave],
) -> dict[str, PlayerSaveCapabilityEvidence]:
    """Publish only declared semantic contracts and their normalized status."""

    runtime_spec = mapping.get("runtime_save")
    tally_spec = (
        runtime_spec.get("active_tallies")
        if isinstance(runtime_spec, Mapping)
        else None
    )
    if not isinstance(tally_spec, Mapping):
        return {}
    capability_id = str(tally_spec.get("capability_id") or "").strip()
    if not capability_id:
        return {}
    semantic_fingerprint, binding_fingerprint = (
        active_tally_contract_fingerprints(tally_spec)
    )
    tallies = (
        runtime_save.active_tallies
        if runtime_save is not None
        else None
    )
    status = getattr(tallies, "status", "unavailable")
    reason = getattr(
        tallies,
        "reason",
        "runtime_projection_unavailable",
    )
    if (
        runtime_save is not None
        and runtime_save.round_active
        and runtime_save.active_round_identity is None
    ):
        status = "unavailable"
        reason = runtime_save.active_identity_reason
    return {
        capability_id: PlayerSaveCapabilityEvidence(
            capability_id=capability_id,
            status=str(status),
            reason=str(reason),
            semantic_fingerprint=semantic_fingerprint,
            binding_fingerprint=binding_fingerprint,
            authority_id=str(tally_spec.get("audit_id") or ""),
            provider_mapping_id=str(
                mapping_resolution.structural_mapping_id
                or mapping.get("mapping_id")
                or ""
            ),
            resolution=mapping_resolution.resolution,
            forward_policy=str(tally_spec.get("forward_policy") or "none"),
        )
    }


def pull_player_save_bytes(
    *,
    device_id: Optional[str] = None,
    device_path: str = PLAYER_SAVE_DEVICE_PATH,
    attempts: int = 3,
    settle_seconds: float = 0.1,
    read_fn: Optional[Callable[..., Optional[bytes]]] = None,
) -> bytes:
    """Return two identical consecutive ADB reads or fail without a snapshot."""

    if attempts < 1:
        raise ValueError("attempts must be positive")
    if read_fn is None:
        from core.adb_utils import read_device_file

        read_fn = read_device_file
    previous: Optional[bytes] = None
    for _attempt in range(attempts):
        first = read_fn(device_path, device_id=device_id)
        if not first:
            previous = None
            continue
        if settle_seconds > 0:
            time.sleep(settle_seconds)
        second = read_fn(device_path, device_id=device_id)
        if second and first == second:
            return bytes(first)
        previous = bytes(second or first)
    qualifier = (
        "changed between consecutive reads"
        if previous
        else "could not be read"
    )
    raise PlayerSavePullError(
        f"player save {qualifier} after {attempts} bounded attempt(s)"
    )


def reconcile_requirements(
    snapshot: PlayerSaveSnapshot,
    requirements: Mapping[str, Any],
    *,
    force_ui_audit: bool = False,
    freshness_verified: bool = False,
    max_snapshot_age_s: Optional[float] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Plan which configured checks may use save evidence or require the UI.

    This function never changes configuration.  ``freshness_verified`` is an
    explicit assertion that the game completed a known serialization boundary
    before this snapshot was pulled; capture time alone does not prove that the
    application flushed recent UI changes.  Every UI-required result names
    ``existing_ui_check`` as its fallback so save import cannot weaken the
    current preflight path.
    """

    expected = _expanded_requirement_values(requirements)
    stale = _snapshot_is_stale(
        snapshot,
        max_snapshot_age_s=max_snapshot_age_s,
        now=now,
    )
    snapshot_trust_reason: Optional[str] = None
    if not snapshot.mapping_supported:
        snapshot_trust_reason = "unsupported_save_version"
    elif not snapshot.shape_valid:
        snapshot_trust_reason = "save_shape_changed"
    elif stale:
        snapshot_trust_reason = "save_snapshot_stale"
    elif not freshness_verified:
        snapshot_trust_reason = "save_freshness_unverified"
    snapshot_trusted = snapshot_trust_reason is None
    save_reason_prefix = (
        "compatible_revision"
        if snapshot.mapping_resolution
        in {"compatible_exact_revision", "compatible_forward_revision"}
        else "exact_version"
    )

    decisions: dict[str, dict[str, Any]] = {}
    for check_id, expected_value in expected.items():
        check_policy = _requirement_policy(requirements, str(check_id))
        evidence = snapshot.checks.get(str(check_id))
        observed = evidence.value if evidence is not None else None
        matches = (
            save_check_matches_requirement(
                str(check_id),
                expected_value,
                observed,
            )
            if evidence is not None and evidence.status == "observed"
            else None
        )
        requirement_supported = bool(
            evidence is not None
            and _requirement_is_supported(
                str(check_id),
                expected_value,
                evidence,
            )
        )
        check_validated = bool(
            snapshot.mapping_maturity == "validated"
            or str(check_id) in snapshot.validated_checks
        )
        observation_only = bool(
            str(check_id) in {"modules", "damage_slider", "orb_distance"}
            and check_policy == "observe"
        )

        if force_ui_audit:
            disposition = SAVE_UI_REQUIRED_DISPOSITION
            reason = "scheduled_ui_audit"
        elif not snapshot_trusted:
            disposition = SAVE_UI_REQUIRED_DISPOSITION
            reason = str(snapshot_trust_reason)
        elif evidence is None or evidence.status != "observed":
            disposition = SAVE_UI_REQUIRED_DISPOSITION
            reason = evidence.reason if evidence is not None else "check_unmapped"
        elif not evidence.complete:
            disposition = SAVE_UI_REQUIRED_DISPOSITION
            reason = "save_evidence_incomplete"
        elif not check_validated:
            disposition = SAVE_UI_REQUIRED_DISPOSITION
            reason = "mapping_candidate_audit"
        elif not requirement_supported:
            disposition = SAVE_UI_REQUIRED_DISPOSITION
            reason = "save_requirement_outside_validated_scope"
        elif observation_only:
            disposition = "save_observation"
            reason = f"{save_reason_prefix}_save_observation"
        elif matches is True:
            disposition = "save_match"
            reason = f"{save_reason_prefix}_save_match"
        elif matches is False:
            disposition = SAVE_MISMATCH_DISPOSITION
            reason = "save_mismatch"
        else:
            disposition = SAVE_UI_REQUIRED_DISPOSITION
            reason = "save_comparison_unavailable"

        save_authoritative = disposition in {
            *SAVE_ACCEPTED_DISPOSITIONS,
            SAVE_MISMATCH_DISPOSITION,
        }
        ui_required = disposition in {
            SAVE_MISMATCH_DISPOSITION,
            SAVE_UI_REQUIRED_DISPOSITION,
        }

        decisions[str(check_id)] = {
            "mapping_id": snapshot.mapping_id,
            "effective_mapping_fingerprint": (
                snapshot.effective_mapping_fingerprint
            ),
            "disposition": disposition,
            "reason": reason,
            "snapshot_trusted": snapshot_trusted,
            "save_evidence_authoritative": save_authoritative,
            "save_evidence_status": (
                evidence.status if evidence is not None else "unmapped"
            ),
            "matches": matches,
            "policy": check_policy,
            "expected": expected_value,
            "observed": observed,
            "save_evidence_complete": (
                evidence.complete if evidence is not None else False
            ),
            "save_check_validated": check_validated,
            "save_requirement_supported": requirement_supported,
            "diagnostics": _check_diagnostics(
                str(check_id),
                expected_value,
                observed,
                evidence,
            ),
            "ui_required": ui_required,
            "ui_requirement_kind": (
                "trusted_mismatch"
                if disposition == SAVE_MISMATCH_DISPOSITION
                else "fallback"
                if disposition == SAVE_UI_REQUIRED_DISPOSITION
                else "none"
            ),
            "repair_queued": disposition == SAVE_MISMATCH_DISPOSITION,
            "fallback": "existing_ui_check",
        }

    ui_required = [
        check_id
        for check_id, decision in decisions.items()
        if decision["ui_required"]
    ]
    trusted_mismatches = [
        check_id
        for check_id, decision in decisions.items()
        if decision["disposition"] == SAVE_MISMATCH_DISPOSITION
    ]
    return {
        "schema_version": 2,
        "mapping_id": snapshot.mapping_id,
        "mapping_maturity": snapshot.mapping_maturity,
        "mapping_resolution": snapshot.mapping_resolution,
        "mapping_authority_id": snapshot.mapping_authority_id,
        "mapping_structural_id": snapshot.mapping_structural_id,
        "effective_mapping_fingerprint": (
            snapshot.effective_mapping_fingerprint
        ),
        "validated_checks": list(snapshot.validated_checks),
        "freshness_verified": bool(freshness_verified),
        "snapshot_trust": {
            "status": "trusted" if snapshot_trusted else "invalidated",
            "reason": snapshot_trust_reason or "verified",
        },
        "save_revision": snapshot.save_revision,
        "ui_backup_preserved": True,
        "checks": decisions,
        "summary": {
            "total": len(decisions),
            "matching_observations": sum(
                decision["matches"] is True for decision in decisions.values()
            ),
            "save_acceptances": sum(
                decision["disposition"] in SAVE_ACCEPTED_DISPOSITIONS
                for decision in decisions.values()
            ),
            "save_matches": sum(
                decision["disposition"] == "save_match"
                for decision in decisions.values()
            ),
            "save_observations": sum(
                decision["disposition"] == "save_observation"
                for decision in decisions.values()
            ),
            "trusted_mismatches": len(trusted_mismatches),
            "trusted_mismatch_checks": trusted_mismatches,
            "ui_required": len(ui_required),
            "ui_required_checks": ui_required,
        },
    }


def reconcile_acquired_requirements(
    acquisition: PlayerSaveAcquisitionBundle,
    requirements: Mapping[str, Any],
    *,
    force_ui_audit: bool = False,
    max_snapshot_age_s: Optional[float] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Reconcile runtime requirements from typed acquisition authority.

    Current configuration is authoritative only at a successfully restored
    forced-serialization boundary.  The legacy boolean API remains available
    solely to the explicit offline inspection path.
    """

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise TypeError("runtime reconciliation requires a typed acquisition")
    if not acquisition.complete or acquisition.snapshot is None:
        raise ValueError("runtime reconciliation requires a complete acquisition")
    forced = (
        acquisition.acquisition_type
        is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
    )
    result = reconcile_requirements(
        acquisition.snapshot,
        requirements,
        force_ui_audit=force_ui_audit,
        freshness_verified=forced,
        max_snapshot_age_s=max_snapshot_age_s,
        now=now,
    )
    result.pop("freshness_verified", None)
    result["acquisition"] = acquisition.redacted_provenance()
    return result


def reconcile_direct_retry_requirements(
    acquisition: PlayerSaveAcquisitionBundle,
    requirements: Mapping[str, Any],
    *,
    runtime_session_id: str,
    source_activity_scope_id: str,
    successor_activity_scope_id: str,
    expected_binding: PlayerSaveTargetBinding,
    max_snapshot_age_s: Optional[float] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Reconcile configuration for one exact natural Game Over -> Retry.

    A natural terminal save is not generic current-configuration authority.
    This deliberately narrow seam accepts it only while the acquisition still
    names the same process, predecessor activity scope, target, and target
    generation that own the verified direct-Retry transition.  Home and
    attachment callers must continue to use their existing acquisition paths.
    """

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise TypeError("direct-Retry reconciliation requires a typed acquisition")
    if not acquisition.complete or acquisition.snapshot is None:
        raise ValueError("direct-Retry reconciliation requires a complete acquisition")
    if acquisition.acquisition_type is not PlayerSaveAcquisitionType.NATURAL_BOUNDARY:
        raise ValueError("direct-Retry reconciliation requires a natural boundary")
    boundary = acquisition.boundary
    if (
        not isinstance(boundary, PlayerSaveNaturalBoundary)
        or boundary.kind is not PlayerSaveBoundaryKind.GAME_OVER
    ):
        raise ValueError("direct-Retry reconciliation requires a Game Over boundary")
    runtime_id = str(runtime_session_id or "").strip()
    source_scope = str(source_activity_scope_id or "").strip()
    successor_scope = str(successor_activity_scope_id or "").strip()
    if (
        not runtime_id
        or not source_scope
        or not successor_scope
        or successor_scope == source_scope
        or boundary.runtime_session_id != runtime_id
        or boundary.activity_scope_id != source_scope
    ):
        raise ValueError("direct-Retry predecessor binding changed")
    if not isinstance(expected_binding, PlayerSaveTargetBinding):
        raise TypeError("direct-Retry reconciliation requires a target binding")
    if not acquisition.matches_binding(expected_binding):
        raise ValueError("direct-Retry target binding changed")

    runtime_save = acquisition.snapshot.runtime_save
    if runtime_save is not None and runtime_save.round_active is not False:
        raise ValueError("direct-Retry terminal save still reports an active round")

    result = reconcile_requirements(
        acquisition.snapshot,
        requirements,
        freshness_verified=True,
        max_snapshot_age_s=max_snapshot_age_s,
        now=now,
    )
    result.pop("freshness_verified", None)
    result["acquisition"] = acquisition.redacted_provenance()
    result["authority"] = "natural_game_over_direct_retry"
    return result


@lru_cache(maxsize=1)
def _load_mappings() -> tuple[dict[str, Any], ...]:
    mappings: list[dict[str, Any]] = []
    sources: dict[str, Path] = {}
    identities: dict[tuple[Any, Any], str] = {}
    for path in sorted(PLAYER_SAVE_MAPPING_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise PlayerSaveError(f"unsupported mapping schema in {path}")
        _validate_raw_field_manifest(payload, source=path)
        _validate_runtime_save_extensions(payload, source=path)
        mapping_id = str(payload.get("mapping_id") or "").strip()
        if not mapping_id:
            raise PlayerSaveError(f"mapping_id is missing in {path}")
        if mapping_id in sources:
            raise PlayerSaveError(
                f"duplicate mapping_id {mapping_id!r} in {sources[mapping_id]} "
                f"and {path}"
            )
        identity = payload.get("identity") or {}
        identity_key = (
            identity.get("data_version"),
            identity.get("game_version"),
        )
        if identity_key in identities:
            raise PlayerSaveError(
                "duplicate player-save identity "
                f"{identity_key!r} in mappings {identities[identity_key]!r} "
                f"and {mapping_id!r}"
            )
        sources[mapping_id] = path
        identities[identity_key] = mapping_id
        mappings.append(payload)
    mappings_by_id = {
        str(mapping["mapping_id"]): mapping for mapping in mappings
    }
    for mapping in mappings:
        _validate_revision_compatibility(
            mapping,
            mappings_by_id=mappings_by_id,
            source=sources[str(mapping["mapping_id"])],
        )
    return tuple(mappings)


def _select_mapping(
    data_version: Optional[int],
    game_version: Optional[int],
) -> Optional[dict[str, Any]]:
    for mapping in _load_mappings():
        identity = mapping.get("identity") or {}
        if (
            identity.get("data_version") == data_version
            and identity.get("game_version") == game_version
        ):
            return mapping
    return None


def _resolve_mapping(
    decoded: Mapping[str, Any],
    *,
    data_version: Optional[int],
    game_version: Optional[int],
) -> _MappingResolution:
    exact = _select_mapping(data_version, game_version)
    if exact is not None:
        mapping_id = str(exact["mapping_id"])
        shape_warnings = tuple(_validate_shape(decoded, exact))
        manifest_warnings = tuple(_manifest_drift_warnings(decoded, exact))
        compatibility = exact.get("revision_compatibility")
        if shape_warnings or not isinstance(compatibility, Mapping):
            return _MappingResolution(
                exact,
                "exact",
                shape_warnings=shape_warnings,
                manifest_warnings=manifest_warnings,
                authority_mapping_id=mapping_id,
                structural_mapping_id=mapping_id,
            )
        authority = _mapping_by_id(
            str(compatibility["authority_mapping_id"])
        )
        additions = _decoded_additional_fields(decoded, authority)
        return _MappingResolution(
            _compatible_mapping(
                structural=exact,
                authority=authority,
                data_version=data_version,
                game_version=game_version,
                mapping_id=mapping_id,
                retain_exact_profile_progression=True,
            ),
            "compatible_exact_revision",
            warnings=(
                "The exact player-save mapping passed its declared additive "
                "revision-compatibility gate; using validated semantics from "
                f"{authority['mapping_id']} for "
                f"{len(compatibility['validated_checks'])} configuration "
                f"check(s). {len(additions)} added root field(s) remain "
                "unpublished.",
            ),
            manifest_warnings=manifest_warnings,
            authority_mapping_id=str(authority["mapping_id"]),
            structural_mapping_id=mapping_id,
        )

    structural = _select_forward_compatibility_mapping(
        decoded,
        data_version=data_version,
        game_version=game_version,
    )
    if structural is None:
        semantic_provider = _select_semantic_forward_provider(
            decoded,
            data_version=data_version,
            game_version=game_version,
        )
        if semantic_provider is None:
            return _MappingResolution(None, "unsupported")
        assert data_version is not None
        assert game_version is not None
        provider_id = str(semantic_provider["mapping_id"])
        mapping_id = (
            f"data-{data_version}-game-{game_version}-semantic-via-"
            f"{(semantic_provider.get('identity') or {})['game_version']}"
        )
        return _MappingResolution(
            _semantic_capability_mapping(
                provider=semantic_provider,
                data_version=data_version,
                game_version=game_version,
                mapping_id=mapping_id,
            ),
            "semantic_forward_revision",
            warnings=(
                "No version-structural mapping exists for the decoded save, "
                "but a declared additive-dependency semantic capability "
                f"resolved through {provider_id}. Legacy version-bound "
                "projections remain unavailable.",
            ),
            manifest_warnings=tuple(
                _manifest_drift_warnings(
                    decoded,
                    semantic_provider,
                    allow_additional_fields=True,
                )
            ),
            authority_mapping_id=provider_id,
            structural_mapping_id=provider_id,
        )
    compatibility = structural["revision_compatibility"]
    authority = _mapping_by_id(str(compatibility["authority_mapping_id"]))
    shape_warnings = tuple(
        _validate_shape(
            decoded,
            structural,
        )
    )
    manifest_warnings = tuple(
        _manifest_drift_warnings(
            decoded,
            structural,
            allow_additional_fields=True,
        )
    )
    structural_id = str(structural["mapping_id"])
    authority_id = str(authority["mapping_id"])
    if shape_warnings:
        detail = "; ".join(shape_warnings)
        return _MappingResolution(
            None,
            "incompatible_revision",
            warnings=(
                "The newly observed player-save version is not structurally "
                f"compatible with {structural_id}: {detail}. All save-backed "
                "consumers require UI fallback.",
            ),
            manifest_warnings=manifest_warnings,
            authority_mapping_id=authority_id,
            structural_mapping_id=structural_id,
        )

    assert data_version is not None
    assert game_version is not None
    mapping_id = (
        f"data-{data_version}-game-{game_version}-compatible-via-"
        f"{(structural.get('identity') or {})['game_version']}"
    )
    additions = _decoded_additional_fields(decoded, structural)
    return _MappingResolution(
        _compatible_mapping(
            structural=structural,
            authority=authority,
            data_version=data_version,
            game_version=game_version,
            mapping_id=mapping_id,
            retain_exact_profile_progression=False,
        ),
        "compatible_forward_revision",
        warnings=(
            "No exact player-save mapping exists for the observed game "
            f"version, but its root is an additive structural match for "
            f"{structural_id}. Using validated semantics from {authority_id}; "
            f"{len(additions)} newly observed root field(s) remain unpublished.",
        ),
        manifest_warnings=manifest_warnings,
        authority_mapping_id=authority_id,
        structural_mapping_id=structural_id,
    )


def _mapping_by_id(mapping_id: str) -> dict[str, Any]:
    for mapping in _load_mappings():
        if str(mapping.get("mapping_id") or "") == mapping_id:
            return mapping
    raise PlayerSaveError(f"player-save mapping {mapping_id!r} is unavailable")


def _select_forward_compatibility_mapping(
    decoded: Mapping[str, Any],
    *,
    data_version: Optional[int],
    game_version: Optional[int],
) -> Optional[dict[str, Any]]:
    if type(data_version) is not int or type(game_version) is not int:
        return None
    actual_class = str(decoded.get("__class__") or "")
    eligible: list[dict[str, Any]] = []
    for mapping in _load_mappings():
        compatibility = mapping.get("revision_compatibility")
        identity = mapping.get("identity") or {}
        mapped_version = identity.get("game_version")
        if (
            isinstance(compatibility, Mapping)
            and compatibility.get("allow_forward_game_versions") is True
            and identity.get("data_version") == data_version
            and identity.get("root_class") == actual_class
            and type(mapped_version) is int
            and mapped_version < game_version
        ):
            eligible.append(mapping)
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda mapping: int((mapping.get("identity") or {})["game_version"]),
    )


def _select_semantic_forward_provider(
    decoded: Mapping[str, Any],
    *,
    data_version: Optional[int],
    game_version: Optional[int],
) -> Optional[dict[str, Any]]:
    """Select only a provider that explicitly allows additive dependencies."""

    if type(data_version) is not int or type(game_version) is not int:
        return None
    actual_class = str(decoded.get("__class__") or "")
    eligible: list[dict[str, Any]] = []
    for mapping in _load_mappings():
        identity = mapping.get("identity") or {}
        extensions = mapping.get("runtime_save_extensions")
        tally_spec = (
            extensions.get("active_tallies")
            if isinstance(extensions, Mapping)
            else None
        )
        mapped_data = identity.get("data_version")
        mapped_game = identity.get("game_version")
        if (
            isinstance(tally_spec, Mapping)
            and tally_spec.get("forward_policy") == "additive_dependencies"
            and identity.get("root_class") == actual_class
            and type(mapped_data) is int
            and type(mapped_game) is int
            and data_version >= mapped_data
            and game_version >= mapped_game
        ):
            eligible.append(mapping)
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda mapping: (
            int((mapping.get("identity") or {})["data_version"]),
            int((mapping.get("identity") or {})["game_version"]),
        ),
    )


def _semantic_capability_mapping(
    *,
    provider: Mapping[str, Any],
    data_version: int,
    game_version: int,
    mapping_id: str,
) -> dict[str, Any]:
    """Build a capability-only mapping without inheriting legacy authority."""

    compatibility = provider.get("revision_compatibility")
    if not isinstance(compatibility, Mapping):
        raise PlayerSaveError("semantic provider has no runtime authority")
    authority = _mapping_by_id(str(compatibility["authority_mapping_id"]))
    runtime_spec = authority.get("runtime_save")
    extensions = provider.get("runtime_save_extensions")
    tally_spec = (
        extensions.get("active_tallies")
        if isinstance(extensions, Mapping)
        else None
    )
    if not isinstance(runtime_spec, Mapping) or not isinstance(tally_spec, Mapping):
        raise PlayerSaveError("semantic provider runtime contract is unavailable")
    effective_runtime = deepcopy(dict(runtime_spec))
    effective_runtime["active_tallies"] = deepcopy(dict(tally_spec))
    effective_runtime["semantic_capabilities_only"] = True
    return {
        "mapping_id": mapping_id,
        "maturity": "candidate",
        "validated_checks": [],
        "identity": {
            "data_version": data_version,
            "game_version": game_version,
            "root_class": str((provider.get("identity") or {})["root_class"]),
        },
        "raw_field_manifest": deepcopy(provider["raw_field_manifest"]),
        "runtime_save": effective_runtime,
        "semantic_capabilities_only": True,
    }


def _compatible_mapping(
    *,
    structural: Mapping[str, Any],
    authority: Mapping[str, Any],
    data_version: int,
    game_version: int,
    mapping_id: str,
    retain_exact_profile_progression: bool,
) -> dict[str, Any]:
    compatibility = structural["revision_compatibility"]
    effective = deepcopy(dict(authority))
    effective["mapping_id"] = mapping_id
    effective["maturity"] = "candidate"
    effective["validated_checks"] = list(compatibility["validated_checks"])
    effective["identity"] = {
        "data_version": data_version,
        "game_version": game_version,
        "root_class": str((structural.get("identity") or {})["root_class"]),
    }
    effective["raw_field_manifest"] = deepcopy(
        structural["raw_field_manifest"]
    )
    if compatibility.get("runtime_save") is not True:
        effective.pop("runtime_save", None)
    elif isinstance(structural.get("runtime_save_extensions"), Mapping):
        runtime_save = effective.get("runtime_save")
        if not isinstance(runtime_save, Mapping):
            raise PlayerSaveError(
                "runtime-save extension has no compatible authority"
            )
        merged_runtime = deepcopy(dict(runtime_save))
        for component_name, component in structural[
            "runtime_save_extensions"
        ].items():
            if not retain_exact_profile_progression and not (
                isinstance(component, Mapping)
                and component.get("forward_policy")
                == "additive_dependencies"
            ):
                continue
            if component_name in merged_runtime:
                raise PlayerSaveError(
                    "runtime-save extension overrides authority component "
                    f"{component_name!r}"
                )
            merged_runtime[component_name] = deepcopy(component)
        effective["runtime_save"] = merged_runtime
    if retain_exact_profile_progression and isinstance(
        structural.get("profile_progression"), Mapping
    ):
        effective["profile_progression"] = deepcopy(
            structural["profile_progression"]
        )
    else:
        effective.pop("profile_progression", None)
    return effective


def _mapping_semantic_fingerprint(
    mapping: Mapping[str, Any],
    *,
    authority_mapping_id: Optional[str],
    structural_mapping_id: Optional[str],
) -> str:
    """Fingerprint the canonical dependency used by local module evidence."""

    structural_compatibility = None
    structural_id = str(structural_mapping_id or "")
    if structural_id:
        try:
            structural_compatibility = deepcopy(
                _mapping_by_id(structural_id).get("revision_compatibility")
            )
        except PlayerSaveError:
            structural_compatibility = deepcopy(
                mapping.get("revision_compatibility")
            )

    return fingerprint_json(
        {
            "schema_version": 1,
            "mapping_id": str(mapping.get("mapping_id") or ""),
            "authority_mapping_id": str(authority_mapping_id or ""),
            "structural_mapping_id": structural_id,
            "identity": deepcopy(mapping.get("identity")),
            "maturity": str(mapping.get("maturity") or ""),
            "validated_checks": sorted(
                str(value) for value in mapping.get("validated_checks") or ()
            ),
            "structural_revision_compatibility": structural_compatibility,
            "module_info_indices": mapping.get("module_info_indices"),
            "module_loadout": mapping.get("module_loadout"),
            "runtime_save": mapping.get("runtime_save"),
        }
    )


def _effective_mapping_fingerprint(
    mapping: Mapping[str, Any],
    *,
    canonical_dependency_fingerprint: str,
    mapping_resolution: str,
    authority_mapping_id: Optional[str],
    structural_mapping_id: Optional[str],
    confirmed_local: Mapping[str, Any],
) -> str:
    """Fingerprint the exact effective authority used by this snapshot."""

    lifecycle = [
        {
            "event_id": item.get("event_id"),
            "candidate_record_id": item.get("candidate_record_id"),
            "state": item.get("state"),
            "check_id": item.get("check_id"),
            "raw_value": item.get("raw_value"),
            "semantic_value": item.get("semantic_value"),
            "scope": item.get("scope"),
        }
        for item in confirmed_local.get("items") or ()
        if isinstance(item, Mapping)
    ]
    return fingerprint_json(
        {
            "schema_version": 1,
            "canonical_dependency_fingerprint": (
                canonical_dependency_fingerprint
            ),
            # Bind every effective semantic/structural mapping decision used
            # by this decode.  The narrower canonical dependency fingerprint
            # above intentionally remains Module-local for confirmed-local
            # lifecycle validation; carry, History, and preflight bindings
            # require the complete effective mapping instead.
            "effective_mapping": deepcopy(mapping),
            "identity": deepcopy(mapping.get("identity")),
            "mapping_id": str(mapping.get("mapping_id") or ""),
            "mapping_resolution": str(mapping_resolution or ""),
            "authority_mapping_id": str(authority_mapping_id or ""),
            "structural_mapping_id": str(structural_mapping_id or ""),
            "module_info_indices": deepcopy(
                mapping.get("module_info_indices")
            ),
            "module_loadout": deepcopy(mapping.get("module_loadout")),
            "damage_slider": deepcopy(mapping.get("damage_slider")),
            "orb_distance": deepcopy(mapping.get("orb_distance")),
            "confirmed_local": {
                "available": confirmed_local.get("available") is True,
                "generation": confirmed_local.get("generation"),
                "document_fingerprint": confirmed_local.get(
                    "document_fingerprint"
                ),
                "applied_event_ids": list(
                    confirmed_local.get("applied_event_ids") or ()
                ),
                "blocked_checks": list(
                    confirmed_local.get("blocked_checks") or ()
                ),
                "lifecycle": lifecycle,
            },
        }
    )


def _apply_confirmed_local_mappings(
    mapping: dict[str, Any],
    *,
    data_version: Optional[int],
    game_version: Optional[int],
    root_class: str,
    mapping_resolution: str,
    authority_mapping_id: Optional[str],
    structural_mapping_id: Optional[str],
    dependency_fingerprint: str,
    store: Optional[ConfirmedLocalMappingStore] = None,
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    """Apply safe local additions to a clone for this fresh decode only."""

    projection = _empty_confirmed_local_projection()
    if (
        mapping_resolution
        not in {"exact", "compatible_exact_revision"}
        or type(data_version) is not int
        or type(game_version) is not int
    ):
        return mapping, projection, ()
    owner = store or ConfirmedLocalMappingStore()
    try:
        document = owner.load(data_version, game_version)
    except (ConfirmedLocalMappingError, OSError) as exc:
        projection.update(
            available=False,
            blocked_checks=[],
            items=[
                {
                    "state": "invalid_local_store",
                    "check_id": "modules",
                    "reason": str(exc),
                }
            ],
        )
        return (
            mapping,
            projection,
            (
                "The confirmed-local player-save mapping store is invalid; "
                "canonical Module mappings remain authoritative and unknown "
                "values continue through UI fallback.",
            ),
        )
    if document is None:
        return mapping, projection, ()
    identity = document["identity"]
    expected_identity = {
        "mapping_id": str(mapping.get("mapping_id") or ""),
        "data_version": data_version,
        "game_version": game_version,
        "root_class": root_class,
    }
    projection["generation"] = document["generation"]
    projection["document_fingerprint"] = fingerprint_json(document)
    if identity != expected_identity:
        projection.update(
            available=False,
            blocked_checks=[],
            items=[
                {
                    "state": "identity_conflict",
                    "check_id": "modules",
                    "reason": "local confirmation identity does not match the save",
                }
            ],
        )
        return (
            mapping,
            projection,
            (
                "The confirmed-local player-save mapping identity changed; "
                "the local overlay was ignored while canonical Module "
                "mappings remain authoritative.",
            ),
        )

    applied: list[str] = []
    items: list[dict[str, Any]] = []
    for event in active_confirmations(document):
        lifecycle = _confirmed_local_event_lifecycle(
            identity,
            event,
            dependency_fingerprint=dependency_fingerprint,
        )
        item = _confirmed_local_item(identity, event, lifecycle)
        if lifecycle in {"integrated", "mirror_pending"}:
            items.append(item)
            continue
        if lifecycle == "canonical_conflict":
            items.append(item)
            continue
        if lifecycle == "reconfirmation_required":
            items.append(item)
            continue
        identities = mapping.get("module_info_indices")
        if not isinstance(identities, dict):
            item["state"] = "reconfirmation_required"
            item["reason"] = "canonical module identity owner is unavailable"
            items.append(item)
            continue
        raw_key = str(event["raw_value"])
        existing = identities.get(raw_key)
        expected = {
            "name": event["semantic_value"],
            "family": event["scope"]["family"],
        }
        if existing is not None and existing != expected:
            item["state"] = "canonical_conflict"
            item["reason"] = "canonical module identity conflicts with local evidence"
            items.append(item)
            continue
        identities[raw_key] = expected
        applied.append(event["event_id"])
        item["state"] = (
            "authority_pending"
            if lifecycle == "authority_pending"
            else "active_local"
        )
        item["reason"] = (
            "local exact-version Module identity confirmation is active; "
            "slot-scoped save authority is unchanged"
        )
        items.append(item)
    projection.update(
        applied_event_ids=applied,
        blocked_checks=[],
        items=items,
    )
    warnings: tuple[str, ...] = ()
    if applied:
        warnings = (
            "A locally confirmed exact-version Module identity was applied "
            "to this fresh save decode for mapping diagnostics only; "
            "slot-scoped save authority and canonical repository integration "
            "remain unchanged.",
        )
    if any(
        item.get("state")
        in {"canonical_conflict", "reconfirmation_required"}
        for item in items
    ):
        warnings += (
            "A confirmed-local Module identity conflicted with or outlived its "
            "canonical dependency; the local event was ignored and canonical "
            "values remain authoritative.",
        )
    return mapping, projection, warnings


def _canonical_module_event_lifecycle(event: Mapping[str, Any]) -> str:
    """Classify repository owner/mirror integration for one local event."""

    try:
        authority = _mapping_by_id(str(event["authority_mapping_id"]))
        structural = _mapping_by_id(str(event["structural_mapping_id"]))
    except (KeyError, PlayerSaveError):
        return "reconfirmation_required"
    owner_state = _canonical_module_event_state(authority, event)
    mirror_state = _canonical_module_event_state(structural, event)
    if "conflict" in {owner_state, mirror_state}:
        return "canonical_conflict"
    if owner_state == "match" and mirror_state == "match":
        return "integrated"
    if owner_state == "match":
        return "mirror_pending"
    if mirror_state == "match":
        return "authority_pending"
    if "unavailable" in {owner_state, mirror_state}:
        return "reconfirmation_required"
    return "active_local"


def _confirmed_local_event_lifecycle(
    identity: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    dependency_fingerprint: Optional[str] = None,
) -> str:
    """Use one lifecycle classifier for both decode and status projection."""

    lifecycle = _canonical_module_event_lifecycle(event)
    if lifecycle in {"integrated", "mirror_pending", "canonical_conflict"}:
        return lifecycle
    current_dependency = dependency_fingerprint
    if current_dependency is None:
        current_dependency = _current_module_event_dependency_fingerprint(
            identity,
            event,
        )
    if (
        current_dependency is None
        or event.get("dependency_fingerprint") != current_dependency
    ):
        return "reconfirmation_required"
    return lifecycle


def _current_module_event_dependency_fingerprint(
    identity: Mapping[str, Any],
    event: Mapping[str, Any],
) -> Optional[str]:
    try:
        authority_id = str(event["authority_mapping_id"])
        structural_id = str(event["structural_mapping_id"])
        authority = _mapping_by_id(authority_id)
        structural = _mapping_by_id(structural_id)
        resolution = str(event["mapping_resolution"])
        if resolution == "exact":
            mapping = deepcopy(structural)
        elif resolution == "compatible_exact_revision":
            mapping = _compatible_mapping(
                structural=structural,
                authority=authority,
                data_version=int(identity["data_version"]),
                game_version=int(identity["game_version"]),
                mapping_id=str(identity["mapping_id"]),
                retain_exact_profile_progression=True,
            )
        else:
            return None
        return _mapping_semantic_fingerprint(
            mapping,
            authority_mapping_id=authority_id,
            structural_mapping_id=structural_id,
        )
    except (KeyError, TypeError, ValueError, PlayerSaveError):
        return None


def _canonical_module_event_state(
    mapping: Mapping[str, Any],
    event: Mapping[str, Any],
) -> str:
    module_loadout = mapping.get("module_loadout")
    if not isinstance(module_loadout, Mapping):
        return "unavailable"
    specs = [
        *(
            module_loadout.get("primary")
            if isinstance(module_loadout.get("primary"), list)
            else []
        ),
        *(
            module_loadout.get("assist")
            if isinstance(module_loadout.get("assist"), list)
            else []
        ),
    ]
    identities = _module_identity_options(mapping, specs)
    if identities is None:
        return "unavailable"
    raw_value = event["raw_value"]
    semantic = event["semantic_value"]
    family = str((event.get("scope") or {}).get("family") or "")
    known_identity = identities.get(raw_value)
    if known_identity is not None:
        return (
            "match"
            if known_identity == (semantic, family)
            else "conflict"
        )
    semantic_identity = next(
        (
            (info_index, mapped_family)
            for info_index, (name, mapped_family) in identities.items()
            if name.casefold() == semantic.casefold()
        ),
        None,
    )
    if semantic_identity is not None:
        return "conflict"
    return "absent"


def _confirmed_local_item(
    identity: Mapping[str, Any],
    event: Mapping[str, Any],
    lifecycle: str,
) -> dict[str, Any]:
    reasons = {
        "active_local": (
            "canonical Module identity integration is pending; slot-scoped "
            "save authority is unchanged"
        ),
        "authority_pending": (
            "canonical Module identity owner integration is pending; "
            "slot-scoped save authority is unchanged"
        ),
        "mirror_pending": (
            "exact-version Module identity mirror integration is pending"
        ),
        "integrated": "canonical Module identity owner and mirror agree",
        "canonical_conflict": (
            "canonical Module identity conflicts with local evidence"
        ),
        "reconfirmation_required": (
            "canonical Module identity dependency is unavailable"
        ),
    }
    return {
        "event_id": event["event_id"],
        "candidate_record_id": event["candidate_record_id"],
        "mapping_id": identity["mapping_id"],
        "data_version": identity["data_version"],
        "game_version": identity["game_version"],
        "check_id": event["check_id"],
        "value_kind": event["value_kind"],
        "raw_value": event["raw_value"],
        "semantic_value": event["semantic_value"],
        "scope": dict(event["scope"]),
        "state": lifecycle,
        "reason": reasons[lifecycle],
        "recorded_at": event["recorded_at"],
    }


def confirmed_local_mapping_status(
    *,
    store: Optional[ConfirmedLocalMappingStore] = None,
    candidate_store: Optional[AppendOnlyMappingCandidateStore] = None,
    repository_root: Path | str = ROOT,
    candidate_status: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Project durable local identity evidence and receipts for status UIs."""

    owner = store or ConfirmedLocalMappingStore()
    if candidate_status is None:
        candidate_status = mapping_candidate_review_status(
            store=candidate_store,
            repository_root=repository_root,
        )
    try:
        documents = owner.list_documents()
    except (ConfirmedLocalMappingError, OSError) as exc:
        return {
            "schema_version": 2,
            "available": False,
            "blocks_startup": False,
            "items": list(candidate_status.get("items") or ()),
            "counts": {
                **dict(candidate_status.get("counts") or {}),
                "invalid_local_store": 1,
            },
            "reason": str(exc),
        }
    items: list[dict[str, Any]] = []
    for document in documents:
        revoked = {
            event["target_event_id"]
            for event in document["events"]
            if event["event_type"] == "revoke"
        }
        for event in document["events"]:
            if event["event_type"] != "accept":
                continue
            lifecycle = (
                "revoked"
                if event["event_id"] in revoked
                else _confirmed_local_event_lifecycle(
                    document["identity"],
                    event,
                )
            )
            item = _confirmed_local_item(
                document["identity"],
                event,
                lifecycle if lifecycle != "revoked" else "integrated",
            )
            if lifecycle == "revoked":
                item["state"] = "revoked"
                item["reason"] = "local confirmation was explicitly revoked"
            items.append(item)
    integration_lifecycle_states = {
        "integration_recovery_required",
        "integration_unconfirmed",
        "production_validation_pending",
        "promotion_pending",
        "restaging_required",
    }
    integration_candidate_ids = {
        str(item.get("candidate_record_id") or "")
        for item in candidate_status.get("items") or ()
        if item.get("state") in integration_lifecycle_states
    }
    items = [
        item
        for item in items
        if item["candidate_record_id"] not in integration_candidate_ids
    ]
    confirmed_candidate_ids = {
        item["candidate_record_id"]
        for item in items
        if item["state"] != "revoked"
    }
    candidate_items = [
        dict(item)
        for item in candidate_status.get("items") or ()
        if item.get("candidate_record_id") not in confirmed_candidate_ids
    ]
    combined_items = [*items, *candidate_items]
    counts: dict[str, int] = {}
    for item in combined_items:
        state = item["state"]
        counts[state] = counts.get(state, 0) + 1
    return {
        "schema_version": 2,
        "available": candidate_status.get("available") is not False,
        "blocks_startup": False,
        "items": combined_items,
        "counts": counts,
        "reason": str(candidate_status.get("reason") or ""),
    }


def _decoded_additional_fields(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> tuple[str, ...]:
    expected = set(_raw_field_manifest_names(mapping))
    return tuple(
        sorted(
            key
            for key in decoded
            if isinstance(key, str) and key not in expected
        )
    )


def _validate_revision_compatibility(
    mapping: Mapping[str, Any],
    *,
    mappings_by_id: Mapping[str, Mapping[str, Any]],
    source: Path | str,
) -> None:
    compatibility = mapping.get("revision_compatibility")
    if compatibility is None:
        return
    expected_keys = {
        "schema_version",
        "authority_mapping_id",
        "validated_checks",
        "runtime_save",
        "allow_forward_game_versions",
    }
    if not isinstance(compatibility, Mapping) or set(compatibility) != expected_keys:
        raise PlayerSaveError(
            f"revision compatibility is malformed in {source}"
        )
    if (
        compatibility.get("schema_version")
        != REVISION_COMPATIBILITY_SCHEMA_VERSION
        or type(compatibility.get("runtime_save")) is not bool
        or type(compatibility.get("allow_forward_game_versions")) is not bool
    ):
        raise PlayerSaveError(
            f"revision compatibility policy is invalid in {source}"
        )
    authority_id = str(
        compatibility.get("authority_mapping_id") or ""
    ).strip()
    authority = mappings_by_id.get(authority_id)
    if authority is None or authority is mapping:
        raise PlayerSaveError(
            f"revision compatibility authority is invalid in {source}"
        )
    identity = mapping.get("identity") or {}
    authority_identity = authority.get("identity") or {}
    if not (
        identity.get("data_version") == authority_identity.get("data_version")
        and identity.get("root_class") == authority_identity.get("root_class")
        and type(identity.get("game_version")) is int
        and type(authority_identity.get("game_version")) is int
        and identity["game_version"] > authority_identity["game_version"]
    ):
        raise PlayerSaveError(
            f"revision compatibility identity is invalid in {source}"
        )

    source_fields = set(_raw_field_manifest_names(mapping))
    authority_fields = set(_raw_field_manifest_names(authority))
    if not authority_fields <= source_fields:
        raise PlayerSaveError(
            f"revision compatibility removed authority fields in {source}"
        )
    additions = source_fields - authority_fields
    source_unknown = set(
        (((mapping.get("raw_field_manifest") or {}).get("dispositions") or {}).get(
            "unknown"
        ) or ())
    )
    if not additions <= source_unknown:
        raise PlayerSaveError(
            "revision compatibility additions must remain unknown and "
            f"unpublished in {source}"
        )

    source_required = {str(value) for value in mapping.get("required_fields") or ()}
    authority_required = {
        str(value) for value in authority.get("required_fields") or ()
    }
    if not authority_required <= source_required:
        raise PlayerSaveError(
            f"revision compatibility removed required fields in {source}"
        )
    source_lengths = mapping.get("required_array_lengths") or {}
    for field_name, expected_length in (
        authority.get("required_array_lengths") or {}
    ).items():
        if source_lengths.get(field_name) != expected_length:
            raise PlayerSaveError(
                "revision compatibility changed an authority array length "
                f"for {field_name!r} in {source}"
            )

    checks = compatibility.get("validated_checks")
    if (
        not isinstance(checks, list)
        or any(not isinstance(check, str) or not check for check in checks)
        or len(checks) != len(set(checks))
        or not set(checks) <= set(authority.get("validated_checks") or ())
    ):
        raise PlayerSaveError(
            f"revision compatibility validated checks are invalid in {source}"
        )
    if compatibility.get("runtime_save") is True and not isinstance(
        authority.get("runtime_save"), Mapping
    ):
        raise PlayerSaveError(
            f"revision compatibility runtime authority is missing in {source}"
        )
    extensions = mapping.get("runtime_save_extensions")
    if isinstance(extensions, Mapping):
        history_spec = (
            (authority.get("runtime_save") or {}).get("battle_history")
            if isinstance(authority.get("runtime_save"), Mapping)
            else None
        )
        active_tallies = extensions.get("active_tallies")
        tally_scope = (
            active_tallies.get("scope")
            if isinstance(active_tallies, Mapping)
            else None
        )
        tally_binding = (
            tally_scope.get("binding")
            if isinstance(tally_scope, Mapping)
            else None
        )
        if (
            not isinstance(history_spec, Mapping)
            or not isinstance(tally_binding, Mapping)
            or tally_binding.get("history_entry_class")
            != history_spec.get("entry_class")
            or tally_binding.get("history_capacity")
            != history_spec.get("capacity")
        ):
            raise PlayerSaveError(
                "active-tally terminal-history binding disagrees with "
                f"runtime authority in {source}"
            )
        direct_history_sources: set[str] = set()
        for section in history_spec.get("more_stats_sections") or ():
            for raw_row in (section or {}).get("rows") or ():
                row_spec = raw_row[1]
                if isinstance(row_spec, str):
                    direct_history_sources.add(row_spec)
                elif isinstance(row_spec, Mapping) and isinstance(
                    row_spec.get("source"), str
                ):
                    direct_history_sources.add(row_spec["source"])
        terminal_sources = {
            str(field_spec["terminal_source"])
            for component in (
                (extensions.get("active_tallies") or {}).get("components")
                or {}
            ).values()
            for field_spec in ((component or {}).get("fields") or {}).values()
            if isinstance(field_spec, Mapping)
            and field_spec.get("terminal_source") is not None
        }
        if not terminal_sources <= direct_history_sources:
            raise PlayerSaveError(
                "active-tally terminal sources are absent from runtime "
                f"authority in {source}"
            )


def _validate_shape(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> list[str]:
    """Validate only the decoded envelope shared by every capability.

    Field presence, additions, and array lengths are capability-local concerns.
    They are reported separately as manifest drift and must not erase unrelated
    normalized evidence.
    """

    warnings: list[str] = []
    identity = mapping.get("identity") or {}
    expected_class = str(identity.get("root_class") or "")
    actual_class = str(decoded.get("__class__") or "")
    if actual_class != expected_class:
        warnings.append(
            f"root class changed: expected {expected_class!r}, got {actual_class!r}"
        )
    non_string_fields = [key for key in decoded if not isinstance(key, str)]
    if non_string_fields:
        warnings.append(
            "decoded root contains non-string field names: "
            + _summarize_field_names(str(key) for key in non_string_fields)
        )
    return warnings


def _manifest_drift_warnings(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    allow_additional_fields: bool = False,
) -> list[str]:
    """Describe closed-world manifest drift without granting it global veto."""

    warnings: list[str] = []
    actual_fields = {key for key in decoded if isinstance(key, str)}
    expected_fields = set(_raw_field_manifest_names(mapping))
    missing_fields = expected_fields - actual_fields
    unexpected_fields = actual_fields - expected_fields
    if missing_fields:
        warnings.append(
            "raw field manifest mismatch: "
            f"{len(missing_fields)} classified field(s) are missing: "
            + _summarize_field_names(missing_fields)
        )
    if unexpected_fields and not allow_additional_fields:
        warnings.append(
            "raw field manifest mismatch: "
            f"{len(unexpected_fields)} unclassified field(s) were decoded: "
            + _summarize_field_names(unexpected_fields)
        )
    for field in mapping.get("required_fields") or []:
        if field not in decoded:
            warnings.append(f"required field is missing: {field}")
    for field, expected_length in (
        mapping.get("required_array_lengths") or {}
    ).items():
        value = decoded.get(field)
        if not _is_sequence(value):
            warnings.append(f"{field} is not an array")
            continue
        if len(value) != int(expected_length):
            warnings.append(
                f"{field} length changed: expected {expected_length}, got {len(value)}"
            )
    return warnings


def _validate_raw_field_manifest(
    mapping: Mapping[str, Any],
    *,
    source: Path | str,
) -> None:
    manifest = mapping.get("raw_field_manifest")
    if not isinstance(manifest, Mapping):
        raise PlayerSaveError(f"raw field manifest is missing in {source}")
    if manifest.get("schema_version") != RAW_FIELD_MANIFEST_SCHEMA_VERSION:
        raise PlayerSaveError(
            f"unsupported raw field manifest schema in {source}"
        )
    audit_id = str(manifest.get("audit_id") or "").strip()
    if not audit_id:
        raise PlayerSaveError(f"raw field manifest audit_id is missing in {source}")
    identity = mapping.get("identity") or {}
    expected_class = str(identity.get("root_class") or "")
    manifest_class = str(manifest.get("root_class") or "")
    if manifest_class != expected_class:
        raise PlayerSaveError(
            "raw field manifest root_class does not match mapping identity "
            f"in {source}"
        )

    dispositions = manifest.get("dispositions")
    if not isinstance(dispositions, Mapping):
        raise PlayerSaveError(f"raw field dispositions are missing in {source}")
    actual_categories = {str(name) for name in dispositions}
    if actual_categories != RAW_FIELD_DISPOSITION_NAMES:
        missing = sorted(RAW_FIELD_DISPOSITION_NAMES - actual_categories)
        unexpected = sorted(actual_categories - RAW_FIELD_DISPOSITION_NAMES)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise PlayerSaveError(
            f"raw field disposition categories are invalid in {source}: "
            + "; ".join(details)
        )

    seen: dict[str, str] = {}
    for disposition in (
        "structural",
        "automation_gating",
        "profile_observation",
        "runtime_observation",
        "private",
        "unknown",
    ):
        fields = dispositions.get(disposition)
        _validate_sorted_field_names(
            fields,
            label=f"raw field disposition {disposition}",
            source=source,
        )
        for field_name in fields:
            _record_raw_field_disposition(
                seen,
                field_name,
                disposition,
                source=source,
            )

    ignored_groups = dispositions.get("ignored_with_reason")
    if not isinstance(ignored_groups, list):
        raise PlayerSaveError(
            f"raw ignored-field groups must be an array in {source}"
        )
    ignored_reasons: set[str] = set()
    for index, group in enumerate(ignored_groups):
        if not isinstance(group, Mapping):
            raise PlayerSaveError(
                f"raw ignored-field group {index} is invalid in {source}"
            )
        reason = str(group.get("reason") or "").strip()
        if not reason:
            raise PlayerSaveError(
                f"raw ignored-field group {index} has no reason in {source}"
            )
        if reason in ignored_reasons:
            raise PlayerSaveError(
                f"raw ignored-field reason is duplicated in {source}: {reason}"
            )
        ignored_reasons.add(reason)
        fields = group.get("fields")
        _validate_sorted_field_names(
            fields,
            label=f"raw ignored-field group {reason}",
            source=source,
        )
        for field_name in fields:
            _record_raw_field_disposition(
                seen,
                field_name,
                "ignored_with_reason",
                source=source,
            )

    expected_count = manifest.get("field_count")
    if type(expected_count) is not int or expected_count < 1:
        raise PlayerSaveError(f"raw field_count is invalid in {source}")
    if expected_count != len(seen):
        raise PlayerSaveError(
            f"raw field_count mismatch in {source}: "
            f"declared {expected_count}, classified {len(seen)}"
        )
    expected_hash = str(manifest.get("field_name_sha256") or "").strip()
    actual_hash = _raw_field_name_sha256(seen)
    if expected_hash != actual_hash:
        raise PlayerSaveError(
            f"raw field-name hash mismatch in {source}: "
            f"declared {expected_hash or 'missing'}, calculated {actual_hash}"
        )

    for field_name in mapping.get("required_fields") or []:
        if str(field_name) not in seen:
            raise PlayerSaveError(
                f"required field {field_name!r} is absent from the raw field "
                f"manifest in {source}"
            )
    for field_name in (mapping.get("required_array_lengths") or {}):
        if str(field_name) not in seen:
            raise PlayerSaveError(
                f"required array {field_name!r} is absent from the raw field "
                f"manifest in {source}"
            )
    progression = mapping.get("profile_progression") or {}
    for component_name, fields in (progression.get("components") or {}).items():
        for output_name, field_spec in (fields or {}).items():
            source_field = str((field_spec or {}).get("source") or "").strip()
            if source_field not in seen:
                raise PlayerSaveError(
                    "profile progression source field is absent from the raw "
                    f"field manifest in {source}: "
                    f"{component_name}.{output_name} -> {source_field or 'missing'}"
                )


def _validate_runtime_save_extensions(
    mapping: Mapping[str, Any],
    *,
    source: Path | str,
) -> None:
    """Validate exact-version runtime additions against their raw allowlist."""

    extensions = mapping.get("runtime_save_extensions")
    if extensions is None:
        return
    if not isinstance(extensions, Mapping) or set(extensions) != {
        "active_tallies"
    }:
        raise PlayerSaveError(
            f"runtime-save extensions are malformed in {source}"
        )
    active = extensions.get("active_tallies")
    expected_keys = {
        "schema_version",
        "capability_id",
        "forward_policy",
        "audit_id",
        "evidence_level",
        "scope",
        "components",
    }
    if not isinstance(active, Mapping) or set(active) != expected_keys:
        raise PlayerSaveError(
            f"active-tally runtime extension is malformed in {source}"
        )
    if (
        active.get("schema_version") != RUNTIME_SAVE_EXTENSION_SCHEMA_VERSION
        or active.get("capability_id")
        != "thetower.player_save.active_run_tallies.v1"
        or active.get("forward_policy") != "additive_dependencies"
        or not str(active.get("audit_id") or "").strip()
        or active.get("evidence_level") != "cross_channel"
    ):
        raise PlayerSaveError(
            f"active-tally runtime authority is invalid in {source}"
        )
    scope = active.get("scope")
    expected_scope = {
        "semantics": {
            "state": "cumulative_active_round",
            "identity": "game_version_tier_started_count_seed",
            "checkpoint_order": "capture_time_source_identity",
            "wave_relation": "optional_nondecreasing_rate_dependency",
            "terminal_identity": (
                "source_order_tail_battle_date_tier_wave_kind"
            ),
            "terminal_relation": (
                "causally_bound_natural_terminal_nondecreasing"
            ),
        },
        "binding": {
            "game_version": "versionNumber",
            "save_revision": "saveRevision",
            "round_active": "roundActiveBool",
            "current_tier": "currentTier",
            "current_wave": "currentWave",
            "round_seed": "roundSeed",
            "round_counter_vector": "roundsStartedThisTier",
            "history_container": "battleHistory",
            "history_entry_class": "BattleHistoryEntry",
            "history_capacity": 30,
            "history_entry_count": "battleHistory.length",
            "history_battle_date": "battleDate",
            "history_tier": "tier",
            "history_kind": "isTournament",
            "history_wave": "wave",
            "history_game_time": "gameTime",
            "history_real_time": "realTime",
        },
    }
    if scope != expected_scope:
        raise PlayerSaveError(
            f"active-tally scope authority is invalid in {source}"
        )

    dispositions = (
        (mapping.get("raw_field_manifest") or {}).get("dispositions") or {}
    )
    allowlisted = set(dispositions.get("runtime_observation") or ())
    components = active.get("components")
    if not isinstance(components, Mapping) or not components:
        raise PlayerSaveError(
            f"active-tally components are unavailable in {source}"
        )
    used_sources: set[str] = set()
    used_terminal_sources: set[str] = set()
    safe_name = re.compile(r"[a-z][a-z0-9_]{0,95}")
    safe_source_name = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
    for component_name, component in components.items():
        if safe_name.fullmatch(str(component_name)) is None:
            raise PlayerSaveError(
                f"active-tally component name is invalid in {source}"
            )
        if not isinstance(component, Mapping) or set(component) != {
            "fields",
            "derived",
        }:
            raise PlayerSaveError(
                f"active-tally component {component_name!r} is malformed "
                f"in {source}"
            )
        fields = component.get("fields")
        if not isinstance(fields, Mapping) or not fields:
            raise PlayerSaveError(
                f"active-tally component {component_name!r} has no fields "
                f"in {source}"
            )
        for output_name, field_spec in fields.items():
            if safe_name.fullmatch(str(output_name)) is None:
                raise PlayerSaveError(
                    f"active-tally output name is invalid in {source}"
                )
            if not isinstance(field_spec, Mapping):
                raise PlayerSaveError(
                    f"active-tally field {component_name}.{output_name} is "
                    f"malformed in {source}"
                )
            required = {"source", "kind", "unit", "monotonic"}
            optional = {"terminal_source"}
            if not required <= set(field_spec) or not set(field_spec) <= (
                required | optional
            ):
                raise PlayerSaveError(
                    f"active-tally field {component_name}.{output_name} is "
                    f"malformed in {source}"
                )
            source_field = str(field_spec.get("source") or "")
            terminal_source = field_spec.get("terminal_source")
            if (
                source_field not in allowlisted
                or source_field in used_sources
                or field_spec.get("kind")
                not in {"nonnegative_integer", "nonnegative_number"}
                or safe_name.fullmatch(str(field_spec.get("unit") or ""))
                is None
                or field_spec.get("monotonic") is not True
                or (
                    terminal_source is not None
                    and (
                        safe_source_name.fullmatch(str(terminal_source)) is None
                        or str(terminal_source) in used_terminal_sources
                    )
                )
            ):
                raise PlayerSaveError(
                    f"active-tally field {component_name}.{output_name} has "
                    f"invalid authority in {source}"
                )
            used_sources.add(source_field)
            if terminal_source is not None:
                used_terminal_sources.add(str(terminal_source))

        derived = component.get("derived")
        if not isinstance(derived, Mapping):
            raise PlayerSaveError(
                f"active-tally derived values changed shape in {source}"
            )
        for output_name, derived_spec in derived.items():
            if (
                safe_name.fullmatch(str(output_name)) is None
                or not isinstance(derived_spec, Mapping)
                or set(derived_spec)
                != {"derive", "numerator", "denominator", "unit"}
                or derived_spec.get("derive")
                not in {"per_real_hour", "per_real_minute", "ratio"}
                or derived_spec.get("numerator") not in fields
                or derived_spec.get("denominator") not in fields
                or safe_name.fullmatch(str(derived_spec.get("unit") or ""))
                is None
            ):
                raise PlayerSaveError(
                    f"active-tally derivation {component_name}.{output_name} "
                    f"is invalid in {source}"
                )
    if used_sources != allowlisted:
        raise PlayerSaveError(
            "runtime-observation fields must be published exactly once by "
            f"the active-tally extension in {source}"
        )
    active_tally_contract_fingerprints(active)


def _validate_sorted_field_names(
    fields: Any,
    *,
    label: str,
    source: Path | str,
) -> None:
    if not isinstance(fields, list):
        raise PlayerSaveError(f"{label} must be an array in {source}")
    if any(not isinstance(field, str) or not field for field in fields):
        raise PlayerSaveError(f"{label} contains an invalid field name in {source}")
    if fields != sorted(fields) or len(fields) != len(set(fields)):
        raise PlayerSaveError(
            f"{label} must contain unique sorted field names in {source}"
        )


def _record_raw_field_disposition(
    seen: dict[str, str],
    field_name: str,
    disposition: str,
    *,
    source: Path | str,
) -> None:
    prior = seen.get(field_name)
    if prior is not None:
        raise PlayerSaveError(
            f"raw field {field_name!r} has duplicate dispositions in {source}: "
            f"{prior}, {disposition}"
        )
    seen[field_name] = disposition


def _raw_field_manifest_names(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    dispositions = (mapping.get("raw_field_manifest") or {}).get(
        "dispositions"
    ) or {}
    names: list[str] = []
    for disposition in (
        "structural",
        "automation_gating",
        "profile_observation",
        "runtime_observation",
        "private",
        "unknown",
    ):
        names.extend(str(field) for field in dispositions.get(disposition) or ())
    for group in dispositions.get("ignored_with_reason") or ():
        names.extend(str(field) for field in (group or {}).get("fields") or ())
    return tuple(sorted(names))


def _raw_field_name_sha256(field_names: Any) -> str:
    canonical = "".join(f"{name}\n" for name in sorted(field_names))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _summarize_field_names(field_names: Any, *, limit: int = 8) -> str:
    ordered = sorted(str(name) for name in field_names)
    shown = ordered[:limit]
    suffix = f", ... (+{len(ordered) - limit})" if len(ordered) > limit else ""
    return ", ".join(shown) + suffix


def _build_profile_summary(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> dict[str, Any]:
    cards = mapping.get("cards") or {}
    slots_per_preset = int(cards.get("slots_per_preset") or 0)
    preset_count = int(cards.get("preset_count") or 0)
    assigned_raw = decoded.get(str(cards.get("assigned_field") or ""))
    assigned = list(assigned_raw) if _is_sequence(assigned_raw) else []
    assigned_counts = (
        [
            sum(
                bool(value)
                for value in assigned[
                    index * slots_per_preset : (index + 1) * slots_per_preset
                ]
            )
            for index in range(preset_count)
        ]
        if slots_per_preset > 0
        else []
    )
    presets = {
        check_id: _selected_preset(decoded, spec)
        for check_id, spec in (mapping.get("presets") or {}).items()
    }
    array_lengths = {
        field: len(decoded.get(field) or [])
        for field in (
            "researchLevel",
            "upgradeWorkshopLevel",
            "upgradeWorkshopDefenseLevel",
            "upgradeWorkshopUtilityLevel",
            "enhancementLevel",
            "enhancementDefenseLevel",
            "enhancementUtilityLevel",
            "cardLevel",
            "moduleEquipped",
            "moduleRecords",
            "battleHistory",
        )
        if _is_sequence(decoded.get(field))
    }
    return {
        "presets": presets,
        "cards": {
            "base_slots": _optional_int(
                decoded.get(str(cards.get("base_slots_field") or ""))
            ),
            "effective_slots": slots_per_preset,
            "preset_count": preset_count,
            "assigned_counts": assigned_counts,
        },
        "array_lengths": array_lengths,
        "owned_counts": {
            "cards": _boolean_sequence_count(decoded.get("cardUnlocked")),
            "ultimate_weapons": sum(
                bool(value)
                for value in (
                    decoded.get("ultimateWeaponUnlocked")
                    if _is_sequence(decoded.get("ultimateWeaponUnlocked"))
                    else ()
                )
            ),
            "guardian_chips": _boolean_sequence_count(
                decoded.get("guardianChipUnlocked")
            ),
        },
    }


def _boolean_sequence_count(value: Any) -> int:
    if not _is_sequence(value):
        return 0
    return sum(item is True for item in value)


def _build_checks(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    captured_at: datetime,
    canonical_module_info_indices: Any = None,
) -> dict[str, SaveCheckEvidence]:
    checks: dict[str, SaveCheckEvidence] = {}
    for check_id, spec in (mapping.get("presets") or {}).items():
        selected = _selected_preset(decoded, spec)
        status = "observed" if selected.get("active_name") else "unmapped"
        checks[str(check_id)] = SaveCheckEvidence(
            check_id=str(check_id),
            status=status,
            value=selected.get("active_name"),
            source_fields=(str(spec["names_field"]), str(spec["active_field"])),
            reason=(
                ""
                if status == "observed"
                else "active preset could not be resolved"
            ),
            authority={"kind": "matching_value"},
        )

    auto_pick_value = decoded.get("autoPickPerk")
    auto_pick_valid = isinstance(auto_pick_value, bool)
    checks["auto_pick_perks"] = SaveCheckEvidence(
        check_id="auto_pick_perks",
        status="observed" if auto_pick_valid else "unmapped",
        value=auto_pick_value if auto_pick_valid else None,
        source_fields=("autoPickPerk",),
        complete=auto_pick_valid,
        reason="" if auto_pick_valid else "autoPickPerk is not an exact boolean",
        authority={"kind": "exact_values", "values": [True]},
    )

    checks["card_recharge_modes"] = _card_recharge_mode_evidence(
        decoded,
        mapping,
    )
    checks["damage_slider"] = _damage_slider_evidence(decoded, mapping)
    checks["orb_distance"] = _orb_distance_evidence(decoded, mapping)

    perk_ids = mapping.get("perk_ids") or {}
    first_id = _exact_int(decoded.get("firstPerkIndex"))
    first_name = perk_ids.get(str(first_id)) if first_id is not None else None
    first_candidates: list[dict[str, Any]] = []
    if first_id is not None and first_id >= 0 and first_name is None:
        candidate = _pending_mapping_candidate(
            value_kind="perk_id",
            raw_value=first_id,
            pairing_method="exact_locator",
            locator="selected",
            expected_observation_count=1,
            known_semantic_values=tuple(
                str(value) for value in perk_ids.values()
            ),
        )
        if candidate is not None:
            first_candidates.append(candidate)
    checks["perk_first_choice"] = SaveCheckEvidence(
        check_id="perk_first_choice",
        status="observed" if first_name else "unmapped",
        value=first_name,
        source_fields=("firstPerkIndex",),
        reason="" if first_name else f"unmapped perk id {first_id}",
        authority={"kind": "matching_value"},
        diagnostics={"mapping_candidates": first_candidates},
    )

    bans, bans_complete, bans_reason = _validated_selected_id_slots(
        decoded.get("bannedPerksIndex"),
        perk_ids,
        mapping.get("perk_bans"),
        label="perk ban",
    )
    checks["perk_bans"] = SaveCheckEvidence(
        check_id="perk_bans",
        status="observed" if bans_complete else "unmapped",
        value=bans,
        source_fields=("bannedPerksIndex",),
        complete=bans_complete,
        reason=bans_reason,
        authority={"kind": "matching_value"},
        diagnostics={
            "mapping_candidates": _selected_slot_mapping_candidates(
                decoded.get("bannedPerksIndex"),
                perk_ids,
                mapping.get("perk_bans"),
                value_kind="perk_id",
            )
        },
    )

    raw_auto_order = decoded.get("autoPickOrder")
    auto_order_spec = mapping.get("auto_pick_order")
    ranked_count = (
        _optional_int(auto_order_spec.get("ranked_count"))
        if isinstance(auto_order_spec, Mapping)
        else None
    )
    auto_order, order_complete, order_reason = _validated_auto_pick_order(
        raw_auto_order,
        perk_ids,
        auto_order_spec,
    )
    checks["perk_auto_pick_order"] = SaveCheckEvidence(
        check_id="perk_auto_pick_order",
        status="observed" if auto_order else "unmapped",
        value=auto_order,
        source_fields=("autoPickOrder",),
        complete=order_complete,
        reason=order_reason,
        authority={"kind": "prefix", "maximum_length": ranked_count or 0},
        diagnostics={
            "mapping_candidates": _ranked_mapping_candidates(
                raw_auto_order,
                perk_ids,
                ranked_count=ranked_count,
                total_count=(
                    _optional_int(auto_order_spec.get("total_count"))
                    if isinstance(auto_order_spec, Mapping)
                    else None
                ),
                value_kind="perk_id",
                observation_count_policy="minimum",
            )
        },
    )

    locks, unmanaged_locks, unmapped_lock_count, lock_complete, lock_reason = (
        _mapped_free_upgrade_locks(
            decoded,
            mapping,
        )
    )
    validated_lock_set = list(
        mapping.get("validated_free_upgrade_lock_set") or ()
    )
    checks["free_upgrade_locks"] = SaveCheckEvidence(
        check_id="free_upgrade_locks",
        status="observed" if lock_complete else "unmapped",
        value=locks,
        source_fields=tuple((mapping.get("free_upgrade_lock_fields") or {}).keys()),
        complete=lock_complete,
        reason=lock_reason,
        authority={"kind": "required_subset", "values": validated_lock_set},
        diagnostics={
            "unmanaged_locks": unmanaged_locks,
            "unmapped_locked_slot_count": unmapped_lock_count,
        },
    )

    checks["modules"] = _module_loadout_evidence(
        decoded,
        mapping,
        canonical_module_info_indices=canonical_module_info_indices,
    )

    target_ids = mapping.get("target_priority_ids") or {}
    priority, priority_complete, priority_reason = _validated_complete_order(
        decoded.get("targetPriorityList"),
        target_ids,
        label="target priority",
    )
    checks["target_priority"] = SaveCheckEvidence(
        check_id="target_priority",
        status="observed" if priority_complete else "unmapped",
        value=priority,
        source_fields=("targetPriorityList",),
        complete=priority_complete,
        reason=priority_reason,
        authority={
            "kind": "complete_order",
            "values": [str(value) for value in target_ids.values()],
        },
        diagnostics={
            "mapping_candidates": _ranked_mapping_candidates(
                decoded.get("targetPriorityList"),
                target_ids,
                ranked_count=len(target_ids),
                total_count=len(target_ids),
                value_kind="target_priority_id",
            )
        },
    )

    guardian_spec = mapping.get("guardian_chips")
    guardian_slot_count = (
        _optional_int(guardian_spec.get("slot_count"))
        if isinstance(guardian_spec, Mapping)
        else None
    )
    guardians, guardians_complete, guardians_reason = _validated_known_id_list(
        decoded.get("guardianChipSlot"),
        mapping.get("guardian_chip_ids") or {},
        label="guardian chip",
        expected_count=guardian_slot_count,
    )
    guardian_slots_unlocked = decoded.get("guardianSlotsUnlocked")
    if _exact_int(guardian_slots_unlocked) is None:
        guardians_complete = False
        guardians_reason = "guardianSlotsUnlocked is not an exact integer"
    checks["guardian_chips"] = SaveCheckEvidence(
        check_id="guardian_chips",
        status="observed" if guardians_complete else "unmapped",
        value=guardians,
        source_fields=("guardianChipSlot", "guardianSlotsUnlocked"),
        complete=guardians_complete,
        reason=guardians_reason,
        authority={"kind": "matching_value"},
        diagnostics={
            "mapping_candidates": _known_list_mapping_candidates(
                decoded.get("guardianChipSlot"),
                mapping.get("guardian_chip_ids") or {},
                expected_count=guardian_slot_count,
                value_kind="guardian_chip_id",
            )
        },
    )

    checks["ultimate_weapons"] = _ultimate_weapon_evidence(decoded, mapping)
    checks.update(_ultimate_weapon_component_evidence(decoded, mapping))

    tournament_conditions = derive_tournament_conditions_from_save(
        decoded,
        mapping,
        captured_at=captured_at,
    )
    tournament_spec = mapping.get("tournament_conditions") or {}
    tournament_source_fields = tuple(
        str(tournament_spec.get(key) or fallback)
        for key, fallback in (
            ("seed_field", "tourneyConditionsSeed"),
            ("active_number_field", "tournamentNumber"),
            ("checked_number_field", "tournamentCheckedNumber"),
            ("records_field", "tournamentRecords"),
            ("league_field", "leagueID"),
        )
    )
    tournament_complete = bool(tournament_conditions.get("complete"))
    checks["tournament_conditions"] = SaveCheckEvidence(
        check_id="tournament_conditions",
        status="observed" if tournament_complete else "unmapped",
        value=tournament_conditions if tournament_complete else None,
        source_fields=tournament_source_fields,
        complete=tournament_complete,
        reason=str(tournament_conditions.get("reason") or ""),
        authority={"kind": "matching_value"},
    )
    league = tournament_conditions.get("league")
    league_id = (
        _exact_int(league.get("id"))
        if isinstance(league, Mapping)
        else None
    )
    league_name = (
        str(league.get("name") or "").strip()
        if isinstance(league, Mapping)
        else ""
    )
    league_reason = str(tournament_conditions.get("reason") or "")
    league_candidates: list[dict[str, Any]] = []
    if league_reason == "league_mapping_not_validated" and league_id is not None:
        candidate = _pending_mapping_candidate(
            value_kind="tournament_league_id",
            raw_value=league_id,
            pairing_method="exact_locator",
            locator="league",
            expected_observation_count=1,
            known_semantic_values=("Legend League",),
        )
        if candidate is not None:
            league_candidates.append(candidate)
    league_complete = bool(tournament_complete and league_name)
    checks["tournament_league"] = SaveCheckEvidence(
        check_id="tournament_league",
        status="observed" if league_complete else "unmapped",
        value=league_name if league_complete else None,
        source_fields=(str(tournament_spec.get("league_field") or "leagueID"),),
        complete=league_complete,
        reason="" if league_complete else league_reason or "tournament league unavailable",
        authority={"kind": "matching_value"},
        diagnostics={"mapping_candidates": league_candidates},
    )
    for check_id, reason in (mapping.get("unmapped_checks") or {}).items():
        checks[str(check_id)] = SaveCheckEvidence(
            check_id=str(check_id),
            status="unmapped",
            value=None,
            source_fields=(),
            complete=False,
            reason=str(reason),
            diagnostics=_unmapped_check_candidate_diagnostics(
                str(check_id),
                decoded,
            ),
        )
    return checks


def _unmapped_check_candidate_diagnostics(
    check_id: str,
    decoded: Mapping[str, Any],
    *,
    scope_context: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Retain safe control discriminators without publishing raw save data."""

    fields: tuple[str, ...]
    if check_id == "damage_slider":
        fields = ("damageAdjustmentLog",)
    elif check_id == "orb_distance":
        fields = (
            "rangeLevelSelected",
            "innerOrbDistance",
            "workshopOrbDistance",
        )
    else:
        return {}

    candidates: list[dict[str, Any]] = []
    for field in fields:
        raw_value = decoded.get(field)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            continue
        candidate = _pending_mapping_candidate(
            value_kind=f"{check_id}_calibration",
            raw_value=raw_value,
            pairing_method="calibration_sample",
            locator=field,
            expected_observation_count=len(fields),
            minimum_evidence_count=2,
            scope={"field": field, **dict(scope_context or {})},
        )
        if candidate is not None:
            candidates.append(candidate)
    return {"mapping_candidates": candidates}


def _damage_slider_evidence(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> SaveCheckEvidence:
    """Decode only causally calibrated Damage Slider discriminator values."""

    spec = mapping.get("damage_slider")
    source_field = (
        str(spec.get("source_field") or "")
        if isinstance(spec, Mapping)
        else ""
    )
    values = spec.get("values") if isinstance(spec, Mapping) else None
    if (
        source_field != "damageAdjustmentLog"
        or not _is_sequence(values)
        or not values
    ):
        return SaveCheckEvidence(
            check_id="damage_slider",
            status="unmapped",
            value=None,
            source_fields=("damageAdjustmentLog",),
            complete=False,
            reason="Damage Slider mapping is malformed",
        )

    raw_to_value: dict[int, str] = {}
    semantic_values: list[str] = []
    for option in values:
        if not isinstance(option, Mapping) or set(option) != {"raw", "value"}:
            raw_to_value.clear()
            break
        raw = _exact_int(option.get("raw"))
        try:
            # Keep save evidence byte-for-byte comparable with the canonical
            # value consumed by the guarded UI workflow (for example, 100%
            # is represented as 1E2%).  The import stays local so decoding
            # does not make UI components an import-time dependency.
            from core.damage_adjuster import normalize_damage_percentage

            value = normalize_damage_percentage(option.get("value"))
        except (TypeError, ValueError):
            raw_to_value.clear()
            break
        if raw is None or raw in raw_to_value or value in semantic_values:
            raw_to_value.clear()
            break
        raw_to_value[raw] = value
        semantic_values.append(value)
    if not raw_to_value:
        return SaveCheckEvidence(
            check_id="damage_slider",
            status="unmapped",
            value=None,
            source_fields=(source_field,),
            complete=False,
            reason="Damage Slider mapping values are malformed",
        )

    raw_value = _exact_int(decoded.get(source_field))
    observed = raw_to_value.get(raw_value) if raw_value is not None else None
    complete = observed is not None
    return SaveCheckEvidence(
        check_id="damage_slider",
        status="observed" if complete else "unmapped",
        value=observed,
        source_fields=(source_field,),
        complete=complete,
        reason=(
            ""
            if complete
            else "Damage Slider discriminator is not an exact mapped integer"
        ),
        authority={"kind": "exact_values", "values": semantic_values},
        diagnostics=(
            {}
            if complete
            else _unmapped_check_candidate_diagnostics(
                "damage_slider",
                decoded,
            )
        ),
    )


def _orb_distance_evidence(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> SaveCheckEvidence:
    """Decode only complete Orb tuples observed in the same preset context."""

    expected_fields = {
        "range_basis": "rangeLevelSelected",
        "extra": "innerOrbDistance",
        "workshop": "workshopOrbDistance",
    }
    spec = mapping.get("orb_distance")
    source_fields = spec.get("source_fields") if isinstance(spec, Mapping) else None
    context_checks = (
        tuple(spec.get("context_checks") or ())
        if isinstance(spec, Mapping)
        else ()
    )
    values = spec.get("values") if isinstance(spec, Mapping) else None
    if (
        not isinstance(source_fields, Mapping)
        or dict(source_fields) != expected_fields
        or context_checks != ("cards_deck", "workshop_preset")
        or not _is_sequence(values)
        or not values
    ):
        return SaveCheckEvidence(
            check_id="orb_distance",
            status="unmapped",
            value=None,
            source_fields=tuple(expected_fields.values()),
            complete=False,
            reason="Orb Distance mapping is malformed",
        )

    observed_context: dict[str, str] = {}
    context_source_fields: list[str] = []
    preset_specs = mapping.get("presets")
    if not isinstance(preset_specs, Mapping):
        preset_specs = {}
    for check_id in context_checks:
        preset_spec = preset_specs.get(check_id)
        if not isinstance(preset_spec, Mapping):
            observed_context.clear()
            break
        selected = _selected_preset(decoded, preset_spec)
        active_name = selected.get("active_name")
        names_field = str(preset_spec.get("names_field") or "")
        active_field = str(preset_spec.get("active_field") or "")
        if not isinstance(active_name, str) or not active_name or not all(
            (names_field, active_field)
        ):
            observed_context.clear()
            break
        observed_context[check_id] = active_name
        context_source_fields.extend((names_field, active_field))

    evidence_source_fields = tuple(
        dict.fromkeys((*expected_fields.values(), *context_source_fields))
    )
    raw_to_value: dict[
        tuple[int, float, float, tuple[str, ...]],
        dict[str, str],
    ] = {}
    semantic_values: list[dict[str, str]] = []
    malformed = len(observed_context) != len(context_checks)
    for option in values:
        if (
            not isinstance(option, Mapping)
            or set(option) != {"raw", "context", "value"}
        ):
            malformed = True
            break
        raw = option.get("raw")
        context = option.get("context")
        value = option.get("value")
        if (
            not isinstance(raw, Mapping)
            or set(raw) != set(expected_fields)
            or not isinstance(context, Mapping)
            or set(context) != set(context_checks)
            or not isinstance(value, Mapping)
            or set(value) != set(expected_fields)
        ):
            malformed = True
            break
        range_raw = _exact_int(raw.get("range_basis"))
        extra_raw = raw.get("extra")
        workshop_raw = raw.get("workshop")
        try:
            # Use the same canonical notation as the guarded UI owner.  This
            # prevents a mapping spelling such as 30m from silently becoming
            # distinct authority from 30.00m.
            from core.orb_distance import normalize_orb_distance_preset

            semantic = normalize_orb_distance_preset(value)
        except (TypeError, ValueError):
            malformed = True
            break
        normalized_context = {
            key: str(context.get(key) or "").strip()
            for key in context_checks
        }
        if (
            range_raw is None
            or isinstance(extra_raw, bool)
            or not isinstance(extra_raw, float)
            or not math.isfinite(extra_raw)
            or isinstance(workshop_raw, bool)
            or not isinstance(workshop_raw, float)
            or not math.isfinite(workshop_raw)
            or not all(semantic.values())
            or not all(normalized_context.values())
        ):
            malformed = True
            break
        raw_key = (
            range_raw,
            extra_raw,
            workshop_raw,
            tuple(normalized_context[key] for key in context_checks),
        )
        if raw_key in raw_to_value:
            malformed = True
            break
        raw_to_value[raw_key] = semantic
        if semantic not in semantic_values:
            semantic_values.append(semantic)
    if malformed or not raw_to_value:
        return SaveCheckEvidence(
            check_id="orb_distance",
            status="unmapped",
            value=None,
            source_fields=evidence_source_fields,
            complete=False,
            reason="Orb Distance mapping values are malformed",
        )

    range_raw = _exact_int(decoded.get(expected_fields["range_basis"]))
    extra_raw = decoded.get(expected_fields["extra"])
    workshop_raw = decoded.get(expected_fields["workshop"])
    raw_valid = bool(
        range_raw is not None
        and not isinstance(extra_raw, bool)
        and isinstance(extra_raw, float)
        and math.isfinite(extra_raw)
        and not isinstance(workshop_raw, bool)
        and isinstance(workshop_raw, float)
        and math.isfinite(workshop_raw)
    )
    observed = (
        raw_to_value.get(
            (
                range_raw,
                extra_raw,
                workshop_raw,
                tuple(observed_context[key] for key in context_checks),
            )
        )
        if raw_valid
        else None
    )
    complete = observed is not None
    return SaveCheckEvidence(
        check_id="orb_distance",
        status="observed" if complete else "unmapped",
        value=observed,
        source_fields=evidence_source_fields,
        complete=complete,
        reason=(
            ""
            if complete
            else "Orb Distance tuple and preset context are not an exact mapped value"
        ),
        authority={"kind": "exact_values", "values": semantic_values},
        diagnostics=(
            {}
            if complete
            else (
                _unmapped_check_candidate_diagnostics(
                    "orb_distance",
                    decoded,
                    scope_context=observed_context,
                )
                if len(observed_context) == len(context_checks)
                else {}
            )
        ),
    )


def _battle_history_killed_by_evidence(
    runtime_save: NormalizedRuntimeSave,
    mapping: Mapping[str, Any],
) -> SaveCheckEvidence:
    """Expose a semantic-neutral terminal cause discriminator for UI pairing."""

    tail = runtime_save.battle_history_tail
    identity = tail.identity
    raw_value = getattr(identity, "killed_by_id", None)
    killed_by_ids = (
        ((mapping.get("runtime_save") or {}).get("battle_history") or {}).get(
            "killed_by_ids"
        )
        or {}
    )
    mapped_value = (
        killed_by_ids.get(str(raw_value))
        if type(raw_value) is int and isinstance(killed_by_ids, Mapping)
        else None
    )
    if (
        tail.structural_status == "observed"
        and isinstance(mapped_value, str)
        and mapped_value
    ):
        return SaveCheckEvidence(
            check_id="battle_history_killed_by",
            status="observed",
            value=mapped_value,
            source_fields=("battleHistory[-1].killedBy",),
            complete=True,
            authority={"kind": "matching_value"},
        )
    reason = str(tail.completed_entry_reason or "")
    candidates: list[dict[str, Any]] = []
    if reason == f"unmapped_killed_by_id:{raw_value}" and type(raw_value) is int:
        candidate = _pending_mapping_candidate(
            value_kind="battle_history_killed_by_id",
            raw_value=raw_value,
            pairing_method="exact_locator",
            locator="killed_by",
            expected_observation_count=1,
            known_semantic_values=tuple(
                str(value)
                for value in killed_by_ids.values()
                if str(value).strip()
            ),
        )
        if candidate is not None:
            candidates.append(candidate)
    return SaveCheckEvidence(
        check_id="battle_history_killed_by",
        status="unmapped",
        value=None,
        source_fields=("battleHistory[-1].killedBy",),
        complete=False,
        reason=reason or "completed battle cause unavailable",
        authority={"kind": "matching_value"},
        diagnostics={"mapping_candidates": candidates},
    )


def _card_recharge_mode_evidence(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> SaveCheckEvidence:
    specs = mapping.get("card_recharge_modes") or {}
    values: dict[str, str] = {}
    source_fields: list[str] = []
    invalid: list[str] = []
    for label, raw_spec in specs.items():
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}
        field = str(spec.get("field") or "")
        source_fields.append(field)
        raw_value = decoded.get(field)
        if not field or not isinstance(raw_value, bool):
            invalid.append(field or str(label))
            continue
        key = "true_value" if raw_value else "false_value"
        normalized = str(spec.get(key) or "").strip()
        if not normalized:
            invalid.append(field)
            continue
        values[str(label)] = normalized

    complete = bool(values) and not invalid and len(values) == len(specs)
    return SaveCheckEvidence(
        check_id="card_recharge_modes",
        status="observed" if complete else "unmapped",
        value=values if complete else None,
        source_fields=tuple(source_fields),
        complete=complete,
        reason=(
            "card recharge fields are missing or changed type: "
            + ", ".join(invalid)
            if invalid
            else "card recharge mapping is empty"
            if not specs
            else ""
        ),
        authority={"kind": "matching_value"},
    )


def _selected_preset(
    decoded: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    raw_names = decoded.get(str(spec.get("names_field") or ""))
    names = list(raw_names) if _is_sequence(raw_names) else []
    if not all(isinstance(name, str) and name.strip() for name in names):
        names = []
    index = _exact_int(decoded.get(str(spec.get("active_field") or "")))
    active_name = (
        str(names[index]).strip()
        if index is not None and 0 <= index < len(names) and str(names[index]).strip()
        else None
    )
    return {
        "names": [str(name) for name in names],
        "active_index": index,
        "active_name": active_name,
    }


def _validated_selected_id_slots(
    raw: Any,
    names: Mapping[str, Any],
    raw_spec: Any,
    *,
    label: str,
) -> tuple[list[str], bool, str]:
    spec = raw_spec if isinstance(raw_spec, Mapping) else {}
    slot_count = _optional_int(spec.get("slot_count"))
    empty_id = _optional_int(spec.get("empty_id"))
    if slot_count is None or empty_id is None:
        return [], False, f"{label} structural contract is incomplete"
    if not _is_sequence(raw) or len(raw) != slot_count:
        actual = len(raw) if _is_sequence(raw) else "non-array"
        return (
            [],
            False,
            f"{label} shape changed: expected {slot_count}, got {actual}",
        )
    numeric = [_exact_int(value) for value in raw]
    if any(value is None for value in numeric):
        return [], False, f"{label} slots require exact integer IDs"
    values = [int(value) for value in numeric if value is not None]
    selected: list[int] = []
    empty_seen = False
    for value in values:
        if value == empty_id:
            empty_seen = True
            continue
        if empty_seen:
            return [], False, f"{label} selected ID appeared after an empty slot"
        selected.append(value)
    if len(selected) != len(set(selected)):
        return [], False, f"{label} contains duplicate selected IDs"
    unknown = [value for value in selected if str(value) not in names]
    if unknown:
        return [], False, _unknown_id_reason(label, unknown)
    return [str(names[str(value)]) for value in selected], True, ""


def _validated_known_id_list(
    raw: Any,
    names: Mapping[str, Any],
    *,
    label: str,
    expected_count: Optional[int],
) -> tuple[list[str], bool, str]:
    if (
        expected_count is None
        or not _is_sequence(raw)
        or len(raw) != expected_count
    ):
        actual = len(raw) if _is_sequence(raw) else "non-array"
        return (
            [],
            False,
            f"{label} shape changed: expected {expected_count}, got {actual}",
        )
    numeric = [_exact_int(value) for value in raw]
    if any(value is None for value in numeric):
        return [], False, f"{label} slots require exact integer IDs"
    values = [int(value) for value in numeric if value is not None]
    if len(values) != len(set(values)):
        return [], False, f"{label} contains duplicate IDs"
    unknown = [value for value in values if str(value) not in names]
    if unknown:
        return [], False, _unknown_id_reason(label, unknown)
    return [str(names[str(value)]) for value in values], True, ""


def _selected_slot_mapping_candidates(
    raw: Any,
    names: Mapping[str, Any],
    raw_spec: Any,
    *,
    value_kind: str,
) -> list[dict[str, Any]]:
    """Retain structurally sound unknown selected-slot discriminators."""

    spec = raw_spec if isinstance(raw_spec, Mapping) else {}
    slot_count = _optional_int(spec.get("slot_count"))
    empty_id = _optional_int(spec.get("empty_id"))
    if (
        slot_count is None
        or empty_id is None
        or not _is_sequence(raw)
        or len(raw) != slot_count
    ):
        return []
    numeric = [_exact_int(value) for value in raw]
    if any(value is None for value in numeric):
        return []
    selected: list[int] = []
    empty_seen = False
    for value in numeric:
        assert value is not None
        if value == empty_id:
            empty_seen = True
            continue
        if empty_seen:
            return []
        selected.append(value)
    if len(selected) != len(set(selected)):
        return []
    return _singleton_remainder_candidates(
        selected,
        names,
        value_kind=value_kind,
    )


def _known_list_mapping_candidates(
    raw: Any,
    names: Mapping[str, Any],
    *,
    expected_count: Optional[int],
    value_kind: str,
) -> list[dict[str, Any]]:
    """Retain unknown IDs from one exact-length unique semantic set."""

    if (
        expected_count is None
        or not _is_sequence(raw)
        or len(raw) != expected_count
    ):
        return []
    numeric = [_exact_int(value) for value in raw]
    if any(value is None for value in numeric):
        return []
    values = [int(value) for value in numeric if value is not None]
    if len(values) != len(set(values)):
        return []
    return _singleton_remainder_candidates(
        values,
        names,
        value_kind=value_kind,
    )


def _singleton_remainder_candidates(
    numeric: Sequence[int],
    names: Mapping[str, Any],
    *,
    value_kind: str,
) -> list[dict[str, Any]]:
    known_values = tuple(str(value) for value in names.values())
    peers = tuple(
        str(names[str(value)]) for value in numeric if str(value) in names
    )
    candidates: list[dict[str, Any]] = []
    for value in numeric:
        if value < 0 or str(value) in names:
            continue
        candidate = _pending_mapping_candidate(
            value_kind=value_kind,
            raw_value=value,
            pairing_method="singleton_remainder",
            locator="selected",
            expected_observation_count=len(numeric),
            known_semantic_values=known_values,
            peer_semantic_values=peers,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _ranked_mapping_candidates(
    raw: Any,
    names: Mapping[str, Any],
    *,
    ranked_count: Optional[int],
    total_count: Optional[int],
    value_kind: str,
    observation_count_policy: str = "exact",
) -> list[dict[str, Any]]:
    """Retain unknown numeric IDs only where UI exposes the same rank."""

    if (
        ranked_count is None
        or total_count is None
        or ranked_count <= 0
        or total_count < ranked_count
        or not _is_sequence(raw)
        or len(raw) != total_count
    ):
        return []
    numeric = [_exact_int(value) for value in raw]
    if any(value is None for value in numeric):
        return []
    values = [int(value) for value in numeric if value is not None]
    if len(values) != len(set(values)):
        return []
    known_values = tuple(str(value) for value in names.values())
    candidates: list[dict[str, Any]] = []
    for rank, value in enumerate(values[:ranked_count]):
        if value < 0 or str(value) in names:
            continue
        peer_limit = (
            rank + 1
            if observation_count_policy == "minimum"
            else ranked_count
        )
        peer_locator_values = {
            f"rank:{peer_rank}": str(names[str(peer_value)])
            for peer_rank, peer_value in enumerate(values[:peer_limit])
            if str(peer_value) in names
        }
        candidate = _pending_mapping_candidate(
            value_kind=value_kind,
            raw_value=value,
            pairing_method="exact_locator",
            locator=f"rank:{rank}",
            expected_observation_count=peer_limit,
            observation_count_policy=observation_count_policy,
            known_semantic_values=known_values,
            peer_locator_values=peer_locator_values,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _pending_mapping_candidate(**kwargs: Any) -> Optional[dict[str, Any]]:
    """Keep optional discovery metadata from affecting save availability."""

    try:
        return pending_mapping_candidate(**kwargs)
    except PlayerSaveMappingCandidateError:
        return None


def _map_id_sequence(
    raw: Any,
    names: Mapping[str, Any],
    *,
    stop_at_negative: bool = False,
    stop_on_unknown: bool = False,
) -> tuple[list[str], list[int]]:
    mapped: list[str] = []
    unknown: list[int] = []
    if not _is_sequence(raw):
        return mapped, unknown
    for value in raw:
        numeric = _optional_int(value)
        if numeric is None:
            continue
        if numeric < 0 and stop_at_negative:
            break
        name = names.get(str(numeric))
        if name is None:
            if numeric >= 0:
                unknown.append(numeric)
            if stop_on_unknown:
                break
            continue
        mapped.append(str(name))
    return mapped, unknown


def _validated_auto_pick_order(
    raw: Any,
    names: Mapping[str, Any],
    raw_spec: Any,
) -> tuple[list[str], bool, str]:
    spec = raw_spec if isinstance(raw_spec, Mapping) else {}
    ranked_count = _optional_int(spec.get("ranked_count"))
    unranked_count = _optional_int(spec.get("unranked_count"))
    total_count = _optional_int(spec.get("total_count"))
    if None in (ranked_count, unranked_count, total_count):
        return [], False, "Auto Pick structural contract is incomplete"
    if (
        ranked_count <= 0
        or unranked_count < 0
        or ranked_count + unranked_count != total_count
    ):
        return [], False, "Auto Pick structural contract is invalid"
    if not _is_sequence(raw) or len(raw) != total_count:
        actual = len(raw) if _is_sequence(raw) else "non-array"
        return (
            [],
            False,
            f"Auto Pick order shape changed: expected {total_count}, got {actual}",
        )

    numeric: list[int] = []
    for index, value in enumerate(raw):
        parsed = _exact_int(value)
        if parsed is None:
            return (
                [],
                False,
                f"Auto Pick order entry {index} is not an exact integer",
            )
        numeric.append(parsed)

    ranked_raw = numeric[:ranked_count]
    expected_ids = {_optional_int(value) for value in names.keys()}
    if None in expected_ids:
        return [], False, "Auto Pick perk mapping contains a non-integer ID"
    expected_numeric_ids = {int(value) for value in expected_ids}
    if len(numeric) != len(set(numeric)):
        return [], False, "Auto Pick order contains duplicate perk IDs"
    if set(numeric) != expected_numeric_ids:
        missing = sorted(expected_numeric_ids - set(numeric))
        unknown = sorted(set(numeric) - expected_numeric_ids)
        return (
            [],
            False,
            "Auto Pick inventory membership changed"
            f" (missing={missing}, unknown={unknown})",
        )
    ranked = [str(names[str(value)]) for value in ranked_raw]
    return ranked, True, ""


def _validated_complete_order(
    raw: Any,
    names: Mapping[str, Any],
    *,
    label: str,
) -> tuple[list[str], bool, str]:
    if not _is_sequence(raw):
        return [], False, f"{label} is not an array"
    expected_count = len(names)
    if len(raw) != expected_count:
        return (
            [],
            False,
            f"{label} length changed: expected {expected_count}, got {len(raw)}",
        )
    numeric: list[int] = []
    for index, value in enumerate(raw):
        parsed = _exact_int(value)
        if parsed is None:
            return [], False, f"{label} entry {index} is not an exact integer"
        numeric.append(parsed)
    if len(set(numeric)) != expected_count:
        return [], False, f"{label} contains duplicate IDs"
    expected_ids = {_optional_int(value) for value in names.keys()}
    if None in expected_ids or set(numeric) != expected_ids:
        return [], False, f"{label} does not contain the complete known ID set"
    return [str(names[str(value)]) for value in numeric], True, ""


def _mapped_free_upgrade_locks(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> tuple[list[str], list[str], int, bool, str]:
    locked: list[str] = []
    invalid: list[str] = []
    unmapped_locked_slot_count = 0
    for field, labels in (mapping.get("free_upgrade_lock_fields") or {}).items():
        raw_flags = decoded.get(field)
        expected_length = len(labels) if _is_sequence(labels) else None
        if (
            not _is_sequence(raw_flags)
            or expected_length is None
            or len(raw_flags) != expected_length
            or len(labels) != expected_length
        ):
            invalid.append(f"{field}:shape")
            continue
        flags = list(raw_flags)
        for index, enabled in enumerate(flags):
            if not isinstance(enabled, bool):
                invalid.append(f"{field}[{index}]:type")
                continue
            if not enabled:
                continue
            label = labels[index] if index < len(labels) else None
            if label:
                locked.append(str(label))
            else:
                unmapped_locked_slot_count += 1
    validated = {
        str(value)
        for value in mapping.get("validated_free_upgrade_lock_set") or ()
    }
    unmanaged = sorted(set(locked) - validated)
    return (
        locked,
        unmanaged,
        unmapped_locked_slot_count,
        not invalid,
        "Free Upgrade lock arrays changed structure: "
        + "; ".join(invalid)
        if invalid
        else "",
    )


def _module_loadout_evidence(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    canonical_module_info_indices: Any = None,
) -> SaveCheckEvidence:
    spec = mapping.get("module_loadout")
    source_fields = ("moduleEquipped", "assistModuleSlots")
    if not isinstance(spec, Mapping):
        return SaveCheckEvidence(
            check_id="modules",
            status="unmapped",
            value=None,
            source_fields=source_fields,
            complete=False,
            reason="module loadout mapping is unavailable",
        )
    primary_specs = spec.get("primary")
    assist_specs = spec.get("assist")
    observation_scope = spec.get("assignment_observation_scope")
    if (
        not _is_sequence(primary_specs)
        or not _is_sequence(assist_specs)
        or len(primary_specs) != 4
        or len(assist_specs) != 4
        or observation_scope
        not in {None, "canonical_global_same_family"}
        or not _module_loadout_specs_are_valid(primary_specs, assist_specs)
    ):
        return SaveCheckEvidence(
            check_id="modules",
            status="unmapped",
            value=None,
            source_fields=source_fields,
            complete=False,
            reason="module loadout structural contract is incomplete",
        )

    primary_raw = decoded.get("moduleEquipped")
    assist_raw = decoded.get("assistModuleSlots")
    if not _is_sequence(primary_raw) or len(primary_raw) != 4:
        return _unmapped_module_evidence(
            source_fields,
            "Primary module structure changed",
        )
    if not _is_sequence(assist_raw) or len(assist_raw) != 4:
        return _unmapped_module_evidence(
            source_fields,
            "Assist module structure changed",
        )

    assignments: dict[str, str] = {}
    supported_names: dict[str, list[str]] = {}
    slot_diagnostics: list[dict[str, str]] = []
    mapping_candidates: list[dict[str, Any]] = []
    module_identities = _module_identity_options(
        mapping,
        (*primary_specs, *assist_specs),
    )
    canonical_identity_mapping = dict(mapping)
    if canonical_module_info_indices is not None:
        canonical_identity_mapping["module_info_indices"] = (
            canonical_module_info_indices
        )
    canonical_module_identities = _module_identity_options(
        canonical_identity_mapping,
        (*primary_specs, *assist_specs),
    )
    if module_identities is None or canonical_module_identities is None:
        return _unmapped_module_evidence(
            source_fields,
            "module infoIndex mapping changed",
        )
    for raw_spec in primary_specs:
        if not isinstance(raw_spec, Mapping):
            return _unmapped_module_evidence(
                source_fields,
                "Primary module mapping changed",
            )
        index = _exact_int(raw_spec.get("array_index"))
        if index is None or not 0 <= index < len(primary_raw):
            return _unmapped_module_evidence(
                source_fields,
                "Primary module slot mapping changed",
            )
        item = primary_raw[index]
        if not _is_module_item(item):
            return _unmapped_module_evidence(
                source_fields,
                "Primary module entry is missing or changed type",
            )
        failure = _record_mapped_module_assignment(
            assignments,
            supported_names,
            slot_diagnostics,
            mapping_candidates,
            module_identities,
            canonical_module_identities,
            raw_spec,
            item,
            observation_scope=observation_scope,
        )
        if failure:
            return _unmapped_module_evidence(
                source_fields,
                failure,
                diagnostics={"slots": slot_diagnostics},
            )

    assist_by_type: dict[int, Mapping[str, Any]] = {}
    assist_slot_class = str(spec.get("assist_slot_class") or "").strip()
    if not assist_slot_class:
        return _unmapped_module_evidence(
            source_fields,
            "Assist module slot class mapping changed",
        )
    for raw_slot in assist_raw:
        if not _is_typed_object(raw_slot, assist_slot_class):
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot changed type",
            )
        slot_type = _exact_int(raw_slot.get("type"))
        unlocked = raw_slot.get("unlocked")
        if slot_type is None or slot_type in assist_by_type:
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot types are incomplete or duplicated",
            )
        if type(unlocked) is not bool or not unlocked:
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot is locked or has changed unlock state",
            )
        module_items = [
            value for value in raw_slot.values() if _is_module_item(value)
        ]
        if len(module_items) != 1:
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot does not contain exactly one ModuleItem",
            )
        assist_by_type[slot_type] = module_items[0]

    assist_specs_by_type: dict[int, Mapping[str, Any]] = {}
    for raw_spec in assist_specs:
        if not isinstance(raw_spec, Mapping):
            return _unmapped_module_evidence(
                source_fields,
                "Assist module mapping changed",
            )
        slot_type = _exact_int(raw_spec.get("type"))
        family = str(raw_spec.get("family") or "").strip()
        if (
            slot_type is None
            or slot_type < 0
            or slot_type in assist_specs_by_type
            or not family
        ):
            return _unmapped_module_evidence(
                source_fields,
                "Assist module type mapping changed",
            )
        assist_specs_by_type[slot_type] = raw_spec

    observed_types = set(assist_by_type)
    expected_types = set(assist_specs_by_type)
    resolved_assist_items = dict(assist_by_type)
    if observed_types != expected_types:
        unknown_types = observed_types - expected_types
        missing_types = expected_types - observed_types
        if len(unknown_types) != 1 or len(missing_types) != 1:
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot membership changed",
            )
        unknown_type = next(iter(unknown_types))
        missing_type = next(iter(missing_types))
        known_families = tuple(
            str(item.get("family")) for item in assist_specs_by_type.values()
        )
        peer_families = tuple(
            str(assist_specs_by_type[slot_type].get("family"))
            for slot_type in sorted(observed_types & expected_types)
        )
        type_candidate = _pending_mapping_candidate(
            value_kind="module_assist_type",
            raw_value=unknown_type,
            pairing_method="singleton_remainder",
            locator="assist_type",
            expected_observation_count=4,
            known_semantic_values=known_families,
            peer_semantic_values=peer_families,
            scope={"role": "assist"},
        )
        if type_candidate is None:
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot membership changed",
            )
        mapping_candidates.append(type_candidate)
        resolved_assist_items[missing_type] = assist_by_type[unknown_type]

    for raw_spec in assist_specs:
        assert isinstance(raw_spec, Mapping)
        slot_type = _exact_int(raw_spec.get("type"))
        item = (
            resolved_assist_items.get(slot_type)
            if slot_type is not None
            else None
        )
        if item is None:
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot membership changed",
            )
        failure = _record_mapped_module_assignment(
            assignments,
            supported_names,
            slot_diagnostics,
            mapping_candidates,
            module_identities,
            canonical_module_identities,
            raw_spec,
            item,
            observation_scope=observation_scope,
        )
        if failure:
            return _unmapped_module_evidence(
                source_fields,
                failure,
                diagnostics={"slots": slot_diagnostics},
            )

    if mapping_candidates:
        for candidate in mapping_candidates:
            candidate["peer_locator_values"] = dict(assignments)
        info_roles = {
            str(candidate.get("scope", {}).get("role") or "")
            for candidate in mapping_candidates
            if candidate.get("value_kind") == "module_info_index"
        }
        if info_roles == {"primary"}:
            candidate_reason = "unsupported primary module infoIndex"
        elif info_roles == {"assist"}:
            candidate_reason = "unsupported assist module infoIndex"
        elif info_roles:
            candidate_reason = "unsupported module infoIndex values"
        else:
            candidate_reason = "unsupported Assist module type"
        return _unmapped_module_evidence(
            source_fields,
            candidate_reason,
            diagnostics={
                "slots": slot_diagnostics,
                "mapping_candidates": mapping_candidates,
            },
        )
    if len(assignments) != 8:
        local_only = any(
            item.get("mapping_status") == "mapped_identity_local_only"
            for item in slot_diagnostics
        )
        unsupported_roles = {
            item.get("role")
            for item in slot_diagnostics
            if item.get("mapping_status")
            == "mapped_identity_unsupported_scope"
        }
        if local_only:
            partial_reason = (
                "locally confirmed module identity requires canonical "
                "integration"
            )
        elif unsupported_roles == {"primary"}:
            partial_reason = "unsupported primary module value for exact slot"
        elif unsupported_roles == {"assist"}:
            partial_reason = "unsupported assist module value for exact slot"
        elif unsupported_roles:
            partial_reason = "unsupported module values for exact slots"
        else:
            partial_reason = "module loadout is partial"
        return _unmapped_module_evidence(
            source_fields,
            partial_reason,
            diagnostics={"slots": slot_diagnostics},
        )
    return SaveCheckEvidence(
        check_id="modules",
        status="observed",
        value=assignments,
        source_fields=source_fields,
        complete=True,
        authority={
            "kind": "slot_scoped_module_values",
            "assignments": assignments,
            "supported_names": supported_names,
        },
        diagnostics={"slots": slot_diagnostics},
    )


def _module_loadout_specs_are_valid(
    primary_specs: Sequence[Any],
    assist_specs: Sequence[Any],
) -> bool:
    """Validate all eight exact slots before retaining unknown values."""

    families = {"cannon", "armor", "generator", "core"}
    expected_scopes = {
        (f"{family}_{role}", family, role)
        for family in families
        for role in ("primary", "assist")
    }
    scopes: set[tuple[str, str, str]] = set()
    primary_indices: set[int] = set()
    assist_types: set[int] = set()
    for role, specs, discriminator, seen in (
        ("primary", primary_specs, "array_index", primary_indices),
        ("assist", assist_specs, "type", assist_types),
    ):
        for raw_spec in specs:
            if not isinstance(raw_spec, Mapping):
                return False
            slot_key = str(raw_spec.get("slot_key") or "").strip()
            family = str(raw_spec.get("family") or "").strip()
            configured_role = str(raw_spec.get("role") or "").strip()
            numeric = _exact_int(raw_spec.get(discriminator))
            scope = (slot_key, family, configured_role)
            if (
                family not in families
                or configured_role != role
                or slot_key != f"{family}_{role}"
                or scope in scopes
                or numeric is None
                or numeric < 0
                or numeric in seen
                or _module_value_options(raw_spec) is None
            ):
                return False
            scopes.add(scope)
            seen.add(numeric)
    return scopes == expected_scopes and primary_indices == set(range(4))


def _record_mapped_module_assignment(
    assignments: dict[str, str],
    supported_names: dict[str, list[str]],
    diagnostics: list[dict[str, str]],
    mapping_candidates: list[dict[str, Any]],
    module_identities: Mapping[int, tuple[str, str]],
    canonical_module_identities: Mapping[int, tuple[str, str]],
    spec: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    observation_scope: Any,
) -> str:
    slot_key = str(spec.get("slot_key") or "").strip()
    family = str(spec.get("family") or "").strip()
    role = str(spec.get("role") or "").strip()
    options = _module_value_options(spec)
    observed_info_index = _exact_int(item.get("infoIndex"))
    if (
        not slot_key
        or slot_key in assignments
        or slot_key in supported_names
        or not family
        or role not in {"primary", "assist"}
        or options is None
    ):
        return "module slot mapping changed"
    if observed_info_index is None:
        return f"{role.title()} module infoIndex is unavailable"
    if observed_info_index < 0:
        return f"{role.title()} module infoIndex is invalid"
    selected = next(
        (option for option in options if option[0] == observed_info_index),
        None,
    )
    known_identity = module_identities.get(observed_info_index)
    if known_identity is None:
        candidate = _pending_mapping_candidate(
            value_kind="module_info_index",
            raw_value=observed_info_index,
            pairing_method="exact_locator",
            locator=slot_key,
            expected_observation_count=8,
            known_semantic_values=tuple(
                name for name, _family in module_identities.values()
            ),
            known_raw_semantic_value=None,
            scope={
                "slot_key": slot_key,
                "family": family,
                "role": role,
            },
        )
        if candidate is None:
            return f"unsupported {role} module infoIndex"
        mapping_candidates.append(candidate)
        diagnostics.append(
            {
                "slot_key": slot_key,
                "family": family,
                "role": role,
                "mapping_status": "unmapped",
            }
        )
        return ""
    name, mapped_family = known_identity
    if mapped_family != family:
        return f"{role.title()} module family mapping changed"
    canonical_identity = canonical_module_identities.get(observed_info_index)
    if canonical_identity != known_identity:
        diagnostics.append(
            {
                "slot_key": slot_key,
                "family": family,
                "role": role,
                "name": name,
                "mapping_status": "mapped_identity_local_only",
            }
        )
        return ""
    if selected is None and observation_scope != "canonical_global_same_family":
        diagnostics.append(
            {
                "slot_key": slot_key,
                "family": family,
                "role": role,
                "name": name,
                "mapping_status": "mapped_identity_unsupported_scope",
            }
        )
        return ""
    assignments[slot_key] = name
    supported_names[slot_key] = [option_name for _index, option_name in options]
    diagnostic = {
        "slot_key": slot_key,
        "family": family,
        "role": role,
        "name": name,
    }
    if selected is None:
        diagnostic["mapping_status"] = "mapped_global_observation"
    diagnostics.append(diagnostic)
    return ""


def _module_identity_options(
    mapping: Mapping[str, Any],
    specs: Sequence[Any],
) -> Optional[dict[int, tuple[str, str]]]:
    raw_identities = mapping.get("module_info_indices")
    if not isinstance(raw_identities, Mapping) or not raw_identities:
        return None
    identities: dict[int, tuple[str, str]] = {}
    seen_names: set[str] = set()
    for raw_info_index, raw_identity in raw_identities.items():
        if not isinstance(raw_identity, Mapping):
            return None
        info_index = _exact_int_string_key(raw_info_index)
        raw_name = raw_identity.get("name")
        raw_family = raw_identity.get("family")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        family = raw_family.strip() if isinstance(raw_family, str) else ""
        normalized_name = _normal_scalar(name)
        if (
            set(raw_identity) != {"name", "family"}
            or info_index is None
            or info_index < 0
            or not name
            or raw_name != name
            or raw_family != family
            or family not in {"cannon", "armor", "generator", "core"}
            or normalized_name in seen_names
        ):
            return None
        identities[info_index] = (name, family)
        seen_names.add(normalized_name)

    for raw_spec in specs:
        if not isinstance(raw_spec, Mapping):
            return None
        family = str(raw_spec.get("family") or "").strip()
        options = _module_value_options(raw_spec)
        if options is None:
            return None
        for info_index, name in options:
            if identities.get(info_index) != (name, family):
                return None
    return identities


def _exact_int_string_key(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if str(parsed) == value else None


def _module_value_options(
    spec: Mapping[str, Any],
) -> Optional[tuple[tuple[int, str], ...]]:
    raw_options = spec.get("values")
    if not _is_sequence(raw_options) or not raw_options:
        return None
    options: list[tuple[int, str]] = []
    seen_indices: set[int] = set()
    seen_names: set[str] = set()
    for raw_option in raw_options:
        if not isinstance(raw_option, Mapping):
            return None
        info_index = _exact_int(raw_option.get("info_index"))
        name = str(raw_option.get("name") or "").strip()
        normalized_name = _normal_scalar(name)
        if (
            info_index is None
            or not name
            or info_index in seen_indices
            or normalized_name in seen_names
        ):
            return None
        seen_indices.add(info_index)
        seen_names.add(normalized_name)
        options.append((info_index, name))
    return tuple(options)


def _is_module_item(value: Any) -> bool:
    return _is_typed_object(value, "ModuleItem")


def _is_typed_object(value: Any, expected_class: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    class_name = str(value.get("__class__") or "").strip()
    return bool(
        class_name
        and class_name.rsplit("+", 1)[-1] == str(expected_class).strip()
    )


def _unmapped_module_evidence(
    source_fields: tuple[str, str],
    reason: str,
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> SaveCheckEvidence:
    return SaveCheckEvidence(
        check_id="modules",
        status="unmapped",
        value=None,
        source_fields=source_fields,
        complete=False,
        reason=reason,
        authority={
            "kind": "slot_scoped_module_values",
            "assignments": {},
            "supported_names": {},
        },
        diagnostics=dict(diagnostics or {}),
    )


def _ultimate_weapon_evidence(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> SaveCheckEvidence:
    names = list(mapping.get("ultimate_weapon_names") or [])
    unlocked_raw = decoded.get("ultimateWeaponUnlocked")
    active_raw = decoded.get("ultimateWeaponOn")
    if (
        not _is_sequence(unlocked_raw)
        or not _is_sequence(active_raw)
        or len(unlocked_raw) != len(names)
        or len(active_raw) != len(names)
        or not all(type(value) is bool for value in unlocked_raw)
        or not all(type(value) is bool for value in active_raw)
    ):
        return SaveCheckEvidence(
            check_id="ultimate_weapons",
            status="unmapped",
            value=None,
            source_fields=("ultimateWeaponUnlocked", "ultimateWeaponOn"),
            complete=False,
            reason="ultimate weapon arrays changed length",
        )
    unlocked = list(unlocked_raw)
    active = list(active_raw)
    value: dict[str, dict[str, str]] = {}
    for index, name in enumerate(names):
        if not bool(unlocked[index]):
            continue
        entry = {"primary": "on" if bool(active[index]) else "off"}
        if name == "Poison Swamp" and "poisonSwampStunOff" in decoded:
            entry["stun"] = (
                "off" if bool(decoded.get("poisonSwampStunOff")) else "on"
            )
        if name == "Spotlight" and "spotlightSmartMissilesOff" in decoded:
            entry["missiles"] = (
                "off"
                if bool(decoded.get("spotlightSmartMissilesOff"))
                else "on"
            )
        value[str(name)] = entry
    return SaveCheckEvidence(
        check_id="ultimate_weapons",
        status="observed",
        value=value,
        source_fields=(
            "ultimateWeaponUnlocked",
            "ultimateWeaponOn",
            "poisonSwampStunOff",
            "spotlightSmartMissilesOff",
        ),
    )


def _ultimate_weapon_component_evidence(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> dict[str, SaveCheckEvidence]:
    names = [str(value) for value in mapping.get("ultimate_weapon_names") or ()]
    unlocked_raw = decoded.get("ultimateWeaponUnlocked")
    active_raw = decoded.get("ultimateWeaponOn")
    arrays_valid = bool(
        names
        and _is_sequence(unlocked_raw)
        and _is_sequence(active_raw)
        and len(unlocked_raw) == len(names)
        and len(active_raw) == len(names)
        and all(isinstance(value, bool) for value in unlocked_raw)
        and all(isinstance(value, bool) for value in active_raw)
    )
    unlocked = list(unlocked_raw) if arrays_valid else []
    active = list(active_raw) if arrays_valid else []
    all_unlocked = arrays_valid and all(unlocked)
    primary_complete = bool(all_unlocked)
    primaries = (
        {
            name: {"primary": "on" if active[index] else "off"}
            for index, name in enumerate(names)
        }
        if primary_complete
        else None
    )
    checks = {
        "ultimate_weapon_primaries": SaveCheckEvidence(
            check_id="ultimate_weapon_primaries",
            status="observed" if primary_complete else "unmapped",
            value=primaries,
            source_fields=("ultimateWeaponUnlocked", "ultimateWeaponOn"),
            complete=primary_complete,
            reason=(
                ""
                if primary_complete
                else (
                    "ultimate weapon arrays require nine exact booleans with "
                    "all weapons unlocked"
                )
            ),
            authority={"kind": "all_named_primary_on", "names": names},
        )
    }

    poison_index = names.index("Poison Swamp") if "Poison Swamp" in names else -1
    poison_raw = decoded.get("poisonSwampStunOff")
    poison_valid = bool(
        arrays_valid
        and poison_index >= 0
        and unlocked[poison_index]
        and isinstance(poison_raw, bool)
    )
    checks["poison_swamp_stun"] = SaveCheckEvidence(
        check_id="poison_swamp_stun",
        status="observed" if poison_valid else "unmapped",
        value=("off" if poison_raw else "on") if poison_valid else None,
        source_fields=("ultimateWeaponUnlocked", "poisonSwampStunOff"),
        complete=poison_valid,
        reason=(
            ""
            if poison_valid
            else "Poison Swamp Stun requires an unlocked weapon and exact boolean"
        ),
        authority={"kind": "allowed_values", "values": ["on", "off"]},
    )

    spotlight_index = names.index("Spotlight") if "Spotlight" in names else -1
    missiles_raw = decoded.get("spotlightSmartMissilesOff")
    missiles_valid = bool(
        arrays_valid
        and spotlight_index >= 0
        and unlocked[spotlight_index]
        and isinstance(missiles_raw, bool)
    )
    checks["spotlight_missiles"] = SaveCheckEvidence(
        check_id="spotlight_missiles",
        status="observed" if missiles_valid else "unmapped",
        value=("off" if missiles_raw else "on") if missiles_valid else None,
        source_fields=(
            "ultimateWeaponUnlocked",
            "spotlightSmartMissilesOff",
        ),
        complete=missiles_valid,
        reason=(
            ""
            if missiles_valid
            else "Spotlight Missiles requires an unlocked weapon and exact boolean"
        ),
        authority={"kind": "allowed_values", "values": ["on"]},
    )
    return checks


def _requirement_values(requirements: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("invariants", "settings"):
        nested = requirements.get(key)
        if isinstance(nested, Mapping):
            return nested
    return requirements


def _expanded_requirement_values(
    requirements: Mapping[str, Any],
) -> dict[str, Any]:
    values = dict(_requirement_values(requirements))
    for metadata_key in (
        "loadout_policies",
        "profile_skips",
        "_gate_waivers",
    ):
        values.pop(metadata_key, None)
    damage = values.get("damage_slider")
    if isinstance(damage, Mapping):
        if str(damage.get("mode") or "").strip().lower() == "preserve":
            values.pop("damage_slider", None)
        elif "value" in damage:
            values["damage_slider"] = damage.get("value")
    orb = values.get("orb_distance")
    if isinstance(orb, Mapping):
        if str(orb.get("mode") or "").strip().lower() == "preserve":
            values.pop("orb_distance", None)
        elif _is_sequence(orb.get("range_presets")):
            values["orb_distance"] = [
                deepcopy(value) for value in orb["range_presets"]
            ]
        elif isinstance(orb.get("resolved"), Mapping):
            values["orb_distance"] = dict(orb["resolved"])
    ultimate = values.pop("ultimate_weapons", None)
    if not isinstance(ultimate, Mapping):
        return values

    primaries: dict[str, dict[str, Any]] = {}
    for raw_name, raw_requirement in ultimate.items():
        if not isinstance(raw_requirement, Mapping):
            continue
        name = str(raw_name)
        if "primary" in raw_requirement:
            primaries[name] = {"primary": raw_requirement["primary"]}
    if primaries:
        values["ultimate_weapon_primaries"] = primaries
    poison = ultimate.get("Poison Swamp")
    if isinstance(poison, Mapping) and "stun" in poison:
        values["poison_swamp_stun"] = poison["stun"]
    spotlight = ultimate.get("Spotlight")
    if isinstance(spotlight, Mapping) and "missiles" in spotlight:
        values["spotlight_missiles"] = spotlight["missiles"]
    return values


def _requirement_policy(
    requirements: Mapping[str, Any],
    check_id: str,
) -> str:
    values = _requirement_values(requirements)
    if check_id in {"damage_slider", "orb_distance"}:
        policy = values.get(check_id)
        if isinstance(policy, Mapping):
            normalized = str(policy.get("mode") or "enforce").strip().lower()
            return (
                normalized
                if normalized in {"enforce", "observe", "preserve"}
                else "enforce"
            )
    policies = values.get("loadout_policies")
    if not isinstance(policies, Mapping):
        return "enforce"
    policy_key = {
        "modules": "modules",
        "target_priority": "target_priority",
    }.get(str(check_id))
    if policy_key is None:
        return "enforce"
    normalized = str(policies.get(policy_key) or "enforce").strip().lower()
    return (
        normalized
        if normalized in {"enforce", "observe", "preserve"}
        else "enforce"
    )


def _requirement_is_supported(
    check_id: str,
    expected: Any,
    evidence: SaveCheckEvidence,
) -> bool:
    authority = evidence.authority
    kind = str(authority.get("kind") or "")
    if check_id == "orb_distance" and kind == "exact_values":
        options = _normalized_orb_requirement_options(expected)
        allowed = [
            candidate
            for value in authority.get("values") or ()
            for candidate in (_normalized_orb_requirement_options(value) or ())
        ]
        return bool(options and any(option in allowed for option in options))
    if kind == "matching_value":
        return True
    if kind == "allowed_values":
        normalized = _normal_scalar(expected)
        return normalized in {
            _normal_scalar(value) for value in authority.get("values") or ()
        }
    if kind == "exact_values":
        return any(
            type(expected) is type(value) and expected == value
            for value in authority.get("values") or ()
        )
    if kind == "required_subset":
        if not _is_sequence(expected):
            return False
        normalized = [_normal_scalar(value) for value in expected]
        allowed = {
            _normal_scalar(value)
            for value in authority.get("values") or ()
        }
        return bool(
            normalized
            and len(set(normalized)) == len(normalized)
            and set(normalized) <= allowed
        )
    if kind == "prefix":
        maximum = _optional_int(authority.get("maximum_length"))
        return bool(
            _is_sequence(expected)
            and maximum is not None
            and 0 < len(expected) <= maximum
        )
    if kind == "complete_order":
        if not _is_sequence(expected):
            return False
        normalized = [_normal_scalar(value) for value in expected]
        allowed = {
            _normal_scalar(value) for value in authority.get("values") or ()
        }
        return len(normalized) == len(allowed) and set(normalized) == allowed
    if kind == "slot_scoped_module_values":
        if not isinstance(expected, Mapping):
            return False
        supported_names = authority.get("supported_names")
        if not isinstance(supported_names, Mapping):
            return False
        expected_names = {
            str(key): _normal_scalar(value)
            for key, value in expected.items()
        }
        if set(expected_names) != {str(key) for key in supported_names}:
            return False
        return all(
            expected_name
            in {
                _normal_scalar(value)
                for value in supported_names.get(slot_key) or ()
            }
            for slot_key, expected_name in expected_names.items()
        )
    if kind == "all_named_primary_on":
        if not isinstance(expected, Mapping):
            return False
        expected_names = {str(value) for value in authority.get("names") or ()}
        if set(str(value) for value in expected) != expected_names:
            return False
        for requirement in expected.values():
            if (
                not isinstance(requirement, Mapping)
                or set(requirement) != {"primary"}
                or _normal_scalar(requirement.get("primary")) != "on"
            ):
                return False
        return True
    return False


def save_observation_supports_requirement(
    check_id: str,
    value: Any,
    evidence: SaveCheckEvidence,
) -> bool:
    """Whether mapped evidence authorizes its value as a future requirement.

    A validated save can sometimes identify a current value without granting
    authority to ask the runtime to reproduce that value.  Capture authoring
    uses this policy-free public seam rather than duplicating the mapping's
    authority-kind rules.
    """

    if not isinstance(evidence, SaveCheckEvidence):
        raise TypeError("save requirement support requires typed evidence")
    return bool(
        evidence.status == "observed"
        and evidence.complete is True
        and _requirement_is_supported(str(check_id), value, evidence)
    )


def save_check_matches_requirement(
    check_id: str,
    expected: Any,
    observed: Any,
) -> bool:
    """Compare one normalized save value with a profile requirement."""

    if check_id == "free_upgrade_locks":
        expected_set = {_normal_scalar(value) for value in expected or []}
        observed_set = {_normal_scalar(value) for value in observed or []}
        return expected_set <= observed_set
    if check_id in {"guardian_chips", "perk_bans"}:
        return {_normal_scalar(value) for value in expected or []} == {
            _normal_scalar(value) for value in observed or []
        }
    if check_id == "perk_auto_pick_order":
        expected_list = [_normal_scalar(value) for value in expected or []]
        observed_list = [_normal_scalar(value) for value in observed or []]
        return observed_list[: len(expected_list)] == expected_list
    if check_id == "modules" and isinstance(expected, Mapping):
        if not isinstance(observed, Mapping):
            return False
        return {
            str(key): _normal_scalar(value)
            for key, value in expected.items()
        } == {
            str(key): _normal_scalar(value)
            for key, value in observed.items()
        }
    if check_id == "orb_distance":
        options = _normalized_orb_requirement_options(expected)
        normalized_observed = _normalized_orb_requirement_options(observed)
        return bool(
            options
            and normalized_observed
            and len(normalized_observed) == 1
            and normalized_observed[0] in options
        )
    if isinstance(expected, Mapping):
        return _mapping_is_subset(expected, observed)
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        return [_normal_scalar(value) for value in expected] == [
            _normal_scalar(value) for value in observed or []
        ]
    return _normal_scalar(expected) == _normal_scalar(observed)


def _check_diagnostics(
    check_id: str,
    expected: Any,
    observed: Any,
    evidence: Optional[SaveCheckEvidence],
) -> dict[str, Any]:
    diagnostics = dict(evidence.diagnostics) if evidence is not None else {}
    if check_id == "free_upgrade_locks" and _is_sequence(expected):
        expected_set = {_normal_scalar(value) for value in expected}
        observed_labels = [str(value) for value in observed or ()]
        diagnostics["unmanaged_locks"] = sorted(
            label
            for label in observed_labels
            if _normal_scalar(label) not in expected_set
        )
    return diagnostics


def _mapping_is_subset(expected: Mapping[str, Any], observed: Any) -> bool:
    if not isinstance(observed, Mapping):
        return False
    for key, expected_value in expected.items():
        if key not in observed:
            return False
        observed_value = observed[key]
        if isinstance(expected_value, Mapping):
            if not _mapping_is_subset(expected_value, observed_value):
                return False
        elif _normal_scalar(expected_value) != _normal_scalar(observed_value):
            return False
    return True


def _normalized_orb_requirement_options(
    value: Any,
) -> Optional[list[dict[str, str]]]:
    """Normalize either one Orb tuple or its complete Range-preset set."""

    try:
        from core.orb_distance import (
            normalize_orb_distance_preset,
            normalize_orb_distance_presets,
        )

        if isinstance(value, Mapping):
            return [normalize_orb_distance_preset(value)]
        if _is_sequence(value):
            return normalize_orb_distance_presets(value)
    except (TypeError, ValueError):
        return None
    return None


def _snapshot_is_stale(
    snapshot: PlayerSaveSnapshot,
    *,
    max_snapshot_age_s: Optional[float],
    now: Optional[datetime],
) -> bool:
    if max_snapshot_age_s is None:
        return False
    captured = datetime.fromisoformat(snapshot.captured_at)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (current - captured).total_seconds() > max(0.0, max_snapshot_age_s)


def _normal_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return "on" if value else "off"
    return value.strip().casefold() if isinstance(value, str) else value


def _unknown_id_reason(label: str, values: Sequence[int]) -> str:
    if not values:
        return ""
    return f"unmapped {label} id(s): " + ", ".join(str(value) for value in values)


def _optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _exact_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


__all__ = [
    "MAX_PLAYER_SAVE_BYTES",
    "PLAYER_SAVE_DEVICE_PATH",
    "PlayerSaveDecodeError",
    "PlayerSaveError",
    "PlayerSaveCapabilityEvidence",
    "PlayerSaveParser",
    "PlayerSavePullError",
    "PlayerSaveSnapshot",
    "SAVE_ACCEPTED_DISPOSITIONS",
    "SAVE_MISMATCH_DISPOSITION",
    "SAVE_UI_REQUIRED_DISPOSITION",
    "SaveCheckEvidence",
    "decode_player_save_bytes",
    "pull_player_save_bytes",
    "read_player_save_file",
    "reconcile_acquired_requirements",
    "reconcile_direct_retry_requirements",
    "reconcile_requirements",
    "save_check_matches_requirement",
    "save_observation_supports_requirement",
]
