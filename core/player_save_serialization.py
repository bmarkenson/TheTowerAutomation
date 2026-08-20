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

from core.player_save import PlayerSaveSnapshot
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
    quiet_player_save_read,
)
from core.run_state import AUTOMATION
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
    restoration_completed: bool = False

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
        return bool(
            self.restoration_completed
            or self.status
            in {
                GuardedSerializationStatus.COMPLETE,
                GuardedSerializationStatus.SOURCE_RESTORED,
            }
        )


class GuardedPlayerSaveSerializer:
    """Serialize one exact target while preserving caller-owned boundaries."""

    def __init__(
        self,
        *,
        acquirer: StablePlayerSaveAcquirer,
        context_guard_fn: Callable[[], bool],
        action_guard_fn: Callable[[], bool],
        source_guard_fn: Callable[[Any, bool], bool],
        background_fn: Optional[Callable[[str], Any]] = None,
        foreground_fn: Optional[Callable[[str], Any]] = None,
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
        allow_paused_terminal_save_refresh: bool = False,
    ) -> None:
        if not isinstance(acquirer, StablePlayerSaveAcquirer):
            raise TypeError("guarded serialization requires the shared acquirer")
        self._acquirer = acquirer
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
        if type(allow_paused_terminal_save_refresh) is not bool:
            raise TypeError(
                "allow_paused_terminal_save_refresh must be a boolean"
            )
        self._allow_paused_terminal_save_refresh = (
            allow_paused_terminal_save_refresh
        )

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

        with self._acquirer.deferred_completion_observers():
            return self._acquire_with_deferred_observer(
                expected_target=expected_target,
                expected_generation=expected_generation,
                target_generation_detail=target_generation_detail,
                source_label=source_label,
                initial_frame=initial_frame,
                stable_initial_source=stable_initial_source,
            )

    def _acquire_with_deferred_observer(
        self,
        *,
        expected_target: str,
        expected_generation: int,
        target_generation_detail: str,
        source_label: str,
        initial_frame: Any = None,
        stable_initial_source: bool = False,
    ) -> GuardedSerializationResult:
        """Run the guarded lifecycle before advisory completion observers."""

        background_dispatched = False
        background_attempted = False
        background_uncertain = False
        foreground_attempted = False
        foreground_uncertain = False
        acquisition: Optional[PlayerSaveAcquisitionBundle] = None
        pending_interrupt: Optional[BaseException] = None
        try:
            expected_binding = PlayerSaveTargetBinding(
                expected_target,
                expected_generation,
            )
        except (TypeError, ValueError):
            return _blocked("exact_target_ownership_unverified")

        # Passive source checks happen before the process-wide mutation
        # transaction so Pause is not delayed by ordinary observation.  The
        # exact target and context are rechecked after the transaction begins.
        if not self._acquirer.binding_matches(expected_binding):
            return _blocked("exact_target_ownership_unverified")
        if not self._context_matches():
            return _blocked("initial_source_boundary_unverified")
        if not self._source_matches(initial_frame, stable_initial_source):
            return _blocked("initial_source_boundary_unverified")
        if not self._context_matches():
            return _blocked("initial_source_boundary_unverified")

        # Lock order is mutation -> exact-target.  Once Android Home input is
        # authorized, restoration remains part of that one atomic lifecycle
        # mutation.  A later Pause waits for restoration rather than stranding
        # the game outside The Tower.
        with AUTOMATION.authorize_mutation(
            self._action_allowed,
            defer_dispatch_boundary=True,
            allow_paused_terminal_save_refresh=(
                self._allow_paused_terminal_save_refresh
            ),
        ) as allowed:
            if not allowed:
                return _blocked(
                    "control_authority_interrupted_before_background"
                )
            with self._acquirer.locked_operation():
                if not self._acquirer.binding_matches(expected_binding):
                    return _blocked("exact_target_ownership_unverified")
                if not self._context_matches():
                    return _blocked("initial_source_boundary_unverified")
                if not self._source_matches(initial_frame, stable_initial_source):
                    return _blocked("initial_source_boundary_unverified")

                restoration_required = False
                restoration_failure: Optional[str] = None
                early_result: Optional[GuardedSerializationResult] = None
                try:
                    try:
                        self._input_log_fn(
                            "Backgrounding The Tower to Android Home from "
                            f"{source_label}",
                            detail=(
                                f"[{self._log_prefix}] input=KEYCODE_HOME "
                                f"target_generation={target_generation_detail}"
                            ),
                        )
                    except Exception:
                        pass
                    # Passive validation and even diagnostic logging above can
                    # overlap a newly persisted Pause.  Consume durable
                    # authority again at the last point before the first
                    # lifecycle input.  Once that input is attempted, paired
                    # launcher restoration remains atomic below.
                    if not AUTOMATION.refresh_mutation_authority(
                        self._action_allowed
                    ):
                        early_result = _blocked(
                            "control_authority_interrupted_before_background"
                        )
                        continue_lifecycle = False
                    else:
                        continue_lifecycle = True
                    if not continue_lifecycle:
                        restoration_required = False
                    else:
                        # From this assignment onward, every exit—including an
                        # asynchronous SIGINT between Python statements—runs the
                        # paired launcher restoration in ``finally``.  Restoring
                        # when interruption landed just before dispatch is a safe
                        # conservative no-op; failing to restore after dispatch is
                        # not.
                        restoration_required = True
                        (
                            backgrounded,
                            background_attempted,
                            background_uncertain,
                            dispatch_interrupt,
                        ) = _invoke_lifecycle_dispatch(
                            self._background_fn,
                            expected_target,
                        )
                        pending_interrupt = dispatch_interrupt
                        try:
                            self._debug_log_fn(
                                f"[{self._log_prefix}] Android Home dispatch "
                                f"result={'accepted' if backgrounded else 'failed'}",
                                "DEBUG",
                            )
                        except Exception:
                            pass
                        background_dispatched = backgrounded
                        if not backgrounded and not background_attempted:
                            restoration_required = False
                            early_result = _blocked(
                                "background_serialization_dispatch_unavailable"
                            )
                        elif backgrounded and pending_interrupt is None:
                            try:
                                self._sleep_fn(0.25)
                                acquisition = self._acquirer.acquire(
                                    PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
                                    expected_binding=expected_binding,
                                )
                            except Exception:
                                acquisition = None
                except BaseException as exc:
                    pending_interrupt = pending_interrupt or exc
                finally:
                    if restoration_required:
                        try:
                            (
                                restoration_failure,
                                foreground_attempted,
                                foreground_uncertain,
                                restoration_interrupt,
                            ) = self._restore_after_background(
                                expected_binding=expected_binding,
                                expected_target=expected_target,
                                source_label=source_label,
                                target_generation_detail=(
                                    target_generation_detail
                                ),
                            )
                        except BaseException as exc:
                            # Last-resort cleanup if interruption lands before
                            # the restoration helper's own deferral boundary.
                            restoration_interrupt = exc
                            (
                                emergency_restored,
                                emergency_attempted,
                                emergency_uncertain,
                                emergency_interrupt,
                            ) = _invoke_lifecycle_dispatch(
                                self._foreground_fn,
                                expected_target,
                            )
                            foreground_uncertain = bool(
                                emergency_uncertain
                                or emergency_interrupt is not None
                            )
                            foreground_attempted = bool(emergency_attempted)
                            restoration_interrupt = (
                                restoration_interrupt or emergency_interrupt
                            )
                            if emergency_restored or emergency_attempted:
                                try:
                                    restoration_failure = (
                                        self._wait_for_source_restoration(
                                            expected_binding,
                                        )
                                    )
                                except BaseException:
                                    restoration_failure = (
                                        "restored_source_convergence_timeout"
                                    )
                            else:
                                restoration_failure = (
                                    "foreground_restoration_failed"
                                )
                        pending_interrupt = (
                            pending_interrupt or restoration_interrupt
                        )

                if pending_interrupt is not None:
                    _raise_lifecycle_interrupt(
                        pending_interrupt,
                        restored=(
                            restoration_required
                            and restoration_failure is None
                        ),
                        mutation_attempted=(
                            background_attempted or foreground_attempted
                        ),
                    )
                if early_result is not None:
                    return early_result
                if restoration_failure is not None:
                    return _blocked(
                        restoration_failure,
                        lifecycle_input_attempted=True,
                        background_dispatched=background_dispatched,
                    )

        # Mandatory source restoration is complete and the mutation boundary
        # is released.  Recheck ownership now so a Pause or workflow handoff
        # that waited for restoration can stop all follow-up work without ever
        # suppressing the restore itself.
        if not self._acquirer.binding_matches(expected_binding):
            return _blocked(
                "restored_target_binding_unverified",
                background_dispatched=background_dispatched,
                source_restored=True,
            )
        if not self._context_matches():
            return _blocked(
                "restored_context_boundary_unverified",
                background_dispatched=background_dispatched,
                source_restored=True,
            )
        if background_uncertain or foreground_uncertain:
            return _blocked(
                (
                    "background_serialization_dispatch_uncertain"
                    if background_uncertain
                    else "foreground_restoration_dispatch_uncertain"
                ),
                lifecycle_input_attempted=True,
                background_dispatched=background_dispatched,
                source_restored=True,
            )
        if not self._action_allowed():
            return _blocked(
                "restored_control_authority_interrupted",
                background_dispatched=background_dispatched,
                source_restored=True,
            )

        if not background_dispatched:
            return _blocked(
                "background_serialization_boundary_failed",
                lifecycle_input_attempted=True,
                source_restored=True,
            )

        if acquisition is None or not acquisition.complete:
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

    def _restore_after_background(
        self,
        *,
        expected_binding: PlayerSaveTargetBinding,
        expected_target: str,
        source_label: str,
        target_generation_detail: str,
    ) -> tuple[Optional[str], bool, bool, Optional[BaseException]]:
        """Restore the launcher source, deferring one shutdown interruption."""

        foregrounded = False
        foreground_attempted = False
        foreground_uncertain = False
        restoration_failure: Optional[str] = None
        pending_interrupt: Optional[BaseException] = None
        try:
            try:
                self._input_log_fn(
                    f"Restoring The Tower from Android Home to {source_label}",
                    detail=(
                        f"[{self._log_prefix}] input=launcher_restore "
                        f"target_generation={target_generation_detail}"
                    ),
                )
            except Exception:
                pass
            (
                foregrounded,
                foreground_attempted,
                foreground_uncertain,
                pending_interrupt,
            ) = _invoke_lifecycle_dispatch(
                self._foreground_fn,
                expected_target,
            )
            try:
                self._debug_log_fn(
                    f"[{self._log_prefix}] launcher restore "
                    f"result={'accepted' if foregrounded else 'failed'}",
                    "DEBUG",
                )
            except Exception:
                pass
            if not foregrounded and not foreground_attempted:
                restoration_failure = "foreground_restoration_failed"
            elif pending_interrupt is None:
                try:
                    restoration_failure = self._wait_for_source_restoration(
                        expected_binding,
                    )
                except Exception:
                    restoration_failure = (
                        "restored_source_convergence_timeout"
                    )
        except BaseException as exc:
            pending_interrupt = pending_interrupt or exc
        finally:
            if pending_interrupt is not None:
                # SIGINT may land anywhere in the restoration sequence, not
                # just inside subprocess.run.  Re-issue one bounded launcher
                # command and re-verify before the original interruption is
                # propagated to App.run() for graceful shutdown.
                try:
                    try:
                        self._input_log_fn(
                            "Retrying The Tower launcher restoration after "
                            "lifecycle interruption",
                            detail=(
                                f"[{self._log_prefix}] "
                                "input=launcher_restore retry=interrupt "
                                "target_generation="
                                f"{target_generation_detail}"
                            ),
                        )
                    except BaseException as exc:
                        pending_interrupt = pending_interrupt or exc
                    (
                        retry_foregrounded,
                        retry_attempted,
                        retry_uncertain,
                        retry_interrupt,
                    ) = _invoke_lifecycle_dispatch(
                        self._foreground_fn,
                        expected_target,
                    )
                    foregrounded = foregrounded or retry_foregrounded
                    foreground_attempted = (
                        foreground_attempted or retry_attempted
                    )
                    foreground_uncertain = bool(
                        foreground_uncertain
                        or retry_uncertain
                        or retry_interrupt is not None
                    )
                    pending_interrupt = pending_interrupt or retry_interrupt
                    if foregrounded or foreground_attempted:
                        try:
                            restoration_failure = (
                                self._wait_for_source_restoration(
                                    expected_binding,
                                )
                            )
                        except BaseException:
                            restoration_failure = (
                                "restored_source_convergence_timeout"
                            )
                    else:
                        restoration_failure = "foreground_restoration_failed"
                except BaseException:
                    restoration_failure = (
                        restoration_failure
                        or "restored_source_convergence_timeout"
                    )

        return (
            restoration_failure,
            foreground_attempted,
            foreground_uncertain,
            pending_interrupt,
        )

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
            authority_failure = self._restored_binding_failure(
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
                # Restoration evidence remains bound to the exact target.
                # Workflow/control changes are consumed after this atomic
                # source-restoration transaction releases its mutation lock.
                authority_failure = self._restored_binding_failure(
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

            authority_failure = self._restored_binding_failure(
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

    def _restored_binding_failure(
        self,
        expected_binding: PlayerSaveTargetBinding,
    ) -> Optional[str]:
        if not self._acquirer.binding_matches(expected_binding):
            return "restored_target_binding_unverified"
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
        try:
            self._debug_log_fn(
                f"[{self._log_prefix}] restored source convergence "
                f"result={outcome} attempt={attempt}/"
                f"{self._restoration_max_attempts} "
                f"elapsed_s={self._elapsed_since(started):.2f}",
                "DEBUG",
            )
        except Exception:
            # Verified device state outranks diagnostics.  A logger write
            # failure is recoverable reporting loss, never source loss.
            pass


def background_to_android_home(target: str) -> Any:
    from core.adb_utils import adb_shell

    return adb_shell(
        ["input", "keyevent", "KEYCODE_HOME"],
        device_id=target,
        report_errors=False,
        return_dispatch_outcome=True,
        defer_uncertain_reporting=True,
    )


def restore_tower_launcher(target: str) -> Any:
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
        return_dispatch_outcome=True,
        defer_uncertain_reporting=True,
    )


def _lifecycle_dispatch_result(value: Any) -> tuple[bool, bool, bool]:
    """Normalize typed production dispatch or conservative injected doubles."""

    if (
        hasattr(value, "accepted")
        and isinstance(getattr(value, "attempted", None), bool)
    ):
        try:
            return (
                bool(value.accepted),
                bool(value.attempted),
                bool(getattr(value, "uncertain", False)),
            )
        except Exception:
            return False, True, True
    # Existing injected callbacks return bool.  False is conservatively an
    # attempted/ambiguous lifecycle input unless typed ADB provenance proves
    # the host failed before dispatch.
    accepted = bool(value)
    return accepted, True, not accepted


def _invoke_lifecycle_dispatch(
    callback: Callable[[str], Any],
    target: str,
) -> tuple[bool, bool, bool, Optional[BaseException]]:
    """Call one lifecycle boundary while retaining interruption provenance."""

    try:
        accepted, attempted, uncertain = _lifecycle_dispatch_result(
            callback(target)
        )
        return accepted, attempted, uncertain, None
    except Exception:
        # Untyped injected callbacks cannot prove a raised command was never
        # dispatched.  Production ADB callbacks return typed provenance.
        return False, True, True, None
    except BaseException as exc:
        # Defer graceful shutdown/SystemExit until the paired restoration has
        # been attempted and verified under the same atomic mutation boundary.
        return False, True, True, exc


def _raise_lifecycle_interrupt(
    interruption: BaseException,
    *,
    restored: bool,
    mutation_attempted: bool,
) -> None:
    if mutation_attempted and not restored:
        AUTOMATION.report_uncertain_mutation(
            "Player-save lifecycle was interrupted after device input; "
            "mandatory source restoration was not verified"
        )
    raise interruption.with_traceback(interruption.__traceback__)


def _blocked(
    reason: str,
    *,
    lifecycle_input_attempted: bool = False,
    background_dispatched: bool = False,
    source_restored: bool = False,
) -> GuardedSerializationResult:
    return GuardedSerializationResult(
        GuardedSerializationStatus.BLOCKED,
        reason,
        lifecycle_input_attempted=(
            lifecycle_input_attempted or background_dispatched
        ),
        background_dispatched=background_dispatched,
        restoration_completed=source_restored,
    )


__all__ = [
    "GuardedPlayerSaveSerializer",
    "GuardedSerializationResult",
    "GuardedSerializationStatus",
    "background_to_android_home",
    "quiet_player_save_read",
    "restore_tower_launcher",
]
