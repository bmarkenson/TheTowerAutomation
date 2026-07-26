"""Bounded retention for generated runtime screenshots and evidence."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time
from typing import Iterable, Optional


DEFAULT_ARTIFACT_RETENTION_DAYS = 30
DEFAULT_ARTIFACT_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_RETENTION_SWEEP_INTERVAL_SECONDS = 6 * 60 * 60
SIZE_PRUNE_GRACE_SECONDS = 5 * 60
ARTIFACT_RETENTION_DAYS_ENV = "THETOWER_ARTIFACT_RETENTION_DAYS"
ARTIFACT_MAX_BYTES_ENV = "THETOWER_ARTIFACT_MAX_BYTES"
RETENTION_SWEEP_INTERVAL_ENV = "THETOWER_RETENTION_SWEEP_INTERVAL_SECONDS"


@dataclass(frozen=True)
class RetentionResult:
    """Summary of one generated-artifact retention sweep."""

    files_removed: int = 0
    bytes_removed: int = 0
    directories_removed: int = 0
    errors: tuple[str, ...] = ()

    def combine(self, other: "RetentionResult") -> "RetentionResult":
        return RetentionResult(
            files_removed=self.files_removed + other.files_removed,
            bytes_removed=self.bytes_removed + other.bytes_removed,
            directories_removed=(
                self.directories_removed + other.directories_removed
            ),
            errors=self.errors + other.errors,
        )


@dataclass(frozen=True)
class _ArtifactFile:
    path: Path
    size: int
    modified_at: float


def prune_artifact_tree(
    root: Path | str,
    *,
    max_age_days: int = DEFAULT_ARTIFACT_RETENTION_DAYS,
    max_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES,
    now: Optional[float] = None,
) -> RetentionResult:
    """Remove aged and then oldest generated files from one owned directory."""

    target = Path(root)
    if not target.exists():
        return RetentionResult()
    if target.is_symlink() or not target.is_dir():
        return RetentionResult(errors=(f"unsafe retention root: {target}",))

    age_days = max(1, int(max_age_days))
    byte_limit = max(1, int(max_bytes))
    current_time = time.time() if now is None else float(now)
    cutoff = current_time - (age_days * 24 * 60 * 60)
    errors: list[str] = []
    files: list[_ArtifactFile] = []

    for directory, directory_names, file_names in os.walk(
        target,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        directory_names[:] = [
            name
            for name in directory_names
            if not (directory_path / name).is_symlink()
        ]
        for name in file_names:
            path = directory_path / name
            try:
                details = path.lstat()
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            if not stat.S_ISREG(details.st_mode):
                continue
            files.append(
                _ArtifactFile(
                    path=path,
                    size=max(0, int(details.st_size)),
                    modified_at=float(details.st_mtime),
                )
            )

    removed_files = 0
    removed_bytes = 0
    remaining: list[_ArtifactFile] = []
    for artifact in sorted(files, key=lambda item: (item.modified_at, str(item.path))):
        if artifact.modified_at >= cutoff:
            remaining.append(artifact)
            continue
        if _unlink_artifact(artifact.path, errors):
            removed_files += 1
            removed_bytes += artifact.size
        else:
            remaining.append(artifact)

    total_bytes = sum(item.size for item in remaining)
    size_prune_cutoff = current_time - SIZE_PRUNE_GRACE_SECONDS
    for artifact in remaining:
        if total_bytes <= byte_limit or artifact.modified_at > size_prune_cutoff:
            continue
        if _unlink_artifact(artifact.path, errors):
            removed_files += 1
            removed_bytes += artifact.size
            total_bytes -= artifact.size

    removed_directories = _remove_empty_directories(target)
    return RetentionResult(
        files_removed=removed_files,
        bytes_removed=removed_bytes,
        directories_removed=removed_directories,
        errors=tuple(errors),
    )


def _unlink_artifact(path: Path, errors: list[str]) -> bool:
    try:
        path.unlink()
        return True
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return False


def _remove_empty_directories(root: Path) -> int:
    removed = 0
    for directory, directory_names, _ in os.walk(
        root,
        topdown=False,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in directory_names:
            candidate = directory_path / name
            if candidate.is_symlink():
                continue
            try:
                candidate.rmdir()
                removed += 1
            except OSError:
                # Nonempty directories are expected and not errors.
                pass
    return removed


class RuntimeArtifactRetention:
    """Rate-limited retention over the runtime-owned evidence directories."""

    def __init__(
        self,
        roots: Iterable[Path | str],
        *,
        max_age_days: int = DEFAULT_ARTIFACT_RETENTION_DAYS,
        max_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES,
        interval_seconds: float = DEFAULT_RETENTION_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self.roots = tuple(_deduplicate_safe_roots(roots))
        self.max_age_days = max(1, int(max_age_days))
        self.max_bytes = max(1, int(max_bytes))
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._last_sweep_monotonic: Optional[float] = None

    @classmethod
    def for_repository(
        cls,
        repository_root: Path | str,
        *,
        extra_roots: Iterable[Path | str | None] = (),
    ) -> "RuntimeArtifactRetention":
        repository = Path(repository_root).resolve()
        roots: list[Path] = [
            repository / "screenshots" / "matches",
            repository / "logs" / "battle_observations",
        ]
        for root in extra_roots:
            if root is None or not str(root).strip():
                continue
            candidate = Path(root)
            candidate = (
                candidate if candidate.is_absolute() else repository / candidate
            ).resolve()
            try:
                relative = candidate.relative_to(repository)
            except ValueError:
                continue
            if not relative.parts:
                continue
            roots.append(candidate)
        return cls(
            roots,
            max_age_days=_positive_environment_integer(
                ARTIFACT_RETENTION_DAYS_ENV,
                DEFAULT_ARTIFACT_RETENTION_DAYS,
            ),
            max_bytes=_positive_environment_integer(
                ARTIFACT_MAX_BYTES_ENV,
                DEFAULT_ARTIFACT_MAX_BYTES,
            ),
            interval_seconds=_positive_environment_integer(
                RETENTION_SWEEP_INTERVAL_ENV,
                DEFAULT_RETENTION_SWEEP_INTERVAL_SECONDS,
            ),
        )

    def maybe_prune(
        self,
        *,
        force: bool = False,
        now: Optional[float] = None,
        monotonic_now: Optional[float] = None,
    ) -> Optional[RetentionResult]:
        monotonic_value = (
            time.monotonic() if monotonic_now is None else float(monotonic_now)
        )
        if (
            not force
            and self._last_sweep_monotonic is not None
            and monotonic_value - self._last_sweep_monotonic
            < self.interval_seconds
        ):
            return None
        self._last_sweep_monotonic = monotonic_value
        result = RetentionResult()
        for root in self.roots:
            result = result.combine(
                prune_artifact_tree(
                    root,
                    max_age_days=self.max_age_days,
                    max_bytes=self.max_bytes,
                    now=now,
                )
            )
        return result


def _deduplicate_safe_roots(roots: Iterable[Path | str]) -> list[Path]:
    safe: list[Path] = []
    seen: set[Path] = set()
    home = Path.home().resolve()
    for root in roots:
        candidate = Path(root).resolve()
        anchor = Path(candidate.anchor)
        if candidate in {anchor, home} or candidate.parent == candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        safe.append(candidate)
    return safe


def _positive_environment_integer(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        parsed = int(raw)
    except ValueError:
        return int(default)
    return parsed if parsed > 0 else int(default)


__all__ = [
    "ARTIFACT_MAX_BYTES_ENV",
    "ARTIFACT_RETENTION_DAYS_ENV",
    "DEFAULT_ARTIFACT_MAX_BYTES",
    "DEFAULT_ARTIFACT_RETENTION_DAYS",
    "DEFAULT_RETENTION_SWEEP_INTERVAL_SECONDS",
    "RETENTION_SWEEP_INTERVAL_ENV",
    "RetentionResult",
    "RuntimeArtifactRetention",
    "prune_artifact_tree",
]
