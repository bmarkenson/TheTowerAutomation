from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools import outcome_retirement


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _git_optional(repository: Path, *arguments: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Retirement Guard Test")
    _git(repository, "config", "user.email", "retirement@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "Base")
    return repository


def _worktree(
    repository: Path,
    tmp_path: Path,
    *,
    branch: str,
    start: str = "main",
) -> Path:
    path = tmp_path / branch.replace("/", "-")
    _git(repository, "worktree", "add", "-b", branch, str(path), start)
    return path


def _commit(
    worktree: Path,
    relative_path: str,
    contents: str,
    message: str,
) -> str:
    path = worktree / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    _git(worktree, "add", relative_path)
    _git(worktree, "commit", "-m", message)
    return _git(worktree, "rev-parse", "HEAD")


def _activate_owner(repository: Path, candidate: str) -> None:
    _git(
        repository,
        "update-ref",
        outcome_retirement.PROMOTION_OWNER_REF,
        candidate,
    )


def _cherry_pick_as_new_commit(
    candidate_worktree: Path,
    source_tip: str,
    message: str,
) -> str:
    _git(candidate_worktree, "cherry-pick", "--no-commit", source_tip)
    _git(candidate_worktree, "commit", "-m", message)
    return _git(candidate_worktree, "rev-parse", "HEAD")


def _three_patch_equivalent_sources(
    tmp_path: Path,
) -> tuple[Path, str, Path, str, list[tuple[str, Path, str, str]]]:
    repository = _repository(tmp_path)
    base = _git(repository, "rev-parse", "main")
    sources: list[tuple[str, Path, str, str]] = []
    for number in range(1, 4):
        branch = f"feature/delegated-{number}"
        worktree = _worktree(repository, tmp_path, branch=branch)
        tip = _commit(
            worktree,
            f"source-{number}.txt",
            f"delegated change {number}\n",
            f"Delegated source {number}",
        )
        sources.append((branch, worktree, tip, ""))

    candidate_worktree = _worktree(
        repository,
        tmp_path,
        branch="integration/coordinated-outcome",
    )
    replacements: list[tuple[str, Path, str, str]] = []
    for number, (branch, worktree, tip, _) in enumerate(sources, start=1):
        replacement = _cherry_pick_as_new_commit(
            candidate_worktree,
            tip,
            f"Aggregate delegated patch {number}",
        )
        assert replacement != tip
        replacements.append((branch, worktree, tip, replacement))
    candidate = _git(candidate_worktree, "rev-parse", "HEAD")
    _activate_owner(repository, candidate)
    return repository, base, candidate_worktree, candidate, replacements


def test_begin_detects_three_patches_newly_represented_in_candidate(
    tmp_path: Path,
) -> None:
    repository, base, candidate_worktree, candidate, sources = (
        _three_patch_equivalent_sources(tmp_path)
    )

    inventory = outcome_retirement.begin_inventory(
        repository,
        base=base,
        candidate=candidate,
        candidate_ref="integration/coordinated-outcome",
    )

    assert inventory["candidate_worktree"] == str(candidate_worktree)
    assert [source["branch"] for source in inventory["sources"]] == [
        f"refs/heads/{branch}" for branch, _, _, _ in sources
    ]
    for source, (_, worktree, tip, _) in zip(
        inventory["sources"], sources, strict=True
    ):
        assert source["tip"] == tip
        assert source["worktree"] == str(worktree)
        assert source["disposition"] == "pending"
        assert source["detected_by"] == ["patch_new_in_candidate"]
        assert source["patches_newly_represented"] == [tip]

    path = outcome_retirement.inventory_path(repository, candidate)
    assert path.parent == repository / ".git/thetower/outcome-retirement"
    assert path.is_file()


def test_patch_already_represented_in_base_is_not_a_candidate_source(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    source_worktree = _worktree(
        repository,
        tmp_path,
        branch="feature/already-in-production",
    )
    source_tip = _commit(
        source_worktree,
        "shared.txt",
        "same patch\n",
        "Source version",
    )
    _cherry_pick_as_new_commit(repository, source_tip, "Production version")
    base = _git(repository, "rev-parse", "main")
    candidate_worktree = _worktree(
        repository,
        tmp_path,
        branch="integration/unrelated-candidate",
    )
    candidate = _commit(
        candidate_worktree,
        "candidate.txt",
        "candidate only\n",
        "Candidate change",
    )
    _activate_owner(repository, candidate)

    inventory = outcome_retirement.begin_inventory(
        repository,
        base=base,
        candidate=candidate,
        candidate_ref="integration/unrelated-candidate",
    )

    assert inventory["sources"] == []


def test_close_rediscovers_a_missing_patch_source_and_keeps_owner(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    base = _git(repository, "rev-parse", "main")
    candidate_worktree = _worktree(
        repository,
        tmp_path,
        branch="integration/late-source",
    )
    candidate = _commit(
        candidate_worktree,
        "shared.txt",
        "candidate patch\n",
        "Candidate patch",
    )
    _activate_owner(repository, candidate)
    inventory = outcome_retirement.begin_inventory(
        repository,
        base=base,
        candidate=candidate,
        candidate_ref="integration/late-source",
    )
    assert inventory["sources"] == []

    source_worktree = _worktree(
        repository,
        tmp_path,
        branch="feature/created-after-inventory",
    )
    source_tip = _commit(
        source_worktree,
        "shared.txt",
        "candidate patch\n",
        "Delegated form of candidate patch",
    )
    _git(repository, "merge", "--ff-only", candidate)
    _git(repository, "worktree", "remove", str(candidate_worktree))
    _git(repository, "branch", "-d", "integration/late-source")

    with pytest.raises(outcome_retirement.ClosureBlocked) as blocked:
        outcome_retirement.close_promotion(repository, candidate=candidate)

    result = blocked.value.result
    assert result["ready"] is False
    assert result["new_sources_added"] == [
        "refs/heads/feature/created-after-inventory"
    ]
    assert result["blockers"] == [
        {
            "branch": "refs/heads/feature/created-after-inventory",
            "code": "pending_disposition",
            "message": "choose integrated, superseded, or retained",
        }
    ]
    assert result["sources"][0]["tip"] == source_tip
    assert (
        _git(repository, "rev-parse", outcome_retirement.PROMOTION_OWNER_REF)
        == candidate
    )


def test_integrated_disposition_requires_exact_ancestry_and_retirement(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    base = _git(repository, "rev-parse", "main")
    source_worktree = _worktree(
        repository,
        tmp_path,
        branch="feature/exact-source",
    )
    source_tip = _commit(
        source_worktree,
        "exact.txt",
        "exact source\n",
        "Exact source",
    )
    candidate_worktree = _worktree(
        repository,
        tmp_path,
        branch="integration/exact-source",
    )
    _git(
        candidate_worktree,
        "merge",
        "--no-ff",
        "--no-edit",
        "feature/exact-source",
    )
    candidate = _git(candidate_worktree, "rev-parse", "HEAD")
    _activate_owner(repository, candidate)
    outcome_retirement.begin_inventory(
        repository,
        base=base,
        candidate=candidate,
        candidate_ref="integration/exact-source",
        declared_sources=("feature/exact-source",),
    )

    outcome_retirement.set_disposition(
        repository,
        candidate=candidate,
        branch="feature/exact-source",
        disposition="integrated",
    )
    guarded = outcome_retirement.check_inventory(repository, candidate=candidate)
    assert {blocker["code"] for blocker in guarded["blockers"]} == {
        "integrated_branch_not_retired",
        "integrated_worktree_not_retired",
    }

    _git(repository, "merge", "--ff-only", candidate)
    _git(repository, "worktree", "remove", str(source_worktree))
    _git(repository, "branch", "-d", "feature/exact-source")
    closed = outcome_retirement.close_promotion(repository, candidate=candidate)

    assert source_tip != candidate
    assert closed["closed"] is True
    assert closed["ready"] is True
    assert _git_optional(
        repository,
        "rev-parse",
        "--verify",
        "--quiet",
        outcome_retirement.PROMOTION_OWNER_REF,
    ) == (1, "")


def test_patch_equivalent_sources_require_superseded_archives_before_close(
    tmp_path: Path,
) -> None:
    repository, base, _, candidate, sources = _three_patch_equivalent_sources(
        tmp_path
    )
    outcome_retirement.begin_inventory(
        repository,
        base=base,
        candidate=candidate,
        candidate_ref="integration/coordinated-outcome",
    )

    branch, _, _, _ = sources[0]
    with pytest.raises(
        outcome_retirement.RetirementGuardError,
        match="patch-equivalent work must be superseded",
    ):
        outcome_retirement.set_disposition(
            repository,
            candidate=candidate,
            branch=branch,
            disposition="integrated",
        )

    for number, (branch, worktree, tip, _) in enumerate(sources, start=1):
        archive = f"archive/test-source-{number}"
        _git(
            repository,
            "tag",
            "-a",
            archive,
            tip,
            "-m",
            f"Archive delegated source {number}",
        )
        outcome_retirement.set_disposition(
            repository,
            candidate=candidate,
            branch=branch,
            disposition="superseded",
            archive_tag=archive,
            reason="Patch was replayed in the aggregate candidate",
        )
        _git(repository, "worktree", "remove", str(worktree))
        _git(repository, "branch", "-D", branch)

    closed = outcome_retirement.close_promotion(repository, candidate=candidate)

    assert closed["closed"] is True
    assert closed["blockers"] == []
    assert outcome_retirement.inventory_path(repository, candidate).is_file()


def test_retained_sources_and_unrelated_dirty_worktrees_are_not_mutated(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    base = _git(repository, "rev-parse", "main")
    retained_worktree = _worktree(
        repository,
        tmp_path,
        branch="feature/retained-source",
    )
    retained_tip = _commit(
        retained_worktree,
        "retained.txt",
        "retained committed state\n",
        "Retained source",
    )
    unrelated_worktree = _worktree(
        repository,
        tmp_path,
        branch="feature/unrelated-dirty",
    )
    candidate_worktree = _worktree(
        repository,
        tmp_path,
        branch="integration/retained-source",
    )
    candidate = _commit(
        candidate_worktree,
        "candidate.txt",
        "candidate\n",
        "Candidate",
    )
    _activate_owner(repository, candidate)
    outcome_retirement.begin_inventory(
        repository,
        base=base,
        candidate=candidate,
        candidate_ref="integration/retained-source",
        declared_sources=("feature/retained-source",),
    )
    outcome_retirement.set_disposition(
        repository,
        candidate=candidate,
        branch="feature/retained-source",
        disposition="retained",
        owner="follow-up worker",
        remaining_work="Finish the independent experiment",
    )

    (retained_worktree / "retained.txt").write_text(
        "operator dirty state\n", encoding="utf-8"
    )
    (unrelated_worktree / "untracked-evidence.txt").write_text(
        "unrelated evidence\n", encoding="utf-8"
    )
    retained_status = _git(retained_worktree, "status", "--porcelain=v1")
    unrelated_status = _git(unrelated_worktree, "status", "--porcelain=v1")

    closed = outcome_retirement.close_promotion(repository, candidate=candidate)

    assert closed["closed"] is True
    assert _git(repository, "rev-parse", "feature/retained-source") == retained_tip
    assert _git(retained_worktree, "status", "--porcelain=v1") == retained_status
    assert _git(unrelated_worktree, "status", "--porcelain=v1") == unrelated_status
