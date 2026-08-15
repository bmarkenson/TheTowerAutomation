# core/run_state.py
"""
Thread-safe automation run/mode state.

This module exposes a small, concurrency-safe controller for the current
run-state and execution mode used by handlers/loops.

YAML-in-docstring legend (kept tiny and consistent per module)

spec_legend:
  r: Return value (shape & invariants)
  s: Side effects (project tags; state/log/fs/adb/etc.)
  e: Errors/exceptions behavior
  p: Parameters (only non-obvious notes; types are in signature)
  notes: Brief extra context that aids correct use

defaults:
  thread_safety: property access is guarded by a threading.Lock
  initial_state: RUNNING
  initial_mode: NEXT_BATTLE
"""

from contextlib import contextmanager
import threading
from enum import Enum
from typing import Callable, Final, Iterator, Optional, Union

from core.dispatch_control_boundary import (
    DispatchControlBoundaryError,
    dispatch_control_boundary,
)


class RunState(str, Enum):
    """
    spec:
      name: RunState
      kind: Enum[str]
      members: [RUNNING, PAUSED, STOPPED, UNKNOWN]
      r: Concrete values are strings; equality by identity/value.
      notes:
        - Used by loops/handlers to gate activity.
    """
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


class ExecMode(str, Enum):
    """
    spec:
      name: ExecMode
      kind: Enum[str]
      members: [NEXT_BATTLE, WAIT, HOME]
      notes:
        - WAIT: pause on GAME OVER and similar screens until operator flips mode.
        - HOME: return to and stay on the home screen.
        - NEXT_BATTLE: start or resume at the next authorized opportunity.
    """
    NEXT_BATTLE = "NEXT_BATTLE"
    WAIT = "WAIT"
    HOME = "HOME"


_StateLike = Union[RunState, str]
_ModeLike = Union[ExecMode, str]


class AutomationControl:
    """
    spec:
      name: AutomationControl
      purpose: Thread-safe holder for the automation's run state and execution mode.
      constructor:
        signature: AutomationControl() -> AutomationControl
        r: New controller with state=RUNNING, mode=NEXT_BATTLE
        s: [state]
      attributes:
        _lock: threading.Lock (private)
        _state: RunState
        _mode: ExecMode
      notes:
        - Property setters accept Enum or str; str is coerced to the Enum and may raise.
        - Access to _state/_mode is always guarded by _lock to avoid races.
    """

    def __init__(self) -> None:
        self._lock: Final = threading.Lock()
        self._mutation_lock: Final = threading.RLock()
        self._mutation_local: Final = threading.local()
        self._state: RunState = RunState.RUNNING
        self._mode: ExecMode = ExecMode.NEXT_BATTLE
        self._mutation_guard: Optional[Callable[[], bool]] = None
        self._uncertain_mutation_handler: Optional[Callable[[str], None]] = None
        self._guard_failure_handler: Optional[Callable[[str], None]] = None
        self._mutation_guard_token: Optional[object] = None
        self._dispatch_control_lock_path: Optional[str] = None
        self._mutations_shutdown = False
        self._mutation_guard_failed = False

    @property
    def state(self) -> RunState:
        """
        spec:
          name: AutomationControl.state (getter)
          signature: state -> RunState
          r: Current run state (Enum)
          s: [state]
          e: none
        """
        with self._lock:
            return self._state

    @state.setter
    def state(self, value: _StateLike) -> None:
        """
        spec:
          name: AutomationControl.state (setter)
          signature: state = value
          p:
            value: RunState | str  # str is coerced via RunState(value)
          r: null
          s: [state]
          e:
            - ValueError: if value is a str not in RunState
            - TypeError: if value is neither RunState nor str
        """
        # Accept Enum or str and coerce; raise on invalid
        if isinstance(value, str):
            value = RunState(value)  # may raise ValueError
        elif not isinstance(value, RunState):
            raise TypeError("state must be RunState or str")
        # State transitions and device mutations share one boundary.  A Pause
        # or Stop therefore waits for an already-dispatched atomic mutation to
        # finish, then prevents every later mutation before its acknowledgement
        # can be published.
        with self._mutation_lock:
            with self._lock:
                self._state = value

    def install_mutation_guard(
        self,
        guard: Callable[[], bool],
        *,
        uncertain_result_handler: Optional[Callable[[str], None]] = None,
        guard_failure_handler: Optional[Callable[[str], None]] = None,
        dispatch_control_lock_path: Optional[str] = None,
    ) -> object:
        """Install the live runtime's final persistent-control refresh.

        Helpers and explicit development tools do not install this callback.
        The production App installs it for the lifetime of ``run()`` so every
        ADB mutation consumes fresh Pause/Stop intent at its final boundary.
        """

        if not callable(guard):
            raise TypeError("mutation guard must be callable")
        if (
            uncertain_result_handler is not None
            and not callable(uncertain_result_handler)
        ):
            raise TypeError("uncertain mutation handler must be callable")
        if guard_failure_handler is not None and not callable(guard_failure_handler):
            raise TypeError("mutation guard failure handler must be callable")
        token = object()
        with self._mutation_lock:
            if self._mutation_guard is not None:
                raise RuntimeError("a runtime mutation guard is already installed")
            self._mutation_guard = guard
            self._uncertain_mutation_handler = uncertain_result_handler
            self._guard_failure_handler = guard_failure_handler
            self._mutation_guard_token = token
            self._dispatch_control_lock_path = (
                str(dispatch_control_lock_path)
                if dispatch_control_lock_path is not None
                else None
            )
            self._mutations_shutdown = False
            self._mutation_guard_failed = False
        return token

    def shutdown_mutations(self, token: object) -> bool:
        """Atomically deny all later mutations for one runtime owner."""

        with self._mutation_lock:
            if token is None or token is not self._mutation_guard_token:
                return False
            self._mutations_shutdown = True
            with self._lock:
                self._state = RunState.STOPPED
            return True

    def clear_mutation_guard(self, token: object) -> bool:
        """Remove only the guard installed by the matching runtime owner."""

        with self._mutation_lock:
            if token is None or token is not self._mutation_guard_token:
                return False
            self._mutation_guard = None
            self._uncertain_mutation_handler = None
            self._guard_failure_handler = None
            self._mutation_guard_token = None
            self._dispatch_control_lock_path = None
            self._mutation_guard_failed = False
            return True

    def report_uncertain_mutation(self, reason: str) -> None:
        """Fail closed after a dispatched mutation has an unknown outcome."""

        with self._mutation_lock:
            handler = self._uncertain_mutation_handler
            runtime_installed = self._mutation_guard is not None
            if runtime_installed:
                # A timeout after subprocess dispatch is catastrophic even if
                # durable persistence or reporting subsequently fails.
                with self._lock:
                    self._state = RunState.PAUSED
            if handler is None:
                return
            try:
                handler(str(reason or "device mutation result was uncertain"))
            except Exception:
                # Local Pause above remains authoritative for this process.
                return

    def _mutation_guards_allow(
        self,
        action_guard: Optional[Callable[[], bool]],
    ) -> bool:
        """Evaluate runtime and workflow guards with fail-closed reporting."""

        scoped_guards = tuple(
            guard
            for guard in getattr(
                self._mutation_local,
                "scoped_action_guards",
                (),
            )
            if guard is not None
        )
        guards_list: list[tuple[Callable[[], bool], bool]] = []
        seen: set[int] = set()
        for guard in (self._mutation_guard, *scoped_guards, action_guard):
            if guard is None or id(guard) in seen:
                continue
            seen.add(id(guard))
            guards_list.append((guard, guard is self._mutation_guard))
        guards = tuple(guards_list)
        allowed = not self._mutations_shutdown
        for guard, runtime_owned in guards:
            if not allowed:
                break
            try:
                if not bool(guard()):
                    allowed = False
                    break
                if runtime_owned:
                    self._mutation_guard_failed = False
            except Exception as exc:
                allowed = False
                if runtime_owned:
                    with self._lock:
                        self._state = RunState.PAUSED
                    if not self._mutation_guard_failed:
                        self._mutation_guard_failed = True
                        failure_handler = self._guard_failure_handler
                        if failure_handler is not None:
                            try:
                                failure_handler(
                                    "final mutation authority refresh failed: "
                                    f"{exc}"
                                )
                            except Exception:
                                pass
        if guards:
            with self._lock:
                allowed = allowed and self._state is RunState.RUNNING
        return allowed

    @contextmanager
    def action_guard_scope(
        self,
        action_guard: Optional[Callable[[], bool]],
    ) -> Iterator[None]:
        """Bind a final-mutation guard to every input in the current route.

        This scope does not hold the mutation or cross-process dispatch lock.
        Each low-level input still opens its own short transaction and rechecks
        all scoped guards immediately before dispatch.
        """

        previous = tuple(
            getattr(self._mutation_local, "scoped_action_guards", ())
        )
        if action_guard is not None:
            self._mutation_local.scoped_action_guards = previous + (
                action_guard,
            )
        try:
            yield
        finally:
            self._mutation_local.scoped_action_guards = previous

    def _acquire_transaction_dispatch_boundary(self) -> bool:
        """Acquire the shared boundary at the transaction's first input."""

        if bool(
            getattr(self._mutation_local, "dispatch_boundary_acquired", False)
        ):
            return True
        lock_path = self._dispatch_control_lock_path
        if lock_path is None:
            self._mutation_local.dispatch_boundary_acquired = True
            return True
        boundary = dispatch_control_boundary(lock_path)
        try:
            boundary.__enter__()
        except DispatchControlBoundaryError as exc:
            with self._lock:
                self._state = RunState.PAUSED
            failure_handler = self._guard_failure_handler
            if failure_handler is not None:
                try:
                    failure_handler(
                        "final mutation dispatch/control lock failed: "
                        f"{exc}"
                    )
                except Exception:
                    pass
            return False
        self._mutation_local.dispatch_boundary = boundary
        self._mutation_local.dispatch_boundary_acquired = True
        return True

    def _release_transaction_dispatch_boundary(self) -> None:
        boundary = getattr(self._mutation_local, "dispatch_boundary", None)
        self._mutation_local.dispatch_boundary = None
        self._mutation_local.dispatch_boundary_acquired = False
        if boundary is None:
            return
        try:
            boundary.__exit__(None, None, None)
        except DispatchControlBoundaryError:
            # The command may already have run and cross-process ordering can
            # no longer be proven.
            self.report_uncertain_mutation(
                "dispatch/control boundary release failed after device mutation"
            )

    def refresh_mutation_authority(
        self,
        action_guard: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Re-read authority immediately before first input in a transaction.

        Long lifecycle operations may do passive validation after entering the
        global mutation boundary.  A Pause can be persisted during those
        checks, so the owning thread must refresh durable authority once more
        immediately before its first device mutation.  Required restoration
        after that first attempt remains part of the same atomic transaction.
        """

        with self._mutation_lock:
            if int(getattr(self._mutation_local, "depth", 0)) < 1:
                raise RuntimeError(
                    "mutation authority can be refreshed only inside an "
                    "authorized mutation transaction"
                )
            if not self._acquire_transaction_dispatch_boundary():
                return False
            effective_action_guard = action_guard or getattr(
                self._mutation_local,
                "outer_action_guard",
                None,
            )
            return self._mutation_guards_allow(effective_action_guard)

    @contextmanager
    def authorize_mutation(
        self,
        action_guard: Optional[Callable[[], bool]] = None,
        *,
        defer_dispatch_boundary: bool = False,
    ) -> Iterator[bool]:
        """Hold the global dispatch boundary around one device mutation.

        The installed runtime guard synchronizes durable control intent.  An
        optional workflow guard may add narrower ownership checks, as the
        watchdog does.  Either failure denies the mutation.  With no installed
        runtime and no workflow guard, low-level helpers retain their existing
        tooling/test behavior.
        """

        with self._mutation_lock:
            if self._mutations_shutdown:
                yield False
                return
            depth = int(getattr(self._mutation_local, "depth", 0))
            if depth > 0:
                # One authorized lifecycle workflow may contain several raw
                # ADB mutations.  Treat it as a single atomic boundary so a
                # Pause cannot strand the game after force-stop/backgrounding
                # but before the required restoration input.
                if (
                    bool(
                        getattr(
                            self._mutation_local,
                            "defer_dispatch_boundary",
                            False,
                        )
                    )
                    and not bool(
                        getattr(
                            self._mutation_local,
                            "dispatch_boundary_acquired",
                            False,
                        )
                    )
                ):
                    if not self._acquire_transaction_dispatch_boundary():
                        yield False
                        return
                    if not self._mutation_guards_allow(
                        getattr(
                            self._mutation_local,
                            "outer_action_guard",
                            None,
                        )
                    ):
                        yield False
                        return
                self._mutation_local.depth = depth + 1
                try:
                    yield True
                finally:
                    self._mutation_local.depth = depth
                return
            self._mutation_local.depth = 1
            self._mutation_local.outer_action_guard = action_guard
            self._mutation_local.defer_dispatch_boundary = bool(
                defer_dispatch_boundary
            )
            self._mutation_local.dispatch_boundary = None
            self._mutation_local.dispatch_boundary_acquired = False
            try:
                if (
                    not defer_dispatch_boundary
                    and not self._acquire_transaction_dispatch_boundary()
                ):
                    yield False
                    return
                allowed = self._mutation_guards_allow(action_guard)
                if not allowed:
                    yield False
                    return
                yield True
            finally:
                self._release_transaction_dispatch_boundary()
                self._mutation_local.depth = 0
                self._mutation_local.outer_action_guard = None
                self._mutation_local.defer_dispatch_boundary = False

    @contextmanager
    def quiescence_boundary(self) -> Iterator[None]:
        """Exclude every device mutation for one control/ownership change."""

        with self._mutation_lock:
            yield

    def _reset_for_testing(self) -> None:
        """Restore the process singleton between isolated pytest examples."""

        with self._mutation_lock:
            self._mutation_guard = None
            self._uncertain_mutation_handler = None
            self._guard_failure_handler = None
            self._mutation_guard_token = None
            self._dispatch_control_lock_path = None
            self._mutations_shutdown = False
            self._mutation_guard_failed = False
            self._mutation_local.depth = 0
            self._mutation_local.outer_action_guard = None
            self._mutation_local.defer_dispatch_boundary = False
            self._mutation_local.dispatch_boundary = None
            self._mutation_local.dispatch_boundary_acquired = False
            self._mutation_local.scoped_action_guards = ()
            with self._lock:
                self._state = RunState.RUNNING
                self._mode = ExecMode.NEXT_BATTLE

    @property
    def mode(self) -> ExecMode:
        """
        spec:
          name: AutomationControl.mode (getter)
          signature: mode -> ExecMode
          r: Current execution mode (Enum)
          s: [state]
          e: none
        """
        with self._lock:
            return self._mode

    @mode.setter
    def mode(self, value: _ModeLike) -> None:
        """
        spec:
          name: AutomationControl.mode (setter)
          signature: mode = value
          p:
            value: ExecMode | str  # str is coerced via ExecMode(value)
          r: null
          s: [state]
          e:
            - ValueError: if value is a str not in ExecMode
            - TypeError: if value is neither ExecMode nor str
        """
        if isinstance(value, str):
            value = ExecMode(value)  # may raise ValueError
        elif not isinstance(value, ExecMode):
            raise TypeError("mode must be ExecMode or str")
        with self._lock:
            self._mode = value


AUTOMATION = AutomationControl()
"""
spec:
  name: AUTOMATION
  kind: singleton
  r: Module-level AutomationControl instance for global coordination
  notes:
    - Handlers/loops read and set this.
    - Treat as process-local singleton; do not recreate per thread.
"""
