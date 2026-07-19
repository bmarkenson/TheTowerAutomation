# handlers/ad_gem_handler.py

import threading
import time
from core.tap_dispatcher import tap
from core.clickmap_access import get_click
from core.input import safe_tap
from core.label_tapper import is_visible
from core.run_state import AUTOMATION, RunState
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

    start_ts = time.time()
    taps = 0
    log(f"Floating gem tapping initiated (duration={duration}s, interval={interval}s)", "ACTION")

    end_time = time.time() + duration
    next_tap_time = time.time()
    pause_started = None
    pause_logged = False
    try:
        while not stop_event.is_set():
            now = time.time()
            if now >= end_time:
                break

            if AUTOMATION.state != RunState.RUNNING:
                if not pause_logged:
                    log("[AD_GEM] Blind tapper paused (automation not RUNNING)", "INFO")
                    pause_logged = True
                if pause_started is None:
                    pause_started = now
                time.sleep(0.25)
                continue

            if pause_started is not None:
                paused_duration = time.time() - pause_started
                end_time += paused_duration
                next_tap_time += paused_duration
                pause_started = None
            if pause_logged:
                log("[AD_GEM] Blind tapper resumed", "INFO")
                pause_logged = False

            if now < next_tap_time:
                time.sleep(min(0.05, max(0.0, next_tap_time - now)))
                continue

            try:
                # Quiet path: suppress per-tap logging at the dispatcher
                tap(x, y, label=label, log_it=False)
                taps += 1
            except Exception as e:
                log(f"[ERROR] Blind gem tapper tap() failed: {e!r}", "ERROR")
                break
            next_tap_time = time.time() + interval
    finally:
        elapsed = int(time.time() - start_ts)
        log(f"Floating gem tapping finished (taps={taps}, elapsed≈{elapsed}s)", "ACTION")
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


def is_blind_gem_tapper_active() -> bool:
    """Return True if the blind floating gem tapper is currently running."""
    return _blind_tapper_active.is_set()


def _collect_visible_ad_gem(
    label: str,
    *,
    allow_fallback_after_retry: bool,
) -> bool:
    """Tap one currently visible ad-gem control and verify its dismissal."""

    max_attempts = 3
    tapped_once = False
    for attempt in range(1, max_attempts + 1):
        # Always capture fresh evidence here.  A frame that scheduled the
        # handler must not remain tap authority after another action or user
        # input could have changed the screen.
        if not is_visible(label):
            if not tapped_once:
                log(f"[AD_GEM] {label} not visible; skipping tap", "INFO")
            return tapped_once

        tapped = safe_tap(
            label,
            require_visible=True,
            retries=1,
            retry_delay=0.4,
            dispatch="now",
            allow_fallback=(allow_fallback_after_retry and attempt > 1),
        )
        if not tapped:
            log(f"[AD_GEM] Failed to tap {label} (match missing)", "WARN")
            return False
        tapped_once = True

        time.sleep(0.5)
        if not is_visible(label):
            return True

        log(f"[AD_GEM] {label} still visible after tap — retrying", "WARN")
        time.sleep(0.4)

    log(f"[AD_GEM] {label} persisted after multiple tap attempts", "ERROR")
    return False


def handle_home_ad_gem() -> bool:
    """Collect the visible five-gem control from an actionable Home screen."""

    log("Handling HOME_AD_GEMS_AVAILABLE overlay", "ACTION")
    # Home cannot host the in-battle floating gem.  Ensure a prior bounded
    # tapper is winding down and never start a new one from this path.
    stop_blind_gem_tapper()
    return _collect_visible_ad_gem(
        "buttons.claim_ad_gem:home",
        allow_fallback_after_retry=False,
    )


def handle_ad_gem() -> bool:
    """
    Handle the 'AD_GEMS_AVAILABLE' overlay event.

    Workflow:
      1. Start a blind floating gem tapper (background) if one is not already running.
      2. Tap the ad gem overlay to collect it.
      3. Wait 1 second before returning.

    Returns:
        bool: True when the overlay was tapped and then disappeared.

    Side effects:
        [tap] Sends tap events to the device.
        [log] Emits action/info logs.
        [loop] Starts background tapping thread.

    Notes:
        - Blind tapper runs for 20s with 1s interval.
        - Uses non-reentrant guard to prevent multiple simultaneous tappers.
        - Home uses ``handle_home_ad_gem`` and never starts this tapper.
    """
    log("Handling AD_GEMS_AVAILABLE overlay", "ACTION")
    start_blind_gem_tapper(duration=20, interval=1, blocking=False)

    collected = _collect_visible_ad_gem(
        "overlays.ad_gem",
        allow_fallback_after_retry=True,
    )
    time.sleep(1)
    return collected
