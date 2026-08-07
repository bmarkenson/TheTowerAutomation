"""Normal-runtime scheduling for shared passive player-save bundles.

This owner controls cadence only.  It performs no Android lifecycle action and
grants no input authority.  One typed passive bundle is delivered to every
registered consumer; consumers project it independently and never reacquire.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import Callable, Optional, Sequence

from core.perk_save_monitor import PerkSaveMonitorContext
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    StablePlayerSaveAcquirer,
)
from utils.logger import log


PassiveBundleConsumer = Callable[
    [PlayerSaveAcquisitionBundle, PerkSaveMonitorContext, str],
    None,
]

_QUEUE_CAPACITY = 8
_SAFE_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,95}")


class PlayerSavePassiveScheduler:
    """Acquire one passive bundle on cadence or a coalesced checkpoint request."""

    def __init__(
        self,
        *,
        acquirer: StablePlayerSaveAcquirer,
        context_fn: Callable[[], Optional[PerkSaveMonitorContext]],
        consumers: Sequence[PassiveBundleConsumer],
        interval_seconds: float = 300.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
        start_worker: bool = True,
    ) -> None:
        if not isinstance(acquirer, StablePlayerSaveAcquirer):
            raise TypeError("passive scheduler requires the shared acquirer")
        interval = float(interval_seconds)
        if interval <= 0:
            raise ValueError("passive scheduler interval must be positive")
        if not callable(context_fn):
            raise TypeError("passive scheduler requires a context provider")
        normalized_consumers = tuple(consumer for consumer in consumers if callable(consumer))
        if not normalized_consumers:
            raise ValueError("passive scheduler requires at least one consumer")
        self._acquirer = acquirer
        self._context_fn = context_fn
        self._consumers = normalized_consumers
        self._interval_seconds = interval
        self._monotonic_fn = monotonic_fn
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
        """Request one prompt checkpoint without blocking the App loop."""

        reason = str(reason_code or "").strip().lower()
        if self._closed or _SAFE_REASON_RE.fullmatch(reason) is None:
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

    def acquire_once(self, reason_code: str = "test_checkpoint") -> bool:
        """Synchronous test seam using the same one-read/many-consumer path."""

        reason = str(reason_code or "").strip().lower()
        if _SAFE_REASON_RE.fullmatch(reason) is None:
            raise ValueError("invalid passive acquisition reason")
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
        deadline = self._monotonic_fn() + max(0.0, float(timeout))
        while self._monotonic_fn() < deadline:
            if self._commands.unfinished_tasks == 0:
                return True
            time.sleep(0.005)
        return self._commands.unfinished_tasks == 0

    def _worker(self) -> None:
        next_periodic = self._monotonic_fn() + self._interval_seconds
        while not self._closed:
            timeout = max(0.0, next_periodic - self._monotonic_fn())
            try:
                reason = self._commands.get(timeout=timeout)
            except queue.Empty:
                self._acquire_and_fan_out("periodic_interval")
                next_periodic = self._monotonic_fn() + self._interval_seconds
                continue
            try:
                if reason is None:
                    return
                with self._pending_lock:
                    self._pending_reasons.discard(reason)
                self._acquire_and_fan_out(reason)
                next_periodic = self._monotonic_fn() + self._interval_seconds
            finally:
                self._commands.task_done()

    def _acquire_and_fan_out(self, reason: str) -> bool:
        if self._closed:
            return False
        try:
            context = self._context_fn()
        except Exception:
            return False
        if not isinstance(context, PerkSaveMonitorContext) or not context.valid():
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
            not isinstance(current_context, PerkSaveMonitorContext)
            or current_context.runtime_session_id != context.runtime_session_id
            or current_context.activity_scope_id != context.activity_scope_id
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
