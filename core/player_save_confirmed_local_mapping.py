"""Durable, exact-version local player-save mapping confirmations.

Candidate receipts remain review evidence.  This module owns the separate,
ignored local authority file that may project one narrowly supported mapping
on a *later* fresh decode.  Runtime never edits tracked canonical mappings.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Optional

from core.player_save_mapping_candidates import (
    PlayerSaveMappingCandidateError,
    fingerprint_json,
    validate_mapping_candidate_record,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIRMED_LOCAL_MAPPING_DIR = (
    ROOT / "config" / "player_save_versions" / "local"
)
CONFIRMED_LOCAL_MAPPING_SCHEMA_VERSION = 1
CONFIRMED_LOCAL_MAPPING_SCHEMA_ID = (
    "thetower.player_save_confirmed_local_mapping.v1"
)
MAX_CONFIRMED_LOCAL_MAPPING_BYTES = 1024 * 1024
MAX_CONFIRMED_LOCAL_MAPPING_EVENTS = 512

_FILE_RE = re.compile(r"data_([0-9]+)_game_([0-9]+)\.confirmed\.json")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,191}")
_SUPPORTED_CHECK = "modules"
_SUPPORTED_VALUE_KIND = "module_info_index"
_SUPPORTED_RESOLUTIONS = frozenset(
    {"exact", "compatible_exact_revision"}
)


class ConfirmedLocalMappingError(ValueError):
    """A local mapping authority document or transition was unsafe."""


class ConfirmedLocalMappingStore:
    """Atomic append-only accept/revoke event documents, one per save version."""

    def __init__(
        self,
        directory: Path | str = DEFAULT_CONFIRMED_LOCAL_MAPPING_DIR,
    ) -> None:
        self.directory = Path(directory)

    def path_for(self, data_version: int, game_version: int) -> Path:
        data = _nonnegative_int(data_version, "data_version")
        game = _nonnegative_int(game_version, "game_version")
        return self.directory / f"data_{data}_game_{game}.confirmed.json"

    def accept_candidate(
        self,
        record: Mapping[str, Any],
        *,
        recorded_at: object = None,
    ) -> dict[str, Any]:
        """Accept one deterministic module value for later fresh decodes.

        This is idempotent for the same candidate and dependency.  A different
        semantic claim for an already-active slot/value is rejected.
        """

        try:
            candidate_record = validate_mapping_candidate_record(record)
        except PlayerSaveMappingCandidateError as exc:
            raise ConfirmedLocalMappingError(
                "confirmed_local_candidate_invalid"
            ) from exc
        mapping = candidate_record["mapping"]
        candidate = candidate_record["candidate"]
        evidence = candidate_record["evidence"]
        if mapping["resolution"] not in _SUPPORTED_RESOLUTIONS:
            raise ConfirmedLocalMappingError(
                "confirmed_local_requires_exact_version"
            )
        if not (
            candidate["check_id"] == _SUPPORTED_CHECK
            and candidate["value_kind"] == _SUPPORTED_VALUE_KIND
            and candidate["status"] == "ready_for_review"
            and candidate["semantic_value"] is not None
            and evidence["evidence_strength"] == "deterministic"
            and evidence["pre_mutation"] is True
        ):
            raise ConfirmedLocalMappingError(
                "confirmed_local_candidate_not_supported"
            )
        scope = _module_scope(candidate["scope"])
        raw_value = _nonnegative_int(
            candidate["raw_discriminator"]["value"],
            "raw_value",
        )
        identity = {
            "mapping_id": _safe_id(mapping["mapping_id"], "mapping_id"),
            "data_version": _nonnegative_int(
                mapping["data_version"], "data_version"
            ),
            "game_version": _nonnegative_int(
                mapping["game_version"], "game_version"
            ),
            "root_class": _bounded_text(
                mapping["root_class"], 256, "root_class"
            ),
        }
        dependency = _sha256(
            mapping["canonical_dependency_fingerprint"],
            "dependency_fingerprint",
        )
        event_payload = {
            "event_type": "accept",
            "candidate_record_id": candidate_record["record_id"],
            "mapping_resolution": mapping["resolution"],
            "authority_mapping_id": mapping["authority_mapping_id"],
            "structural_mapping_id": mapping["structural_mapping_id"],
            "check_id": _SUPPORTED_CHECK,
            "value_kind": _SUPPORTED_VALUE_KIND,
            "raw_value": raw_value,
            "semantic_value": candidate["semantic_value"],
            "scope": scope,
            "dependency_fingerprint": dependency,
            "snapshot_fingerprint": evidence["snapshot_fingerprint"],
            "ui_evidence_fingerprint": evidence["ui_evidence_fingerprint"],
            "source_observation_fingerprint": evidence[
                "source_observation_fingerprint"
            ],
        }
        event_id = fingerprint_json(
            {
                "schema_id": CONFIRMED_LOCAL_MAPPING_SCHEMA_ID,
                "identity": identity,
                "event": event_payload,
            }
        )
        event = {
            "event_id": event_id,
            "recorded_at": _utc_datetime(
                datetime.now(timezone.utc)
                if recorded_at is None
                else recorded_at,
                "recorded_at",
            ).isoformat(),
            **event_payload,
        }
        path = self.path_for(
            identity["data_version"], identity["game_version"]
        )

        def update(current: Optional[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
            document = current or _empty_document(identity)
            if document["identity"] != identity:
                raise ConfirmedLocalMappingError(
                    "confirmed_local_identity_changed"
                )
            for prior in document["events"]:
                if prior["event_id"] == event_id:
                    if prior != event:
                        # Write time is intentionally not part of the stable ID.
                        comparable = dict(prior)
                        comparable["recorded_at"] = event["recorded_at"]
                        if comparable != event:
                            raise ConfirmedLocalMappingError(
                                "confirmed_local_event_id_conflict"
                            )
                    return document, False
            active = active_confirmations(document)
            target = (scope["slot_key"], raw_value)
            for prior in active:
                if (
                    prior["raw_value"] == raw_value
                    and prior["semantic_value"] != event["semantic_value"]
                ):
                    raise ConfirmedLocalMappingError(
                        "confirmed_local_active_raw_conflict"
                    )
                if (
                    prior["semantic_value"] == event["semantic_value"]
                    and prior["raw_value"] != raw_value
                ):
                    raise ConfirmedLocalMappingError(
                        "confirmed_local_active_semantic_conflict"
                    )
                prior_target = (
                    prior["scope"]["slot_key"],
                    prior["raw_value"],
                )
                same_slot = prior["scope"]["slot_key"] == scope["slot_key"]
                if same_slot and prior["semantic_value"] == event["semantic_value"]:
                    if prior["raw_value"] != raw_value:
                        raise ConfirmedLocalMappingError(
                            "confirmed_local_active_raw_conflict"
                        )
                if prior_target != target:
                    continue
                if prior["semantic_value"] != event["semantic_value"]:
                    raise ConfirmedLocalMappingError(
                        "confirmed_local_active_semantic_conflict"
                    )
                if (
                    prior["dependency_fingerprint"]
                    == event["dependency_fingerprint"]
                    and prior["mapping_resolution"]
                    == event["mapping_resolution"]
                    and prior["authority_mapping_id"]
                    == event["authority_mapping_id"]
                    and prior["structural_mapping_id"]
                    == event["structural_mapping_id"]
                ):
                    return document, False
                same_observation = all(
                    prior[field] == event[field]
                    for field in (
                        "snapshot_fingerprint",
                        "ui_evidence_fingerprint",
                        "source_observation_fingerprint",
                    )
                )
                if (
                    prior["candidate_record_id"] == event["candidate_record_id"]
                    or same_observation
                ):
                    raise ConfirmedLocalMappingError(
                        "confirmed_local_reconfirmation_requires_fresh_evidence"
                    )
                _require_revocation_capacity(
                    event_count=len(document["events"]) + 2,
                    active_count=len(active),
                )
                revoke_payload = {
                    "event_type": "revoke",
                    "target_event_id": prior["event_id"],
                    "reason": "superseded_by_fresh_confirmation",
                }
                revoke_event = {
                    "event_id": fingerprint_json(
                        {
                            "schema_id": CONFIRMED_LOCAL_MAPPING_SCHEMA_ID,
                            "identity": identity,
                            "event": revoke_payload,
                        }
                    ),
                    "recorded_at": event["recorded_at"],
                    **revoke_payload,
                }
                return {
                    **document,
                    "generation": document["generation"] + 2,
                    "events": [
                        *document["events"],
                        revoke_event,
                        event,
                    ],
                }, True
            _require_revocation_capacity(
                event_count=len(document["events"]) + 1,
                active_count=len(active) + 1,
            )
            return {
                **document,
                "generation": document["generation"] + 1,
                "events": [*document["events"], event],
            }, True

        document, changed = self._update(path, update)
        return {
            "changed": changed,
            "event_id": event_id,
            "generation": document["generation"],
            "document_fingerprint": fingerprint_json(document),
            "path": path,
        }

    def revoke(
        self,
        *,
        data_version: int,
        game_version: int,
        target_event_id: str,
        reason: str,
        expected_generation: int,
        expected_document_fingerprint: str,
        recorded_at: object = None,
    ) -> dict[str, Any]:
        """Append one explicit revocation without deleting prior evidence."""

        path = self.path_for(data_version, game_version)
        target = _sha256(target_event_id, "target_event_id")
        normalized_reason = _bounded_text(reason, 512, "reason")
        expected_generation = _nonnegative_int(
            expected_generation,
            "expected_generation",
        )
        expected_document_fingerprint = _sha256(
            expected_document_fingerprint,
            "expected_document_fingerprint",
        )

        def update(current: Optional[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
            if current is None:
                raise ConfirmedLocalMappingError(
                    "confirmed_local_document_missing"
                )
            if (
                current["generation"] != expected_generation
                or fingerprint_json(current) != expected_document_fingerprint
            ):
                raise ConfirmedLocalMappingError(
                    "confirmed_local_revoke_compare_and_swap_failed"
                )
            accepts = {
                event["event_id"]
                for event in current["events"]
                if event["event_type"] == "accept"
            }
            if target not in accepts:
                raise ConfirmedLocalMappingError(
                    "confirmed_local_accept_event_missing"
                )
            payload = {
                "event_type": "revoke",
                "target_event_id": target,
                "reason": normalized_reason,
            }
            event_id = fingerprint_json(
                {
                    "schema_id": CONFIRMED_LOCAL_MAPPING_SCHEMA_ID,
                    "identity": current["identity"],
                    "event": payload,
                }
            )
            for event in current["events"]:
                if event["event_id"] == event_id:
                    return current, False
            if any(
                event["event_type"] == "revoke"
                and event["target_event_id"] == target
                for event in current["events"]
            ):
                return current, False
            if len(current["events"]) >= MAX_CONFIRMED_LOCAL_MAPPING_EVENTS:
                raise ConfirmedLocalMappingError(
                    "confirmed_local_event_limit_reached"
                )
            event = {
                "event_id": event_id,
                "recorded_at": _utc_datetime(
                    datetime.now(timezone.utc)
                    if recorded_at is None
                    else recorded_at,
                    "recorded_at",
                ).isoformat(),
                **payload,
            }
            return {
                **current,
                "generation": current["generation"] + 1,
                "events": [*current["events"], event],
            }, True

        document, changed = self._update(path, update)
        revocations = [
            event
            for event in document["events"]
            if event["event_type"] == "revoke"
            and event["target_event_id"] == target
        ]
        return {
            "changed": changed,
            "event_id": revocations[-1]["event_id"],
            "generation": document["generation"],
            "document_fingerprint": fingerprint_json(document),
            "path": path,
        }

    def load(self, data_version: int, game_version: int) -> Optional[dict[str, Any]]:
        """Read one exact-version document without a process cache."""

        return self._read_path(self.path_for(data_version, game_version))

    def list_documents(self) -> list[dict[str, Any]]:
        """Read every local document; malformed state fails explicitly."""

        try:
            if not self.directory.exists():
                return []
            paths = sorted(self.directory.glob("*.confirmed.json"))
        except OSError as exc:
            raise ConfirmedLocalMappingError(
                "confirmed_local_directory_unavailable"
            ) from exc
        documents: list[dict[str, Any]] = []
        for path in paths:
            match = _FILE_RE.fullmatch(path.name)
            if match is None:
                continue
            document = self._read_path(path)
            if document is None:
                continue
            identity = document["identity"]
            if (
                identity["data_version"] != int(match.group(1))
                or identity["game_version"] != int(match.group(2))
            ):
                raise ConfirmedLocalMappingError(
                    "confirmed_local_filename_identity_mismatch"
                )
            documents.append(document)
        return documents

    def _read_path(self, path: Path) -> Optional[dict[str, Any]]:
        descriptor = -1
        try:
            if not path.exists():
                return None
            lock_path = path.with_suffix(path.suffix + ".lock")
            descriptor = _open_lock(lock_path)
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            return _read_document(path)
        except ConfirmedLocalMappingError:
            raise
        except OSError as exc:
            raise ConfirmedLocalMappingError(
                "confirmed_local_document_unavailable"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _update(self, path: Path, update_fn):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        descriptor = _open_lock(lock_path)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            current = _read_document(path) if path.exists() else None
            updated, changed = update_fn(current)
            normalized = validate_confirmed_local_mapping_document(updated)
            if changed:
                _atomic_write(path, normalized)
            return normalized, changed
        finally:
            os.close(descriptor)


def active_confirmations(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return accepted events not superseded by a later revocation."""

    normalized = validate_confirmed_local_mapping_document(document)
    revoked = {
        event["target_event_id"]
        for event in normalized["events"]
        if event["event_type"] == "revoke"
    }
    return [
        dict(event)
        for event in normalized["events"]
        if event["event_type"] == "accept" and event["event_id"] not in revoked
    ]


def validate_confirmed_local_mapping_document(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version",
        "schema_id",
        "identity",
        "generation",
        "events",
    }:
        raise ConfirmedLocalMappingError("confirmed_local_document_changed_shape")
    if (
        raw.get("schema_version") != CONFIRMED_LOCAL_MAPPING_SCHEMA_VERSION
        or raw.get("schema_id") != CONFIRMED_LOCAL_MAPPING_SCHEMA_ID
    ):
        raise ConfirmedLocalMappingError("confirmed_local_schema_unsupported")
    identity_raw = raw.get("identity")
    if not isinstance(identity_raw, Mapping) or set(identity_raw) != {
        "mapping_id",
        "data_version",
        "game_version",
        "root_class",
    }:
        raise ConfirmedLocalMappingError("confirmed_local_identity_changed_shape")
    identity = {
        "mapping_id": _safe_id(identity_raw.get("mapping_id"), "mapping_id"),
        "data_version": _nonnegative_int(
            identity_raw.get("data_version"), "data_version"
        ),
        "game_version": _nonnegative_int(
            identity_raw.get("game_version"), "game_version"
        ),
        "root_class": _bounded_text(
            identity_raw.get("root_class"), 256, "root_class"
        ),
    }
    generation = _nonnegative_int(raw.get("generation"), "generation")
    raw_events = raw.get("events")
    if not isinstance(raw_events, list) or len(raw_events) > MAX_CONFIRMED_LOCAL_MAPPING_EVENTS:
        raise ConfirmedLocalMappingError("confirmed_local_events_invalid")
    events: list[dict[str, Any]] = []
    accept_ids: set[str] = set()
    event_ids: set[str] = set()
    revoked_ids: set[str] = set()
    for raw_event in raw_events:
        event = _validate_event(raw_event, identity)
        event_id = event["event_id"]
        if event_id in event_ids:
            raise ConfirmedLocalMappingError("confirmed_local_event_duplicated")
        event_ids.add(event_id)
        if event["event_type"] == "accept":
            accept_ids.add(event_id)
        else:
            target = event["target_event_id"]
            if target not in accept_ids or target in revoked_ids:
                raise ConfirmedLocalMappingError(
                    "confirmed_local_revocation_order_invalid"
                )
            revoked_ids.add(target)
        events.append(event)
    if generation != len(events):
        raise ConfirmedLocalMappingError("confirmed_local_generation_invalid")
    _validate_active_module_bijection(events)
    _require_revocation_capacity(
        event_count=len(events),
        active_count=len(
            [
                event
                for event in events
                if event["event_type"] == "accept"
                and event["event_id"] not in revoked_ids
            ]
        ),
    )
    normalized = {
        "schema_version": CONFIRMED_LOCAL_MAPPING_SCHEMA_VERSION,
        "schema_id": CONFIRMED_LOCAL_MAPPING_SCHEMA_ID,
        "identity": identity,
        "generation": generation,
        "events": events,
    }
    if normalized != dict(raw):
        raise ConfirmedLocalMappingError("confirmed_local_document_not_canonical")
    return json.loads(json.dumps(normalized))


def _validate_event(raw: object, identity: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConfirmedLocalMappingError("confirmed_local_event_invalid")
    event_type = raw.get("event_type")
    common = {"event_id", "recorded_at", "event_type"}
    if event_type == "accept":
        expected = common | {
            "candidate_record_id",
            "mapping_resolution",
            "authority_mapping_id",
            "structural_mapping_id",
            "check_id",
            "value_kind",
            "raw_value",
            "semantic_value",
            "scope",
            "dependency_fingerprint",
            "snapshot_fingerprint",
            "ui_evidence_fingerprint",
            "source_observation_fingerprint",
        }
        if set(raw) != expected:
            raise ConfirmedLocalMappingError(
                "confirmed_local_accept_changed_shape"
            )
        scope = _module_scope(raw.get("scope"))
        payload = {
            "event_type": "accept",
            "candidate_record_id": _sha256(
                raw.get("candidate_record_id"), "candidate_record_id"
            ),
            "mapping_resolution": _choice(
                raw.get("mapping_resolution"),
                _SUPPORTED_RESOLUTIONS,
                "mapping_resolution",
            ),
            "authority_mapping_id": _safe_id(
                raw.get("authority_mapping_id"), "authority_mapping_id"
            ),
            "structural_mapping_id": _safe_id(
                raw.get("structural_mapping_id"), "structural_mapping_id"
            ),
            "check_id": _choice(
                raw.get("check_id"), frozenset({_SUPPORTED_CHECK}), "check_id"
            ),
            "value_kind": _choice(
                raw.get("value_kind"),
                frozenset({_SUPPORTED_VALUE_KIND}),
                "value_kind",
            ),
            "raw_value": _nonnegative_int(raw.get("raw_value"), "raw_value"),
            "semantic_value": _bounded_text(
                raw.get("semantic_value"), 256, "semantic_value"
            ),
            "scope": scope,
            "dependency_fingerprint": _sha256(
                raw.get("dependency_fingerprint"), "dependency_fingerprint"
            ),
            "snapshot_fingerprint": _sha256(
                raw.get("snapshot_fingerprint"), "snapshot_fingerprint"
            ),
            "ui_evidence_fingerprint": _sha256(
                raw.get("ui_evidence_fingerprint"),
                "ui_evidence_fingerprint",
            ),
            "source_observation_fingerprint": _sha256(
                raw.get("source_observation_fingerprint"),
                "source_observation_fingerprint",
            ),
        }
    elif event_type == "revoke":
        expected = common | {"target_event_id", "reason"}
        if set(raw) != expected:
            raise ConfirmedLocalMappingError(
                "confirmed_local_revoke_changed_shape"
            )
        payload = {
            "event_type": "revoke",
            "target_event_id": _sha256(
                raw.get("target_event_id"), "target_event_id"
            ),
            "reason": _bounded_text(raw.get("reason"), 512, "reason"),
        }
    else:
        raise ConfirmedLocalMappingError("confirmed_local_event_type_invalid")
    event_id = _sha256(raw.get("event_id"), "event_id")
    expected_id = fingerprint_json(
        {
            "schema_id": CONFIRMED_LOCAL_MAPPING_SCHEMA_ID,
            "identity": dict(identity),
            "event": payload,
        }
    )
    if event_id != expected_id:
        raise ConfirmedLocalMappingError("confirmed_local_event_id_mismatch")
    normalized = {
        "event_id": event_id,
        "recorded_at": _utc_datetime(
            raw.get("recorded_at"), "recorded_at"
        ).isoformat(),
        **payload,
    }
    if normalized != dict(raw):
        raise ConfirmedLocalMappingError("confirmed_local_event_not_canonical")
    return normalized


def _empty_document(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CONFIRMED_LOCAL_MAPPING_SCHEMA_VERSION,
        "schema_id": CONFIRMED_LOCAL_MAPPING_SCHEMA_ID,
        "identity": dict(identity),
        "generation": 0,
        "events": [],
    }


def _validate_active_module_bijection(
    events: list[dict[str, Any]],
) -> None:
    """Require one global raw/name bijection while allowing repeated scopes."""

    revoked = {
        event["target_event_id"]
        for event in events
        if event["event_type"] == "revoke"
    }
    active = [
        event
        for event in events
        if event["event_type"] == "accept" and event["event_id"] not in revoked
    ]
    raw_to_name: dict[int, str] = {}
    name_to_raw: dict[str, int] = {}
    slot_to_pair: dict[str, tuple[int, str]] = {}
    for event in active:
        raw_value = event["raw_value"]
        semantic = event["semantic_value"]
        slot_key = event["scope"]["slot_key"]
        if raw_value in raw_to_name and raw_to_name[raw_value] != semantic:
            raise ConfirmedLocalMappingError(
                "confirmed_local_active_raw_conflict"
            )
        if semantic in name_to_raw and name_to_raw[semantic] != raw_value:
            raise ConfirmedLocalMappingError(
                "confirmed_local_active_semantic_conflict"
            )
        pair = (raw_value, semantic)
        if slot_key in slot_to_pair and slot_to_pair[slot_key] != pair:
            raise ConfirmedLocalMappingError(
                "confirmed_local_active_slot_conflict"
            )
        raw_to_name[raw_value] = semantic
        name_to_raw[semantic] = raw_value
        slot_to_pair[slot_key] = pair


def _require_revocation_capacity(
    *,
    event_count: int,
    active_count: int,
) -> None:
    if event_count + active_count > MAX_CONFIRMED_LOCAL_MAPPING_EVENTS:
        raise ConfirmedLocalMappingError(
            "confirmed_local_revocation_capacity_exhausted"
        )


def _read_document(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ConfirmedLocalMappingError(
            "confirmed_local_document_unavailable"
        ) from exc
    if size <= 0 or size > MAX_CONFIRMED_LOCAL_MAPPING_BYTES:
        raise ConfirmedLocalMappingError("confirmed_local_document_size_invalid")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfirmedLocalMappingError(
            "confirmed_local_document_invalid"
        ) from exc
    return validate_confirmed_local_mapping_document(raw)


def _atomic_write(path: Path, document: Mapping[str, Any]) -> None:
    rendered = (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    if len(rendered) > MAX_CONFIRMED_LOCAL_MAPPING_BYTES:
        raise ConfirmedLocalMappingError("confirmed_local_document_too_large")
    try:
        previous = path.read_bytes() if path.exists() else None
    except OSError as exc:
        raise ConfirmedLocalMappingError(
            "confirmed_local_document_unavailable"
        ) from exc
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        os.fchmod(descriptor, 0o600)
        written = os.write(descriptor, rendered)
        if written != len(rendered):
            raise OSError("confirmed local mapping write was partial")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
    except OSError as exc:
        if replaced:
            try:
                _restore_previous_document(path, previous)
            except OSError as rollback_exc:
                raise ConfirmedLocalMappingError(
                    "confirmed_local_commit_state_uncertain"
                ) from rollback_exc
        raise ConfirmedLocalMappingError(
            "confirmed_local_document_commit_failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore_previous_document(path: Path, previous: Optional[bytes]) -> None:
    """Best-effort rollback when the directory durability barrier fails."""

    if previous is None:
        path.unlink(missing_ok=True)
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".rollback", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            written = os.write(descriptor, previous)
            if written != len(previous):
                raise OSError("confirmed local mapping rollback was partial")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    return descriptor


def _module_scope(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "slot_key",
        "family",
        "role",
    }:
        raise ConfirmedLocalMappingError("confirmed_local_module_scope_invalid")
    scope = {
        "slot_key": _safe_id(raw.get("slot_key"), "slot_key"),
        "family": _safe_id(raw.get("family"), "family"),
        "role": _choice(
            raw.get("role"), frozenset({"primary", "assist"}), "role"
        ),
    }
    if scope["slot_key"] != f"{scope['family']}_{scope['role']}":
        raise ConfirmedLocalMappingError("confirmed_local_module_scope_invalid")
    return scope


def _choice(raw: object, allowed: frozenset[str], label: str) -> str:
    value = str(raw or "").strip()
    if value not in allowed:
        raise ConfirmedLocalMappingError(f"confirmed_local_{label}_invalid")
    return value


def _safe_id(raw: object, label: str) -> str:
    value = str(raw or "").strip()
    if _SAFE_ID_RE.fullmatch(value) is None:
        raise ConfirmedLocalMappingError(f"confirmed_local_{label}_invalid")
    return value


def _sha256(raw: object, label: str) -> str:
    value = str(raw or "").strip()
    if _SHA256_RE.fullmatch(value) is None:
        raise ConfirmedLocalMappingError(f"confirmed_local_{label}_invalid")
    return value


def _bounded_text(raw: object, maximum: int, label: str) -> str:
    value = str(raw or "").strip()
    if not value or len(value) > maximum or "\x00" in value:
        raise ConfirmedLocalMappingError(f"confirmed_local_{label}_invalid")
    return value


def _nonnegative_int(raw: object, label: str) -> int:
    if type(raw) is not int or raw < 0:
        raise ConfirmedLocalMappingError(f"confirmed_local_{label}_invalid")
    return raw


def _utc_datetime(raw: object, label: str) -> datetime:
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConfirmedLocalMappingError(
                f"confirmed_local_{label}_invalid"
            ) from exc
    else:
        raise ConfirmedLocalMappingError(f"confirmed_local_{label}_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConfirmedLocalMappingError(f"confirmed_local_{label}_invalid")
    return value.astimezone(timezone.utc)


__all__ = [
    "CONFIRMED_LOCAL_MAPPING_SCHEMA_ID",
    "CONFIRMED_LOCAL_MAPPING_SCHEMA_VERSION",
    "ConfirmedLocalMappingError",
    "ConfirmedLocalMappingStore",
    "DEFAULT_CONFIRMED_LOCAL_MAPPING_DIR",
    "active_confirmations",
    "validate_confirmed_local_mapping_document",
]
