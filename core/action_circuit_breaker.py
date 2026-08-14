"""Process-local circuit breakers for repeatedly failing UI actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Optional


@dataclass(frozen=True)
class CircuitSnapshot:
    """Immutable state for one semantic action and observation epoch."""

    epoch: Hashable
    failures: int
    tripped: bool
    reason: Optional[str]


class ActionCircuitBreaker:
    """Stop cross-heartbeat retries until material evidence changes.

    Handlers may own a bounded local retry transaction.  This breaker owns the
    larger scheduling boundary: once that transaction fails, the same action
    is not started again from an equivalent observation epoch.  Time alone
    never resets authority.
    """

    def __init__(self, *, failure_limit: int = 1) -> None:
        if int(failure_limit) < 1:
            raise ValueError("failure_limit must be at least one")
        self._failure_limit = int(failure_limit)
        self._states: dict[str, CircuitSnapshot] = {}

    def allows(self, action: str, *, epoch: Hashable) -> bool:
        """Return whether ``action`` may start in this exact epoch."""

        key = str(action)
        state = self._states.get(key)
        if state is None or state.epoch != epoch:
            self._states[key] = CircuitSnapshot(epoch, 0, False, None)
            return True
        return not state.tripped

    def record_failure(
        self,
        action: str,
        *,
        epoch: Hashable,
        reason: str,
    ) -> bool:
        """Record one exhausted transaction; return true on the trip edge."""

        key = str(action)
        previous = self._states.get(key)
        failures = (
            previous.failures + 1
            if previous is not None and previous.epoch == epoch
            else 1
        )
        was_tripped = bool(
            previous is not None
            and previous.epoch == epoch
            and previous.tripped
        )
        tripped = failures >= self._failure_limit
        self._states[key] = CircuitSnapshot(
            epoch,
            failures,
            tripped,
            str(reason or "action transaction failed"),
        )
        return tripped and not was_tripped

    def record_success(
        self,
        action: str,
        *,
        epoch: Optional[Hashable] = None,
    ) -> bool:
        """Clear one action only when an optional scheduled epoch still owns it."""

        key = str(action)
        previous = self._states.get(key)
        if previous is None or (
            epoch is not None and previous.epoch != epoch
        ):
            return False
        self._states.pop(key, None)
        return bool(previous is not None and previous.tripped)

    def reset(self, action: str) -> bool:
        """Clear one action after its semantic precondition disappeared."""

        return self.record_success(action)

    def snapshot(self, action: str) -> Optional[CircuitSnapshot]:
        return self._states.get(str(action))
