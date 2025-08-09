# handlers/ad_gem_handler.py

import threading
import time
from core.tap_dispatcher import tap
from core.clickmap_access import get_click
from core.label_tapper import tap_label_now
from utils.logger import log

_blind_tapper_active = threading.Event()
_blind_tapper_stop = threading.Event()  # cooperative cancel


def _blind_floating_gem_tapper(duration=20, interval=1, stop_event=None):
    """
    Blindly tap in the floating gem region for a specified duration.

    Args:
        duration (int | float, optional):
            Number of seconds to continue tapping. Default is 20.
        interval (int | float, optional):
            Delay between taps in seconds. Default is 1.
        stop_event (threading.Event | None, optional):
            When set, exits early.

    Returns:
        None

    Side effects:
        [tap] Sends tap events to the device.
        [log] Emits warnings and action logs.
        [loop] Runs until duration expires or interrupted.

    Notes:
        - Clamps interval to 0.1s minimum if <= 0.
        - Exits early if no floating gem tap location is defined.
        - Always clears the `_blind_tapper_active` flag on exit.
    """
    if stop_event is None:
        stop_event = _blind_tapper_stop

    if duration <= 0:
        log("[WARN] Blind floating gem tapper called with non-positive duration; skipping", "WARN")
        _blind_tapper_active.clear()
        return
    if interval <= 0:
        log("[WARN] Interval <= 0; clamping to 0.1s", "WARN")
        interval = 0.1

    coords = get_click("gesture_targets.floating_gem_blind_tap")
    if not coords:
        log("[WARN] No blind tap location defined for floating gem", "WARN")
        _blind_tapper_active.clear()
        return

    x, y = coords
    label = "floating_gem_blind_tap"

    end_time = time.time() + duration
    try:
        while time.time() < end_time and not stop_event.is_set():
            try:
                tap(x, y, label=label)
            except Exception as e:
                log(f"[ERROR] Blind gem tapper tap() failed: {e!r}", "ERROR")
                break
            # Sleep in small chunks to respond quickly to stop_event
            target = time.time() + interval
            while not stop_event.is_set() and time.time() < target:
                time.sleep(min(0.05, target - time.time()))
    finally:
        _blind_tapper_active.clear()
        stop_event.clear()


def start_blind_gem_tapper(duration=20, interval=1, blocking=False):
    """
    Start the blind floating gem tapper for a given duration and interval.

    Args:
        duration (int | float, optional):
            Number of seconds to run. Must be > 0. Default is 20.
        interval (int | float, optional):
            Delay between taps in seconds. Must be > 0. Default is 1.
        blocking (bool, optional):
            If True, runs in the current thread until complete.
            If False (default), runs in a background thread and returns immediately.

    Returns:
        None

    Side effects:
        [tap] Sends repeated tap events to the device.
        [log] Emits structured logs.
        [loop] May run until duration expires.

    Notes:
        - Non-reentrant: will not start if another instance is active.
        - The active state is tracked via `_blind_tapper_active`.
    """
    if duration <= 0:
        log("[WARN] duration must be > 0; aborting request", "WARN")
        return
    if interval <= 0:
        log("[WARN] interval must be > 0; aborting request", "WARN")
        return

    if _blind_tapper_active.is_set():
        log("[INFO] Blind tapper already active; not starting another", "INFO")
        return

    coords = get_click("gesture_targets.floating_gem_blind_tap")
    if not coords:
        log("[WARN] No blind tap location defined for floating gem; not starting", "WARN")
        return

    _blind_tapper_stop.clear()
    _blind_tapper_active.set()

    if blocking:
        log(f"[ACTION] Starting blind gem tapper (blocking) for {duration}s @ {interval}s", "ACTION")
        try:
            _blind_floating_gem_tapper(duration=duration, interval=interval, stop_event=_blind_tapper_stop)
        finally:
            pass
    else:
        log(f"[ACTION] Starting blind gem tapper (background) for {duration}s @ {interval}s", "ACTION")
        threading.Thread(
            target=_blind_floating_gem_tapper,
            kwargs={"duration": duration, "interval": interval, "stop_event": _blind_tapper_stop},
            daemon=False  # keep alive inside the process
        ).start()


def stop_blind_gem_tapper():
    """
    Request the running blind tapper to stop early (cooperative cancel).
    Returns True if a running tapper was signaled, else False.
    """
    if _blind_tapper_active.is_set():
        _blind_tapper_stop.set()
        return True
    return False


def handle_ad_gem():
    """
    Handle the 'AD_GEMS_AVAILABLE' overlay event.

    Workflow:
      1. Start a blind floating gem tapper (background) if one is not already running.
      2. Tap the ad gem overlay to collect it.
      3. Wait 1 second before returning.

    Returns:
        None

    Side effects:
        [tap] Sends tap events to the device.
        [log] Emits action/info logs.
        [loop] Starts background tapping thread.

    Notes:
        - Blind tapper runs for 20s with 1s interval.
        - Uses non-reentrant guard to prevent multiple simultaneous tappers.
    """
    log("Handling AD_GEMS_AVAILABLE overlay", "ACTION")
    start_blind_gem_tapper(duration=20, interval=1, blocking=False)
    tap_label_now("overlays.ad_gem")
    time.sleep(1)
