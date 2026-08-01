"""Capture Tournament completion evidence without dismissing the result."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Any, Mapping, Optional

import cv2
import numpy as np

from core.android_clipboard import read_battle_report_clipboard
from core.battle_stats import parse_more_stats_clipboard
from core.input import tap_if_visible
from core.label_tapper import is_visible
from core.ss_capture import capture_adb_screenshot
from core.tournament_results import (
    attach_tournament_conditions,
    build_tournament_result,
    find_recent_tournament_result,
    make_tournament_id,
    persist_tournament_result,
)
from core.tournament_conditions import (
    capture_current_tournament_conditions,
    tournament_conditions_complete,
    unavailable_tournament_conditions,
)
from utils.logger import log


Frame = np.ndarray


def handle_tournament_results(
    summary_frame: Optional[Frame] = None,
    *,
    battle_context: Optional[Mapping[str, Any]] = None,
    captured_at: Optional[datetime] = None,
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

    existing = find_recent_tournament_result(summary, now=when)
    if existing is not None:
        if not tournament_conditions_complete(existing.get("battle_conditions")):
            conditions = _capture_battle_conditions(when)
            if tournament_conditions_complete(conditions):
                try:
                    enriched = attach_tournament_conditions(existing, conditions)
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
                        "[TOURNAMENT_RESULTS] Attached Battle Conditions to recent "
                        f"result {existing.get('tournament_id')}",
                        "INFO",
                        console=True,
                    )
        log(
            "[TOURNAMENT_RESULTS] Current summary already matches recent result "
            f"{existing.get('tournament_id')}; skipping duplicate capture",
            "INFO",
            console=True,
        )
        return existing

    clipboard_text = None
    detailed_reason = "more_stats_not_opened"
    detailed_frame = None
    if tap_if_visible(
        "buttons.more_stats:tournament",
        screenshot=summary,
        retries=1,
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
            elif not tap_if_visible(
                "buttons.close:more_stats",
                screenshot=close_frame,
                retries=1,
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

    context = dict(battle_context or {})
    strategy_name = context.pop("strategy", None)
    run_configuration = context.pop("run_configuration", None)
    battle_conditions = context.pop("battle_conditions", None)
    if not tournament_conditions_complete(battle_conditions):
        battle_conditions = _capture_battle_conditions(when)
    try:
        record = build_tournament_result(
            summary,
            clipboard_text,
            detailed_reason=detailed_reason,
            tournament_id=result_id,
            captured_at=when,
            strategy_name=strategy_name,
            run_configuration=run_configuration,
            runtime_context=context,
            battle_conditions=battle_conditions,
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


def _capture_battle_conditions(when: datetime) -> dict[str, Any]:
    """Keep optional save evidence from blocking terminal-result capture."""

    try:
        return capture_current_tournament_conditions(captured_at=when)
    except Exception as exc:
        log(
            "[TOURNAMENT_RESULTS] Battle Condition save capture failed without "
            f"blocking the result: {exc}",
            "ERROR",
            console=True,
        )
        return unavailable_tournament_conditions("condition_capture_failed")


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


__all__ = ["handle_tournament_results"]
