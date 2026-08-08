# handlers/ad_gem_handler.py

import threading
import time
from typing import Callable, Optional
from core.tap_dispatcher import tap_now
from core.clickmap_access import get_click
from core.input import safe_tap
from core.label_tapper import is_visible
from core.run_state import AUTOMATION, RunState
from utils.logger import log, log_action_intent, log_result

_blind_tapper_active = threading.Event()
_blind_tapper_stop = threading.Event()  # cooperative cancel


ActionGuard = Optional[Callable[[], bool]]


def _action_allowed(action_guard_fn: ActionGuard) -> bool:
    if action_guard_fn is None:
        return AUTOMATION.state == RunState.RUNNING
    try:
        return bool(action_guard_fn())
    except Exception as exc:
        log(f"[AD_GEM] Auxiliary authority check failed: {exc}", "ERROR")
        return False


def _blind_floating_gem_tapper(
    duration=20,
    interval=1,
    stop_event=None,
    action_guard_fn: ActionGuard = None,
):
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
        [log] Emits workflow, input, and diagnostic logs.
        [loop] Runs until duration expires or interrupted.

    Notes:
        - Clamps interval to 0.1s minimum if <= 0.
        - Exits early if no floating gem tap location is defined.
        - Always clears the `_blind_tapper_active` flag on exit.
    """
    if stop_event is None:
        stop_event = _blind_tapper_stop

    if duration <= 0:
        log(
            "Blind floating gem tapper called with non-positive duration; skipping",
            "WARN",
        )
        _blind_tapper_active.clear()
        return
    if interval <= 0:
        log("Blind floating gem interval <= 0; clamping to 0.1s", "WARN")
        interval = 0.1

    coords = get_click("gesture_targets.floating_gem_blind_tap")
    if not coords:
        log("No blind tap location defined for floating gem", "WARN")
        _blind_tapper_active.clear()
        return

    x, y = coords
    label = "floating_gem_blind_tap"

    log_action_intent(
        "Scanning for floating gems",
        reason="an in-battle ad-gem overlay can coincide with a moving gem",
        detail=f"[AD_GEM] duration_s={duration} interval_s={interval}",
    )
    start_ts = time.time()
    taps = 0
    failure_reason = None
    log(
        f"[AD_GEM] Floating-gem scan started "
        f"(duration={duration}s, interval={interval}s)",
        "DEBUG",
    )

    end_time = time.time() + duration
    next_tap_time = time.time()
    authority_lost = False
    try:
        while not stop_event.is_set():
            now = time.time()
            if now >= end_time:
                break

            if now < next_tap_time:
                time.sleep(min(0.05, max(0.0, next_tap_time - now)))
                continue

            # This is intentionally the final check before every blind input.
            # A guard accepted when the worker started is never reusable tap
            # authority after Pause, route ownership, gate replacement, or a
            # battle/screen transition.
            if not _action_allowed(action_guard_fn):
                authority_lost = True
                log(
                    "[AD_GEM] Floating-gem scan stopped because auxiliary "
                    "authority was lost",
                    "DEBUG",
                )
                break
            try:
                if not tap_now(x, y, label=label, log_it=True):
                    failure_reason = "tap dispatch failed"
                    break
                taps += 1
            except Exception as e:
                failure_reason = repr(e)
                log(f"Blind gem tapper tap_now() failed: {e!r}", "ERROR")
                break
            # Keep the sweep on its wall-clock cadence.  The mandatory
            # pre-input authority check must not add its own latency to every
            # interval and gradually drift behind the rotating gem.
            next_tap_time = max(
                next_tap_time + interval,
                time.time() + 0.05,
            )
    except Exception as exc:
        failure_reason = repr(exc)
        log(f"Blind gem tapper failed: {exc!r}", "ERROR")
    finally:
        elapsed = int(time.time() - start_ts)
        was_stopped = stop_event.is_set()
        tap_word = "tap" if taps == 1 else "taps"
        if failure_reason is not None:
            result_summary = (
                "Floating-gem scan failed — input dispatch stopped unexpectedly"
            )
            disposition = "failed"
        elif was_stopped or authority_lost:
            result_summary = (
                f"Floating-gem scan interrupted — dispatched {taps} {tap_word}"
            )
            disposition = "interrupted"
        else:
            result_summary = (
                f"Floating-gem scan complete — dispatched {taps} {tap_word}"
            )
            disposition = "completed"
        log_result(
            result_summary,
            detail=(
                f"[AD_GEM] result={disposition} taps={taps} elapsed_s={elapsed} "
                f"stop_requested={was_stopped} "
                + ("authority_lost=True " if authority_lost else "")
                + f"failure={failure_reason}"
            ),
        )
        _blind_tapper_active.clear()
        stop_event.clear()


def start_blind_gem_tapper(
    duration=20,
    interval=1,
    blocking=False,
    *,
    action_guard_fn: ActionGuard = None,
):
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
        - Each tap dispatch is synchronous inside this already-backgrounded
          worker, so active state covers the complete input operation.
    """
    if duration <= 0:
        log("Blind floating gem duration must be > 0; request ignored", "WARN")
        return
    if interval <= 0:
        log("Blind floating gem interval must be > 0; request ignored", "WARN")
        return

    if _blind_tapper_active.is_set():
        log("[AD_GEM] Blind tapper already active; not starting another", "DEBUG")
        return

    if not _action_allowed(action_guard_fn):
        log(
            "[AD_GEM] Floating-gem scan not started because auxiliary "
            "authority is unavailable",
            "DEBUG",
        )
        return

    coords = get_click("gesture_targets.floating_gem_blind_tap")
    if not coords:
        log("No blind tap location defined for floating gem; scan not started", "WARN")
        return

    _blind_tapper_stop.clear()
    _blind_tapper_active.set()

    if blocking:
        _blind_floating_gem_tapper(
            duration=duration,
            interval=interval,
            stop_event=_blind_tapper_stop,
            action_guard_fn=action_guard_fn,
        )
    else:
        worker = threading.Thread(
            target=_blind_floating_gem_tapper,
            kwargs={
                "duration": duration,
                "interval": interval,
                "stop_event": _blind_tapper_stop,
                "action_guard_fn": action_guard_fn,
            },
            daemon=False,  # keep alive inside the process
        )
        try:
            worker.start()
        except Exception as exc:
            _blind_tapper_active.clear()
            log(f"Could not start blind gem tapper: {exc!r}", "ERROR")


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
    action_guard_fn: ActionGuard = None,
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
                log(f"[AD_GEM] {label} not visible; skipping tap", "DEBUG")
            return tapped_once

        # The fresh match above proves the screen precondition; recheck the
        # collector's authority immediately before the input itself.
        if not _action_allowed(action_guard_fn):
            log(
                f"[AD_GEM] Refusing {label}: auxiliary authority was lost",
                "DEBUG",
            )
            return False
        guard_kwargs = (
            {"action_guard_fn": action_guard_fn}
            if action_guard_fn is not None
            else {}
        )
        tapped = safe_tap(
            label,
            retries=1,
            retry_delay=0.4,
            dispatch="now",
            **guard_kwargs,
        )
        if not tapped:
            log(f"[AD_GEM] Failed to tap {label} (match missing)", "DEBUG")
            return False
        tapped_once = True

        time.sleep(0.5)
        if not is_visible(label):
            return True

        log(f"[AD_GEM] {label} still visible after tap — retrying", "DEBUG")
        time.sleep(0.4)

    log(f"[AD_GEM] {label} persisted after multiple tap attempts", "ERROR")
    return False


def handle_home_ad_gem(*, action_guard_fn: ActionGuard = None) -> bool:
    """Collect the visible five-gem control from an actionable Home screen."""

    label = "buttons.claim_ad_gem:home"
    log_action_intent(
        "Collecting the Home ad gem",
        reason="the Home overlay indicates that a five-gem reward is available",
        detail=f"[AD_GEM] source=home label={label}",
    )
    # Home cannot host the in-battle floating gem.  Ensure a prior bounded
    # tapper is winding down and never start a new one from this path.
    stop_blind_gem_tapper()
    collected = _collect_visible_ad_gem(
        label,
        action_guard_fn=action_guard_fn,
    )
    log_result(
        (
            "Home ad-gem collection complete — reward collected"
            if collected
            else "Home ad-gem collection complete — no reward was collected"
        ),
        detail=(
            f"[AD_GEM] result={'collected' if collected else 'no_op'} "
            f"source=home label={label}"
        ),
    )
    return collected


def handle_ad_gem(
    *,
    action_guard_fn: ActionGuard = None,
    floating_action_guard_fn: ActionGuard = None,
) -> bool:
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
    label = "overlays.ad_gem"
    log_action_intent(
        "Collecting the in-battle ad gem",
        reason="the current battle frame indicates that an ad-gem reward is available",
        detail=f"[AD_GEM] source=battle label={label}",
    )
    floating_guard = floating_action_guard_fn or action_guard_fn
    if floating_guard is None:
        start_blind_gem_tapper(duration=20, interval=1, blocking=False)
    else:
        start_blind_gem_tapper(
            duration=20,
            interval=1,
            blocking=False,
            action_guard_fn=floating_guard,
        )

    if action_guard_fn is None:
        collected = _collect_visible_ad_gem(label)
    else:
        collected = _collect_visible_ad_gem(
            label,
            action_guard_fn=action_guard_fn,
        )
    time.sleep(1)
    log_result(
        (
            "In-battle ad-gem collection complete — reward collected"
            if collected
            else "In-battle ad-gem collection complete — no reward was collected"
        ),
        detail=(
            f"[AD_GEM] result={'collected' if collected else 'no_op'} "
            f"source=battle label={label}"
        ),
    )
    return collected
