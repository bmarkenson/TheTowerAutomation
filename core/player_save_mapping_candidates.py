"""Strict, privacy-safe, review-only player-save mapping candidates.

Candidate receipts are evidence, never runtime authority.  This module has no
dependency on the player-save decoder or action-authority owners.  Runtime code
may append a receipt after deterministic same-boundary pairing; only operator
review surfaces read the receipt file, and even those surfaces can produce only
a proposed repository patch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING_CANDIDATE_RECEIPT_PATH = (
    ROOT / "logs" / "player_save_mapping_candidates" / "receipts-v2.jsonl"
)
MAPPING_CANDIDATE_SCHEMA_VERSION = 2
MAPPING_CANDIDATE_SCHEMA_ID = "thetower.player_save_mapping_candidate.v2"
MAX_MAPPING_CANDIDATE_RECEIPT_BYTES = 32 * 1024

MAPPING_CANDIDATE_STATUSES = frozenset(
    {
        "ready_for_review",
        "needs_more_evidence",
        "ambiguous",
        "conflicts_existing_mapping",
    }
)
MAPPING_CANDIDATE_VALUE_KINDS = frozenset(
    {
        "battle_history_killed_by_id",
        "guardian_chip_id",
        "module_assist_type",
        "module_info_index",
        "orb_distance_calibration",
        "perk_id",
        "target_priority_id",
        "tournament_league_id",
    }
)
MAPPING_CANDIDATE_CHECKS = frozenset(
    {
        "battle_history_killed_by",
        "guardian_chips",
        "modules",
        "orb_distance",
        "perk_auto_pick_order",
        "perk_bans",
        "perk_first_choice",
        "target_priority",
        "tournament_league",
    }
)
MAPPING_CANDIDATE_PAIRINGS = frozenset(
    {"exact_locator", "singleton_remainder", "calibration_sample"}
)
MAPPING_CANDIDATE_STRENGTHS = frozenset(
    {"deterministic", "supporting", "insufficient", "conflicting"}
)
MAPPING_CANDIDATE_COUNT_POLICIES = frozenset({"exact", "minimum"})
MAPPING_CANDIDATE_MAPPING_RESOLUTIONS = frozenset(
    {
        "exact",
        "compatible_exact_revision",
        "compatible_forward_revision",
        # Retained for already-written review receipts from the pending branch.
        "compatible",
    }
)

_SAFE_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,95}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,191}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ALLOWED_SCOPE_KEYS = frozenset({"slot_key", "family", "role", "field"})
_CHECK_VALUE_KINDS = {
    "battle_history_killed_by": frozenset({"battle_history_killed_by_id"}),
    "guardian_chips": frozenset({"guardian_chip_id"}),
    "modules": frozenset({"module_assist_type", "module_info_index"}),
    "orb_distance": frozenset({"orb_distance_calibration"}),
    "perk_auto_pick_order": frozenset({"perk_id"}),
    "perk_bans": frozenset({"perk_id"}),
    "perk_first_choice": frozenset({"perk_id"}),
    "target_priority": frozenset({"target_priority_id"}),
    "tournament_league": frozenset({"tournament_league_id"}),
}


class PlayerSaveMappingCandidateError(ValueError):
    """A candidate, receipt file, or proposal failed its strict contract."""


def fingerprint_json(value: object) -> str:
    """Return the canonical SHA-256 used by evidence and record identities."""

    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_value_not_json_safe"
        ) from exc
    return hashlib.sha256(rendered).hexdigest()


def pending_mapping_candidate(
    *,
    value_kind: object,
    raw_value: object,
    pairing_method: object,
    locator: object,
    expected_observation_count: object,
    observation_count_policy: object = "exact",
    known_semantic_values: Sequence[object] = (),
    known_raw_semantic_value: object = None,
    peer_semantic_values: Sequence[object] = (),
    peer_locator_values: Optional[Mapping[object, object]] = None,
    scope: Optional[Mapping[object, object]] = None,
    minimum_evidence_count: object = 1,
) -> dict[str, Any]:
    """Build one sanitized decoder-side discriminator with no UI claim."""

    kind = _choice(value_kind, MAPPING_CANDIDATE_VALUE_KINDS, "value_kind")
    raw = _raw_discriminator(kind, raw_value)
    pairing = _choice(
        pairing_method,
        MAPPING_CANDIDATE_PAIRINGS,
        "pairing_method",
    )
    expected = _positive_int(
        expected_observation_count,
        "expected_observation_count",
    )
    minimum = _positive_int(minimum_evidence_count, "minimum_evidence_count")
    return {
        "value_kind": kind,
        "raw_discriminator": raw,
        "pairing_method": pairing,
        "locator": _safe_id(locator, "locator"),
        "expected_observation_count": expected,
        "observation_count_policy": _choice(
            observation_count_policy,
            MAPPING_CANDIDATE_COUNT_POLICIES,
            "observation_count_policy",
        ),
        "minimum_evidence_count": minimum,
        "known_semantic_values": list(
            _semantic_values(known_semantic_values, "known_semantic_values")
        ),
        "known_raw_semantic_value": _optional_semantic(
            known_raw_semantic_value,
            "known_raw_semantic_value",
        ),
        "peer_semantic_values": list(
            _semantic_values(peer_semantic_values, "peer_semantic_values")
        ),
        "peer_locator_values": _locator_values(
            peer_locator_values or {},
            "peer_locator_values",
        ),
        "scope": _scope(scope or {}),
    }


def build_mapping_candidate_context(
    *,
    mapping_id: object,
    data_version: object,
    game_version: object,
    mapping_resolution: object,
    authority_mapping_id: object,
    structural_mapping_id: object,
    snapshot_fingerprint: object,
    candidates: Mapping[object, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Build the private capture-bound context retained until UI inspection."""

    if not isinstance(candidates, Mapping):
        raise PlayerSaveMappingCandidateError("candidate_context_invalid")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for raw_check, raw_candidates in candidates.items():
        check = _choice(raw_check, MAPPING_CANDIDATE_CHECKS, "check_id")
        if not _is_sequence(raw_candidates):
            raise PlayerSaveMappingCandidateError("candidate_context_invalid")
        items = [_normalize_pending(item) for item in raw_candidates]
        for item in items:
            _require_check_value_kind(check, item["value_kind"])
            _require_pending_pairing_invariants(check, item)
        if items:
            normalized[check] = items
    if not normalized:
        raise PlayerSaveMappingCandidateError("candidate_context_empty")
    resolution = _choice(
        mapping_resolution,
        MAPPING_CANDIDATE_MAPPING_RESOLUTIONS,
        "mapping_resolution",
    )
    normalized_mapping_id = _safe_id(mapping_id, "mapping_id")
    authority_id = _safe_id(authority_mapping_id, "authority_mapping_id")
    structural_id = _safe_id(
        structural_mapping_id,
        "structural_mapping_id",
    )
    if resolution == "exact" and not (
        normalized_mapping_id == authority_id == structural_id
    ):
        raise PlayerSaveMappingCandidateError(
            "exact_mapping_candidate_authority_mismatch"
        )
    return {
        "schema_version": 1,
        "mapping": {
            "mapping_id": normalized_mapping_id,
            "data_version": _nonnegative_int(data_version, "data_version"),
            "game_version": _nonnegative_int(game_version, "game_version"),
            "resolution": resolution,
            "authority_mapping_id": authority_id,
            "structural_mapping_id": structural_id,
        },
        "snapshot_fingerprint": _sha256(
            snapshot_fingerprint,
            "snapshot_fingerprint",
        ),
        "candidates": normalized,
    }


def validate_mapping_candidate_context(raw: object) -> Optional[dict[str, Any]]:
    """Return one canonical private candidate context or ``None``."""

    try:
        context = _exact_mapping(
            raw,
            {"schema_version", "mapping", "snapshot_fingerprint", "candidates"},
            "context",
        )
        if context.get("schema_version") != 1:
            return None
        raw_mapping = _exact_mapping(
            context.get("mapping"),
            {
                "mapping_id",
                "data_version",
                "game_version",
                "resolution",
                "authority_mapping_id",
                "structural_mapping_id",
            },
            "context_mapping",
        )
        mapping = {
            "mapping_id": _safe_id(
                raw_mapping.get("mapping_id"), "mapping_id"
            ),
            "data_version": _nonnegative_int(
                raw_mapping.get("data_version"), "data_version"
            ),
            "game_version": _nonnegative_int(
                raw_mapping.get("game_version"), "game_version"
            ),
            "resolution": _choice(
                raw_mapping.get("resolution"),
                MAPPING_CANDIDATE_MAPPING_RESOLUTIONS,
                "mapping_resolution",
            ),
            "authority_mapping_id": _safe_id(
                raw_mapping.get("authority_mapping_id"),
                "authority_mapping_id",
            ),
            "structural_mapping_id": _safe_id(
                raw_mapping.get("structural_mapping_id"),
                "structural_mapping_id",
            ),
        }
        candidates = context.get("candidates")
        if not isinstance(candidates, Mapping):
            return None
        normalized = build_mapping_candidate_context(
            mapping_id=mapping["mapping_id"],
            data_version=mapping["data_version"],
            game_version=mapping["game_version"],
            mapping_resolution=mapping["resolution"],
            authority_mapping_id=mapping["authority_mapping_id"],
            structural_mapping_id=mapping["structural_mapping_id"],
            snapshot_fingerprint=context.get("snapshot_fingerprint"),
            candidates=candidates,
        )
    except (PlayerSaveMappingCandidateError, TypeError, ValueError):
        return None
    return normalized if normalized == dict(raw) else None


def build_mapping_candidate_ui_evidence(
    check_id: object,
    *,
    canonical_values: Sequence[object],
    locator_values: Mapping[object, object],
    locator_scopes: Optional[Mapping[object, Mapping[object, object]]] = None,
    complete: object = True,
    pre_mutation: object = True,
    observed_at: object = None,
) -> dict[str, Any]:
    """Build the privacy-safe UI half of one same-boundary pairing."""

    check = _choice(check_id, MAPPING_CANDIDATE_CHECKS, "check_id")
    stamp = _utc_datetime(
        datetime.now(timezone.utc) if observed_at is None else observed_at,
        "ui_evidence_observed_at",
    ).isoformat()
    safe_values = list(
        _semantic_values(
            canonical_values,
            "canonical_values",
            allow_duplicates=(check in {"modules", "orb_distance"}),
        )
    )
    safe_locators = _locator_values(locator_values, "locator_values")
    raw_scopes = locator_scopes or {}
    if not isinstance(raw_scopes, Mapping):
        raise PlayerSaveMappingCandidateError("locator_scopes_invalid")
    safe_scopes = {
        _safe_id(locator, "scope_locator"): _scope(scope)
        for locator, scope in raw_scopes.items()
    }
    source_fingerprint = fingerprint_json(
        {
            "schema_version": 1,
            "check_id": check,
            "canonical_values": safe_values,
            "locator_values": safe_locators,
            "locator_scopes": safe_scopes,
            "complete": complete is True,
            "pre_mutation": pre_mutation is True,
        }
    )
    evidence = {
        "canonical_values": safe_values,
        "locator_values": safe_locators,
        "locator_scopes": safe_scopes,
        "complete": complete is True,
        "pre_mutation": pre_mutation is True,
        "observed_at": stamp,
        "source_observation_fingerprint": source_fingerprint,
    }
    _ui_observation(check, evidence)
    return evidence


def resolve_mapping_candidates(
    check_id: object,
    pending: Sequence[Mapping[str, Any]],
    ui_evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Classify every safe discriminator against canonical pre-mutation UI."""

    check = _choice(check_id, MAPPING_CANDIDATE_CHECKS, "check_id")
    if not _is_sequence(pending):
        raise PlayerSaveMappingCandidateError("pending_candidates_invalid")
    candidates = tuple(_normalize_pending(item) for item in pending)
    for item in candidates:
        _require_check_value_kind(check, item["value_kind"])
        _require_pending_pairing_invariants(check, item)
    locator_values, semantic_values, locator_scopes = _ui_observation(
        check,
        ui_evidence,
    )
    results: list[dict[str, Any]] = []
    duplicate_identities = {
        identity
        for identity in {
            _pending_identity(item) for item in candidates
        }
        if sum(_pending_identity(item) == identity for item in candidates) > 1
    }
    for item in candidates:
        semantic: Optional[str] = None
        status = "ambiguous"
        strength = "insufficient"
        reason = "UI evidence did not prove a unique same-boundary pairing"
        if _pending_identity(item) in duplicate_identities:
            reason = "multiple pending discriminators have the same identity"
        elif item["pairing_method"] == "exact_locator":
            peers_match = all(
                locator_values.get(locator) == value
                for locator, value in item["peer_locator_values"].items()
            )
            observed_scope = locator_scopes.get(item["locator"], {})
            if (
                _observation_count_matches(item, len(semantic_values))
                and peers_match
                and (
                    not item["scope"]
                    or observed_scope == item["scope"]
                )
            ):
                semantic = locator_values.get(item["locator"])
                if semantic is not None:
                    status, strength, reason = _resolved_disposition(
                        item,
                        semantic,
                    )
        elif item["pairing_method"] == "singleton_remainder":
            candidates_for_scope = _semantic_values_for_scope(
                item,
                semantic_values,
                locator_scopes,
            )
            remainder = set(candidates_for_scope) - set(
                item["peer_semantic_values"]
            )
            if (
                len(candidates_for_scope)
                == item["expected_observation_count"]
                and len(candidates_for_scope) == len(set(candidates_for_scope))
                and len(remainder) == 1
            ):
                semantic = next(iter(remainder))
                status, strength, reason = _resolved_disposition(item, semantic)
        else:
            observed_scope = locator_scopes.get(item["locator"], {})
            if (
                len(semantic_values) == item["expected_observation_count"]
                and len(locator_values) == item["expected_observation_count"]
                and observed_scope == item["scope"]
            ):
                semantic = locator_values.get(item["locator"])
            if semantic is not None:
                status = "needs_more_evidence"
                strength = "supporting"
                reason = (
                    "calibration evidence is paired but cannot establish a "
                    "versioned mapping from one observation"
                )
        observed_for_candidate = semantic_values
        resolved_scope = item["scope"]
        if item["value_kind"] == "module_assist_type":
            observed_for_candidate = _semantic_values_for_scope(
                item,
                semantic_values,
                locator_scopes,
            )
            if semantic is not None:
                matching_scopes = {
                    tuple(sorted(scope.items()))
                    for scope in locator_scopes.values()
                    if scope.get("role") == "assist"
                    and scope.get("family") == semantic
                }
                if len(matching_scopes) != 1:
                    semantic = None
                    status = "ambiguous"
                    strength = "insufficient"
                    reason = "assist family did not identify exactly one UI slot"
                else:
                    resolved_scope = dict(next(iter(matching_scopes)))
        results.append(
            {
                **item,
                "scope": resolved_scope,
                "semantic_value": semantic,
                "observed_semantic_values": list(observed_for_candidate),
                "status": status,
                "evidence_strength": strength,
                "reason": reason,
            }
        )
    return tuple(results)


def reconcile_mapping_candidate_resolutions(
    claims: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Fail closed when one global raw ID pairs with different semantics."""

    if not _is_sequence(claims):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_resolution_claims_invalid"
        )
    normalized: list[dict[str, Any]] = []
    for claim in claims:
        shaped = _exact_mapping(claim, {"check_id", "candidate"}, "claim")
        check_id = _choice(
            shaped.get("check_id"),
            MAPPING_CANDIDATE_CHECKS,
            "check_id",
        )
        candidate = _normalize_resolved(shaped.get("candidate"))
        _require_check_value_kind(check_id, candidate["value_kind"])
        normalized.append({"check_id": check_id, "candidate": candidate})

    by_discriminator: dict[tuple[str, str, str], list[int]] = {}
    for index, claim in enumerate(normalized):
        candidate = claim["candidate"]
        scope_owner = (
            candidate["scope"].get("field", "")
            if candidate["value_kind"] == "orb_distance_calibration"
            else ""
        )
        identity = (
            candidate["value_kind"],
            json.dumps(candidate["raw_discriminator"], sort_keys=True),
            scope_owner,
        )
        by_discriminator.setdefault(identity, []).append(index)
    for indexes in by_discriminator.values():
        semantics = {
            normalized[index]["candidate"]["semantic_value"]
            for index in indexes
            if normalized[index]["candidate"]["semantic_value"] is not None
        }
        if len(semantics) <= 1:
            continue
        for index in indexes:
            candidate = normalized[index]["candidate"]
            normalized[index]["candidate"] = {
                **candidate,
                "semantic_value": None,
                "status": "ambiguous",
                "evidence_strength": "insufficient",
                "reason": (
                    "the same save discriminator paired with conflicting UI "
                    "semantics in this capture"
                ),
            }
    return tuple(normalized)


def build_mapping_candidate_record(
    *,
    mapping: Mapping[str, Any],
    check_id: object,
    candidate: Mapping[str, Any],
    snapshot_fingerprint: object,
    ui_evidence_fingerprint: object,
    source_observation_fingerprint: object,
    workflow_provenance: Mapping[str, Any],
    observed_at: object,
    recorded_at: object = None,
) -> dict[str, Any]:
    """Build one canonical append-only candidate or evidence-gap receipt."""

    normalized_mapping = _normalize_mapping(mapping)
    normalized_check = _choice(check_id, MAPPING_CANDIDATE_CHECKS, "check_id")
    resolved = _normalize_resolved(candidate)
    _require_check_value_kind(normalized_check, resolved["value_kind"])
    if (
        normalized_mapping["resolution"]
        in {"compatible", "compatible_forward_revision"}
        and resolved["status"] == "ready_for_review"
    ):
        resolved = {
            **resolved,
            "status": "needs_more_evidence",
            "evidence_strength": "supporting",
            "reason": (
                "the pairing is deterministic, but the captured save used "
                "revision-compatibility authority and needs exact-version evidence"
            ),
        }
    workflow = _normalize_workflow_provenance(workflow_provenance)
    observed_stamp = _utc_datetime(observed_at, "observed_at").isoformat()
    recorded_stamp = _utc_datetime(
        datetime.now(timezone.utc) if recorded_at is None else recorded_at,
        "recorded_at",
    ).isoformat()
    candidate_payload = {
        "check_id": normalized_check,
        "value_kind": resolved["value_kind"],
        "raw_discriminator": resolved["raw_discriminator"],
        "locator": resolved["locator"],
        "scope": resolved["scope"],
        "semantic_value": resolved["semantic_value"],
        "observed_semantic_values": resolved["observed_semantic_values"],
        "status": resolved["status"],
        "reason": resolved["reason"],
    }
    evidence_payload = {
        "snapshot_fingerprint": _sha256(
            snapshot_fingerprint,
            "snapshot_fingerprint",
        ),
        "ui_evidence_fingerprint": _sha256(
            ui_evidence_fingerprint,
            "ui_evidence_fingerprint",
        ),
        "source_observation_fingerprint": _sha256(
            source_observation_fingerprint,
            "source_observation_fingerprint",
        ),
        "workflow": workflow,
        "pairing_method": resolved["pairing_method"],
        "evidence_strength": resolved["evidence_strength"],
        "pre_mutation": True,
        "observed_at": observed_stamp,
    }
    authority = {
        "disposition": "candidate_only",
        "mapping_promotion": "explicit_reviewed_repository_change",
        "runtime_reads_receipt": False,
        "authorizes_ui_suppression": False,
        "authorizes_input": False,
        "authorizes_repair": False,
        "changes_configuration": False,
        "changes_strategy": False,
        "self_promotes": False,
    }
    record_id = _candidate_record_id(
        normalized_mapping,
        candidate_payload,
        evidence_payload,
        authority,
    )
    record = {
        "schema_version": MAPPING_CANDIDATE_SCHEMA_VERSION,
        "schema_id": MAPPING_CANDIDATE_SCHEMA_ID,
        "record_type": "mapping_candidate",
        "record_id": record_id,
        "recorded_at": recorded_stamp,
        "mapping": normalized_mapping,
        "candidate": candidate_payload,
        "evidence": evidence_payload,
        "authority": authority,
    }
    validate_mapping_candidate_record(record)
    return record


class AppendOnlyMappingCandidateStore:
    """Locked mode-0600 JSONL storage with idempotent atomic appends."""

    def __init__(
        self,
        path: Path | str = DEFAULT_MAPPING_CANDIDATE_RECEIPT_PATH,
    ) -> None:
        self.path = Path(path)

    def append_once(self, record: Mapping[str, Any]) -> bool:
        """Append once; return ``False`` for an identical durable receipt."""

        normalized = validate_mapping_candidate_record(record)
        rendered = _render_record(normalized)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        appended = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.fchmod(descriptor, 0o600)
            _recover_partial_tail(descriptor)
            original_size = os.fstat(descriptor).st_size
            os.lseek(descriptor, 0, os.SEEK_SET)
            existing = _read_locked_records(descriptor)
            for item in existing:
                if item["record_id"] != normalized["record_id"]:
                    continue
                if item != normalized:
                    raise PlayerSaveMappingCandidateError(
                        "mapping_candidate_record_id_conflict"
                    )
                break
            else:
                try:
                    written = os.write(descriptor, rendered)
                    if written != len(rendered):
                        os.ftruncate(descriptor, original_size)
                        os.fsync(descriptor)
                        raise OSError("candidate receipt append was partial")
                except OSError:
                    if os.fstat(descriptor).st_size != original_size:
                        os.ftruncate(descriptor, original_size)
                        os.fsync(descriptor)
                    raise
                os.fsync(descriptor)
                appended = True
        finally:
            os.close(descriptor)
        # A duplicate after a prior fail-first directory fsync still retries the
        # directory durability boundary without appending a second receipt.
        _fsync_directory(self.path.parent)
        return appended

    def append(self, record: Mapping[str, Any]) -> None:
        self.append_once(record)

    @contextmanager
    def locked_records(self) -> Iterator[list[dict[str, Any]]]:
        """Yield one immutable queue view while blocking concurrent appends."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.fchmod(descriptor, 0o600)
            _recover_partial_tail(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            yield _read_locked_records(descriptor)
        finally:
            os.close(descriptor)

    def list_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        descriptor = os.open(self.path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _recover_partial_tail(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            return _read_locked_records(descriptor)
        finally:
            os.close(descriptor)

    def get(self, record_id: object) -> dict[str, Any]:
        normalized_id = _sha256(record_id, "record_id")
        matches = [
            record
            for record in self.list_records()
            if record["record_id"] == normalized_id
        ]
        if len(matches) != 1:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_record_not_found"
            )
        return matches[0]


def mapping_candidate_review_status(
    *,
    store: Optional[AppendOnlyMappingCandidateStore] = None,
    repository_root: Path | str = ROOT,
) -> dict[str, Any]:
    """Project durable candidate receipts into a nonblocking review queue."""

    owner = store or AppendOnlyMappingCandidateStore()
    try:
        records = owner.list_records()
        mappings = _repository_mappings_by_id(Path(repository_root))
    except (OSError, PlayerSaveMappingCandidateError) as exc:
        return {
            "schema_version": 1,
            "available": False,
            "items": [],
            "counts": {"candidate_store_unavailable": 1},
            "reason": str(exc),
        }
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        candidate = record["candidate"]
        claim_key = fingerprint_json(
            {
                "mapping": record["mapping"],
                "check_id": candidate["check_id"],
                "value_kind": candidate["value_kind"],
                "raw_discriminator": candidate["raw_discriminator"],
                "scope": candidate["scope"],
                "semantic_value": candidate["semantic_value"],
            }
        )
        prior = latest.get(claim_key)
        if prior is None or record["recorded_at"] > prior["recorded_at"]:
            latest[claim_key] = record
    items = [
        _mapping_candidate_status_item(record, mappings)
        for record in latest.values()
    ]
    items.sort(
        key=lambda item: (
            str(item["state"]),
            str(item["check_id"]),
            str(item["mapping_id"]),
            str(item["record_id"]),
        )
    )
    counts: dict[str, int] = {}
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    return {
        "schema_version": 1,
        "available": True,
        "items": items,
        "counts": counts,
        "reason": "",
    }


def mapping_candidate_record_status(
    record: Mapping[str, Any],
    *,
    repository_root: Path | str = ROOT,
) -> dict[str, Any]:
    """Project one exact durable record without latest-claim deduplication."""

    normalized = validate_mapping_candidate_record(record)
    mappings = _repository_mappings_by_id(Path(repository_root))
    return _mapping_candidate_status_item(normalized, mappings)


def _repository_mappings_by_id(
    repository_root: Path,
) -> dict[str, dict[str, Any]]:
    directory = repository_root / "config" / "player_save_versions"
    mappings: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_repository_mapping_invalid"
            ) from exc
        mapping_id = _safe_id(payload.get("mapping_id"), "mapping_id")
        if mapping_id in mappings:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_repository_mapping_duplicated"
            )
        mappings[mapping_id] = payload
    return mappings


def canonical_mapping_set_fingerprint(
    mappings: Mapping[str, Mapping[str, Any]],
    *,
    authority_mapping_id: object,
    structural_mapping_id: object,
) -> str:
    """Fingerprint the exact canonical mapping documents used by one decode."""

    authority_id = _safe_id(authority_mapping_id, "authority_mapping_id")
    structural_id = _safe_id(structural_mapping_id, "structural_mapping_id")
    selected: list[dict[str, Any]] = []
    for mapping_id in sorted({authority_id, structural_id}):
        mapping = mappings.get(mapping_id)
        if not isinstance(mapping, Mapping):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_target_mapping_missing"
            )
        selected.append(deepcopy(dict(mapping)))
    return fingerprint_json(
        {
            "schema_version": 1,
            "authority_mapping_id": authority_id,
            "structural_mapping_id": structural_id,
            "mappings": selected,
        }
    )


def _mapping_candidate_status_item(
    record: Mapping[str, Any],
    mappings: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    mapping = record["mapping"]
    candidate = record["candidate"]
    candidate_status = candidate["status"]
    if candidate_status == "needs_more_evidence":
        state = "more_evidence_required"
        reason = candidate["reason"]
    elif candidate_status == "ambiguous":
        state = "evidence_ambiguous"
        reason = candidate["reason"]
    elif candidate_status == "conflicts_existing_mapping":
        state = "canonical_conflict"
        reason = candidate["reason"]
    elif mapping["resolution"] not in {
        "exact",
        "compatible_exact_revision",
    }:
        state = "more_evidence_required"
        reason = "exact-version evidence is required before integration"
    else:
        target_ids = [mapping["mapping_id"]]
        if mapping["resolution"] == "compatible_exact_revision":
            target_ids = [
                mapping["authority_mapping_id"],
                mapping["structural_mapping_id"],
            ]
        target_states = [
            _candidate_state_in_mapping(candidate, mappings.get(mapping_id))
            for mapping_id in target_ids
        ]
        if "conflict" in target_states:
            state = "canonical_conflict"
            reason = "canonical mapping conflicts with the observed pairing"
        elif target_states and all(value == "match" for value in target_states):
            state = "integrated"
            reason = "canonical mapping already contains the observed pairing"
        elif target_states == ["match", "absent"]:
            state = "mirror_pending"
            reason = "exact-version structural mirror integration is pending"
        elif target_states == ["absent", "match"]:
            state = "authority_pending"
            reason = "canonical authority integration is pending"
        elif "unsupported" in target_states:
            state = "review_required"
            reason = "the observation has no automatic canonical patch owner"
        elif "missing" in target_states:
            state = "review_required"
            reason = "a canonical target mapping is unavailable"
        else:
            state = "review_required"
            reason = "canonical mapping integration is pending"
    return {
        "record_id": record["record_id"],
        "candidate_record_id": record["record_id"],
        "mapping_id": mapping["mapping_id"],
        "data_version": mapping["data_version"],
        "game_version": mapping["game_version"],
        "check_id": candidate["check_id"],
        "value_kind": candidate["value_kind"],
        "raw_value": candidate["raw_discriminator"]["value"],
        "semantic_value": candidate["semantic_value"],
        "scope": dict(candidate["scope"]),
        "state": state,
        "reason": reason,
        "recorded_at": record["recorded_at"],
    }


def _candidate_state_in_mapping(
    candidate: Mapping[str, Any],
    mapping: Optional[Mapping[str, Any]],
) -> str:
    if mapping is None:
        return "missing"
    kind = candidate["value_kind"]
    raw_value = candidate["raw_discriminator"]["value"]
    semantic = candidate["semantic_value"]
    owners = {
        "perk_id": mapping.get("perk_ids"),
        "guardian_chip_id": mapping.get("guardian_chip_ids"),
        "target_priority_id": mapping.get("target_priority_ids"),
    }
    owner = owners.get(kind)
    if kind == "battle_history_killed_by_id":
        history = (mapping.get("runtime_save") or {}).get("battle_history")
        owner = (
            history.get("killed_by_ids")
            if isinstance(history, Mapping)
            else None
        )
    if isinstance(owner, Mapping):
        mapped = owner.get(str(raw_value))
        if mapped is not None:
            return "match" if mapped == semantic else "conflict"
        return "conflict" if semantic in owner.values() else "absent"
    if kind == "module_info_index":
        loadout = mapping.get("module_loadout") or {}
        role = candidate["scope"].get("role")
        slots = loadout.get(role) if isinstance(loadout, Mapping) else None
        if not isinstance(slots, list):
            return "missing"
        all_values = [
            value
            for candidate_role in ("primary", "assist")
            for slot in loadout.get(candidate_role, ())
            if isinstance(slot, Mapping)
            for value in slot.get("values", ())
            if isinstance(value, Mapping)
        ]
        if any(
            value.get("info_index") == raw_value
            and value.get("name") != semantic
            for value in all_values
        ) or any(
            value.get("name") == semantic
            and value.get("info_index") != raw_value
            for value in all_values
        ):
            return "conflict"
        target = next(
            (
                slot
                for slot in slots
                if isinstance(slot, Mapping)
                and slot.get("slot_key") == candidate["scope"].get("slot_key")
            ),
            None,
        )
        if not isinstance(target, Mapping):
            return "missing"
        return (
            "match"
            if any(
                isinstance(value, Mapping)
                and value.get("info_index") == raw_value
                and value.get("name") == semantic
                for value in target.get("values", ())
            )
            else "absent"
        )
    if kind == "module_assist_type":
        assist = (mapping.get("module_loadout") or {}).get("assist")
        if not isinstance(assist, list):
            return "missing"
        target = next(
            (
                item
                for item in assist
                if isinstance(item, Mapping)
                and item.get("slot_key") == candidate["scope"].get("slot_key")
                and item.get("family") == semantic
            ),
            None,
        )
        if not isinstance(target, Mapping):
            return "missing"
        if target.get("type") == raw_value:
            return "match"
        if any(
            isinstance(item, Mapping) and item.get("type") == raw_value
            for item in assist
        ):
            return "conflict"
        return "absent"
    return "unsupported"


# Compatibility with the reference branch's public writer name.
AppendOnlyMappingCandidateWriter = AppendOnlyMappingCandidateStore


def proposed_mapping_patch(
    record: Mapping[str, Any],
    *,
    repository_root: Path | str = ROOT,
) -> dict[str, Any]:
    """Return a non-mutating repository proposal for one review-ready receipt."""

    normalized = validate_mapping_candidate_record(record)
    candidate = normalized["candidate"]
    mapping = normalized["mapping"]
    if mapping["resolution"] not in {"exact", "compatible_exact_revision"}:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_proposal_requires_exact_mapping"
        )
    reviewable_conflict = (
        candidate["value_kind"] == "module_assist_type"
        and candidate["status"] == "conflicts_existing_mapping"
    )
    if candidate["status"] != "ready_for_review" and not reviewable_conflict:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_not_ready_for_proposal"
        )
    root = Path(repository_root)
    target_ids = [mapping["mapping_id"]]
    if mapping["resolution"] == "compatible_exact_revision":
        target_ids = [
            mapping["authority_mapping_id"],
            mapping["structural_mapping_id"],
        ]
    targets: list[dict[str, Any]] = []
    for mapping_id in target_ids:
        target, target_bytes, target_mapping = _repository_mapping_target(
            root,
            mapping_id,
        )
        state = _candidate_state_in_mapping(candidate, target_mapping)
        if state == "conflict":
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_conflicts_current_file"
            )
        operations = (
            []
            if state == "match"
            else [_proposal_operation(candidate, target_mapping)]
        )
        targets.append(
            {
                "path": target.relative_to(root).as_posix(),
                "expected_sha256": hashlib.sha256(target_bytes).hexdigest(),
                "mapping_id": mapping_id,
                "state": "integrated" if state == "match" else "pending",
                "operations": operations,
            }
        )
    if not any(target["operations"] for target in targets):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_proposal_already_integrated"
        )
    proposal = {
        "schema_version": 1,
        "capability": "player_save_mapping_candidate_review_v1",
        "record_id": normalized["record_id"],
        "status": "proposed",
        "validation": [
            ".venv/bin/python -m pytest -q test/test_player_save.py",
            ".venv/bin/python tools/development.py checkpoint",
        ],
        "applies_changes": False,
        "promotes_mapping": False,
        "review_required": True,
    }
    if mapping["resolution"] == "exact":
        proposal["target"] = {
            key: targets[0][key]
            for key in ("path", "expected_sha256", "mapping_id")
        }
        proposal["operations"] = targets[0]["operations"]
        return proposal
    proposal.update(
        schema_version=2,
        capability="player_save_mapping_candidate_review_v2",
        atomic_group=True,
        targets=targets,
    )
    return proposal


def validate_mapping_candidate_result(
    record: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> None:
    """Require one rendered canonical mapping to own the receipt exactly."""

    normalized = validate_mapping_candidate_record(record)
    if not isinstance(mapping, Mapping):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_proposal_result_invalid"
        )
    candidate = normalized["candidate"]
    if candidate["value_kind"] not in {
        "module_info_index",
        "module_assist_type",
    }:
        if _candidate_state_in_mapping(candidate, mapping) != "match":
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_result_mismatch"
            )
        return
    loadout = mapping.get("module_loadout")
    if not isinstance(loadout, Mapping):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_proposal_result_invalid"
        )
    raw_to_name: dict[int, str] = {}
    name_to_raw: dict[str, int] = {}
    for role in ("primary", "assist"):
        slots = loadout.get(role)
        if not isinstance(slots, list):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_result_invalid"
            )
        for slot in slots:
            if not isinstance(slot, Mapping):
                raise PlayerSaveMappingCandidateError(
                    "mapping_candidate_proposal_result_invalid"
                )
            values = slot.get("values")
            if not isinstance(values, list):
                raise PlayerSaveMappingCandidateError(
                    "mapping_candidate_proposal_result_invalid"
                )
            for value in values:
                if (
                    not isinstance(value, Mapping)
                    or isinstance(value.get("info_index"), bool)
                    or not isinstance(value.get("info_index"), int)
                    or not isinstance(value.get("name"), str)
                    or not value["name"].strip()
                ):
                    raise PlayerSaveMappingCandidateError(
                        "mapping_candidate_proposal_result_invalid"
                    )
                raw_value = value["info_index"]
                semantic_value = value["name"]
                prior_name = raw_to_name.get(raw_value)
                prior_raw = name_to_raw.get(semantic_value)
                if (
                    prior_name is not None
                    and prior_name != semantic_value
                ) or (
                    prior_raw is not None
                    and prior_raw != raw_value
                ):
                    raise PlayerSaveMappingCandidateError(
                        "mapping_candidate_proposal_result_conflict"
                    )
                raw_to_name[raw_value] = semantic_value
                name_to_raw[semantic_value] = raw_value
    if _candidate_state_in_mapping(candidate, mapping) != "match":
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_proposal_result_mismatch"
        )


def _repository_mapping_target(
    repository_root: Path,
    mapping_id: str,
) -> tuple[Path, bytes, dict[str, Any]]:
    directory = repository_root / "config" / "player_save_versions"
    matches: list[tuple[Path, bytes, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload_bytes = path.read_bytes()
            payload = json.loads(payload_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_target_mapping_invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_target_mapping_invalid"
            )
        if payload.get("mapping_id") == mapping_id:
            matches.append((path, payload_bytes, payload))
    if len(matches) != 1:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_target_mapping_missing"
        )
    return matches[0]


def validate_mapping_candidate_record(record: object) -> dict[str, Any]:
    """Return a deep-normalized receipt or reject any shape drift."""

    if not isinstance(record, Mapping) or set(record) != {
        "schema_version",
        "schema_id",
        "record_type",
        "record_id",
        "recorded_at",
        "mapping",
        "candidate",
        "evidence",
        "authority",
    }:
        raise PlayerSaveMappingCandidateError("mapping_candidate_changed_shape")
    if record.get("schema_version") != MAPPING_CANDIDATE_SCHEMA_VERSION:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_schema_unsupported"
        )
    if record.get("schema_id") != MAPPING_CANDIDATE_SCHEMA_ID:
        raise PlayerSaveMappingCandidateError("mapping_candidate_schema_mismatch")
    if record.get("record_type") != "mapping_candidate":
        raise PlayerSaveMappingCandidateError("mapping_candidate_type_invalid")
    record_id = _sha256(record.get("record_id"), "record_id")
    recorded_at = _utc_datetime(record.get("recorded_at"), "recorded_at")
    mapping = _normalize_mapping(record.get("mapping"))

    raw_candidate = _exact_mapping(
        record.get("candidate"),
        {
            "check_id",
            "value_kind",
            "raw_discriminator",
            "locator",
            "scope",
            "semantic_value",
            "observed_semantic_values",
            "status",
            "reason",
        },
        "candidate",
    )
    kind = _choice(
        raw_candidate.get("value_kind"),
        MAPPING_CANDIDATE_VALUE_KINDS,
        "value_kind",
    )
    candidate = {
        "check_id": _choice(
            raw_candidate.get("check_id"),
            MAPPING_CANDIDATE_CHECKS,
            "check_id",
        ),
        "value_kind": kind,
        "raw_discriminator": _raw_discriminator(
            kind,
            raw_candidate.get("raw_discriminator"),
            already_wrapped=True,
        ),
        "locator": _safe_id(raw_candidate.get("locator"), "locator"),
        "scope": _scope(raw_candidate.get("scope")),
        "semantic_value": _optional_semantic(
            raw_candidate.get("semantic_value"),
            "semantic_value",
        ),
        "observed_semantic_values": list(
            _semantic_values(
                raw_candidate.get("observed_semantic_values"),
                "observed_semantic_values",
                allow_duplicates=(
                    kind
                    in {"module_info_index", "module_assist_type", "orb_distance_calibration"}
                ),
            )
        ),
        "status": _choice(
            raw_candidate.get("status"),
            MAPPING_CANDIDATE_STATUSES,
            "candidate_status",
        ),
        "reason": _bounded_text(raw_candidate.get("reason"), 512, "reason"),
    }
    _require_check_value_kind(candidate["check_id"], candidate["value_kind"])
    if (
        candidate["status"] in {"ready_for_review", "conflicts_existing_mapping"}
        and candidate["semantic_value"] is None
    ):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_semantic_value_required"
        )

    raw_evidence = _exact_mapping(
        record.get("evidence"),
        {
            "snapshot_fingerprint",
            "ui_evidence_fingerprint",
            "source_observation_fingerprint",
            "workflow",
            "pairing_method",
            "evidence_strength",
            "pre_mutation",
            "observed_at",
        },
        "evidence",
    )
    evidence = {
        "snapshot_fingerprint": _sha256(
            raw_evidence.get("snapshot_fingerprint"),
            "snapshot_fingerprint",
        ),
        "ui_evidence_fingerprint": _sha256(
            raw_evidence.get("ui_evidence_fingerprint"),
            "ui_evidence_fingerprint",
        ),
        "source_observation_fingerprint": _sha256(
            raw_evidence.get("source_observation_fingerprint"),
            "source_observation_fingerprint",
        ),
        "workflow": _normalize_workflow_provenance(
            raw_evidence.get("workflow")
        ),
        "pairing_method": _choice(
            raw_evidence.get("pairing_method"),
            MAPPING_CANDIDATE_PAIRINGS,
            "pairing_method",
        ),
        "evidence_strength": _choice(
            raw_evidence.get("evidence_strength"),
            MAPPING_CANDIDATE_STRENGTHS,
            "evidence_strength",
        ),
        "pre_mutation": raw_evidence.get("pre_mutation"),
        "observed_at": _utc_datetime(
            raw_evidence.get("observed_at"),
            "observed_at",
        ).isoformat(),
    }
    if evidence["pre_mutation"] is not True:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_requires_pre_mutation_evidence"
        )
    _require_candidate_disposition(candidate, evidence)
    _require_persisted_pairing_invariants(candidate, evidence["pairing_method"])
    if (
        mapping["resolution"]
        not in {"exact", "compatible_exact_revision"}
        and candidate["status"] == "ready_for_review"
    ):
        raise PlayerSaveMappingCandidateError(
            "compatible_mapping_candidate_cannot_be_ready"
        )
    authority = _exact_mapping(
        record.get("authority"),
        {
            "disposition",
            "mapping_promotion",
            "runtime_reads_receipt",
            "authorizes_ui_suppression",
            "authorizes_input",
            "authorizes_repair",
            "changes_configuration",
            "changes_strategy",
            "self_promotes",
        },
        "authority",
    )
    expected_authority = {
        "disposition": "candidate_only",
        "mapping_promotion": "explicit_reviewed_repository_change",
        "runtime_reads_receipt": False,
        "authorizes_ui_suppression": False,
        "authorizes_input": False,
        "authorizes_repair": False,
        "changes_configuration": False,
        "changes_strategy": False,
        "self_promotes": False,
    }
    if dict(authority) != expected_authority:
        raise PlayerSaveMappingCandidateError("mapping_candidate_authority_invalid")
    normalized = {
        "schema_version": MAPPING_CANDIDATE_SCHEMA_VERSION,
        "schema_id": MAPPING_CANDIDATE_SCHEMA_ID,
        "record_type": "mapping_candidate",
        "record_id": record_id,
        "recorded_at": recorded_at.isoformat(),
        "mapping": mapping,
        "candidate": candidate,
        "evidence": evidence,
        "authority": expected_authority,
    }
    expected_record_id = _candidate_record_id(
        mapping,
        candidate,
        evidence,
        expected_authority,
    )
    if record_id != expected_record_id:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_record_id_mismatch"
        )
    if normalized != dict(record):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_not_canonical"
        )
    return json.loads(json.dumps(normalized))


def _candidate_record_id(
    mapping: Mapping[str, Any],
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> str:
    """Fingerprint only the canonical persisted semantics, not write time."""

    return fingerprint_json(
        {
            "schema_version": MAPPING_CANDIDATE_SCHEMA_VERSION,
            "schema_id": MAPPING_CANDIDATE_SCHEMA_ID,
            "record_type": "mapping_candidate",
            "mapping": mapping,
            "candidate": candidate,
            "evidence": evidence,
            "authority": authority,
        }
    )


def _require_check_value_kind(check_id: str, value_kind: str) -> None:
    if value_kind not in _CHECK_VALUE_KINDS.get(check_id, frozenset()):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_check_value_kind_mismatch"
        )


def _require_pending_pairing_invariants(
    check_id: str,
    candidate: Mapping[str, Any],
) -> None:
    """Reject pairings that do not match their authoritative mapping owner."""

    kind = candidate["value_kind"]
    pairing = candidate["pairing_method"]
    scope = candidate["scope"]
    policy = candidate["observation_count_policy"]
    expected = {
        "battle_history_killed_by": (
            "battle_history_killed_by_id",
            "exact_locator",
        ),
        "guardian_chips": ("guardian_chip_id", "singleton_remainder"),
        "orb_distance": ("orb_distance_calibration", "calibration_sample"),
        "perk_auto_pick_order": ("perk_id", "exact_locator"),
        "perk_bans": ("perk_id", "singleton_remainder"),
        "perk_first_choice": ("perk_id", "exact_locator"),
        "target_priority": ("target_priority_id", "exact_locator"),
        "tournament_league": ("tournament_league_id", "exact_locator"),
    }.get(check_id)
    if check_id == "modules":
        expected = (
            ("module_assist_type", "singleton_remainder")
            if kind == "module_assist_type"
            else ("module_info_index", "exact_locator")
        )
    if expected != (kind, pairing):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_pairing_kind_mismatch"
        )
    if policy == "minimum" and check_id != "perk_auto_pick_order":
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_count_policy_invalid"
        )
    if check_id == "modules" and kind == "module_info_index":
        if set(scope) != {"slot_key", "family", "role"}:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_scope_invalid"
            )
        if scope["role"] not in {"primary", "assist"}:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_scope_invalid"
            )
    elif check_id == "modules":
        if scope != {"role": "assist"}:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_assist_scope_invalid"
            )
    elif check_id == "orb_distance":
        if scope != {"field": candidate["locator"]}:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_orb_scope_invalid"
            )
    elif scope:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_unexpected_scope"
        )


def _require_persisted_pairing_invariants(
    candidate: Mapping[str, Any],
    pairing_method: str,
) -> None:
    check_id = candidate["check_id"]
    kind = candidate["value_kind"]
    scope = candidate["scope"]
    expected_pairing = {
        "battle_history_killed_by": "exact_locator",
        "guardian_chips": "singleton_remainder",
        "orb_distance": "calibration_sample",
        "perk_auto_pick_order": "exact_locator",
        "perk_bans": "singleton_remainder",
        "perk_first_choice": "exact_locator",
        "target_priority": "exact_locator",
        "tournament_league": "exact_locator",
    }.get(check_id)
    if check_id == "modules":
        expected_pairing = (
            "singleton_remainder"
            if kind == "module_assist_type"
            else "exact_locator"
        )
    if pairing_method != expected_pairing:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_pairing_kind_mismatch"
        )
    if check_id == "modules":
        if (
            set(scope) != {"slot_key", "family", "role"}
            or scope["role"] not in {"primary", "assist"}
        ):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_scope_invalid"
            )
        if kind == "module_assist_type" and scope["role"] != "assist":
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_assist_scope_invalid"
            )
    elif check_id == "orb_distance":
        if scope != {"field": candidate["locator"]}:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_orb_scope_invalid"
            )
    elif scope:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_unexpected_scope"
        )


def _observation_count_matches(item: Mapping[str, Any], actual: int) -> bool:
    expected = item["expected_observation_count"]
    if item["observation_count_policy"] == "minimum":
        return actual >= expected
    return actual == expected


def _require_candidate_disposition(
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    status = candidate["status"]
    strength = evidence["evidence_strength"]
    semantic = candidate["semantic_value"]
    expected_strength = {
        "ready_for_review": "deterministic",
        "needs_more_evidence": "supporting",
        "ambiguous": "insufficient",
        "conflicts_existing_mapping": "conflicting",
    }[status]
    if strength != expected_strength:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_disposition_mismatch"
        )
    if status == "ambiguous":
        if semantic is not None:
            raise PlayerSaveMappingCandidateError(
                "ambiguous_mapping_candidate_has_semantic_value"
            )
        return
    if semantic is None or semantic not in candidate["observed_semantic_values"]:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_semantic_evidence_mismatch"
        )


def _resolved_disposition(
    item: Mapping[str, Any],
    semantic: str,
) -> tuple[str, str, str]:
    known_raw_semantic = item.get("known_raw_semantic_value")
    if known_raw_semantic is not None:
        if semantic != known_raw_semantic:
            return (
                "conflicts_existing_mapping",
                "conflicting",
                "raw discriminator conflicts with its existing global semantic mapping",
            )
        if item["minimum_evidence_count"] > 1:
            return (
                "needs_more_evidence",
                "supporting",
                "deterministic repeated-pair placement needs an additional independent observation",
            )
        return (
            "ready_for_review",
            "deterministic",
            "existing global discriminator pair was observed in a new exact module scope",
        )
    if semantic in set(item["known_semantic_values"]):
        return (
            "conflicts_existing_mapping",
            "conflicting",
            "raw discriminator pairs with a semantic already owned by another mapping entry",
        )
    if item["minimum_evidence_count"] > 1:
        return (
            "needs_more_evidence",
            "supporting",
            "deterministic pairing needs an additional independent observation",
        )
    return (
        "ready_for_review",
        "deterministic",
        "unique exact-boundary pre-mutation pairing is ready for operator review",
    )


def _ui_observation(
    check_id: str,
    raw: Mapping[str, Any],
) -> tuple[dict[str, str], tuple[str, ...], dict[str, dict[str, str]]]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "canonical_values",
        "locator_values",
        "locator_scopes",
        "complete",
        "pre_mutation",
        "observed_at",
        "source_observation_fingerprint",
    }:
        raise PlayerSaveMappingCandidateError("ui_candidate_evidence_changed_shape")
    if raw.get("complete") is not True or raw.get("pre_mutation") is not True:
        return {}, (), {}
    _utc_datetime(raw.get("observed_at"), "ui_evidence_observed_at")
    _sha256(
        raw.get("source_observation_fingerprint"),
        "source_observation_fingerprint",
    )
    values = _semantic_values(
        raw.get("canonical_values"),
        "canonical_values",
        allow_duplicates=(check_id in {"modules", "orb_distance"}),
    )
    locators = _locator_values(raw.get("locator_values"), "locator_values")
    raw_scopes = raw.get("locator_scopes")
    if not isinstance(raw_scopes, Mapping):
        raise PlayerSaveMappingCandidateError("locator_scopes_invalid")
    scopes = {
        _safe_id(locator, "scope_locator"): _scope(scope)
        for locator, scope in raw_scopes.items()
    }
    if set(scopes) - set(locators):
        raise PlayerSaveMappingCandidateError("locator_scopes_unpaired")
    if check_id == "modules" and len(locators) != 8:
        return {}, (), {}
    return locators, values, scopes


def _normalize_pending(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "value_kind",
        "raw_discriminator",
        "pairing_method",
        "locator",
        "expected_observation_count",
        "observation_count_policy",
        "minimum_evidence_count",
        "known_semantic_values",
        "known_raw_semantic_value",
        "peer_semantic_values",
        "peer_locator_values",
        "scope",
    }:
        raise PlayerSaveMappingCandidateError("pending_candidate_changed_shape")
    discriminator = raw.get("raw_discriminator")
    if not isinstance(discriminator, Mapping) or set(discriminator) != {
        "kind",
        "value",
    }:
        raise PlayerSaveMappingCandidateError("raw_discriminator_changed_shape")
    return pending_mapping_candidate(
        value_kind=raw.get("value_kind"),
        raw_value=discriminator.get("value"),
        pairing_method=raw.get("pairing_method"),
        locator=raw.get("locator"),
        expected_observation_count=raw.get("expected_observation_count"),
        observation_count_policy=raw.get("observation_count_policy"),
        minimum_evidence_count=raw.get("minimum_evidence_count"),
        known_semantic_values=raw.get("known_semantic_values"),
        known_raw_semantic_value=raw.get("known_raw_semantic_value"),
        peer_semantic_values=raw.get("peer_semantic_values"),
        peer_locator_values=raw.get("peer_locator_values"),
        scope=raw.get("scope"),
    )


def _normalize_resolved(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "value_kind",
        "raw_discriminator",
        "pairing_method",
        "locator",
        "expected_observation_count",
        "observation_count_policy",
        "minimum_evidence_count",
        "known_semantic_values",
        "known_raw_semantic_value",
        "peer_semantic_values",
        "peer_locator_values",
        "scope",
        "semantic_value",
        "observed_semantic_values",
        "status",
        "evidence_strength",
        "reason",
    }:
        raise PlayerSaveMappingCandidateError("resolved_candidate_changed_shape")
    pending = _normalize_pending(
        {key: value for key, value in raw.items() if key not in {
            "semantic_value",
            "observed_semantic_values",
            "status",
            "evidence_strength",
            "reason",
        }}
    )
    return {
        **pending,
        "semantic_value": _optional_semantic(
            raw.get("semantic_value"),
            "semantic_value",
        ),
        "observed_semantic_values": list(
            _semantic_values(
                raw.get("observed_semantic_values"),
                "observed_semantic_values",
                allow_duplicates=(
                    pending["value_kind"]
                    in {
                        "module_info_index",
                        "module_assist_type",
                        "orb_distance_calibration",
                    }
                ),
            )
        ),
        "status": _choice(
            raw.get("status"),
            MAPPING_CANDIDATE_STATUSES,
            "candidate_status",
        ),
        "evidence_strength": _choice(
            raw.get("evidence_strength"),
            MAPPING_CANDIDATE_STRENGTHS,
            "evidence_strength",
        ),
        "reason": _bounded_text(raw.get("reason"), 512, "reason"),
    }


def _normalize_mapping(raw: object) -> dict[str, Any]:
    mapping = _exact_mapping(
        raw,
        {
            "mapping_id",
            "data_version",
            "game_version",
            "root_class",
            "resolution",
            "authority_mapping_id",
            "structural_mapping_id",
            "canonical_dependency_fingerprint",
        },
        "mapping",
    )
    normalized = {
        "mapping_id": _safe_id(mapping.get("mapping_id"), "mapping_id"),
        "data_version": _nonnegative_int(
            mapping.get("data_version"),
            "data_version",
        ),
        "game_version": _nonnegative_int(
            mapping.get("game_version"),
            "game_version",
        ),
        "root_class": _bounded_text(
            mapping.get("root_class"),
            256,
            "root_class",
        ),
        "resolution": _choice(
            mapping.get("resolution"),
            MAPPING_CANDIDATE_MAPPING_RESOLUTIONS,
            "mapping_resolution",
        ),
        "authority_mapping_id": _safe_id(
            mapping.get("authority_mapping_id"),
            "authority_mapping_id",
        ),
        "structural_mapping_id": _safe_id(
            mapping.get("structural_mapping_id"),
            "structural_mapping_id",
        ),
        "canonical_dependency_fingerprint": _sha256(
            mapping.get("canonical_dependency_fingerprint"),
            "canonical_dependency_fingerprint",
        ),
    }
    if normalized["resolution"] == "exact" and not (
        normalized["mapping_id"]
        == normalized["authority_mapping_id"]
        == normalized["structural_mapping_id"]
    ):
        raise PlayerSaveMappingCandidateError(
            "exact_mapping_candidate_authority_mismatch"
        )
    if (
        normalized["resolution"] == "compatible_exact_revision"
        and (
            normalized["mapping_id"]
            != normalized["structural_mapping_id"]
            or normalized["authority_mapping_id"]
            == normalized["structural_mapping_id"]
        )
    ):
        raise PlayerSaveMappingCandidateError(
            "exact_revision_candidate_structural_mismatch"
        )
    return normalized


def _normalize_workflow_provenance(raw: object) -> dict[str, Any]:
    workflow = _exact_mapping(
        raw,
        {
            "capture_request_id",
            "inspection_request_id",
            "runtime_session_fingerprint",
            "pid",
            "target_generation_fingerprint",
            "activity_scope_fingerprint",
            "game_state",
            "active_round_identity_fingerprint",
            "boundary_fingerprint",
        },
        "workflow",
    )
    game_state = _choice(
        workflow.get("game_state"),
        frozenset(
            {
                "home_new_battle",
                "active_battle",
                "terminal_game_over",
                "terminal_tournament_results",
            }
        ),
        "game_state",
    )
    active_round = workflow.get("active_round_identity_fingerprint")
    if game_state != "home_new_battle":
        active_round = _sha256(active_round, "active_round_identity_fingerprint")
    elif active_round is not None:
        raise PlayerSaveMappingCandidateError(
            "home_candidate_cannot_bind_active_round"
        )
    return {
        "capture_request_id": _safe_id(
            workflow.get("capture_request_id"),
            "capture_request_id",
        ),
        "inspection_request_id": _safe_id(
            workflow.get("inspection_request_id"),
            "inspection_request_id",
        ),
        "runtime_session_fingerprint": _sha256(
            workflow.get("runtime_session_fingerprint"),
            "runtime_session_fingerprint",
        ),
        "pid": _positive_int(workflow.get("pid"), "pid"),
        "target_generation_fingerprint": _sha256(
            workflow.get("target_generation_fingerprint"),
            "target_generation_fingerprint",
        ),
        "activity_scope_fingerprint": _sha256(
            workflow.get("activity_scope_fingerprint"),
            "activity_scope_fingerprint",
        ),
        "game_state": game_state,
        "active_round_identity_fingerprint": active_round,
        "boundary_fingerprint": _sha256(
            workflow.get("boundary_fingerprint"),
            "boundary_fingerprint",
        ),
    }


def _proposal_operation(
    candidate: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> dict[str, Any]:
    kind = candidate["value_kind"]
    raw_value = candidate["raw_discriminator"]["value"]
    semantic = candidate["semantic_value"]
    paths = {
        "perk_id": "/perk_ids",
        "guardian_chip_id": "/guardian_chip_ids",
        "target_priority_id": "/target_priority_ids",
    }
    if kind in paths:
        owner = mapping.get(paths[kind].removeprefix("/"))
        if not isinstance(owner, Mapping):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_owner_missing"
            )
        key = str(raw_value)
        if key in owner or semantic in set(owner.values()):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_conflicts_current_file"
            )
        return {
            "op": "add",
            "path": f"{paths[kind]}/{key}",
            "value": semantic,
        }
    if kind == "battle_history_killed_by_id":
        history = (mapping.get("runtime_save") or {}).get("battle_history")
        owner = (
            history.get("killed_by_ids")
            if isinstance(history, Mapping)
            else None
        )
        key = str(raw_value)
        if not isinstance(owner, Mapping):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_owner_missing"
            )
        if key in owner or semantic in set(owner.values()):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_conflicts_current_file"
            )
        return {
            "op": "add",
            "path": f"/runtime_save/battle_history/killed_by_ids/{key}",
            "value": semantic,
        }
    if kind == "module_info_index":
        scope = candidate["scope"]
        role = scope.get("role")
        module_loadout = mapping.get("module_loadout") or {}
        slots = module_loadout.get(role)
        if role not in {"primary", "assist"} or not isinstance(slots, list):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_owner_missing"
            )
        slot_index = next(
            (
                index
                for index, item in enumerate(slots)
                if isinstance(item, Mapping)
                and item.get("slot_key") == scope.get("slot_key")
            ),
            None,
        )
        if slot_index is None:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_slot_missing"
            )
        all_values = [
            value
            for slot_role in ("primary", "assist")
            for slot in module_loadout.get(slot_role, ())
            if isinstance(slot, Mapping)
            for value in slot.get("values", ())
            if isinstance(value, Mapping)
        ]
        raw_conflict = any(
            value.get("info_index") == raw_value
            and value.get("name") != semantic
            for value in all_values
        )
        semantic_conflict = any(
            value.get("name") == semantic
            and value.get("info_index") != raw_value
            for value in all_values
        )
        if raw_conflict or semantic_conflict:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_conflicts_current_file"
            )
        target_values = slots[slot_index].get("values")
        if not isinstance(target_values, list):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_owner_missing"
            )
        if any(
            isinstance(value, Mapping)
            and value.get("info_index") == raw_value
            and value.get("name") == semantic
            for value in target_values
        ):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_already_integrated"
            )
        return {
            "op": "add",
            "path": f"/module_loadout/{role}/{slot_index}/values/-",
            "value": {"info_index": raw_value, "name": semantic},
        }
    if kind == "module_assist_type":
        module_loadout = mapping.get("module_loadout") or {}
        assist = module_loadout.get("assist")
        if not isinstance(assist, list):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_owner_missing"
            )
        if any(
            isinstance(item, Mapping) and item.get("type") == raw_value
            for item in assist
        ):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_proposal_conflicts_current_file"
            )
        matches = [
            (index, item)
            for index, item in enumerate(assist)
            if isinstance(item, Mapping)
            and item.get("family") == semantic
            and item.get("slot_key") == candidate["scope"].get("slot_key")
        ]
        if len(matches) != 1:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_module_slot_missing"
            )
        slot_index, _item = matches[0]
        return {
            "op": "replace",
            "path": f"/module_loadout/assist/{slot_index}/type",
            "value": raw_value,
        }
    raise PlayerSaveMappingCandidateError(
        "mapping_candidate_kind_has_no_authoritative_patch_owner"
    )


def _raw_discriminator(
    kind: str,
    raw: object,
    *,
    already_wrapped: bool = False,
) -> dict[str, Any]:
    discriminator_kind = (
        "finite_number" if kind == "orb_distance_calibration" else "integer_id"
    )
    if already_wrapped:
        wrapped = _exact_mapping(raw, {"kind", "value"}, "raw_discriminator")
        if wrapped.get("kind") != discriminator_kind:
            raise PlayerSaveMappingCandidateError(
                "raw_discriminator_kind_invalid"
            )
        raw = wrapped.get("value")
    if kind == "orb_distance_calibration":
        # Calibration candidates still retain only one finite primitive; raw
        # field contents or save objects are never accepted.
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PlayerSaveMappingCandidateError("raw_discriminator_invalid")
        if not (-1_000_000 <= float(raw) <= 1_000_000):
            raise PlayerSaveMappingCandidateError("raw_discriminator_invalid")
        value: int | float = raw
    else:
        value = _nonnegative_int(raw, "raw_discriminator")
        if value > 9_223_372_036_854_775_807:
            raise PlayerSaveMappingCandidateError("raw_discriminator_invalid")
    return {"kind": discriminator_kind, "value": value}


def _pending_identity(item: Mapping[str, Any]) -> tuple[str, str]:
    return (
        item["value_kind"],
        json.dumps(item["raw_discriminator"], sort_keys=True),
    )


def _semantic_values_for_scope(
    item: Mapping[str, Any],
    default: tuple[str, ...],
    scopes: Mapping[str, Mapping[str, str]],
) -> tuple[str, ...]:
    if item["value_kind"] != "module_assist_type":
        return default
    values = tuple(
        sorted(
            scope["family"]
            for scope in scopes.values()
            if scope.get("role") == "assist" and scope.get("family")
        )
    )
    return values if len(values) == len(set(values)) else ()


def _render_record(record: Mapping[str, Any]) -> bytes:
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
    if len(rendered) > MAX_MAPPING_CANDIDATE_RECEIPT_BYTES:
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_receipt_exceeds_size_bound"
        )
    return rendered


def _read_locked_records(descriptor: int) -> list[dict[str, Any]]:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    payload = b"".join(chunks)
    if not payload:
        return []
    if not payload.endswith(b"\n"):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_receipt_partial_line"
        )
    records: list[dict[str, Any]] = []
    for line in payload.splitlines():
        if len(line) > MAX_MAPPING_CANDIDATE_RECEIPT_BYTES:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_receipt_exceeds_size_bound"
            )
        try:
            raw = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_receipt_invalid_json"
            ) from exc
        records.append(validate_mapping_candidate_record(raw))
    return records


def _recover_partial_tail(descriptor: int) -> None:
    """Discard only an unterminated crash tail, preserving durable records."""

    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    payload = b"".join(chunks)
    if not payload or payload.endswith(b"\n"):
        return
    last_complete = payload.rfind(b"\n") + 1
    os.ftruncate(descriptor, last_complete)
    os.fsync(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _scope(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping) or set(raw) - _ALLOWED_SCOPE_KEYS:
        raise PlayerSaveMappingCandidateError("candidate_scope_invalid")
    return {
        _safe_code(key, "scope_key"): _safe_id(value, f"scope_{key}")
        for key, value in sorted(raw.items())
    }


def _locator_values(raw: object, label: str) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return {
        _safe_id(key, f"{label}_key"): _semantic(value, f"{label}_value")
        for key, value in sorted(raw.items(), key=lambda item: str(item[0]))
    }


def _semantic_values(
    raw: object,
    label: str,
    *,
    allow_duplicates: bool = False,
) -> tuple[str, ...]:
    if not _is_sequence(raw):
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    values = tuple(_semantic(value, label) for value in raw)
    if not allow_duplicates and len(values) != len(set(values)):
        raise PlayerSaveMappingCandidateError(f"{label}_duplicated")
    return values


def _optional_semantic(raw: object, label: str) -> Optional[str]:
    return None if raw is None else _semantic(raw, label)


def _semantic(raw: object, label: str) -> str:
    if not isinstance(raw, str):
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    value = raw.strip()
    if (
        not value
        or len(value) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value


def _bounded_text(raw: object, maximum: int, label: str) -> str:
    if not isinstance(raw, str):
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    value = raw.strip()
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value


def _safe_code(raw: object, label: str) -> str:
    value = str(raw or "")
    if _SAFE_CODE_RE.fullmatch(value) is None:
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value


def _safe_id(raw: object, label: str) -> str:
    value = str(raw or "")
    if _SAFE_ID_RE.fullmatch(value) is None:
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value


def _sha256(raw: object, label: str) -> str:
    value = str(raw or "")
    if _SHA256_RE.fullmatch(value) is None:
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value


def _choice(raw: object, allowed: frozenset[str], label: str) -> str:
    value = str(raw or "")
    if value not in allowed:
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value


def _nonnegative_int(raw: object, label: str) -> int:
    if type(raw) is not int or raw < 0:
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return raw


def _positive_int(raw: object, label: str) -> int:
    value = _nonnegative_int(raw, label)
    if value <= 0:
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value


def _utc_datetime(raw: object, label: str) -> datetime:
    if isinstance(raw, datetime):
        value = raw
    else:
        try:
            value = datetime.fromisoformat(str(raw))
        except ValueError as exc:
            raise PlayerSaveMappingCandidateError(f"{label}_invalid") from exc
    if value.tzinfo is None:
        raise PlayerSaveMappingCandidateError(f"{label}_invalid")
    return value.astimezone(timezone.utc)


def _exact_mapping(raw: object, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != keys:
        raise PlayerSaveMappingCandidateError(f"mapping_candidate_{label}_changed")
    return raw


def _is_sequence(raw: object) -> bool:
    return isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray))


__all__ = [
    "AppendOnlyMappingCandidateStore",
    "AppendOnlyMappingCandidateWriter",
    "DEFAULT_MAPPING_CANDIDATE_RECEIPT_PATH",
    "MAPPING_CANDIDATE_CHECKS",
    "MAPPING_CANDIDATE_SCHEMA_ID",
    "MAPPING_CANDIDATE_SCHEMA_VERSION",
    "MAPPING_CANDIDATE_STATUSES",
    "MAPPING_CANDIDATE_VALUE_KINDS",
    "PlayerSaveMappingCandidateError",
    "build_mapping_candidate_context",
    "build_mapping_candidate_record",
    "canonical_mapping_set_fingerprint",
    "fingerprint_json",
    "mapping_candidate_record_status",
    "mapping_candidate_review_status",
    "pending_mapping_candidate",
    "proposed_mapping_patch",
    "reconcile_mapping_candidate_resolutions",
    "resolve_mapping_candidates",
    "validate_mapping_candidate_context",
    "validate_mapping_candidate_record",
    "validate_mapping_candidate_result",
]
