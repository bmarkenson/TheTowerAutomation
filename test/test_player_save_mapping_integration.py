from __future__ import annotations

import base64
import fcntl
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import core.player_save_mapping_develop_integration as develop_integration
from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    build_mapping_candidate_record,
    pending_mapping_candidate,
    resolve_mapping_candidates,
)
from core.player_save_mapping_develop_integration import (
    SAVE_MAPPING_INTEGRATION_CAPABILITY,
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
    develop = tmp_path / "TheTower-worktrees" / "dev"
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
    _git(production, "branch", "develop")
    develop.parent.mkdir(parents=True)
    _git(production, "worktree", "add", str(develop), "develop")
    for path in (develop / "config/player_save_versions").glob("*.json"):
        path.chmod(0o664)
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
    return production, develop, store, record, manager


def _review(manager: SaveMappingIntegrationManager, record: dict) -> dict:
    return manager.review(candidate_record_id=record["record_id"])


def _integrate(manager, record, review):
    return manager.integrate(
        candidate_record_id=record["record_id"],
        reviewed_proposal_fingerprint=review["reviewed_proposal_fingerprint"],
    )


def _start_evidence(production: Path, transaction: dict) -> dict[str, str]:
    started = datetime.fromisoformat(
        transaction["integration_available_since"]
    ) + timedelta(seconds=1)
    return {
        "main_commit": _git(production, "rev-parse", "main"),
        "acquired_at": started.isoformat(),
    }


def test_catalog_exposes_fixed_develop_eligibility_without_workspaces(
    integration_repository,
):
    production, develop, _store, record, manager = integration_repository

    catalog = manager.catalog()

    assert catalog["available"] is True
    assert catalog["capability"] == SAVE_MAPPING_INTEGRATION_CAPABILITY
    assert "workspaces" not in catalog
    assert catalog["repository"] == {
        "main_commit": _git(production, "rev-parse", "main"),
        "develop_commit": _git(production, "rev-parse", "develop"),
        "synchronized": True,
        "production_clean": True,
        "develop_clean": True,
        "develop_path": str(develop),
        "integration_available": True,
        "code": "",
        "reason": "",
    }
    assert catalog["items"][0]["record_id"] == record["record_id"]
    assert catalog["items"][0]["review_available"] is True


def test_review_is_nonmutating_and_binds_both_canonical_targets(
    integration_repository,
):
    production, develop, _store, record, manager = integration_repository
    before = {
        root: {
            path.name: path.read_bytes()
            for path in (root / "config/player_save_versions").glob("*.json")
        }
        for root in (production, develop)
    }

    review = _review(manager, record)

    assert review["operation"] == "review"
    assert review["integrate"] == {"available": True, "code": "", "reason": ""}
    assert "workspace" not in review
    assert len(review["reviewed_proposal_fingerprint"]) == 64
    assert len(review["canonical_mapping_fingerprint"]) == 64
    assert [target["mapping_id"] for target in review["proposal"]["targets"]] == [
        "data-9-game-1073",
        "data-9-game-1101",
    ]
    for root, files in before.items():
        assert not _git(root, "status", "--porcelain")
        for name, content in files.items():
            assert (root / "config/player_save_versions" / name).read_bytes() == content


def test_real_group_writable_mapping_modes_are_normalized_to_git_regular_files(
    integration_repository,
):
    production, develop, _store, record, manager = integration_repository

    review = _review(manager, record)
    result = _integrate(manager, record, review)

    assert {target["mode"] for target in review["rendered_targets"]} == {0o664}
    for target in result["targets"]:
        entry = _git(
            production,
            "ls-tree",
            result["integration_commit"],
            "--",
            target["path"],
        )
        assert entry.startswith("100644 blob ")
    assert not _git(develop, "status", "--porcelain")


def test_executable_canonical_target_is_rejected_during_read_only_review(
    integration_repository,
):
    production, develop, _store, record, manager = integration_repository
    _git(production, "config", "core.fileMode", "false")
    for root in (production, develop):
        (root / "config/player_save_versions/data_9_game_1073.json").chmod(0o775)

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)

    assert failure.value.code == "proposal_target_invalid"
    assert not manager.transaction_path.exists()


def test_missing_git_identity_disables_integration_before_confirmation(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    _git(production, "config", "user.name", "")
    _git(production, "config", "user.email", "")

    catalog = manager.catalog()

    assert catalog["repository"]["integration_available"] is False
    assert catalog["repository"]["code"] == "git_identity_unavailable"
    assert catalog["items"][0]["review_available"] is False
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "git_identity_unavailable"


def test_contradictory_semantics_leave_the_routine_fast_lane(
    integration_repository,
):
    _production, _develop, store, record, manager = integration_repository
    conflicting = _record(
        module_name="Contradictory Fixture Cannon Assist",
        source_fingerprint="2" * 64,
        recorded_at="2026-08-10T12:01:01+00:00",
    )
    store.append_once(conflicting)

    catalog = manager.catalog()

    assert len(catalog["items"]) == 2
    assert all(item["review_available"] is False for item in catalog["items"])
    assert all(
        item["review_code"]
        == "mapping_candidate_requires_ordinary_development"
        for item in catalog["items"]
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "mapping_candidate_requires_ordinary_development"


def test_integrate_creates_one_verified_develop_commit_and_leaves_main_untouched(
    integration_repository,
):
    production, develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")
    production_before = {
        path.name: path.read_bytes()
        for path in (production / "config/player_save_versions").glob("*.json")
    }

    result = _integrate(manager, record, review)

    commit = result["integration_commit"]
    assert result["disposition"] == "committed_to_develop"
    assert result["committed"] is True
    assert result["promoted"] is False
    assert result["mapping_invariants"] == "passed"
    assert result["promotion_validation"] == "pending"
    assert result["idempotent"] is False
    assert _git(production, "rev-parse", "main") == base
    assert _git(production, "rev-parse", "develop") == commit
    assert _git(production, "rev-parse", f"{commit}^") == base
    assert not _git(production, "status", "--porcelain")
    assert not _git(develop, "status", "--porcelain")
    assert _git(production, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines() == [
        "config/player_save_versions/data_9_game_1073.json",
        "config/player_save_versions/data_9_game_1101.json",
    ]
    message = _git(production, "show", "-s", "--format=%B", commit)
    assert message.startswith(
        f"Integrate save mapping candidate {record['record_id'][:12]}"
    )
    assert f"Save-Mapping-Candidate-ID: {record['record_id']}" in message
    assert (
        f"Save-Mapping-Proposal-Fingerprint: "
        f"{review['reviewed_proposal_fingerprint']}"
    ) in message
    for name, content in production_before.items():
        assert (production / "config/player_save_versions" / name).read_bytes() == content


def test_retry_after_lost_response_is_idempotent_and_does_not_create_a_commit(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    first = _integrate(manager, record, review)
    count = _git(production, "rev-list", "--count", "develop")

    repeated = _integrate(manager, record, review)

    assert repeated["integration_commit"] == first["integration_commit"]
    assert repeated["idempotent"] is True
    assert _git(production, "rev-list", "--count", "develop") == count


def test_exact_retry_after_external_promotion_reports_current_promoted_state(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    first = _integrate(manager, record, review)
    _git(production, "merge", "--ff-only", "develop")

    repeated = _integrate(manager, record, review)

    assert repeated["integration_commit"] == first["integration_commit"]
    assert repeated["idempotent"] is True
    assert repeated["promoted"] is True


def test_catalog_tracks_promotion_then_fresh_decode_validation(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    result = _integrate(manager, record, review)

    pending = manager.catalog()
    assert pending["items"][0]["state"] == "promotion_pending"
    assert pending["items"][0]["integration_commit"] == result["integration_commit"]

    _git(production, "merge", "--ff-only", "develop")
    deployed = manager.catalog()
    assert deployed["items"][0]["state"] == "production_validation_pending"

    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    identity = transaction["mapping_identity"]
    snapshot = SimpleNamespace(
        shape_valid=True,
        canonical_mapping_fingerprint=transaction["canonical_mapping_fingerprint"],
        mapping_id=identity["mapping_id"],
        mapping_authority_id=identity["authority_mapping_id"],
        mapping_structural_id=identity["structural_mapping_id"],
        source_sha256="a" * 64,
        captured_at="2026-08-12T20:00:00+00:00",
    )
    assert manager.observe_canonical_decode(
        snapshot,
        start_evidence=_start_evidence(production, transaction),
    ) is True
    assert not manager.transaction_path.exists()

    complete = manager.catalog()
    assert complete["items"] == []
    assert manager.status()["counts"]["integrated"] == 1


@pytest.mark.parametrize("root_name", ["production", "develop"])
def test_dirty_standing_worktree_disables_review_without_writing(
    integration_repository,
    root_name,
):
    production, develop, _store, record, manager = integration_repository
    root = production if root_name == "production" else develop
    (root / "operator-note.txt").write_text("owned\n", encoding="utf-8")

    catalog = manager.catalog()

    assert catalog["repository"]["integration_available"] is False
    assert catalog["items"][0]["review_available"] is False
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == f"{root_name}_worktree_dirty"


def test_unequal_main_and_develop_tips_disable_fast_lane(integration_repository):
    _production, develop, _store, record, manager = integration_repository
    (develop / "unrelated.txt").write_text("integration work\n", encoding="utf-8")
    _git(develop, "add", "unrelated.txt")
    _git(develop, "commit", "-m", "unrelated develop integration")

    catalog = manager.catalog()

    assert catalog["repository"]["code"] == "repository_not_synchronized"
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "repository_not_synchronized"


def test_busy_lock_reports_retryable_failure_without_mutation(
    integration_repository,
):
    production, develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    descriptor = os.open(manager.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SaveMappingIntegrationError) as failure:
            _integrate(manager, record, review)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert failure.value.code == "integration_busy"
    assert _git(production, "rev-parse", "main") == _git(production, "rev-parse", "develop")
    assert not _git(develop, "status", "--porcelain")


def test_crash_after_journal_is_recovered_only_by_explicit_retry(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "journal_written":
            raise SystemExit("simulated response loss")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit, match="simulated response loss"):
        _integrate(interrupted, record, review)

    assert manager.transaction_path.exists()
    assert _git(production, "rev-parse", "main") == _git(production, "rev-parse", "develop")
    assert not _git(develop, "status", "--porcelain")
    catalog = manager.catalog()
    assert catalog["items"][0]["state"] == "integration_recovery_required"
    assert catalog["items"][0]["review_available"] is True
    recovery_review = _review(manager, record)
    assert recovery_review["recovery_required"] is True
    assert recovery_review["reviewed_proposal_fingerprint"] == review[
        "reviewed_proposal_fingerprint"
    ]
    recovered = _integrate(manager, record, recovery_review)
    assert recovered["idempotent"] is True
    assert recovered["committed"] is True


def test_crash_after_develop_fast_forward_recovers_as_exact_idempotent_success(
    integration_repository,
):
    production, _develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_fast_forwarded":
            raise SystemExit("simulated response loss after ref update")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit, match="after ref update"):
        _integrate(interrupted, record, review)

    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    expected_commit = transaction["integration"]["expected_commit"]
    assert transaction["phase"] == "commit_ready"
    assert _git(production, "rev-parse", "develop") == expected_commit

    catalog = manager.catalog()
    assert catalog["items"][0]["state"] == "integration_recovery_required"
    assert catalog["items"][0]["review_available"] is True
    recovery_review = _review(manager, record)
    assert recovery_review["recovery_required"] is True
    recovered = _integrate(manager, record, recovery_review)

    assert recovered["integration_commit"] == expected_commit
    assert recovered["idempotent"] is True
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    assert transaction["phase"] == "committed_to_develop"


def test_ref_first_crash_recovers_only_the_exact_stale_develop_checkout(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")

    def interrupt(transition: str) -> None:
        if transition == "journal_written":
            raise SystemExit("simulated crash before ref update")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit, match="before ref update"):
        _integrate(interrupted, record, review)
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    expected = transaction["integration"]["expected_commit"]
    _git(develop, "switch", "--detach", base)
    _git(production, "update-ref", "refs/heads/develop", expected, base)
    assert _git(develop, "status", "--porcelain") == ""

    recovery_review = _review(manager, record)
    recovered = _integrate(manager, record, recovery_review)

    assert recovered["integration_commit"] == expected
    assert recovered["idempotent"] is True
    assert not _git(develop, "status", "--porcelain")


def test_crash_after_atomic_ref_update_is_gui_recoverable_while_detached(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_ref_updated":
            raise SystemExit("simulated crash after atomic ref update")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit, match="atomic ref update"):
        _integrate(interrupted, record, review)
    assert _git(develop, "branch", "--show-current") == ""
    assert not _git(develop, "status", "--porcelain")

    catalog = manager.catalog()
    assert catalog["available"] is True
    assert catalog["items"][0]["state"] == "integration_recovery_required"
    assert catalog["items"][0]["review_available"] is True
    recovery_review = _review(manager, record)
    recovered = _integrate(manager, record, recovery_review)

    assert recovered["idempotent"] is True
    assert _git(develop, "branch", "--show-current") == "develop"
    assert not _git(develop, "status", "--porcelain")


def test_partial_post_cas_checkout_is_preserved_as_unconfirmed(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_ref_updated":
            raise SystemExit("simulated crash during checkout refresh")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit, match="checkout refresh"):
        _integrate(interrupted, record, review)
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    changed = next(target for target in transaction["targets"] if target["changed"])
    target_path = develop / changed["path"]
    target_path.write_bytes(base64.b64decode(changed["after_base64"]))
    target_path.chmod(changed["mode"])
    assert _git(develop, "branch", "--show-current") == ""
    assert _git(develop, "status", "--porcelain")

    catalog = manager.catalog()
    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert catalog["items"][0]["review_available"] is False
    assert target_path.read_bytes() == base64.b64decode(changed["after_base64"])


@pytest.mark.parametrize("index_bits", range(4))
@pytest.mark.parametrize("worktree_bits", range(4))
def test_partial_checkout_recovery_matrix_is_actionable_only_when_clean(
    integration_repository,
    index_bits,
    worktree_bits,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_ref_updated":
            raise SystemExit("simulated crash during checkout refresh")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit):
        _integrate(interrupted, record, review)
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    changed = [target for target in transaction["targets"] if target["changed"]]
    assert len(changed) == 2
    for index, target in enumerate(changed):
        path = develop / target["path"]
        index_source = (
            transaction["integration"]["expected_commit"]
            if index_bits & (1 << index)
            else transaction["repository"]["base_commit"]
        )
        _git(
            develop,
            "update-index",
            "--cacheinfo",
            "100644",
            _git(develop, "rev-parse", f"{index_source}:{target['path']}"),
            target["path"],
        )
        content_key = "after_base64" if worktree_bits & (1 << index) else "before_base64"
        path.write_bytes(base64.b64decode(target[content_key]))
        path.chmod(target["mode"])

    catalog = manager.catalog()
    if index_bits or worktree_bits:
        assert catalog["items"][0]["state"] == "integration_unconfirmed"
        assert catalog["items"][0]["review_available"] is False
        return
    assert catalog["items"][0]["state"] == "integration_recovery_required"
    recovery_review = _review(manager, record)
    recovered = _integrate(manager, record, recovery_review)

    assert recovered["idempotent"] is True
    assert _git(develop, "branch", "--show-current") == "develop"
    assert not _git(develop, "status", "--porcelain")


def test_unrelated_post_cas_checkout_state_is_not_advertised_for_recovery(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_ref_updated":
            raise SystemExit("simulated crash during checkout refresh")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit):
        _integrate(interrupted, record, review)
    (develop / "operator-note.txt").write_text("preserve\n", encoding="utf-8")

    catalog = manager.catalog()

    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert catalog["items"][0]["review_available"] is False
    assert (develop / "operator-note.txt").read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize(
    ("transition", "lock_owner", "git_path"),
    [
        ("journal_written", "production", "refs/heads/develop"),
        ("develop_ref_updated", "develop", "index"),
    ],
)
def test_git_crash_lock_artifacts_disable_recovery_without_removal(
    integration_repository,
    transition,
    lock_owner,
    git_path,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(observed_transition: str) -> None:
        if observed_transition == transition:
            raise SystemExit("simulated killed Git operation")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit):
        _integrate(interrupted, record, review)
    owner = production if lock_owner == "production" else develop
    resolved_git_path = Path(_git(owner, "rev-parse", "--git-path", git_path))
    if not resolved_git_path.is_absolute():
        resolved_git_path = owner / resolved_git_path
    lock_path = Path(f"{resolved_git_path}.lock")
    lock_path.write_text("crash artifact\n", encoding="utf-8")

    catalog = manager.catalog()

    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert catalog["items"][0]["review_available"] is False
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(manager, record, review)
    assert failure.value.code == "commit_state_uncertain"
    assert lock_path.read_text(encoding="utf-8") == "crash artifact\n"


@pytest.mark.parametrize("race_kind", ["unrelated", "target"])
def test_concurrent_edit_at_partial_recovery_switch_is_preserved_and_unconfirmed(
    integration_repository,
    monkeypatch,
    race_kind,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_ref_updated":
            raise SystemExit("simulated crash during checkout refresh")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit):
        _integrate(interrupted, record, review)
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    recovery_review = _review(manager, record)
    original = develop_integration._git_mutate
    injected = False
    if race_kind == "unrelated":
        raced_path = develop / "operator-owned.txt"
        marker = "concurrent operator edit\n"
    else:
        target = next(item for item in transaction["targets"] if item["changed"])
        raced_path = develop / target["path"]
        marker = "concurrent operator target edit\n"

    def racing_git(repository_root, *arguments, **kwargs):
        nonlocal injected
        if (
            arguments[:2] == ("switch", "--detach")
            and not injected
        ):
            injected = True
            raced_path.write_text(marker, encoding="utf-8")
        return original(repository_root, *arguments, **kwargs)

    monkeypatch.setattr(develop_integration, "_git_mutate", racing_git)

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(manager, record, recovery_review)

    assert failure.value.code == "commit_state_uncertain"
    assert raced_path.read_text(encoding="utf-8") == marker
    assert _git(develop, "ls-files", "--unmerged") == ""
    catalog = manager.catalog()
    if catalog["available"]:
        assert catalog["items"][0]["state"] == "integration_unconfirmed"
        assert catalog["items"][0]["review_available"] is False
    else:
        assert catalog["items"] == []


def test_promoted_commit_ready_recovery_binds_the_original_reviewed_base(
    integration_repository,
):
    production, _develop, store, record, manager = integration_repository
    review = _review(manager, record)
    base = review["reviewed_base_commit"]

    def interrupt(transition: str) -> None:
        if transition == "develop_fast_forwarded":
            raise SystemExit("simulated response loss before phase update")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit, match="before phase update"):
        _integrate(interrupted, record, review)
    _git(production, "merge", "--ff-only", "develop")

    recovery_review = _review(manager, record)
    recovered = _integrate(manager, record, recovery_review)

    assert recovery_review["reviewed_base_commit"] == base
    assert recovery_review["repository"]["main_commit"] != base
    assert recovered["base_commit"] == base
    assert recovered["promoted"] is True
    assert recovered["idempotent"] is True


@pytest.mark.parametrize("transition", ["commit_created", "journal_written"])
def test_new_conflicting_candidate_before_ref_update_closes_routine_lane(
    integration_repository,
    transition,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")

    def append_conflict(observed_transition: str) -> None:
        if observed_transition == transition:
            store.append_once(
                _record(
                    module_name="Contradictory Fixture Cannon Assist",
                    source_fingerprint="8" * 64,
                    recorded_at="2026-08-10T12:02:01+00:00",
                )
            )

    racing = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=append_conflict,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(racing, record, review)

    assert failure.value.code == "mapping_candidate_requires_ordinary_development"
    assert _git(production, "rev-parse", "develop") == base
    assert not _git(develop, "status", "--porcelain")


@pytest.mark.parametrize("transition", ["journal_written", "develop_ref_updated"])
def test_conflicting_candidate_blocks_interrupted_transaction_recovery(
    integration_repository,
    transition,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(observed_transition: str) -> None:
        if observed_transition == transition:
            raise SystemExit("simulated response loss")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit):
        _integrate(interrupted, record, review)
    store.append_once(
        _record(
            module_name="Contradictory Fixture Cannon Assist",
            source_fingerprint="8" * 64,
            recorded_at="2026-08-10T12:02:01+00:00",
        )
    )
    branch_before = _git(develop, "branch", "--show-current")
    catalog = manager.catalog()

    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert catalog["items"][0]["review_available"] is False
    with pytest.raises(SaveMappingIntegrationError):
        _review(manager, record)

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(manager, record, review)

    assert failure.value.code == "mapping_candidate_requires_ordinary_development"
    assert _git(develop, "branch", "--show-current") == branch_before


def test_concurrent_develop_ref_movement_is_preserved_and_reported_uncertain(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def move_develop(transition: str) -> None:
        if transition == "before_develop_fast_forward":
            (develop / "concurrent.txt").write_text("concurrent\n", encoding="utf-8")
            _git(develop, "add", "concurrent.txt")
            _git(develop, "commit", "-m", "concurrent develop commit")

    drifting = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=move_develop,
    )
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(drifting, record, review)

    assert failure.value.code == "commit_state_uncertain"
    assert _git(develop, "log", "-1", "--format=%s") == "concurrent develop commit"
    assert manager.transaction_path.exists()


def test_branch_switch_before_fast_forward_is_detected_without_mutating_it(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")

    def switch_branch(transition: str) -> None:
        if transition == "before_develop_fast_forward":
            _git(develop, "switch", "-c", "operator-other")

    drifting = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=switch_branch,
    )

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(drifting, record, review)

    assert failure.value.code == "commit_state_uncertain"
    assert _git(production, "rev-parse", "develop") == base
    assert _git(production, "rev-parse", "operator-other") == base


def test_branch_switch_at_ref_command_cannot_advance_the_wrong_branch(
    integration_repository,
    monkeypatch,
):
    production, develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")
    original = develop_integration._git_mutate
    switched = False

    def racing_git(repository_root, *arguments, **kwargs):
        nonlocal switched
        if arguments and arguments[0] == "update-ref" and not switched:
            switched = True
            _git(develop, "switch", "-c", "operator-race")
        return original(repository_root, *arguments, **kwargs)

    monkeypatch.setattr(develop_integration, "_git_mutate", racing_git)

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(manager, record, review)

    assert failure.value.code == "commit_state_uncertain"
    assert _git(production, "rev-parse", "operator-race") == base
    assert _git(develop, "status", "--porcelain") == ""
    assert _git(production, "rev-parse", "develop") != base


def test_branch_switch_at_checkout_refresh_cannot_dirty_the_other_branch(
    integration_repository,
    monkeypatch,
):
    production, develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")
    _git(production, "branch", "operator-existing", base)
    original = develop_integration._git_mutate
    switched = False

    def racing_git(repository_root, *arguments, **kwargs):
        nonlocal switched
        if (
            arguments[:3] == ("switch", "--no-guess", "develop")
            and not switched
        ):
            switched = True
            _git(develop, "switch", "operator-existing")
        return original(repository_root, *arguments, **kwargs)

    monkeypatch.setattr(develop_integration, "_git_mutate", racing_git)

    result = _integrate(manager, record, review)

    assert result["committed"] is True
    assert _git(production, "rev-parse", "operator-existing") == base
    assert _git(develop, "branch", "--show-current") == "develop"
    assert not _git(develop, "status", "--porcelain")


def test_main_movement_at_ref_transaction_cannot_advance_develop(
    integration_repository,
    monkeypatch,
):
    production, develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    base = _git(production, "rev-parse", "main")
    original = develop_integration._git_mutate
    moved = False

    def racing_git(repository_root, *arguments, **kwargs):
        nonlocal moved
        if arguments and arguments[0] == "update-ref" and not moved:
            moved = True
            (production / "concurrent-main.txt").write_text(
                "concurrent\n",
                encoding="utf-8",
            )
            _git(production, "add", "concurrent-main.txt")
            _git(production, "commit", "-m", "concurrent main commit")
        return original(repository_root, *arguments, **kwargs)

    monkeypatch.setattr(develop_integration, "_git_mutate", racing_git)

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(manager, record, review)

    assert failure.value.code == "commit_state_uncertain"
    assert _git(production, "rev-parse", "develop") == base
    assert _git(develop, "branch", "--show-current") == "develop"
    assert not _git(develop, "status", "--porcelain")


def test_descendant_refs_are_unconfirmed_not_actionable_recovery(
    integration_repository,
):
    production, develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_fast_forwarded":
            raise SystemExit("simulated response loss")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit):
        _integrate(interrupted, record, review)
    _git(production, "merge", "--ff-only", "develop")
    (production / "main-descendant.txt").write_text("main\n", encoding="utf-8")
    _git(production, "add", "main-descendant.txt")
    _git(production, "commit", "-m", "main descendant")
    (develop / "develop-descendant.txt").write_text("develop\n", encoding="utf-8")
    _git(develop, "add", "develop-descendant.txt")
    _git(develop, "commit", "-m", "develop descendant")

    catalog = manager.catalog()

    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert catalog["items"][0]["review_available"] is False
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _review(manager, record)
    assert failure.value.code == "repository_not_synchronized"


def test_promoted_recovery_rejects_superseded_production_targets(
    integration_repository,
):
    production, _develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def interrupt(transition: str) -> None:
        if transition == "develop_fast_forwarded":
            raise SystemExit("simulated response loss")

    interrupted = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=interrupt,
    )
    with pytest.raises(SystemExit):
        _integrate(interrupted, record, review)
    _git(production, "merge", "--ff-only", "develop")
    target = production / "config/player_save_versions/data_9_game_1073.json"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    _git(production, "add", str(target.relative_to(production)))
    _git(production, "commit", "-m", "supersede promoted canonical target")

    catalog = manager.catalog()

    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert "superseding" in catalog["items"][0]["reason"]
    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(manager, record, review)
    assert failure.value.code == "commit_state_uncertain"


def test_production_dirtied_after_ref_update_prevents_success_claim(
    integration_repository,
):
    production, _develop, store, record, manager = integration_repository
    review = _review(manager, record)

    def dirty_production(transition: str) -> None:
        if transition == "develop_fast_forwarded":
            (production / "operator-note.txt").write_text(
                "concurrent\n",
                encoding="utf-8",
            )

    drifting = SaveMappingIntegrationManager(
        repository_root=production,
        candidate_store=store,
        lock_path=manager.lock_path,
        transaction_path=manager.transaction_path,
        decode_receipt_path=manager.decode_receipts.path,
        transaction_fault_hook=dirty_production,
    )

    with pytest.raises(SaveMappingIntegrationError) as failure:
        _integrate(drifting, record, review)

    assert failure.value.code == "commit_state_uncertain"
    assert manager.transaction_path.exists()


def test_later_develop_target_drift_is_not_advertised_for_promotion(
    integration_repository,
):
    _production, develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    _integrate(manager, record, review)
    target = develop / "config/player_save_versions/data_9_game_1073.json"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    _git(develop, "add", str(target.relative_to(develop)))
    _git(develop, "commit", "-m", "supersede canonical target")

    catalog = manager.catalog()

    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert "superseded" in catalog["items"][0]["reason"]


def test_divergent_main_is_not_advertised_as_fast_forward_promotion(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    _integrate(manager, record, review)
    (production / "production-only.txt").write_text("advance\n", encoding="utf-8")
    _git(production, "add", "production-only.txt")
    _git(production, "commit", "-m", "divergent production change")

    catalog = manager.catalog()

    assert catalog["items"][0]["state"] == "integration_unconfirmed"
    assert "diverged" in catalog["items"][0]["reason"]


def test_newer_identical_observation_does_not_hide_transaction_lifecycle(
    integration_repository,
):
    _production, _develop, store, record, manager = integration_repository
    review = _review(manager, record)
    result = _integrate(manager, record, review)
    store.append_once(
        _record(
            source_fingerprint="2" * 64,
            recorded_at="2026-08-10T12:01:01+00:00",
        )
    )

    catalog = manager.catalog()
    transaction_item = next(
        item
        for item in catalog["items"]
        if item.get("integration_commit") == result["integration_commit"]
    )

    assert transaction_item["state"] == "promotion_pending"


def test_legacy_feature_preparation_journal_blocks_without_interpretation(
    integration_repository,
):
    _production, _develop, _store, _record, manager = integration_repository
    manager.transaction_path.write_text(
        json.dumps({"kind": "save_mapping_preparation_transaction"}) + "\n",
        encoding="utf-8",
    )

    catalog = manager.catalog()

    assert catalog["available"] is False
    assert catalog["code"] == "legacy_transaction_recovery_required"


def test_decode_receipt_rejects_wrong_mapping_fingerprint(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    _integrate(manager, record, review)
    _git(production, "merge", "--ff-only", "develop")
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    identity = transaction["mapping_identity"]

    assert manager.observe_canonical_decode(
        SimpleNamespace(
            shape_valid=True,
            canonical_mapping_fingerprint="0" * 64,
            mapping_id=identity["mapping_id"],
            mapping_authority_id=identity["authority_mapping_id"],
            mapping_structural_id=identity["structural_mapping_id"],
            source_sha256="a" * 64,
            captured_at="2026-08-12T20:00:00+00:00",
        ),
        start_evidence=_start_evidence(production, transaction),
    ) is False
    assert not manager.decode_receipts.path.exists()


def test_decode_receipt_is_recorded_once_for_repeated_stable_acquisitions(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    _integrate(manager, record, review)
    _git(production, "merge", "--ff-only", "develop")
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    identity = transaction["mapping_identity"]

    def snapshot(source: str, captured_at: str) -> SimpleNamespace:
        return SimpleNamespace(
            shape_valid=True,
            canonical_mapping_fingerprint=transaction[
                "canonical_mapping_fingerprint"
            ],
            mapping_id=identity["mapping_id"],
            mapping_authority_id=identity["authority_mapping_id"],
            mapping_structural_id=identity["structural_mapping_id"],
            source_sha256=source,
            captured_at=captured_at,
        )

    assert manager.observe_canonical_decode(
        snapshot("a" * 64, "2026-08-12T20:00:00+00:00"),
        start_evidence=_start_evidence(production, transaction),
    ) is True
    assert manager.observe_canonical_decode(
        snapshot("b" * 64, "2026-08-12T20:01:00+00:00"),
        start_evidence=_start_evidence(production, transaction),
    ) is False

    records = manager.decode_receipts.list_records()
    assert len(records) == 1
    assert records[0]["acquisition_main_commit"] == _git(
        production,
        "rev-parse",
        "main",
    )
    assert records[0]["acquisition_started_at"]


def test_decode_receipt_requires_acquisition_to_start_after_integration(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    _integrate(manager, record, review)
    _git(production, "merge", "--ff-only", "develop")
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    identity = transaction["mapping_identity"]
    too_early = datetime.fromisoformat(
        transaction["integration_available_since"]
    ) - timedelta(seconds=1)

    observed = manager.observe_canonical_decode(
        SimpleNamespace(
            shape_valid=True,
            canonical_mapping_fingerprint=transaction[
                "canonical_mapping_fingerprint"
            ],
            mapping_id=identity["mapping_id"],
            mapping_authority_id=identity["authority_mapping_id"],
            mapping_structural_id=identity["structural_mapping_id"],
            source_sha256="a" * 64,
            captured_at="2026-08-12T20:00:00+00:00",
        ),
        start_evidence={
            "main_commit": _git(production, "rev-parse", "main"),
            "acquired_at": too_early.isoformat(),
        },
    )

    assert observed is False
    assert manager.transaction_path.exists()
    assert not manager.decode_receipts.path.exists()


def test_decode_receipt_store_repairs_only_an_incomplete_final_line(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    _integrate(manager, record, review)
    _git(production, "merge", "--ff-only", "develop")
    transaction = json.loads(manager.transaction_path.read_text(encoding="utf-8"))
    identity = transaction["mapping_identity"]
    assert manager.observe_canonical_decode(
        SimpleNamespace(
            shape_valid=True,
            canonical_mapping_fingerprint=transaction[
                "canonical_mapping_fingerprint"
            ],
            mapping_id=identity["mapping_id"],
            mapping_authority_id=identity["authority_mapping_id"],
            mapping_structural_id=identity["structural_mapping_id"],
            source_sha256="a" * 64,
            captured_at="2026-08-12T20:00:00+00:00",
        ),
        start_evidence=_start_evidence(production, transaction),
    ) is True
    with manager.decode_receipts.path.open("ab") as handle:
        handle.write(b'{"partial"')

    records = manager.decode_receipts.list_records()

    assert len(records) == 1
    assert manager.decode_receipts.path.read_bytes().endswith(b"\n")


def test_corrupt_decode_receipt_keeps_lifecycle_visible_as_unavailable(
    integration_repository,
):
    production, _develop, _store, record, manager = integration_repository
    review = _review(manager, record)
    _integrate(manager, record, review)
    _git(production, "merge", "--ff-only", "develop")
    manager.decode_receipts.path.write_text("not-json\n", encoding="utf-8")

    catalog = manager.catalog()
    status = manager.status()

    assert catalog["available"] is False
    assert catalog["code"] == "decode_receipt_store_invalid"
    assert status["available"] is False
    assert status["items"]
