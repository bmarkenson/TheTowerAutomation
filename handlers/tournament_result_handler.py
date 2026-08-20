"""Capture Tournament completion evidence without dismissing the result."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.android_clipboard import read_battle_report_clipboard
from core.battle_stats import (
    more_stats_from_terminal_save_report,
    parse_more_stats_clipboard,
)
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.input import (
    TapDispatchOutcome,
    TapDispatchStatus,
    TapVerification,
    safe_tap,
    tap_if_visible,
)
from core.label_tapper import is_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.tournament_results import (
    attach_tournament_conditions,
    build_tournament_result,
    find_recent_tournament_result,
    make_tournament_id,
    persist_tournament_result,
)
from core.tournament_conditions import (
    tournament_conditions_complete,
    unavailable_tournament_conditions,
)
from core.terminal_save_report import terminal_save_report_complete
from core.player_save_mapping_candidates import (
    build_mapping_candidate_ui_evidence,
)
from utils.logger import log


Frame = np.ndarray
TOURNAMENT_OK_REGION = (350, 1360, 380, 230)
TOURNAMENT_OK_POINT = (540, 1475)


def handle_tournament_results(
    summary_frame: Optional[Frame] = None,
    *,
    battle_context: Optional[Mapping[str, Any]] = None,
    captured_at: Optional[datetime] = None,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    mapping_observation_fn: Optional[
        Callable[[str, Mapping[str, Any]], int]
    ] = None,
) -> Optional[dict[str, Any]]:
    """Persist summary and Round Stats, then restore Tournament Stats.

    The terminal ``OK`` button is deliberately outside this handler's
    authority. Every tap here is matched against the same fresh frame used to
    verify the expected dialog.
    """

    when = captured_at or datetime.now().astimezone()
    result_id = make_tournament_id(when)
    summary = summary_frame if summary_frame is not None else capture_adb_screenshot()
    if summary is None or not is_visible("indicators.tournament_stats", screenshot=summary):
        log("[TOURNAMENT_RESULTS] Tournament Stats identity was not verified", "ERROR")
        return None

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    run_configuration = context.pop("run_configuration", None)
    battle_conditions = context.pop(
        "battle_conditions",
        unavailable_tournament_conditions(
            "terminal_condition_projection_unavailable"
        ),
    )
    if not isinstance(battle_conditions, Mapping):
        battle_conditions = unavailable_tournament_conditions(
            "terminal_condition_projection_invalid"
        )
    terminal_save_report = context.pop("terminal_save_report", None)

    existing = find_recent_tournament_result(summary, now=when)
    if existing is not None:
        if (
            not tournament_conditions_complete(existing.get("battle_conditions"))
            and tournament_conditions_complete(battle_conditions)
        ):
            try:
                enriched = attach_tournament_conditions(
                    existing, battle_conditions
                )
                persist_tournament_result(enriched)
            except Exception as exc:
                log(
                    "[TOURNAMENT_RESULTS] Could not enrich recent result "
                    f"with Battle Conditions: {exc}",
                    "ERROR",
                    console=True,
                )
            else:
                existing = enriched
                log(
                    "[TOURNAMENT_RESULTS] Attached terminal Battle Conditions "
                    f"to recent result {existing.get('tournament_id')}",
                    "INFO",
                    console=True,
                )
        log(
            "[TOURNAMENT_RESULTS] Current summary already matches recent result "
            f"{existing.get('tournament_id')}; skipping duplicate capture",
            "INFO",
            console=True,
        )
        _record_tournament_mapping_observations(
            existing,
            mapping_observation_fn,
            observed_at=when,
        )
        return existing

    clipboard_text = None
    detailed_stats = None
    detailed_reason = "terminal_save_report_unavailable"
    detailed_frame = None
    if terminal_save_report_complete(terminal_save_report):
        try:
            detailed_stats = more_stats_from_terminal_save_report(
                terminal_save_report
            )
        except Exception as exc:
            detailed_reason = f"save_record_build_failed:{exc}"
        else:
            detailed_reason = "terminal_player_save"
            log(
                "[TOURNAMENT_RESULTS] Loaded 144 exact Round Stats rows from "
                "the causally bound terminal player save",
                "INFO",
                console=True,
            )
    elif isinstance(terminal_save_report, Mapping):
        detailed_reason = str(
            terminal_save_report.get("reason")
            or "terminal_save_report_unavailable"
        )

    if detailed_stats is None:
        log(
            "[TOURNAMENT_RESULTS] Save-backed Round Stats unavailable "
            f"({detailed_reason}); using verified More Stats fallback",
            "INFO",
        )
    detail_tap_kwargs: dict[str, Any] = {
        "screenshot": summary,
        "retries": 1,
    }
    if action_guard_fn is not None:
        detail_tap_kwargs["action_guard_fn"] = action_guard_fn
    if detailed_stats is None and tap_if_visible(
        "buttons.more_stats:tournament",
        **detail_tap_kwargs,
    ):
        time.sleep(1.2)
        detailed_frame = capture_adb_screenshot()
        if detailed_frame is None or not is_visible(
            "indicators.more_stats",
            screenshot=detailed_frame,
        ):
            detailed_reason = "more_stats_not_verified"
        else:
            clipboard_text, detailed_reason = _copy_detailed_report(detailed_frame)
            close_frame = capture_adb_screenshot()
            if close_frame is None or not is_visible(
                "indicators.more_stats",
                screenshot=close_frame,
            ):
                detailed_reason += ":close_guard_failed"
            else:
                close_tap_kwargs: dict[str, Any] = {
                    "screenshot": close_frame,
                    "retries": 1,
                }
                if action_guard_fn is not None:
                    close_tap_kwargs["action_guard_fn"] = action_guard_fn
                if not tap_if_visible(
                    "buttons.close:more_stats",
                    **close_tap_kwargs,
                ):
                    detailed_reason += ":close_failed"
                else:
                    time.sleep(1.0)
                    restored = capture_adb_screenshot()
                    if restored is None or not is_visible(
                        "indicators.tournament_stats",
                        screenshot=restored,
                    ):
                        detailed_reason += ":summary_not_restored"

    try:
        record = build_tournament_result(
            summary,
            clipboard_text,
            detailed_stats=detailed_stats,
            detailed_reason=detailed_reason,
            tournament_id=result_id,
            captured_at=when,
            strategy_name=strategy_name,
            run_configuration=run_configuration,
            runtime_context=context,
            battle_conditions=battle_conditions,
        )
        _record_tournament_mapping_observations(
            record,
            mapping_observation_fn,
            observed_at=when,
        )
        json_path, markdown_path = persist_tournament_result(record)
    except Exception as exc:
        log(
            f"[TOURNAMENT_RESULTS] Structured result capture failed: {exc}",
            "ERROR",
            console=True,
        )
        _retain_evidence(result_id, summary, detailed_frame)
        return None

    log(
        f"[TOURNAMENT_RESULTS] Saved result: {json_path} (view: {markdown_path})",
        "INFO",
        console=True,
    )
    if record["quality"]["retain_source_images"]:
        _retain_evidence(result_id, summary, detailed_frame)
    return record


def _record_tournament_mapping_observations(
    record: Mapping[str, Any],
    callback: Optional[Callable[[str, Mapping[str, Any]], int]],
    *,
    observed_at: datetime,
) -> None:
    """Submit independent league/cause observations from a valid summary."""

    if callback is None:
        return
    summary = record.get("summary")
    fields = summary.get("fields") if isinstance(summary, Mapping) else None
    quality = summary.get("quality") if isinstance(summary, Mapping) else None
    if not (
        isinstance(fields, Mapping)
        and isinstance(quality, Mapping)
        and quality.get("valid") is True
    ):
        return
    observations = (
        ("tournament_league", "league"),
        ("battle_history_killed_by", "killed_by"),
    )
    for check_id, locator in observations:
        field = fields.get(locator)
        semantic = (
            str(field.get("value") or "").strip()
            if isinstance(field, Mapping)
            else ""
        )
        if not semantic:
            continue
        try:
            evidence = build_mapping_candidate_ui_evidence(
                check_id,
                canonical_values=[semantic],
                locator_values={locator: semantic},
                observed_at=observed_at,
            )
            callback(check_id, evidence)
        except Exception as exc:
            log(
                "[PLAYER_SAVE_MAPPING] Tournament mapping observation "
                f"failed for {check_id} without affecting result capture: {exc}",
                "WARN",
            )


def dismiss_tournament_results_to_home(
    *,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    timeout_s: float = 8.0,
    capture_fn: Callable[[], Optional[Frame]] = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Leave Tournament Results and its section for ordinary New Battle Home."""

    frame = capture_fn()
    if frame is None:
        log(
            "[TOURNAMENT_RESULTS] A fresh terminal route frame is unavailable",
            "ERROR",
            console=True,
        )
        return False
    initial_state = str(
        detect_state_and_overlays(frame).get("state") or ""
    ).upper()
    if (
        initial_state in {"HOME", "HOME_SCREEN"}
        and detect_home_battle_control(frame).control
        is HomeBattleControl.NEW_BATTLE
    ):
        return True
    if _tournament_ok_visible(frame):
        verification = TapVerification(
            screenshot=frame,
            target_region=TOURNAMENT_OK_REGION,
            description="Tournament Results OK button",
            verifier=_tournament_ok_visible,
        )
        if not safe_tap(
            TOURNAMENT_OK_POINT,
            screenshot=frame,
            verification=verification,
            log_label="buttons.ok:tournament_results",
            action_guard_fn=action_guard_fn,
        ):
            return False
    elif initial_state != "TOURNAMENT_SCREEN":
        log(
            "[TOURNAMENT_RESULTS] Verified Tournament terminal route is unavailable",
            "ERROR",
            console=True,
        )
        return False

    deadline = time.monotonic() + max(0.0, float(timeout_s))
    return_to_game_dispatched = False
    while time.monotonic() <= deadline:
        current = capture_fn()
        if current is not None:
            detection = detect_state_and_overlays(current)
            state = str(detection.get("state") or "").upper()
            if state in {"HOME", "HOME_SCREEN"} and (
                detect_home_battle_control(current).control
                is HomeBattleControl.NEW_BATTLE
            ):
                return True
            if state == "TOURNAMENT_SCREEN" and not return_to_game_dispatched:
                raw_outcome = tap_if_visible(
                    "buttons.return_to_game",
                    screenshot=current,
                    retries=0,
                    failure_log_level="DEBUG",
                    action_guard_fn=action_guard_fn,
                    return_dispatch_outcome=True,
                )
                outcome = (
                    raw_outcome
                    if isinstance(raw_outcome, TapDispatchOutcome)
                    else TapDispatchOutcome(
                        TapDispatchStatus.DISPATCHED
                        if raw_outcome
                        else TapDispatchStatus.NOT_DISPATCHED
                    )
                )
                if outcome.uncertain:
                    log(
                        "[TOURNAMENT_RESULTS] Return-to-Game dispatch outcome "
                        "is uncertain; the route will not repeat the input",
                        "ERROR",
                        console=True,
                    )
                    return False
                return_to_game_dispatched = outcome.dispatched
        sleep_fn(0.5)
    destination = (
        "ordinary Home after Return-to-Game"
        if return_to_game_dispatched
        else "the verified Tournament Return-to-Game control"
    )
    log(
        f"[TOURNAMENT_RESULTS] Timed out waiting for {destination}",
        "ERROR",
        console=True,
    )
    return False


def _tournament_ok_visible(frame: Optional[Frame]) -> bool:
    """Verify the bounded green OK artwork on a Tournament Stats dialog."""

    if frame is None or frame.ndim != 3:
        return False
    if not is_visible("indicators.tournament_stats", screenshot=frame):
        return False
    x, y, width, height = TOURNAMENT_OK_REGION
    if frame.shape[1] < x + width or frame.shape[0] < y + height:
        return False
    crop = frame[y : y + height, x : x + width]
    blue, green, red = cv2.split(crop)
    green_pixels = (
        (green >= 140)
        & (green.astype(np.float32) >= red.astype(np.float32) * 1.15)
        & (green.astype(np.float32) >= blue.astype(np.float32) * 1.15)
    )
    bright_pixels = (red >= 180) & (green >= 180) & (blue >= 180)
    rows, columns = np.where(green_pixels)
    return bool(
        len(columns) >= 1500
        and len(rows) >= 1500
        and int(bright_pixels.sum()) >= 500
        and int(columns.max() - columns.min()) >= 250
        and int(rows.max() - rows.min()) >= 120
    )


def _copy_detailed_report(frame: Frame) -> tuple[Optional[str], str]:
    """Copy and structurally validate the visible Round Stats report."""

    before = read_battle_report_clipboard()
    if not tap_if_visible(
        "buttons.copy:more_stats",
        screenshot=frame,
        retries=1,
    ):
        return None, "copy_button_not_visible"

    after = None
    for attempt in range(4):
        if attempt:
            time.sleep(0.25)
        candidate = read_battle_report_clipboard()
        if candidate.success:
            after = candidate
            if not before.success or candidate.text != before.text:
                break
    if after is None or not after.success or not after.text:
        return None, "clipboard_read_failed"
    try:
        detailed = parse_more_stats_clipboard(after.text)
    except Exception as exc:
        return None, f"clipboard_parse_failed:{exc}"
    if not detailed["quality"]["valid"]:
        return None, "clipboard_validation_failed"
    return after.text, "clipboard_copy"


def _retain_evidence(
    result_id: str,
    summary: Optional[Frame],
    detailed: Optional[Frame],
) -> None:
    evidence_dir = Path("screenshots/matches")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for suffix, frame in (("summary", summary), ("round_stats", detailed)):
        if frame is not None:
            cv2.imwrite(str(evidence_dir / f"{result_id}_{suffix}.png"), frame)


__all__ = [
    "dismiss_tournament_results_to_home",
    "handle_tournament_results",
]
