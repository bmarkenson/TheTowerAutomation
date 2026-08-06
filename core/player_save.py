"""Read, normalize, and reconcile The Tower ``playerInfo.dat`` snapshots.

The save is an independent observation channel.  It may replace a mapped UI
read only after an exact version mapping has been live-validated.  Unknown,
structurally changed, stale, incomplete, or mismatched saves always route the
check back through the existing UI implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import time
from typing import Any, Optional

from core.runtime_save import (
    NormalizedRuntimeSave,
    RuntimeSaveNormalizationError,
    normalize_runtime_save,
)
from core.profile_progression import (
    ProfileProgressionError,
    normalize_profile_progression,
)
from core.tournament_conditions import derive_tournament_conditions_from_save


ROOT = Path(__file__).resolve().parents[1]
PLAYER_SAVE_MAPPING_DIR = ROOT / "config" / "player_save_versions"
PLAYER_SAVE_DEVICE_PATH = (
    "/sdcard/Android/data/"
    "com.TechTreeGames.TheTower/files/playerInfo.dat"
)
MAX_PLAYER_SAVE_BYTES = 512 * 1024
MAX_DECOMPRESSED_SAVE_BYTES = 4 * 1024 * 1024
SNAPSHOT_SCHEMA_VERSION = 3
RAW_FIELD_MANIFEST_SCHEMA_VERSION = 1
RAW_FIELD_DISPOSITION_NAMES = frozenset(
    {
        "structural",
        "automation_gating",
        "profile_observation",
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
    """One profile-facing value derived from an exact save mapping."""

    check_id: str
    status: str
    value: Any
    source_fields: tuple[str, ...]
    complete: bool = True
    reason: str = ""
    authority: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "value": self.value,
            "source_fields": list(self.source_fields),
            "complete": self.complete,
            "reason": self.reason,
            "authority": dict(self.authority),
            "diagnostics": dict(self.diagnostics),
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
    profile_progression: Mapping[str, Any] = field(default_factory=dict)

    @property
    def mapping_supported(self) -> bool:
        return self.mapping_id is not None

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
            },
            "warnings": list(self.warnings),
            "profile_summary": dict(self.profile_summary),
            "profile_progression": dict(self.profile_progression),
            "checks": {
                check_id: evidence.as_dict()
                for check_id, evidence in sorted(self.checks.items())
            },
            "runtime_save": (
                self.runtime_save.as_dict()
                if self.runtime_save is not None
                else None
            ),
        }


def read_player_save_file(path: Path | str) -> PlayerSaveSnapshot:
    """Decode one local save without modifying or retaining the raw file."""

    source = Path(path)
    return decode_player_save_bytes(
        source.read_bytes(),
        source_name=source.name,
    )


def decode_player_save_bytes(
    payload: bytes,
    *,
    source_name: str = "playerInfo.dat",
    captured_at: Optional[datetime] = None,
) -> PlayerSaveSnapshot:
    """Decode a gzip-wrapped or raw NRBF save into a redacted snapshot."""

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
    mapping = _select_mapping(data_version, game_version)
    warnings: list[str] = []
    if mapping is None:
        warnings.append(
            "No exact player-save mapping exists for "
            f"dataVersion={data_version}, versionNumber={game_version}; "
            "all configuration checks require UI fallback."
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
        )

    shape_warnings = _validate_shape(decoded, mapping)
    warnings.extend(shape_warnings)
    shape_valid = not shape_warnings
    checks: dict[str, SaveCheckEvidence] = {}
    profile_summary: dict[str, Any] = {}
    profile_progression: dict[str, Any] = {}
    runtime_save: Optional[NormalizedRuntimeSave] = None
    if shape_valid:
        checks = _build_checks(decoded, mapping, captured_at=stamp)
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
                "The exact-version profile progression projection failed "
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
                "The exact-version runtime projection failed closed: "
                f"{exc}. Runtime save evidence requires UI fallback."
            )
    else:
        warnings.append(
            "The exact version matched but its structural signature changed; "
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
        profile_progression=profile_progression,
    )


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

    decisions: dict[str, dict[str, Any]] = {}
    for check_id, expected_value in expected.items():
        check_policy = _requirement_policy(requirements, str(check_id))
        evidence = snapshot.checks.get(str(check_id))
        observed = evidence.value if evidence is not None else None
        matches = (
            _check_matches(str(check_id), expected_value, observed)
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
            str(check_id) == "modules" and check_policy == "observe"
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
            reason = "exact_version_save_observation"
        elif matches is True:
            disposition = "save_match"
            reason = "exact_version_save_match"
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


@lru_cache(maxsize=1)
def _load_mappings() -> tuple[dict[str, Any], ...]:
    mappings: list[dict[str, Any]] = []
    for path in sorted(PLAYER_SAVE_MAPPING_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise PlayerSaveError(f"unsupported mapping schema in {path}")
        _validate_raw_field_manifest(payload, source=path)
        mappings.append(payload)
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


def _validate_shape(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> list[str]:
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
    if unexpected_fields:
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
    assigned = list(decoded.get(str(cards.get("assigned_field") or "")) or [])
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
            "cards": sum(bool(value) for value in decoded.get("cardUnlocked") or []),
            "ultimate_weapons": sum(
                bool(value) for value in decoded.get("ultimateWeaponUnlocked") or []
            ),
            "guardian_chips": sum(
                bool(value) for value in decoded.get("guardianChipUnlocked") or []
            ),
        },
    }


def _build_checks(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    captured_at: datetime,
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

    perk_ids = mapping.get("perk_ids") or {}
    first_id = _exact_int(decoded.get("firstPerkIndex"))
    first_name = perk_ids.get(str(first_id)) if first_id is not None else None
    checks["perk_first_choice"] = SaveCheckEvidence(
        check_id="perk_first_choice",
        status="observed" if first_name else "unmapped",
        value=first_name,
        source_fields=("firstPerkIndex",),
        reason="" if first_name else f"unmapped perk id {first_id}",
        authority={"kind": "matching_value"},
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

    checks["modules"] = _module_loadout_evidence(decoded, mapping)

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
    for check_id, reason in (mapping.get("unmapped_checks") or {}).items():
        checks[str(check_id)] = SaveCheckEvidence(
            check_id=str(check_id),
            status="unmapped",
            value=None,
            source_fields=(),
            complete=False,
            reason=str(reason),
        )
    return checks


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
    if (
        not _is_sequence(primary_specs)
        or not _is_sequence(assist_specs)
        or len(primary_specs) != 4
        or len(assist_specs) != 4
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
            raw_spec,
            item,
        )
        if failure:
            return _unmapped_module_evidence(source_fields, failure)

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

    for raw_spec in assist_specs:
        if not isinstance(raw_spec, Mapping):
            return _unmapped_module_evidence(
                source_fields,
                "Assist module mapping changed",
            )
        slot_type = _exact_int(raw_spec.get("type"))
        item = assist_by_type.get(slot_type) if slot_type is not None else None
        if item is None:
            return _unmapped_module_evidence(
                source_fields,
                "Assist module slot membership changed",
            )
        failure = _record_mapped_module_assignment(
            assignments,
            supported_names,
            slot_diagnostics,
            raw_spec,
            item,
        )
        if failure:
            return _unmapped_module_evidence(source_fields, failure)

    if len(assignments) != 8:
        return _unmapped_module_evidence(
            source_fields,
            "module loadout is partial",
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


def _record_mapped_module_assignment(
    assignments: dict[str, str],
    supported_names: dict[str, list[str]],
    diagnostics: list[dict[str, str]],
    spec: Mapping[str, Any],
    item: Mapping[str, Any],
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
    selected = next(
        (option for option in options if option[0] == observed_info_index),
        None,
    )
    if selected is None:
        return f"unsupported {role} module infoIndex"
    _info_index, name = selected
    assignments[slot_key] = name
    supported_names[slot_key] = [option_name for _index, option_name in options]
    diagnostics.append(
        {
            "slot_key": slot_key,
            "family": family,
            "role": role,
            "name": name,
        }
    )
    return ""


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
    )


def _ultimate_weapon_evidence(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> SaveCheckEvidence:
    names = list(mapping.get("ultimate_weapon_names") or [])
    unlocked = list(decoded.get("ultimateWeaponUnlocked") or [])
    active = list(decoded.get("ultimateWeaponOn") or [])
    if len(unlocked) != len(names) or len(active) != len(names):
        return SaveCheckEvidence(
            check_id="ultimate_weapons",
            status="unmapped",
            value=None,
            source_fields=("ultimateWeaponUnlocked", "ultimateWeaponOn"),
            complete=False,
            reason="ultimate weapon arrays changed length",
        )
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


def _check_matches(check_id: str, expected: Any, observed: Any) -> bool:
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
    "PlayerSavePullError",
    "PlayerSaveSnapshot",
    "SAVE_ACCEPTED_DISPOSITIONS",
    "SAVE_MISMATCH_DISPOSITION",
    "SAVE_UI_REQUIRED_DISPOSITION",
    "SaveCheckEvidence",
    "decode_player_save_bytes",
    "pull_player_save_bytes",
    "read_player_save_file",
    "reconcile_requirements",
]
