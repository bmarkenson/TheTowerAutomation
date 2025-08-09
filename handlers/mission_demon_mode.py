# handlers/mission_demon_mode.py

"""
Mission: Demon Mode → End Round → Retry.

This module provides:
1) A single bounded round runner: `run_demon_mode_strategy(...)`
   - Waits for RUNNING
   - Waits for Demon Mode button and taps it
   - Waits a configured duration (post_demon_wait)
   - Opens menu (if closed), taps End Round → Yes → Retry
   - Uses per-phase timeouts to avoid hanging; returns a structured MissionResult

2) A campaign orchestrator: `run_demon_mode_campaign(...)`
   - Repeats the strategy round until a stop condition (max runs, duration, stopfile,
     user interrupt, or a custom `until(progress)` predicate fed by `progress_detector`)
   - Aggregates outcomes and timing

3) Back-compat wrapper: `run_demon_mode(wait_seconds=75)`
   - Preserves the original API; delegates to `run_demon_mode_strategy` and returns None.

Notes
- Side effects: ADB screenshots, OpenCV detection, on-device taps, file I/O for screenshots, and logging.
- Error policy: normal UI/detection issues are reflected in the result; programmer errors still raise.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, Any, Optional

from core.ss_capture import capture_and_save_screenshot
from core.clickmap_access import tap_now
from core.floating_button_detector import detect_floating_buttons, tap_floating_button
from core.state_detector import detect_state_and_overlays
from core.label_tapper import tap_label_now
from utils.logger import log


# ===== Mission types =====

class MissionOutcome(Enum):
    SUCCESS = auto()
    TIMEOUT_WAITING_FOR_RUNNING = auto()
    TIMEOUT_WAITING_FOR_DEMON = auto()
    UI_FLOW_FAILURE = auto()
    ABORTED_BY_USER = auto()


@dataclass
class MissionResult:
    outcome: MissionOutcome
    details: str = ""
    elapsed_s: float = 0.0
    phases: Dict[str, float] = field(default_factory=dict)  # phase_name -> seconds
    errors: list[str] = field(default_factory=list)


@dataclass
class MissionConfig:
    # Poll intervals
    poll_running_interval_s: float = 2.0
    poll_buttons_interval_s: float = 1.0

    # Post-activation wait (match legacy default 75s)
    post_demon_wait_s: float = 75.0

    # Timeouts per phase and overall per-round deadline
    timeout_running_s: float = 60.0
    timeout_demon_s: float = 45.0
    overall_deadline_s: float = 240.0

    # Verification & retries for taps that should change UI
    verify_tap: bool = True
    max_tap_retries: int = 2


@dataclass
class CampaignResult:
    runs: int
    successes: int
    timeouts_running: int
    timeouts_demon: int
    ui_failures: int
    aborted: bool
    total_elapsed_s: float
    last_result: Optional[MissionResult] = None
    progress: Dict[str, Any] | None = None


# ===== Strategy (single bounded round) =====

def run_demon_mode_strategy(
    config: MissionConfig | None = None,
    *,
    dry_run: bool = False,
    on_event: Callable[[str, Dict[str, Any]], None] | None = None,
) -> MissionResult:
    """
    Run one bounded Demon→End Round→Retry round and return a MissionResult.

    Flow
    - Wait for RUNNING (≤ timeout_running_s), poll every poll_running_interval_s
    - Wait for Demon Mode button (≤ timeout_demon_s), tap; wait post_demon_wait_s
    - Ensure menu open; tap End Round → Yes → Retry (best-effort with logging)

    Returns
    - MissionResult(outcome, details, elapsed, per-phase durations, errors)

    Side Effects
    - [adb][cv2][fs][state][tap][log]
    """
    cfg = config or MissionConfig()
    t0 = time.monotonic()
    deadline = t0 + cfg.overall_deadline_s
    phases: Dict[str, float] = {}
    errors: list[str] = []

    def emit(event: str, data: Dict[str, Any]):
        if on_event:
            try:
                on_event(event, data)
            except Exception as e:
                # Don’t let callbacks break mission flow
                log(f"[MISSION] on_event error @ {event}: {e}", "WARN")

    def _now() -> float:
        return time.monotonic()

    def _before_deadline() -> bool:
        return _now() < deadline

    def _phase(name: str, fn: Callable[[], MissionOutcome | None]) -> MissionOutcome | None:
        p0 = _now()
        try:
            emit("PHASE_START", {"name": name})
            return fn()
        finally:
            phases[name] = _now() - p0
            emit("PHASE_END", {"name": name, "duration_s": phases[name]})

    def _wait_for_state_running() -> MissionOutcome | None:
        end_by = _now() + cfg.timeout_running_s
        while _now() < end_by and _before_deadline():
            if dry_run:
                return None
            screen = capture_and_save_screenshot()
            result = detect_state_and_overlays(screen)
            if result.get("state") == "RUNNING":
                log("[MISSION] Game is in RUNNING state", "INFO")
                return None
            log("[MISSION] Waiting for RUNNING state...", "DEBUG")
            time.sleep(cfg.poll_running_interval_s)
        return MissionOutcome.TIMEOUT_WAITING_FOR_RUNNING

    def _tap_floating_button_with_verify(button_key: str) -> bool:
        """
        Tap a floating button (already detected via polling) and verify it no longer appears,
        retrying up to cfg.max_tap_retries. Returns True on verified success (or dry_run).
        """
        attempts = 0
        while True:
            attempts += 1
            if not dry_run:
                screen = capture_and_save_screenshot()
                buttons = detect_floating_buttons(screen)
                if any(b["name"] == button_key for b in buttons):
                    tap_floating_button(button_key, buttons)
                else:
                    if not cfg.verify_tap:
                        return True

            if not cfg.verify_tap or dry_run:
                return True

            # Verify disappearance (or state change) by re-detecting
            screen = capture_and_save_screenshot()
            buttons = detect_floating_buttons(screen)
            gone = not any(b["name"] == button_key for b in buttons)
            if gone:
                return True
            if attempts > cfg.max_tap_retries:
                return False
            log(f"[MISSION] '{button_key}' still visible — retrying tap ({attempts}/{cfg.max_tap_retries})", "WARN")
            time.sleep(cfg.poll_buttons_interval_s)

    def _wait_for_and_tap(button_key: str, timeout_s: float) -> MissionOutcome | None:
        end_by = _now() + timeout_s
        while _now() < end_by and _before_deadline():
            if dry_run:
                return None
            screen = capture_and_save_screenshot()
            buttons = detect_floating_buttons(screen)
            if any(b["name"] == button_key for b in buttons):
                log(f"[MISSION] {button_key.split('.')[-1].title()} button detected!", "INFO")
                ok = _tap_floating_button_with_verify(button_key)
                if not ok:
                    errors.append(f"Verify failed for {button_key}")
                    return MissionOutcome.UI_FLOW_FAILURE
                return None
            log(f"[MISSION] Waiting for {button_key}...", "DEBUG")
            time.sleep(cfg.poll_buttons_interval_s)
        return MissionOutcome.TIMEOUT_WAITING_FOR_DEMON

    try:
        log("[MISSION] Starting Demon Mode -> End Round -> Retry mission", "ACTION")

        # Phase: WAIT_RUNNING
        outcome = _phase("WAIT_RUNNING", _wait_for_state_running)
        if outcome:
            return MissionResult(
                outcome=outcome,
                details="RUNNING state not reached within timeout",
                elapsed_s=time.monotonic() - t0,
                phases=phases,
                errors=errors,
            )

        # Phase: WAIT_TAP_DEMON
        outcome = _phase("WAIT_TAP_DEMON", lambda: _wait_for_and_tap("floating_buttons.demon_mode", cfg.timeout_demon_s))
        if outcome:
            return MissionResult(
                outcome=outcome,
                details="Demon Mode button not available (or verify failed)",
                elapsed_s=time.monotonic() - t0,
                phases=phases,
                errors=errors,
            )

        # Phase: POST_DEMON_WAIT (preserve legacy countdown print)
        def _post_demon() -> MissionOutcome | None:
            wait_seconds = int(cfg.post_demon_wait_s)
            log(f"[MISSION] Demon Mode activated. Waiting {wait_seconds}s...", "INFO")
            if not dry_run:
                for remaining in range(wait_seconds, 0, -1):
                    print(f"\r[WAIT] {remaining} seconds remaining...", end="", flush=True)
                    time.sleep(1)
                print("\r[WAIT] Done.                                                  ")
            return None

        _phase("POST_DEMON_WAIT", _post_demon)

        # Phase: END_GAME_SEQUENCE
        def _end_game() -> MissionOutcome | None:
            if dry_run:
                return None

            screen = capture_and_save_screenshot()
            result = detect_state_and_overlays(screen)
            if "MENU_OPEN" not in result.get("overlays", []):
                log("[MISSION] Menu is closed — opening it", "DEBUG")
                tap_now("overlays.toggle_menu")
                time.sleep(1)

            try:
                tap_label_now("overlays.end_round")
            except Exception as e:
                msg = f"Failed to tap End Round: {e}"
                log(f"[MISSION] {msg}", "WARN")
                errors.append(msg)
            time.sleep(1)

            try:
                screen = capture_and_save_screenshot()
                tap_label_now("buttons.yes:end_round")
            except Exception as e:
                msg = f"Confirm Yes not visible: {e}"
                log(f"[MISSION] {msg}", "WARN")
                errors.append(msg)
            time.sleep(1)

            try:
                screen = capture_and_save_screenshot()
                tap_label_now("buttons.retry:game_over")
            except Exception as e:
                msg = f"Retry button not visible: {e}"
                log(f"[MISSION] {msg}", "WARN")
                errors.append(msg)

            return None

        _phase("END_GAME_SEQUENCE", _end_game)

        log("[MISSION] Demon-Mode strategy complete", "SUCCESS")
        return MissionResult(
            outcome=MissionOutcome.SUCCESS,
            details="Round completed",
            elapsed_s=time.monotonic() - t0,
            phases=phases,
            errors=errors,
        )

    except KeyboardInterrupt:
        log("[MISSION] Aborted by user", "WARN")
        return MissionResult(
            outcome=MissionOutcome.ABORTED_BY_USER,
            details="User interrupted",
            elapsed_s=time.monotonic() - t0,
            phases=phases,
            errors=errors,
        )


# ===== Campaign (repeat rounds until stop) =====

def run_demon_mode_campaign(
    config: MissionConfig | None = None,
    *,
    max_runs: int | None = None,
    max_duration_s: float | None = None,
    sleep_between_runs_s: float = 2.0,
    stopfile: str | None = None,
    progress_detector: Callable[[Any | None], Dict[str, Any]] | None = None,
    until: Callable[[Dict[str, Any]], bool] | None = None,
    on_event: Callable[[str, Dict[str, Any]], None] | None = None,
    dry_run: bool = False,
) -> CampaignResult:
    """
    Orchestrate repeated Demon→End Round→Retry rounds until a stop condition.

    Stop conditions (any):
    - max_runs reached
    - max_duration_s exceeded
    - stopfile exists
    - until(progress) returns True (if progress_detector supplied)
    - KeyboardInterrupt (returns aborted=True)

    Returns
    - CampaignResult with aggregates, last MissionResult, and last progress (if any)

    Side Effects
    - [adb][cv2][fs][state][tap][log][loop]
    """
    cfg = config or MissionConfig()
    t0 = time.monotonic()
    runs = successes = to_run = to_demon = ui_fail = 0
    aborted = False
    last_result: Optional[MissionResult] = None
    last_progress: Dict[str, Any] | None = None

    def emit(event: str, data: Dict[str, Any]):
        if on_event:
            try:
                on_event(event, data)
            except Exception as e:
                log(f"[CAMPAIGN] on_event error @ {event}: {e}", "WARN")

    try:
        emit("CAMPAIGN_START", {"max_runs": max_runs, "max_duration_s": max_duration_s})
        while True:
            # Check duration bound
            if max_duration_s is not None and (time.monotonic() - t0) >= max_duration_s:
                log("[CAMPAIGN] Max duration reached", "INFO")
                break
            # Check run bound
            if max_runs is not None and runs >= max_runs:
                log("[CAMPAIGN] Max runs reached", "INFO")
                break
            # Check stopfile
            if stopfile and os.path.exists(stopfile):
                log(f"[CAMPAIGN] Stopfile detected at {stopfile}", "INFO")
                break

            emit("ROUND_START", {"round_index": runs + 1})
            last_result = run_demon_mode_strategy(cfg, dry_run=dry_run, on_event=on_event)
            emit("ROUND_END", {"round_index": runs + 1, "outcome": last_result.outcome.name})
            runs += 1

            # Aggregate outcomes
            oc = last_result.outcome
            if oc == MissionOutcome.SUCCESS:
                successes += 1
            elif oc == MissionOutcome.TIMEOUT_WAITING_FOR_RUNNING:
                to_run += 1
            elif oc == MissionOutcome.TIMEOUT_WAITING_FOR_DEMON:
                to_demon += 1
            elif oc == MissionOutcome.UI_FLOW_FAILURE:
                ui_fail += 1
            elif oc == MissionOutcome.ABORTED_BY_USER:
                aborted = True
                break

            # Optional progress detection + termination predicate
            if progress_detector:
                try:
                    screen = None if dry_run else capture_and_save_screenshot()
                    last_progress = progress_detector(screen)
                    emit("PROGRESS", {"round_index": runs, "progress": last_progress})
                    if until and until(last_progress):
                        log("[CAMPAIGN] Until-condition satisfied", "INFO")
                        break
                except Exception as e:
                    log(f"[CAMPAIGN] progress_detector error: {e}", "WARN")

            # Inter-round pause & re-check stopfile
            if sleep_between_runs_s > 0:
                time.sleep(sleep_between_runs_s)
            if stopfile and os.path.exists(stopfile):
                log(f"[CAMPAIGN] Stopfile detected at {stopfile}", "INFO")
                break

        return CampaignResult(
            runs=runs,
            successes=successes,
            timeouts_running=to_run,
            timeouts_demon=to_demon,
            ui_failures=ui_fail,
            aborted=aborted,
            total_elapsed_s=time.monotonic() - t0,
            last_result=last_result,
            progress=last_progress,
        )

    except KeyboardInterrupt:
        log("[CAMPAIGN] Aborted by user", "WARN")
        return CampaignResult(
            runs=runs,
            successes=successes,
            timeouts_running=to_run,
            timeouts_demon=to_demon,
            ui_failures=ui_fail,
            aborted=True,
            total_elapsed_s=time.monotonic() - t0,
            last_result=last_result,
            progress=last_progress,
        )


# ===== Legacy wrapper (backward compatibility) =====

def run_demon_mode(wait_seconds: int = 75) -> None:
    """
    Backward-compatible wrapper for the original API.

    Args:
        wait_seconds: how long to wait after activating Demon Mode before ending the round.

    Returns:
        None — delegates to run_demon_mode_strategy and discards the MissionResult.
    """
    cfg = MissionConfig(post_demon_wait_s=float(wait_seconds))
    _ = run_demon_mode_strategy(cfg)
    return None
