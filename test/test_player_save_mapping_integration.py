from __future__ import annotations

from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    build_mapping_candidate_record,
    pending_mapping_candidate,
    resolve_mapping_candidates,
)
from core.player_save_mapping_staged_candidate import (
    CanonicalDecodeReceiptStore,
    PROMOTION_OWNER_REF,
    SAVE_MAPPING_AUTOMATIC_PROMOTION_CAPABILITY,
    SAVE_MAPPING_DISPOSITION_CAPABILITY,
    SAVE_MAPPING_INTEGRATION_CAPABILITY,
    SAVE_MAPPING_MACHINE_VERIFICATION_CAPABILITY,
    SAVE_MAPPING_STAGING_REF,
    SaveMappingIntegrationError,
    SaveMappingIntegrationManager,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = "2026-08-10T12:00:00+00:00"
FIXTURE_INFO_INDEX = 9_000_000_001
FIXTURE_MODULE_NAME = "Integration Fixture Cannon Assist"


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _record(
    *,
    module_name: str = FIXTURE_MODULE_NAME,
    source_fingerprint: str = "1" * 64,
    recorded_at: str = "2026-08-10T12:00:01+00:00",
    slot_key: str = "cannon_assist",
) -> dict:
    slots = {
        "cannon_primary": "Amplifying Strike",
        "armor_primary": "Orbital Augment",
        "generator_primary": "Project Funding",
        "core_primary": "Dimension Core",
        "cannon_assist": "Being Annihilator",
        "armor_assist": "Space Displacer",
        "generator_assist": "Singularity Harness",
        "core_assist": "Harmony Conductor",
    }
    slots[slot_key] = module_name
    family, role = slot_key.rsplit("_", 1)
    pending = pending_mapping_candidate(
        value_kind="module_info_index",
        raw_value=FIXTURE_INFO_INDEX,
        pairing_method="exact_locator",
        locator=slot_key,
        expected_observation_count=8,
        peer_locator_values={
            locator: value
            for locator, value in slots.items()
            if locator != slot_key
        },
        scope={"slot_key": slot_key, "family": family, "role": role},
    )
    resolved = resolve_mapping_candidates(
        "modules",
        [pending],
        {
            "canonical_values": list(slots.values()),
            "locator_values": slots,
            "locator_scopes": {
                locator: {
                    "slot_key": locator,
                    "family": locator.rsplit("_", 1)[0],
                    "role": locator.rsplit("_", 1)[1],
                }
                for locator in slots
            },
            "complete": True,
            "pre_mutation": True,
            "observed_at": OBSERVED_AT,
            "source_observation_fingerprint": source_fingerprint,
        },
    )[0]
    return build_mapping_candidate_record(
        mapping={
            "mapping_id": "data-9-game-1101",
            "data_version": 9,
            "game_version": 1101,
            "root_class": "SaveLoad+PlayerData",
            "resolution": "compatible_exact_revision",
            "authority_mapping_id": "data-9-game-1073",
            "structural_mapping_id": "data-9-game-1101",
            "canonical_dependency_fingerprint": "9" * 64,
        },
        check_id="modules",
        candidate=resolved,
        snapshot_fingerprint="a" * 64,
        ui_evidence_fingerprint="b" * 64,
        source_observation_fingerprint=source_fingerprint,
        workflow_provenance={
            "capture_request_id": "capture-1",
            "inspection_request_id": "inspection-1",
            "runtime_session_fingerprint": "c" * 64,
            "pid": 4242,
            "target_generation_fingerprint": "d" * 64,
            "activity_scope_fingerprint": "e" * 64,
            "game_state": "active_battle",
            "active_round_identity_fingerprint": "7" * 64,
            "boundary_fingerprint": "f" * 64,
        },
        observed_at=OBSERVED_AT,
        recorded_at=recorded_at,
    )


def _killed_by_record(
    *,
    game_state: str = "terminal_game_over",
    boundary_fingerprint: str = "b" * 64,
) -> dict:
    pending = pending_mapping_candidate(
        value_kind="battle_history_killed_by_id",
        raw_value=9,
        pairing_method="exact_locator",
        locator="killed_by",
        expected_observation_count=1,
        known_semantic_values=("Boss",),
    )
    resolved = resolve_mapping_candidates(
        "battle_history_killed_by",
        [pending],
        {
            "canonical_values": ["Ray"],
            "locator_values": {"killed_by": "Ray"},
            "locator_scopes": {},
            "complete": True,
            "pre_mutation": True,
            "observed_at": OBSERVED_AT,
            "source_observation_fingerprint": "3" * 64,
        },
    )[0]
    return build_mapping_candidate_record(
        mapping={
            "mapping_id": "data-9-game-1101",
            "data_version": 9,
            "game_version": 1101,
            "root_class": "SaveLoad+PlayerData",
            "resolution": "compatible_exact_revision",
            "authority_mapping_id": "data-9-game-1073",
            "structural_mapping_id": "data-9-game-1101",
            "canonical_dependency_fingerprint": "9" * 64,
        },
        check_id="battle_history_killed_by",
        candidate=resolved,
        snapshot_fingerprint="4" * 64,
        ui_evidence_fingerprint="5" * 64,
        source_observation_fingerprint="3" * 64,
        workflow_provenance={
            "capture_request_id": "capture-killed-by",
            "inspection_request_id": "inspection-killed-by",
            "runtime_session_fingerprint": "6" * 64,
            "pid": 4242,
            "target_generation_fingerprint": "7" * 64,
            "activity_scope_fingerprint": "8" * 64,
            "game_state": game_state,
            "active_round_identity_fingerprint": "a" * 64,
            "boundary_fingerprint": boundary_fingerprint,
        },
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-10T12:02:01+00:00",
    )


@pytest.fixture
def integration_repository(tmp_path: Path):
    production = tmp_path / "TheTower"
    mapping_dir = production / "config" / "player_save_versions"
    mapping_dir.mkdir(parents=True)
    for name in ("data_9_game_1073.json", "data_9_game_1101.json"):
        shutil.copyfile(
            ROOT / "config" / "player_save_versions" / name,
            mapping_dir / name,
        )
        (mapping_dir / name).chmod(0o664)
    # The repository itself now contains the real 9 -> Ray integration.  This
    # fixture deliberately models its immediately preceding production state
    # so the staged-candidate lifecycle remains testable.
    authority_path = mapping_dir / "data_9_game_1073.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["runtime_save"]["battle_history"]["killed_by_ids"].pop(
        "9",
        None,
    )
    authority_path.write_text(
        json.dumps(authority, indent=2) + "\n",
        encoding="utf-8",
    )
    _git(production, "init", "-b", "main")
    _git(production, "config", "user.name", "TheTower Test")
    _git(production, "config", "user.email", "thetower@example.invalid")
    (production / "operator-owned.txt").write_text("base\n", encoding="utf-8")
    _git(production, "add", "config", "operator-owned.txt")
    _git(production, "commit", "-m", "mapping base")
    store = AppendOnlyMappingCandidateStore(tmp_path / "receipts.jsonl")
    record = _record()
    store.append_once(record)
    manager = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=tmp_path / "integration.lock",
        transaction_path=tmp_path / "integration-transaction.json",
        decode_receipt_path=tmp_path / "decode-receipts.jsonl",
    )
    return production, store, record, manager


def _review(manager: SaveMappingIntegrationManager, record: dict) -> dict:
    return manager.review(candidate_record_id=record["record_id"])


def _stage(manager, record, review):
    return manager.stage(
        candidate_record_id=record["record_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )


def _transaction(manager: SaveMappingIntegrationManager) -> dict:
    return json.loads(manager.transaction_path.read_text(encoding="utf-8"))


def _promote(production: Path) -> None:
    _git(production, "merge", "--ff-only", SAVE_MAPPING_STAGING_REF)


def _configure_origin(production: Path) -> Path:
    origin = production.parent / "origin.git"
    _git(origin.parent, "init", "--bare", "-b", "main", str(origin))
    _git(production, "remote", "add", "origin", str(origin))
    _git(production, "push", "-u", "origin", "main")
    return origin


def _start_evidence(production: Path, transaction: dict) -> dict[str, str]:
    started = datetime.fromisoformat(
        transaction["integration_available_since"]
    ) + timedelta(seconds=1)
    return {
        "main_commit": _git(production, "rev-parse", "main"),
        "acquired_at": started.isoformat(),
    }


def _snapshot(transaction: dict, *, fingerprint: str | None = None):
    identity = transaction["mapping_identity"]
    return SimpleNamespace(
        shape_valid=True,
        canonical_mapping_fingerprint=(
            fingerprint or transaction["canonical_mapping_fingerprint"]
        ),
        mapping_id=identity["mapping_id"],
        mapping_authority_id=identity["authority_mapping_id"],
        mapping_structural_id=identity["structural_mapping_id"],
        source_sha256="6" * 64,
        captured_at="2026-08-10T12:05:00+00:00",
    )


def _unrelated_commit(production: Path, text: str = "advanced\n") -> str:
    (production / "operator-owned.txt").write_text(text, encoding="utf-8")
    _git(production, "add", "operator-owned.txt")
    _git(production, "commit", "-m", "unrelated main advance")
    return _git(production, "rev-parse", "main")


def _detached_commit(production: Path, subject: str) -> str:
    tree = _git(production, "rev-parse", "main^{tree}")
    return _git(
        production,
        "commit-tree",
        tree,
        "-p",
        _git(production, "rev-parse", "main"),
        "-m",
        subject,
    )


def test_catalog_needs_only_clean_main_and_empty_private_staging_ref(
    integration_repository,
):
    production, _store, record, manager = integration_repository

    catalog = manager.catalog()

    assert catalog["available"] is True
    assert catalog["schema_version"] == 3
    assert catalog["capability"] == SAVE_MAPPING_INTEGRATION_CAPABILITY
    assert catalog["repository"] == {
        "main_commit": _git(production, "rev-parse", "main"),
        "staging_ref": SAVE_MAPPING_STAGING_REF,
        "staged_commit": None,
        "production_clean": True,
        "integration_available": True,
        "code": "",
        "reason": "",
    }
    assert catalog["items"][0]["record_id"] == record["record_id"]
    assert catalog["items"][0]["review_available"] is True
    assert catalog["items"][0]["dismiss_available"] is True
    assert catalog["items"][0]["agent_review_prompt"] == ""
    assert "Review the exact proposal" in catalog["items"][0]["next_action"]


def test_review_is_nonmutating_and_binds_only_mapping_inputs(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    before = {
        path.name: path.read_bytes()
        for path in (production / "config/player_save_versions").glob("*.json")
    }

    review = _review(manager, record)

    assert review["schema_version"] == 3
    assert review["operation"] == "review"
    assert review["stage"] == {"available": True, "code": "", "reason": ""}
    assert len(review["reviewed_proposal_fingerprint"]) == 64
    assert len(review["canonical_mapping_fingerprint"]) == 64
    assert [target["mapping_id"] for target in review["proposal"]["targets"]] == [
        "data-9-game-1073",
        "data-9-game-1101",
    ]
    assert not _git(production, "status", "--porcelain")
    assert {
        path.name: path.read_bytes()
        for path in (production / "config/player_save_versions").glob("*.json")
    } == before
    assert not _git(production, "for-each-ref", "--format=%(objectname)", SAVE_MAPPING_STAGING_REF)


def test_compatible_killed_by_review_and_stage_change_only_runtime_authority(
    integration_repository,
):
    production, store, _record_fixture, manager = integration_repository
    record = _killed_by_record()
    store.append_once(record)

    catalog_item = next(
        item
        for item in manager.catalog()["items"]
        if item["record_id"] == record["record_id"]
    )
    assert catalog_item["review_available"] is False
    assert catalog_item["automatic_integration"] is True
    assert catalog_item["automatic_integration_blocked"] is False
    assert catalog_item["agent_review_prompt"] == ""
    assert catalog_item["machine_verification"]["eligible"] is True
    assert catalog_item["machine_verification"]["proof"] == {
        "check_id": "battle_history_killed_by",
        "value_kind": "battle_history_killed_by_id",
        "raw_value": 9,
        "semantic_value": "Ray",
        "evidence_strength": "deterministic",
        "pairing_method": "exact_locator",
        "pre_mutation": True,
        "game_state": "terminal_game_over",
        "snapshot_fingerprint": "4" * 64,
        "ui_evidence_fingerprint": "5" * 64,
        "source_observation_fingerprint": "3" * 64,
        "runtime_session_fingerprint": "6" * 64,
        "target_generation_fingerprint": "7" * 64,
        "activity_scope_fingerprint": "8" * 64,
        "active_round_identity_fingerprint": "a" * 64,
        "boundary_fingerprint": "b" * 64,
    }

    review = _review(manager, record)
    assert [target["mapping_id"] for target in review["proposal"]["targets"]] == [
        "data-9-game-1073"
    ]

    result = _stage(manager, record, review)
    assert [target["mapping_id"] for target in result["targets"]] == [
        "data-9-game-1073"
    ]
    assert _git(
        production,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        result["staged_commit"],
    ).splitlines() == [
        "config/player_save_versions/data_9_game_1073.json"
    ]


def test_machine_verified_killed_by_integrates_and_publishes_without_review(
    integration_repository,
):
    production, store, _record_fixture, manager = integration_repository
    origin = _configure_origin(production)
    record = _killed_by_record()
    store.append_once(record)
    base = _git(production, "rev-parse", "main")

    plan = manager.automatic_reconciliation_plan()
    result = manager.reconcile_automatic()

    assert plan == {
        "capability": SAVE_MAPPING_AUTOMATIC_PROMOTION_CAPABILITY,
        "needed": True,
        "action": "machine_verify_and_integrate",
        "candidate_record_id": record["record_id"],
    }
    assert result["operation"] == "integrate"
    assert result["disposition"] == "promoted"
    assert result["promoted"] is True
    assert result["published"] is True
    assert result["automatic_retry"] is False
    assert result["agent_required"] is False
    assert _git(production, "rev-parse", "main") == result["staged_commit"]
    assert _git(origin, "rev-parse", "main") == result["remote_main_commit"]
    assert _git(
        production,
        "rev-parse",
        f"refs/tags/{result['rollback_tag']}^{{commit}}",
    ) == base
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        PROMOTION_OWNER_REF,
    )
    authority = json.loads(
        (
            production
            / "config/player_save_versions/data_9_game_1073.json"
        ).read_text(encoding="utf-8")
    )
    assert authority["runtime_save"]["battle_history"]["killed_by_ids"]["9"] == "Ray"
    assert _transaction(manager)["phase"] == "published"
    assert manager.catalog()["items"][0]["state"] == "production_validation_pending"

    transaction = _transaction(manager)
    assert manager.observe_canonical_decode(
        _snapshot(transaction),
        start_evidence=_start_evidence(production, transaction),
    ) is True
    assert not manager.transaction_path.exists()
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        SAVE_MAPPING_STAGING_REF,
    )


def test_nonterminal_killed_by_evidence_still_requires_operator_review(
    integration_repository,
):
    _production, store, _record_fixture, manager = integration_repository
    record = _killed_by_record(game_state="active_battle")
    store.append_once(record)

    item = next(
        candidate
        for candidate in manager.catalog()["items"]
        if candidate["record_id"] == record["record_id"]
    )

    assert item["automatic_integration"] is False
    assert item["review_available"] is True
    assert item["machine_verification"] == {
        "capability": SAVE_MAPPING_MACHINE_VERIFICATION_CAPABILITY,
        "eligible": False,
        "code": "operator_review_required",
        "reason": (
            "This observation is not a complete exact-boundary killed-by "
            "proof, so its meaning still requires operator review."
        ),
        "proof": None,
    }


def test_placeholder_killed_by_fingerprint_cannot_be_machine_verified(
    integration_repository,
):
    _production, store, _record_fixture, manager = integration_repository
    record = _killed_by_record(boundary_fingerprint="0" * 64)
    store.append_once(record)

    item = next(
        candidate
        for candidate in manager.catalog()["items"]
        if candidate["record_id"] == record["record_id"]
    )

    assert item["automatic_integration"] is False
    assert item["review_available"] is True
    assert item["machine_verification"]["code"] == "operator_review_required"


def test_explicitly_reviewed_mapping_is_promoted_instead_of_left_staged(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    origin = _configure_origin(production)
    review = _review(manager, record)

    result = manager.integrate_reviewed(
        candidate_record_id=record["record_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert result["disposition"] == "promoted"
    assert result["published"] is True
    assert _git(production, "rev-parse", "main") == result["staged_commit"]
    assert _git(origin, "rev-parse", "main") == result["staged_commit"]
    assert _transaction(manager)["phase"] == "published"


def test_busy_global_promotion_owner_queues_then_background_consumes(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    _configure_origin(production)
    other = _detached_commit(production, "other promotion")
    _git(
        production,
        "update-ref",
        "--create-reflog",
        "-m",
        "promotion other /tmp/other",
        PROMOTION_OWNER_REF,
        other,
        "0" * 40,
    )
    base = _git(production, "rev-parse", "main")
    review = _review(manager, record)

    queued = manager.integrate_reviewed(
        candidate_record_id=record["record_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert queued["disposition"] == "promotion_queued"
    assert queued["code"] == "promotion_owner_busy"
    assert queued["automatic_retry"] is True
    assert queued["promoted"] is False
    assert _git(production, "rev-parse", "main") == base
    assert _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF) == queued["staged_commit"]

    _git(production, "update-ref", "-d", PROMOTION_OWNER_REF, other)
    completed = manager.reconcile_automatic()

    assert completed["disposition"] == "promoted"
    assert completed["published"] is True


def test_published_aggregate_is_recognized_without_pushing_unrelated_main(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    origin = _configure_origin(production)
    staged = _stage(manager, record, _review(manager, record))
    _promote(production)
    aggregate = _unrelated_commit(production, "aggregate\n")
    _git(production, "push", "origin", "main")

    completed = manager.promote_staged()

    assert completed["disposition"] == "promoted"
    assert completed["remote_main_commit"] == aggregate
    assert aggregate != staged["staged_commit"]
    assert _git(origin, "rev-parse", "main") == aggregate
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        PROMOTION_OWNER_REF,
    )


def test_remote_failure_keeps_durable_queue_and_agent_recovery_request(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)

    queued = manager.integrate_reviewed(
        candidate_record_id=record["record_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert queued["disposition"] == "promotion_queued"
    assert queued["promoted"] is True
    assert queued["published"] is False
    assert queued["code"] == "remote_publication_pending"
    assert queued["agent_required"] is True
    assert "Please finish TheTower automatic save-mapping integration" in queued[
        "agent_review_prompt"
    ]
    assert _transaction(manager)["phase"] == "promoting"
    assert _git(production, "rev-parse", PROMOTION_OWNER_REF) == queued["staged_commit"]

    origin = _configure_origin(production)
    completed = manager.reconcile_automatic()

    assert completed["disposition"] == "promoted"
    assert completed["published"] is True
    assert _git(origin, "rev-parse", "main") == completed["staged_commit"]
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        PROMOTION_OWNER_REF,
    )


def test_proven_remote_divergence_queues_before_local_production_moves(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    origin = _configure_origin(production)
    base = _git(production, "rev-parse", "main")
    remote_advance = _detached_commit(production, "remote outcome")
    _git(
        production,
        "push",
        "origin",
        f"{remote_advance}:refs/heads/main",
    )
    review = _review(manager, record)

    queued = manager.integrate_reviewed(
        candidate_record_id=record["record_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert queued["disposition"] == "promotion_queued"
    assert queued["code"] == "remote_publication_pending"
    assert queued["promoted"] is False
    assert queued["published"] is False
    assert "Local production was not changed" in queued["reason"]
    assert _git(production, "rev-parse", "main") == base
    assert _git(origin, "rev-parse", "main") == remote_advance
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        PROMOTION_OWNER_REF,
    )


def test_dirty_production_after_staging_is_a_durable_automatic_queue(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    _configure_origin(production)
    staged = _stage(manager, record, _review(manager, record))
    dirty = production / "untracked.txt"
    dirty.write_text("operator work\n", encoding="utf-8")

    queued = manager.reconcile_automatic()

    assert queued["disposition"] == "promotion_queued"
    assert queued["code"] == "production_worktree_dirty"
    assert queued["staged_commit"] == staged["staged_commit"]
    assert queued["promoted"] is False
    assert queued["published"] is False
    assert queued["automatic_retry"] is True
    assert "agent" in queued["agent_review_prompt"].lower()

    dirty.unlink()
    completed = manager.reconcile_automatic()

    assert completed["disposition"] == "promoted"
    assert completed["published"] is True


def test_published_owner_release_failure_is_consumed_as_cleanup_queue(
    integration_repository,
):
    production, store, record, manager = integration_repository
    _configure_origin(production)
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "remote_published":
            raise RuntimeError("lost before owner release")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        mapping_set_lock_path=manager.mapping_set_lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(RuntimeError, match="lost before owner release"):
        interrupted.integrate_reviewed(
            candidate_record_id=record["record_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )
    transaction = _transaction(manager)
    owner_path = Path(
        _git(production, "rev-parse", "--git-path", PROMOTION_OWNER_REF)
    )
    if not owner_path.is_absolute():
        owner_path = production / owner_path
    owner_lock = Path(f"{owner_path}.lock")
    owner_lock.parent.mkdir(parents=True, exist_ok=True)
    owner_lock.write_text("held\n", encoding="utf-8")
    try:
        queued = manager.reconcile_automatic()

        assert queued["disposition"] == "promotion_queued"
        assert queued["code"] == "promotion_owner_release_failed"
        assert queued["promoted"] is True
        assert queued["published"] is True
        assert queued["automatic_retry"] is True
        item = manager.catalog()["items"][0]
        assert item["state"] == "promotion_cleanup_pending"
        assert item["review_available"] is False
        assert item["agent_review_prompt"].startswith(
            "Please recover TheTower automatic save-mapping integration"
        )
        assert "do not repeat review" in item["agent_review_prompt"]
        assert manager.automatic_reconciliation_plan()["action"] == (
            "release_promotion_owner"
        )
    finally:
        owner_lock.unlink(missing_ok=True)

    completed = manager.reconcile_automatic()

    assert completed["disposition"] == "promoted"
    assert completed["published"] is True
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        PROMOTION_OWNER_REF,
    )
    assert _transaction(manager)["phase"] == "published"


def test_crash_after_local_fast_forward_recovers_same_commit_and_owner(
    integration_repository,
):
    production, store, record, manager = integration_repository
    origin = _configure_origin(production)
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "main_promoted":
            raise RuntimeError("lost after local fast-forward")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        mapping_set_lock_path=manager.mapping_set_lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(RuntimeError, match="lost after local fast-forward"):
        interrupted.integrate_reviewed(
            candidate_record_id=record["record_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )
    transaction = _transaction(manager)
    expected = transaction["staging"]["expected_commit"]
    assert transaction["phase"] == "promoting"
    assert _git(production, "rev-parse", "main") == expected
    assert _git(production, "rev-parse", PROMOTION_OWNER_REF) == expected
    assert _git(origin, "rev-parse", "main") != expected

    recovered = manager.reconcile_automatic()

    assert recovered["staged_commit"] == expected
    assert recovered["disposition"] == "promoted"
    assert recovered["published"] is True
    assert _git(origin, "rev-parse", "main") == expected
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        PROMOTION_OWNER_REF,
    )


def test_decode_closes_published_crash_and_releases_exact_owned_ref(
    integration_repository,
):
    production, store, record, manager = integration_repository
    _configure_origin(production)
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "remote_published":
            raise RuntimeError("lost before owner release")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        mapping_set_lock_path=manager.mapping_set_lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(RuntimeError, match="lost before owner release"):
        interrupted.integrate_reviewed(
            candidate_record_id=record["record_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )
    transaction = _transaction(manager)
    expected = transaction["staging"]["expected_commit"]
    assert transaction["phase"] == "published"
    assert _git(production, "rev-parse", PROMOTION_OWNER_REF) == expected

    assert manager.observe_canonical_decode(
        _snapshot(transaction),
        start_evidence=_start_evidence(production, transaction),
    ) is True

    assert not manager.transaction_path.exists()
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        PROMOTION_OWNER_REF,
    )
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        SAVE_MAPPING_STAGING_REF,
    )


def test_dismiss_preserves_receipt_hides_observation_and_is_idempotent(
    integration_repository,
):
    _production, store, record, manager = integration_repository

    first = manager.dismiss(candidate_record_id=record["record_id"])
    second = manager.dismiss(candidate_record_id=record["record_id"])

    assert first["capability"] == SAVE_MAPPING_DISPOSITION_CAPABILITY
    assert first["operation"] == "dismiss"
    assert first["changed"] is True
    assert first["evidence_preserved"] is True
    assert second == {**first, "changed": False}
    assert store.get(record["record_id"]) == record
    assert manager.catalog()["items"] == []
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "mapping_candidate_dismissed"


def test_stage_creates_one_private_candidate_and_leaves_main_index_and_tree_untouched(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")
    tree = {
        path.name: path.read_bytes()
        for path in (production / "config/player_save_versions").glob("*.json")
    }
    before_mappings = {
        name: json.loads(contents)
        for name, contents in tree.items()
    }

    result = _stage(manager, record, review)

    commit = result["staged_commit"]
    assert result["operation"] == "stage"
    assert result["disposition"] == "staged_for_promotion"
    assert result["staging_ref"] == SAVE_MAPPING_STAGING_REF
    assert result["committed"] is True
    assert result["staged"] is True
    assert result["promoted"] is False
    assert result["mapping_invariants"] == "passed"
    assert result["promotion_validation"] == "pending"
    assert result["idempotent"] is False
    assert _git(production, "rev-parse", "main") == base
    assert _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF) == commit
    assert _git(production, "rev-parse", f"{commit}^") == base
    assert _git(production, "show", "-s", "--format=%s", commit).startswith(
        "Stage save mapping candidate"
    )
    assert not _git(production, "status", "--porcelain")
    assert _git(
        production,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        commit,
    ).splitlines() == [
        "config/player_save_versions/data_9_game_1073.json",
        "config/player_save_versions/data_9_game_1101.json",
    ]
    assert {
        path.name: path.read_bytes()
        for path in (production / "config/player_save_versions").glob("*.json")
    } == tree
    for name, before in before_mappings.items():
        staged = json.loads(
            _git(
                production,
                "show",
                f"{commit}:config/player_save_versions/{name}",
            )
        )
        assert staged["module_loadout"] == before["module_loadout"]
        assert staged["module_info_indices"][str(FIXTURE_INFO_INDEX)] == {
            "name": FIXTURE_MODULE_NAME,
            "family": "cannon",
        }


def test_unrelated_main_advance_does_not_invalidate_review(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)
    reviewed_base = review["reviewed_base_commit"]
    current_main = _unrelated_commit(production)

    result = _stage(manager, record, review)

    assert current_main != reviewed_base
    assert result["base_commit"] == current_main
    assert _git(production, "rev-parse", f"{result['staged_commit']}^") == current_main
    assert _git(production, "rev-parse", "main") == current_main
    assert _git(
        production,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        result["staged_commit"],
    ).splitlines() == [
        "config/player_save_versions/data_9_game_1073.json",
        "config/player_save_versions/data_9_game_1101.json",
    ]


def test_mapping_target_change_invalidates_review(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)
    path = production / "config/player_save_versions/data_9_game_1073.json"
    path.write_bytes(path.read_bytes() + b"\n")
    _git(production, "add", path.relative_to(production).as_posix())
    _git(production, "commit", "-m", "change reviewed mapping input")

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _stage(manager, record, review)

    assert failure.value.code == "reviewed_proposal_stale"
    assert not _git(production, "for-each-ref", "--format=%(objectname)", SAVE_MAPPING_STAGING_REF)


def test_group_writable_files_stage_as_regular_git_files(
    integration_repository,
):
    production, _store, record, manager = integration_repository

    result = _stage(manager, record, _review(manager, record))

    assert {target["mode"] for target in result["targets"]} == {0o664}
    for target in result["targets"]:
        assert _git(
            production,
            "ls-tree",
            result["staged_commit"],
            "--",
            target["path"],
        ).startswith("100644 blob ")


def test_executable_mapping_target_is_rejected_before_staging(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    _git(production, "config", "core.fileMode", "false")
    (production / "config/player_save_versions/data_9_game_1073.json").chmod(0o775)

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)

    assert failure.value.code == "proposal_target_invalid"
    assert not manager.transaction_path.exists()


def test_missing_git_identity_disables_staging(integration_repository):
    production, _store, record, manager = integration_repository
    _git(production, "config", "user.name", "")
    _git(production, "config", "user.email", "")

    catalog = manager.catalog()

    assert catalog["repository"]["integration_available"] is False
    assert catalog["repository"]["code"] == "git_identity_unavailable"
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "git_identity_unavailable"


def test_contradictory_semantics_leave_the_routine_lane(integration_repository):
    _production, store, record, manager = integration_repository
    store.append_once(
        _record(
            module_name="Contradictory Fixture Cannon Assist",
            source_fingerprint="2" * 64,
            recorded_at="2026-08-10T12:01:01+00:00",
        )
    )

    catalog = manager.catalog()

    assert len(catalog["items"]) == 2
    assert all(item["review_available"] is False for item in catalog["items"])
    assert all(item["dismiss_available"] is True for item in catalog["items"])
    assert all(
        item["agent_review_prompt"].startswith(
            "Please review TheTower save-mapping observation"
        )
        for item in catalog["items"]
    )
    assert all(
        "Copy the agent-review request" in item["next_action"]
        for item in catalog["items"]
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "mapping_candidate_requires_ordinary_development"


def test_contradictory_module_family_across_scopes_leaves_routine_lane(
    integration_repository,
):
    _production, store, record, manager = integration_repository
    store.append_once(
        _record(
            slot_key="core_primary",
            source_fingerprint="2" * 64,
            recorded_at="2026-08-10T12:01:01+00:00",
        )
    )

    catalog = manager.catalog()

    assert len(catalog["items"]) == 2
    assert all(item["review_available"] is False for item in catalog["items"])
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "mapping_candidate_requires_ordinary_development"


def test_active_staging_transaction_blocks_dismissal(integration_repository):
    _production, _store, record, manager = integration_repository
    _stage(manager, record, _review(manager, record))

    with pytest.raises(SaveMappingIntegrationError) as failure:
        manager.dismiss(candidate_record_id=record["record_id"])

    assert failure.value.code == "transaction_recovery_required"


def test_retry_is_idempotent_and_does_not_create_another_commit(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)
    first = _stage(manager, record, review)
    count = _git(production, "rev-list", "--count", SAVE_MAPPING_STAGING_REF)

    repeated = _stage(manager, record, review)

    assert repeated["staged_commit"] == first["staged_commit"]
    assert repeated["idempotent"] is True
    assert _git(production, "rev-list", "--count", SAVE_MAPPING_STAGING_REF) == count


def test_exact_retry_after_promotion_reports_promoted(integration_repository):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)
    first = _stage(manager, record, review)
    _promote(production)

    repeated = _stage(manager, record, review)

    assert repeated["staged_commit"] == first["staged_commit"]
    assert repeated["idempotent"] is True
    assert repeated["promoted"] is True


def test_catalog_tracks_promotion_and_decode_then_retires_private_ref(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    _configure_origin(production)
    result = _stage(manager, record, _review(manager, record))
    pending = manager.catalog()
    assert pending["items"][0]["state"] == "promotion_pending"
    assert pending["items"][0]["staged_commit"] == result["staged_commit"]

    promoted = manager.promote_staged()
    assert promoted["disposition"] == "promoted"
    assert promoted["published"] is True
    deployed = manager.catalog()
    assert deployed["items"][0]["state"] == "production_validation_pending"
    transaction = _transaction(manager)

    assert manager.observe_canonical_decode(
        _snapshot(transaction),
        start_evidence=_start_evidence(production, transaction),
    ) is True
    assert not manager.transaction_path.exists()
    assert not _git(production, "for-each-ref", "--format=%(objectname)", SAVE_MAPPING_STAGING_REF)
    assert manager.catalog()["items"] == []


def test_main_advance_after_staging_is_reported_as_restaging_required(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)
    first = _stage(manager, record, review)
    current_main = _unrelated_commit(production)

    item = manager.status()["items"][0]

    assert item["state"] == "restaging_required"
    assert "main advanced" in item["reason"]
    recovery = _review(manager, record)
    assert recovery["recovery_required"] is True
    assert "restage" in recovery["stage"]["reason"]

    restaged = _stage(manager, record, recovery)

    assert restaged["staged_commit"] != first["staged_commit"]
    assert restaged["base_commit"] == current_main
    assert _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF) == restaged["staged_commit"]
    assert _git(production, "rev-parse", f"{restaged['staged_commit']}^") == current_main


def test_dirty_production_disables_review_without_writing(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    (production / "untracked.txt").write_text("operator work\n", encoding="utf-8")

    catalog = manager.catalog()

    assert catalog["repository"]["integration_available"] is False
    assert catalog["repository"]["code"] == "production_worktree_dirty"
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "production_worktree_dirty"
    assert not manager.transaction_path.exists()


def test_machine_verified_candidate_exposes_agent_route_when_integration_blocked(
    integration_repository,
):
    production, store, _record_fixture, manager = integration_repository
    record = _killed_by_record()
    store.append_once(record)
    (production / "untracked.txt").write_text("operator work\n", encoding="utf-8")

    item = next(
        candidate
        for candidate in manager.catalog()["items"]
        if candidate["record_id"] == record["record_id"]
    )

    assert item["automatic_integration"] is True
    assert item["automatic_integration_blocked"] is True
    assert item["automatic_integration_blocker_code"] == "production_worktree_dirty"
    assert "Production has tracked" in item[
        "automatic_integration_blocker_reason"
    ]
    assert "No semantic review is needed" in item["next_action"]
    assert item["agent_review_prompt"].startswith(
        "Please recover TheTower automatic save-mapping integration"
    )
    assert "do not repeat semantic review" in item["agent_review_prompt"]
    assert not manager.transaction_path.exists()


def test_restaging_recovers_after_exact_old_ref_was_retired(
    integration_repository,
):
    production, store, record, manager = integration_repository
    review = _review(manager, record)
    _stage(manager, record, review)
    current_main = _unrelated_commit(production)
    recovery = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "stale_candidate_ref_retired":
            raise RuntimeError("lost after exact ref retirement")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(RuntimeError, match="lost after exact ref retirement"):
        _stage(interrupted, record, recovery)

    assert manager.transaction_path.exists()
    assert not _git(
        production,
        "for-each-ref",
        "--format=%(objectname)",
        SAVE_MAPPING_STAGING_REF,
    )
    assert manager.status()["items"][0]["state"] == "restaging_required"

    restaged = _stage(manager, record, _review(manager, record))

    assert restaged["base_commit"] == current_main
    assert _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF) == restaged["staged_commit"]


def test_existing_private_ref_blocks_another_candidate(integration_repository):
    production, _store, record, manager = integration_repository
    occupied = _detached_commit(production, "occupied staging ref")
    _git(production, "update-ref", SAVE_MAPPING_STAGING_REF, occupied)

    catalog = manager.catalog()

    assert catalog["repository"]["staged_commit"] == occupied
    assert catalog["repository"]["code"] == "staging_ref_occupied"
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "staging_ref_occupied"


def test_busy_lock_reports_retryable_failure_without_mutation(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    review = _review(manager, record)
    descriptor = os.open(manager.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SaveMappingIntegrationError) as failure:
            _stage(manager, record, review)
    finally:
        os.close(descriptor)

    assert failure.value.code == "integration_busy"
    assert not manager.transaction_path.exists()
    assert not _git(production, "for-each-ref", "--format=%(objectname)", SAVE_MAPPING_STAGING_REF)


def test_crash_after_journal_recovers_only_by_explicit_retry(
    integration_repository,
):
    production, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "journal_written":
            raise RuntimeError("lost process")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(RuntimeError, match="lost process"):
        _stage(interrupted, record, review)

    assert manager.transaction_path.exists()
    assert not _git(production, "for-each-ref", "--format=%(objectname)", SAVE_MAPPING_STAGING_REF)
    assert manager.catalog()["items"][0]["state"] == "integration_recovery_required"

    recovered = _stage(manager, record, review)

    assert recovered["idempotent"] is True
    assert _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF) == recovered["staged_commit"]


def test_crash_after_ref_update_recovers_exact_staged_commit(
    integration_repository,
):
    production, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "staging_ref_updated":
            raise RuntimeError("lost response")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(RuntimeError, match="lost response"):
        _stage(interrupted, record, review)
    expected = _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF)
    assert _transaction(manager)["phase"] == "commit_ready"

    recovered = _stage(manager, record, review)

    assert recovered["idempotent"] is True
    assert recovered["staged_commit"] == expected
    assert _transaction(manager)["phase"] == "staged"


def test_concurrent_main_advance_at_ref_boundary_is_preserved(
    integration_repository,
):
    production, store, record, manager = integration_repository
    review = _review(manager, record)

    def advance_main(transition: str) -> None:
        if transition == "before_staging_ref_update":
            _unrelated_commit(production, "concurrent\n")

    racing = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=advance_main,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _stage(racing, record, review)

    assert failure.value.code == "commit_state_uncertain"
    assert (production / "operator-owned.txt").read_text(encoding="utf-8") == "concurrent\n"
    assert not _git(production, "for-each-ref", "--format=%(objectname)", SAVE_MAPPING_STAGING_REF)


def test_concurrent_staging_ref_creation_is_preserved(
    integration_repository,
):
    production, store, record, manager = integration_repository
    review = _review(manager, record)
    other = _detached_commit(production, "concurrent staged candidate")

    def occupy_ref(transition: str) -> None:
        if transition == "before_staging_ref_update":
            _git(production, "update-ref", SAVE_MAPPING_STAGING_REF, other)

    racing = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=occupy_ref,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _stage(racing, record, review)

    assert failure.value.code == "commit_state_uncertain"
    assert _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF) == other


def test_promoted_target_drift_is_unconfirmed(integration_repository):
    production, _store, record, manager = integration_repository
    _stage(manager, record, _review(manager, record))
    _promote(production)
    path = production / "config/player_save_versions/data_9_game_1073.json"
    path.write_bytes(path.read_bytes() + b"\n")
    _git(production, "add", path.relative_to(production).as_posix())
    _git(production, "commit", "-m", "supersede staged target")

    item = manager.status()["items"][0]

    assert item["state"] == "integration_unconfirmed"
    assert "superseded" in item["reason"]


def test_legacy_develop_transaction_is_not_interpreted(
    integration_repository,
):
    _production, _store, record, manager = integration_repository
    manager.transaction_path.write_text(
        json.dumps({"kind": "save_mapping_develop_integration_transaction"}),
        encoding="utf-8",
    )

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)

    assert failure.value.code == "legacy_transaction_recovery_required"


def test_decode_requires_matching_fingerprint_and_post_stage_start(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    _stage(manager, record, _review(manager, record))
    _promote(production)
    transaction = _transaction(manager)
    good_start = _start_evidence(production, transaction)

    assert manager.observe_canonical_decode(
        _snapshot(transaction, fingerprint="0" * 64),
        start_evidence=good_start,
    ) is False
    early = {
        **good_start,
        "acquired_at": (
            datetime.fromisoformat(transaction["integration_available_since"])
            - timedelta(seconds=1)
        ).isoformat(),
    }
    assert manager.observe_canonical_decode(
        _snapshot(transaction),
        start_evidence=early,
    ) is False
    assert manager.transaction_path.exists()
    assert _git(production, "rev-parse", SAVE_MAPPING_STAGING_REF)


def test_decode_receipt_store_is_idempotent_and_repairs_partial_tail(tmp_path):
    path = tmp_path / "decode-receipts.jsonl"
    store = CanonicalDecodeReceiptStore(path)
    unsigned = {
        "candidate_record_id": "1" * 64,
        "integration_commit": "2" * 40,
        "canonical_mapping_fingerprint": "3" * 64,
        "snapshot_mapping_fingerprint": "3" * 64,
        "snapshot_fingerprint": "4" * 64,
        "acquisition_started_at": "2026-08-10T12:00:00+00:00",
        "acquisition_main_commit": "5" * 40,
        "captured_at": "2026-08-10T12:00:01+00:00",
    }
    from core.player_save_mapping_candidates import fingerprint_json

    record = {
        "schema_version": 2,
        "receipt_id": fingerprint_json(unsigned),
        **unsigned,
    }

    assert store.append_once(record) is True
    assert store.append_once(record) is False
    with path.open("ab") as handle:
        handle.write(b'{"partial"')
    assert store.list_records() == [record]
    assert path.read_bytes().endswith(b"\n")


def test_complete_corrupt_decode_receipt_keeps_status_unavailable(
    integration_repository,
):
    production, _store, record, manager = integration_repository
    _configure_origin(production)
    _stage(manager, record, _review(manager, record))
    manager.promote_staged()
    manager.decode_receipts.path.write_text("{}\n", encoding="utf-8")

    status = manager.status()

    assert status["available"] is False
    assert "invalid shape" in status["reason"]
