"""Bound recorder for natural UI fallback against one save snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional

from core.player_save import PlayerSaveSnapshot
from core.player_save_confirmed_local_mapping import (
    ConfirmedLocalMappingError,
    ConfirmedLocalMappingStore,
)
from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    PlayerSaveMappingCandidateError,
    build_mapping_candidate_record,
    fingerprint_json,
    resolve_mapping_candidates,
)
from utils.logger import log


class BoundPlayerSaveMappingObserver:
    """Pair one retained exact save with UI evidence while continuity holds."""

    def __init__(
        self,
        *,
        snapshot: PlayerSaveSnapshot,
        context_guard_fn: Callable[[], bool],
        workflow_provenance: Mapping[str, Any],
        candidate_store: Optional[AppendOnlyMappingCandidateStore] = None,
        confirmation_store: Optional[ConfirmedLocalMappingStore] = None,
    ) -> None:
        self._snapshot = snapshot
        self._context_guard_fn = context_guard_fn
        self._workflow_provenance = dict(workflow_provenance)
        self._candidate_store = candidate_store or AppendOnlyMappingCandidateStore()
        self._confirmation_store = confirmation_store or ConfirmedLocalMappingStore()
        self._closed = False
        self._record_ids: set[str] = set()
        self._observation_keys: set[str] = set()

    def close(self, reason: str = "correlation_closed") -> None:
        self._closed = True
        log(
            "[PLAYER_SAVE_MAPPING] Bound candidate correlation closed: "
            f"reason={str(reason or 'correlation_closed')}",
            "DEBUG",
        )

    def record_mapping_observation(
        self,
        check_id: str,
        ui_evidence: Mapping[str, Any],
    ) -> int:
        snapshot = self._snapshot
        normalized = str(check_id or "").strip()
        try:
            context_valid = self._context_guard_fn() is True
        except Exception:
            context_valid = False
        if self._closed or not context_valid:
            self._closed = True
            return 0
        evidence = snapshot.checks.get(normalized)
        diagnostics = getattr(evidence, "diagnostics", {})
        pending = (
            diagnostics.get("mapping_candidates")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if (
            snapshot.mapping_id is None
            or snapshot.data_version is None
            or snapshot.game_version is None
            or snapshot.mapping_resolution
            not in {"exact", "compatible_exact_revision"}
            or not isinstance(pending, list)
            or not pending
            or not isinstance(ui_evidence, Mapping)
            or ui_evidence.get("pre_mutation") is not True
        ):
            return 0
        try:
            resolved = resolve_mapping_candidates(
                normalized,
                pending,
                ui_evidence,
            )
        except PlayerSaveMappingCandidateError:
            return 0
        mapping = {
            "mapping_id": snapshot.mapping_id,
            "data_version": snapshot.data_version,
            "game_version": snapshot.game_version,
            "root_class": snapshot.root_class,
            "resolution": snapshot.mapping_resolution,
            "authority_mapping_id": snapshot.mapping_authority_id,
            "structural_mapping_id": snapshot.mapping_structural_id,
            "canonical_dependency_fingerprint": (
                snapshot.mapping_semantic_fingerprint
            ),
        }
        snapshot_fingerprint = fingerprint_json(
            {
                "schema_version": 1,
                "source_sha256": snapshot.source_sha256,
                "mapping_semantic_fingerprint": (
                    snapshot.mapping_semantic_fingerprint
                ),
            }
        )
        ui_fingerprint = fingerprint_json(dict(ui_evidence))
        recorded = 0
        for candidate in resolved:
            observation_key = fingerprint_json(
                {
                    "check_id": normalized,
                    "source_observation_fingerprint": ui_evidence.get(
                        "source_observation_fingerprint"
                    ),
                    "candidate": candidate,
                }
            )
            if observation_key in self._observation_keys:
                continue
            try:
                record = build_mapping_candidate_record(
                    mapping=mapping,
                    check_id=normalized,
                    candidate=candidate,
                    snapshot_fingerprint=snapshot_fingerprint,
                    ui_evidence_fingerprint=ui_fingerprint,
                    source_observation_fingerprint=ui_evidence.get(
                        "source_observation_fingerprint"
                    ),
                    workflow_provenance=self._workflow_provenance,
                    observed_at=ui_evidence.get("observed_at"),
                )
            except PlayerSaveMappingCandidateError:
                continue
            record_id = record["record_id"]
            if record_id in self._record_ids:
                continue
            try:
                appended = self._candidate_store.append_once(record)
            except Exception:
                log(
                    "[PLAYER_SAVE_MAPPING] Bound candidate receipt write "
                    "failed; UI fallback and action authority are unchanged",
                    "WARN",
                    console=True,
                )
                continue
            self._record_ids.add(record_id)
            self._observation_keys.add(observation_key)
            if appended:
                recorded += 1
            payload = record["candidate"]
            if not (
                payload["status"] == "ready_for_review"
                and payload["check_id"] == "modules"
                and payload["value_kind"] == "module_info_index"
                and snapshot.mapping_semantic_fingerprint is not None
            ):
                continue
            try:
                durable_record = self._candidate_store.get(record_id)
                accepted = self._confirmation_store.accept_candidate(
                    durable_record
                )
            except (
                ConfirmedLocalMappingError,
                PlayerSaveMappingCandidateError,
                OSError,
            ) as exc:
                log(
                    "[PLAYER_SAVE_MAPPING] Bound local confirmation write "
                    f"failed: {exc}; candidate remains pending",
                    "WARN",
                    console=True,
                )
                continue
            log(
                "[PLAYER_SAVE_MAPPING] Bound exact-version local "
                f"confirmation {'accepted' if accepted['changed'] else 'already active'}; "
                "it is eligible only on a later fresh decode",
                "WARN",
                console=True,
            )
        return recorded


__all__ = ["BoundPlayerSaveMappingObserver"]
