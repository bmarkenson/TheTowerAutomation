"""Narrow, reviewed fast lane for deterministic save mappings.

This module intentionally does not implement a general development workflow.
It accepts only server-generated mapping proposals, only while clean ``main``
and ``develop`` are at the same commit, and creates one verified child commit
on ``develop``.  Promotion and runtime validation remain separate checkpoints.
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
    _require_workspace_targets_match,
    _target_path,
    _write_all,
)


SAVE_MAPPING_INTEGRATION_CAPABILITY = "save_mapping_develop_integration_v1"
SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION = 2
SAVE_MAPPING_REVIEW_STATUS_CAPABILITY = "save_mapping_review_status_v2"

_TRANSACTION_KIND = "save_mapping_develop_integration_transaction"
_TRANSACTION_SCHEMA_VERSION = 2
_MAX_TRANSACTION_BYTES = 8 * 1024 * 1024
_COMMIT_SUBJECT_PREFIX = "Integrate save mapping candidate"
_CANDIDATE_TRAILER = "Save-Mapping-Candidate-ID"
_PROPOSAL_TRAILER = "Save-Mapping-Proposal-Fingerprint"
_RECEIPT_SCHEMA_VERSION = 2


def _is_hex(value: object, length: int) -> bool:
    text = str(value or "")
    return len(text) == length and all(
        character in "0123456789abcdef" for character in text
    )


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
        "scope": candidate["scope"],
    }
    if include_semantic:
        identity["semantic_value"] = candidate["semantic_value"]
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
        "integration": {
            "path": review["repository"]["develop_path"],
            "branch": "develop",
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
        "integration",
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
    if raw.get("kind") == "save_mapping_preparation_transaction":
        raise SaveMappingIntegrationError(
            "legacy_transaction_recovery_required",
            "A legacy feature-worktree preparation journal remains. Inspect and "
            "retire that prepared change before direct develop integration.",
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
        or raw.get("phase") not in {"commit_ready", "committed_to_develop"}
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
    integration = raw.get("integration")
    identity = raw.get("mapping_identity")
    if (
        not isinstance(repository, Mapping)
        or set(repository) != {"base_commit"}
        or not _is_git_object(repository.get("base_commit"))
        or not isinstance(integration, Mapping)
        or set(integration) != {"path", "branch", "expected_commit"}
        or not str(integration.get("path") or "")
        or integration.get("branch") != "develop"
        or not _is_git_object(integration.get("expected_commit"))
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
        "integration": dict(integration),
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
    """Review and commit one deterministic candidate directly to ``develop``."""

    def __init__(
        self,
        *,
        repository_root: Path | str,
        candidate_store: AppendOnlyMappingCandidateStore,
        development_root: Path | str | None = None,
        lock_path: Path | str | None = None,
        transaction_path: Path | str | None = None,
        decode_receipt_path: Path | str | None = None,
        transaction_fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.candidate_store = candidate_store
        # Retained as a constructor compatibility seam; no feature discovery uses it.
        self.development_root = (
            Path(development_root).resolve()
            if development_root is not None
            else None
        )
        self.lock_path = (
            Path(lock_path)
            if lock_path is not None
            else candidate_store.path.with_name("canonical-integration.lock")
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

    def catalog(self) -> dict[str, Any]:
        """Return candidate review state without leaking projection failures."""

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
        """Build candidate review state and fixed develop eligibility."""

        try:
            transaction = _load_transaction(self.transaction_path)
            base = (
                self._lightweight_transaction_state(transaction)
                if transaction is not None
                and transaction["phase"] == "commit_ready"
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
            if item.get("state") in {
                "promotion_pending",
                "production_validation_pending",
                "integration_unconfirmed",
            }:
                projected.update(
                    review_available=False,
                    review_code=str(item.get("state")),
                    review_reason=str(item.get("reason") or ""),
                )
            elif item.get("state") == "integration_recovery_required":
                recoverable = bool(
                    transaction is not None
                    and transaction["phase"] == "commit_ready"
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
                    proposed_mapping_patch(record, repository_root=base["develop_path"])
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
        """Project candidate lifecycle across develop, production, and decode proof."""

        current = dict(base or self._base_state())
        if transaction is None:
            transaction = _load_transaction(self.transaction_path)
        main_status = mapping_candidate_review_status(
            store=self.candidate_store,
            repository_root=self.repository_root,
        )
        develop_status = mapping_candidate_review_status(
            store=self.candidate_store,
            repository_root=current["develop_path"],
        )
        if main_status.get("available") is not True:
            return main_status
        if develop_status.get("available") is not True:
            return develop_status
        main_items = {
            str(item.get("candidate_record_id")): dict(item)
            for item in main_status.get("items") or ()
        }
        develop_items = {
            str(item.get("candidate_record_id")): dict(item)
            for item in develop_status.get("items") or ()
        }
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
            item = mapping_candidate_record_status(
                record,
                repository_root=current["develop_path"],
            )
            lifecycle, reason = self._transaction_lifecycle(
                transaction,
                current,
                main_candidate=main_candidate,
            )
            item.update(
                state=lifecycle,
                reason=reason,
                integration_commit=transaction["integration"]["expected_commit"],
            )
            main_items[candidate_id] = item
        else:
            for candidate_id, develop_item in develop_items.items():
                main_item = main_items.get(candidate_id)
                if (
                    develop_item.get("state") == "integrated"
                    and main_item is not None
                    and main_item.get("state") != "integrated"
                ):
                    main_item.update(
                        state="integration_unconfirmed",
                        reason=(
                            "develop contains this mapping without a durable "
                            "fast-lane integration record; ordinary review is required"
                        ),
                    )
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
        """Return a non-mutating proposal bound to one synchronized base commit."""

        transaction = _load_transaction(self.transaction_path)
        if (
            transaction is not None
            and transaction["candidate_record_id"]
            == str(candidate_record_id or "").lower()
            and transaction["phase"] == "commit_ready"
        ):
            base = self._lightweight_transaction_state(transaction)
            lifecycle, _reason = self._transaction_lifecycle(transaction, base)
            if lifecycle == "integration_recovery_required":
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
                repository_root=base["develop_path"],
            )
            develop_targets = _render_proposal_targets(
                proposal,
                repository_root=base["develop_path"],
                candidate_record=record,
            )
            production_targets = _render_proposal_targets(
                proposal,
                repository_root=self.repository_root,
                candidate_record=record,
            )
            _require_workspace_targets_match(develop_targets, production_targets)
            self._validate_target_scope(base["develop_path"], develop_targets)
            canonical_fingerprint = self._canonical_mapping_fingerprint(
                base["develop_path"],
                develop_targets,
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
            "base_commit": base["main_commit"],
            "proposal": proposal,
            "rendered_targets": [_public_target(target) for target in develop_targets],
            "canonical_mapping_fingerprint": canonical_fingerprint,
            "commit_contract": {
                "subject_prefix": _COMMIT_SUBJECT_PREFIX,
                "candidate_trailer": _CANDIDATE_TRAILER,
                "proposal_trailer": _PROPOSAL_TRAILER,
            },
        }
        reviewed_fingerprint = fingerprint_json(fingerprint_payload)
        integration = {"available": True, "code": "", "reason": ""}
        recovery_required = False
        if transaction is not None:
            matches = (
                transaction["candidate_record_id"] == record["record_id"]
                and transaction["reviewed_proposal_fingerprint"]
                == reviewed_fingerprint
                and transaction["repository"]["base_commit"]
                == base["main_commit"]
            )
            recovery_required = True
            integration = {
                "available": matches,
                "code": "transaction_recovery_required",
                "reason": (
                    "An interrupted integration for this exact review can be "
                    "continued once; refresh after the result."
                    if matches
                    else "An interrupted integration for another candidate must "
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
                _public_target(target) for target in develop_targets
            ],
            "integrate": integration,
            "recovery_required": recovery_required,
        }

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
        repository.update(
            integration_available=False,
            code="transaction_recovery_required",
            reason="An exact durable integration transaction requires recovery.",
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
            "integrate": {
                "available": True,
                "code": "transaction_recovery_required",
                "reason": (
                    "Retry this exact durable transaction once; Git refs and "
                    "canonical invariants will be reverified before success."
                ),
            },
            "recovery_required": True,
        }

    def integrate(
        self,
        *,
        candidate_record_id: object,
        reviewed_proposal_fingerprint: object,
    ) -> dict[str, Any]:
        """Create and install the exact reviewed child commit on ``develop``."""

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
                return self._apply_transaction(transaction, idempotent=True)

            review = self.review(candidate_record_id=candidate_record_id)
            if supplied != review["reviewed_proposal_fingerprint"]:
                raise SaveMappingIntegrationError(
                    "reviewed_proposal_stale",
                    "The proposal or synchronized repository snapshot changed; "
                    "nothing was committed. Refresh and review again.",
                )
            if review["integrate"]["available"] is not True:
                raise SaveMappingIntegrationError(
                    str(review["integrate"].get("code") or "integration_unavailable"),
                    str(review["integrate"].get("reason") or "Integration is unavailable."),
                )
            record = self.candidate_store.get(candidate_record_id)
            targets = _render_proposal_targets(
                review["proposal"],
                repository_root=Path(review["repository"]["develop_path"]),
                candidate_record=record,
            )
            message = _commit_message(record["record_id"], supplied)
            expected_commit = self._build_commit(
                Path(review["repository"]["develop_path"]),
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

    def observe_canonical_decode(
        self,
        snapshot: object,
        *,
        start_evidence: object = None,
    ) -> bool:
        """Record a stable post-promotion decode; failures stay nonblocking."""

        try:
            transaction = _load_transaction(self.transaction_path)
            if transaction is None or transaction["phase"] != "committed_to_develop":
                return False
            expected = transaction["integration"]["expected_commit"]
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
                    and current["transaction_fingerprint"]
                    == transaction["transaction_fingerprint"]
                    and self._matching_decode_receipt(current)
                ):
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
        if transaction["phase"] == "commit_ready":
            if self._transaction_git_lock_present(transaction):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Git integration lock artifacts remain; inspect them before "
                    "retrying or changing repository state.",
                )
            try:
                with self.candidate_store.locked_records() as candidate_records:
                    self._validate_locked_candidate(
                        transaction,
                        candidate_records,
                    )
                    self._restore_detached_transaction_checkout(transaction)
            except PlayerSaveMappingCandidateError as exc:
                raise SaveMappingIntegrationError(
                    _proposal_error_code(exc),
                    _proposal_error_message(exc),
                ) from exc
        self._verify_transaction_binding(transaction)
        base_commit = transaction["repository"]["base_commit"]
        expected_commit = transaction["integration"]["expected_commit"]
        develop_path = Path(transaction["integration"]["path"])
        main_commit = _git_output(self.repository_root, "rev-parse", "refs/heads/main")
        develop_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/develop",
        )
        if develop_commit == expected_commit:
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
            self._verify_committed_state(transaction)
            if transaction["phase"] != "committed_to_develop":
                transaction = _set_transaction_phase(
                    self.transaction_path,
                    transaction,
                    "committed_to_develop",
                )
            return _integration_result(
                transaction,
                idempotent=True,
                promoted=_git_success(
                    self.repository_root,
                    "merge-base",
                    "--is-ancestor",
                    expected_commit,
                    _git_output(
                        self.repository_root,
                        "rev-parse",
                        "refs/heads/main",
                    ),
                ),
            )
        if develop_commit != base_commit or main_commit != base_commit:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "main or develop moved outside the reviewed integration; no ref "
                "was changed automatically.",
            )
        if _git_status(self.repository_root) or _git_status(develop_path):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "main or develop changed after review; no ref was changed.",
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
                self._fault("before_develop_fast_forward")
                self._verify_transaction_binding(transaction)
                if _git_status(self.repository_root) or _git_status(develop_path):
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "main or develop changed immediately before integration; "
                        "no ref was changed.",
                    )
                if (
                    _git_output(
                        self.repository_root,
                        "rev-parse",
                        "refs/heads/main",
                    )
                    != base_commit
                    or _git_output(
                        self.repository_root,
                        "rev-parse",
                        "refs/heads/develop",
                    )
                    != base_commit
                ):
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "main or develop moved immediately before integration; "
                        "no ref was changed.",
                    )
                detached = _git_mutate(
                    develop_path,
                    "switch",
                    "--detach",
                    base_commit,
                    check=False,
                )
                if detached.returncode != 0:
                    if self._transaction_git_lock_present(transaction):
                        raise SaveMappingIntegrationError(
                            "commit_state_uncertain",
                            "Git integration lock artifacts appeared before the "
                            "checkout boundary; inspect before continuing.",
                        )
                    raise SaveMappingIntegrationError(
                        "develop_fast_forward_failed",
                        "develop could not enter the verified checkout boundary; "
                        "its ref remains at the reviewed base. Refresh before "
                        "retrying once.",
                    )
                self._verify_detached_transaction_checkout(transaction)
                try:
                    result = _git_mutate(
                        self.repository_root,
                        "update-ref",
                        "-m",
                        "save-mapping direct develop integration",
                        "--stdin",
                        input_bytes=(
                            f"verify refs/heads/main {base_commit}\n"
                            f"update refs/heads/develop {expected_commit} "
                            f"{base_commit}\n"
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
                observed = _git_output(
                    self.repository_root,
                    "rev-parse",
                    "refs/heads/develop",
                )
                if observed != expected_commit:
                    if observed == base_commit:
                        self._restore_detached_transaction_checkout(transaction)
                        if (
                            observed_main == base_commit
                            and not _git_status(self.repository_root)
                            and not _git_status(develop_path)
                            and not self._transaction_git_lock_present(transaction)
                        ):
                            raise SaveMappingIntegrationError(
                                "develop_fast_forward_failed",
                                "The verified ref transaction was not applied; "
                                "main and develop remain clean at the reviewed "
                                "base. Refresh before retrying once.",
                            )
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "The main/develop ref transaction could not be proved exact.",
                    )
                self._fault("develop_ref_updated")
                # Switching from the exact detached base to the explicitly
                # advanced develop branch lets Git serialize all index/worktree
                # updates with concurrent checkout commands. A different branch
                # is never used as the source or ref-update target.
                self._restore_detached_transaction_checkout(transaction)
                self._verify_transaction_binding(transaction)
                if _git_status(develop_path):
                    raise SaveMappingIntegrationError(
                        "commit_state_uncertain",
                        "develop advanced, but its checkout is not clean; inspect "
                        "before continuing.",
                    )
                if result.returncode != 0:
                    # The explicit ref transaction is authoritative after a
                    # lost or late command status.
                    idempotent = True
        except PlayerSaveMappingCandidateError as exc:
            raise SaveMappingIntegrationError(
                _proposal_error_code(exc),
                _proposal_error_message(exc),
            ) from exc
        self._verify_transaction_binding(transaction)
        self._fault("develop_fast_forwarded")
        self._verify_committed_state(transaction)
        transaction = _set_transaction_phase(
            self.transaction_path,
            transaction,
            "committed_to_develop",
        )
        self._fault("transaction_committed")
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
        self._require_routine_candidate_records(matches[0], candidate_records)

    def _restore_detached_transaction_checkout(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        """Finish only an exact transaction-owned partial checkout."""

        base_commit = transaction["repository"]["base_commit"]
        expected_commit = transaction["integration"]["expected_commit"]
        develop_path = Path(transaction["integration"]["path"])
        matches = [
            item
            for item in _linked_worktrees(self.repository_root)
            if item.path == develop_path
        ]
        if len(matches) != 1:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The durable develop checkout path is no longer unique.",
            )
        checkout = matches[0]
        if (
            checkout.branch == "develop"
            and checkout.head in {base_commit, expected_commit}
            and not _git_status(develop_path)
        ):
            return
        if not self._transaction_checkout_recoverable(transaction, checkout):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The durable develop checkout is not clean at an exact "
                "transaction endpoint.",
            )
        if checkout.head == base_commit:
            # A plain switch refuses any edit that arrives after the clean-state
            # proof. It never rewrites that edit into conflict markers.
            completed = _git_mutate(
                develop_path,
                "switch",
                "--detach",
                expected_commit,
                check=False,
            )
            if completed.returncode != 0:
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "The exact partial develop checkout could not be completed.",
                )
        refreshed = [
            item
            for item in _linked_worktrees(self.repository_root)
            if item.path == develop_path
            and item.branch == "(detached)"
            and item.head == expected_commit
            and not item.locked
            and not item.prunable
        ]
        if len(refreshed) != 1 or _git_status(develop_path):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The completed develop checkout is not the exact clean result.",
            )
        restored = _git_mutate(
            develop_path,
            "switch",
            "--no-guess",
            "develop",
            check=False,
        )
        if restored.returncode != 0:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The exact detached develop checkout could not be reattached.",
            )
        self._verify_transaction_binding(transaction)
        if _git_status(develop_path):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The reattached develop checkout is not clean.",
            )

    def _transaction_checkout_recoverable(
        self,
        transaction: Mapping[str, Any],
        checkout: Optional[object] = None,
    ) -> bool:
        """Prove a checkout is clean at one exact transaction endpoint."""

        base_commit = transaction["repository"]["base_commit"]
        expected_commit = transaction["integration"]["expected_commit"]
        develop_path = Path(transaction["integration"]["path"])
        try:
            if checkout is None:
                matches = [
                    item
                    for item in _linked_worktrees(self.repository_root)
                    if item.path == develop_path
                ]
                if len(matches) != 1:
                    return False
                checkout = matches[0]
            if (
                checkout.branch not in {"develop", "(detached)"}
                or checkout.head not in {base_commit, expected_commit}
                or checkout.locked
                or checkout.prunable
            ):
                return False
            return not _git_status(develop_path)
        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            SaveMappingIntegrationError,
        ):
            return False

    def _transaction_git_lock_present(
        self,
        transaction: Mapping[str, Any],
    ) -> bool:
        """Detect ordinary Git crash artifacts without removing any of them."""

        develop_path = Path(transaction["integration"]["path"])
        try:
            owners = (
                (develop_path, "index"),
                (develop_path, "HEAD"),
                (self.repository_root, "refs/heads/develop"),
                (self.repository_root, "refs/heads/main"),
                (self.repository_root, "packed-refs"),
            )
            for owner, git_path in owners:
                resolved = Path(_git_output(owner, "rev-parse", "--git-path", git_path))
                if not resolved.is_absolute():
                    resolved = owner / resolved
                if Path(f"{resolved}.lock").exists():
                    return True
        except (OSError, SaveMappingIntegrationError):
            return True
        return False

    def _verify_detached_transaction_checkout(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        base_commit = transaction["repository"]["base_commit"]
        develop_path = Path(transaction["integration"]["path"])
        matches = [
            item
            for item in _linked_worktrees(self.repository_root)
            if item.path == develop_path
            and item.branch == "(detached)"
            and item.head == base_commit
            and not item.locked
            and not item.prunable
        ]
        if len(matches) != 1 or _git_status(develop_path):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "develop did not enter the exact clean detached integration boundary.",
            )

    def _verify_committed_state(self, transaction: Mapping[str, Any]) -> None:
        base_commit = transaction["repository"]["base_commit"]
        expected_commit = transaction["integration"]["expected_commit"]
        develop_path = Path(transaction["integration"]["path"])
        self._verify_commit(
            expected_commit,
            base_commit=base_commit,
            targets=_transaction_targets(transaction),
            commit_message=transaction["commit_message"],
        )
        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        if main_commit != base_commit:
            # Promotion may already have happened after a lost response.
            if not _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                expected_commit,
                main_commit,
            ):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Production moved without containing the integration commit.",
                )
            if not self._ref_targets_match(transaction, main_commit):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Production contains the integration commit, but its exact "
                    "canonical targets were superseded.",
                )
            record = self.candidate_store.get(transaction["candidate_record_id"])
            main_match = mapping_candidate_record_status(
                record,
                repository_root=self.repository_root,
            )
            if main_match.get("state") != "integrated":
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Production contains the integration commit, but its current "
                    "canonical mapping no longer owns the candidate.",
                )
        if _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/develop",
        ) != expected_commit:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "develop no longer points to the exact integration commit.",
            )
        if not self._ref_targets_match(transaction, expected_commit):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The integration commit no longer contains its exact canonical targets.",
            )
        if _git_status(develop_path):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "develop is not clean after canonical integration.",
            )
        if _git_status(self.repository_root):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "Production is not clean after canonical integration.",
            )
        self._verify_transaction_binding(transaction)
        status = mapping_candidate_review_status(
            store=self.candidate_store,
            repository_root=develop_path,
        )
        if status.get("available") is not True:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The committed develop mapping failed its canonical invariants.",
            )
        record = self.candidate_store.get(transaction["candidate_record_id"])
        match = mapping_candidate_record_status(
            record,
            repository_root=develop_path,
        )
        if match.get("state") != "integrated":
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The committed develop mapping failed its canonical invariants.",
            )

    def _verify_transaction_binding(self, transaction: Mapping[str, Any]) -> None:
        worktrees = _linked_worktrees(self.repository_root)
        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        productions = [
            item
            for item in worktrees
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
        matches = [
            item
            for item in worktrees
            if item.branch == "develop"
            and item.path == Path(transaction["integration"]["path"]).resolve()
            and item.head
            == _git_output(
                self.repository_root,
                "rev-parse",
                "refs/heads/develop",
            )
            and not item.locked
            and not item.prunable
        ]
        if len(matches) != 1:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The bound develop worktree identity changed.",
            )
        self._validate_target_scope(
            Path(transaction["integration"]["path"]),
            _transaction_targets(transaction),
        )

    def _build_commit(
        self,
        develop_path: Path,
        *,
        base_commit: str,
        targets: Sequence[_PreparedTarget],
        commit_message: str,
    ) -> str:
        name = _git_config_value(develop_path, "user.name")
        email = _git_config_value(develop_path, "user.email")
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
            _git_mutate(develop_path, "read-tree", base_commit, env=environment)
            for target in targets:
                if not target.changed:
                    continue
                blob = _git_mutate(
                    develop_path,
                    "hash-object",
                    "-w",
                    "--stdin",
                    input_bytes=target.after,
                ).stdout.decode("ascii").strip()
                mode = "100755" if target.mode & 0o111 else "100644"
                _git_mutate(
                    develop_path,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{mode},{blob},{target.path}",
                    env=environment,
                )
            tree = _git_mutate(
                develop_path,
                "write-tree",
                env=environment,
            ).stdout.decode("ascii").strip()
            commit = _git_mutate(
                develop_path,
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

    def _base_state(self) -> dict[str, Any]:
        worktrees = _linked_worktrees(self.repository_root)
        production = next(
            (item for item in worktrees if item.path == self.repository_root),
            None,
        )
        develops = [item for item in worktrees if item.branch == "develop"]
        if production is None or production.branch != "main":
            raise SaveMappingIntegrationError(
                "production_role_invalid",
                "The control surface repository root is not the linked main worktree.",
            )
        if len(develops) != 1:
            raise SaveMappingIntegrationError(
                "develop_worktree_unavailable",
                "Exactly one linked develop worktree is required.",
            )
        develop = develops[0]
        main_commit = _git_output(self.repository_root, "rev-parse", "refs/heads/main")
        develop_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/develop",
        )
        production_clean = not _git_status(self.repository_root)
        develop_clean = not _git_status(develop.path)
        identity_available = bool(
            _git_config_value(develop.path, "user.name")
            and _git_config_value(develop.path, "user.email")
        )
        available = True
        code = ""
        reason = ""
        if develop.locked or develop.prunable:
            available = False
            code = "develop_worktree_unavailable"
            reason = "The linked develop worktree is locked or prunable."
        elif production.head != main_commit:
            available = False
            code = "production_head_changed"
            reason = "The production worktree is not at the current main tip."
        elif develop.head != develop_commit:
            available = False
            code = "develop_head_changed"
            reason = "The develop worktree is not at the current develop tip."
        elif not production_clean:
            available = False
            code = "production_worktree_dirty"
            reason = "Production has tracked, staged, or untracked changes."
        elif not develop_clean:
            available = False
            code = "develop_worktree_dirty"
            reason = "develop has tracked, staged, or untracked changes."
        elif main_commit != develop_commit:
            available = False
            code = "repository_not_synchronized"
            reason = (
                "main and develop are not at the same commit; finish the pending "
                "integration or production promotion first."
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
            "develop_commit": develop_commit,
            "synchronized": main_commit == develop_commit,
            "production_clean": production_clean,
            "develop_clean": develop_clean,
            "develop_path": develop.path,
        }

    def _lightweight_transaction_state(
        self,
        transaction: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Read only refs needed by a persistent lifecycle projection."""

        main_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/main",
        )
        develop_commit = _git_output(
            self.repository_root,
            "rev-parse",
            "refs/heads/develop",
        )
        return {
            "integration_available": False,
            "code": "",
            "reason": "",
            "main_commit": main_commit,
            "develop_commit": develop_commit,
            "synchronized": main_commit == develop_commit,
            "production_clean": False,
            "develop_clean": False,
            "develop_path": Path(transaction["integration"]["path"]),
        }

    @staticmethod
    def _public_repository(base: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "main_commit": base["main_commit"],
            "develop_commit": base["develop_commit"],
            "synchronized": base["synchronized"],
            "production_clean": base["production_clean"],
            "develop_clean": base["develop_clean"],
            "develop_path": str(base["develop_path"]),
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
        expected = transaction["integration"]["expected_commit"]
        main_contains = _git_success(
            self.repository_root,
            "merge-base",
            "--is-ancestor",
            expected,
            str(base["main_commit"]),
        )
        develop_contains = _git_success(
            self.repository_root,
            "merge-base",
            "--is-ancestor",
            expected,
            str(base["develop_commit"]),
        )
        if transaction["phase"] != "committed_to_develop":
            if self._transaction_git_lock_present(transaction):
                return (
                    "integration_unconfirmed",
                    "Git integration lock artifacts remain; inspect them before "
                    "retrying or changing repository state",
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
            if main_contains and not self._ref_targets_match(
                transaction,
                str(base["main_commit"]),
            ):
                return (
                    "integration_unconfirmed",
                    "Production contains superseding canonical target content "
                    "outside the durable transaction",
                )
            if develop_contains and not self._ref_targets_match(
                transaction,
                str(base["develop_commit"]),
            ):
                return (
                    "integration_unconfirmed",
                    "develop contains superseding canonical target content "
                    "outside the durable transaction",
                )
            if not self._transaction_checkout_recoverable(transaction):
                return (
                    "integration_unconfirmed",
                    "The develop checkout contains state outside the exact "
                    "transaction-owned partial recovery boundary",
                )
            main_exact = str(base["main_commit"]) == expected
            develop_exact = str(base["develop_commit"]) == expected
            base_exact = transaction["repository"]["base_commit"]
            if main_exact and develop_exact:
                if not self._ref_targets_match(
                    transaction,
                    str(base["main_commit"]),
                ) or not self._ref_targets_match(
                    transaction,
                    str(base["develop_commit"]),
                ):
                    return (
                        "integration_unconfirmed",
                        "A current branch contains superseding canonical target "
                        "content outside the durable transaction",
                    )
                if main_candidate is None:
                    record = self.candidate_store.get(
                        transaction["candidate_record_id"]
                    )
                    main_candidate = mapping_candidate_record_status(
                        record,
                        repository_root=self.repository_root,
                    )
                if main_candidate.get("state") != "integrated":
                    return (
                        "integration_unconfirmed",
                        "Production contains the generated commit but no longer "
                        "owns the durable candidate",
                    )
                return (
                    "integration_recovery_required",
                    "The generated integration commit requires one exact "
                    "recovery verification before promotion",
                )
            if str(base["main_commit"]) == base_exact and develop_exact:
                if not self._ref_targets_match(
                    transaction,
                    str(base["develop_commit"]),
                ):
                    return (
                        "integration_unconfirmed",
                        "develop contains superseding canonical target content "
                        "outside the durable transaction",
                    )
                return (
                    "integration_recovery_required",
                    "The generated integration commit requires one exact "
                    "recovery verification before promotion",
                )
            if (
                str(base["main_commit"]) == base_exact
                and str(base["develop_commit"]) == base_exact
            ):
                return (
                    "integration_recovery_required",
                    "The generated integration commit requires one exact "
                    "recovery verification before promotion",
                )
            return (
                "integration_unconfirmed",
                "Repository refs are not the exact base or generated commit "
                "required for recovery; inspect before continuing",
            )
        if main_contains:
            if not self._ref_targets_match(transaction, str(base["main_commit"])):
                return (
                    "integration_unconfirmed",
                    "Production contains the integration commit but its exact "
                    "canonical target content was superseded",
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
                    "Production contains the integration commit but its current "
                    "canonical mapping no longer owns this candidate",
                )
            if self._matching_decode_receipt(transaction):
                return (
                    "integrated",
                    "production contains the integration commit and a fresh "
                    "stable decode confirmed it",
                )
            return (
                "production_validation_pending",
                f"Production contains {expected[:12]}; awaiting a fresh stable save decode",
            )
        if develop_contains:
            if base["main_commit"] != transaction["repository"]["base_commit"]:
                return (
                    "integration_unconfirmed",
                    "Production diverged from the reviewed base; the integration "
                    "is not a fast-forward promotion candidate",
                )
            if not self._ref_targets_match(transaction, str(base["develop_commit"])):
                return (
                    "integration_unconfirmed",
                    "develop contains the integration commit but its exact "
                    "canonical target content was superseded",
                )
            return (
                "promotion_pending",
                f"Committed to develop as {expected[:12]}; awaiting production promotion",
            )
        if (
            base["main_commit"] == transaction["repository"]["base_commit"]
            and base["develop_commit"] == transaction["repository"]["base_commit"]
        ):
            return (
                "integration_recovery_required",
                "A reviewed integration commit is ready; explicitly retry once to recover it",
            )
        return (
            "integration_unconfirmed",
            "Repository refs moved outside the durable integration transaction; inspect before continuing",
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
            == transaction["integration"]["expected_commit"]
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
                transaction["integration"]["expected_commit"],
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
            "integration_commit": transaction["integration"]["expected_commit"],
            "state": lifecycle,
            "reason": reason,
            "recovery_required": lifecycle in {
                "integration_recovery_required",
                "integration_unconfirmed",
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

    def _require_routine_candidate(self, record: Mapping[str, Any]) -> None:
        self._require_routine_candidate_records(
            record,
            self.candidate_store.list_records(),
        )

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
            str(item.get("semantic_value"))
            for item in matches
            if item.get("semantic_value") is not None
        }
        if (
            len(semantics) != 1
            or semantics != {str(candidate["semantic_value"])}
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


def _integration_result(
    transaction: Mapping[str, Any],
    *,
    idempotent: bool,
    promoted: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
        "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
        "operation": "integrate",
        "disposition": "committed_to_develop",
        "idempotent": idempotent,
        "candidate_record_id": transaction["candidate_record_id"],
        "reviewed_proposal_fingerprint": transaction[
            "reviewed_proposal_fingerprint"
        ],
        "base_commit": transaction["repository"]["base_commit"],
        "develop_commit": transaction["integration"]["expected_commit"],
        "integration_commit": transaction["integration"]["expected_commit"],
        "committed": True,
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
    }
    return messages.get(str(exc), "The canonical mapping proposal is unavailable.")


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
    "SAVE_MAPPING_INTEGRATION_CAPABILITY",
    "SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION",
    "SAVE_MAPPING_REVIEW_STATUS_CAPABILITY",
    "SaveMappingIntegrationError",
    "SaveMappingIntegrationManager",
    "canonical_mapping_decode_start",
    "canonical_mapping_runtime_commit",
    "observe_canonical_mapping_decode",
]
