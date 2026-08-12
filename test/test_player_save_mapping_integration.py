from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import core.player_save_mapping_integration as integration_module
from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    build_mapping_candidate_record,
    pending_mapping_candidate,
    resolve_mapping_candidates,
)
from core.player_save_mapping_integration import (
    SaveMappingIntegrationError,
    SaveMappingIntegrationManager,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = "2026-08-10T12:00:00+00:00"
FIXTURE_INFO_INDEX = 9_000_000_001
FIXTURE_MODULE_NAME = "Integration Fixture Cannon Assist"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _record() -> dict:
    slots = {
        "cannon_primary": "Amplifying Strike",
        "armor_primary": "Orbital Augment",
        "generator_primary": "Project Funding",
        "core_primary": "Dimension Core",
        "cannon_assist": FIXTURE_MODULE_NAME,
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
    evidence = {
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
        "source_observation_fingerprint": "1" * 64,
    }
    resolved = resolve_mapping_candidates("modules", [pending], evidence)[0]
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
        source_observation_fingerprint="1" * 64,
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
        recorded_at="2026-08-10T12:00:01+00:00",
    )


@pytest.fixture
def integration_repository(tmp_path: Path):
    production = tmp_path / "TheTower"
    worktrees = tmp_path / "TheTower-worktrees"
    develop = worktrees / "dev"
    feature = worktrees / "workers" / "mapping-test"
    mapping_dir = production / "config" / "player_save_versions"
    mapping_dir.mkdir(parents=True)
    for name in ("data_9_game_1073.json", "data_9_game_1101.json"):
        shutil.copyfile(
            ROOT / "config" / "player_save_versions" / name,
            mapping_dir / name,
        )
    _git(production, "init", "-b", "main")
    _git(production, "config", "user.name", "TheTower Test")
    _git(production, "config", "user.email", "thetower@example.invalid")
    _git(production, "add", "config")
    _git(production, "commit", "-m", "mapping base")
    _git(production, "branch", "develop")
    develop.parent.mkdir(parents=True)
    _git(production, "worktree", "add", str(develop), "develop")
    feature.parent.mkdir(parents=True)
    _git(
        production,
        "worktree",
        "add",
        "-b",
        "feature/mapping-test",
        str(feature),
        "develop",
    )
    store = AppendOnlyMappingCandidateStore(tmp_path / "receipts.jsonl")
    record = _record()
    store.append_once(record)
    manager = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        development_root=worktrees,
        lock_path=tmp_path / "integration.lock",
    )
    return production, develop, feature, store, record, manager


def test_catalog_discovers_only_server_issued_feature_workspace(
    integration_repository,
):
    production, develop, feature, _store, record, manager = integration_repository

    catalog = manager.catalog()

    assert catalog["available"] is True
    assert catalog["repository"]["main_commit"] == _git(production, "rev-parse", "main")
    assert catalog["repository"]["develop_commit"] == _git(
        production, "rev-parse", "develop"
    )
    assert [item["branch"] for item in catalog["workspaces"]] == [
        "feature/mapping-test"
    ]
    workspace = catalog["workspaces"][0]
    assert workspace["path_display"] == str(feature)
    assert workspace["available"] is True
    assert workspace["clean"] is True
    assert catalog["items"][0]["record_id"] == record["record_id"]
    assert catalog["items"][0]["review_available"] is True
    assert not _git(production, "status", "--porcelain")
    assert not _git(develop, "status", "--porcelain")


def test_catalog_isolates_a_missing_prunable_feature_worktree(
    integration_repository,
):
    production, _develop, feature, _store, _record, manager = (
        integration_repository
    )
    stale = feature.parent / "retired-mapping-test"
    _git(
        production,
        "worktree",
        "add",
        "-b",
        "feature/retired-mapping-test",
        str(stale),
        "develop",
    )
    shutil.rmtree(stale)

    catalog = manager.catalog()

    assert catalog["available"] is True
    by_branch = {item["branch"]: item for item in catalog["workspaces"]}
    assert by_branch["feature/mapping-test"]["available"] is True
    assert by_branch["feature/retired-mapping-test"]["available"] is False
    assert by_branch["feature/retired-mapping-test"]["code"] == (
        "workspace_link_unavailable"
    )


def test_review_is_non_mutating_and_binds_dual_target_result_hashes(
    integration_repository,
):
    production, develop, feature, _store, record, manager = integration_repository
    catalog = manager.catalog()
    workspace = catalog["workspaces"][0]
    before = {
        root: {
            name: (root / "config" / "player_save_versions" / name).read_bytes()
            for name in ("data_9_game_1073.json", "data_9_game_1101.json")
        }
        for root in (production, develop, feature)
    }

    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )

    assert review["prepare"] == {"available": True, "code": "", "reason": ""}
    assert len(review["reviewed_proposal_fingerprint"]) == 64
    assert review["proposal"]["atomic_group"] is True
    assert [target["mapping_id"] for target in review["proposal"]["targets"]] == [
        "data-9-game-1073",
        "data-9-game-1101",
    ]
    for root, files in before.items():
        for name, content in files.items():
            assert (root / "config" / "player_save_versions" / name).read_bytes() == content
    assert not _git(feature, "status", "--porcelain")


def test_prepare_changes_only_feature_targets_and_is_idempotent(
    integration_repository,
):
    production, develop, feature, _store, record, manager = integration_repository
    catalog = manager.catalog()
    workspace = catalog["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    production_before = {
        path.name: path.read_bytes()
        for path in (production / "config" / "player_save_versions").glob("*.json")
    }
    develop_before = {
        path.name: path.read_bytes()
        for path in (develop / "config" / "player_save_versions").glob("*.json")
    }

    result = manager.prepare(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert result["disposition"] == "prepared"
    assert result["idempotent"] is False
    assert result["committed"] is False
    assert result["promoted"] is False
    assert result["validation_status"] == "pending"
    assert all(target["changed"] for target in result["targets"])
    assert _git(feature, "diff", "--name-only").splitlines() == [
        "config/player_save_versions/data_9_game_1073.json",
        "config/player_save_versions/data_9_game_1101.json",
    ]
    assert not _git(feature, "diff", "--cached", "--name-only")
    for name, content in production_before.items():
        assert (production / "config" / "player_save_versions" / name).read_bytes() == content
    for name, content in develop_before.items():
        assert (develop / "config" / "player_save_versions" / name).read_bytes() == content
    for name in ("data_9_game_1073.json", "data_9_game_1101.json"):
        mapping = json.loads(
            (feature / "config" / "player_save_versions" / name).read_text(
                encoding="utf-8"
            )
        )
        assert mapping["module_loadout"]["assist"][0]["values"][-1] == {
            "info_index": FIXTURE_INFO_INDEX,
            "name": FIXTURE_MODULE_NAME,
        }

    repeated = manager.prepare(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )
    assert repeated["disposition"] == "prepared"
    assert repeated["idempotent"] is True

    reopened = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    assert reopened["prepared"] is True
    assert reopened["prepare"]["code"] == "already_prepared"
    assert reopened["prepared_result"] == repeated
    assert reopened["recovery_required"] is False


def test_prepare_rejects_moved_develop_tip_with_stale_review(
    integration_repository,
):
    production, develop, _feature, _store, record, manager = integration_repository
    catalog = manager.catalog()
    workspace = catalog["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    (develop / "unrelated.txt").write_text("new develop work\n", encoding="utf-8")
    _git(develop, "add", "unrelated.txt")
    _git(develop, "commit", "-m", "advance develop")

    with pytest.raises(
        SaveMappingIntegrationError,
        match="repository snapshot changed",
    ) as failure:
        manager.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
        )

    assert failure.value.code == "reviewed_proposal_stale"


def test_prepare_rejects_unrelated_dirty_workspace_without_writing_targets(
    integration_repository,
):
    _production, _develop, feature, _store, record, manager = integration_repository
    catalog = manager.catalog()
    workspace = catalog["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    targets = {
        path: path.read_bytes()
        for path in (feature / "config" / "player_save_versions").glob("*.json")
    }
    (feature / "operator-note.txt").write_text("owned work\n", encoding="utf-8")

    with pytest.raises(SaveMappingIntegrationError) as failure:
        manager.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
        )

    assert failure.value.code == "workspace_dirty"
    assert all(path.read_bytes() == content for path, content in targets.items())


def test_prepare_reports_busy_lock_without_writing(integration_repository):
    _production, _develop, feature, _store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    manager.lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(manager.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SaveMappingIntegrationError) as failure:
            manager.prepare(
                candidate_record_id=record["record_id"],
                workspace_id=workspace["workspace_id"],
                reviewed_proposal_fingerprint=review[
                    "reviewed_proposal_fingerprint"
                ],
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert failure.value.code == "integration_busy"
    assert not _git(feature, "status", "--porcelain")


def test_group_write_failure_rolls_back_every_feature_target(
    integration_repository,
    monkeypatch,
):
    _production, _develop, feature, _store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    targets = {
        path: path.read_bytes()
        for path in (feature / "config" / "player_save_versions").glob("*.json")
    }
    replace = integration_module.os.replace
    calls = 0

    def fail_second_target_replace(source, destination):
        nonlocal calls
        calls += 1
        # Journal publication is the first rename; fail the second target.
        if calls == 3:
            raise OSError("simulated second-target replacement failure")
        return replace(source, destination)

    monkeypatch.setattr(
        integration_module.os,
        "replace",
        fail_second_target_replace,
    )

    with pytest.raises(SaveMappingIntegrationError) as failure:
        manager.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )

    assert failure.value.code == "mapping_prepare_write_failed"
    assert calls >= 4
    assert all(path.read_bytes() == content for path, content in targets.items())
    assert not _git(feature, "status", "--porcelain")


def test_interrupted_first_replace_is_durably_recovered_on_explicit_prepare(
    integration_repository,
):
    production, _develop, feature, store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )

    def interrupt_after_first_target(transition: str) -> None:
        if transition == "target_replaced:0":
            raise SystemExit("simulated process termination")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        development_root=manager.development_root,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        transaction_fault_hook=interrupt_after_first_target,
    )
    with pytest.raises(SystemExit, match="simulated process termination"):
        interrupted.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )

    assert manager.transaction_path.exists()
    interrupted_review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    assert interrupted_review["recovery_required"] is True
    assert interrupted_review["prepare"]["available"] is True
    assert interrupted_review["prepare"]["code"] == (
        "transaction_recovery_required"
    )
    assert interrupted_review["prepared_result"] is None

    result = manager.prepare(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert result["disposition"] == "prepared"
    assert result["reviewed_proposal_fingerprint"] == review[
        "reviewed_proposal_fingerprint"
    ]
    assert not manager.transaction_path.exists()
    assert not list(feature.rglob("*.mapping-stage-*"))
    assert len(_git(feature, "diff", "--name-only").splitlines()) == 2


def test_target_content_drift_after_staging_is_preserved_without_replacement(
    integration_repository,
):
    production, _develop, feature, store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    target = feature / "config/player_save_versions/data_9_game_1073.json"
    other = feature / "config/player_save_versions/data_9_game_1101.json"
    operator_content = target.read_bytes() + b"\n"
    other_before = other.read_bytes()

    def edit_after_staging(transition: str) -> None:
        if transition == "before_target_replace:0":
            target.write_bytes(operator_content)

    drifting = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        development_root=manager.development_root,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        transaction_fault_hook=edit_after_staging,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        drifting.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )

    assert failure.value.code == "proposal_base_changed"
    assert target.read_bytes() == operator_content
    assert other.read_bytes() == other_before
    assert not manager.transaction_path.exists()
    assert not list(feature.rglob("*.mapping-stage-*"))


def test_target_mode_drift_after_staging_is_preserved_without_replacement(
    integration_repository,
):
    production, _develop, feature, store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    target = feature / "config/player_save_versions/data_9_game_1073.json"
    original_mode = target.stat().st_mode & 0o7777
    changed_mode = original_mode ^ 0o100

    def chmod_after_staging(transition: str) -> None:
        if transition == "targets_staged":
            target.chmod(changed_mode)

    drifting = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        development_root=manager.development_root,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        transaction_fault_hook=chmod_after_staging,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        drifting.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )

    assert failure.value.code == "proposal_base_changed"
    assert target.stat().st_mode & 0o7777 == changed_mode
    assert not manager.transaction_path.exists()
    assert not list(feature.rglob("*.mapping-stage-*"))


def test_untracked_mode_drift_after_review_rejects_original_fingerprint(
    integration_repository,
):
    _production, _develop, feature, _store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    target = feature / "config/player_save_versions/data_9_game_1073.json"
    changed_mode = (target.stat().st_mode & 0o7777) ^ 0o020
    target.chmod(changed_mode)
    assert not _git(feature, "status", "--porcelain")

    with pytest.raises(SaveMappingIntegrationError) as failure:
        manager.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )

    assert failure.value.code == "proposal_workspace_mismatch"
    assert target.stat().st_mode & 0o7777 == changed_mode
    assert not manager.transaction_path.exists()


def test_clean_lexical_symlink_target_is_rejected_before_journal_or_mutation(
    integration_repository,
):
    _production, _develop, feature, _store, record, manager = integration_repository
    target = feature / "config/player_save_versions/data_9_game_1073.json"
    alternate = target.with_name("data_9_game_1073_link_target.json")
    alternate.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(alternate.name)
    _git(feature, "add", "config/player_save_versions")
    _git(feature, "commit", "-m", "install lexical mapping symlink")
    alternate_before = alternate.read_bytes()
    workspace = manager.catalog()["workspaces"][0]

    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )

    assert review["prepare"]["available"] is False
    assert review["prepare"]["code"] == "proposal_target_invalid"
    with pytest.raises(SaveMappingIntegrationError) as failure:
        manager.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )
    assert failure.value.code == "proposal_target_invalid"
    assert target.is_symlink()
    assert alternate.read_bytes() == alternate_before
    assert not manager.transaction_path.exists()


def test_failed_target_directory_fsync_retains_journal_until_recovery_barrier(
    integration_repository,
    monkeypatch,
):
    production, _develop, feature, store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    target_directory = feature / "config/player_save_versions"
    original_fsync_directory = integration_module._fsync_directory
    fail_target_barrier = False

    def enable_failure_before_last_target(transition: str) -> None:
        nonlocal fail_target_barrier
        if transition == "before_target_replace:1":
            fail_target_barrier = True

    def fail_target_directory_barrier(path: Path) -> None:
        if fail_target_barrier and path == target_directory:
            raise OSError("simulated target-directory fsync failure")
        original_fsync_directory(path)

    monkeypatch.setattr(
        integration_module,
        "_fsync_directory",
        fail_target_directory_barrier,
    )
    failing = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        development_root=manager.development_root,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        transaction_fault_hook=enable_failure_before_last_target,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        failing.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )

    assert failure.value.code == "mapping_prepare_rollback_failed"
    assert manager.transaction_path.exists()
    fail_target_barrier = False

    recovered = manager.prepare(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert recovered["disposition"] == "prepared"
    assert recovered["idempotent"] is True
    assert not manager.transaction_path.exists()


def test_develop_ref_movement_after_replacements_never_reports_success(
    integration_repository,
):
    production, develop, feature, store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )

    def advance_develop_after_replacements(transition: str) -> None:
        if transition != "targets_replaced":
            return
        (develop / "concurrent.txt").write_text(
            "develop moved during preparation\n",
            encoding="utf-8",
        )
        _git(develop, "add", "concurrent.txt")
        _git(develop, "commit", "-m", "concurrent develop movement")

    drifting = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        development_root=manager.development_root,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        transaction_fault_hook=advance_develop_after_replacements,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        drifting.prepare(
            candidate_record_id=record["record_id"],
            workspace_id=workspace["workspace_id"],
            reviewed_proposal_fingerprint=review[
                "reviewed_proposal_fingerprint"
            ],
        )

    assert failure.value.code == "mapping_prepare_rollback_failed"
    assert manager.transaction_path.exists()
    assert len(_git(feature, "diff", "--name-only").splitlines()) == 2


def test_successful_prepare_preserves_target_modes(integration_repository):
    _production, _develop, feature, _store, record, manager = integration_repository
    workspace = manager.catalog()["workspaces"][0]
    review = manager.review(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
    )
    targets = [
        feature / "config/player_save_versions/data_9_game_1073.json",
        feature / "config/player_save_versions/data_9_game_1101.json",
    ]
    before_modes = [path.stat().st_mode & 0o7777 for path in targets]

    manager.prepare(
        candidate_record_id=record["record_id"],
        workspace_id=workspace["workspace_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )

    assert [path.stat().st_mode & 0o7777 for path in targets] == before_modes


def test_invalid_transaction_journal_makes_catalog_structurally_unavailable(
    integration_repository,
):
    _production, _develop, feature, _store, _record, manager = (
        integration_repository
    )
    manager.transaction_path.write_text("{}\n", encoding="utf-8")

    catalog = manager.catalog()

    assert catalog["available"] is False
    assert catalog["code"] == "commit_state_uncertain"
    assert catalog["transaction"] is None
    assert not _git(feature, "status", "--porcelain")
