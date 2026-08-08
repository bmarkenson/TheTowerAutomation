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
            self._sleep_fn(0.5)

            if not (
                self._acquirer.binding_matches(expected_binding)
                and self._context_matches()
                and self._action_allowed()
            ):
                return _blocked(
                    "restored_source_boundary_unverified",
                    background_dispatched=True,
                )
            if not self._source_matches(None, True):
                return _blocked(
                    "restored_source_boundary_unverified",
                    background_dispatched=True,
                )
            # Do not let a context/control change during stable restoration
            # become an apparently verified serialization boundary.
            if not self._context_matches() or not self._action_allowed():
                return _blocked(
                    "restored_source_boundary_unverified",
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
