"""Guarded operator preparation of reviewed player-save mapping proposals.

The production control surface discovers linked feature worktrees but never
writes production or ``develop``.  A client selects only an opaque workspace
identity and a durable candidate receipt, reviews a server-computed proposal,
and may then prepare that exact proposal in one clean feature worktree.  The
result remains uncommitted, unvalidated, and unpromoted by design.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
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
    fingerprint_json,
    mapping_candidate_review_status,
    proposed_mapping_patch,
    validate_mapping_candidate_result,
)


SAVE_MAPPING_INTEGRATION_CAPABILITY = "save_mapping_integration_v1"
SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION = 1


class SaveMappingIntegrationError(ValueError):
    """A catalog, review, workspace, or preparation guard rejected the request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Worktree:
    path: Path
    head: str
    branch: str
    locked: bool = False
    prunable: bool = False


@dataclass
class _JsonNode:
    kind: str
    start: int
    end: int
    members: Optional[dict[str, "_JsonNode"]] = None
    items: Optional[list["_JsonNode"]] = None


@dataclass(frozen=True)
class _PreparedTarget:
    path: str
    mapping_id: str
    before_sha256: str
    after_sha256: str
    before: bytes
    after: bytes
    changed: bool
    mode: int


_TRANSACTION_KIND = "save_mapping_preparation_transaction"
_MAX_TRANSACTION_BYTES = 8 * 1024 * 1024


def _transaction_document(
    review: Mapping[str, Any],
    targets: Sequence[_PreparedTarget],
) -> dict[str, Any]:
    transaction = {
        "schema_version": 1,
        "kind": _TRANSACTION_KIND,
        "transaction_id": secrets.token_hex(16),
        "candidate_record_id": review["candidate_record_id"],
        "reviewed_proposal_fingerprint": review[
            "reviewed_proposal_fingerprint"
        ],
        "repository": {
            "main_commit": review["repository"]["main_commit"],
            "develop_commit": review["repository"]["develop_commit"],
        },
        "workspace": {
            "workspace_id": review["workspace"]["workspace_id"],
            "path": review["workspace"]["path_display"],
            "branch": review["workspace"]["branch"],
            "head_commit": review["workspace"]["head_commit"],
        },
        "targets": [
            {
                "path": target.path,
                "mapping_id": target.mapping_id,
                "before_sha256": target.before_sha256,
                "after_sha256": target.after_sha256,
                "before_base64": base64.b64encode(target.before).decode("ascii"),
                "after_base64": base64.b64encode(target.after).decode("ascii"),
                "changed": target.changed,
                "mode": target.mode,
            }
            for target in targets
        ],
    }
    transaction["transaction_fingerprint"] = fingerprint_json(transaction)
    return transaction


def _transaction_targets(
    transaction: Mapping[str, Any],
) -> list[_PreparedTarget]:
    return [
        _PreparedTarget(
            path=item["path"],
            mapping_id=item["mapping_id"],
            before_sha256=item["before_sha256"],
            after_sha256=item["after_sha256"],
            before=item["before"],
            after=item["after"],
            changed=item["changed"],
            mode=item["mode"],
        )
        for item in transaction["targets"]
    ]


def _validate_transaction_document(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version",
        "kind",
        "transaction_id",
        "candidate_record_id",
        "reviewed_proposal_fingerprint",
        "repository",
        "workspace",
        "targets",
        "transaction_fingerprint",
    }:
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical preparation transaction record is invalid; inspect "
            "the selected feature worktree before continuing.",
        )
    document = dict(raw)
    supplied_fingerprint = document.pop("transaction_fingerprint")
    if (
        raw.get("schema_version") != 1
        or raw.get("kind") != _TRANSACTION_KIND
        or not _is_hex(raw.get("transaction_id"), 32)
        or not _is_hex(raw.get("candidate_record_id"), 64)
        or not _is_hex(raw.get("reviewed_proposal_fingerprint"), 64)
        or not _is_hex(supplied_fingerprint, 64)
        or fingerprint_json(document) != supplied_fingerprint
    ):
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical preparation transaction identity is invalid; "
            "inspect the selected feature worktree before continuing.",
        )
    repository = raw.get("repository")
    workspace = raw.get("workspace")
    raw_targets = raw.get("targets")
    if (
        not isinstance(repository, Mapping)
        or set(repository) != {"main_commit", "develop_commit"}
        or not _is_git_object(repository.get("main_commit"))
        or not _is_git_object(repository.get("develop_commit"))
        or not isinstance(workspace, Mapping)
        or set(workspace) != {"workspace_id", "path", "branch", "head_commit"}
        or not _is_hex(workspace.get("workspace_id"), 64)
        or not str(workspace.get("path") or "")
        or not str(workspace.get("branch") or "").startswith("feature/")
        or not _is_git_object(workspace.get("head_commit"))
        or not isinstance(raw_targets, list)
        or not 1 <= len(raw_targets) <= 16
    ):
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical preparation transaction scope is invalid; inspect "
            "the selected feature worktree before continuing.",
        )
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_target in raw_targets:
        if not isinstance(raw_target, Mapping) or set(raw_target) != {
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
                "A canonical preparation transaction target is invalid.",
            )
        relative = str(raw_target.get("path") or "")
        if (
            not relative
            or relative in seen
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not str(raw_target.get("mapping_id") or "")
            or not _is_hex(raw_target.get("before_sha256"), 64)
            or not _is_hex(raw_target.get("after_sha256"), 64)
            or type(raw_target.get("changed")) is not bool
            or isinstance(raw_target.get("mode"), bool)
            or not isinstance(raw_target.get("mode"), int)
            or not 0 <= raw_target["mode"] <= 0o7777
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical preparation transaction target identity is invalid.",
            )
        try:
            before = base64.b64decode(
                raw_target["before_base64"],
                validate=True,
            )
            after = base64.b64decode(
                raw_target["after_base64"],
                validate=True,
            )
        except (TypeError, ValueError) as exc:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical preparation transaction payload is invalid.",
            ) from exc
        if (
            hashlib.sha256(before).hexdigest()
            != raw_target["before_sha256"]
            or hashlib.sha256(after).hexdigest()
            != raw_target["after_sha256"]
            or (before != after) != raw_target["changed"]
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical preparation transaction payload changed.",
            )
        seen.add(relative)
        targets.append(
            {
                **dict(raw_target),
                "before": before,
                "after": after,
            }
        )
    return {
        **dict(raw),
        "repository": dict(repository),
        "workspace": dict(workspace),
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
            "The canonical preparation transaction cannot be inspected.",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical preparation transaction is not a regular file.",
        )
    try:
        payload = path.read_bytes()
        if len(payload) > _MAX_TRANSACTION_BYTES:
            raise ValueError("transaction record is too large")
        raw = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SaveMappingIntegrationError(
            "commit_state_uncertain",
            "The canonical preparation transaction cannot be decoded.",
        ) from exc
    return _validate_transaction_document(raw)


def _write_transaction(path: Path, transaction: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        raise SaveMappingIntegrationError(
            "transaction_recovery_required",
            "An interrupted canonical preparation must be recovered first.",
        )
    content = (
        json.dumps(transaction, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.stage-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    installed = False
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, content)
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


def _remove_transaction(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        _fsync_directory(path.parent)
    except OSError:
        # If a power loss resurrects the journal, all-after recovery is safe.
        pass
    return True


def _is_hex(value: object, length: int) -> bool:
    text = str(value or "")
    return len(text) == length and all(
        character in "0123456789abcdef" for character in text
    )


def _is_git_object(value: object) -> bool:
    text = str(value or "")
    return 40 <= len(text) <= 64 and all(
        character in "0123456789abcdef" for character in text
    )


class SaveMappingIntegrationManager:
    """Project, review, and prepare exact proposals in linked feature roots."""

    def __init__(
        self,
        *,
        repository_root: Path | str,
        candidate_store: AppendOnlyMappingCandidateStore,
        development_root: Path | str | None = None,
        lock_path: Path | str | None = None,
        transaction_path: Path | str | None = None,
        transaction_fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.candidate_store = candidate_store
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
        self._transaction_fault_hook = transaction_fault_hook

    def catalog(self) -> dict[str, Any]:
        """Return the candidate queue and server-discovered feature worktrees."""

        try:
            base = self._base_state()
            transaction = _load_transaction(self.transaction_path)
        except SaveMappingIntegrationError as exc:
            return {
                "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
                "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
                "available": False,
                "reason": str(exc),
                "code": exc.code,
                "repository": None,
                "workspaces": [],
                "items": [],
                "transaction": None,
            }
        status = mapping_candidate_review_status(
            store=self.candidate_store,
            repository_root=base["develop_path"],
        )
        if status.get("available") is not True:
            return {
                "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
                "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
                "available": False,
                "reason": str(status.get("reason") or "Candidate queue unavailable"),
                "code": "candidate_queue_unavailable",
                "repository": _public_repository_state(base),
                "workspaces": self._workspace_catalog(base),
                "items": [],
                "transaction": _public_transaction_state(transaction),
            }
        items = []
        for item in status.get("items", []):
            if item.get("state") == "integrated":
                continue
            projected = dict(item)
            try:
                record = self.candidate_store.get(item.get("record_id"))
                proposed_mapping_patch(
                    record,
                    repository_root=base["develop_path"],
                )
            except (OSError, PlayerSaveMappingCandidateError) as exc:
                projected.update(
                    review_available=False,
                    review_code=_proposal_error_code(exc),
                    review_reason=_proposal_error_message(exc),
                )
            else:
                projected.update(
                    review_available=base["available"],
                    review_code="" if base["available"] else base["code"],
                    review_reason="" if base["available"] else base["reason"],
                )
            items.append(projected)
        return {
            "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
            "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
            "available": True,
            "reason": "",
            "code": "",
            "repository": _public_repository_state(base),
            "workspaces": self._workspace_catalog(base),
            "items": items,
            "transaction": _public_transaction_state(transaction),
        }

    def review(
        self,
        *,
        candidate_record_id: object,
        workspace_id: object,
    ) -> dict[str, Any]:
        """Return one non-mutating proposal bound to an exact feature snapshot."""

        base = self._base_state()
        if not base["available"]:
            raise SaveMappingIntegrationError(base["code"], base["reason"])
        workspace = self._selected_workspace(base, workspace_id)
        try:
            record = self.candidate_store.get(candidate_record_id)
            proposal = proposed_mapping_patch(
                record,
                repository_root=base["develop_path"],
            )
            targets = _render_proposal_targets(
                proposal,
                repository_root=base["develop_path"],
                candidate_record=record,
            )
        except (OSError, PlayerSaveMappingCandidateError) as exc:
            raise SaveMappingIntegrationError(
                _proposal_error_code(exc),
                _proposal_error_message(exc),
            ) from exc
        prepared = _workspace_prepared_state(workspace["path"], targets)
        workspace_public = _public_workspace(workspace)
        fingerprint_payload = {
            "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
            "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
            "candidate_record_id": record["record_id"],
            "repository": {
                "main_commit": base["main_commit"],
                "develop_commit": base["develop_commit"],
            },
            "workspace": {
                "workspace_id": workspace_public["workspace_id"],
                "branch": workspace_public["branch"],
                "head_commit": workspace_public["head_commit"],
            },
            "proposal": proposal,
            "rendered_targets": [
                {
                    "path": target.path,
                    "mapping_id": target.mapping_id,
                    "before_sha256": target.before_sha256,
                    "after_sha256": target.after_sha256,
                    "changed": target.changed,
                    "mode": target.mode,
                }
                for target in targets
            ],
        }
        reviewed_fingerprint = fingerprint_json(fingerprint_payload)
        transaction = _load_transaction(self.transaction_path)
        recovery_required = transaction is not None
        transaction_matches_review = bool(
            transaction
            and transaction["candidate_record_id"] == record["record_id"]
            and transaction["reviewed_proposal_fingerprint"]
            == reviewed_fingerprint
            and transaction["workspace"]["workspace_id"]
            == workspace_public["workspace_id"]
        )
        if transaction_matches_review:
            prepare = {
                "available": True,
                "code": "transaction_recovery_required",
                "reason": (
                    "An interrupted preparation for this exact review must be "
                    "recovered. Prepare again once; do not retry automatically."
                ),
            }
        elif transaction is not None:
            prepare = {
                "available": False,
                "code": "transaction_recovery_required",
                "reason": (
                    "An interrupted preparation for another reviewed selection "
                    "must be recovered first. Select its candidate and feature "
                    "worktree, then prepare that exact review once."
                ),
            }
        elif prepared and workspace["code"] == "workspace_dirty":
            prepare = {
                "available": False,
                "code": "already_prepared",
                "reason": (
                    "This exact proposal is already prepared in the selected "
                    "feature worktree. Validation, commit, and promotion remain."
                ),
            }
        elif not workspace["available"]:
            prepare = {
                "available": False,
                "code": workspace["code"] or "workspace_unavailable",
                "reason": workspace["reason"] or (
                    "The selected feature worktree is unavailable."
                ),
            }
        elif workspace["clean"]:
            try:
                workspace_targets = _render_proposal_targets(
                    proposal,
                    repository_root=workspace["path"],
                    candidate_record=record,
                )
                _require_workspace_targets_match(targets, workspace_targets)
            except SaveMappingIntegrationError as exc:
                prepare = {
                    "available": False,
                    "code": exc.code,
                    "reason": str(exc),
                }
            else:
                prepare = {"available": True, "code": "", "reason": ""}
        else:
            prepare = {
                "available": False,
                "code": workspace["code"] or "workspace_dirty",
                "reason": workspace["reason"] or (
                    "The selected feature worktree has unrelated changes."
                ),
            }
        response = {
            "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
            "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
            "operation": "review",
            "candidate_record_id": record["record_id"],
            "reviewed_proposal_fingerprint": reviewed_fingerprint,
            "repository": _public_repository_state(base),
            "workspace": workspace_public,
            "proposal": proposal,
            "prepare": prepare,
            "prepared": prepared,
            "recovery_required": recovery_required,
            "prepared_result": None,
        }
        if prepared and transaction is None:
            response["prepared_result"] = _prepared_result(
                response,
                targets,
                idempotent=True,
            )
        return response

    def prepare(
        self,
        *,
        candidate_record_id: object,
        workspace_id: object,
        reviewed_proposal_fingerprint: object,
    ) -> dict[str, Any]:
        """Prepare the exact reviewed proposal, leaving Git lifecycle untouched."""

        supplied = str(reviewed_proposal_fingerprint or "").strip().lower()
        if len(supplied) != 64 or any(
            ch not in "0123456789abcdef" for ch in supplied
        ):
            raise SaveMappingIntegrationError(
                "reviewed_proposal_fingerprint_invalid",
                "A full reviewed proposal fingerprint is required.",
            )
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise SaveMappingIntegrationError(
                "integration_lock_unavailable",
                "Canonical preparation coordination is unavailable.",
            ) from exc
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fchmod(descriptor, 0o600)
            except BlockingIOError as exc:
                raise SaveMappingIntegrationError(
                    "integration_busy",
                    "Another canonical preparation is in progress; "
                    "refresh before retrying.",
                ) from exc
            except OSError as exc:
                raise SaveMappingIntegrationError(
                    "integration_lock_unavailable",
                    "Canonical preparation coordination is unavailable.",
                ) from exc
            self._recover_interrupted_transaction(workspace_id)
            review = self.review(
                candidate_record_id=candidate_record_id,
                workspace_id=workspace_id,
            )
            expected = review["reviewed_proposal_fingerprint"]
            if supplied != expected:
                raise SaveMappingIntegrationError(
                    "reviewed_proposal_stale",
                    "The proposal or repository snapshot changed; nothing "
                    "was written. Refresh and review again.",
                )
            try:
                record = self.candidate_store.get(candidate_record_id)
            except (OSError, PlayerSaveMappingCandidateError) as exc:
                raise SaveMappingIntegrationError(
                    _proposal_error_code(exc),
                    _proposal_error_message(exc),
                ) from exc
            targets = _render_proposal_targets(
                review["proposal"],
                repository_root=Path(review["repository"]["develop_path"]),
                candidate_record=record,
            )
            workspace_path = Path(review["workspace"]["path_display"])
            if review["prepared"]:
                return _prepared_result(review, targets, idempotent=True)
            if review["prepare"]["available"] is not True:
                raise SaveMappingIntegrationError(
                    str(review["prepare"].get("code") or "prepare_unavailable"),
                    str(
                        review["prepare"].get("reason")
                        or "Preparation is unavailable."
                    ),
                )
            workspace_targets = _render_proposal_targets(
                review["proposal"],
                repository_root=workspace_path,
                candidate_record=record,
            )
            _require_workspace_targets_match(targets, workspace_targets)
            self._commit_target_group(review, workspace_targets)
            if not _workspace_prepared_state(
                workspace_path,
                workspace_targets,
            ):
                raise SaveMappingIntegrationError(
                    "prepared_state_unconfirmed",
                    "The feature-worktree write completed but its exact "
                    "prepared state could not be confirmed.",
                )
            return _prepared_result(review, workspace_targets, idempotent=False)
        finally:
            os.close(descriptor)

    def _commit_target_group(
        self,
        review: Mapping[str, Any],
        targets: Sequence[_PreparedTarget],
    ) -> None:
        transaction_document = _transaction_document(review, targets)
        try:
            _write_transaction(self.transaction_path, transaction_document)
        except SaveMappingIntegrationError:
            raise
        except OSError as exc:
            raise SaveMappingIntegrationError(
                "transaction_write_failed",
                "The canonical preparation transaction could not be recorded; "
                "nothing was written to the feature worktree.",
            ) from exc
        transaction = _validate_transaction_document(transaction_document)
        replaced_count = 0
        try:
            self._stage_transaction_targets(transaction)
            self._fault("targets_staged")
            self._verify_transaction_snapshot(transaction, phase="staged")
            workspace_root = Path(transaction["workspace"]["path"])
            for index, target in enumerate(_transaction_targets(transaction)):
                if not target.changed:
                    continue
                path = _target_path(workspace_root, target.path)
                self._fault(f"before_target_replace:{index}")
                self._verify_target(path, target, expected="before")
                stage = self._transaction_stage_path(
                    workspace_root,
                    transaction,
                    target,
                )
                os.replace(stage, path)
                replaced_count += 1
                _fsync_directory(path.parent)
                self._fault(f"target_replaced:{index}")
            self._fault("targets_replaced")
            self._verify_transaction_snapshot(transaction, phase="prepared")
        except BaseException as exc:
            if not isinstance(exc, Exception):
                # A later explicit Prepare recovers from the durable journal.
                raise
            if replaced_count == 0:
                try:
                    self._discard_unapplied_transaction(transaction)
                except SaveMappingIntegrationError as cleanup_exc:
                    raise SaveMappingIntegrationError(
                        "mapping_prepare_rollback_failed",
                        "Canonical preparation was interrupted before its first "
                        "target, but cleanup could not be confirmed; inspect the "
                        "feature worktree before continuing.",
                    ) from cleanup_exc
                if isinstance(exc, SaveMappingIntegrationError):
                    raise
                raise SaveMappingIntegrationError(
                    "mapping_prepare_write_failed",
                    "Canonical preparation failed before changing a target; the "
                    "feature worktree was restored.",
                ) from exc
            try:
                recovery = self._recover_interrupted_transaction(
                    transaction["workspace"]["workspace_id"]
                )
            except SaveMappingIntegrationError as recovery_exc:
                raise SaveMappingIntegrationError(
                    "mapping_prepare_rollback_failed",
                    "Canonical preparation changed at least one target and its "
                    "rollback could not be confirmed; inspect the feature "
                    "worktree before continuing.",
                ) from recovery_exc
            if recovery == "prepared":
                return
            raise SaveMappingIntegrationError(
                "mapping_prepare_write_failed",
                "Canonical preparation failed; every transaction-owned target "
                "was restored.",
            ) from exc
        if not _remove_transaction(self.transaction_path):
            raise SaveMappingIntegrationError(
                "prepared_state_unconfirmed",
                "The feature targets are prepared, but transaction cleanup "
                "could not be confirmed. Inspect before another action.",
            )

    def _recover_interrupted_transaction(
        self,
        workspace_id: object,
    ) -> Optional[str]:
        transaction = _load_transaction(self.transaction_path)
        if transaction is None:
            return None
        supplied = str(workspace_id or "").strip().lower()
        if supplied != transaction["workspace"]["workspace_id"]:
            raise SaveMappingIntegrationError(
                "transaction_recovery_required",
                "An interrupted canonical preparation belongs to another "
                "feature worktree; select that worktree and prepare again to "
                "recover it.",
            )
        self._verify_transaction_identity(transaction)
        workspace_root = Path(transaction["workspace"]["path"])
        targets = _transaction_targets(transaction)
        states: dict[str, str] = {}
        for target in targets:
            path = _target_path(workspace_root, target.path)
            states[target.path] = self._target_state(path, target)
        if any(state == "other" for state in states.values()):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "An interrupted canonical preparation target changed outside "
                "the transaction; nothing was overwritten. Inspect the feature "
                "worktree before continuing.",
            )
        self._verify_recovery_status(transaction, states)
        changed_states = [
            states[target.path] for target in targets if target.changed
        ]
        if changed_states and all(state == "after" for state in changed_states):
            self._fsync_prepared_target_directories(transaction)
            self._remove_transaction_stages(transaction)
            self._verify_transaction_snapshot(transaction, phase="prepared")
            if not _remove_transaction(self.transaction_path):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Prepared targets are exact, but the recovery journal could "
                    "not be retired.",
                )
            return "prepared"
        if any(state == "after" for state in changed_states):
            for target in targets:
                if not target.changed or states[target.path] != "after":
                    continue
                path = _target_path(workspace_root, target.path)
                self._verify_target(path, target, expected="after")
                stage = self._transaction_stage_path(
                    workspace_root,
                    transaction,
                    target,
                )
                if os.path.lexists(stage):
                    self._verify_recovery_stage(stage, target)
                    stage.unlink()
                    _fsync_directory(stage.parent)
                self._write_stage(stage, target.before, target.mode)
                self._verify_target(path, target, expected="after")
                os.replace(stage, path)
                _fsync_directory(path.parent)
            outcome = "rolled_back"
        else:
            outcome = "unchanged"
        self._remove_transaction_stages(transaction)
        self._verify_recovered_base(transaction)
        if not _remove_transaction(self.transaction_path):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The recovered feature targets are exact, but the transaction "
                "journal could not be retired.",
            )
        return outcome

    @staticmethod
    def _fsync_prepared_target_directories(
        transaction: Mapping[str, Any],
    ) -> None:
        workspace_root = Path(transaction["workspace"]["path"])
        directories = {
            _target_path(workspace_root, target.path).parent
            for target in _transaction_targets(transaction)
            if target.changed
        }
        try:
            for directory in directories:
                _fsync_directory(directory)
        except OSError as exc:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "Prepared target rename durability could not be confirmed; "
                "the recovery journal was retained.",
            ) from exc

    def _stage_transaction_targets(self, transaction: Mapping[str, Any]) -> None:
        workspace_root = Path(transaction["workspace"]["path"])
        for target in _transaction_targets(transaction):
            if not target.changed:
                continue
            stage = self._transaction_stage_path(
                workspace_root,
                transaction,
                target,
            )
            self._write_stage(stage, target.after, target.mode)
        for directory in {
            _target_path(workspace_root, target.path).parent
            for target in _transaction_targets(transaction)
            if target.changed
        }:
            _fsync_directory(directory)

    @staticmethod
    def _write_stage(path: Path, content: bytes, mode: int) -> None:
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                metadata, existing = _read_regular_file(path)
            except (OSError, ValueError) as exc:
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "A transaction stage cannot be inspected.",
                ) from exc
            if (
                stat.S_IMODE(metadata.st_mode) != mode
                or existing != content
            ):
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "A transaction stage changed outside canonical preparation.",
                )
            return
        try:
            os.fchmod(descriptor, mode)
            _write_all(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _verify_transaction_snapshot(
        self,
        transaction: Mapping[str, Any],
        *,
        phase: str,
    ) -> None:
        self._verify_transaction_identity(transaction)
        workspace_root = Path(transaction["workspace"]["path"])
        targets = _transaction_targets(transaction)
        expected_status: set[bytes] = set()
        for target in targets:
            path = _target_path(workspace_root, target.path)
            expected = "after" if phase == "prepared" and target.changed else "before"
            self._verify_target(path, target, expected=expected)
            if not target.changed:
                continue
            if phase == "staged":
                stage = self._transaction_stage_path(
                    workspace_root,
                    transaction,
                    target,
                )
                self._verify_stage(stage, target.after, target.mode)
                expected_status.add(
                    b"?? " + stage.relative_to(workspace_root).as_posix().encode()
                )
            elif phase == "prepared":
                expected_status.add(b" M " + target.path.encode())
            else:
                raise AssertionError(f"Unsupported transaction phase {phase!r}")
        if set(_git_status(workspace_root)) != expected_status:
            raise SaveMappingIntegrationError(
                "proposal_base_changed" if phase == "staged" else "prepared_state_unconfirmed",
                "The selected feature worktree changed outside the reviewed "
                "canonical transaction.",
            )

    def _verify_transaction_identity(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        base = self._base_state()
        repository = transaction["repository"]
        workspace = transaction["workspace"]
        if (
            not base["available"]
            or base["main_commit"] != repository["main_commit"]
            or base["develop_commit"] != repository["develop_commit"]
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A bound repository ref changed during canonical preparation; "
                "inspect the feature worktree before continuing.",
            )
        matches = [
            item
            for item in base["worktrees"]
            if item.path == Path(workspace["path"]).resolve()
            and item.branch == workspace["branch"]
            and item.head == workspace["head_commit"]
            and not item.locked
            and not item.prunable
        ]
        if len(matches) != 1:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The bound feature-worktree identity changed during canonical "
                "preparation.",
            )
        expected_workspace_id = fingerprint_json(
            {
                "schema_version": 1,
                "repository_root": str(self.repository_root),
                "path": str(matches[0].path),
                "branch": matches[0].branch,
                "head_commit": matches[0].head,
            }
        )
        if expected_workspace_id != workspace["workspace_id"]:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The bound feature-worktree snapshot identity changed.",
            )

    @staticmethod
    def _verify_target(
        path: Path,
        target: _PreparedTarget,
        *,
        expected: str,
    ) -> None:
        try:
            metadata, content = _read_regular_file(path)
        except (OSError, ValueError) as exc:
            raise SaveMappingIntegrationError(
                "proposal_base_changed",
                "A canonical target cannot be inspected.",
            ) from exc
        expected_hash = (
            target.before_sha256 if expected == "before" else target.after_sha256
        )
        if (
            stat.S_IMODE(metadata.st_mode) != target.mode
            or hashlib.sha256(content).hexdigest() != expected_hash
        ):
            raise SaveMappingIntegrationError(
                "proposal_base_changed"
                if expected == "before"
                else "prepared_state_unconfirmed",
                "A canonical target changed outside the reviewed transaction.",
            )

    @staticmethod
    def _verify_stage(path: Path, content: bytes, mode: int) -> None:
        try:
            metadata, observed = _read_regular_file(path)
        except (OSError, ValueError) as exc:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical transaction stage cannot be inspected.",
            ) from exc
        if (
            stat.S_IMODE(metadata.st_mode) != mode
            or observed != content
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical transaction stage changed unexpectedly.",
            )

    @staticmethod
    def _target_state(path: Path, target: _PreparedTarget) -> str:
        try:
            metadata, content = _read_regular_file(path)
            content_hash = hashlib.sha256(content).hexdigest()
        except (OSError, ValueError):
            return "other"
        if stat.S_IMODE(metadata.st_mode) != target.mode:
            return "other"
        if content_hash == target.before_sha256:
            return "before"
        if content_hash == target.after_sha256:
            return "after"
        return "other"

    def _verify_recovery_status(
        self,
        transaction: Mapping[str, Any],
        states: Mapping[str, str],
    ) -> None:
        workspace_root = Path(transaction["workspace"]["path"])
        expected: set[bytes] = set()
        for target in _transaction_targets(transaction):
            if target.changed and states[target.path] == "after":
                expected.add(b" M " + target.path.encode())
            stage = self._transaction_stage_path(
                workspace_root,
                transaction,
                target,
            )
            if not os.path.lexists(stage):
                continue
            self._verify_recovery_stage(stage, target)
            expected.add(
                b"?? " + stage.relative_to(workspace_root).as_posix().encode()
            )
        if set(_git_status(workspace_root)) != expected:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The interrupted feature worktree contains unrelated changes; "
                "nothing was overwritten.",
            )

    @staticmethod
    def _verify_recovery_stage(path: Path, target: _PreparedTarget) -> None:
        try:
            metadata, content = _read_regular_file(path)
            digest = hashlib.sha256(content).hexdigest()
        except (OSError, ValueError) as exc:
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A recovery stage cannot be inspected.",
            ) from exc
        if (
            stat.S_IMODE(metadata.st_mode) != target.mode
            or digest not in {target.before_sha256, target.after_sha256}
        ):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A recovery stage changed outside canonical preparation.",
            )

    def _discard_unapplied_transaction(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        # No target replacement occurred. Remove only transaction-owned stages;
        # a concurrent target edit, if any, remains untouched.
        self._remove_transaction_stages(transaction)
        if not _remove_transaction(self.transaction_path):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The unapplied canonical transaction could not be retired.",
            )

    def _remove_transaction_stages(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        workspace_root = Path(transaction["workspace"]["path"])
        directories: set[Path] = set()
        for target in _transaction_targets(transaction):
            stage = self._transaction_stage_path(
                workspace_root,
                transaction,
                target,
            )
            if not os.path.lexists(stage):
                continue
            self._verify_recovery_stage(stage, target)
            try:
                stage.unlink()
            except OSError as exc:
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "A canonical transaction stage could not be removed.",
                ) from exc
            directories.add(stage.parent)
        for directory in directories:
            try:
                _fsync_directory(directory)
            except OSError as exc:
                raise SaveMappingIntegrationError(
                    "commit_state_uncertain",
                    "Canonical transaction-stage cleanup could not be confirmed.",
                ) from exc

    def _verify_recovered_base(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        self._verify_transaction_identity(transaction)
        workspace_root = Path(transaction["workspace"]["path"])
        for target in _transaction_targets(transaction):
            self._verify_target(
                _target_path(workspace_root, target.path),
                target,
                expected="before",
            )
        if _git_status(workspace_root):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "The canonical transaction rolled back, but the feature "
                "worktree is not clean.",
            )

    @staticmethod
    def _transaction_stage_path(
        workspace_root: Path,
        transaction: Mapping[str, Any],
        target: _PreparedTarget,
    ) -> Path:
        target_path = _target_path(workspace_root, target.path)
        stage = target_path.with_name(
            f"{target_path.name}.mapping-stage-"
            f"{transaction['transaction_id']}"
        )
        if not _is_within(stage, workspace_root):
            raise SaveMappingIntegrationError(
                "commit_state_uncertain",
                "A canonical transaction stage escaped the feature worktree.",
            )
        return stage

    def _fault(self, transition: str) -> None:
        if self._transaction_fault_hook is not None:
            self._transaction_fault_hook(transition)

    def _base_state(self) -> dict[str, Any]:
        development_root = self.development_root or _configured_development_root(
            self.repository_root
        )
        worktrees = _linked_worktrees(self.repository_root)
        production = next(
            (item for item in worktrees if item.path == self.repository_root),
            None,
        )
        develop = next((item for item in worktrees if item.branch == "develop"), None)
        if production is None or production.branch != "main":
            raise SaveMappingIntegrationError(
                "production_role_invalid",
                "The control surface repository root is not the linked main worktree.",
            )
        if develop is None:
            raise SaveMappingIntegrationError(
                "develop_worktree_unavailable",
                "The linked develop worktree is unavailable.",
            )
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
        ancestor = _git_success(
            self.repository_root,
            "merge-base",
            "--is-ancestor",
            main_commit,
            develop_commit,
        )
        production_clean = not _git_status(self.repository_root)
        develop_clean = not _git_status(develop.path)
        available = True
        code = ""
        reason = ""
        if not ancestor:
            available = False
            code = "develop_not_descended_from_main"
            reason = "develop does not descend from production main."
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
            reason = "Production has tracked or untracked repository changes."
        elif not develop_clean:
            available = False
            code = "develop_worktree_dirty"
            reason = "develop has tracked or untracked repository changes."
        return {
            "available": available,
            "code": code,
            "reason": reason,
            "main_commit": main_commit,
            "develop_commit": develop_commit,
            "main_is_ancestor": ancestor,
            "production_clean": production_clean,
            "develop_clean": develop_clean,
            "develop_path": develop.path,
            "development_root": development_root,
            "worktrees": worktrees,
        }

    def _workspace_catalog(self, base: Mapping[str, Any]) -> list[dict[str, Any]]:
        workspaces = [
            self._workspace_state(item, base)
            for item in base["worktrees"]
            if item.branch.startswith("feature/")
        ]
        workspaces.sort(key=lambda item: (item["branch"], item["path_display"]))
        return [_public_workspace(item) for item in workspaces]

    def _workspace_state(
        self,
        worktree: _Worktree,
        base: Mapping[str, Any],
    ) -> dict[str, Any]:
        clean = False
        available = True
        code = ""
        reason = ""
        if not _is_within(worktree.path, Path(base["development_root"])):
            available = False
            code = "workspace_outside_development_root"
            reason = "The feature worktree is outside the configured development root."
        elif worktree.locked or worktree.prunable:
            available = False
            code = "workspace_link_unavailable"
            reason = "The linked feature worktree is locked or prunable."
        else:
            try:
                clean = not _git_status(worktree.path)
            except SaveMappingIntegrationError:
                available = False
                code = "workspace_inspection_failed"
                reason = "The linked feature worktree could not be inspected."
        if available and (
            not _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                str(base["main_commit"]),
                worktree.head,
            )
            or not _git_success(
                self.repository_root,
                "merge-base",
                "--is-ancestor",
                str(base["develop_commit"]),
                worktree.head,
            )
        ):
            available = False
            code = "workspace_not_descended_from_integration_base"
            reason = (
                "The feature worktree does not descend from the current main "
                "and develop tips."
            )
        elif available and not clean:
            available = False
            code = "workspace_dirty"
            reason = "The feature worktree has tracked or untracked changes."
        elif available and not base["available"]:
            available = False
            code = str(base["code"])
            reason = str(base["reason"])
        workspace_id = fingerprint_json(
            {
                "schema_version": 1,
                "repository_root": str(self.repository_root),
                "path": str(worktree.path),
                "branch": worktree.branch,
                "head_commit": worktree.head,
            }
        )
        return {
            "workspace_id": workspace_id,
            "path": worktree.path,
            "path_display": str(worktree.path),
            "branch": worktree.branch,
            "head_commit": worktree.head,
            "role": "feature",
            "clean": clean,
            "available": available,
            "code": code,
            "reason": reason,
        }

    def _selected_workspace(
        self,
        base: Mapping[str, Any],
        workspace_id: object,
    ) -> dict[str, Any]:
        supplied = str(workspace_id or "").strip().lower()
        matches = [
            item
            for item in (
                self._workspace_state(worktree, base)
                for worktree in base["worktrees"]
                if worktree.branch.startswith("feature/")
            )
            if item["workspace_id"] == supplied
        ]
        if len(matches) != 1:
            raise SaveMappingIntegrationError(
                "workspace_snapshot_stale",
                "The selected feature-worktree snapshot is unavailable or changed.",
            )
        return matches[0]


def _configured_development_root(repository_root: Path) -> Path:
    path = repository_root / "requirements" / "development-environment.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        environment_root = Path(str(payload["environment_root"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SaveMappingIntegrationError(
            "development_root_unavailable",
            "The configured development-worktree root is unavailable.",
        ) from exc
    if not environment_root.is_absolute():
        raise SaveMappingIntegrationError(
            "development_root_invalid",
            "The configured development environment root is not absolute.",
        )
    return environment_root.resolve().parent


def _linked_worktrees(repository_root: Path) -> list[_Worktree]:
    raw = _git_bytes(repository_root, "worktree", "list", "--porcelain", "-z")
    records: list[_Worktree] = []
    current: dict[str, Any] = {}
    for token in raw.split(b"\0"):
        if not token:
            if current:
                records.append(_worktree_record(current))
                current = {}
            continue
        key, _, value = token.partition(b" ")
        name = key.decode("ascii", "strict")
        if name in {"detached", "bare", "locked", "prunable"} and not value:
            current[name] = True
        else:
            current[name] = value.decode("utf-8", "surrogateescape")
    if current:
        records.append(_worktree_record(current))
    if not records:
        raise SaveMappingIntegrationError(
            "worktree_catalog_unavailable",
            "Git did not report any linked worktrees.",
        )
    return records


def _worktree_record(record: Mapping[str, Any]) -> _Worktree:
    try:
        path = Path(str(record["worktree"])).resolve()
        head = str(record["HEAD"])
    except (KeyError, OSError) as exc:
        raise SaveMappingIntegrationError(
            "worktree_catalog_invalid",
            "Git returned an incomplete linked-worktree record.",
        ) from exc
    branch_ref = str(record.get("branch") or "")
    branch = branch_ref.removeprefix("refs/heads/")
    if not branch or record.get("detached"):
        branch = "(detached)"
    return _Worktree(
        path=path,
        head=head,
        branch=branch,
        locked=bool(record.get("locked")),
        prunable=bool(record.get("prunable")),
    )


def _git_command(repository_root: Path, *arguments: str) -> list[str]:
    return [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "diff.external=",
        "-C",
        str(repository_root),
        *arguments,
    ]


def _git_run(
    repository_root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            _git_command(repository_root, *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_EXTERNAL_DIFF": "",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SaveMappingIntegrationError(
            "git_inspection_failed",
            "The linked-worktree catalog could not be inspected.",
        ) from exc
    if check and result.returncode != 0:
        raise SaveMappingIntegrationError(
            "git_inspection_failed",
            "The linked-worktree catalog could not be inspected.",
        )
    return result


def _git_bytes(repository_root: Path, *arguments: str) -> bytes:
    return _git_run(repository_root, *arguments).stdout


def _git_output(repository_root: Path, *arguments: str) -> str:
    try:
        return _git_bytes(repository_root, *arguments).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise SaveMappingIntegrationError(
            "git_inspection_failed",
            "Git returned a non-ASCII object identity.",
        ) from exc


def _git_success(repository_root: Path, *arguments: str) -> bool:
    return _git_run(repository_root, *arguments, check=False).returncode == 0


def _git_status(repository_root: Path) -> tuple[bytes, ...]:
    output = _git_bytes(
        repository_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    return tuple(item for item in output.split(b"\0") if item)


def _public_repository_state(base: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "main_commit": base["main_commit"],
        "develop_commit": base["develop_commit"],
        "main_is_ancestor": base["main_is_ancestor"],
        "production_clean": base["production_clean"],
        "develop_clean": base["develop_clean"],
        "develop_path": str(base["develop_path"]),
        "available": base["available"],
        "code": base["code"],
        "reason": base["reason"],
    }


def _public_workspace(workspace: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": workspace["workspace_id"],
        "path_display": workspace["path_display"],
        "branch": workspace["branch"],
        "head_commit": workspace["head_commit"],
        "role": workspace["role"],
        "clean": workspace["clean"],
        "available": workspace["available"],
        "code": workspace["code"],
        "reason": workspace["reason"],
    }


def _public_transaction_state(
    transaction: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    if transaction is None:
        return None
    workspace = transaction["workspace"]
    return {
        "recovery_required": True,
        "candidate_record_id": transaction["candidate_record_id"],
        "reviewed_proposal_fingerprint": transaction[
            "reviewed_proposal_fingerprint"
        ],
        "workspace_id": workspace["workspace_id"],
        "branch": workspace["branch"],
        "path_display": workspace["path"],
        "head_commit": workspace["head_commit"],
    }


def _proposal_error_code(exc: BaseException) -> str:
    message = str(exc)
    if message.startswith("mapping_candidate_"):
        return message
    return "mapping_proposal_unavailable"


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
    }
    return messages.get(str(exc), "The canonical mapping proposal is unavailable.")


def _render_proposal_targets(
    proposal: Mapping[str, Any],
    *,
    repository_root: Path,
    candidate_record: Mapping[str, Any],
) -> list[_PreparedTarget]:
    root = repository_root.resolve()
    if proposal.get("schema_version") == 2:
        raw_targets = proposal.get("targets")
        if not isinstance(raw_targets, Sequence) or isinstance(
            raw_targets, (str, bytes, bytearray)
        ):
            raise SaveMappingIntegrationError(
                "proposal_targets_invalid",
                "The reviewed proposal has no valid atomic target group.",
            )
        target_specs = list(raw_targets)
    else:
        target = proposal.get("target")
        operations = proposal.get("operations")
        if not isinstance(target, Mapping) or not isinstance(operations, list):
            raise SaveMappingIntegrationError(
                "proposal_targets_invalid",
                "The reviewed proposal has no valid target.",
            )
        target_specs = [{**dict(target), "operations": operations}]
    rendered: list[_PreparedTarget] = []
    seen: set[Path] = set()
    for spec in target_specs:
        if not isinstance(spec, Mapping):
            raise SaveMappingIntegrationError(
                "proposal_target_invalid",
                "A reviewed proposal target is invalid.",
            )
        relative = str(spec.get("path") or "")
        path = _target_path(root, relative)
        if path in seen:
            raise SaveMappingIntegrationError(
                "proposal_target_invalid",
                "A reviewed proposal target escapes or duplicates the repository root.",
            )
        seen.add(path)
        try:
            metadata, before = _read_regular_file(path)
        except ValueError as exc:
            raise SaveMappingIntegrationError(
                "proposal_target_invalid",
                "A reviewed canonical mapping target is not a regular file.",
            ) from exc
        except OSError as exc:
            raise SaveMappingIntegrationError(
                "proposal_target_unavailable",
                "A reviewed canonical mapping target is unavailable.",
            ) from exc
        before_hash = hashlib.sha256(before).hexdigest()
        if before_hash != spec.get("expected_sha256"):
            raise SaveMappingIntegrationError(
                "proposal_base_changed",
                "A canonical mapping base hash changed; nothing was written. "
                "Refresh and review again.",
            )
        operations = spec.get("operations")
        if not isinstance(operations, list):
            raise SaveMappingIntegrationError(
                "proposal_operations_invalid",
                "A reviewed proposal target has invalid operations.",
            )
        after = before
        for operation in operations:
            after = _apply_json_operation(after, operation)
        try:
            rendered_mapping = json.loads(after)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SaveMappingIntegrationError(
                "proposal_result_invalid",
                "The reviewed proposal did not produce valid JSON.",
            ) from exc
        try:
            validate_mapping_candidate_result(
                candidate_record,
                rendered_mapping,
            )
        except PlayerSaveMappingCandidateError as exc:
            raise SaveMappingIntegrationError(
                "proposal_result_invalid",
                "The reviewed proposal did not produce the exact canonical "
                "candidate mapping.",
            ) from exc
        rendered.append(
            _PreparedTarget(
                path=relative,
                mapping_id=str(spec.get("mapping_id") or ""),
                before_sha256=before_hash,
                after_sha256=hashlib.sha256(after).hexdigest(),
                before=before,
                after=after,
                changed=after != before,
                mode=stat.S_IMODE(metadata.st_mode),
            )
        )
    if not any(item.changed for item in rendered):
        raise SaveMappingIntegrationError(
            "proposal_has_no_changes",
            "The reviewed proposal has no pending canonical changes.",
        )
    return rendered


def _require_workspace_targets_match(
    reviewed_targets: Sequence[_PreparedTarget],
    workspace_targets: Sequence[_PreparedTarget],
) -> None:
    reviewed = [
        (
            target.path,
            target.mapping_id,
            target.before_sha256,
            target.after_sha256,
            target.changed,
            target.mode,
        )
        for target in reviewed_targets
    ]
    workspace = [
        (
            target.path,
            target.mapping_id,
            target.before_sha256,
            target.after_sha256,
            target.changed,
            target.mode,
        )
        for target in workspace_targets
    ]
    if workspace != reviewed:
        raise SaveMappingIntegrationError(
            "proposal_workspace_mismatch",
            "The selected feature target content or file mode differs from the "
            "reviewed integration base; nothing was written. Refresh after "
            "reconciling the feature worktree.",
        )


def _apply_json_operation(source: bytes, operation: object) -> bytes:
    if not isinstance(operation, Mapping) or set(operation) != {
        "op",
        "path",
        "value",
    }:
        raise SaveMappingIntegrationError(
            "proposal_operation_invalid",
            "A reviewed JSON operation is invalid.",
        )
    op = str(operation["op"])
    if op not in {"add", "replace"}:
        raise SaveMappingIntegrationError(
            "proposal_operation_invalid",
            "A reviewed JSON operation is not allowlisted.",
        )
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SaveMappingIntegrationError(
            "proposal_target_invalid",
            "A canonical mapping target is not UTF-8 JSON.",
        ) from exc
    parser = _JsonSpanParser(text)
    root = parser.parse()
    path = str(operation["path"])
    tokens = _json_pointer_tokens(path)
    if not tokens:
        raise SaveMappingIntegrationError(
            "proposal_operation_invalid",
            "Replacing the JSON document root is not allowed.",
        )
    parent = _json_pointer_node(root, tokens[:-1])
    leaf = tokens[-1]
    value = operation["value"]
    if parent.kind == "object":
        members = parent.members or {}
        existing = members.get(leaf)
        if op == "replace":
            if existing is None:
                raise SaveMappingIntegrationError(
                    "proposal_operation_stale",
                    "A reviewed replace target no longer exists.",
                )
            replacement = _inline_json(value)
            return (text[: existing.start] + replacement + text[existing.end :]).encode(
                "utf-8"
            )
        if existing is not None:
            raise SaveMappingIntegrationError(
                "proposal_operation_stale",
                "A reviewed add target already exists.",
            )
        updated = _insert_object_member(text, parent, leaf, value)
        return updated.encode("utf-8")
    if parent.kind == "array" and leaf == "-" and op == "add":
        return _append_array_item(text, parent, value).encode("utf-8")
    raise SaveMappingIntegrationError(
        "proposal_operation_invalid",
        "A reviewed JSON operation does not target an allowlisted container.",
    )


class _JsonSpanParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)

    def parse(self) -> _JsonNode:
        node, position = self._value(self._space(0))
        if self._space(position) != self.length:
            self._error()
        return node

    def _space(self, position: int) -> int:
        while position < self.length and self.text[position] in " \t\r\n":
            position += 1
        return position

    def _value(self, position: int) -> tuple[_JsonNode, int]:
        if position >= self.length:
            self._error()
        token = self.text[position]
        if token == "{":
            return self._object(position)
        if token == "[":
            return self._array(position)
        if token == '"':
            end = self._string_end(position)
            return _JsonNode("scalar", position, end), end
        decoder = json.JSONDecoder()
        try:
            _, end = decoder.raw_decode(self.text, position)
        except json.JSONDecodeError:
            self._error()
        return _JsonNode("scalar", position, end), end

    def _object(self, start: int) -> tuple[_JsonNode, int]:
        position = self._space(start + 1)
        members: dict[str, _JsonNode] = {}
        if position < self.length and self.text[position] == "}":
            return (
                _JsonNode("object", start, position + 1, members=members),
                position + 1,
            )
        while True:
            if position >= self.length or self.text[position] != '"':
                self._error()
            key_end = self._string_end(position)
            try:
                key = json.loads(self.text[position:key_end])
            except json.JSONDecodeError:
                self._error()
            if key in members:
                self._error()
            position = self._space(key_end)
            if position >= self.length or self.text[position] != ":":
                self._error()
            value, position = self._value(self._space(position + 1))
            members[str(key)] = value
            position = self._space(position)
            if position < self.length and self.text[position] == ",":
                position = self._space(position + 1)
                continue
            if position < self.length and self.text[position] == "}":
                end = position + 1
                return _JsonNode("object", start, end, members=members), end
            self._error()

    def _array(self, start: int) -> tuple[_JsonNode, int]:
        position = self._space(start + 1)
        items: list[_JsonNode] = []
        if position < self.length and self.text[position] == "]":
            return _JsonNode("array", start, position + 1, items=items), position + 1
        while True:
            item, position = self._value(position)
            items.append(item)
            position = self._space(position)
            if position < self.length and self.text[position] == ",":
                position = self._space(position + 1)
                continue
            if position < self.length and self.text[position] == "]":
                end = position + 1
                return _JsonNode("array", start, end, items=items), end
            self._error()

    def _string_end(self, start: int) -> int:
        position = start + 1
        escaped = False
        while position < self.length:
            token = self.text[position]
            if escaped:
                escaped = False
            elif token == "\\":
                escaped = True
            elif token == '"':
                return position + 1
            position += 1
        self._error()

    def _error(self) -> None:
        raise SaveMappingIntegrationError(
            "proposal_target_invalid",
            "A canonical mapping target is not valid JSON.",
        )


def _json_pointer_tokens(path: str) -> list[str]:
    if not path.startswith("/"):
        raise SaveMappingIntegrationError(
            "proposal_operation_invalid",
            "A reviewed JSON operation has an invalid pointer.",
        )
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in path[1:].split("/")
    ]


def _json_pointer_node(root: _JsonNode, tokens: Sequence[str]) -> _JsonNode:
    node = root
    for token in tokens:
        if node.kind == "object":
            child = (node.members or {}).get(token)
        elif node.kind == "array" and token.isdigit():
            index = int(token)
            items = node.items or []
            child = items[index] if 0 <= index < len(items) else None
        else:
            child = None
        if child is None:
            raise SaveMappingIntegrationError(
                "proposal_operation_stale",
                "A reviewed JSON pointer no longer resolves.",
            )
        node = child
    return node


def _line_indent(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    prefix = text[start:position]
    return prefix[: len(prefix) - len(prefix.lstrip(" \t"))]


def _block_json(value: object, indent: str) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
    )
    lines = rendered.splitlines()
    return lines[0] + "".join(f"\n{indent}{line}" for line in lines[1:])


def _inline_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SaveMappingIntegrationError(
            "proposal_operation_invalid",
            "A reviewed JSON value is invalid.",
        ) from exc


def _insert_object_member(
    text: str,
    node: _JsonNode,
    key: str,
    value: object,
) -> str:
    members = node.members or {}
    close = node.end - 1
    close_indent = _line_indent(text, close)
    child_indent = close_indent + "  "
    rendered = (
        f"{json.dumps(key, ensure_ascii=False)}: "
        f"{_block_json(value, child_indent)}"
    )
    if members:
        last = list(members.values())[-1]
        insertion = f",\n{child_indent}{rendered}"
        return text[: last.end] + insertion + text[last.end :]
    insertion = f"\n{child_indent}{rendered}\n{close_indent}"
    return text[:close] + insertion + text[close:]


def _append_array_item(text: str, node: _JsonNode, value: object) -> str:
    items = node.items or []
    close = node.end - 1
    close_indent = _line_indent(text, close)
    child_indent = close_indent + "  "
    rendered = _block_json(value, child_indent)
    if items:
        insertion = f",\n{child_indent}{rendered}"
        return text[: items[-1].end] + insertion + text[items[-1].end :]
    insertion = f"\n{child_indent}{rendered}\n{close_indent}"
    return text[:close] + insertion + text[close:]


def _workspace_prepared_state(
    workspace_root: Path,
    targets: Sequence[_PreparedTarget],
) -> bool:
    changed = {target.path for target in targets if target.changed}
    statuses = _git_status(workspace_root)
    if len(statuses) != len(changed):
        return False
    observed: set[str] = set()
    for status_entry in statuses:
        if len(status_entry) < 4 or status_entry[:3] != b" M ":
            return False
        observed.add(status_entry[3:].decode("utf-8", "surrogateescape"))
    if observed != changed:
        return False
    for target in targets:
        try:
            path = _target_path(workspace_root, target.path)
            metadata, content = _read_regular_file(path)
        except (OSError, ValueError, SaveMappingIntegrationError):
            return False
        expected = target.after if target.changed else target.before
        if (
            stat.S_IMODE(metadata.st_mode) != target.mode
            or content != expected
        ):
            return False
    return True


def _prepared_result(
    review: Mapping[str, Any],
    targets: Sequence[_PreparedTarget],
    *,
    idempotent: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION,
        "capability": SAVE_MAPPING_INTEGRATION_CAPABILITY,
        "operation": "prepare",
        "disposition": "prepared",
        "idempotent": idempotent,
        "candidate_record_id": review["candidate_record_id"],
        "reviewed_proposal_fingerprint": review[
            "reviewed_proposal_fingerprint"
        ],
        "repository": review["repository"],
        "workspace": review["workspace"],
        "committed": False,
        "promoted": False,
        "validation_status": "pending",
        "targets": [
            {
                "path": target.path,
                "mapping_id": target.mapping_id,
                "before_sha256": target.before_sha256,
                "after_sha256": target.after_sha256,
                "changed": target.changed,
            }
            for target in targets
        ],
        "validation": list(review["proposal"].get("validation") or []),
    }


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short mapping integration write")
        offset += written


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _target_path(repository_root: Path, relative: str) -> Path:
    root = Path(repository_root)
    relative_path = Path(relative)
    if (
        not relative
        or not root.is_absolute()
        or relative_path.is_absolute()
        or not relative_path.parts
        or ".." in relative_path.parts
    ):
        raise SaveMappingIntegrationError(
            "proposal_target_invalid",
            "A canonical target path is not a repository-relative file.",
        )
    candidate = root / relative_path
    if not _is_within(candidate, root):
        raise SaveMappingIntegrationError(
            "proposal_target_invalid",
            "A canonical target path escaped the feature worktree.",
        )
    parent = root
    for component in relative_path.parts[:-1]:
        parent /= component
        try:
            metadata = parent.lstat()
        except OSError as exc:
            raise SaveMappingIntegrationError(
                "proposal_target_invalid",
                "A canonical target parent is unavailable.",
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SaveMappingIntegrationError(
                "proposal_target_invalid",
                "A canonical target parent is not a lexical repository directory.",
            )
    return candidate


def _read_regular_file(path: Path) -> tuple[os.stat_result, bytes]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("path is not a lexical regular file")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or stat.S_IMODE(opened.st_mode) != stat.S_IMODE(metadata.st_mode)
        ):
            raise OSError("regular file identity changed while opening")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
            or stat.S_IMODE(final.st_mode) != stat.S_IMODE(opened.st_mode)
            or final.st_size != len(content)
        ):
            raise OSError("regular file changed while reading")
        return final, content
    finally:
        os.close(descriptor)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


__all__ = [
    "SAVE_MAPPING_INTEGRATION_CAPABILITY",
    "SAVE_MAPPING_INTEGRATION_SCHEMA_VERSION",
    "SaveMappingIntegrationError",
    "SaveMappingIntegrationManager",
]
