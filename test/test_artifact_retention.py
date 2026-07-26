from __future__ import annotations

import os
from pathlib import Path

from core.artifact_retention import RuntimeArtifactRetention, prune_artifact_tree


DAY = 24 * 60 * 60


def _write_artifact(
    path: Path,
    *,
    size: int,
    modified_at: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (modified_at, modified_at))


def test_retention_removes_expired_then_oldest_files_to_enforce_size(tmp_path):
    root = tmp_path / "screenshots" / "matches"
    now = 10_000_000.0
    expired = root / "expired.png"
    oldest_kept = root / "oldest-kept.png"
    middle = root / "middle.png"
    newest = root / "nested" / "newest.png"
    _write_artifact(expired, size=10, modified_at=now - 31 * DAY)
    _write_artifact(oldest_kept, size=70, modified_at=now - 20 * DAY)
    _write_artifact(middle, size=70, modified_at=now - 10 * DAY)
    _write_artifact(newest, size=70, modified_at=now - DAY)

    result = prune_artifact_tree(
        root,
        max_age_days=30,
        max_bytes=140,
        now=now,
    )

    assert result.files_removed == 2
    assert result.bytes_removed == 80
    assert not expired.exists()
    assert not oldest_kept.exists()
    assert middle.exists()
    assert newest.exists()
    assert result.errors == ()


def test_retention_skips_symlinked_subtrees(tmp_path):
    root = tmp_path / "screenshots" / "matches"
    outside = tmp_path / "operator-owned"
    outside_file = outside / "keep.png"
    now = 20_000_000.0
    _write_artifact(outside_file, size=50, modified_at=now - 100 * DAY)
    root.mkdir(parents=True)
    (root / "linked").symlink_to(outside, target_is_directory=True)

    result = prune_artifact_tree(
        root,
        max_age_days=30,
        max_bytes=1,
        now=now,
    )

    assert result.files_removed == 0
    assert outside_file.exists()


def test_runtime_retention_is_rate_limited_and_rejects_broad_extra_roots(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("THETOWER_ARTIFACT_RETENTION_DAYS", "30")
    monkeypatch.setenv("THETOWER_ARTIFACT_MAX_BYTES", "100")
    monkeypatch.setenv("THETOWER_RETENTION_SWEEP_INTERVAL_SECONDS", "60")
    evidence = tmp_path / "screenshots" / "matches" / "old.png"
    _write_artifact(evidence, size=20, modified_at=1.0)

    retention = RuntimeArtifactRetention.for_repository(
        tmp_path,
        extra_roots=(tmp_path, tmp_path.parent),
    )

    first = retention.maybe_prune(now=40 * DAY, monotonic_now=100.0)
    assert first is not None
    assert first.files_removed == 1
    assert retention.maybe_prune(now=40 * DAY, monotonic_now=120.0) is None
    assert retention.maybe_prune(now=40 * DAY, monotonic_now=161.0) is not None
    assert tmp_path.resolve() not in retention.roots
    assert tmp_path.parent.resolve() not in retention.roots
