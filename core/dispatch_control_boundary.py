"""Cross-process ordering for durable control and Android mutations."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import threading
from typing import Iterator


class DispatchControlBoundaryError(RuntimeError):
    """Raised when the shared dispatch/control boundary cannot be acquired."""


class _ProcessBoundary:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.local = threading.local()


_BOUNDARIES_LOCK = threading.Lock()
_BOUNDARIES: dict[str, _ProcessBoundary] = {}


def dispatch_lock_path_for(control_path: Path | str) -> Path:
    """Return the companion lock shared by control writers and dispatchers."""

    path = Path(control_path)
    return path.with_name(f".{path.name}.dispatch.lock")


def _process_boundary(path: Path) -> _ProcessBoundary:
    key = str(path.resolve(strict=False))
    with _BOUNDARIES_LOCK:
        boundary = _BOUNDARIES.get(key)
        if boundary is None:
            boundary = _ProcessBoundary()
            _BOUNDARIES[key] = boundary
        return boundary


@contextmanager
def dispatch_control_boundary(lock_path: Path | str) -> Iterator[None]:
    """Linearize one control transition or mutation across all processes.

    The process-local RLock makes the boundary thread-safe and reentrant.  The
    advisory flock supplies the same ordering between the automation runtime,
    Control Surface service, CLI, and any other supported control writer.
    """

    path = Path(lock_path)
    boundary = _process_boundary(path)
    with boundary.lock:
        depth = int(getattr(boundary.local, "depth", 0))
        if depth > 0:
            boundary.local.depth = depth + 1
            try:
                yield
            finally:
                boundary.local.depth = depth
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                boundary.local.depth = 1
                try:
                    yield
                finally:
                    boundary.local.depth = 0
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            raise DispatchControlBoundaryError(
                f"Unable to lock dispatch/control boundary {path}: {exc}"
            ) from exc


__all__ = [
    "DispatchControlBoundaryError",
    "dispatch_control_boundary",
    "dispatch_lock_path_for",
]
