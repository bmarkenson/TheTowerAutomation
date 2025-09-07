# core/automation_state.py
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
  initial_mode: RETRY
"""

import threading
from enum import Enum
from typing import Union, Final


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
      members: [RETRY, WAIT, HOME]
      notes:
        - WAIT: pause on GAME OVER and similar screens until operator flips mode.
        - HOME: navigate/idle on home screen (not all handlers implement this yet).
        - RETRY: default auto-progression behavior.
    """
    RETRY = "RETRY"
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
        r: New controller with state=RUNNING, mode=RETRY
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
        self._state: RunState = RunState.RUNNING
        self._mode: ExecMode = ExecMode.RETRY

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
        with self._lock:
            self._state = value

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
