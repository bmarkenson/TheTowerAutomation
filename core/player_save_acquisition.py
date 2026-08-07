"""Typed, exact-target acquisition of one stable runtime player save.

The owner in this module is deliberately policy-free.  It serializes target
operations, verifies one exact target generation before and after transport,
performs the quiet two-identical-read pull, decodes in memory, and returns only
sanitized status plus a normalized snapshot.  Android lifecycle actions,
semantic projection, and consumer fallback remain with their existing owners.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Optional

from core.adb_target_session import ADB_TARGET_OPERATION_LOCK, AdbTargetSnapshot

if TYPE_CHECKING:
    from core.player_save import PlayerSaveSnapshot


class PlayerSaveAcquisitionType(str, Enum):
    """How the save's serialization boundary was established."""

    FORCED_SERIALIZATION = "forced_serialization"
    NATURAL_BOUNDARY = "natural_boundary"
    PASSIVE_STABLE_READ = "passive_stable_read"


class PlayerSaveAcquisitionStatus(str, Enum):
    """Transport/binding outcome, independent of later projection outcomes."""

    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"
    BINDING_REJECTED = "binding_rejected"
    BINDING_LOST = "binding_lost"


class PlayerSaveBoundaryKind(str, Enum):
    GAME_OVER = "GAME_OVER"
    TOURNAMENT_RESULTS = "TOURNAMENT_RESULTS"


@dataclass(frozen=True, repr=False)
class PlayerSaveTargetBinding:
    """Private exact target/generation; only its fingerprint is serializable."""

    target: str = field(repr=False)
    generation: int = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("player-save target binding requires a target")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("player-save target binding requires a generation")
        object.__setattr__(self, "target", self.target.strip())

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Any,
    ) -> Optional["PlayerSaveTargetBinding"]:
        if snapshot is None or getattr(snapshot, "owned", False) is not True:
            return None
        target = getattr(snapshot, "target", None)
        generation = getattr(snapshot, "generation", None)
        try:
            return cls(target=target, generation=generation)
        except (TypeError, ValueError):
            return None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            (
                "thetower-player-save-target-generation-v1\0"
                f"{self.target}\0{self.generation}"
            ).encode("utf-8")
        ).hexdigest()

    @property
    def private_key(self) -> tuple[str, int]:
        """Internal scope key; never include this tuple in durable evidence."""

        return self.target, self.generation

    def __repr__(self) -> str:
        return (
            "PlayerSaveTargetBinding("
            f"fingerprint='{self.fingerprint[:16]}...')"
        )


@dataclass(frozen=True)
class PlayerSaveNaturalBoundary:
    """Lifecycle-issued proof that a terminal screen is a natural save boundary."""

    kind: PlayerSaveBoundaryKind
    observed_at: datetime
    runtime_session_id: str = field(repr=False)
    activity_scope_id: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PlayerSaveBoundaryKind):
            raise TypeError("natural boundary kind must be typed")
        if not str(self.runtime_session_id or "").strip():
            raise ValueError("natural boundary requires a runtime session")
        object.__setattr__(self, "observed_at", _aware_utc(self.observed_at))
        object.__setattr__(
            self,
            "runtime_session_id",
            str(self.runtime_session_id).strip(),
        )
        scope = str(self.activity_scope_id or "").strip() or None
        object.__setattr__(self, "activity_scope_id", scope)

    def redacted(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "observed_at": self.observed_at.isoformat(),
            "runtime_session": _redacted("runtime", self.runtime_session_id),
            "activity_scope": (
                _redacted("scope", self.activity_scope_id)
                if self.activity_scope_id
                else None
            ),
        }


@dataclass(frozen=True)
class PlayerSaveAcquisitionBundle:
    """Immutable result of one exact-target stable acquisition attempt."""

    acquisition_type: PlayerSaveAcquisitionType
    status: PlayerSaveAcquisitionStatus
    reason: str
    binding: Optional[PlayerSaveTargetBinding] = field(repr=False)
    acquisition_started_at: datetime
    captured_at: Optional[datetime]
    acquisition_completed_at: datetime
    transport_stable: bool
    snapshot: Optional["PlayerSaveSnapshot"] = field(default=None, repr=False)
    boundary: Optional[PlayerSaveNaturalBoundary] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition_type, PlayerSaveAcquisitionType):
            raise TypeError("acquisition_type must be typed")
        if not isinstance(self.status, PlayerSaveAcquisitionStatus):
            raise TypeError("acquisition status must be typed")
        object.__setattr__(self, "reason", _safe_reason(self.reason))
        started = _aware_utc(self.acquisition_started_at)
        completed = _aware_utc(self.acquisition_completed_at)
        captured = _aware_utc(self.captured_at) if self.captured_at else None
        object.__setattr__(self, "acquisition_started_at", started)
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "acquisition_completed_at", completed)
        if completed < started:
            raise ValueError("acquisition completion precedes start")
        if captured is not None and not started <= captured <= completed:
            raise ValueError("capture time is outside acquisition interval")
        if self.acquisition_type is PlayerSaveAcquisitionType.NATURAL_BOUNDARY:
            if (
                self.status is PlayerSaveAcquisitionStatus.COMPLETE
                and not isinstance(self.boundary, PlayerSaveNaturalBoundary)
            ):
                raise ValueError("natural acquisition requires boundary evidence")
            if self.boundary is not None and not isinstance(
                self.boundary,
                PlayerSaveNaturalBoundary,
            ):
                raise TypeError("natural boundary evidence must be typed")
        elif self.boundary is not None:
            raise ValueError("only natural acquisition carries boundary evidence")
        if self.status is PlayerSaveAcquisitionStatus.COMPLETE:
            if self.binding is None or self.snapshot is None:
                raise ValueError("complete acquisition requires binding and snapshot")
            if self.transport_stable is not True or captured is None:
                raise ValueError("complete acquisition requires stable capture")
        elif self.snapshot is not None:
            raise ValueError("failed acquisition cannot retain a snapshot")

    @property
    def complete(self) -> bool:
        return bool(
            self.status is PlayerSaveAcquisitionStatus.COMPLETE
            and self.snapshot is not None
        )

    @property
    def binding_fingerprint(self) -> Optional[str]:
        return self.binding.fingerprint if self.binding is not None else None

    def matches_binding(self, binding: PlayerSaveTargetBinding) -> bool:
        return self.binding == binding

    def redacted_provenance(self) -> dict[str, Any]:
        """Return the only acquisition representation safe for persistence."""

        return {
            "schema_version": 1,
            "type": self.acquisition_type.value,
            "status": self.status.value,
            "reason": self.reason,
            "binding_fingerprint": self.binding_fingerprint,
            "transport_stable": self.transport_stable,
            "timing": {
                "started_at": self.acquisition_started_at.isoformat(),
                "captured_at": (
                    self.captured_at.isoformat() if self.captured_at else None
                ),
                "completed_at": self.acquisition_completed_at.isoformat(),
            },
            "boundary": self.boundary.redacted() if self.boundary else None,
        }


class StablePlayerSaveAcquirer:
    """Own the global lock and exact-target stable read/decode boundary."""

    def __init__(
        self,
        *,
        target_snapshot_fn: Optional[Callable[[], AdbTargetSnapshot]] = None,
        fixed_target: Optional[str] = None,
        pull_fn: Optional[Callable[..., bytes]] = None,
        decode_fn: Optional[Callable[..., "PlayerSaveSnapshot"]] = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        pull_options: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if (target_snapshot_fn is None) == (fixed_target is None):
            raise ValueError(
                "provide exactly one target_snapshot_fn or fixed_target"
            )
        if fixed_target is not None:
            fixed = PlayerSaveTargetBinding(str(fixed_target), 1)
            self._target_snapshot_fn = lambda: AdbTargetSnapshot(
                fixed.target,
                fixed.generation,
                True,
            )
        else:
            assert target_snapshot_fn is not None
            self._target_snapshot_fn = target_snapshot_fn

        from core.player_save import decode_player_save_bytes, pull_player_save_bytes

        self._default_pull = pull_fn is None or pull_fn is pull_player_save_bytes
        self._pull_fn = pull_fn or pull_player_save_bytes
        self._decode_fn = decode_fn or decode_player_save_bytes
        self._now_fn = now_fn
        self._pull_options = dict(pull_options or {})

    @contextmanager
    def locked_operation(self) -> Iterator["StablePlayerSaveAcquirer"]:
        """Keep target handoff excluded across a caller-owned lifecycle."""

        with ADB_TARGET_OPERATION_LOCK:
            yield self

    def current_binding(self) -> Optional[PlayerSaveTargetBinding]:
        with ADB_TARGET_OPERATION_LOCK:
            return self._current_binding_locked()

    def binding_matches(self, expected: PlayerSaveTargetBinding) -> bool:
        with ADB_TARGET_OPERATION_LOCK:
            return self._current_binding_locked() == expected

    def acquire(
        self,
        acquisition_type: PlayerSaveAcquisitionType,
        *,
        expected_binding: Optional[PlayerSaveTargetBinding] = None,
        boundary: Optional[PlayerSaveNaturalBoundary] = None,
    ) -> PlayerSaveAcquisitionBundle:
        """Acquire and decode once, returning only safe typed failure state."""

        if not isinstance(acquisition_type, PlayerSaveAcquisitionType):
            raise TypeError("acquisition type must be typed")
        started_at = self._now()
        with ADB_TARGET_OPERATION_LOCK:
            return self._acquire_locked(
                acquisition_type,
                expected_binding=expected_binding,
                boundary=boundary,
                started_at=started_at,
            )

    def _acquire_locked(
        self,
        acquisition_type: PlayerSaveAcquisitionType,
        *,
        expected_binding: Optional[PlayerSaveTargetBinding],
        boundary: Optional[PlayerSaveNaturalBoundary],
        started_at: datetime,
    ) -> PlayerSaveAcquisitionBundle:
        if (
            acquisition_type is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
            and not isinstance(boundary, PlayerSaveNaturalBoundary)
        ) or (
            acquisition_type is not PlayerSaveAcquisitionType.NATURAL_BOUNDARY
            and boundary is not None
        ):
            return self._failure(
                acquisition_type,
                PlayerSaveAcquisitionStatus.BINDING_REJECTED,
                "acquisition_boundary_invalid",
                expected_binding,
                started_at,
                boundary=(
                    boundary
                    if acquisition_type
                    is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
                    and isinstance(boundary, PlayerSaveNaturalBoundary)
                    else None
                ),
            )

        binding = self._current_binding_locked()
        if binding is None:
            return self._failure(
                acquisition_type,
                PlayerSaveAcquisitionStatus.BINDING_REJECTED,
                "exact_target_ownership_unverified",
                expected_binding,
                started_at,
                boundary=boundary,
            )
        if expected_binding is not None and binding != expected_binding:
            return self._failure(
                acquisition_type,
                PlayerSaveAcquisitionStatus.BINDING_REJECTED,
                "exact_target_binding_mismatch",
                binding,
                started_at,
                boundary=boundary,
            )

        payload: Optional[bytes] = None
        captured_at: Optional[datetime] = None
        transport_stable = False
        snapshot: Optional["PlayerSaveSnapshot"] = None
        reason: Optional[str] = None
        try:
            pull_kwargs: dict[str, Any] = {
                **self._pull_options,
                "device_id": binding.target,
            }
            if self._default_pull:
                pull_kwargs.setdefault("attempts", 3)
                pull_kwargs.setdefault("settle_seconds", 0.1)
                pull_kwargs["read_fn"] = quiet_player_save_read
            payload = self._pull_fn(**pull_kwargs)
            transport_stable = True
            captured_at = max(self._now(), started_at)
            snapshot = self._decode_fn(
                payload,
                source_name="playerInfo.dat",
                captured_at=captured_at,
            )
        except Exception as exc:
            reason = _failure_reason(exc)
        finally:
            payload = None

        binding_after = self._current_binding_locked()
        if binding_after != binding:
            snapshot = None
            return self._failure(
                acquisition_type,
                PlayerSaveAcquisitionStatus.BINDING_LOST,
                "exact_target_binding_lost",
                binding,
                started_at,
                captured_at=captured_at,
                transport_stable=transport_stable,
                boundary=boundary,
            )

        if reason is not None or snapshot is None:
            return self._failure(
                acquisition_type,
                PlayerSaveAcquisitionStatus.UNAVAILABLE,
                reason or "player_save_acquisition_failed",
                binding,
                started_at,
                captured_at=captured_at,
                transport_stable=transport_stable,
                boundary=boundary,
            )

        return PlayerSaveAcquisitionBundle(
            acquisition_type=acquisition_type,
            status=PlayerSaveAcquisitionStatus.COMPLETE,
            reason="save_acquired",
            binding=binding,
            acquisition_started_at=started_at,
            captured_at=captured_at,
            acquisition_completed_at=max(
                self._now(),
                captured_at,
                started_at,
            ),
            transport_stable=True,
            snapshot=snapshot,
            boundary=boundary,
        )

    def _current_binding_locked(self) -> Optional[PlayerSaveTargetBinding]:
        try:
            return PlayerSaveTargetBinding.from_snapshot(
                self._target_snapshot_fn()
            )
        except Exception:
            return None

    def _failure(
        self,
        acquisition_type: PlayerSaveAcquisitionType,
        status: PlayerSaveAcquisitionStatus,
        reason: str,
        binding: Optional[PlayerSaveTargetBinding],
        started_at: datetime,
        *,
        captured_at: Optional[datetime] = None,
        transport_stable: bool = False,
        boundary: Optional[PlayerSaveNaturalBoundary] = None,
    ) -> PlayerSaveAcquisitionBundle:
        normalized_capture = (
            max(captured_at, started_at) if captured_at is not None else None
        )
        return PlayerSaveAcquisitionBundle(
            acquisition_type=acquisition_type,
            status=status,
            reason=reason,
            binding=binding,
            acquisition_started_at=started_at,
            captured_at=normalized_capture,
            acquisition_completed_at=max(
                self._now(),
                normalized_capture or started_at,
                started_at,
            ),
            transport_stable=transport_stable,
            boundary=boundary,
        )

    def _now(self) -> datetime:
        return _aware_utc(self._now_fn())


def quiet_player_save_read(
    path: str,
    *,
    device_id: Optional[str] = None,
) -> Optional[bytes]:
    """Read the exact target without emitting target-bearing transport errors."""

    from core.adb_utils import read_device_file

    return read_device_file(
        path,
        device_id=device_id,
        report_errors=False,
    )


def _failure_reason(exc: Exception) -> str:
    from core.player_save import (
        PlayerSaveDecodeError,
        PlayerSaveError,
        PlayerSavePullError,
    )

    if isinstance(exc, PlayerSavePullError):
        return "stable_read_unavailable"
    if isinstance(exc, PlayerSaveDecodeError):
        return "decoder_unavailable"
    if isinstance(exc, PlayerSaveError):
        return "save_mapping_unavailable"
    return "player_save_acquisition_failed"


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("acquisition timestamps must be datetime values")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("acquisition timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _redacted(label: str, value: str) -> str:
    return hashlib.sha256(
        f"thetower-player-save-{label}-v1\0{value}".encode("utf-8")
    ).hexdigest()


def _safe_reason(value: Any) -> str:
    normalized = "_".join(str(value or "unknown").strip().lower().split())
    return "".join(
        character
        for character in normalized[:120]
        if character.isalnum() or character in {"_", "-", ":"}
    ) or "unknown"


__all__ = [
    "PlayerSaveAcquisitionBundle",
    "PlayerSaveAcquisitionStatus",
    "PlayerSaveAcquisitionType",
    "PlayerSaveBoundaryKind",
    "PlayerSaveNaturalBoundary",
    "PlayerSaveTargetBinding",
    "StablePlayerSaveAcquirer",
    "quiet_player_save_read",
]
