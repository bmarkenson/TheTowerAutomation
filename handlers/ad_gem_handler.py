# handlers/ad_gem_handler.py

import threading
import time
from enum import Enum
from typing import Callable, Optional
from core.tap_dispatcher import tap_now
from core.clickmap_access import get_click
from core.input import TapDispatchOutcome, TapDispatchStatus, safe_tap
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.label_tapper import is_visible
from core.run_state import AUTOMATION, RunState
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log, log_action_intent, log_result

_blind_tapper_active = threading.Event()
_blind_tapper_stop = threading.Event()  # cooperative cancel


ActionGuard = Optional[Callable[[], bool]]


class AdGemCollectionStatus(str, Enum):
    """Typed result for one bounded visible-gem transaction."""

    COLLECTED = "collected"
    NO_TARGET = "no_target"
    INTERRUPTED = "interrupted"
    EXHAUSTED = "exhausted"
    UNCERTAIN = "uncertain"

    def __bool__(self) -> bool:
        return self is AdGemCollectionStatus.COLLECTED


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
                if not tap_now(
                    x,
                    y,
                    label=label,
                    log_it=True,
                    action_guard_fn=action_guard_fn,
                ):
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


def _fresh_target_observation(label: str):
    """Return complete target and source evidence, or no evidence."""

    frame = capture_adb_screenshot()
    if frame is None or not is_complete_screenshot(frame):
        return None, None, None
    try:
        detection = detect_state_and_overlays(frame)
        state = str(detection.get("state") or "UNKNOWN").upper()
        home_control = (
            detect_home_battle_control(frame).control.value
            if state == "HOME_SCREEN"
            else None
        )
        visible = is_visible(label, screenshot=frame)
    except Exception:
        return None, None, None
    return (
        frame,
        visible,
        (state, home_control),
    )


def _collect_visible_ad_gem(
    label: str,
    *,
    action_guard_fn: ActionGuard = None,
    required_state: Optional[str] = None,
) -> AdGemCollectionStatus:
    """Tap one currently visible ad-gem control and verify its dismissal."""

    max_attempts = 3
    tapped_once = False
    source_fingerprint = None
    for attempt in range(1, max_attempts + 1):
        # Always capture fresh evidence here.  A frame that scheduled the
        # handler must not remain tap authority after another action or user
        # input could have changed the screen.
        frame, visible, source = _fresh_target_observation(label)
        if visible is None:
            return (
                AdGemCollectionStatus.UNCERTAIN
                if tapped_once
                else AdGemCollectionStatus.INTERRUPTED
            )
        required = str(required_state or "").upper()
        source_valid = bool(
            isinstance(source, tuple)
            and (not required or source[0] == required)
            and not (
                required == "HOME_SCREEN"
                and HomeBattleControl.parse(source[1])
                is HomeBattleControl.UNKNOWN
            )
        )
        if not source_valid or (
            source_fingerprint is not None
            and source != source_fingerprint
        ):
            return (
                AdGemCollectionStatus.UNCERTAIN
                if tapped_once
                else AdGemCollectionStatus.INTERRUPTED
            )
        source_fingerprint = source
        if not visible:
            if not tapped_once:
                log(f"[AD_GEM] {label} not visible; skipping tap", "DEBUG")
                return AdGemCollectionStatus.NO_TARGET
            # A single template miss is not enough to prove that an accepted
            # input cleared the reward. Require a second complete frame.
            time.sleep(0.25)
            (
                confirmation_frame,
                confirmed_visible,
                confirmed_source,
            ) = (
                _fresh_target_observation(label)
            )
            if confirmed_visible is None:
                return AdGemCollectionStatus.UNCERTAIN
            if confirmed_source != source_fingerprint:
                return AdGemCollectionStatus.UNCERTAIN
            if not confirmed_visible:
                return AdGemCollectionStatus.COLLECTED
            frame = confirmation_frame
            visible = True

        # The fresh match above proves the screen precondition; recheck the
        # collector's authority immediately before the input itself.
        if not _action_allowed(action_guard_fn):
            log(
                f"[AD_GEM] Refusing {label}: auxiliary authority was lost",
                "DEBUG",
            )
            return AdGemCollectionStatus.INTERRUPTED
        guard_kwargs = (
            {"action_guard_fn": action_guard_fn}
            if action_guard_fn is not None
            else {}
        )
        raw_tap = safe_tap(
            label,
            retries=0,
            retry_delay=0.4,
            dispatch="now",
            screenshot=frame,
            return_dispatch_outcome=True,
            **guard_kwargs,
        )
        tap_outcome = (
            raw_tap
            if isinstance(raw_tap, TapDispatchOutcome)
            else TapDispatchOutcome(
                TapDispatchStatus.DISPATCHED
                if raw_tap
                else TapDispatchStatus.NOT_DISPATCHED
            )
        )
        if tap_outcome.uncertain:
            log(f"[AD_GEM] Dispatch result was uncertain for {label}", "ERROR")
            return AdGemCollectionStatus.UNCERTAIN
        if not tap_outcome.dispatched:
            log(f"[AD_GEM] Failed to tap {label} (match missing)", "DEBUG")
            if not _action_allowed(action_guard_fn):
                return AdGemCollectionStatus.INTERRUPTED
            return AdGemCollectionStatus.NO_TARGET
        tapped_once = True

        absent_observations = 0
        target_persisted = False
        for _ in range(3):
            time.sleep(0.25)
            (
                _verification_frame,
                verified_visible,
                verified_source,
            ) = (
                _fresh_target_observation(label)
            )
            if verified_visible is None:
                continue
            if verified_source != source_fingerprint:
                return AdGemCollectionStatus.UNCERTAIN
            if verified_visible:
                target_persisted = True
                break
            absent_observations += 1
            if absent_observations >= 2:
                return AdGemCollectionStatus.COLLECTED

        if not target_persisted:
            log(
                f"[AD_GEM] {label} dispatch had no authoritative post-input "
                "outcome",
                "ERROR",
            )
            return AdGemCollectionStatus.UNCERTAIN

        log(f"[AD_GEM] {label} still visible after tap — retrying", "DEBUG")
        time.sleep(0.4)

    log(f"[AD_GEM] {label} persisted after multiple tap attempts", "ERROR")
    return AdGemCollectionStatus.EXHAUSTED


def handle_home_ad_gem(
    *, action_guard_fn: ActionGuard = None
) -> AdGemCollectionStatus:
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
    outcome = _collect_visible_ad_gem(
        label,
        action_guard_fn=action_guard_fn,
        required_state="HOME_SCREEN",
    )
    log_result(
        (
            "Home ad-gem collection complete — reward collected"
            if outcome is AdGemCollectionStatus.COLLECTED
            else "Home ad-gem collection complete — no reward was collected"
        ),
        detail=(
            f"[AD_GEM] result={outcome.value} "
            f"source=home label={label}"
        ),
    )
    return outcome


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
            if bool(collected)
            else "In-battle ad-gem collection complete — no reward was collected"
        ),
        detail=(
            f"[AD_GEM] result="
            f"{collected.value if isinstance(collected, AdGemCollectionStatus) else 'collected' if collected else 'no_op'} "
            f"source=battle label={label}"
        ),
    )
    return bool(collected)
