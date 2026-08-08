"""Game Over navigation and structured battle-stat capture."""

import copy
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

import cv2

from utils.logger import log, log_action_intent, log_result
from core.android_clipboard import read_battle_report_clipboard
from core.ss_capture import capture_adb_screenshot
from core.run_state import AUTOMATION, ExecMode, RunState
from core.input import tap_if_visible
from core.label_tapper import is_visible
from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from core.battle_stats import (
    attach_battle_perks,
    build_battle_record,
    build_battle_record_from_clipboard,
    build_battle_record_from_player_save,
    make_battle_id,
    persist_battle_record,
)
from core.battle_perks import ocr_selected_perks
from core.perk_save_monitor import merge_terminal_perk_evidence
from core.terminal_save_report import terminal_save_report_complete


MORE_STATS_INDICATOR = "indicators.more_stats"
MORE_STATS_CONTENT_REGION = (100, 330, 880, 1370)
PERKS_INDICATOR = "indicators.perks_panel"
PERKS_CONTENT_REGION = (100, 414, 880, 1340)


@dataclass(frozen=True)
class GameOverHandlingOutcome:
    """Separate optional collection from terminal-route completion."""

    route_completed: bool
    route: str
    record: Optional[dict[str, Any]] = None
    stats_status: str = "unavailable"
    failure_step: Optional[str] = None


@dataclass(frozen=True)
class _GameOverStatsCaptureOutcome:
    """Best-effort data result plus the terminal modal restoration fact."""

    record: Optional[dict[str, Any]] = None
    screen_restored: bool = True
    failure_step: Optional[str] = None


def _game_stats_visible(screenshot) -> bool:
    """Accept either stable Game Stats title or More Stats button evidence."""

    return is_visible(
        "indicators.game_over",
        screenshot=screenshot,
    ) or is_visible(
        "buttons.more_stats:game_over",
        screenshot=screenshot,
    )


def handle_game_over(
    *,
    capture_stats: bool = True,
    battle_context: Optional[Mapping[str, Any]] = None,
    control_sync: Optional[Callable[[], None]] = None,
    before_terminal_action: Optional[Callable[[], None]] = None,
    after_retry_started: Optional[Callable[[], None]] = None,
    on_terminal_failure: Optional[Callable[[str], bool]] = None,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    return_home_after_battle: bool = False,
    report_disposition: Optional[Mapping[str, Any]] = None,
    captured_at: Optional[datetime] = None,
    battle_id: Optional[str] = None,
):
    """
    Handle the GAME OVER flow: capture stats, close stats, and retry or pause.

    Workflow:
      1) Capture the initial Game Stats dialog in memory.
      2) Use a proven save-backed final Perk inventory, or capture the complete
         ordered Selected Perks list and return to Game Stats.
      3) Prefer the causally bound, exact-version save History projection.
      4) If save evidence is unavailable or contradicts Game Stats, open More
         Stats and copy the complete report through Android's clipboard service.
      5) If clipboard acquisition or freshness validation fails, fall back to
         guarded overlapping screenshots and OCR.
      6) Close More Stats only when the UI fallback was opened.
      7) Based on AUTOMATION.mode, unless a guarded post-run workflow requires Home:
         - WAIT: loop until mode changes.
         - HOME: tap the Game Stats Home button and return to the home screen.
         - else: tap "Retry"; if it fails, abort handler.
         A required Home workflow bypasses WAIT but still honors PAUSED and
         STOPPED control.

    Returns:
        The structured battle record when capture succeeds, otherwise ``None``.

    Side effects:
        [adb] Captures screenshots in memory.
        [save][clipboard][ocr][fs] Writes a structured record and optional evidence.
        [fs] Creates directories and files.
        [tap][swipe] Sends UI input.
        [log] Emits structured logs.
        [loop] May wait/sleep and/or loop while in WAIT mode.

    Defaults:
        Uses several sleeps between actions (≈1.2–1.5s), and a final 2s sleep.

    Errors:
        Collection failures are best effort. An unavailable terminal control
        leaves the route pending for a later fresh frame without rewriting
        action authority or terminal policy.
    """
    captured_at = captured_at or datetime.now().astimezone()
    expected_battle_id = make_battle_id(captured_at)
    if battle_id is not None and str(battle_id) != expected_battle_id:
        raise ValueError("battle_id does not match the supplied capture time")
    battle_id = str(battle_id or expected_battle_id)
    session_id = _make_session_id(captured_at.timetuple())
    completed_record = None
    disposition = (
        copy.deepcopy(dict(report_disposition))
        if isinstance(report_disposition, Mapping)
        else None
    )
    log_action_intent(
        "Completing the finished battle",
        reason=(
            "preserve its stats and perks before following the configured "
            "post-run route"
            if capture_stats
            else "follow the configured post-run route without stats capture"
        ),
        detail=f"[GAME_OVER] session={session_id} capture_stats={capture_stats}",
    )

    stats_screen_restored = True
    stats_failure_step = None
    if capture_stats:
        try:
            stats_outcome = _capture_game_over_stats(
                battle_context=battle_context,
                battle_id=battle_id,
                captured_at=captured_at,
                session_id=session_id,
                disposition=disposition,
                action_guard_fn=action_guard_fn,
            )
        except Exception as exc:
            # Data extraction is deliberately subordinate to the selected
            # terminal route. Retain diagnostic evidence, then continue toward
            # Home/Retry instead of converting a data bug into global Pause.
            log(
                f"[BATTLE_STATS] Optional terminal collection failed: {exc}",
                "ERROR",
                console=True,
            )
            stats_outcome = _GameOverStatsCaptureOutcome(
                screen_restored=False,
                failure_step=f"terminal collection exception: {exc}",
            )
        completed_record = stats_outcome.record
        stats_screen_restored = stats_outcome.screen_restored
        stats_failure_step = stats_outcome.failure_step
    else:
        log("[GAME_OVER] Fast mode enabled — skipping More Stats capture", "DEBUG")

    if completed_record is not None:
        stats_status = "saved"
        stats_summary = "stats saved"
    elif capture_stats:
        stats_status = "unavailable"
        stats_summary = "stats unavailable"
    else:
        stats_status = "skipped"
        stats_summary = "stats capture skipped"

    # Decide the terminal action while continuing to consume the same control
    # file used by the main loop. PAUSED blocks every action; STOPPED exits.
    mode = AUTOMATION.mode
    entered_wait = mode == ExecMode.WAIT and not return_home_after_battle
    if entered_wait:
        log_result(
            (
                f"Finished-battle actions complete — {stats_summary}; "
                "automation is waiting on the Game Over screen (mode WAIT)"
            ),
            detail=(
                f"[GAME_OVER] result=completed session={session_id} route=wait "
                f"stats={stats_status} next_mode=WAIT"
            ),
        )
    mode = _wait_for_game_over_direction(
        control_sync,
        wait_mode_blocks=not return_home_after_battle,
    )
    if mode is None:
        if entered_wait:
            log(
                "Automation stopped after finished-battle actions completed "
                "while waiting on the Game Over screen",
                "INFO",
                console=True,
            )
        else:
            log_result(
                (
                    "Finished-battle handling interrupted — automation stopped "
                    "before post-run navigation"
                ),
                detail=(
                    f"[GAME_OVER] result=interrupted session={session_id} "
                    f"capture_stats={capture_stats} "
                    f"stats_saved={completed_record is not None}"
                ),
            )
        return GameOverHandlingOutcome(
            False,
            "stopped",
            completed_record,
            stats_status,
            "automation stopped before post-run navigation",
        )
    if not stats_screen_restored:
        stats_screen_restored = restore_game_stats_for_terminal_route(
            action_guard_fn=action_guard_fn,
        )
        if not stats_screen_restored:
            return _abort_handler(
                stats_failure_step or "Restore Game Stats after collection",
                session_id,
                on_terminal_failure=on_terminal_failure,
                record=completed_record,
                stats_status=stats_status,
            )
    if entered_wait:
        log_action_intent(
            "Following the finished-battle direction",
            reason=f"mode {mode.value} was selected after WAIT",
            detail=(
                f"[GAME_OVER] session={session_id} previous_mode=WAIT "
                f"next_mode={mode.value}"
            ),
        )
    if before_terminal_action is not None:
        before_terminal_action()
    if return_home_after_battle:
        mode = ExecMode.HOME
    if mode == ExecMode.HOME:
        if not tap_if_visible(
            "buttons.home:game_over",
            retries=1,
            action_guard_fn=action_guard_fn,
        ):
            return _abort_handler(
                "Go Home from Game Stats",
                session_id,
                on_terminal_failure=on_terminal_failure,
                record=completed_record,
                stats_status=stats_status,
            )
        route = "home"
        route_summary = "returned Home"
        log("[GAME_OVER] Mode HOME selected after Game Stats", "DEBUG")
    else:
        if not tap_if_visible(
            "buttons.retry:game_over",
            retries=1,
            action_guard_fn=action_guard_fn,
        ):
            return _abort_handler(
                "Retry Game",
                session_id,
                on_terminal_failure=on_terminal_failure,
                record=completed_record,
                stats_status=stats_status,
            )
        if after_retry_started is not None:
            after_retry_started()
        route = "retry"
        route_summary = "selected Retry"

    time.sleep(2)
    log_result(
        f"Finished-battle handling complete — {stats_summary}; {route_summary}",
        detail=(
            f"[GAME_OVER] result=completed session={session_id} route={route} "
            f"stats={stats_status}"
        ),
    )
    return GameOverHandlingOutcome(
        True,
        route,
        completed_record,
        stats_status,
    )


def _capture_game_over_stats(
    *,
    battle_context: Optional[Mapping[str, Any]],
    battle_id: str,
    captured_at: datetime,
    session_id: str,
    disposition: Optional[Mapping[str, Any]],
    action_guard_fn: Optional[Callable[[], bool]],
) -> _GameOverStatsCaptureOutcome:
    """Attempt terminal collection without owning the Home/Retry decision."""

    context = dict(battle_context or {})
    terminal_save_report = context.pop("terminal_save_report", None)
    # Keep source frames in memory. Routine successful captures persist only
    # structured data; screenshots are failure evidence.
    img_game_stats = capture_adb_screenshot()
    if img_game_stats is None:
        log(
            "[BATTLE_STATS] Game Stats capture unavailable; continuing to the "
            "selected terminal route",
            "WARN",
        )
        return _GameOverStatsCaptureOutcome(
            failure_step="Capture Game Stats",
        )

    perks, perks_frames, perks_screen_restored = _resolve_game_over_perks(
        context,
        action_guard_fn=action_guard_fn,
    )
    if not perks_screen_restored:
        log(
            "[BATTLE_STATS] Perks collection did not restore Game Stats; "
            "terminal-screen recovery will take precedence over data",
            "WARN",
        )
        return _GameOverStatsCaptureOutcome(
            screen_restored=False,
            failure_step="Close Perks",
        )

    record, save_reason = _capture_save_battle_record(
        terminal_save_report,
        battle_id=battle_id,
        game_stats_frame=img_game_stats,
        battle_context=context,
        captured_at=captured_at,
    )
    if record is not None:
        attach_battle_perks(record, perks)
        if disposition is not None:
            record["report_disposition"] = copy.deepcopy(dict(disposition))
        if _persist_battle_stats_record(
            record,
            session_id=session_id,
            game_stats_frame=img_game_stats,
            more_stats_frames=[],
            perks_frames=perks_frames,
        ):
            return _GameOverStatsCaptureOutcome(record=record)
        return _GameOverStatsCaptureOutcome(
            failure_step="Persist save-backed battle record",
        )

    log(
        "[BATTLE_STATS] Save-backed report unavailable "
        f"({save_reason}); using verified More Stats fallback",
        "INFO",
    )
    # Tap More Stats only after Perks capture has restored Game Stats. A miss
    # leaves the terminal modal untouched, so routing may continue immediately.
    if not tap_if_visible(
        "buttons.more_stats:game_over",
        retries=1,
        action_guard_fn=action_guard_fn,
    ):
        log(
            "[BATTLE_STATS] More Stats could not be opened; continuing to the "
            "selected terminal route",
            "WARN",
        )
        return _GameOverStatsCaptureOutcome(
            failure_step="Tap More Stats",
        )

    time.sleep(1.5)
    completed_record = None
    record, clipboard_reason = _capture_clipboard_battle_record(
        battle_id=battle_id,
        game_stats_frame=img_game_stats,
        battle_context=context,
        captured_at=captured_at,
        action_guard_fn=action_guard_fn,
    )
    if record is not None:
        attach_battle_perks(record, perks)
        if disposition is not None:
            record["report_disposition"] = copy.deepcopy(dict(disposition))
        if _persist_battle_stats_record(
            record,
            session_id=session_id,
            game_stats_frame=img_game_stats,
            more_stats_frames=[],
            perks_frames=perks_frames,
        ):
            completed_record = record
    else:
        log(
            f"[BATTLE_STATS] Clipboard capture unavailable ({clipboard_reason}); "
            "using guarded OCR fallback",
            "WARN",
        )
        # Repeatedly swipe to the true top, verifying the Round Stats panel
        # before and after every fallback gesture.
        top = scroll_to_edge(
            "gesture_targets.goto_top:more_stats",
            source_label=MORE_STATS_INDICATOR,
            progress_region=MORE_STATS_CONTENT_REGION,
            max_swipes=8,
            settle_s=1.2,
            action_guard_fn=action_guard_fn,
        )
        if top.screenshot is None:
            log(
                "[BATTLE_STATS] More Stats OCR source is unavailable "
                f"({top.reason}); preserving terminal navigation",
                "WARN",
            )
        else:
            if top.success:
                capture = capture_scroll_to_edge(
                    "gesture_targets.goto_pg2:more_stats",
                    source_label=MORE_STATS_INDICATOR,
                    screenshot=top.screenshot,
                    progress_region=MORE_STATS_CONTENT_REGION,
                    max_swipes=16,
                    settle_s=1.2,
                    action_guard_fn=action_guard_fn,
                )
                more_stats_frames = list(capture.screenshots)
                source_complete = capture.success
                source_reason = capture.reason
            else:
                # Preserve incomplete evidence but do not strand the completed
                # battle on a paging/OCR failure.
                more_stats_frames = [top.screenshot]
                source_complete = False
                source_reason = f"top_{top.reason}"

            completed_record = _save_battle_stats_record(
                battle_id=battle_id,
                session_id=session_id,
                game_stats_frame=img_game_stats,
                more_stats_frames=more_stats_frames,
                source_complete=source_complete,
                source_reason=source_reason,
                battle_context=context,
                captured_at=captured_at,
                perks=perks,
                perks_frames=perks_frames,
                report_disposition=disposition,
            )

    # More Stats is optional, but leaving its modal open would block the
    # authoritative Home/Retry control. Close it under the same live guard and
    # verify Game Stats before reporting the collection route complete.
    close_dispatched = tap_if_visible(
        "buttons.close:more_stats",
        retries=1,
        action_guard_fn=action_guard_fn,
    )
    if close_dispatched:
        restored = _wait_for_game_stats(timeout=3.0) is not None
    else:
        current = capture_adb_screenshot()
        restored = bool(current is not None and _game_stats_visible(current))
    if not restored:
        return _GameOverStatsCaptureOutcome(
            record=completed_record,
            screen_restored=False,
            failure_step="Close More Stats",
        )
    time.sleep(1.2)
    return _GameOverStatsCaptureOutcome(record=completed_record)


def restore_game_stats_for_terminal_route(
    screenshot=None,
    *,
    action_guard_fn: Optional[Callable[[], bool]] = None,
) -> bool:
    """Restore the terminal modal from a collection sub-screen, if proven."""

    current = screenshot if screenshot is not None else capture_adb_screenshot()
    if current is None:
        return False
    if _game_stats_visible(current):
        return True
    close_label = None
    if is_visible(PERKS_INDICATOR, screenshot=current):
        close_label = "buttons.close:perks"
    elif is_visible(MORE_STATS_INDICATOR, screenshot=current):
        close_label = "buttons.close:more_stats"
    if close_label is None:
        return False
    if not tap_if_visible(
        close_label,
        screenshot=current,
        retries=1,
        action_guard_fn=action_guard_fn,
    ):
        return False
    return _wait_for_game_stats(timeout=3.0) is not None


def _wait_for_game_over_direction(
    control_sync: Optional[Callable[[], None]],
    *,
    wait_mode_blocks: bool = True,
) -> Optional[ExecMode]:
    """Wait interruptibly for terminal input authority and a direction."""

    while True:
        if control_sync is not None:
            control_sync()
        state = AUTOMATION.state
        if state is RunState.STOPPED:
            return None
        if state is RunState.PAUSED or (
            wait_mode_blocks and AUTOMATION.mode is ExecMode.WAIT
        ):
            time.sleep(1)
            continue
        return AUTOMATION.mode


def _capture_game_over_perks(
    *,
    action_guard_fn: Optional[Callable[[], bool]] = None,
):
    """Capture every ordered Selected Perks row and restore Game Stats."""

    game_stats_screen = capture_adb_screenshot()
    if game_stats_screen is None:
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="game_stats_capture_failed",
            ),
            [],
            True,
        )
    if not _game_stats_visible(game_stats_screen):
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="game_stats_not_visible",
            ),
            [],
            False,
        )
    if not tap_if_visible(
        "buttons.perks:game_over",
        screenshot=game_stats_screen,
        retries=1,
        action_guard_fn=action_guard_fn,
    ):
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="perks_button_not_visible",
            ),
            [],
            True,
        )

    perks_screen = _wait_for_visible(PERKS_INDICATOR, timeout=5.0)
    if perks_screen is None:
        current = capture_adb_screenshot()
        restored = bool(
            current is not None
            and _game_stats_visible(current)
        )
        return (
            ocr_selected_perks(
                [],
                source_complete=False,
                source_reason="perks_panel_not_visible",
            ),
            [],
            restored,
        )

    top = scroll_to_edge(
        "gesture_targets.goto_top:perks",
        source_label=PERKS_INDICATOR,
        screenshot=perks_screen,
        progress_region=PERKS_CONTENT_REGION,
        max_swipes=8,
        settle_s=0.8,
        action_guard_fn=action_guard_fn,
    )
    if top.screenshot is None:
        frames = []
        source_complete = False
        source_reason = f"top_{top.reason}"
    elif top.success:
        capture = capture_scroll_to_edge(
            "gesture_targets.goto_next:perks",
            source_label=PERKS_INDICATOR,
            screenshot=top.screenshot,
            progress_region=PERKS_CONTENT_REGION,
            max_swipes=20,
            settle_s=0.8,
            action_guard_fn=action_guard_fn,
        )
        frames = list(capture.screenshots)
        source_complete = capture.success
        source_reason = capture.reason
    else:
        frames = [top.screenshot]
        source_complete = False
        source_reason = f"top_{top.reason}"

    try:
        perks = ocr_selected_perks(
            frames,
            source_complete=source_complete,
            source_reason=source_reason,
        )
        log(
            f"[BATTLE_PERKS] Captured {perks['quality']['perk_count']} ordered perk(s)",
            "DEBUG",
        )
    except Exception as exc:
        log(f"[BATTLE_PERKS] OCR failed: {exc}", "ERROR", console=True)
        perks = ocr_selected_perks(
            [],
            source_complete=False,
            source_reason=f"ocr_failed:{exc}",
        )

    restored = False
    for attempt in range(2):
        if not tap_if_visible(
            "buttons.close:perks",
            retries=1,
            action_guard_fn=action_guard_fn,
        ):
            break
        game_stats_screen = _wait_for_game_stats(timeout=3.0)
        if game_stats_screen is not None:
            restored = True
            break
        current = capture_adb_screenshot()
        if current is None or not is_visible(PERKS_INDICATOR, screenshot=current):
            break
        log(
            "[BATTLE_PERKS] Perks remained open after close; retrying the "
            f"verified close ({attempt + 2}/2)",
            "DEBUG",
        )
    return perks, frames, restored


def _resolve_game_over_perks(
    context: dict[str, Any],
    *,
    action_guard_fn: Optional[Callable[[], bool]] = None,
):
    """Use a proven saved final inventory or retain the terminal UI route."""

    monitoring = context.get("perk_save_monitoring")
    inventory = (
        monitoring.get("final_inventory")
        if isinstance(monitoring, Mapping)
        else None
    )
    if isinstance(inventory, Mapping):
        quality = inventory.get("quality")
        exact_picks = inventory.get("exact_saved_picks")
        if (
            monitoring.get("status") == "complete_final_prefix"
            and monitoring.get("context_status") == "bound"
            and monitoring.get("active_failure_reason") is None
            and monitoring.get("round_conflict_reason") is None
            and monitoring.get("ui_fallback", {}).get("required") is False
            and isinstance(monitoring.get("checkpoint"), Mapping)
            and isinstance(monitoring.get("exhaustion"), Mapping)
            and monitoring.get("terminal_window", {}).get("status")
            == "closed_by_inactive_cleared_projection"
            and inventory.get("status") == "complete_exact_saved_inventory"
            and inventory.get("source_method") == "player_save_perk_checkpoint"
            and inventory.get("exact_saved_prefix") == monitoring.get("checkpoint")
            and isinstance(exact_picks, list)
            and bool(exact_picks)
            and exact_picks == monitoring["checkpoint"].get("picks")
            and isinstance(quality, Mapping)
            and quality.get("valid") is True
            and quality.get("source_complete") is True
        ):
            log(
                "[BATTLE_PERKS] Complete post-exhaustion save checkpoint "
                "replaced terminal Perks-panel navigation",
                "INFO",
            )
            return dict(inventory), [], True

    terminal_ui, frames, restored = _capture_game_over_perks(
        action_guard_fn=action_guard_fn,
    )
    if not restored or not isinstance(monitoring, Mapping):
        return terminal_ui, frames, restored
    if (
        monitoring.get("context_status") != "bound"
        or not isinstance(monitoring.get("checkpoint"), Mapping)
        or monitoring.get("active_failure_reason")
        or monitoring.get("round_conflict_reason")
    ):
        return terminal_ui, frames, restored

    inventory, merge = merge_terminal_perk_evidence(
        monitoring,
        terminal_ui,
        top_bar_timeline=context.get("perk_selection_timeline"),
        game_over_wave=context.get("last_wave"),
    )
    context["perk_terminal_merge"] = merge
    if inventory is not None:
        return inventory, frames, restored

    if merge.get("reason") != "terminal_ui_incomplete":
        terminal_ui = dict(terminal_ui)
        quality = dict(terminal_ui.get("quality") or {})
        quality["valid"] = False
        quality["retain_source_images"] = True
        warnings = list(quality.get("warnings") or [])
        warnings.append(
            "Terminal Perks evidence conflicted with the retained exact saved "
            f"prefix ({merge.get('reason') or 'unknown conflict'})"
        )
        quality["warnings"] = warnings
        terminal_ui["quality"] = quality
    return terminal_ui, frames, restored


def _wait_for_visible(label: str, *, timeout: float, poll: float = 0.25):
    """Return a fresh frame only after the expected control is visible."""

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        screenshot = capture_adb_screenshot()
        if screenshot is not None and is_visible(label, screenshot=screenshot):
            return screenshot
        time.sleep(max(0.05, poll))
    return None


def _wait_for_game_stats(*, timeout: float, poll: float = 0.25):
    """Return only after the Perks close restores the Game Stats modal."""

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        screenshot = capture_adb_screenshot()
        if screenshot is not None and _game_stats_visible(screenshot):
            return screenshot
        time.sleep(max(0.05, poll))
    return None


def _capture_save_battle_record(
    terminal_report: Any,
    *,
    battle_id: str,
    game_stats_frame,
    battle_context: Optional[Mapping[str, Any]],
    captured_at: datetime,
):
    """Build one record without terminal UI input when save proof is complete."""

    if not terminal_save_report_complete(terminal_report):
        reason = (
            terminal_report.get("reason")
            if isinstance(terminal_report, Mapping)
            else "terminal_save_report_not_captured"
        )
        return None, str(reason or "terminal_save_report_unavailable")

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    run_configuration = context.pop("run_configuration", None)
    try:
        record = build_battle_record_from_player_save(
            game_stats_frame,
            terminal_report,
            battle_id=battle_id,
            captured_at=captured_at,
            strategy_name=strategy_name,
            run_configuration=run_configuration,
            runtime_context=context,
        )
    except Exception as exc:
        return None, f"save_record_build_failed:{exc}"

    quality = record.get("quality", {})
    if quality.get("valid") is not True:
        identity = quality.get("identity", {})
        mismatches = identity.get("mismatches", [])
        if mismatches:
            fields = ",".join(
                str(item.get("field") or "unknown")
                for item in mismatches
                if isinstance(item, Mapping)
            )
            return None, f"save_identity_mismatch:{fields or 'unknown'}"
        return None, "save_record_validation_failed"

    row_count = record.get("more_stats", {}).get("quality", {}).get("row_count")
    log(
        f"[BATTLE_STATS] Loaded {row_count} exact Stats rows from the "
        "causally bound terminal player save",
        "INFO",
        console=True,
    )
    return record, "terminal_player_save"


def _capture_clipboard_battle_record(
    *,
    battle_id: str,
    game_stats_frame,
    battle_context: Optional[Mapping[str, Any]],
    captured_at: datetime,
    action_guard_fn: Optional[Callable[[], bool]] = None,
):
    """Copy, read, parse, and freshness-check the visible More Stats report."""

    stats_screen = capture_adb_screenshot()
    if stats_screen is None:
        return None, "capture_before_copy_failed"
    if not is_visible(MORE_STATS_INDICATOR, screenshot=stats_screen):
        return None, "more_stats_not_visible"

    before = read_battle_report_clipboard()
    if not tap_if_visible(
        "buttons.copy:more_stats",
        screenshot=stats_screen,
        retries=1,
        action_guard_fn=action_guard_fn,
    ):
        return None, "copy_tap_failed"

    candidate = None
    after_reason = "clipboard_read_failed"
    for attempt in range(4):
        if attempt:
            time.sleep(0.25)
        after = read_battle_report_clipboard()
        after_reason = after.reason
        if not after.success:
            continue
        candidate = after.text
        if not before.success or candidate != before.text:
            break
    if candidate is None:
        return None, after_reason

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    run_configuration = context.pop("run_configuration", None)
    try:
        record = build_battle_record_from_clipboard(
            game_stats_frame,
            candidate,
            battle_id=battle_id,
            captured_at=captured_at,
            strategy_name=strategy_name,
            run_configuration=run_configuration,
            runtime_context=context,
        )
    except Exception as exc:
        return None, f"clipboard_parse_failed:{exc}"

    more_quality = record["more_stats"]["quality"]
    if not more_quality["valid"]:
        warnings = "; ".join(more_quality.get("warnings", []))
        return None, f"clipboard_validation_failed:{warnings or 'invalid_report'}"

    identity = record["quality"]["identity"]
    if identity["mismatches"]:
        fields = ",".join(item["field"] for item in identity["mismatches"])
        return None, f"clipboard_identity_mismatch:{fields}"

    changed = bool(before.success and candidate != before.text)
    checked = set(identity["checked_fields"])
    strongly_identified = {"wave", "tier"}.issubset(checked)
    if not changed and not strongly_identified:
        return None, "clipboard_freshness_unproven"

    log(
        f"[BATTLE_STATS] Copied {more_quality['row_count']} exact Stats rows "
        "from the Android clipboard",
        "INFO",
        console=True,
    )
    return record, "clipboard_copy"


def _save_battle_stats_record(
    *,
    battle_id: str,
    session_id: str,
    game_stats_frame,
    more_stats_frames,
    source_complete: bool,
    source_reason: str,
    battle_context: Optional[Mapping[str, Any]],
    captured_at: Optional[datetime] = None,
    perks: Optional[Mapping[str, Any]] = None,
    perks_frames=(),
    report_disposition: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Persist OCR output and retain source images only for uncertain records."""

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    run_configuration = context.pop("run_configuration", None)
    try:
        record = build_battle_record(
            game_stats_frame,
            more_stats_frames,
            source_complete=source_complete,
            source_reason=source_reason,
            battle_id=battle_id,
            captured_at=captured_at,
            strategy_name=strategy_name,
            run_configuration=run_configuration,
            runtime_context=context,
        )
        if perks is not None:
            attach_battle_perks(record, perks)
        if isinstance(report_disposition, Mapping):
            record["report_disposition"] = copy.deepcopy(
                dict(report_disposition)
            )
        persisted = _persist_battle_stats_record(
            record,
            session_id=session_id,
            game_stats_frame=game_stats_frame,
            more_stats_frames=more_stats_frames,
            perks_frames=perks_frames,
        )
        return record if persisted else None
    except Exception as exc:
        log(f"[BATTLE_STATS] Structured capture failed: {exc}", "ERROR", console=True)

    save_image(game_stats_frame, f"{session_id}_game_stats_OCR_EVIDENCE")
    for index, frame in enumerate(more_stats_frames, start=1):
        save_image(frame, f"{session_id}_more_stats_{index}_OCR_EVIDENCE")
    for index, frame in enumerate(perks_frames, start=1):
        save_image(frame, f"{session_id}_perks_{index}_OCR_EVIDENCE")
    return None


def _persist_battle_stats_record(
    record: Mapping[str, Any],
    *,
    session_id: str,
    game_stats_frame,
    more_stats_frames,
    perks_frames=(),
) -> bool:
    """Persist a built record and retain only the source frames it needs."""

    try:
        json_path, markdown_path = persist_battle_record(record)
        log(
            f"[BATTLE_STATS] Saved record: {json_path} (view: {markdown_path})",
            "INFO",
            console=True,
        )
    except Exception as exc:
        log(f"[BATTLE_STATS] Record persistence failed: {exc}", "ERROR", console=True)
        save_image(game_stats_frame, f"{session_id}_game_stats_OCR_EVIDENCE")
        for index, frame in enumerate(more_stats_frames, start=1):
            save_image(frame, f"{session_id}_more_stats_{index}_OCR_EVIDENCE")
        for index, frame in enumerate(perks_frames, start=1):
            save_image(frame, f"{session_id}_perks_{index}_OCR_EVIDENCE")
        return False

    if not record["quality"]["retain_source_images"]:
        return True
    warnings = "; ".join(record["quality"]["warnings"]) or "validation failed"
    log(f"[BATTLE_STATS] Retaining source screenshots: {warnings}", "WARN")
    save_image(game_stats_frame, f"{session_id}_game_stats_OCR_EVIDENCE")
    for index, frame in enumerate(more_stats_frames, start=1):
        save_image(frame, f"{session_id}_more_stats_{index}_OCR_EVIDENCE")
    for index, frame in enumerate(perks_frames, start=1):
        save_image(frame, f"{session_id}_perks_{index}_OCR_EVIDENCE")
    return True


def _make_session_id(captured_at=None):
    """
    Build a session identifier for captured artifacts.

    Returns:
        str: "GameYYYYMMDD_%H%M%S"
    """
    return "Game" + time.strftime("%Y%m%d_%H%M%S", captured_at)


def save_image(img, tag):
    """
    Persist a screenshot to the matches directory with a descriptive tag.

    Args:
        img (ndarray | None): BGR image to write (cv2). If None, skip with a warning.
        tag (str): Filename tag (without extension).

    Returns:
        None

    Side effects:
        [fs] Ensures parent directories exist and writes a PNG file.
        [cv2] Uses cv2.imwrite to serialize the image.
        [log] Logs path and warns if img is None.
    """
    if img is None:
        log(f"[CAPTURE] No image to save for tag '{tag}' (img=None). Skipping.", "WARN")
        return
    path = os.path.join("screenshots", "matches", f"{tag}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)
    log(f"[CAPTURE] Saved screenshot: {path}", "DEBUG")


def _abort_handler(
    step,
    session_id,
    *,
    on_terminal_failure: Optional[Callable[[str], bool]] = None,
    record: Optional[dict[str, Any]] = None,
    stats_status: str = "unavailable",
):
    """
    Abort helper for the GAME OVER handler.

    Log and retain evidence for a terminal route that should retry from a later
    fresh frame. This helper never changes global action authority.

    Args:
        step (str): Human-readable step name that failed.
        session_id (str): Session identifier used for artifact naming.

    Returns:
        None

    Side effects:
        [adb][cv2][fs][log] Capture & persist debug screenshot; emit error.
        [state] Reports the failure to the caller without changing authority.
    """
    log(f"[ABORT] Game Over handler failed at: {step}", "ERROR")
    debug_img = capture_adb_screenshot()
    save_image(debug_img, f"{session_id}_ABORT_{step.replace(' ', '_')}")
    terminal_policy = AUTOMATION.mode.value
    if on_terminal_failure is not None:
        try:
            on_terminal_failure(step)
        except Exception as exc:
            log(
                "[ABORT] Could not publish the pending terminal failure: "
                f"{exc}",
                "ERROR",
                console=True,
            )
    action_authority = AUTOMATION.state.value
    log_result(
        f"Finished-battle route pending — {step} did not complete",
        detail=(
            f"[GAME_OVER] result=pending_retry session={session_id} "
            f"failed_step={step} terminal_policy={terminal_policy} "
            f"action_authority={action_authority} retry=true"
        ),
    )
    return GameOverHandlingOutcome(
        False,
        "pending_retry",
        record,
        stats_status,
        str(step),
    )
