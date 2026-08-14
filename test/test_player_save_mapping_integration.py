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
    SAVE_MAPPING_INTEGRATION_CAPABILITY,
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
) -> dict:
    slots = {
        "cannon_primary": "Amplifying Strike",
        "armor_primary": "Orbital Augment",
        "generator_primary": "Project Funding",
        "core_primary": "Dimension Core",
        "cannon_assist": module_name,
        "armor_assist": "Space Displacer",
        "generator_assist": "Singularity Harness",
        "core_assist": "Harmony Conductor",
    }
    pending = pending_mapping_candidate(
        value_kind="module_info_index",
        raw_value=FIXTURE_INFO_INDEX,
        pairing_method="exact_locator",
        locator="cannon_assist",
        expected_observation_count=8,
        peer_locator_values={
            locator: value
            for locator, value in slots.items()
            if locator != "cannon_assist"
        },
        scope={"slot_key": "cannon_assist", "family": "cannon", "role": "assist"},
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
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "mapping_candidate_requires_ordinary_development"


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
    result = _stage(manager, record, _review(manager, record))
    pending = manager.catalog()
    assert pending["items"][0]["state"] == "promotion_pending"
    assert pending["items"][0]["staged_commit"] == result["staged_commit"]

    _promote(production)
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
    _stage(manager, record, _review(manager, record))
    _promote(production)
    manager.decode_receipts.path.write_text("{}\n", encoding="utf-8")

    status = manager.status()

    assert status["available"] is False
    assert "invalid shape" in status["reason"]
