"""Normal-runtime scheduling for shared passive save bundles.

An independent periodic read opportunistically observes naturally serialized
saves.  Stable Perk selection/exhaustion observations may also request a prompt
checkpoint.  Neither path forces serialization, grants freshness authority, or
reschedules the other.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import Callable, Optional, Sequence

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    StablePlayerSaveAcquirer,
)
from core.player_save_observation import PlayerSaveObservationContext
from utils.logger import log


PassiveBundleConsumer = Callable[
    [PlayerSaveAcquisitionBundle, PlayerSaveObservationContext, str],
    None,
]

_QUEUE_CAPACITY = 8
_PERIODIC_OBSERVATION_REASON = "periodic_interval"
_DEFAULT_OBSERVATION_INTERVAL_SECONDS = 300.0
_PERK_CHECKPOINT_REASONS = frozenset(
    {
        "perk_exhaustion_boundary",
        "perk_selection_boundary",
    }
)
_PASSIVE_ACQUISITION_REASONS = frozenset(
    {*_PERK_CHECKPOINT_REASONS, _PERIODIC_OBSERVATION_REASON}
)


class PlayerSavePassiveScheduler:
    """Acquire periodic and coalesced Perk-requested passive bundles."""

    def __init__(
        self,
        *,
        acquirer: StablePlayerSaveAcquirer,
        context_fn: Callable[[], Optional[PlayerSaveObservationContext]],
        consumers: Sequence[PassiveBundleConsumer],
        interval_seconds: float = _DEFAULT_OBSERVATION_INTERVAL_SECONDS,
        start_worker: bool = True,
    ) -> None:
        if not isinstance(acquirer, StablePlayerSaveAcquirer):
            raise TypeError("passive scheduler requires the shared acquirer")
        interval = float(interval_seconds)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError(
                "passive scheduler interval must be positive and finite"
            )
        if not callable(context_fn):
            raise TypeError("passive scheduler requires a context provider")
        normalized_consumers = tuple(consumer for consumer in consumers if callable(consumer))
        if not normalized_consumers:
            raise ValueError("passive scheduler requires at least one consumer")
        self._acquirer = acquirer
        self._context_fn = context_fn
        self._consumers = normalized_consumers
        self._interval_seconds = interval
        self._commands: queue.Queue[Optional[str]] = queue.Queue(
            maxsize=_QUEUE_CAPACITY
        )
        self._pending_lock = threading.Lock()
        self._pending_reasons: set[str] = set()
        self._closed = False
        self._thread: Optional[threading.Thread] = None
        if start_worker:
            self._thread = threading.Thread(
                target=self._worker,
                name="player-save-passive",
                daemon=True,
            )
            self._thread.start()

    def request_observation(self, reason_code: str) -> bool:
        """Request one recognized Perk checkpoint without blocking App."""

        reason = str(reason_code or "").strip().lower()
        if self._closed or reason not in _PERK_CHECKPOINT_REASONS:
            return False
        with self._pending_lock:
            if reason in self._pending_reasons:
                return True
            self._pending_reasons.add(reason)
        try:
            self._commands.put_nowait(reason)
        except queue.Full:
            with self._pending_lock:
                self._pending_reasons.discard(reason)
            return False
        return True

    def acquire_once(
        self,
        reason_code: str = "perk_selection_boundary",
    ) -> bool:
        """Synchronous test seam using the same one-read/many-consumer path."""

        reason = str(reason_code or "").strip().lower()
        if reason not in _PASSIVE_ACQUISITION_REASONS:
            raise ValueError(
                "passive acquisition is limited to periodic or explicit "
                "Perk checkpoints"
            )
        return self._acquire_and_fan_out(reason)

    def close(self, *, wait: bool = False, timeout: float = 1.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._commands.put_nowait(None)
        except queue.Full:
            pass
        if wait and self._thread is not None:
            self._thread.join(timeout=max(0.0, float(timeout)))

    def wait_until_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if self._commands.unfinished_tasks == 0:
                return True
            time.sleep(0.005)
        return self._commands.unfinished_tasks == 0

    def _worker(self) -> None:
        next_periodic = time.monotonic() + self._interval_seconds
        while not self._closed:
            timeout = max(0.0, next_periodic - time.monotonic())
            try:
                reason = self._commands.get(timeout=timeout)
            except queue.Empty:
                self._acquire_and_fan_out(_PERIODIC_OBSERVATION_REASON)
                next_periodic = time.monotonic() + self._interval_seconds
                continue
            try:
                if reason is None:
                    return
                with self._pending_lock:
                    self._pending_reasons.discard(reason)
                self._acquire_and_fan_out(reason)
            finally:
                self._commands.task_done()

    def _acquire_and_fan_out(self, reason: str) -> bool:
        reason = str(reason or "").strip().lower()
        if self._closed or reason not in _PASSIVE_ACQUISITION_REASONS:
            return False
        try:
            context = self._context_fn()
        except Exception:
            return False
        if not isinstance(context, PlayerSaveObservationContext) or not context.valid():
            return False
        acquisition = self._acquirer.acquire(
            PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
            expected_binding=context.target_binding,
        )
        try:
            current_context = self._context_fn()
        except Exception:
            return False
        if (
            not isinstance(current_context, PlayerSaveObservationContext)
            or current_context != context
        ):
            return False
        delivered = False
        for consumer in self._consumers:
            try:
                consumer(acquisition, context, reason)
                delivered = True
            except Exception:
                log(
                    "[PLAYER_SAVE_PASSIVE] A passive bundle consumer rejected "
                    "one observation; other consumers remain independent",
                    "DEBUG",
                )
        return delivered


__all__ = [
    "PassiveBundleConsumer",
    "PlayerSavePassiveScheduler",
]
