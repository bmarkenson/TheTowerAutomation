"""Narrow, durable integration lane for deterministic save mappings.

This module intentionally does not implement a general development workflow.
It accepts only server-generated mapping proposals, creates one verified child
commit from clean ``main`` with a private Git index, and publishes that object
under one private staging ref.  That staging ref is a durable transaction
boundary, not a terminal queue: the integration owner consumes it through a
guarded fast-forward, remote publication, and fresh runtime decode.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Optional, Sequence

from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateDispositionStore,
    AppendOnlyMappingCandidateStore,
    PlayerSaveMappingCandidateError,
    canonical_mapping_set_fingerprint,
    fingerprint_json,
    mapping_candidate_record_status,
    mapping_candidate_review_status,
    proposed_mapping_patch,
)
from core.player_save_mapping_integration import (
    SaveMappingIntegrationError,
    _PreparedTarget,
    _fsync_directory,
    _git_output,
    _git_status,
    _git_success,
    _is_git_object,
    _linked_worktrees,
    _read_regular_file,
    _render_proposal_targets,
    _target_path,
    _write_all,
)


SAVE_MAPPING_INTEGRATION_CAPABILITY = "save_mapping_staged_candidate_v1"
SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION = 3
SAVE_MAPPING_REVIEW_STATUS_CAPABILITY = "save_mapping_review_status_v2"
SAVE_MAPPING_DISPOSITION_CAPABILITY = "save_mapping_candidate_disposition_v1"
SAVE_MAPPING_AUTOMATIC_PROMOTION_CAPABILITY = (
    "save_mapping_automatic_promotion_v1"
)
SAVE_MAPPING_MACHINE_VERIFICATION_CAPABILITY = (
    "save_mapping_machine_verification_v1"
)
SAVE_MAPPING_STAGING_REF = "refs/thetower/save-mapping-candidate"
PROMOTION_OWNER_REF = "refs/thetower/promotion-owner"

_TRANSACTION_KIND = "save_mapping_staged_candidate_transaction"
_TRANSACTION_SCHEMA_VERSION = 3
_MAX_TRANSACTION_BYTES = 8 * 1024 * 1024
_COMMIT_SUBJECT_PREFIX = "Stage save mapping candidate"
_CANDIDATE_TRAILER = "Save-Mapping-Candidate-ID"
_PROPOSAL_TRAILER = "Save-Mapping-Proposal-Fingerprint"
_RECEIPT_SCHEMA_VERSION = 2
_ZERO_OBJECT_ID = "0" * 40
_PROMOTION_PHASES = frozenset({"commit_ready", "staged", "promoting", "published"})
_QUEUEABLE_PROMOTION_CODES = frozenset(
    {
        "integration_busy",
        "local_promotion_failed",
        "promotion_owner_busy",
        "promotion_owner_release_failed",
        "production_worktree_dirty",
        "remote_publication_pending",
        "rollback_tag_failed",
    }
)

_DISMISSIBLE_CANDIDATE_STATES = frozenset(
    {
        "more_evidence_required",
        "evidence_ambiguous",
        "canonical_conflict",
        "review_required",
        "mirror_pending",
        "authority_pending",
    }
)


def _is_hex(value: object, length: int) -> bool:
    text = str(value or "")
    return len(text) == length and all(
        character in "0123456789abcdef" for character in text
    )


def _is_evidence_fingerprint(value: object) -> bool:
    return _is_hex(value, 64) and str(value) != "0" * 64


def _utc_datetime(value: object) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _commit_message(candidate_record_id: str, proposal_fingerprint: str) -> str:
    return (
        f"{_COMMIT_SUBJECT_PREFIX} {candidate_record_id[:12]}\n\n"
        f"{_CANDIDATE_TRAILER}: {candidate_record_id}\n"
        f"{_PROPOSAL_TRAILER}: {proposal_fingerprint}\n"
    )


def _mapping_identity(record: Mapping[str, Any]) -> dict[str, str]:
    mapping = record["mapping"]
    mapping_id = str(mapping["mapping_id"])
    resolution = str(mapping["resolution"])
    authority = str(mapping.get("authority_mapping_id") or mapping_id)
    structural = str(mapping.get("structural_mapping_id") or mapping_id)
    return {
        "mapping_id": mapping_id,
        "resolution": resolution,
        "authority_mapping_id": authority,
        "structural_mapping_id": structural,
    }


def _candidate_claim_identity(
    record: Mapping[str, Any],
    *,
    include_semantic: bool,
) -> dict[str, Any]:
    candidate = record["candidate"]
    identity: dict[str, Any] = {
        "mapping": record["mapping"],
        "check_id": candidate["check_id"],
        "value_kind": candidate["value_kind"],
        "raw_discriminator": candidate["raw_discriminator"],
    }
    if candidate["value_kind"] != "module_info_index":
        identity["scope"] = candidate["scope"]
    if include_semantic:
        identity["semantic_value"] = candidate["semantic_value"]
        if candidate["value_kind"] == "module_info_index":
            identity["family"] = candidate["scope"].get("family", "")
    return identity


def _target_document(target: _PreparedTarget) -> dict[str, Any]:
    return {
        "path": target.path,
        "mapping_id": target.mapping_id,
        "before_sha256": target.before_sha256,
        "after_sha256": target.after_sha256,
        "before_base64": base64.b64encode(target.before).decode("ascii"),
        "after_base64": base64.b64encode(target.after).decode("ascii"),
        "changed": target.changed,
        "mode": target.mode,
    }


def _transaction_targets(transaction: Mapping[str, Any]) -> list[_PreparedTarget]:
    return [
        _PreparedTarget(
            path=item["path"],
            mapping_id=item["mapping_id"],
            before_sha256=item["before_sha256"],
            after_sha256=item["after_sha256"],
            before=base64.b64decode(item["before_base64"], validate=True),
            after=base64.b64decode(item["after_base64"], validate=True),
            changed=item["changed"],
            mode=item["mode"],
        )
        for item in transaction["targets"]
    ]


def _transaction_document(
    review: Mapping[str, Any],
    targets: Sequence[_PreparedTarget],
    *,
    expected_commit: str,
    commit_message: str,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": _TRANSACTION_SCHEMA_VERSION,
        "kind": _TRANSACTION_KIND,
        "transaction_id": secrets.token_hex(16),
        "phase": "commit_ready",
        "candidate_record_id": review["candidate_record_id"],
        "reviewed_proposal_fingerprint": review[
            "reviewed_proposal_fingerprint"
        ],
        "repository": {"base_commit": review["repository"]["main_commit"]},
        "staging": {
            "ref": SAVE_MAPPING_STAGING_REF,
            "expected_commit": expected_commit,
        },
        "mapping_identity": dict(review["mapping_identity"]),
        "canonical_mapping_fingerprint": review[
            "canonical_mapping_fingerprint"
        ],
        "integration_available_since": datetime.now(timezone.utc).isoformat(),
        "commit_message": commit_message,
        "targets": [_target_document(target) for target in targets],
    }
    document["transaction_fingerprint"] = fingerprint_json(document)
    return _validate_transaction_document(document)


def _validate_transaction_document(raw: object) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "kind",
        "transaction_id",
        "phase",
        "candidate_record_id",
        "reviewed_proposal_fingerprint",
        "repository",
        "staging",
        "mapping_identity",
        "canonical_mapping_fingerprint",
        "integration_available_since",
        "commit_message",
        "targets",
        "transaction_fingerprint",
    }
    if not isinstance(raw, Mapping):
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction is not a JSON object.",
        )
    if raw.get("kind") in {
        "save_mapping_preparation_transaction",
        "save_mapping_develop_integration_transaction",
    }:
        raise SaveMappingIntegrationError(
            "legacy_transaction_recovery_required",
            "A legacy save-mapping transaction remains. Inspect and retire its "
            "prepared branch state before using private-ref staging.",
        )
    if set(raw) != expected_keys:
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction shape is invalid.",
        )
    unsigned = {key: value for key, value in raw.items() if key != "transaction_fingerprint"}
    if (
        raw.get("schema_version") != _TRANSACTION_SCHEMA_VERSION
        or raw.get("kind") != _TRANSACTION_KIND
        or raw.get("phase") not in _PROMOTION_PHASES
        or not _is_hex(raw.get("transaction_id"), 32)
        or not _is_hex(raw.get("candidate_record_id"), 64)
        or not _is_hex(raw.get("reviewed_proposal_fingerprint"), 64)
        or not _is_hex(raw.get("canonical_mapping_fingerprint"), 64)
        or _utc_datetime(raw.get("integration_available_since")) is None
        or not _is_hex(raw.get("transaction_fingerprint"), 64)
        or fingerprint_json(unsigned) != raw.get("transaction_fingerprint")
    ):
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction identity is invalid.",
        )
    repository = raw.get("repository")
    staging = raw.get("staging")
    identity = raw.get("mapping_identity")
    if (
        not isinstance(repository, Mapping)
        or set(repository) != {"base_commit"}
        or not _is_git_object(repository.get("base_commit"))
        or not isinstance(staging, Mapping)
        or set(staging) != {"ref", "expected_commit"}
        or staging.get("ref") != SAVE_MAPPING_STAGING_REF
        or not _is_git_object(staging.get("expected_commit"))
        or not isinstance(identity, Mapping)
        or set(identity) != {
            "mapping_id",
            "resolution",
            "authority_mapping_id",
            "structural_mapping_id",
        }
        or not all(str(identity.get(key) or "") for key in identity)
        or not isinstance(raw.get("commit_message"), str)
        or not raw.get("commit_message")
        or raw.get("commit_message")
        != _commit_message(
            str(raw.get("candidate_record_id")),
            str(raw.get("reviewed_proposal_fingerprint")),
        )
    ):
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction scope is invalid.",
        )
    raw_targets = raw.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 16:
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction targets are invalid.",
        )
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_targets:
        if not isinstance(item, Mapping) or set(item) != {
            "path",
            "mapping_id",
            "before_sha256",
            "after_sha256",
            "before_base64",
            "after_base64",
            "changed",
            "mode",
        }:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical integration transaction target is invalid.",
            )
        relative = str(item.get("path") or "")
        if (
            not relative
            or relative in seen
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or Path(relative).parent != Path("config/player_save_versions")
            or Path(relative).suffix != ".json"
            or not str(item.get("mapping_id") or "")
            or not _is_hex(item.get("before_sha256"), 64)
            or not _is_hex(item.get("after_sha256"), 64)
            or type(item.get("changed")) is not bool
            or isinstance(item.get("mode"), bool)
            or not isinstance(item.get("mode"), int)
            or int(item["mode"]) & 0o111
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical integration transaction target identity is invalid.",
            )
        try:
            before = base64.b64decode(item["before_base64"], validate=True)
            after = base64.b64decode(item["after_base64"], validate=True)
        except (TypeError, ValueError) as exc:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical integration transaction payload is invalid.",
            ) from exc
        if (
            hashlib.sha256(before).hexdigest() != item["before_sha256"]
            or hashlib.sha256(after).hexdigest() != item["after_sha256"]
            or (before != after) != item["changed"]
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical integration transaction payload changed.",
            )
        seen.add(relative)
        targets.append(dict(item))
    return {
        **dict(raw),
        "repository": dict(repository),
        "staging": dict(staging),
        "mapping_identity": dict(identity),
        "targets": targets,
    }


def _load_transaction(path: Path) -> Optional[dict[str, Any]]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction cannot be inspected.",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction is not a regular file.",
        )
    try:
        payload = path.read_bytes()
        if len(payload) > _MAX_TRANSACTION_BYTES:
            raise ValueError("transaction too large")
        raw = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical integration transaction cannot be decoded.",
        ) from exc
    return _validate_transaction_document(raw)


def _publish_json(path: Path, document: Mapping[str, Any], *, replace: bool) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if replace:
            if not os.path.lexists(path):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "The canonical integration transaction disappeared.",
                )
        elif os.path.lexists(path):
            raise SaveMappingIntegrationError(
                "transaction_recovery_required",
                "An earlier canonical integration must be recovered first.",
            )
        payload = (json.dumps(document, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.stage-",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        installed = False
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, path)
            installed = True
            _fsync_directory(path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if not installed:
                temporary.unlink(missing_ok=True)
    except SaveMappingIntegrationError:
        raise
    except OSError as exc:
        raise SaveMappingIntegrationError(
            "transaction_write_failed",
            "The canonical integration transaction could not be written durably.",
        ) from exc


def _set_transaction_phase(
    path: Path,
    transaction: Mapping[str, Any],
    phase: str,
) -> dict[str, Any]:
    unsigned = {
        key: value
        for key, value in transaction.items()
        if key != "transaction_fingerprint"
    }
    unsigned["phase"] = phase
    updated = {**unsigned, "transaction_fingerprint": fingerprint_json(unsigned)}
    validated = _validate_transaction_document(updated)
    _publish_json(path, validated, replace=True)
    return validated


def _remove_transaction(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The completed canonical integration journal could not be retired.",
        ) from exc
    _fsync_directory(path.parent)


class CanonicalDecodeReceiptStore:
    """Append-only, privacy-safe proof of post-deployment stable decoding."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def list_records(self) -> list[dict[str, Any]]:
        try:
            descriptor = os.open(self.path, os.O_RDWR)
        except FileNotFoundError:
            return []
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._recover_partial_tail(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            return self._read_locked(descriptor)
        finally:
            os.close(descriptor)

    def append_once(self, record: Mapping[str, Any]) -> bool:
        validated = self._validate(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.fchmod(descriptor, 0o600)
            self._recover_partial_tail(descriptor)
            original_size = os.fstat(descriptor).st_size
            os.lseek(descriptor, 0, os.SEEK_SET)
            for item in self._read_locked(descriptor):
                same_integration = (
                    item["candidate_record_id"]
                    == validated["candidate_record_id"]
                    and item["integration_commit"]
                    == validated["integration_commit"]
                )
                if not same_integration:
                    continue
                if (
                    item["canonical_mapping_fingerprint"]
                    == validated["canonical_mapping_fingerprint"]
                    and item["snapshot_mapping_fingerprint"]
                    == validated["snapshot_mapping_fingerprint"]
                ):
                    return False
                raise SaveMappingIntegrationError(
                    "decode_receipt_conflict",
                    "A canonical decode receipt conflicts with the deployed "
                    "integration identity.",
                )
            line = (json.dumps(validated, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            try:
                os.lseek(descriptor, 0, os.SEEK_END)
                _write_all(descriptor, line)
                os.fsync(descriptor)
            except OSError:
                if os.fstat(descriptor).st_size != original_size:
                    os.ftruncate(descriptor, original_size)
                    os.fsync(descriptor)
                raise
            _fsync_directory(self.path.parent)
            return True
        finally:
            os.close(descriptor)

    def _read_locked(self, descriptor: int) -> list[dict[str, Any]]:
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
            raise SaveMappingIntegrationError(
                "decode_receipt_store_invalid",
                "The canonical decode receipt store has an incomplete tail.",
            )
        records: list[dict[str, Any]] = []
        for line in payload.splitlines():
            try:
                raw = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SaveMappingIntegrationError(
                    "decode_receipt_store_invalid",
                    "The canonical decode receipt store is invalid.",
                ) from exc
            records.append(self._validate(raw))
        return records

    @staticmethod
    def _recover_partial_tail(descriptor: int) -> None:
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

    @staticmethod
    def _validate(raw: object) -> dict[str, Any]:
        keys = {
            "schema_version",
            "receipt_id",
            "candidate_record_id",
            "integration_commit",
            "canonical_mapping_fingerprint",
            "snapshot_mapping_fingerprint",
            "snapshot_fingerprint",
            "acquisition_started_at",
            "acquisition_main_commit",
            "captured_at",
        }
        if not isinstance(raw, Mapping) or set(raw) != keys:
            raise SaveMappingIntegrationError(
                "decode_receipt_store_invalid",
                "A canonical decode receipt has an invalid shape.",
            )
        unsigned = {
            key: raw[key]
            for key in keys
            if key not in {"schema_version", "receipt_id"}
        }
        if (
            raw.get("schema_version") != _RECEIPT_SCHEMA_VERSION
            or not _is_hex(raw.get("receipt_id"), 64)
            or fingerprint_json(unsigned) != raw.get("receipt_id")
            or not _is_hex(raw.get("candidate_record_id"), 64)
            or not _is_git_object(raw.get("integration_commit"))
            or not _is_hex(raw.get("canonical_mapping_fingerprint"), 64)
            or not _is_hex(raw.get("snapshot_mapping_fingerprint"), 64)
            or not _is_hex(raw.get("snapshot_fingerprint"), 64)
            or not _is_git_object(raw.get("acquisition_main_commit"))
            or _utc_datetime(raw.get("acquisition_started_at")) is None
            or _utc_datetime(raw.get("captured_at")) is None
        ):
            raise SaveMappingIntegrationError(
                "decode_receipt_store_invalid",
                "A canonical decode receipt has an invalid identity.",
            )
        return dict(raw)


class SaveMappingIntegrationManager:
    """Review, stage, and consume one deterministic mapping transaction."""

    def __init__(
        self,
        *,
        repository_root: Path | str,
        candidate_store: AppendOnlyMappingCandidateStore,
        disposition_path: Path | str | None = None,
        lock_path: Path | str | None = None,
        mapping_set_lock_path: Path | str | None = None,
        transaction_path: Path | str | None = None,
        decode_receipt_path: Path | str | None = None,
        transaction_fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.candidate_store = candidate_store
        self.candidate_dispositions = AppendOnlyMappingCandidateDispositionStore(
            Path(disposition_path)
            if disposition_path is not None
            else candidate_store.path.with_name("dispositions-v1.jsonl")
        )
        self.lock_path = (
            Path(lock_path)
            if lock_path is not None
            else candidate_store.path.with_name("canonical-integration.lock")
        )
        self.mapping_set_lock_path = (
            Path(mapping_set_lock_path)
            if mapping_set_lock_path is not None
            else candidate_store.path.with_name("canonical-mapping-files.lock")
        )
        self.transaction_path = (
            Path(transaction_path)
            if transaction_path is not None
            else candidate_store.path.with_name(
                "canonical-integration-transaction.json"
            )
        )
        receipt_path = (
            Path(decode_receipt_path)
            if decode_receipt_path is not None
            else candidate_store.path.with_name("canonical-decodes-v2.jsonl")
        )
        self.decode_receipts = CanonicalDecodeReceiptStore(receipt_path)
        self._transaction_fault_hook = transaction_fault_hook

    def machine_verification(
        self,
        *,
        candidate_record_id: object,
    ) -> dict[str, Any]:
        """Certify the one evidence class that needs no operator judgment.

        A killed-by identifier is self-verifying only when the durable receipt
        binds one exact terminal UI observation to one pre-mutation save
        snapshot and retains every runtime, target, round, activity, and
        boundary fingerprint.  All other mapping classes continue through
        explicit review.
        """

        try:
            record = self.candidate_store.get(candidate_record_id)
            records = self.candidate_store.list_records()
            self._require_candidate_not_dismissed(record)
        except (OSError, PlayerSaveMappingCandidateError) as exc:
            return _machine_verification_result(
                None,
                eligible=False,
                code=_proposal_error_code(exc),
                reason=_proposal_error_message(exc),
            )
        return self._machine_verification_record(record, records)

    def _machine_verification_record(
        self,
        record: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        candidate = record.get("candidate")
        evidence = record.get("evidence")
        workflow = evidence.get("workflow") if isinstance(evidence, Mapping) else None
        mapping = record.get("mapping")
        checks = (
            isinstance(candidate, Mapping)
            and candidate.get("status") == "ready_for_review"
            and candidate.get("check_id") == "battle_history_killed_by"
            and candidate.get("value_kind") == "battle_history_killed_by_id"
            and isinstance(candidate.get("semantic_value"), str)
            and bool(str(candidate.get("semantic_value") or "").strip())
            and isinstance(candidate.get("raw_discriminator"), Mapping)
            and candidate["raw_discriminator"].get("kind") == "integer_id"
            and isinstance(candidate["raw_discriminator"].get("value"), int)
            and not isinstance(candidate["raw_discriminator"].get("value"), bool)
            and isinstance(mapping, Mapping)
            and mapping.get("resolution") in {"exact", "compatible_exact_revision"}
            and isinstance(evidence, Mapping)
            and evidence.get("evidence_strength") == "deterministic"
            and evidence.get("pairing_method") == "exact_locator"
            and evidence.get("pre_mutation") is True
            and all(
                _is_evidence_fingerprint(evidence.get(name))
                for name in (
                    "snapshot_fingerprint",
                    "ui_evidence_fingerprint",
                    "source_observation_fingerprint",
                )
            )
            and isinstance(workflow, Mapping)
            and workflow.get("game_state")
            in {"terminal_game_over", "terminal_tournament_results"}
            and all(
                _is_evidence_fingerprint(workflow.get(name))
                for name in (
                    "runtime_session_fingerprint",
                    "target_generation_fingerprint",
                    "activity_scope_fingerprint",
                    "active_round_identity_fingerprint",
                    "boundary_fingerprint",
                )
            )
        )
        if not checks:
            return _machine_verification_result(
                record,
                eligible=False,
                code="operator_review_required",
                reason=(
                    "This observation is not a complete exact-boundary killed-by "
                    "proof, so its meaning still requires operator review."
                ),
            )
        try:
            self._require_routine_candidate_records(record, records)
            current = mapping_candidate_record_status(
                record,
                repository_root=self.repository_root,
            )
            if current.get("state") != "integrated":
                proposed_mapping_patch(
                    record,
                    repository_root=self.repository_root,
                )
        except (OSError, PlayerSaveMappingCandidateError) as exc:
            return _machine_verification_result(
                record,
                eligible=False,
                code=_proposal_error_code(exc),
                reason=_proposal_error_message(exc),
            )
        return _machine_verification_result(
            record,
            eligible=True,
            code="machine_verified",
            reason=(
                "The exact terminal UI observation and its paired "
                "pre-mutation save prove this killed-by mapping without "
                "operator judgment."
            ),
        )

    def catalog(self) -> dict[str, Any]:
        """Return staged-candidate review state without leaking projection failures."""

        try:
            return self._catalog()
        except (
            OSError,
            PlayerSaveMappingCandidateError,
            SaveMappingIntegrationError,
        ) as exc:
            return {
                "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
                "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
                "available": False,
                "reason": str(exc),
                "code": str(
                    getattr(exc, "code", None)
                    or _proposal_error_code(exc)
                ),
                "repository": None,
                "items": [],
                "transaction": None,
            }

    def _catalog(self) -> dict[str, Any]:
        """Build candidate review state and fixed private-ref eligibility."""

        try:
            transaction = _load_transaction(self.transaction_path)
            base = (
                self._lightweight_transaction_state(transaction)
                if transaction is not None
                else self._base_state()
            )
            status = self.candidate_lifecycle_status(
                base=base,
                transaction=transaction,
            )
        except (
            OSError,
            PlayerSaveMappingCandidateError,
            SaveMappingIntegrationError,
        ) as exc:
            return {
                "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
                "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
                "available": False,
                "reason": str(exc),
                "code": str(
                    getattr(exc, "code", None)
                    or _proposal_error_code(exc)
                ),
                "repository": None,
                "items": [],
                "transaction": None,
            }
        if status.get("available") is not True:
            return {
                "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
                "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
                "available": False,
                "reason": str(status.get("reason") or "Candidate queue unavailable"),
                "code": "candidate_queue_unavailable",
                "repository": self._public_repository(base),
                "items": [],
                "transaction": self._public_transaction(transaction, base),
            }
        completed = bool(transaction and self._transaction_validated(transaction, base))
        active_transaction = None if completed else transaction
        items: list[dict[str, Any]] = []
        for item in status.get("items") or ():
            if item.get("state") == "integrated":
                continue
            projected = dict(item)
            verification = self.machine_verification(
                candidate_record_id=item.get("record_id")
            )
            if item.get("state") in {
                "promotion_pending",
                "promotion_cleanup_pending",
                "remote_publication_pending",
                "production_validation_pending",
                "integration_unconfirmed",
            }:
                projected.update(
                    review_available=False,
                    review_code=str(item.get("state")),
                    review_reason=str(item.get("reason") or ""),
                )
            elif item.get("state") in {
                "integration_recovery_required",
                "restaging_required",
            }:
                recoverable = bool(
                    transaction is not None
                    and transaction["candidate_record_id"]
                    == item.get("candidate_record_id")
                )
                projected.update(
                    review_available=recoverable,
                    review_code=("" if recoverable else str(item.get("state"))),
                    review_reason=(
                        "Review the durable exact transaction, then retry it once."
                        if recoverable
                        else str(item.get("reason") or "")
                    ),
                )
            elif active_transaction is not None:
                projected.update(
                    review_available=False,
                    review_code="transaction_recovery_required",
                    review_reason=(
                        "Finish or inspect the existing canonical integration "
                        "before reviewing another candidate."
                    ),
                )
            else:
                try:
                    record = self.candidate_store.get(item.get("record_id"))
                    self._require_routine_candidate(record)
                    proposed_mapping_patch(
                        record,
                        repository_root=self.repository_root,
                    )
                except (OSError, PlayerSaveMappingCandidateError) as exc:
                    projected.update(
                        review_available=False,
                        review_code=_proposal_error_code(exc),
                        review_reason=_proposal_error_message(exc),
                    )
                else:
                    projected.update(
                        review_available=base["integration_available"],
                        review_code=("" if base["integration_available"] else base["code"]),
                        review_reason=("" if base["integration_available"] else base["reason"]),
                    )
            automatic_integration = verification.get("eligible") is True
            preautomatic_code = str(projected.get("review_code") or "")
            preautomatic_reason = str(projected.get("review_reason") or "")
            automatic_blocked = bool(
                automatic_integration
                and (
                    item.get("state")
                    in {
                        "integration_recovery_required",
                        "integration_unconfirmed",
                        "promotion_pending",
                        "promotion_cleanup_pending",
                        "remote_publication_pending",
                        "restaging_required",
                    }
                    or (
                        item.get("state") == "automatic_integration_pending"
                        and (
                            active_transaction is not None
                            or base["integration_available"] is not True
                        )
                    )
                )
            )
            automatic_blocker_code = preautomatic_code if automatic_blocked else ""
            automatic_blocker_reason = (
                preautomatic_reason if automatic_blocked else ""
            )
            if automatic_integration:
                projected.update(
                    review_available=False,
                    review_code="machine_verified",
                    review_reason=str(verification["reason"]),
                )
            dismiss_available = bool(
                not automatic_integration
                and active_transaction is None
                and item.get("state") in _DISMISSIBLE_CANDIDATE_STATES
            )
            integration_recovery = item.get("state") in {
                "integration_recovery_required",
                "integration_unconfirmed",
                "promotion_pending",
                "promotion_cleanup_pending",
                "remote_publication_pending",
                "restaging_required",
            }
            review_available = projected.get("review_available") is True
            projected.update(
                machine_verification=verification,
                automatic_integration=automatic_integration,
                automatic_integration_blocked=automatic_blocked,
                automatic_integration_blocker_code=automatic_blocker_code,
                automatic_integration_blocker_reason=automatic_blocker_reason,
                dismiss_available=dismiss_available,
                dismiss_reason=(
                    "Dismiss this observation while preserving its durable receipt."
                    if dismiss_available
                    else (
                        "This exact causal proof is machine-verified and is being "
                        "integrated automatically."
                        if automatic_integration
                        else _dismiss_unavailable_reason(
                            projected,
                            active_transaction,
                        )
                    )
                ),
                agent_review_prompt=(
                    ""
                    if review_available
                    or (automatic_integration and not automatic_blocked)
                    else _agent_review_prompt(
                        projected,
                        dismiss_available=dismiss_available,
                        automatic_integration=automatic_integration,
                        integration_recovery=integration_recovery,
                    )
                ),
                next_action=(
                    _automatic_integration_next_action(
                        projected,
                        blocker_reason=automatic_blocker_reason,
                    )
                    if automatic_integration
                    else
                    "Review the exact proposal, or dismiss this observation if "
                    "you know it is incorrect."
                    if review_available
                    else _nonreviewable_next_action(projected)
                ),
            )
            items.append(projected)
        return {
            "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
            "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
            "available": True,
            "reason": "",
            "code": "",
            "repository": self._public_repository(base),
            "items": items,
            "transaction": self._public_transaction(transaction, base),
        }

    def candidate_lifecycle_status(
        self,
        *,
        base: Optional[Mapping[str, Any]] = None,
        transaction: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Project lifecycle across private staging, production, and decode proof."""

        current = dict(base or self._base_state())
        if transaction is None:
            transaction = _load_transaction(self.transaction_path)
        main_status = mapping_candidate_review_status(
            store=self.candidate_store,
            disposition_store=self.candidate_dispositions,
            repository_root=self.repository_root,
        )
        if main_status.get("available") is not True:
            return main_status
        main_items = {
            str(item.get("candidate_record_id")): dict(item)
            for item in main_status.get("items") or ()
        }
        for candidate_id, item in main_items.items():
            verification = self.machine_verification(
                candidate_record_id=candidate_id
            )
            if (
                verification.get("eligible") is True
                and item.get("state") == "review_required"
            ):
                item.update(
                    state="automatic_integration_pending",
                    reason=str(verification["reason"]),
                )
        if transaction is not None:
            candidate_id = transaction["candidate_record_id"]
            record = self.candidate_store.get(candidate_id)
            transaction_claim = _candidate_claim_identity(
                record,
                include_semantic=True,
            )
            for other_id in tuple(main_items):
                if other_id == candidate_id:
                    continue
                other = self.candidate_store.get(other_id)
                if _candidate_claim_identity(
                    other,
                    include_semantic=True,
                ) == transaction_claim:
                    main_items.pop(other_id, None)
            main_candidate = mapping_candidate_record_status(
                record,
                repository_root=self.repository_root,
            )
            item = dict(main_candidate)
            lifecycle, reason = self._transaction_lifecycle(
                transaction,
                current,
                main_candidate=main_candidate,
            )
            item.update(
                state=lifecycle,
                reason=reason,
                integration_commit=transaction["staging"]["expected_commit"],
                staged_commit=transaction["staging"]["expected_commit"],
            )
            main_items[candidate_id] = item
        items = sorted(
            main_items.values(),
            key=lambda item: (
                str(item.get("state")),
                str(item.get("check_id")),
                str(item.get("mapping_id")),
                str(item.get("record_id")),
            ),
        )
        counts: dict[str, int] = {}
        for item in items:
            state = str(item["state"])
            counts[state] = counts.get(state, 0) + 1
        return {
            "schema_version": 2,
            "capability": SAVE_MAPPING_REVIEW_STATUS_CAPABILITY,
            "available": True,
            "items": items,
            "counts": counts,
            "reason": "",
        }

    def status(self) -> dict[str, Any]:
        """Return a non-raising lifecycle projection for the main status API."""

        try:
            transaction = _load_transaction(self.transaction_path)
            if transaction is None:
                status = mapping_candidate_review_status(
                    store=self.candidate_store,
                    disposition_store=self.candidate_dispositions,
                    repository_root=self.repository_root,
                )
                if status.get("available") is True and any(
                    item.get("state") != "integrated"
                    for item in status.get("items") or ()
                ):
                    return self.candidate_lifecycle_status(
                        base=self._base_state(),
                        transaction=None,
                    )
                return {
                    **status,
                    "schema_version": 2,
                    "capability": SAVE_MAPPING_REVIEW_STATUS_CAPABILITY,
                }
            return self.candidate_lifecycle_status(
                base=self._lightweight_transaction_state(transaction),
                transaction=transaction,
            )
        except (OSError, PlayerSaveMappingCandidateError, SaveMappingIntegrationError) as exc:
            fallback = mapping_candidate_review_status(
                store=self.candidate_store,
                disposition_store=self.candidate_dispositions,
                repository_root=self.repository_root,
            )
            return {
                **fallback,
                "schema_version": 2,
                "capability": SAVE_MAPPING_REVIEW_STATUS_CAPABILITY,
                "available": False,
                "reason": str(exc),
            }

    def review(self, *, candidate_record_id: object) -> dict[str, Any]:
        """Return a non-mutating proposal bound to exact mapping inputs."""

        transaction = _load_transaction(self.transaction_path)
        if (
            transaction is not None
            and transaction["candidate_record_id"]
            == str(candidate_record_id or "").lower()
        ):
            base = self._lightweight_transaction_state(transaction)
            lifecycle, _reason = self._transaction_lifecycle(transaction, base)
            if lifecycle in {
                "integration_recovery_required",
                "restaging_required",
            }:
                return self._recovery_review(transaction, base)
        base = self._base_state()
        if transaction is not None and self._transaction_validated(
            transaction,
            base,
        ):
            transaction = None
        if not base["integration_available"]:
            raise SaveMappingIntegrationError(base["code"], base["reason"])
        try:
            record = self.candidate_store.get(candidate_record_id)
            self._require_routine_candidate(record)
            proposal = proposed_mapping_patch(
                record,
                repository_root=self.repository_root,
            )
            staged_targets = _render_proposal_targets(
                proposal,
                repository_root=self.repository_root,
                candidate_record=record,
            )
            self._validate_target_scope(self.repository_root, staged_targets)
            canonical_fingerprint = self._canonical_mapping_fingerprint(
                self.repository_root,
                staged_targets,
                _mapping_identity(record),
            )
        except SaveMappingIntegrationError:
            raise
        except (OSError, PlayerSaveMappingCandidateError) as exc:
            raise SaveMappingIntegrationError(
                _proposal_error_code(exc),
                _proposal_error_message(exc),
            ) from exc
        fingerprint_payload = {
            "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
            "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
            "candidate_record_id": record["record_id"],
            "proposal": proposal,
            "rendered_targets": [_public_target(target) for target in staged_targets],
            "canonical_mapping_fingerprint": canonical_fingerprint,
            "commit_contract": {
                "subject_prefix": _COMMIT_SUBJECT_PREFIX,
                "candidate_trailer": _CANDIDATE_TRAILER,
                "proposal_trailer": _PROPOSAL_TRAILER,
            },
        }
        reviewed_fingerprint = fingerprint_json(fingerprint_payload)
        staging = {"available": True, "code": "", "reason": ""}
        recovery_required = False
        if transaction is not None:
            matches = (
                transaction["candidate_record_id"] == record["record_id"]
                and transaction["reviewed_proposal_fingerprint"]
                == reviewed_fingerprint
            )
            recovery_required = True
            staging = {
                "available": matches,
                "code": "transaction_recovery_required",
                "reason": (
                    "Interrupted staging for this exact review can be "
                    "continued once; refresh after the result."
                    if matches
                    else "Interrupted staging for another candidate must "
                    "be recovered first."
                ),
            }
        return {
            "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
            "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
            "operation": "review",
            "candidate_record_id": record["record_id"],
            "reviewed_proposal_fingerprint": reviewed_fingerprint,
            "reviewed_base_commit": base["main_commit"],
            "repository": self._public_repository(base),
            "mapping_identity": _mapping_identity(record),
            "canonical_mapping_fingerprint": canonical_fingerprint,
            "proposal": proposal,
            "rendered_targets": [
                _public_target(target) for target in staged_targets
            ],
            "stage": staging,
            "recovery_required": recovery_required,
        }

    def dismiss(self, *, candidate_record_id: object) -> dict[str, Any]:
        """Dismiss one exact observation without deleting its source receipt."""

        descriptor = self._acquire_lock()
        try:
            candidate_id = str(candidate_record_id or "").strip().lower()
            try:
                prior = self.candidate_dispositions.dismissal_for(candidate_id)
            except (OSError, PlayerSaveMappingCandidateError) as exc:
                raise SaveMappingIntegrationError(
                    _proposal_error_code(exc),
                    _proposal_error_message(exc),
                ) from exc
            if prior is not None:
                return _dismissal_result(prior, changed=False)
            if _load_transaction(self.transaction_path) is not None:
                raise SaveMappingIntegrationError(
                    "transaction_recovery_required",
                    "Finish or inspect the existing canonical integration before "
                    "dismissing an observation.",
                )
            try:
                record = self.candidate_store.get(candidate_id)
                status = mapping_candidate_record_status(
                    record,
                    repository_root=self.repository_root,
                )
            except (OSError, PlayerSaveMappingCandidateError) as exc:
                raise SaveMappingIntegrationError(
                    _proposal_error_code(exc),
                    _proposal_error_message(exc),
                ) from exc
            if status.get("state") not in _DISMISSIBLE_CANDIDATE_STATES:
                raise SaveMappingIntegrationError(
                    "mapping_candidate_not_dismissible",
                    "This observation is no longer an active dismissible candidate. "
                    "Refresh the catalog before choosing another action.",
                )
            try:
                disposition = self.candidate_dispositions.dismiss(
                    record["record_id"]
                )
            except (OSError, PlayerSaveMappingCandidateError) as exc:
                raise SaveMappingIntegrationError(
                    _proposal_error_code(exc),
                    _proposal_error_message(exc),
                ) from exc
            return _dismissal_result(
                disposition,
                changed=bool(disposition["changed"]),
            )
        finally:
            os.close(descriptor)

    def _recovery_review(
        self,
        transaction: Mapping[str, Any],
        base: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = self.candidate_store.get(transaction["candidate_record_id"])
        targets = _transaction_targets(transaction)
        proposal = {
            "schema_version": 2,
            "capability": "player_save_mapping_candidate_review_v2",
            "record_id": transaction["candidate_record_id"],
            "status": "recovery",
            "atomic_group": True,
            "targets": [
                {
                    "path": target.path,
                    "expected_sha256": target.before_sha256,
                    "mapping_id": target.mapping_id,
                    "state": "recovery",
                    "operations": [],
                }
                for target in targets
            ],
        }
        repository = self._public_repository(base)
        lifecycle, _lifecycle_reason = self._transaction_lifecycle(
            transaction,
            base,
        )
        restaging = lifecycle == "restaging_required"
        repository.update(
            integration_available=False,
            code="transaction_recovery_required",
            reason=(
                "The exact candidate must be retired and restaged on current main."
                if restaging
                else "An exact durable staging transaction requires recovery."
            ),
        )
        return {
            "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
            "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
            "operation": "review",
            "candidate_record_id": record["record_id"],
            "reviewed_proposal_fingerprint": transaction[
                "reviewed_proposal_fingerprint"
            ],
            "reviewed_base_commit": transaction["repository"]["base_commit"],
            "repository": repository,
            "mapping_identity": dict(transaction["mapping_identity"]),
            "canonical_mapping_fingerprint": transaction[
                "canonical_mapping_fingerprint"
            ],
            "proposal": proposal,
            "rendered_targets": [_public_target(target) for target in targets],
            "stage": {
                "available": True,
                "code": "transaction_recovery_required",
                "reason": (
                    "Retire only the exact old candidate and restage these unchanged "
                    "mapping inputs on current main."
                    if restaging
                    else "Retry this exact durable transaction once; Git refs and "
                    "canonical invariants will be reverified before success."
                ),
            },
            "recovery_required": True,
        }

    def stage(
        self,
        *,
        candidate_record_id: object,
        reviewed_proposal_fingerprint: object,
    ) -> dict[str, Any]:
        """Create and publish the exact reviewed child commit to the staging ref."""

        supplied = str(reviewed_proposal_fingerprint or "").strip().lower()
        if not _is_hex(supplied, 64):
            raise SaveMappingIntegrationError(
                "reviewed_proposal_fingerprint_invalid",
                "A full reviewed proposal fingerprint is required.",
            )
        descriptor = self._acquire_lock()
        try:
            transaction = _load_transaction(self.transaction_path)
            if transaction is not None and self._transaction_validated(
                transaction,
                self._lightweight_transaction_state(transaction),
            ):
                exact_retry = (
                    str(candidate_record_id or "").lower()
                    == transaction["candidate_record_id"]
                    and supplied
                    == transaction["reviewed_proposal_fingerprint"]
                )
                result = _integration_result(
                    transaction,
                    idempotent=True,
                    promoted=True,
                )
                self._release_owned_promotion_owner(transaction)
                self._retire_staging_ref(transaction)
                _remove_transaction(self.transaction_path)
                if exact_retry:
                    return result
                transaction = None
            if transaction is not None:
                if (
                    str(candidate_record_id or "").lower()
                    != transaction["candidate_record_id"]
                    or supplied
                    != transaction["reviewed_proposal_fingerprint"]
                ):
                    raise SaveMappingIntegrationError(
                        "transaction_recovery_required",
                        "An interrupted integration has a different reviewed identity.",
                    )
                lifecycle, _reason = self._transaction_lifecycle(
                    transaction,
                    self._lightweight_transaction_state(transaction),
                )
                if lifecycle == "restaging_required":
                    if _git_status(self.repository_root):
                        raise SaveMappingIntegrationError(
                            "production_worktree_dirty",
                            "Production changed while restaging was confirmed.",
                        )
                    self._fault("before_stale_candidate_retirement")
                    self._retire_staging_ref(transaction)
                    self._fault("stale_candidate_ref_retired")
                    _remove_transaction(self.transaction_path)
                    self._fault("stale_candidate_retired")
                    transaction = None
                else:
                    return self._apply_transaction(transaction, idempotent=True)

            review = self.review(candidate_record_id=candidate_record_id)
            if supplied != review["reviewed_proposal_fingerprint"]:
                raise SaveMappingIntegrationError(
                    "reviewed_proposal_stale",
                    "The reviewed mapping inputs changed; nothing was staged. "
                    "Refresh and review again.",
                )
            if review["stage"]["available"] is not True:
                raise SaveMappingIntegrationError(
                    str(review["stage"].get("code") or "staging_unavailable"),
                    str(review["stage"].get("reason") or "Staging is unavailable."),
                )
            record = self.candidate_store.get(candidate_record_id)
            targets = _render_proposal_targets(
                review["proposal"],
                repository_root=self.repository_root,
                candidate_record=record,
            )
            message = _commit_message(record["record_id"], supplied)
            expected_commit = self._build_commit(
                self.repository_root,
                base_commit=review["repository"]["main_commit"],
                targets=targets,
                commit_message=message,
            )
            self._verify_commit(
                expected_commit,
                base_commit=review["repository"]["main_commit"],
                targets=targets,
                commit_message=message,
            )
            self._fault("commit_created")
            transaction = _transaction_document(
                review,
                targets,
                expected_commit=expected_commit,
                commit_message=message,
            )
            _publish_json(self.transaction_path, transaction, replace=False)
            self._fault("journal_written")
            return self._apply_transaction(transaction, idempotent=False)
        finally:
            os.close(descriptor)

    def integrate_reviewed(
        self,
        *,
        candidate_record_id: object,
        reviewed_proposal_fingerprint: object,
    ) -> dict[str, Any]:
        """Stage and immediately consume one explicitly reviewed proposal."""

        self.stage(
            candidate_record_id=candidate_record_id,
            reviewed_proposal_fingerprint=reviewed_proposal_fingerprint,
        )
        return self._promote_or_queue()

    def automatic_reconciliation_plan(self) -> dict[str, Any]:
        """Describe one pending automatic action without changing state."""

        try:
            transaction = _load_transaction(self.transaction_path)
            if transaction is not None:
                needs_cleanup = bool(
                    transaction["phase"] == "published"
                    and self._matching_decode_receipt(transaction)
                )
                owner_cleanup = bool(
                    transaction["phase"] == "published"
                    and self._promotion_owner_is_owned(transaction)
                )
                if (
                    transaction["phase"] != "published"
                    or needs_cleanup
                    or owner_cleanup
                ):
                    return {
                        "capability": SAVE_MAPPING_AUTOMATIC_PROMOTION_CAPABILITY,
                        "needed": True,
                        "action": (
                            "retire_validated_transaction"
                            if needs_cleanup
                            else "release_promotion_owner"
                            if owner_cleanup
                            else "consume_staged_transaction"
                        ),
                        "candidate_record_id": transaction[
                            "candidate_record_id"
                        ],
                    }
                return _automatic_reconciliation_idle()
            catalog = self._catalog()
            if catalog.get("available") is not True:
                return _automatic_reconciliation_idle(
                    reason=str(catalog.get("reason") or "Catalog unavailable")
                )
            candidate = next(
                (
                    item
                    for item in catalog.get("items") or ()
                    if item.get("automatic_integration") is True
                ),
                None,
            )
            if candidate is None:
                return _automatic_reconciliation_idle()
            return {
                "capability": SAVE_MAPPING_AUTOMATIC_PROMOTION_CAPABILITY,
                "needed": True,
                "action": "machine_verify_and_integrate",
                "candidate_record_id": candidate["candidate_record_id"],
            }
        except (
            OSError,
            PlayerSaveMappingCandidateError,
            SaveMappingIntegrationError,
        ) as exc:
            return _automatic_reconciliation_idle(reason=str(exc))

    def reconcile_automatic(self) -> dict[str, Any]:
        """Consume staged work or integrate one machine-verifiable candidate."""

        plan = self.automatic_reconciliation_plan()
        if plan.get("needed") is not True:
            return plan
        if plan["action"] == "machine_verify_and_integrate":
            candidate_id = plan["candidate_record_id"]
            verification = self.machine_verification(
                candidate_record_id=candidate_id
            )
            if verification.get("eligible") is not True:
                raise SaveMappingIntegrationError(
                    str(
                        verification.get("code")
                        or "machine_verification_invalidated"
                    ),
                    str(
                        verification.get("reason")
                        or "Machine verification was invalidated."
                    ),
                )
            review = self.review(candidate_record_id=candidate_id)
            self.stage(
                candidate_record_id=candidate_id,
                reviewed_proposal_fingerprint=review[
                    "reviewed_proposal_fingerprint"
                ],
            )
        return self._promote_or_queue()

    def _promote_or_queue(
        self,
        *,
        restaging_attempted: bool = False,
    ) -> dict[str, Any]:
        try:
            return self.promote_staged()
        except SaveMappingIntegrationError as exc:
            if exc.code == "restaging_required" and not restaging_attempted:
                transaction = _load_transaction(self.transaction_path)
                if transaction is None:
                    raise
                recovery = self._recovery_review(
                    transaction,
                    self._lightweight_transaction_state(transaction),
                )
                self.stage(
                    candidate_record_id=transaction["candidate_record_id"],
                    reviewed_proposal_fingerprint=recovery[
                        "reviewed_proposal_fingerprint"
                    ],
                )
                return self._promote_or_queue(restaging_attempted=True)
            if exc.code not in _QUEUEABLE_PROMOTION_CODES:
                raise
            transaction = _load_transaction(self.transaction_path)
            if transaction is None:
                raise
            return _promotion_queued_result(
                transaction,
                repository_root=self.repository_root,
                code=exc.code,
                reason=str(exc),
            )

    def promote_staged(self) -> dict[str, Any]:
        """Consume the durable private candidate through publication.

        The global promotion-owner ref serializes the local production
        mutation.  All Git updates are exact and non-forcing.  If publication
        cannot be completed, the durable transaction and owner remain for the
        background reconciler or an agent to finish.
        """

        descriptor = self._acquire_lock()
        try:
            transaction = _load_transaction(self.transaction_path)
            if transaction is None:
                raise SaveMappingIntegrationError(
                    "staged_transaction_missing",
                    "No durable save-mapping transaction is available to promote.",
                )
            if transaction["phase"] == "commit_ready":
                self._apply_transaction(transaction, idempotent=True)
                transaction = _load_transaction(self.transaction_path)
                if transaction is None:
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "The staged transaction disappeared during recovery.",
                    )
            self._verify_transaction_binding(transaction)
            expected = transaction["staging"]["expected_commit"]
            base_commit = transaction["repository"]["base_commit"]
            main_commit = _git_output(
                self.repository_root,
                "rev-parse",
                "refs/heads/main",
            )
            main_contains = _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                expected,
                main_commit,
            )

            if not main_contains:
                if main_commit != base_commit:
                    raise SaveMappingIntegrationError(
                        "restaging_required",
                        "main advanced without the staged mapping; the exact "
                        "candidate must be regenerated on the current tip.",
                    )
                self._verify_staged_state(transaction)
                try:
                    observed_remote = self._remote_main_commit()
                except SaveMappingIntegrationError:
                    observed_remote = ""
                if (
                    observed_remote
                    and observed_remote != expected
                    and not _git_success(
                        self.repository_root,
                        "merge-base",
                        "--is-ancestor",
                        observed_remote,
                        expected,
                    )
                ):
                    raise SaveMappingIntegrationError(
                        "remote_publication_pending",
                        "origin/main has advanced outside this exact mapping "
                        "candidate. Local production was not changed; an outcome "
                        "coordinator or agent must reconcile the remote boundary.",
                    )
                if transaction["phase"] != "promoting":
                    transaction = _set_transaction_phase(
                        self.transaction_path,
                        transaction,
                        "promoting",
                    )
                self._acquire_promotion_owner(transaction)
                self._fault("promotion_owner_acquired")
                rollback_tag = self._ensure_rollback_tag(transaction)
                self._fault("rollback_tag_created")
                self._fast_forward_production(transaction)
                main_commit = expected
            else:
                rollback_tag = self._verified_rollback_tag(transaction)
                self._verify_promoted_state(transaction)
                if transaction["phase"] != "published":
                    transaction = _set_transaction_phase(
                        self.transaction_path,
                        transaction,
                        "promoting",
                    )
                try:
                    observed_remote = self._remote_main_commit()
                except SaveMappingIntegrationError:
                    observed_remote = ""
                if (
                    main_commit == expected
                    and not (
                        observed_remote
                        and self._remote_contains(expected, observed_remote)
                    )
                ):
                    self._acquire_promotion_owner(transaction)
                    self._fault("promotion_owner_acquired")
                    rollback_tag = self._ensure_rollback_tag(transaction)
                    self._fault("rollback_tag_created")

            remote_commit = self._publish_promoted_transaction(
                transaction,
                main_commit=main_commit,
            )
            self._verify_promoted_state(transaction)
            if transaction["phase"] != "published":
                transaction = _set_transaction_phase(
                    self.transaction_path,
                    transaction,
                    "published",
                )
            self._fault("remote_published")
            self._release_owned_promotion_owner(transaction)
            self._fault("promotion_owner_released")
            result = _promoted_result(
                transaction,
                rollback_tag=rollback_tag,
                remote_main_commit=remote_commit,
            )
            if self._matching_decode_receipt(transaction):
                self._retire_staging_ref(transaction)
                _remove_transaction(self.transaction_path)
            return result
        finally:
            os.close(descriptor)

    def observe_canonical_decode(
        self,
        snapshot: object,
        *,
        start_evidence: object = None,
    ) -> bool:
        """Record a stable post-promotion decode; failures stay nonblocking."""

        try:
            transaction = _load_transaction(self.transaction_path)
            if transaction is None or transaction["phase"] not in {
                "staged",
                "promoting",
                "published",
            }:
                return False
            expected = transaction["staging"]["expected_commit"]
            if not isinstance(start_evidence, Mapping):
                return False
            acquisition_started_at = _utc_datetime(
                start_evidence.get("acquired_at")
            )
            integration_available_since = _utc_datetime(
                transaction["integration_available_since"]
            )
            if (
                not _is_git_object(start_evidence.get("main_commit"))
                or acquisition_started_at is None
                or integration_available_since is None
                or not _git_success(
                    self.repository_root,
                    "merge-base",
                    "--is-ancestor",
                    expected,
                    str(start_evidence["main_commit"]),
                )
                or acquisition_started_at < integration_available_since
            ):
                return False
            main_commit = _git_output(
                self.repository_root,
                "rev-parse",
                "refs/heads/main",
            )
            if not _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                expected,
                main_commit,
            ):
                return False
            if getattr(snapshot, "shape_valid", False) is not True:
                return False
            observed_fingerprint = str(
                getattr(snapshot, "canonical_mapping_fingerprint", "") or ""
            )
            if observed_fingerprint != transaction["canonical_mapping_fingerprint"]:
                return False
            identity = transaction["mapping_identity"]
            if (
                str(getattr(snapshot, "mapping_id", "") or "")
                != identity["mapping_id"]
                or str(getattr(snapshot, "mapping_authority_id", "") or "")
                != identity["authority_mapping_id"]
                or str(getattr(snapshot, "mapping_structural_id", "") or "")
                != identity["structural_mapping_id"]
            ):
                return False
            for target in _transaction_targets(transaction):
                _metadata, content = _read_regular_file(
                    _target_path(self.repository_root, target.path)
                )
                if hashlib.sha256(content).hexdigest() != target.after_sha256:
                    return False
            unsigned = {
                "candidate_record_id": transaction["candidate_record_id"],
                "integration_commit": expected,
                "canonical_mapping_fingerprint": transaction[
                    "canonical_mapping_fingerprint"
                ],
                "snapshot_mapping_fingerprint": observed_fingerprint,
                "snapshot_fingerprint": str(
                    getattr(snapshot, "source_sha256", "") or ""
                ),
                "acquisition_started_at": acquisition_started_at.isoformat(),
                "acquisition_main_commit": str(start_evidence["main_commit"]),
                "captured_at": str(getattr(snapshot, "captured_at", "") or ""),
            }
            receipt = {
                "schema_version": _RECEIPT_SCHEMA_VERSION,
                "receipt_id": fingerprint_json(unsigned),
                **unsigned,
            }
            appended = self.decode_receipts.append_once(receipt)
            # The receipt is the durable post-deployment proof.  Retire the
            # matching lifecycle journal only after its append/fsync succeeds,
            # and only while holding the same integration coordination lock.
            # Lock contention is harmless: the next explicit integration will
            # reap a completed journal before doing any new work.
            try:
                descriptor = self._acquire_lock()
            except SaveMappingIntegrationError:
                return appended
            try:
                current = _load_transaction(self.transaction_path)
                if (
                    current is not None
                    and current["candidate_record_id"]
                    == transaction["candidate_record_id"]
                    and current["staging"]["expected_commit"] == expected
                    and current["phase"] == "published"
                    and self._matching_decode_receipt(current)
                ):
                    self._release_owned_promotion_owner(current)
                    self._retire_staging_ref(current)
                    _remove_transaction(self.transaction_path)
            finally:
                os.close(descriptor)
            return appended
        except Exception:
            return False

    def _apply_transaction(
        self,
        transaction: Mapping[str, Any],
        *,
        idempotent: bool,
    ) -> dict[str, Any]:
        if self._transaction_git_lock_present(transaction):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "Git lock artifacts remain; inspect them before retrying or "
                "changing repository state.",
            )
        self._verify_transaction_binding(transaction)
        base_commit = transaction["repository"]["base_commit"]
        expected_commit = transaction["staging"]["expected_commit"]
        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        staged_commit = self._staging_ref_commit()

        if staged_commit == expected_commit:
            if transaction["phase"] == "commit_ready":
                try:
                    with self.candidate_store.locked_records() as candidate_records:
                        self._validate_locked_candidate(
                            transaction,
                            candidate_records,
                        )
                except PlayerSaveMappingCandidateError as exc:
                    raise SaveMappingIntegrationError(
                        _proposal_error_code(exc),
                        _proposal_error_message(exc),
                    ) from exc
            self._verify_staged_state(transaction)
            if transaction["phase"] == "commit_ready":
                transaction = _set_transaction_phase(
                    self.transaction_path,
                    transaction,
                    "staged",
                )
            return _integration_result(
                transaction,
                idempotent=True,
                promoted=_git_success(
                    self.repository_root,
                    "merge-base",
                    "--is-ancestor",
                    expected_commit,
                    main_commit,
                ),
            )

        if transaction["phase"] in {"staged", "promoting", "published"}:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The private staging ref no longer points to the exact durable "
                "candidate; no ref was changed automatically.",
            )
        if staged_commit is not None:
            raise SaveMappingIntegrationError(
                "staging_ref_occupied",
                "The private staging ref contains another candidate; inspect and "
                "retire it before continuing.",
            )
        if main_commit != base_commit:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "main moved outside the reviewed staging transaction; no ref was "
                "changed automatically.",
            )
        if _git_status(self.repository_root):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The production worktree changed after review; no ref was changed.",
            )
        self._verify_commit(
            expected_commit,
            base_commit=base_commit,
            targets=_transaction_targets(transaction),
            commit_message=transaction["commit_message"],
        )
        try:
            with self.candidate_store.locked_records() as candidate_records:
                self._validate_locked_candidate(transaction, candidate_records)
                self._fault("before_staging_ref_update")
                self._verify_transaction_binding(transaction)
                if _git_status(self.repository_root):
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "The production worktree changed immediately before "
                        "staging; no ref was changed.",
                    )
                if (
                    _git_output(
                        self.repository_root,
                        "rev-parse",
                        "refs/heads/main",
                    )
                    != base_commit
                    or self._staging_ref_commit() is not None
                ):
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "main or the private staging ref moved immediately before "
                        "staging; no ref was changed.",
                    )
                try:
                    result = _git_mutate(
                        self.repository_root,
                        "update-ref",
                        "-m",
                        "stage reviewed save-mapping candidate",
                        "--stdin",
                        input_bytes=(
                            f"verify refs/heads/main {base_commit}\n"
                            f"create {SAVE_MAPPING_STAGING_REF} "
                            f"{expected_commit}\n"
                        ).encode("ascii"),
                        check=False,
                    )
                except SaveMappingIntegrationError:
                    result = subprocess.CompletedProcess([], 1, b"", b"")
                observed_main = _git_output(
                    self.repository_root,
                    "rev-parse",
                    "refs/heads/main",
                )
                observed_staged = self._staging_ref_commit()
                if observed_staged != expected_commit:
                    if (
                        observed_staged is None
                        and observed_main == base_commit
                        and not _git_status(self.repository_root)
                        and not self._transaction_git_lock_present(transaction)
                    ):
                        raise SaveMappingIntegrationError(
                            "staging_ref_update_failed",
                            "The verified staging-ref transaction was not applied; "
                            "main and its worktree remain unchanged at the reviewed "
                            "base. Refresh before retrying once.",
                        )
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "The main/staging ref transaction could not be proved exact.",
                    )
                self._fault("staging_ref_updated")
                if result.returncode != 0:
                    # The observed exact ref is authoritative after a lost or
                    # late command status.
                    idempotent = True
        except PlayerSaveMappingCandidateError as exc:
            raise SaveMappingIntegrationError(
                _proposal_error_code(exc),
                _proposal_error_message(exc),
            ) from exc
        self._verify_transaction_binding(transaction)
        self._verify_staged_state(transaction)
        transaction = _set_transaction_phase(
            self.transaction_path,
            transaction,
            "staged",
        )
        self._fault("transaction_staged")
        return _integration_result(
            transaction,
            idempotent=idempotent,
            promoted=False,
        )

    def _validate_locked_candidate(
        self,
        transaction: Mapping[str, Any],
        candidate_records: Sequence[Mapping[str, Any]],
    ) -> None:
        matches = [
            record
            for record in candidate_records
            if record.get("record_id") == transaction["candidate_record_id"]
        ]
        if len(matches) != 1:
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_record_not_found"
            )
        self._require_candidate_not_dismissed(matches[0])
        self._require_routine_candidate_records(matches[0], candidate_records)

    def _staging_ref_commit(self) -> Optional[str]:
        result = _git_output(
            self.repository_root,
            "for-each-ref",
            "--format=%(objectname)",
            SAVE_MAPPING_STAGING_REF,
        ).strip()
        if not result:
            return None
        if not _is_git_object(result):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The private save-mapping staging ref is invalid.",
            )
        return result

    def _retire_staging_ref(self, transaction: Mapping[str, Any]) -> None:
        expected = transaction["staging"]["expected_commit"]
        observed = self._staging_ref_commit()
        if observed is None:
            return
        if observed != expected:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The private staging ref moved to another object and was not retired.",
            )
        result = _git_mutate(
            self.repository_root,
            "update-ref",
            "-d",
            SAVE_MAPPING_STAGING_REF,
            expected,
            check=False,
        )
        remaining = self._staging_ref_commit()
        if remaining is None:
            return
        if remaining != expected:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The private staging ref changed while retirement was attempted.",
            )
        raise SaveMappingIntegrationError(
            "staging_ref_update_failed",
            "The completed private staging ref could not be retired.",
        )

    def _transaction_git_lock_present(
        self,
        transaction: Mapping[str, Any],
    ) -> bool:
        """Detect ordinary Git crash artifacts without removing any of them."""

        try:
            for git_path in (
                "index",
                "HEAD",
                transaction["staging"]["ref"],
                "refs/heads/main",
                "packed-refs",
            ):
                resolved = Path(
                    _git_output(
                        self.repository_root,
                        "rev-parse",
                        "--git-path",
                        git_path,
                    )
                )
                if not resolved.is_absolute():
                    resolved = self.repository_root / resolved
                if Path(f"{resolved}.lock").exists():
                    return True
        except (OSError, SaveMappingIntegrationError):
            return True
        return False

    def _verify_staged_state(self, transaction: Mapping[str, Any]) -> None:
        base_commit = transaction["repository"]["base_commit"]
        expected_commit = transaction["staging"]["expected_commit"]
        self._verify_commit(
            expected_commit,
            base_commit=base_commit,
            targets=_transaction_targets(transaction),
            commit_message=transaction["commit_message"],
        )
        if self._staging_ref_commit() != expected_commit:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The private staging ref no longer points to the exact candidate.",
            )
        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        if main_commit != base_commit:
            if not _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                expected_commit,
                main_commit,
            ):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Production moved without containing the staged commit.",
                )
            if not self._ref_targets_match(transaction, main_commit):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Production contains the staged commit, but its exact canonical "
                    "targets were superseded.",
                )
            record = self.candidate_store.get(transaction["candidate_record_id"])
            main_match = mapping_candidate_record_status(
                record,
                repository_root=self.repository_root,
            )
            if main_match.get("state") != "integrated":
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Production contains the staged commit, but its current "
                    "canonical mapping no longer owns the candidate.",
                )
        if not self._ref_targets_match(transaction, expected_commit):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The staged commit no longer contains its exact canonical targets.",
            )
        if (
            self._canonical_mapping_fingerprint_at_commit(
                expected_commit,
                transaction["mapping_identity"],
            )
            != transaction["canonical_mapping_fingerprint"]
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The staged canonical mapping set failed its reviewed invariants.",
            )
        if _git_status(self.repository_root):
            raise SaveMappingIntegrationError(
                "production_worktree_dirty",
                "Production changed after staging. Automatic integration will "
                "retry after the worktree is clean; ask an agent if it persists.",
            )
        self._verify_transaction_binding(transaction)

    def _verify_promoted_state(self, transaction: Mapping[str, Any]) -> None:
        """Verify a locally integrated candidate without requiring its ref."""

        base_commit = transaction["repository"]["base_commit"]
        expected = transaction["staging"]["expected_commit"]
        self._verify_commit(
            expected,
            base_commit=base_commit,
            targets=_transaction_targets(transaction),
            commit_message=transaction["commit_message"],
        )
        staged_commit = self._staging_ref_commit()
        if staged_commit not in {None, expected}:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The private staging ref moved to another object.",
            )
        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        if not _git_success(
            self.repository_root,
            "merge-base",
            "--is-ancestor",
            expected,
            main_commit,
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "Production does not contain the exact staged mapping commit.",
            )
        if not self._ref_targets_match(transaction, main_commit):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "Production superseded the staged canonical mapping targets.",
            )
        if (
            self._canonical_mapping_fingerprint_at_commit(
                expected,
                transaction["mapping_identity"],
            )
            != transaction["canonical_mapping_fingerprint"]
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The promoted mapping set differs from the reviewed invariants.",
            )
        record = self.candidate_store.get(transaction["candidate_record_id"])
        if mapping_candidate_record_status(
            record,
            repository_root=self.repository_root,
        ).get("state") != "integrated":
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "Production contains the commit but no longer owns its mapping.",
            )
        if _git_status(self.repository_root):
            raise SaveMappingIntegrationError(
                "production_worktree_dirty",
                "Production is not clean after local mapping promotion.",
            )
        self._verify_transaction_binding(transaction)

    @staticmethod
    def _promotion_owner_message(transaction: Mapping[str, Any]) -> str:
        return f"save-mapping promotion {transaction['transaction_id']}"

    def _promotion_owner_commit(self) -> Optional[str]:
        value = _git_output(
            self.repository_root,
            "for-each-ref",
            "--format=%(objectname)",
            PROMOTION_OWNER_REF,
        ).strip()
        if not value:
            return None
        if not _is_git_object(value):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The global promotion-owner ref is invalid.",
            )
        return value

    def _promotion_owner_reflog_message(self) -> str:
        return _git_output(
            self.repository_root,
            "reflog",
            "show",
            "-1",
            "--format=%gs",
            PROMOTION_OWNER_REF,
        ).strip()

    def _acquire_promotion_owner(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        expected = transaction["staging"]["expected_commit"]
        message = self._promotion_owner_message(transaction)
        observed = self._promotion_owner_commit()
        if observed == expected and self._promotion_owner_reflog_message() == message:
            return
        if observed is not None:
            raise SaveMappingIntegrationError(
                "promotion_owner_busy",
                "Another production promotion owns the mutable repository "
                "transaction. Automatic integration will retry after it closes.",
            )
        result = _git_mutate(
            self.repository_root,
            "update-ref",
            "--create-reflog",
            "-m",
            message,
            PROMOTION_OWNER_REF,
            expected,
            _ZERO_OBJECT_ID,
            check=False,
        )
        observed = self._promotion_owner_commit()
        if (
            observed == expected
            and self._promotion_owner_reflog_message() == message
        ):
            return
        if result.returncode != 0 or observed is not None:
            raise SaveMappingIntegrationError(
                "promotion_owner_busy",
                "Another production promotion won the ownership boundary. "
                "Automatic integration will retry after it closes.",
            )
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The global promotion-owner acquisition could not be proved exact.",
        )

    def _release_owned_promotion_owner(
        self,
        transaction: Mapping[str, Any],
    ) -> bool:
        expected = transaction["staging"]["expected_commit"]
        if self._promotion_owner_commit() != expected:
            return False
        if (
            self._promotion_owner_reflog_message()
            != self._promotion_owner_message(transaction)
        ):
            return False
        _git_mutate(
            self.repository_root,
            "update-ref",
            "-d",
            PROMOTION_OWNER_REF,
            expected,
            check=False,
        )
        remaining = self._promotion_owner_commit()
        if remaining is None:
            return True
        if remaining != expected:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The promotion-owner ref changed during guarded release.",
            )
        raise SaveMappingIntegrationError(
            "promotion_owner_release_failed",
            "Published integration is complete, but its exact promotion-owner "
            "ref could not be released.",
        )

    def _promotion_owner_is_owned(
        self,
        transaction: Mapping[str, Any],
    ) -> bool:
        expected = transaction["staging"]["expected_commit"]
        return bool(
            self._promotion_owner_commit() == expected
            and self._promotion_owner_reflog_message()
            == self._promotion_owner_message(transaction)
        )

    @staticmethod
    def _rollback_tag_name(transaction: Mapping[str, Any]) -> str:
        return (
            "production-before-save-mapping-"
            f"{transaction['transaction_id'][:12]}-"
            f"{transaction['repository']['base_commit'][:7]}"
        )

    def _ensure_rollback_tag(self, transaction: Mapping[str, Any]) -> str:
        tag = self._rollback_tag_name(transaction)
        base_commit = transaction["repository"]["base_commit"]
        inspected = _git_mutate(
            self.repository_root,
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/tags/{tag}^{{commit}}",
            check=False,
        )
        observed = inspected.stdout.decode("ascii", "replace").strip()
        if observed:
            if observed != base_commit:
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "The deterministic mapping rollback tag names another commit.",
                )
            return tag
        result = _git_mutate(
            self.repository_root,
            "tag",
            "-a",
            tag,
            "-m",
            (
                "Production before automatic save-mapping integration "
                f"{transaction['candidate_record_id'][:12]}"
            ),
            base_commit,
            check=False,
        )
        inspected = _git_mutate(
            self.repository_root,
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/tags/{tag}^{{commit}}",
            check=False,
        )
        observed = inspected.stdout.decode("ascii", "replace").strip()
        if observed == base_commit:
            return tag
        if result.returncode != 0 and not observed:
            raise SaveMappingIntegrationError(
                "rollback_tag_failed",
                "The rollback tag could not be created; main was not changed.",
            )
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The rollback tag result could not be proved exact.",
        )

    def _verified_rollback_tag(
        self,
        transaction: Mapping[str, Any],
    ) -> Optional[str]:
        tag = self._rollback_tag_name(transaction)
        inspected = _git_mutate(
            self.repository_root,
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/tags/{tag}^{{commit}}",
            check=False,
        )
        observed = inspected.stdout.decode("ascii", "replace").strip()
        if not observed:
            return None
        if observed != transaction["repository"]["base_commit"]:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The deterministic mapping rollback tag names another commit.",
            )
        return tag

    def _remote_main_commit(self) -> str:
        try:
            result = _git_mutate(
                self.repository_root,
                "ls-remote",
                "--refs",
                "origin",
                "refs/heads/main",
                check=False,
            )
        except SaveMappingIntegrationError as exc:
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "origin/main could not be read. Local promotion is durable and "
                "automatic publication will retry; ask an agent if this persists.",
            ) from exc
        if result.returncode != 0:
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "origin/main could not be read. Local promotion is durable and "
                "automatic publication will retry; ask an agent if this persists.",
            )
        lines = [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line]
        if len(lines) != 1:
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "origin/main is absent or ambiguous. Local promotion is durable; "
                "ask an agent to inspect publication if this persists.",
            )
        object_id, separator, reference = lines[0].partition("\t")
        if (
            not separator
            or reference != "refs/heads/main"
            or not _is_git_object(object_id)
        ):
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "origin/main returned an invalid identity. Local promotion is "
                "durable; ask an agent to inspect publication.",
            )
        return object_id

    def _remote_contains(self, expected: str, remote_commit: str) -> bool:
        return remote_commit == expected or _git_success(
            self.repository_root,
            "merge-base",
            "--is-ancestor",
            expected,
            remote_commit,
        )

    def _publish_promoted_transaction(
        self,
        transaction: Mapping[str, Any],
        *,
        main_commit: str,
    ) -> str:
        expected = transaction["staging"]["expected_commit"]
        try:
            remote_commit = self._remote_main_commit()
        except SaveMappingIntegrationError:
            remote_commit = ""
        if remote_commit and self._remote_contains(expected, remote_commit):
            return remote_commit
        if main_commit != expected:
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "Production contains this mapping inside a larger coordinated "
                "outcome, but origin/main does not contain it yet. The outcome "
                "coordinator must publish current main; automatic verification "
                "will then close this mapping transaction.",
            )
        try:
            push = _git_mutate(
                self.repository_root,
                "push",
                "origin",
                f"{expected}:refs/heads/main",
                check=False,
            )
        except SaveMappingIntegrationError as exc:
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "The exact non-forcing origin/main publication could not run. "
                "Local promotion is durable and automatic publication will retry; "
                "ask an agent if this persists.",
            ) from exc
        try:
            remote_commit = self._remote_main_commit()
        except SaveMappingIntegrationError as exc:
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "The exact non-forcing origin/main publication could not be "
                "verified. Automatic publication will retry; ask an agent if "
                "this persists.",
            ) from exc
        if self._remote_contains(expected, remote_commit):
            return remote_commit
        if push.returncode != 0:
            raise SaveMappingIntegrationError(
                "remote_publication_pending",
                "origin/main rejected the exact non-forcing publication. Local "
                "promotion remains durable; an agent must reconcile the remote.",
            )
        raise SaveMappingIntegrationError(
            "remote_publication_pending",
            "The remote publication response could not be proved exact. Local "
            "promotion remains durable for automatic recovery.",
        )

    def _verify_transaction_binding(self, transaction: Mapping[str, Any]) -> None:
        if transaction["staging"]["ref"] != SAVE_MAPPING_STAGING_REF:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The durable private staging ref identity changed.",
            )
        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        productions = [
            item
            for item in _linked_worktrees(self.repository_root)
            if item.branch == "main"
            and item.path == self.repository_root
            and item.head == main_commit
            and not item.locked
            and not item.prunable
        ]
        if len(productions) != 1:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The bound production main worktree identity changed.",
            )
        self._validate_target_scope(
            self.repository_root,
            _transaction_targets(transaction),
        )

    def _build_commit(
        self,
        repository_root: Path,
        *,
        base_commit: str,
        targets: Sequence[_PreparedTarget],
        commit_message: str,
    ) -> str:
        name = _git_config_value(repository_root, "user.name")
        email = _git_config_value(repository_root, "user.email")
        if not name or not email:
            raise SaveMappingIntegrationError(
                "git_identity_unavailable",
                "The repository Git author identity is unavailable; nothing was committed.",
            )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, index_name = tempfile.mkstemp(
            prefix=".save-mapping-index-",
            dir=self.lock_path.parent,
        )
        os.close(descriptor)
        index_path = Path(index_name)
        index_path.unlink()
        environment = {"GIT_INDEX_FILE": str(index_path)}
        try:
            _git_mutate(repository_root, "read-tree", base_commit, env=environment)
            for target in targets:
                if not target.changed:
                    continue
                blob = _git_mutate(
                    repository_root,
                    "hash-object",
                    "-w",
                    "--stdin",
                    input_bytes=target.after,
                ).stdout.decode("ascii").strip()
                mode = "100755" if target.mode & 0o111 else "100644"
                _git_mutate(
                    repository_root,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{mode},{blob},{target.path}",
                    env=environment,
                )
            tree = _git_mutate(
                repository_root,
                "write-tree",
                env=environment,
            ).stdout.decode("ascii").strip()
            commit = _git_mutate(
                repository_root,
                "commit-tree",
                tree,
                "-p",
                base_commit,
                input_bytes=commit_message.encode("utf-8"),
            ).stdout.decode("ascii").strip()
        finally:
            index_path.unlink(missing_ok=True)
        if not _is_git_object(commit):
            raise SaveMappingIntegrationError(
                "git_commit_failed",
                "Git did not create a valid canonical integration commit.",
            )
        return commit

    def _verify_commit(
        self,
        commit: str,
        *,
        base_commit: str,
        targets: Sequence[_PreparedTarget],
        commit_message: str,
    ) -> None:
        if _git_output(self.repository_root, "rev-parse", f"{commit}^") != base_commit:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The generated integration commit has an unexpected parent.",
            )
        changed = tuple(
            item.decode("utf-8", "surrogateescape")
            for item in _git_bytes_mutate(
                self.repository_root,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                commit,
            ).split(b"\0")
            if item
        )
        expected_paths = tuple(sorted(target.path for target in targets if target.changed))
        if tuple(sorted(changed)) != expected_paths:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The generated integration commit changed an unexpected path.",
            )
        for target in targets:
            content = _git_bytes_mutate(
                self.repository_root,
                "show",
                f"{commit}:{target.path}",
            )
            expected = target.after if target.changed else target.before
            if content != expected:
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "A generated integration commit blob differs from review.",
                )
            entry = _git_output(
                self.repository_root,
                "ls-tree",
                commit,
                "--",
                target.path,
            )
            expected_mode = "100755" if target.mode & 0o111 else "100644"
            if not entry.startswith(f"{expected_mode} blob "):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "A generated integration commit mode differs from review.",
                )
        observed_message = _git_bytes_mutate(
            self.repository_root,
            "show",
            "-s",
            "--format=%B",
            commit,
        ).decode("utf-8").rstrip("\n")
        if observed_message != commit_message.rstrip("\n"):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The generated integration commit provenance differs from review.",
            )

    def _validate_target_scope(
        self,
        repository_root: Path,
        targets: Sequence[_PreparedTarget],
    ) -> None:
        allowed_parent = Path("config/player_save_versions")
        for target in targets:
            relative = Path(target.path)
            if (
                relative.parent != allowed_parent
                or relative.suffix != ".json"
                or target.mode & 0o111
            ):
                raise SaveMappingIntegrationError(
                    "proposal_target_invalid",
                    "Routine integration is limited to canonical mapping JSON files.",
                )
            if not _git_success(
                repository_root,
                "ls-files",
                "--error-unmatch",
                "--",
                target.path,
            ):
                raise SaveMappingIntegrationError(
                    "proposal_target_invalid",
                    "A canonical mapping target is not tracked by Git.",
                )

    @staticmethod
    def _canonical_mapping_fingerprint(
        repository_root: Path,
        targets: Sequence[_PreparedTarget],
        identity: Mapping[str, str],
    ) -> str:
        overrides = {target.path: target.after for target in targets}
        mappings: dict[str, Mapping[str, Any]] = {}
        directory = repository_root / "config/player_save_versions"
        try:
            for path in sorted(directory.glob("*.json")):
                relative = path.relative_to(repository_root).as_posix()
                content = overrides.get(relative, path.read_bytes())
                mapping = json.loads(content)
                mappings[str(mapping["mapping_id"])] = mapping
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            raise SaveMappingIntegrationError(
                "canonical_mapping_validation_failed",
                "The prospective canonical mapping set could not be validated.",
            ) from exc
        return canonical_mapping_set_fingerprint(
            mappings,
            authority_mapping_id=identity["authority_mapping_id"],
            structural_mapping_id=identity["structural_mapping_id"],
        )

    def _canonical_mapping_fingerprint_at_commit(
        self,
        commit: str,
        identity: Mapping[str, str],
    ) -> str:
        mappings: dict[str, Mapping[str, Any]] = {}
        try:
            paths = [
                item.decode("utf-8", "surrogateescape")
                for item in _git_bytes_mutate(
                    self.repository_root,
                    "ls-tree",
                    "-r",
                    "--name-only",
                    "-z",
                    commit,
                    "--",
                    "config/player_save_versions",
                ).split(b"\0")
                if item
            ]
            for relative in sorted(paths):
                path = Path(relative)
                if (
                    path.parent != Path("config/player_save_versions")
                    or path.suffix != ".json"
                ):
                    continue
                mapping = json.loads(
                    _git_bytes_mutate(
                        self.repository_root,
                        "show",
                        f"{commit}:{relative}",
                    )
                )
                mappings[str(mapping["mapping_id"])] = mapping
        except (
            SaveMappingIntegrationError,
            UnicodeDecodeError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            raise SaveMappingIntegrationError(
                "canonical_mapping_validation_failed",
                "The staged canonical mapping set could not be validated.",
            ) from exc
        return canonical_mapping_set_fingerprint(
            mappings,
            authority_mapping_id=identity["authority_mapping_id"],
            structural_mapping_id=identity["structural_mapping_id"],
        )

    def _base_state(self) -> dict[str, Any]:
        worktrees = _linked_worktrees(self.repository_root)
        production = next(
            (item for item in worktrees if item.path == self.repository_root),
            None,
        )
        if production is None or production.branch != "main":
            raise SaveMappingIntegrationError(
                "production_role_invalid",
                "The control surface repository root is not the linked main worktree.",
            )
        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        staged_commit = self._staging_ref_commit()
        production_clean = not _git_status(self.repository_root)
        identity_available = bool(
            _git_config_value(self.repository_root, "user.name")
            and _git_config_value(self.repository_root, "user.email")
        )
        available = True
        code = ""
        reason = ""
        if production.locked or production.prunable:
            available = False
            code = "production_worktree_unavailable"
            reason = "The production worktree is locked or prunable."
        elif production.head != main_commit:
            available = False
            code = "production_head_changed"
            reason = "The production worktree is not at the current main tip."
        elif not production_clean:
            available = False
            code = "production_worktree_dirty"
            reason = "Production has tracked, staged, or untracked changes."
        elif staged_commit is not None:
            available = False
            code = "staging_ref_occupied"
            reason = (
                "A private save-mapping candidate is already staged; finish or "
                "inspect it before reviewing another."
            )
        elif not identity_available:
            available = False
            code = "git_identity_unavailable"
            reason = "The repository Git author name or email is unavailable."
        return {
            "integration_available": available,
            "code": code,
            "reason": reason,
            "main_commit": main_commit,
            "staging_ref": SAVE_MAPPING_STAGING_REF,
            "staged_commit": staged_commit,
            "production_clean": production_clean,
        }

    def _lightweight_transaction_state(
        self,
        transaction: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Read only state needed by a persistent lifecycle projection."""

        return {
            "integration_available": False,
            "code": "",
            "reason": "",
            "main_commit": _git_output(
                self.repository_root,
                "rev-parse",
                "refs/heads/main",
            ),
            "staging_ref": transaction["staging"]["ref"],
            "staged_commit": self._staging_ref_commit(),
            "production_clean": not _git_status(self.repository_root),
        }

    @staticmethod
    def _public_repository(base: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "main_commit": base["main_commit"],
            "staging_ref": base["staging_ref"],
            "staged_commit": base["staged_commit"],
            "production_clean": base["production_clean"],
            "integration_available": base["integration_available"],
            "code": base["code"],
            "reason": base["reason"],
        }

    def _transaction_lifecycle(
        self,
        transaction: Mapping[str, Any],
        base: Mapping[str, Any],
        *,
        main_candidate: Optional[Mapping[str, Any]] = None,
    ) -> tuple[str, str]:
        expected = transaction["staging"]["expected_commit"]
        base_commit = transaction["repository"]["base_commit"]
        main_commit = str(base["main_commit"])
        staged_commit = base.get("staged_commit")
        main_contains = _git_success(
            self.repository_root,
            "merge-base",
            "--is-ancestor",
            expected,
            main_commit,
        )
        if transaction["phase"] == "commit_ready":
            if self._transaction_git_lock_present(transaction):
                return (
                    "integration_unconfirmed",
                    "Git lock artifacts remain; inspect them before retrying or "
                    "changing repository state",
                )
            try:
                self._validate_locked_candidate(
                    transaction,
                    self.candidate_store.list_records(),
                )
            except (OSError, PlayerSaveMappingCandidateError):
                return (
                    "integration_unconfirmed",
                    "Candidate evidence changed or conflicts with the durable "
                    "transaction; route this claim through ordinary development",
                )
            if staged_commit not in {None, expected}:
                return (
                    "integration_unconfirmed",
                    "The private staging ref contains an object outside the durable "
                    "transaction",
                )
            if main_contains:
                if not self._ref_targets_match(transaction, main_commit):
                    return (
                        "integration_unconfirmed",
                        "Production contains superseding canonical target content "
                        "outside the durable transaction",
                    )
                return (
                    "integration_recovery_required",
                    "The staged commit requires one exact recovery verification",
                )
            if main_commit == base_commit and staged_commit in {None, expected}:
                return (
                    "integration_recovery_required",
                    "The reviewed staged commit requires one exact retry to finish",
                )
            return (
                "integration_unconfirmed",
                "Repository refs moved outside the durable staging transaction; "
                "inspect before continuing",
            )

        if main_contains:
            if staged_commit not in {None, expected}:
                return (
                    "integration_unconfirmed",
                    "The private staging ref moved to another object",
                )
            if not self._ref_targets_match(transaction, main_commit):
                return (
                    "integration_unconfirmed",
                    "Production contains the staged commit but its exact canonical "
                    "target content was superseded",
                )
            if main_candidate is None:
                record = self.candidate_store.get(
                    transaction["candidate_record_id"]
                )
                main_candidate = mapping_candidate_record_status(
                    record,
                    repository_root=self.repository_root,
                )
            if main_candidate is None or main_candidate.get("state") != "integrated":
                return (
                    "integration_unconfirmed",
                    "Production contains the staged commit but its current canonical "
                    "mapping no longer owns this candidate",
                )
            if transaction["phase"] != "published":
                return (
                    "remote_publication_pending",
                    f"Production contains {expected[:12]}; automatic origin/main "
                    "publication is pending",
                )
            if self._promotion_owner_is_owned(transaction):
                return (
                    "promotion_cleanup_pending",
                    "Production and origin contain the mapping; automatic "
                    "release of its exact promotion owner is pending",
                )
            if self._matching_decode_receipt(transaction):
                return (
                    "integrated",
                    "production contains the staged commit and a fresh stable "
                    "decode confirmed it",
                )
            return (
                "production_validation_pending",
                f"Production contains {expected[:12]}; awaiting a fresh stable save decode",
            )

        if staged_commit == expected and main_commit == base_commit:
            if not self._ref_targets_match(transaction, expected):
                return (
                    "integration_unconfirmed",
                    "The private staging ref no longer contains the reviewed "
                    "canonical targets",
                )
            return (
                "promotion_pending",
                f"Staged as {expected[:12]}; automatic production promotion "
                "is queued",
            )

        if (
            staged_commit in {None, expected}
            and _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                base_commit,
                main_commit,
            )
        ) and self._ref_targets_match_before(transaction, main_commit):
            return (
                "restaging_required",
                "main advanced after this candidate was staged; review the same "
                "mapping inputs and restage it on the current tip",
            )

        return (
            "integration_unconfirmed",
            "Repository refs moved outside the durable staging transaction; "
            "inspect before continuing",
        )

    def _ref_targets_match(
        self,
        transaction: Mapping[str, Any],
        commit: str,
    ) -> bool:
        try:
            for target in _transaction_targets(transaction):
                content = _git_bytes_mutate(
                    self.repository_root,
                    "show",
                    f"{commit}:{target.path}",
                )
                if hashlib.sha256(content).hexdigest() != target.after_sha256:
                    return False
                entry = _git_output(
                    self.repository_root,
                    "ls-tree",
                    commit,
                    "--",
                    target.path,
                )
                if not entry.startswith("100644 blob "):
                    return False
        except SaveMappingIntegrationError:
            return False
        return True

    def _ref_targets_match_before(
        self,
        transaction: Mapping[str, Any],
        commit: str,
    ) -> bool:
        try:
            for target in _transaction_targets(transaction):
                content = _git_bytes_mutate(
                    self.repository_root,
                    "show",
                    f"{commit}:{target.path}",
                )
                if hashlib.sha256(content).hexdigest() != target.before_sha256:
                    return False
                entry = _git_output(
                    self.repository_root,
                    "ls-tree",
                    commit,
                    "--",
                    target.path,
                )
                if not entry.startswith("100644 blob "):
                    return False
        except SaveMappingIntegrationError:
            return False
        return True

    def _transaction_validated(
        self,
        transaction: Mapping[str, Any],
        base: Mapping[str, Any],
    ) -> bool:
        lifecycle, _reason = self._transaction_lifecycle(transaction, base)
        return lifecycle == "integrated"

    def _matching_decode_receipt(self, transaction: Mapping[str, Any]) -> bool:
        records = [
            record
            for record in self.decode_receipts.list_records()
            if record["candidate_record_id"] == transaction["candidate_record_id"]
            and record["integration_commit"]
            == transaction["staging"]["expected_commit"]
        ]
        if not records:
            return False
        if any(
            record["canonical_mapping_fingerprint"]
            != transaction["canonical_mapping_fingerprint"]
            or record["snapshot_mapping_fingerprint"]
            != transaction["canonical_mapping_fingerprint"]
            or _utc_datetime(record["acquisition_started_at"]) is None
            or _utc_datetime(record["acquisition_started_at"])
            < _utc_datetime(transaction["integration_available_since"])
            or not _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                transaction["staging"]["expected_commit"],
                record["acquisition_main_commit"],
            )
            for record in records
        ):
            raise SaveMappingIntegrationError(
                "decode_receipt_conflict",
                "A canonical decode receipt does not match the deployed "
                "integration identity.",
            )
        return True

    def _public_transaction(
        self,
        transaction: Optional[Mapping[str, Any]],
        base: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        if transaction is None:
            return None
        lifecycle, reason = self._transaction_lifecycle(transaction, base)
        return {
            "candidate_record_id": transaction["candidate_record_id"],
            "reviewed_proposal_fingerprint": transaction[
                "reviewed_proposal_fingerprint"
            ],
            "phase": transaction["phase"],
            "staging_ref": transaction["staging"]["ref"],
            "staged_commit": transaction["staging"]["expected_commit"],
            "state": lifecycle,
            "reason": reason,
            "recovery_required": lifecycle in {
                "integration_recovery_required",
                "integration_unconfirmed",
                "restaging_required",
            },
        }

    def _acquire_lock(self) -> int:
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise SaveMappingIntegrationError(
                "integration_lock_unavailable",
                "Canonical integration coordination is unavailable.",
            ) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fchmod(descriptor, 0o600)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise SaveMappingIntegrationError(
                "integration_busy",
                "Another canonical integration is in progress; refresh before retrying.",
            ) from exc
        except OSError as exc:
            os.close(descriptor)
            raise SaveMappingIntegrationError(
                "integration_lock_unavailable",
                "Canonical integration coordination is unavailable.",
            ) from exc
        return descriptor

    def _acquire_mapping_set_lock(self) -> int:
        """Exclude runtime mapping loads only across the local checkout."""

        descriptor: Optional[int] = None
        try:
            self.mapping_set_lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self.mapping_set_lock_path,
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.fchmod(descriptor, 0o600)
            return descriptor
        except OSError as exc:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise SaveMappingIntegrationError(
                "integration_lock_unavailable",
                "Canonical mapping-file coordination is unavailable.",
            ) from exc

    def _fast_forward_production(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        base_commit = transaction["repository"]["base_commit"]
        expected = transaction["staging"]["expected_commit"]
        descriptor = self._acquire_mapping_set_lock()
        try:
            if (
                _git_output(
                    self.repository_root,
                    "rev-parse",
                    "refs/heads/main",
                )
                != base_commit
                or self._staging_ref_commit() != expected
            ):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Production changed at the exact promotion boundary.",
                )
            if _git_status(self.repository_root):
                raise SaveMappingIntegrationError(
                    "production_worktree_dirty",
                    "Production changed at the exact promotion boundary. "
                    "Automatic integration will retry after the worktree is clean.",
                )
            result = _git_mutate(
                self.repository_root,
                "merge",
                "--ff-only",
                "--no-edit",
                expected,
                check=False,
            )
            observed_main = _git_output(
                self.repository_root,
                "rev-parse",
                "refs/heads/main",
            )
            if result.returncode != 0 or observed_main != expected:
                if observed_main == base_commit and not _git_status(
                    self.repository_root
                ):
                    raise SaveMappingIntegrationError(
                        "local_promotion_failed",
                        "The exact fast-forward was rejected; production "
                        "remains at the reviewed base.",
                    )
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "The local save-mapping promotion could not be proved exact.",
                )
            self._fault("main_promoted")
            self._verify_promoted_state(transaction)
        finally:
            os.close(descriptor)

    def _require_routine_candidate(self, record: Mapping[str, Any]) -> None:
        self._require_candidate_not_dismissed(record)
        self._require_routine_candidate_records(
            record,
            self.candidate_store.list_records(),
        )

    def _require_candidate_not_dismissed(
        self,
        record: Mapping[str, Any],
    ) -> None:
        if self.candidate_dispositions.dismissal_for(record.get("record_id")):
            raise PlayerSaveMappingCandidateError("mapping_candidate_dismissed")

    @staticmethod
    def _require_routine_candidate_records(
        record: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        _require_routine_candidate(record)
        candidate = record["candidate"]
        identity = _candidate_claim_identity(record, include_semantic=False)
        matches = []
        for observed in records:
            observed_candidate = observed["candidate"]
            observed_identity = _candidate_claim_identity(
                observed,
                include_semantic=False,
            )
            if observed_identity == identity:
                matches.append(observed_candidate)
        semantics = {
            (
                str(item.get("semantic_value")),
                (
                    str((item.get("scope") or {}).get("family") or "")
                    if item.get("value_kind") == "module_info_index"
                    else ""
                ),
            )
            for item in matches
            if item.get("semantic_value") is not None
        }
        expected_semantics = {
            (
                str(candidate["semantic_value"]),
                (
                    str((candidate.get("scope") or {}).get("family") or "")
                    if candidate.get("value_kind") == "module_info_index"
                    else ""
                ),
            )
        }
        if (
            len(semantics) != 1
            or semantics != expected_semantics
            or any(item.get("status") != "ready_for_review" for item in matches)
        ):
            raise PlayerSaveMappingCandidateError(
                "mapping_candidate_requires_ordinary_development"
            )

    def _fault(self, transition: str) -> None:
        if self._transaction_fault_hook is not None:
            self._transaction_fault_hook(transition)


def _public_target(target: _PreparedTarget) -> dict[str, Any]:
    return {
        "path": target.path,
        "mapping_id": target.mapping_id,
        "before_sha256": target.before_sha256,
        "after_sha256": target.after_sha256,
        "changed": target.changed,
        "mode": target.mode,
    }


def _machine_verification_result(
    record: Optional[Mapping[str, Any]],
    *,
    eligible: bool,
    code: str,
    reason: str,
) -> dict[str, Any]:
    proof: Optional[dict[str, Any]] = None
    if eligible and record is not None:
        candidate = record["candidate"]
        evidence = record["evidence"]
        workflow = evidence["workflow"]
        proof = {
            "check_id": candidate["check_id"],
            "value_kind": candidate["value_kind"],
            "raw_value": candidate["raw_discriminator"]["value"],
            "semantic_value": candidate["semantic_value"],
            "evidence_strength": evidence["evidence_strength"],
            "pairing_method": evidence["pairing_method"],
            "pre_mutation": evidence["pre_mutation"],
            "game_state": workflow["game_state"],
            "snapshot_fingerprint": evidence["snapshot_fingerprint"],
            "ui_evidence_fingerprint": evidence["ui_evidence_fingerprint"],
            "source_observation_fingerprint": evidence[
                "source_observation_fingerprint"
            ],
            "runtime_session_fingerprint": workflow[
                "runtime_session_fingerprint"
            ],
            "target_generation_fingerprint": workflow[
                "target_generation_fingerprint"
            ],
            "activity_scope_fingerprint": workflow[
                "activity_scope_fingerprint"
            ],
            "active_round_identity_fingerprint": workflow[
                "active_round_identity_fingerprint"
            ],
            "boundary_fingerprint": workflow["boundary_fingerprint"],
        }
    return {
        "capability": SAVE_MAPPING_MACHINE_VERIFICATION_CAPABILITY,
        "eligible": eligible,
        "code": code,
        "reason": reason,
        "proof": proof,
    }


def _automatic_integration_next_action(
    item: Mapping[str, Any],
    *,
    blocker_reason: str = "",
) -> str:
    state = str(item.get("state") or "")
    if state == "production_validation_pending":
        return (
            "No review is needed. Production and origin contain the verified "
            "mapping; a fresh stable save decode will close the transaction."
        )
    if state == "remote_publication_pending":
        return (
            "No review is needed. Local promotion completed and automatic "
            "origin/main publication is being reconciled. If it persists, ask "
            "an agent to complete the reported publication boundary."
        )
    if state == "promotion_cleanup_pending":
        return (
            "No review is needed. Production and origin contain the mapping; "
            "automatic promotion-owner cleanup is being retried. If it "
            "persists, copy the agent request."
        )
    if state in {"integration_unconfirmed", "restaging_required"}:
        return (
            "The evidence needs no review, but its repository transaction needs "
            "an agent to recover the exact reported boundary."
        )
    if blocker_reason:
        return (
            "No semantic review is needed, but automatic integration is blocked: "
            f"{blocker_reason} Automatic reconciliation will retry where safe; "
            "copy the agent request if the blocker persists."
        )
    return (
        "No review is needed. The exact Game Over/save proof is verified and "
        "automatic integration is queued."
    )


def _automatic_reconciliation_idle(*, reason: str = "") -> dict[str, Any]:
    return {
        "capability": SAVE_MAPPING_AUTOMATIC_PROMOTION_CAPABILITY,
        "needed": False,
        "action": "idle",
        "candidate_record_id": None,
        "reason": reason,
    }


def _dismissal_result(
    disposition: Mapping[str, Any],
    *,
    changed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capability": SAVE_MAPPING_DISPOSITION_CAPABILITY,
        "operation": "dismiss",
        "disposition": "dismissed",
        "candidate_record_id": disposition["candidate_record_id"],
        "event_id": disposition["event_id"],
        "recorded_at": disposition["recorded_at"],
        "changed": changed,
        "evidence_preserved": True,
    }


def _integration_result(
    transaction: Mapping[str, Any],
    *,
    idempotent: bool,
    promoted: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
        "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
        "operation": "stage",
        "disposition": "staged_for_promotion",
        "idempotent": idempotent,
        "candidate_record_id": transaction["candidate_record_id"],
        "reviewed_proposal_fingerprint": transaction[
            "reviewed_proposal_fingerprint"
        ],
        "base_commit": transaction["repository"]["base_commit"],
        "staging_ref": transaction["staging"]["ref"],
        "staged_commit": transaction["staging"]["expected_commit"],
        "committed": True,
        "staged": True,
        "promoted": promoted,
        "mapping_invariants": "passed",
        "promotion_validation": "pending",
        "targets": [
            {
                key: item[key]
                for key in (
                    "path",
                    "mapping_id",
                    "before_sha256",
                    "after_sha256",
                    "changed",
                    "mode",
                )
            }
            for item in transaction["targets"]
        ],
    }


def _promotion_agent_prompt(
    transaction: Mapping[str, Any],
    *,
    code: str,
    reason: str,
) -> str:
    return (
        "Please finish TheTower automatic save-mapping integration "
        f"{transaction['candidate_record_id']}.\n"
        f"Staged commit: {transaction['staging']['expected_commit']}\n"
        f"Transaction: {transaction['transaction_id']}\n"
        f"Blocker: {code}: {reason}\n"
        "Inspect the durable transaction, private staging ref, production main, "
        "promotion-owner ref, rollback tag, and origin/main. Preserve the exact "
        "candidate and finish or recover the guarded publication boundary; do "
        "not create a second mapping commit or repeat unrelated validation."
    )


def _promotion_queued_result(
    transaction: Mapping[str, Any],
    *,
    repository_root: Path,
    code: str,
    reason: str,
) -> dict[str, Any]:
    result = _integration_result(
        transaction,
        idempotent=True,
        promoted=_git_success(
            repository_root,
            "merge-base",
            "--is-ancestor",
            transaction["staging"]["expected_commit"],
            "refs/heads/main",
        ),
    )
    published = transaction["phase"] == "published"
    result.update(
        operation="integrate",
        disposition="promotion_queued",
        published=published,
        automatic_retry=True,
        agent_required=code in {
            "production_worktree_dirty",
            "remote_publication_pending",
        },
        code=code,
        reason=reason,
        next_action=(
            "Production and origin contain the mapping. Automatic reconciliation "
            "will retry its exact transaction cleanup; copy the agent request if "
            "that state persists."
            if published
            else "Automatic reconciliation will retry. If this state persists, "
            "copy the agent request shown with this result."
        ),
        agent_review_prompt=_promotion_agent_prompt(
            transaction,
            code=code,
            reason=reason,
        ),
    )
    return result


def _promoted_result(
    transaction: Mapping[str, Any],
    *,
    rollback_tag: Optional[str],
    remote_main_commit: str,
) -> dict[str, Any]:
    result = _integration_result(
        transaction,
        idempotent=False,
        promoted=True,
    )
    result.update(
        operation="integrate",
        disposition="promoted",
        published=True,
        automatic_retry=False,
        agent_required=False,
        code="",
        reason="",
        next_action=(
            "Production and origin contain the mapping. A fresh stable save "
            "decode will retire the durable integration transaction."
        ),
        agent_review_prompt="",
        rollback_tag=rollback_tag,
        remote_main_commit=remote_main_commit,
    )
    return result


def _git_command(repository_root: Path, *arguments: str) -> list[str]:
    return [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "tag.gpgSign=false",
        "-C",
        str(repository_root),
        *arguments,
    ]


def _git_mutate(
    repository_root: Path,
    *arguments: str,
    input_bytes: Optional[bytes] = None,
    env: Optional[Mapping[str, str]] = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            _git_command(repository_root, *arguments),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_EXTERNAL_DIFF": "",
                **dict(env or {}),
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SaveMappingIntegrationError(
            "git_commit_failed",
            "Git could not complete the canonical integration operation.",
        ) from exc
    if check and result.returncode != 0:
        raise SaveMappingIntegrationError(
            "git_commit_failed",
            "Git rejected the canonical integration operation.",
        )
    return result


def _git_config_value(repository_root: Path, key: str) -> str:
    result = _git_mutate(
        repository_root,
        "config",
        "--get",
        key,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace").strip()


def _git_bytes_mutate(repository_root: Path, *arguments: str) -> bytes:
    return _git_mutate(repository_root, *arguments).stdout


def _proposal_error_code(exc: BaseException) -> str:
    message = str(exc)
    if message.startswith("mapping_candidate_"):
        return message
    return "mapping_proposal_unavailable"


def _require_routine_candidate(record: Mapping[str, Any]) -> None:
    candidate = record.get("candidate")
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("status") != "ready_for_review"
    ):
        raise PlayerSaveMappingCandidateError(
            "mapping_candidate_requires_ordinary_development"
        )


def _proposal_error_message(exc: BaseException) -> str:
    messages = {
        "mapping_candidate_not_ready_for_proposal": (
            "This observation is not ready for canonical integration."
        ),
        "mapping_candidate_proposal_requires_exact_mapping": (
            "Exact-version mapping evidence is required before integration."
        ),
        "mapping_candidate_proposal_conflicts_current_file": (
            "The current canonical mapping conflicts with this observation."
        ),
        "mapping_candidate_proposal_already_integrated": (
            "The canonical mapping already contains this observation."
        ),
        "mapping_candidate_target_mapping_missing": (
            "A canonical target mapping is unavailable."
        ),
        "mapping_candidate_target_mapping_invalid": (
            "A canonical target mapping could not be validated."
        ),
        "mapping_candidate_requires_ordinary_development": (
            "Conflicting, ambiguous, or incomplete evidence requires ordinary "
            "development review."
        ),
        "mapping_candidate_dismissed": (
            "This observation was dismissed. Refresh the catalog before "
            "choosing another action."
        ),
        "mapping_candidate_not_dismissible": (
            "This observation is no longer an active dismissible candidate."
        ),
    }
    return messages.get(str(exc), "The canonical mapping proposal is unavailable.")


def _agent_review_prompt(
    item: Mapping[str, Any],
    *,
    dismiss_available: bool,
    automatic_integration: bool = False,
    integration_recovery: bool = False,
) -> str:
    candidate_id = str(
        item.get("candidate_record_id") or item.get("record_id") or "unknown"
    )
    prefix = (
        "Please recover TheTower automatic save-mapping integration "
        if automatic_integration or integration_recovery
        else "Please review TheTower save-mapping observation "
    )
    reason = str(
        item.get("automatic_integration_blocker_reason")
        or item.get("review_reason")
        or item.get("reason")
        or "unknown"
    )
    if automatic_integration:
        resolution = (
            "The terminal/save evidence is already machine-verified; do not "
            "repeat semantic review. Resolve the reported repository or "
            "publication blocker, preserve the exact candidate, and let the "
            "automatic consumer finish."
        )
    elif integration_recovery:
        resolution = (
            "The exact proposal was already reviewed; do not repeat review or "
            "unrelated validation. Resolve the reported repository or "
            "publication boundary, preserve the exact candidate, and let the "
            "automatic consumer finish."
        )
    elif dismiss_available:
        resolution = (
            "Either implement and validate the correct mapping through the "
            "normal feature-branch workflow, or dismiss this exact observation "
            "if it is incorrect. Preserve the original receipt and report what "
            "changed."
        )
    else:
        resolution = (
            "Resolve or complete the reported integration lifecycle without "
            "bypassing or dismissing it, then report what changed."
        )
    return (
        f"{prefix}{candidate_id}. The GUI cannot safely complete it.\n"
        f"Mapping: {item.get('mapping_id') or 'unknown'}\n"
        f"Check: {item.get('check_id') or 'unknown'}\n"
        f"State: {item.get('state') or 'unknown'}\n"
        f"Reason: {reason}\n"
        "Inspect the durable receipt and the current canonical mapping owners. "
        f"{resolution}"
    )


def _nonreviewable_next_action(item: Mapping[str, Any]) -> str:
    state = str(item.get("state") or "")
    if state == "promotion_pending":
        return (
            "The reviewed mapping is durably queued for automatic promotion. "
            "If the reported blocker persists, copy the agent request."
        )
    if state == "remote_publication_pending":
        return (
            "Local promotion completed; automatic publication is being retried. "
            "If it persists, copy the agent request."
        )
    if state == "promotion_cleanup_pending":
        return (
            "Production and origin contain the mapping; automatic release of "
            "its exact promotion owner is being retried. If it persists, copy "
            "the agent request."
        )
    if state == "production_validation_pending":
        return (
            "Production and origin contain the mapping. Wait for one fresh "
            "stable save decode; no further review is needed."
        )
    reason = str(
        item.get("review_reason")
        or item.get("reason")
        or "The exact proposal is unavailable."
    )
    return f"{reason} Copy the agent-review request for help resolving it."


def _dismiss_unavailable_reason(
    item: Mapping[str, Any],
    active_transaction: Optional[Mapping[str, Any]],
) -> str:
    if active_transaction is not None:
        return (
            "Finish or inspect the existing canonical integration before "
            "dismissing an observation."
        )
    if item.get("state") not in _DISMISSIBLE_CANDIDATE_STATES:
        return (
            "This lifecycle state must be completed or recovered; it cannot be "
            "dismissed as an unreviewed observation."
        )
    return "Dismissal is temporarily unavailable; refresh the catalog."


def canonical_mapping_runtime_commit(
    *,
    repository_root: Path | str,
) -> Optional[str]:
    """Capture the production commit once, before runtime acquisition begins."""

    try:
        return _git_output(
            Path(repository_root).resolve(),
            "rev-parse",
            "refs/heads/main",
        )
    except Exception:
        return None


def canonical_mapping_decode_start(
    *,
    runtime_main_commit: object,
    acquired_at: datetime,
) -> Optional[dict[str, str]]:
    """Bind one acquisition start to the runtime's deployment commit in O(1)."""

    if not _is_git_object(runtime_main_commit):
        return None
    return {
        "main_commit": str(runtime_main_commit),
        "acquired_at": acquired_at.astimezone(timezone.utc).isoformat(),
    }


def observe_canonical_mapping_decode(
    snapshot: object,
    *,
    repository_root: Path | str,
    candidate_store_path: Path | str,
    start_evidence: object = None,
) -> bool:
    """Best-effort runtime adapter used after a complete stable acquisition."""

    try:
        store = AppendOnlyMappingCandidateStore(candidate_store_path)
        manager = SaveMappingIntegrationManager(
            repository_root=repository_root,
            candidate_store=store,
        )
        return manager.observe_canonical_decode(
            snapshot,
            start_evidence=start_evidence,
        )
    except Exception:
        return False


__all__ = [
    "CanonicalDecodeReceiptStore",
    "PROMOTION_OWNER_REF",
    "SAVE_MAPPING_AUTOMATIC_PROMOTION_CAPABILITY",
    "SAVE_MAPPING_DISPOSITION_CAPABILITY",
    "SAVE_MAPPING_INTEGRATION_CAPABILITY",
    "SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION",
    "SAVE_MAPPING_MACHINE_VERIFICATION_CAPABILITY",
    "SAVE_MAPPING_REVIEW_STATUS_CAPABILITY",
    "SAVE_MAPPING_STAGING_REF",
    "SaveMappingIntegrationError",
    "SaveMappingIntegrationManager",
    "canonical_mapping_decode_start",
    "canonical_mapping_runtime_commit",
    "observe_canonical_mapping_decode",
]
