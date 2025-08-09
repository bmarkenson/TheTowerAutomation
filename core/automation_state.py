import threading
from enum import Enum
from typing import Union, Final

class RunState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"

class ExecMode(str, Enum):
    RETRY = "RETRY"
    WAIT = "WAIT"
    HOME = "HOME"

_StateLike = Union[RunState, str]
_ModeLike = Union[ExecMode, str]

class AutomationControl:
    """Thread-safe holder for the automation's run state and execution mode."""
    def __init__(self) -> None:
        self._lock: Final = threading.Lock()
        self._state: RunState = RunState.RUNNING
        self._mode: ExecMode = ExecMode.RETRY

    @property
    def state(self) -> RunState:
        with self._lock:
            return self._state

    @state.setter
    def state(self, value: _StateLike) -> None:
        # Accept Enum or str and coerce; raise on invalid
        if isinstance(value, str):
            value = RunState(value)  # may raise ValueError
        elif not isinstance(value, RunState):
            raise TypeError("state must be RunState or str")
        with self._lock:
            self._state = value

    @property
    def mode(self) -> ExecMode:
        with self._lock:
            return self._mode

    @mode.setter
    def mode(self, value: _ModeLike) -> None:
        if isinstance(value, str):
            value = ExecMode(value)  # may raise ValueError
        elif not isinstance(value, ExecMode):
            raise TypeError("mode must be ExecMode or str")
        with self._lock:
            self._mode = value

AUTOMATION = AutomationControl()
