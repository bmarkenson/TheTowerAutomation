"""Guard one Android-Home player-save serialization and source restoration.

This module owns the shared device lifecycle used by Home preflight and the
narrow active-battle attachment check.  It never interprets save evidence or
authorizes a UI fallback: callers may do that only after ``SOURCE_RESTORED``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Callable, Optional

from core.player_save import (
    PlayerSaveSnapshot,
    decode_player_save_bytes,
    pull_player_save_bytes,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
    quiet_player_save_read,
)
from utils.logger import log, log_input


class GuardedSerializationStatus(str, Enum):
    COMPLETE = "complete"
    SOURCE_RESTORED = "source_restored"
    BLOCKED = "blocked"


RESTORED_SOURCE_INITIAL_SETTLE_SECONDS = 0.5
RESTORED_SOURCE_CONVERGENCE_TIMEOUT_SECONDS = 12.0
RESTORED_SOURCE_RETRY_INTERVAL_SECONDS = 0.5
RESTORED_SOURCE_MAX_ATTEMPTS = 6


@dataclass(frozen=True)
class GuardedSerializationResult:
    status: GuardedSerializationStatus
    reason: str
    acquisition: Optional[PlayerSaveAcquisitionBundle] = field(
        default=None,
        repr=False,
    )
    # True from the moment KEYCODE_HOME is attempted.  A transport-level
    # failure is not proof that Android ignored the input, so callers must
    # recapture before using their pre-serialization frame even when
    # ``background_dispatched`` is false.
    lifecycle_input_attempted: bool = False
    background_dispatched: bool = False

    @property
    def snapshot(self) -> Optional[PlayerSaveSnapshot]:
        acquisition = self.acquisition
        return acquisition.snapshot if acquisition is not None else None

    @property
    def complete(self) -> bool:
        return bool(
            self.status is GuardedSerializationStatus.COMPLETE
            and self.snapshot is not None
        )

    @property
    def source_restored(self) -> bool:
        return self.status in {
            GuardedSerializationStatus.COMPLETE,
            GuardedSerializationStatus.SOURCE_RESTORED,
        }


class GuardedPlayerSaveSerializer:
    """Serialize one exact target while preserving caller-owned boundaries."""

    def __init__(
        self,
        *,
        target_snapshot_fn: Callable[[], AdbTargetSnapshot],
        context_guard_fn: Callable[[], bool],
        action_guard_fn: Callable[[], bool],
        source_guard_fn: Callable[[Any, bool], bool],
        background_fn: Optional[Callable[[str], bool]] = None,
        foreground_fn: Optional[Callable[[str], bool]] = None,
        pull_fn: Callable[..., bytes] = pull_player_save_bytes,
        decode_fn: Callable[..., PlayerSaveSnapshot] = decode_player_save_bytes,
        acquirer: Optional[StablePlayerSaveAcquirer] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        restoration_timeout_seconds: float = (
            RESTORED_SOURCE_CONVERGENCE_TIMEOUT_SECONDS
        ),
        restoration_retry_interval_seconds: float = (
            RESTORED_SOURCE_RETRY_INTERVAL_SECONDS
        ),
        restoration_max_attempts: int = RESTORED_SOURCE_MAX_ATTEMPTS,
        input_log_fn: Callable[..., None] = log_input,
        debug_log_fn: Callable[..., None] = log,
        log_prefix: str = "PLAYER_SAVE_SERIALIZATION",
    ) -> None:
        self._acquirer = acquirer or StablePlayerSaveAcquirer(
            target_snapshot_fn=target_snapshot_fn,
            pull_fn=pull_fn,
            decode_fn=decode_fn,
        )
        self._context_guard_fn = context_guard_fn
        self._action_guard_fn = action_guard_fn
        self._source_guard_fn = source_guard_fn
        self._background_fn = background_fn or background_to_android_home
        self._foreground_fn = foreground_fn or restore_tower_launcher
        self._sleep_fn = sleep_fn
        self._monotonic_fn = monotonic_fn
        self._restoration_timeout_seconds = float(
            restoration_timeout_seconds
        )
        self._restoration_retry_interval_seconds = float(
            restoration_retry_interval_seconds
        )
        self._restoration_max_attempts = int(restoration_max_attempts)
        if self._restoration_timeout_seconds < 0:
            raise ValueError("restoration timeout must not be negative")
        if self._restoration_retry_interval_seconds < 0:
            raise ValueError("restoration retry interval must not be negative")
        if self._restoration_max_attempts < 1:
            raise ValueError("restoration max attempts must be positive")
        self._input_log_fn = input_log_fn
        self._debug_log_fn = debug_log_fn
        self._log_prefix = str(log_prefix or "PLAYER_SAVE_SERIALIZATION")

    def acquire(
        self,
        *,
        expected_target: str,
        expected_generation: int,
        target_generation_detail: str,
        source_label: str,
        initial_frame: Any = None,
        stable_initial_source: bool = False,
    ) -> GuardedSerializationResult:
        """Return a snapshot or a reason whose restoration class is explicit."""

        background_dispatched = False
        try:
            expected_binding = PlayerSaveTargetBinding(
                expected_target,
                expected_generation,
            )
        except (TypeError, ValueError):
            return _blocked("exact_target_ownership_unverified")

        with self._acquirer.locked_operation():
            if not self._acquirer.binding_matches(expected_binding):
                return _blocked("exact_target_ownership_unverified")
            if not self._context_matches():
                return _blocked("initial_source_boundary_unverified")
            if not self._source_matches(initial_frame, stable_initial_source):
                return _blocked("initial_source_boundary_unverified")
            # Scope/process context may change while the stable source frames
            # are being collected. Recheck it at the actual input boundary.
            if not self._context_matches():
                return _blocked("initial_source_boundary_unverified")
            if not self._action_allowed():
                return _blocked("control_authority_interrupted_before_background")

            self._input_log_fn(
                f"Backgrounding The Tower to Android Home from {source_label}",
                detail=(
                    f"[{self._log_prefix}] input=KEYCODE_HOME "
                    f"target_generation={target_generation_detail}"
                ),
            )
            try:
                backgrounded = bool(self._background_fn(expected_target))
            except Exception:
                backgrounded = False
            self._debug_log_fn(
                f"[{self._log_prefix}] Android Home dispatch "
                f"result={'accepted' if backgrounded else 'failed'}",
                "DEBUG",
            )
            if not backgrounded:
                return _blocked(
                    "background_serialization_boundary_failed",
                    lifecycle_input_attempted=True,
                )
            background_dispatched = True
            self._sleep_fn(0.25)

            acquisition = self._acquirer.acquire(
                PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
                expected_binding=expected_binding,
            )

            if not self._action_allowed():
                return _blocked(
                    "control_authority_interrupted_before_foreground",
                    background_dispatched=True,
                )
            self._input_log_fn(
                f"Restoring The Tower from Android Home to {source_label}",
                detail=(
                    f"[{self._log_prefix}] input=launcher_restore "
                    f"target_generation={target_generation_detail}"
                ),
            )
            try:
                foregrounded = bool(self._foreground_fn(expected_target))
            except Exception:
                foregrounded = False
            self._debug_log_fn(
                f"[{self._log_prefix}] launcher restore "
                f"result={'accepted' if foregrounded else 'failed'}",
                "DEBUG",
            )
            if not foregrounded:
                return _blocked(
                    "foreground_restoration_failed",
                    background_dispatched=True,
                )
            restoration_failure = self._wait_for_source_restoration(
                expected_binding,
            )
            if restoration_failure is not None:
                return _blocked(
                    restoration_failure,
                    background_dispatched=True,
                )

        if not acquisition.complete:
            return GuardedSerializationResult(
                GuardedSerializationStatus.SOURCE_RESTORED,
                "save_acquisition_failed",
                acquisition=acquisition,
                lifecycle_input_attempted=True,
                background_dispatched=background_dispatched,
            )
        return GuardedSerializationResult(
            GuardedSerializationStatus.COMPLETE,
            "save_acquired",
            acquisition=acquisition,
            lifecycle_input_attempted=True,
            background_dispatched=background_dispatched,
        )

    def _context_matches(self) -> bool:
        try:
            return self._context_guard_fn() is True
        except Exception:
            return False

    def _action_allowed(self) -> bool:
        try:
            return self._action_guard_fn() is True
        except Exception:
            return False

    def _source_matches(self, initial_frame: Any, stable: bool) -> bool:
        try:
            return self._source_guard_fn(initial_frame, stable) is True
        except Exception:
            return False

    def _wait_for_source_restoration(
        self,
        expected_binding: PlayerSaveTargetBinding,
    ) -> Optional[str]:
        """Wait briefly for the foreground source to become stably observable."""

        started = self._monotonic()
        initial_settle = min(
            RESTORED_SOURCE_INITIAL_SETTLE_SECONDS,
            self._restoration_timeout_seconds,
        )
        if initial_settle > 0:
            self._sleep_fn(initial_settle)

        for attempt in range(1, self._restoration_max_attempts + 1):
            authority_failure = self._restored_authority_failure(
                expected_binding,
            )
            if authority_failure is not None:
                self._log_restoration_outcome(
                    authority_failure,
                    attempt=attempt,
                    started=started,
                )
                return authority_failure

            if self._source_matches(None, True):
                # Stable observation is evidence only while the exact target,
                # caller context, and action authority still match.
                authority_failure = self._restored_authority_failure(
                    expected_binding,
                )
                if authority_failure is not None:
                    self._log_restoration_outcome(
                        authority_failure,
                        attempt=attempt,
                        started=started,
                    )
                    return authority_failure
                self._log_restoration_outcome(
                    "verified",
                    attempt=attempt,
                    started=started,
                )
                return None

            authority_failure = self._restored_authority_failure(
                expected_binding,
            )
            if authority_failure is not None:
                self._log_restoration_outcome(
                    authority_failure,
                    attempt=attempt,
                    started=started,
                )
                return authority_failure

            elapsed = self._elapsed_since(started)
            exhausted = bool(
                attempt >= self._restoration_max_attempts
                or elapsed >= self._restoration_timeout_seconds
            )
            self._log_restoration_outcome(
                (
                    "restored_source_convergence_timeout"
                    if exhausted
                    else "source_not_yet_stable"
                ),
                attempt=attempt,
                started=started,
            )
            if exhausted:
                return "restored_source_convergence_timeout"

            remaining = max(
                0.0,
                self._restoration_timeout_seconds - elapsed,
            )
            retry_delay = min(
                self._restoration_retry_interval_seconds,
                remaining,
            )
            if retry_delay > 0:
                self._sleep_fn(retry_delay)

        return "restored_source_convergence_timeout"

    def _restored_authority_failure(
        self,
        expected_binding: PlayerSaveTargetBinding,
    ) -> Optional[str]:
        if not self._acquirer.binding_matches(expected_binding):
            return "restored_target_binding_unverified"
        if not self._context_matches():
            return "restored_context_boundary_unverified"
        if not self._action_allowed():
            return "restored_control_authority_interrupted"
        return None

    def _monotonic(self) -> float:
        try:
            return float(self._monotonic_fn())
        except Exception:
            return 0.0

    def _elapsed_since(self, started: float) -> float:
        return max(0.0, self._monotonic() - started)

    def _log_restoration_outcome(
        self,
        outcome: str,
        *,
        attempt: int,
        started: float,
    ) -> None:
        self._debug_log_fn(
            f"[{self._log_prefix}] restored source convergence "
            f"result={outcome} attempt={attempt}/"
            f"{self._restoration_max_attempts} "
            f"elapsed_s={self._elapsed_since(started):.2f}",
            "DEBUG",
        )


def background_to_android_home(target: str) -> bool:
    from core.adb_utils import adb_shell

    return adb_shell(
        ["input", "keyevent", "KEYCODE_HOME"],
        device_id=target,
        report_errors=False,
    ) is not None


def restore_tower_launcher(target: str) -> bool:
    from core.adb_utils import adb_shell

    return adb_shell(
        [
            "monkey",
            "-p",
            "com.TechTreeGames.TheTower",
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        device_id=target,
        report_errors=False,
    ) is not None


def _blocked(
    reason: str,
    *,
    lifecycle_input_attempted: bool = False,
    background_dispatched: bool = False,
) -> GuardedSerializationResult:
    return GuardedSerializationResult(
        GuardedSerializationStatus.BLOCKED,
        reason,
        lifecycle_input_attempted=(
            lifecycle_input_attempted or background_dispatched
        ),
        background_dispatched=background_dispatched,
    )


__all__ = [
    "GuardedPlayerSaveSerializer",
    "GuardedSerializationResult",
    "GuardedSerializationStatus",
    "background_to_android_home",
    "quiet_player_save_read",
    "restore_tower_launcher",
]
