# tap_dispatcher.py
"""
Queued tap injection via a single background worker.

spec_legend:
  r: Return value (shape & invariants)
  s: Side effects (project tags like [tap][log][thread])
  e: Errors/exceptions behavior
  p: Parameter notes beyond the signature
  notes: Usage guidance / invariants

defaults:
  queue_semantics: FIFO ordering preserved per process
  worker: A daemon thread is started on import and processes TAP_QUEUE
  tap_path: Uses core.adb_utils.input_tap to map canonical points to device pixels
  synchronous_path: tap_now executes in the caller's existing worker thread
  logging: Per-tap logging goes through utils.logger.log_input when log_it=True
"""

import threading
import queue
import time
import random
from typing import Callable, Optional
from utils.logger import log, log_input
from core.adb_utils import input_tap

TAP_QUEUE = queue.Queue()
"""
spec:
  name: TAP_QUEUE
  kind: queue.Queue
  r: In-process FIFO for (x:int, y:int, label:Optional[str], log_it:bool, action_guard_fn:Callable|None)
  notes:
    - Back-compat: the worker also accepts 3-tuples (x, y, label) and sets log_it=True.
"""


def log_tap(x, y, label):
    """
    spec:
      name: log_tap
      signature: log_tap(x:int, y:int, label: str|None) -> None
      r: null
      s: [log]
      notes:
        - Emits a concise INPUT line plus DEBUG coordinate detail.
    """
    log_input(
        f"Tap dispatched: {label or 'unlabeled target'}",
        detail=f"TAP {label or ''} at ({x},{y})",
    )


def _execute_tap(
    x,
    y,
    label,
    *,
    log_it: bool,
    action_guard_fn: Optional[Callable[[], bool]] = None,
) -> bool:
    """Dispatch one queued tap and report success only when ADB accepted it."""

    error = None
    try:
        if action_guard_fn is None:
            dispatched = input_tap(x, y) is not None
        else:
            dispatched = input_tap(
                x,
                y,
                action_guard_fn=action_guard_fn,
            ) is not None
    except Exception as exc:
        dispatched = False
        error = exc

    if not log_it:
        return dispatched
    if dispatched:
        log_tap(x, y, label)
        return True

    display_label = label or "unlabeled target"
    log(f"Queued tap failed: {display_label}", "WARN")
    detail = f"TAP failed label={label or ''} at ({x},{y})"
    if error is not None:
        detail += f" error={error!r}"
    log(detail, "DEBUG")
    return False


def tap_now(
    x,
    y,
    label=None,
    *,
    log_it: bool = True,
    action_guard_fn: Optional[Callable[[], bool]] = None,
) -> bool:
    """Dispatch one tap synchronously through the existing logging boundary."""

    return _execute_tap(
        x,
        y,
        label,
        log_it=log_it,
        action_guard_fn=action_guard_fn,
    )


def tap(
    x,
    y,
    label=None,
    *,
    log_it: bool = True,
    action_guard_fn: Optional[Callable[[], bool]] = None,
):
    """
    Public function for scripts to submit tap requests.

    spec:
      name: tap
      signature: tap(x:int, y:int, label:str|None=None, *, log_it:bool=True) -> None
      p:
        log_it: When False, the worker will perform the tap without calling log_tap.
      r: null
      s: [thread]
      e: none (puts into an unbounded Queue; may block briefly only under extreme memory pressure)
      notes:
        - Enqueues a 5-tuple (x, y, label, log_it, action_guard_fn) for the worker.
        - Callers should not assume immediate execution; it is asynchronous.
    """
    TAP_QUEUE.put((x, y, label, log_it, action_guard_fn))


def _tap_worker():
    """
    spec:
      name: _tap_worker
      kind: background-thread
      r: never returns (infinite loop)
      s: [tap][log]
      e:
        - queue.Empty is handled internally with a short idle wait.
        - Other exceptions from input_tap are not re-raised here (same-process resilience).
      notes:
        - Accepts 5-tuples plus legacy 3- and 4-tuple items from TAP_QUEUE.
    """
    last_keepalive = time.time()
    while True:
        now = time.time()
        try:
            item = TAP_QUEUE.get(timeout=1)
            # Backward compatibility: accept old 3- and 4-tuples.
            if isinstance(item, tuple) and len(item) == 3:
                x, y, label = item
                log_it = True
                action_guard_fn = None
            elif isinstance(item, tuple) and len(item) == 4:
                x, y, label, log_it = item
                action_guard_fn = None
            else:
                x, y, label, log_it, action_guard_fn = item
            _execute_tap(
                x,
                y,
                label,
                log_it=log_it,
                action_guard_fn=action_guard_fn,
            )
        except queue.Empty:
            pass  # nothing to do

# Start worker thread (on import)
threading.Thread(target=_tap_worker, daemon=True).start()


def main():
    """
    spec:
      name: main
      signature: main() -> None
      r: null
      s: [log][loop]
      e:
        - KeyboardInterrupt: prints a shutdown message and exits cleanly.
      notes:
        - Utility runner that keeps the process alive so the worker can service taps.
    """
    log("Tap dispatcher running. Press Ctrl+C to exit.", level="INFO")
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("Shutting down dispatcher.")


if __name__ == "__main__":
    main()
