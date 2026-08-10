"""Guarded Home-boundary enforcement for strategy-owned Perk configuration."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.battle_lifecycle import HomeBattleControl
from core.battle_perks import (
    ocr_perk_configuration_row_near,
    ocr_perk_configuration_rows,
)
from core.home_battle import detect_home_battle_control
from core.input import TapVerification, safe_tap, swipe_now, tap_if_visible
from core.label_tapper import is_visible
from core.perk_configuration import (
    evaluate_profile_perk_configuration,
    extract_configured_perk_bans,
    extract_ranked_auto_pick_order,
    normalize_perk_configuration_requirements,
    normalize_perk_first_choice_requirement,
    parse_perk_configuration_selection,
    perk_configuration_label,
    perk_entries_match,
    semantic_perk_entry,
)
from core.player_save_mapping_candidates import (
    build_mapping_candidate_ui_evidence,
)
from core.scrolling import capture_scroll_to_edge, guarded_swipe, scroll_to_edge
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.workshop_preset import measure_preset_slot_selection
from utils.logger import log, log_action_intent, log_result


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]
RowsFn = Callable[[Frame], list[dict[str, Any]]]
ScrollStopFn = Callable[[Frame], Optional[str]]

PERK_CONFIGURATION_INDICATOR = "indicators.perks_configuration"
PERK_CONTENT_REGION = (100, 420, 880, 1330)
HOME_PERKS_CONTROL_REGION = (985, 475, 90, 100)
MIN_HOME_PERKS_GRAY_PIXELS = 800
PERK_TABS = {
    "perk_first_choice": ("First Perk", (222, 210, 210, 90)),
    "perk_bans": ("Ban Perks", (436, 210, 210, 90)),
    "perk_auto_pick_order": ("Auto Pick", (650, 210, 210, 90)),
}

BAN_AVAILABLE_START_SWIPES = 2
MAX_BAN_SCAN_SWIPES = 14
MAX_AUTO_PICK_SCAN_SWIPES = 20
MAX_AUTO_PICK_MOVE_TAPS = 300
MAX_AUTO_PICK_LOCAL_REACQUIRE_SWIPES = 4
MAX_AUTO_PICK_LOCAL_CONTEXT_RETRIES = 2
MAX_AUTO_PICK_SEMANTIC_RESYNCS = 2
MAX_PERK_OCR_RETRIES = 2
PERK_OCR_RETRY_SETTLE_SECONDS = 0.6
MAX_BAN_STABLE_CAPTURE_ATTEMPTS = 4
MAX_BAN_RECONCILIATION_ACTIONS = 14
MAX_BAN_NO_PROGRESS_RETRIES = 1
AUTO_PICK_UP_X = 915
BAN_SELECTED_TOGGLE_X = 540
BAN_TOGGLE_X = 944


@dataclass(frozen=True)
class HomePerkConfigurationResult:
    valid: bool
    changed: bool
    reason: str
    failed_check: str | None
    evidence: Mapping[str, Any]
    home_screenshot: Frame


class HomePerkConfigurationError(RuntimeError):
    pass


class HomePerkConfigurationRepairExhausted(HomePerkConfigurationError):
    """A bounded in-panel repair proved that a fresh Home retry cannot help."""

    pass


def _finish_home_perk_configuration(
    result: HomePerkConfigurationResult,
) -> HomePerkConfigurationResult:
    """Emit the terminal result for one Home Perk configuration pass."""

    changed_fields = [
        check_id
        for check_id in (
            "perk_first_choice",
            "perk_bans",
            "perk_auto_pick_order",
        )
        if isinstance(result.evidence.get(check_id), Mapping)
        and result.evidence[check_id].get("changed") is True
    ]
    changed_labels = {
        "perk_bans": "Ban Perks",
        "perk_auto_pick_order": "Auto Pick order",
        "perk_first_choice": "First Perk Choice",
    }
    if result.valid and changed_fields:
        summary = (
            "Home Perk configuration complete — repaired and verified "
            f"{', '.join(changed_labels[field] for field in changed_fields)}"
        )
    elif result.valid:
        summary = "Home Perk configuration complete — verified without changes"
    else:
        summary = f"Home Perk configuration failed — {result.reason}"
    log_result(
        summary,
        detail=(
            f"[HOME_PERKS] result={'completed' if result.valid else 'failed'} "
            f"valid={result.valid} changed={result.changed} "
            f"changed_fields={changed_fields} failed_check={result.failed_check} "
            f"reason={result.reason}"
        ),
    )
    return result


def detect_home_perks_configuration_control(
    screenshot: Optional[Frame],
) -> dict[str, Any]:
    """Recognize the expanded Home-menu Perks item beside Cards."""

    x, y, width, height = HOME_PERKS_CONTROL_REGION
    if (
        screenshot is None
        or not isinstance(screenshot, np.ndarray)
        or screenshot.ndim != 3
        or y + height > screenshot.shape[0]
        or x + width > screenshot.shape[1]
    ):
        return {
            "visible": False,
            "gray_pixels": 0,
            "region": list(HOME_PERKS_CONTROL_REGION),
        }
    crop = screenshot[y : y + height, x : x + width]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray_pixels = int(((hsv[:, :, 1] < 60) & (hsv[:, :, 2] >= 20)).sum())
    return {
        "visible": gray_pixels >= MIN_HOME_PERKS_GRAY_PIXELS,
        "gray_pixels": gray_pixels,
        "minimum_gray_pixels": MIN_HOME_PERKS_GRAY_PIXELS,
        "region": list(HOME_PERKS_CONTROL_REGION),
    }


def ensure_home_perk_configuration(
    requirements: Mapping[str, Any],
    *,
    home_screenshot: Frame,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    detect_home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    visible_fn: Callable[..., bool] = is_visible,
    swipe_fn: Callable[[str], bool] = swipe_now,
    row_fn: RowsFn = ocr_perk_configuration_rows,
    row_near_fn: Callable[..., Optional[dict[str, Any]]] = (
        ocr_perk_configuration_row_near
    ),
    parse_selection_fn: Callable[..., Mapping[str, Any]] = (
        parse_perk_configuration_selection
    ),
    measure_selection_fn: Callable[..., Any] = measure_preset_slot_selection,
    waived_fields: Sequence[str] = (),
    mapping_observation_fn: Optional[
        Callable[[str, Mapping[str, Any]], Any]
    ] = None,
    repair_observer_fn: Optional[Callable[[str], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    operator_workflow: bool = True,
) -> HomePerkConfigurationResult:
    """Verify and restore the strategy's Ban and Auto Pick lists at Home."""

    required_bans, required_auto_pick = (
        normalize_perk_configuration_requirements(requirements)
    )
    required_first_choice = (
        normalize_perk_first_choice_requirement(requirements)
        if "perk_first_choice" in requirements
        else None
    )
    waived = {
        str(field)
        for field in waived_fields
        if str(field)
        in {"perk_first_choice", "perk_bans", "perk_auto_pick_order"}
    }
    _require_new_battle_home(
        home_screenshot,
        detector,
        detect_home_control_fn,
    )
    if operator_workflow:
        log_action_intent(
            "Checking Home Perk configuration",
            reason=(
                "verify the strategy-owned Ban and Auto Pick settings before "
                "the new battle and repair only authoritative mismatches"
            ),
            detail=(
                f"[HOME_PERKS] required_bans={len(required_bans)} "
                f"required_auto_pick={len(required_auto_pick)} "
                f"required_first_choice={required_first_choice} "
                f"waived={sorted(waived)}"
            ),
        )
    perks = _open_configuration(
        home_screenshot,
        capture_fn=capture_fn,
        detector=detector,
        safe_tap_fn=safe_tap_fn,
        visible_fn=visible_fn,
        sleep_fn=sleep_fn,
    )

    changed_fields: set[str] = set()
    repair_announced = False

    def announce_repair(check_id: str) -> None:
        nonlocal repair_announced
        if repair_announced:
            return
        repair_announced = True
        if repair_observer_fn is not None:
            repair_observer_fn(check_id)

    current = perks
    first_choice_evidence: dict[str, Any] | None = None
    if (
        required_first_choice is not None
        and "perk_first_choice" not in waived
    ):
        first_top = _select_and_scroll_top(
            current,
            field="perk_first_choice",
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            measure_selection_fn=measure_selection_fn,
            sleep_fn=sleep_fn,
        )
        first_frames, current, first_complete = _capture_configuration_pages(
            first_top,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        captured_first = dict(
            parse_selection_fn(
                first_frames,
                field="perk_first_choice",
                source_complete=first_complete,
                source_reason=(
                    "bottom edge verified"
                    if first_complete
                    else "configuration bottom edge was not verified"
                ),
            )
        )
        first_choice_evidence = _first_choice_comparison(
            required_first_choice,
            captured_first,
        )
        _record_initial_mapping_observation(
            mapping_observation_fn,
            "perk_first_choice",
            captured_first,
        )
        if first_choice_evidence["valid"] is not True:
            quality = captured_first.get("quality")
            selected = captured_first.get("selected")
            if (
                not isinstance(quality, Mapping)
                or quality.get("valid") is not True
                or not isinstance(selected, list)
                or len(selected) != 1
                or selected[0].get("key") is None
            ):
                raise HomePerkConfigurationError(
                    "First Perk Choice was not authoritative enough to repair"
                )
            announce_repair("perk_first_choice")
            _rank, target_frame, target_row = _locate_auto_pick_key(
                first_top,
                required_first_choice,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                sleep_fn=sleep_fn,
            )
            current = _tap_configuration_row(
                target_frame,
                target_row,
                x=BAN_SELECTED_TOGGLE_X,
                action="set_first_perk_choice",
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                visible_fn=visible_fn,
                row_fn=row_fn,
                row_near_fn=row_near_fn,
                sleep_fn=sleep_fn,
                require_identity_after=False,
            )
            changed_fields.add("perk_first_choice")
            first_top = _scroll_configuration_top(
                current,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                sleep_fn=sleep_fn,
            )
            first_frames, current, first_complete = _capture_configuration_pages(
                first_top,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                sleep_fn=sleep_fn,
            )
            captured_first = dict(
                parse_selection_fn(
                    first_frames,
                    field="perk_first_choice",
                    source_complete=first_complete,
                    source_reason=(
                        "bottom edge verified"
                        if first_complete
                        else "configuration bottom edge was not verified"
                    ),
                )
            )
            first_choice_evidence = _first_choice_comparison(
                required_first_choice,
                captured_first,
            )

    if "perk_bans" in waived:
        bans_top = current
        captured_bans = _synthetic_configuration_capture(required_bans)
    else:
        bans_top = _select_and_scroll_top(
            current,
            field="perk_bans",
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            measure_selection_fn=measure_selection_fn,
            sleep_fn=sleep_fn,
        )
        bans_top, captured_bans = _capture_bans_with_ocr_retries(
            bans_top,
            capture_fn=capture_fn,
            detector=detector,
            visible_fn=visible_fn,
            row_fn=row_fn,
            sleep_fn=sleep_fn,
        )
        _record_initial_mapping_observation(
            mapping_observation_fn,
            "perk_bans",
            captured_bans,
        )
    if not _ban_capture_matches(required_bans, captured_bans):
        ban_quality = captured_bans.get("quality")
        if (
            not isinstance(ban_quality, Mapping)
            or ban_quality.get("valid") is not True
        ):
            raise HomePerkConfigurationError(
                "Ban Perks remained non-authoritative after local OCR retries"
            )
        log(
            "[HOME_PERKS] Verified Ban Perks differ from the strategy; "
            "starting guarded repair",
            "DEBUG",
        )
        announce_repair("perk_bans")
        bans_top, captured_bans = _repair_bans(
            bans_top,
            required_bans,
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            row_near_fn=row_near_fn,
            sleep_fn=sleep_fn,
        )
        changed_fields.add("perk_bans")

    if "perk_auto_pick_order" in waived:
        auto_top = bans_top
        auto_frames = [bans_top]
        current = bans_top
        captured_auto = _synthetic_configuration_capture(required_auto_pick)
    else:
        auto_top = _select_and_scroll_top(
            bans_top,
            field="perk_auto_pick_order",
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            measure_selection_fn=measure_selection_fn,
            sleep_fn=sleep_fn,
        )
        auto_frames, current, captured_auto = (
            _capture_ranked_order_with_ocr_retries(
                auto_top,
                ranking_count=len(required_auto_pick),
                capture_fn=capture_fn,
                detector=detector,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                sleep_fn=sleep_fn,
            )
        )
        _record_initial_mapping_observation(
            mapping_observation_fn,
            "perk_auto_pick_order",
            captured_auto,
        )
    evidence = evaluate_profile_perk_configuration(
        requirements,
        bans_frame=bans_top,
        auto_pick_frames=auto_frames,
        captured_bans=captured_bans,
        captured_auto_pick=captured_auto,
        row_fn=row_fn,
    )
    _mark_omitted_configuration_fields(evidence, waived)
    if first_choice_evidence is not None:
        evidence["perk_first_choice"] = first_choice_evidence
        if first_choice_evidence["valid"] is not True:
            evidence.setdefault("failed_checks", []).insert(
                0, "perk_first_choice"
            )
            evidence["valid"] = False
    if (
        evidence["perk_auto_pick_order"]["valid"] is not True
        and "perk_auto_pick_order" not in waived
        and _field_values_authoritatively_differ(
            evidence["perk_auto_pick_order"],
            ordered=True,
        )
    ):
        log(
            "[HOME_PERKS] Verified Auto Pick order differs from the "
            "strategy; starting guarded repair",
            "DEBUG",
        )
        announce_repair("perk_auto_pick_order")
        auto_frames, current, captured_auto = _repair_auto_pick_order(
            current,
            required_auto_pick,
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            row_near_fn=row_near_fn,
            observed_keys=evidence["perk_auto_pick_order"].get("observed"),
            sleep_fn=sleep_fn,
        )
        changed_fields.add("perk_auto_pick_order")
        evidence = evaluate_profile_perk_configuration(
            requirements,
            bans_frame=bans_top,
            auto_pick_frames=auto_frames,
            captured_bans=captured_bans,
            captured_auto_pick=captured_auto,
            row_fn=row_fn,
        )
        _mark_omitted_configuration_fields(evidence, waived)
        if first_choice_evidence is not None:
            evidence["perk_first_choice"] = first_choice_evidence
            if first_choice_evidence["valid"] is not True:
                evidence.setdefault("failed_checks", []).insert(
                    0, "perk_first_choice"
                )
                evidence["valid"] = False

    home = _close_to_home(
        current,
        capture_fn=capture_fn,
        detector=detector,
        detect_home_control_fn=detect_home_control_fn,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    failed_checks = [
        str(check_id)
        for check_id in evidence.get("failed_checks") or ()
        if str(check_id) not in waived
    ]
    evidence["blocking_failed_checks"] = failed_checks
    evidence["blocking_valid"] = not failed_checks
    for check_id in (
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
    ):
        if isinstance(evidence.get(check_id), dict):
            evidence[check_id]["changed"] = check_id in changed_fields
    failed_check = str(failed_checks[0]) if failed_checks else None
    reason = (
        "strategy Perk configuration verified"
        if not failed_checks
        else str(evidence[failed_check]["reason"])
        if failed_check and isinstance(evidence.get(failed_check), Mapping)
        else "strategy Perk configuration remained invalid"
    )
    result = HomePerkConfigurationResult(
        valid=not failed_checks,
        changed=bool(changed_fields),
        reason=reason,
        failed_check=failed_check,
        evidence=evidence,
        home_screenshot=home,
    )
    return _finish_home_perk_configuration(result) if operator_workflow else result


def _repair_bans(
    top: Frame,
    expected_keys: Sequence[str],
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    row_near_fn: Callable[..., Optional[dict[str, Any]]],
    sleep_fn: Callable[[float], None],
) -> tuple[Frame, dict[str, Any]]:
    expected = set(expected_keys)
    current, captured = _capture_stable_bans(
        top,
        capture_fn=capture_fn,
        detector=detector,
        visible_fn=visible_fn,
        row_fn=row_fn,
        sleep_fn=sleep_fn,
    )
    no_progress: dict[tuple[Any, ...], int] = {}

    for action_number in range(1, MAX_BAN_RECONCILIATION_ACTIONS + 1):
        if _ban_capture_matches(expected_keys, captured):
            return current, captured

        quality = captured.get("quality")
        selected = [
            dict(entry)
            for entry in captured.get("selected") or ()
            if isinstance(entry, Mapping)
        ]
        if not isinstance(quality, Mapping) or quality.get("valid") is not True:
            raise HomePerkConfigurationRepairExhausted(
                "Ban Perks selected block did not settle authoritatively"
            )
        selected_keys = {
            str(entry["key"])
            for entry in selected
            if entry.get("key") is not None
        }
        missing = [key for key in expected_keys if key not in selected_keys]
        extras = [
            entry
            for entry in selected
            if entry.get("key") is not None
            and entry.get("key") not in expected
        ]
        unknown = [entry for entry in selected if entry.get("key") is None]
        if unknown and missing:
            raise HomePerkConfigurationRepairExhausted(
                "Ban Perks selected block was ambiguous; an unrecognized row "
                "could be a required ban"
            )

        before_signature = _ban_capture_signature(captured)
        action_kind: str
        target: dict[str, Any]
        if extras or unknown:
            action_kind = "deselect"
            target = dict((extras or unknown)[0])
            current = _tap_configuration_row(
                current,
                target,
                x=BAN_SELECTED_TOGGLE_X,
                action=(
                    "perk_ban_deselect:"
                    f"{target.get('key') or 'unknown'}"
                ),
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                visible_fn=visible_fn,
                row_fn=row_fn,
                row_near_fn=row_near_fn,
                sleep_fn=sleep_fn,
                require_identity_after=False,
            )
        else:
            if not missing:
                raise HomePerkConfigurationRepairExhausted(
                    "Ban Perks stable readback had an unsupported duplicate state"
                )
            capacity = int(quality.get("capacity") or 0)
            if capacity <= 0 or len(selected) >= capacity:
                raise HomePerkConfigurationRepairExhausted(
                    "Ban Perks was full before all required bans were selected"
                )
            action_kind = "select"
            target = {
                "key": missing[0],
                "display_text": perk_configuration_label(missing[0]),
            }
            current = _select_available_ban(
                current,
                target,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                row_near_fn=row_near_fn,
                sleep_fn=sleep_fn,
            )

        current = _scroll_ban_configuration_top(
            current,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            sleep_fn=sleep_fn,
        )
        current, refreshed = _capture_stable_bans(
            current,
            capture_fn=capture_fn,
            detector=detector,
            visible_fn=visible_fn,
            row_fn=row_fn,
            sleep_fn=sleep_fn,
        )
        after_signature = _ban_capture_signature(refreshed)
        target_key = str(target.get("key") or "unknown")
        progress_key = (
            before_signature,
            action_kind,
            target_key,
        )
        if after_signature == before_signature:
            retries = no_progress.get(progress_key, 0)
            if retries >= MAX_BAN_NO_PROGRESS_RETRIES:
                raise HomePerkConfigurationRepairExhausted(
                    "Ban Perks made no stable progress after a guarded "
                    f"{action_kind} retry for "
                    f"{perk_configuration_label(target.get('key'))}"
                )
            no_progress[progress_key] = retries + 1
            log(
                "[HOME_PERKS] Ban Perks guarded input produced a stable "
                "no-op; replanning one local retry "
                f"({retries + 1}/{MAX_BAN_NO_PROGRESS_RETRIES}) "
                f"action={action_kind} "
                f"target={perk_configuration_label(target.get('key'))}",
                "DEBUG",
            )
        else:
            before_keys = set(_ban_capture_keys(captured))
            after_keys = set(_ban_capture_keys(refreshed))
            log(
                "[HOME_PERKS] Ban Perks stable transition verified; "
                f"action={action_kind} "
                f"target={perk_configuration_label(target.get('key'))} "
                f"removed={sorted(before_keys - after_keys)} "
                f"added={sorted(after_keys - before_keys)} "
                f"round={action_number}/{MAX_BAN_RECONCILIATION_ACTIONS}",
                "DEBUG",
            )
        captured = refreshed
        if _ban_capture_matches(expected_keys, captured):
            return current, captured

    raise HomePerkConfigurationRepairExhausted(
        "Ban Perks repair exceeded its bounded in-panel action budget"
    )


def _ban_capture_keys(captured: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(entry.get("key") or "")
        for entry in captured.get("selected") or ()
        if isinstance(entry, Mapping)
    )


def _ban_capture_signature(captured: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """Return comparable evidence only for a complete authoritative read."""

    quality = captured.get("quality")
    if not isinstance(quality, Mapping) or quality.get("valid") is not True:
        return None
    return (
        _ban_capture_keys(captured),
        int(quality.get("capacity") or 0),
        bool(quality.get("empty_slot_seen")),
        int(quality.get("selected_outline_count") or 0),
    )


def _capture_stable_bans(
    current: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    visible_fn: Callable[..., bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[Frame, dict[str, Any]]:
    """Require two matching authoritative Selected Perks snapshots."""

    captured = extract_configured_perk_bans(current, row_fn=row_fn)
    previous_signature = _ban_capture_signature(captured)
    for attempt in range(1, MAX_BAN_STABLE_CAPTURE_ATTEMPTS + 1):
        fresh = _fresh_perk_configuration_capture(
            capture_fn=capture_fn,
            detector=detector,
            visible_fn=visible_fn,
            sleep_fn=sleep_fn,
        )
        fresh_capture = extract_configured_perk_bans(fresh, row_fn=row_fn)
        fresh_signature = _ban_capture_signature(fresh_capture)
        if fresh_signature is not None and fresh_signature == previous_signature:
            return fresh, fresh_capture
        log(
            "[HOME_PERKS] Ban Perks Selected snapshot was not yet stable; "
            "confirming again in the current panel "
            f"({attempt}/{MAX_BAN_STABLE_CAPTURE_ATTEMPTS})",
            "DEBUG",
        )
        current = fresh
        captured = fresh_capture
        previous_signature = fresh_signature
    raise HomePerkConfigurationRepairExhausted(
        "Ban Perks selected block did not produce two matching "
        "authoritative snapshots"
    )


def _select_available_ban(
    current: Frame,
    target: Mapping[str, Any],
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    row_near_fn: Callable[..., Optional[dict[str, Any]]],
    sleep_fn: Callable[[float], None],
) -> Frame:
    """Find and select one missing Ban choice before the next replan."""

    for _ in range(BAN_AVAILABLE_START_SWIPES):
        current = _swipe_configuration(
            current,
            "gesture_targets.goto_next:perks",
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
    located: dict[str, Any] | None = None

    def target_stop(frame: Frame) -> Optional[str]:
        nonlocal located
        matches = [
            row
            for row in (
                semantic_perk_entry(raw) for raw in row_fn(frame)
            )
            if perk_entries_match(target, row)
        ]
        if len(matches) == 1:
            located = matches[0]
            return "ban_available_target_visible"
        if len(matches) > 1:
            raise HomePerkConfigurationRepairExhausted(
                "Ban Perks Available list exposed duplicate target rows for "
                f"{perk_configuration_label(target.get('key'))}"
            )
        return None

    capture = capture_scroll_to_edge(
        "gesture_targets.goto_next:perks",
        source_label=PERK_CONFIGURATION_INDICATOR,
        screenshot=current,
        progress_region=PERK_CONTENT_REGION,
        max_swipes=MAX_BAN_SCAN_SWIPES,
        settle_s=0.8,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
        stop_fn=target_stop,
    )
    if capture.reason == "ban_available_target_visible" and located is not None:
        current = capture.screenshots[-1] if capture.screenshots else current
        return _tap_configuration_row(
            current,
            located,
            x=BAN_TOGGLE_X,
            action=f"perk_ban_toggle:{located.get('key') or 'unknown'}",
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
            row_fn=row_fn,
            row_near_fn=row_near_fn,
            sleep_fn=sleep_fn,
            require_identity_after=True,
        )
    if not capture.success and capture.reason != "max_swipes_exceeded":
        raise HomePerkConfigurationRepairExhausted(
            "Ban Perks Available list scan failed while locating "
            f"{perk_configuration_label(target.get('key'))}: "
            f"{capture.reason}"
        )
    raise HomePerkConfigurationRepairExhausted(
        "could not locate Ban Perks choice: "
        f"{perk_configuration_label(target.get('key'))}"
    )


def _repair_auto_pick_order(
    current: Frame,
    expected_keys: Sequence[str],
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    row_near_fn: Callable[..., Optional[dict[str, Any]]],
    observed_keys: Sequence[str | None],
    sleep_fn: Callable[[float], None],
) -> tuple[list[Frame], Frame, dict[str, Any]]:
    if (
        len(observed_keys) != len(expected_keys)
        or any(key is None for key in observed_keys)
        or len(set(observed_keys)) != len(observed_keys)
    ):
        raise HomePerkConfigurationError(
            "Auto Pick repair requires one complete authoritative ranked order"
        )

    working_order = [str(key) for key in observed_keys]
    total_taps = 0
    semantic_resyncs = 0
    current = _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    desired_rank = 1
    while desired_rank <= len(expected_keys):
        key = expected_keys[desired_rank - 1]
        if working_order[desired_rank - 1] == key:
            desired_rank += 1
            continue

        cached_rank = (
            working_order.index(key) + 1
            if key in working_order
            else None
        )
        if cached_rank is not None and cached_rank < desired_rank:
            raise HomePerkConfigurationError(
                f"{perk_configuration_label(key)} appeared above its guarded "
                f"target rank {desired_rank}"
            )
        try:
            _rank, current, row = _locate_auto_pick_key(
                current,
                key,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                sleep_fn=sleep_fn,
                return_to_top=False,
            )
        except HomePerkConfigurationError as exc:
            if semantic_resyncs >= MAX_AUTO_PICK_SEMANTIC_RESYNCS:
                raise
            semantic_resyncs += 1
            log(
                "[HOME_PERKS] Auto Pick forward target acquisition lost its "
                "semantic viewport; recapturing the ranked order "
                f"({semantic_resyncs}/{MAX_AUTO_PICK_SEMANTIC_RESYNCS}) "
                f"target={perk_configuration_label(key)} reason={exc}",
                "DEBUG",
            )
            current, working_order = _recapture_auto_pick_semantic_order(
                current,
                ranking_count=len(expected_keys),
                capture_fn=capture_fn,
                detector=detector,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                sleep_fn=sleep_fn,
            )
            continue

        estimated_rank = cached_rank
        anchor_key = (
            expected_keys[desired_rank - 2]
            if desired_rank > 1
            else None
        )
        target_taps = 0
        restart_from_semantic_order = False
        while True:
            try:
                (
                    current,
                    row,
                    previous,
                    _following,
                    at_top,
                ) = _reacquire_auto_pick_move_context(
                    current,
                    key,
                    capture_fn=capture_fn,
                    visible_fn=visible_fn,
                    swipe_fn=swipe_fn,
                    row_fn=row_fn,
                    sleep_fn=sleep_fn,
                )
            except HomePerkConfigurationError as exc:
                if semantic_resyncs >= MAX_AUTO_PICK_SEMANTIC_RESYNCS:
                    raise HomePerkConfigurationRepairExhausted(
                        "Auto Pick local target context remained unavailable "
                        "after bounded semantic recovery; "
                        f"target={perk_configuration_label(key)}; "
                        f"predecessor={perk_configuration_label(anchor_key)}; "
                        f"reason={exc}"
                    ) from exc
                semantic_resyncs += 1
                log(
                    "[HOME_PERKS] Auto Pick local target context was lost; "
                    "recapturing the ranked order "
                    f"({semantic_resyncs}/{MAX_AUTO_PICK_SEMANTIC_RESYNCS}) "
                    f"target={perk_configuration_label(key)} reason={exc}",
                    "DEBUG",
                )
                current, working_order = _recapture_auto_pick_semantic_order(
                    current,
                    ranking_count=len(expected_keys),
                    capture_fn=capture_fn,
                    detector=detector,
                    visible_fn=visible_fn,
                    swipe_fn=swipe_fn,
                    row_fn=row_fn,
                    sleep_fn=sleep_fn,
                )
                desired_rank = 1
                restart_from_semantic_order = True
                break

            if (
                target_taps == 0
                and previous is not None
                and previous.get("key") is None
            ):
                (
                    current,
                    row,
                    previous,
                    _following,
                    at_top,
                ) = _retry_unknown_auto_pick_predecessor(
                    current,
                    key,
                    previous,
                    capture_fn=capture_fn,
                    detector=detector,
                    visible_fn=visible_fn,
                    swipe_fn=swipe_fn,
                    row_fn=row_fn,
                    sleep_fn=sleep_fn,
                )

            if target_taps == 0 and not _auto_pick_context_matches_order(
                working_order,
                key,
                previous,
                at_top=at_top,
            ):
                if semantic_resyncs >= MAX_AUTO_PICK_SEMANTIC_RESYNCS:
                    raise HomePerkConfigurationRepairExhausted(
                        "Auto Pick local row context remained inconsistent "
                        "with the authoritative ranked order"
                    )
                semantic_resyncs += 1
                log(
                    "[HOME_PERKS] Auto Pick local row context disagreed with "
                    "the cached ranked order; recapturing before input "
                    f"({semantic_resyncs}/{MAX_AUTO_PICK_SEMANTIC_RESYNCS}) "
                    f"target={perk_configuration_label(key)}",
                    "DEBUG",
                )
                current, working_order = _recapture_auto_pick_semantic_order(
                    current,
                    ranking_count=len(expected_keys),
                    capture_fn=capture_fn,
                    detector=detector,
                    visible_fn=visible_fn,
                    swipe_fn=swipe_fn,
                    row_fn=row_fn,
                    sleep_fn=sleep_fn,
                )
                desired_rank = 1
                restart_from_semantic_order = True
                break

            if anchor_key is not None and previous is not None and (
                previous.get("key") == anchor_key
            ):
                if (
                    estimated_rank is not None
                    and estimated_rank != desired_rank
                ):
                    log(
                        "[HOME_PERKS] Auto Pick local adjacency reconciled "
                        f"{perk_configuration_label(key)} rank estimate "
                        f"{estimated_rank} to target {desired_rank}",
                        "DEBUG",
                    )
                break
            if anchor_key is None and at_top:
                break
            if at_top:
                raise HomePerkConfigurationRepairExhausted(
                    f"{perk_configuration_label(key)} appeared at a proven "
                    "list top before its guarded predecessor "
                    f"{perk_configuration_label(anchor_key)}"
                )
            if previous is None:
                raise HomePerkConfigurationError(
                    f"could not identify the row immediately above "
                    f"{perk_configuration_label(key)}"
                )
            if total_taps >= MAX_AUTO_PICK_MOVE_TAPS:
                raise HomePerkConfigurationError(
                    "Auto Pick repair exceeded its bounded move budget"
                )
            displaced = previous
            current = _tap_configuration_row(
                current,
                row,
                x=AUTO_PICK_UP_X,
                action=f"auto_pick_move_up:{key}",
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                visible_fn=visible_fn,
                row_fn=row_fn,
                row_near_fn=row_near_fn,
                sleep_fn=sleep_fn,
                require_identity_after=False,
            )
            total_taps += 1
            target_taps += 1
            (
                current,
                row,
                previous,
                _following,
                at_top,
            ) = _confirm_auto_pick_local_swap(
                key,
                displaced,
                current=current,
                capture_fn=capture_fn,
                detector=detector,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                sleep_fn=sleep_fn,
            )
            prior_estimate = estimated_rank
            if estimated_rank is not None:
                estimated_rank = max(1, estimated_rank - 1)
            displaced_label = perk_configuration_label(
                str(displaced.get("key") or "") or None,
                str(displaced.get("display_text") or ""),
            )
            rank_estimate = (
                f"{prior_estimate}->{estimated_rank}"
                if prior_estimate is not None
                else "outside_cached_prefix"
            )
            log(
                "[HOME_PERKS] Auto Pick locally verified one upward swap; "
                f"perk={perk_configuration_label(key)} "
                f"rank_estimate={rank_estimate} "
                f"displaced={displaced_label}",
                "DEBUG",
            )
            if anchor_key is not None and previous is not None and (
                previous.get("key") == anchor_key
            ):
                break
            if anchor_key is None and at_top:
                break

        if restart_from_semantic_order:
            continue

        if key in working_order:
            working_order.remove(key)
        working_order.insert(desired_rank - 1, key)
        del working_order[len(expected_keys) :]
        if working_order[desired_rank - 1] != key:
            raise HomePerkConfigurationError(
                "Auto Pick semantic order did not accept a verified move"
            )
        desired_rank += 1

    final_top = _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    _frames, _current, final = _capture_ranked_order_with_ocr_retries(
        final_top,
        ranking_count=len(expected_keys),
        capture_fn=capture_fn,
        detector=detector,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        row_fn=row_fn,
        sleep_fn=sleep_fn,
    )
    final_keys = [entry.get("key") for entry in final["selected"]]
    if final["quality"]["valid"] is not True or final_keys != list(expected_keys):
        raise HomePerkConfigurationError(
            "Auto Pick order remained different after guarded moves"
        )
    return _frames, _current, final


def _auto_pick_context_matches_order(
    working_order: Sequence[str],
    key: str,
    previous: Mapping[str, Any] | None,
    *,
    at_top: bool,
) -> bool:
    """Check fresh local adjacency against the cached ranked prefix."""

    if key in working_order:
        index = working_order.index(key)
        if index == 0:
            return at_top
        return bool(
            previous is not None
            and previous.get("key") == working_order[index - 1]
        )

    if at_top or previous is None or previous.get("key") is None:
        return False
    previous_key = str(previous["key"])
    return (
        previous_key not in working_order
        or previous_key == working_order[-1]
    )


def _retry_unknown_auto_pick_predecessor(
    current: Frame,
    key: str,
    previous: Mapping[str, Any],
    *,
    capture_fn: Capture,
    detector: Detector,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[
    Frame,
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    bool,
]:
    """Retry an unknown adjacent row locally; a top-prefix scan cannot help."""

    predecessor_text = str(
        previous.get("display_text") or previous.get("text_raw") or ""
    ).strip()
    last_reason = "local predecessor remained unrecognized"
    for retry in range(1, MAX_AUTO_PICK_LOCAL_CONTEXT_RETRIES + 1):
        log(
            "[HOME_PERKS] Auto Pick predecessor OCR was unrecognized; "
            "retrying fresh local context without rescanning the ranked "
            f"prefix ({retry}/{MAX_AUTO_PICK_LOCAL_CONTEXT_RETRIES}) "
            f"target={perk_configuration_label(key)} "
            f"predecessor={predecessor_text!r}",
            "DEBUG",
        )
        fresh = _fresh_perk_configuration_capture(
            capture_fn=capture_fn,
            detector=detector,
            visible_fn=visible_fn,
            sleep_fn=sleep_fn,
        )
        try:
            context = _reacquire_auto_pick_move_context(
                fresh,
                key,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                sleep_fn=sleep_fn,
            )
        except HomePerkConfigurationError as exc:
            last_reason = str(exc)
            continue
        current, row, predecessor, following, at_top = context
        if predecessor is not None:
            predecessor_text = str(
                predecessor.get("display_text")
                or predecessor.get("text_raw")
                or ""
            ).strip()
            if predecessor.get("key") is not None:
                return current, row, predecessor, following, at_top
        last_reason = "local predecessor remained unrecognized"
    raise HomePerkConfigurationRepairExhausted(
        "Auto Pick could not identify the row immediately above "
        f"{perk_configuration_label(key)} after local OCR retries; "
        f"predecessor={predecessor_text!r}; reason={last_reason}"
    )


def _visible_auto_pick_move_context(
    frame: Frame,
    key: str,
    *,
    row_fn: RowsFn,
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
] | None:
    """Return a target row and its immediate visible neighbors."""

    rows = sorted(
        (semantic_perk_entry(raw) for raw in row_fn(frame)),
        key=lambda entry: int(entry.get("top") or 0),
    )
    matches = [
        index
        for index, entry in enumerate(rows)
        if entry.get("key") == key
    ]
    if len(matches) != 1:
        return None
    index = matches[0]
    return (
        rows[index],
        rows[index - 1] if index > 0 else None,
        rows[index + 1] if index + 1 < len(rows) else None,
    )


def _reacquire_auto_pick_move_context(
    current: Frame,
    key: str,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
    require_previous: bool = True,
) -> tuple[
    Frame,
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    bool,
]:
    """Reacquire a moving row locally, scrolling upward only as needed."""

    found_context: tuple[
        dict[str, Any],
        dict[str, Any] | None,
        dict[str, Any] | None,
    ] | None = None

    def context_stop(frame: Frame) -> Optional[str]:
        nonlocal found_context
        context = _visible_auto_pick_move_context(frame, key, row_fn=row_fn)
        if context is not None and (
            context[1] is not None or not require_previous
        ):
            found_context = context
            return "auto_pick_move_context_visible"
        return None

    capture = scroll_to_edge(
        "gesture_targets.goto_previous:perks",
        source_label=PERK_CONFIGURATION_INDICATOR,
        screenshot=current,
        progress_region=PERK_CONTENT_REGION,
        max_swipes=MAX_AUTO_PICK_LOCAL_REACQUIRE_SWIPES + 1,
        settle_s=0.8,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
        stop_fn=context_stop,
    )
    latest = capture.screenshot if capture.screenshot is not None else current
    if capture.reason == "auto_pick_move_context_visible" and found_context:
        return latest, *found_context, False
    raise HomePerkConfigurationError(
        "Auto Pick local repair could not reacquire "
        f"{perk_configuration_label(key)} with a visible predecessor while "
        f"scrolling upward; reason={capture.reason}"
    )


def _confirm_auto_pick_local_swap(
    key: str,
    displaced: Mapping[str, Any],
    *,
    current: Frame,
    capture_fn: Capture,
    detector: Detector,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[
    Frame,
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    bool,
]:
    """Confirm one local adjacent swap without rescanning from list top."""

    for attempt in range(2):
        context = _visible_auto_pick_move_context(current, key, row_fn=row_fn)
        if (
            context is not None
            and context[2] is not None
            and perk_entries_match(displaced, context[2])
        ):
            return current, *context, False
        if attempt == 0:
            log(
                "[HOME_PERKS] Auto Pick local swap was not settled on the "
                "first post-input read; confirming from a fresh screenshot",
                "DEBUG",
            )
            sleep_fn(0.8)
            fresh = capture_fn()
            if (
                fresh is None
                or detector(fresh).get("state") != "PERKS"
                or not visible_fn(
                    PERK_CONFIGURATION_INDICATOR,
                    screenshot=fresh,
                )
            ):
                raise HomePerkConfigurationError(
                    "Perks configuration identity lost while confirming an "
                    "Auto Pick move"
                )
            current = fresh

    (
        current,
        row,
        previous,
        following,
        at_top,
    ) = _reacquire_auto_pick_move_context(
        current,
        key,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        row_fn=row_fn,
        sleep_fn=sleep_fn,
        require_previous=False,
    )
    if following is None or not perk_entries_match(displaced, following):
        raise HomePerkConfigurationError(
            f"{perk_configuration_label(key)} did not make one verified "
            "local upward swap"
        )
    return current, row, previous, following, at_top


def _locate_auto_pick_key(
    current: Frame,
    key: str,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
    return_to_top: bool = True,
) -> tuple[int | None, Frame, dict[str, Any]]:
    if return_to_top:
        current = _scroll_configuration_top(
            current,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
    ordered: list[dict[str, Any]] = []
    located: tuple[int | None, dict[str, Any]] | None = None

    def target_stop(frame: Frame) -> Optional[str]:
        nonlocal located
        if not return_to_top:
            matches = []
            for raw in row_fn(frame):
                row = semantic_perk_entry(raw)
                if row.get("key") == key:
                    matches.append(row)
            if len(matches) == 1:
                located = (None, matches[0])
                return "auto_pick_key_visible"
            return None
        for raw in row_fn(frame):
            row = semantic_perk_entry(raw)
            if any(perk_entries_match(existing, row) for existing in ordered):
                continue
            ordered.append(row)
            if row.get("key") == key:
                located = (len(ordered), row)
                return "auto_pick_key_visible"
        return None

    capture = capture_scroll_to_edge(
        "gesture_targets.goto_next:perks",
        source_label=PERK_CONFIGURATION_INDICATOR,
        screenshot=current,
        progress_region=PERK_CONTENT_REGION,
        max_swipes=MAX_AUTO_PICK_SCAN_SWIPES,
        settle_s=0.8,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
        stop_fn=target_stop,
    )
    if capture.reason == "auto_pick_key_visible" and located is not None:
        found_frame = capture.screenshots[-1] if capture.screenshots else current
        return located[0], found_frame, located[1]
    if not capture.success and capture.reason != "max_swipes_exceeded":
        raise HomePerkConfigurationError(
            f"Auto Pick list scan failed while locating "
            f"{perk_configuration_label(key)}: {capture.reason}"
        )
    raise HomePerkConfigurationError(
        f"Auto Pick list did not expose {perk_configuration_label(key)}"
    )


def _tap_configuration_row(
    current: Frame,
    row: Mapping[str, Any],
    *,
    x: int,
    action: str,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    visible_fn: Callable[..., bool],
    row_fn: RowsFn,
    row_near_fn: Callable[..., Optional[dict[str, Any]]],
    sleep_fn: Callable[[float], None],
    require_identity_after: bool = True,
) -> Frame:
    expected = (
        dict(row)
        if "key" in row
        else semantic_perk_entry(row)
    )
    authority = capture_fn()
    if (
        authority is None
        or detector(authority).get("state") != "PERKS"
        or not visible_fn(PERK_CONFIGURATION_INDICATOR, screenshot=authority)
    ):
        raise HomePerkConfigurationError(
            f"Perks configuration identity lost before {action}"
        )
    matches = [
        semantic_perk_entry(candidate)
        for candidate in row_fn(authority)
        if perk_entries_match(
            expected,
            semantic_perk_entry(candidate),
        )
    ]
    if len(matches) != 1:
        raise HomePerkConfigurationError(
            f"Perk configuration target was not uniquely reacquired before "
            f"{action}"
        )
    current = authority
    row = matches[0]
    top = int(row["top"])
    bottom = int(row["bottom"])
    center = (top + bottom) // 2
    region_x = 850 if x > 800 else 239
    region_width = 130 if x > 800 else 601

    def verifies(frame: Frame) -> bool:
        if (
            detector(frame).get("state") != "PERKS"
            or not visible_fn(PERK_CONFIGURATION_INDICATOR, screenshot=frame)
        ):
            return False
        candidate = row_near_fn(frame, center)
        return bool(
            candidate
            and perk_entries_match(expected, semantic_perk_entry(candidate))
        )

    if not safe_tap_fn(
        (x, center),
        dispatch="now",
        log_label=f"home_preflight:{action}",
        verification=TapVerification(
            screenshot=current,
            target_region=(region_x, top, region_width, bottom - top + 1),
            description=f"{action}:fresh_perk_row",
            verifier=verifies,
        ),
    ):
        raise HomePerkConfigurationError(f"guarded Perk action failed: {action}")
    sleep_fn(0.45)
    fresh = capture_fn()
    if fresh is None or detector(fresh).get("state") != "PERKS":
        raise HomePerkConfigurationError(f"Perks screen lost after {action}")
    if not visible_fn(PERK_CONFIGURATION_INDICATOR, screenshot=fresh):
        raise HomePerkConfigurationError(
            f"Perks configuration identity lost after {action}"
        )
    if _region_difference(
        current,
        fresh,
        (239, top, 733, bottom - top + 1),
    ) <= 0.10:
        raise HomePerkConfigurationError(
            f"Perk configuration row did not change after {action}"
        )
    if not require_identity_after:
        return fresh

    candidate = row_near_fn(fresh, center)
    if not candidate or not perk_entries_match(
        expected,
        semantic_perk_entry(candidate),
    ):
        raise HomePerkConfigurationError(
            f"Perk configuration target changed identity after {action}"
        )
    return fresh


def _open_configuration(
    home: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    if not detect_home_perks_configuration_control(home)["visible"]:
        raise HomePerkConfigurationError(
            "Home Perks menu item was not independently verified"
        )
    if not safe_tap_fn(
        "navigation.home_perks_configuration",
        dispatch="now",
        log_label="home_preflight:perks_configuration",
        verification=TapVerification(
            screenshot=home,
            target_region=HOME_PERKS_CONTROL_REGION,
            description="home_perks_configuration:visible",
            verifier=lambda frame: (
                detector(frame).get("state") == "HOME_SCREEN"
                and detect_home_perks_configuration_control(frame)["visible"]
            ),
        ),
    ):
        raise HomePerkConfigurationError("Home Perks configuration tap failed")
    perks = _wait_for_state(
        "PERKS",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    if not visible_fn(PERK_CONFIGURATION_INDICATOR, screenshot=perks):
        raise HomePerkConfigurationError(
            "Perks destination was not the Home configuration panel"
        )
    return perks


def _select_and_scroll_top(
    current: Frame,
    *,
    field: str,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    measure_selection_fn: Callable[..., Any],
    sleep_fn: Callable[[float], None],
) -> Frame:
    label, region = PERK_TABS[field]
    selection = measure_selection_fn(current, region)
    if not selection.selected:
        if (
            detector(current).get("state") != "PERKS"
            or not visible_fn(PERK_CONFIGURATION_INDICATOR, screenshot=current)
        ):
            raise HomePerkConfigurationError(
                f"lost Perks configuration before {label}"
            )
        x, y, width, height = region
        if not safe_tap_fn(
            (x + width // 2, y + height // 2),
            dispatch="now",
            log_label=f"home_preflight:perk_tab:{field}",
            verification=TapVerification(
                screenshot=current,
                target_region=region,
                description=f"perk_configuration_tab:{field}",
                verifier=lambda frame: (
                    detector(frame).get("state") == "PERKS"
                    and visible_fn(
                        PERK_CONFIGURATION_INDICATOR,
                        screenshot=frame,
                    )
                    and not measure_selection_fn(frame, region).selected
                ),
            ),
        ):
            raise HomePerkConfigurationError(f"{label} tab tap failed")
        for _ in range(8):
            sleep_fn(0.25)
            fresh = capture_fn()
            if fresh is None:
                continue
            if detector(fresh).get("state") != "PERKS":
                raise HomePerkConfigurationError(
                    f"lost Perks configuration after {label}"
                )
            if measure_selection_fn(fresh, region).selected:
                current = fresh
                break
        else:
            raise HomePerkConfigurationError(
                f"{label} tab did not become selected"
            )
    if field == "perk_bans":
        return _scroll_ban_configuration_top(
            current,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            sleep_fn=sleep_fn,
        )
    return _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )


def _capture_configuration_pages(
    top: Frame,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    sleep_fn: Callable[[float], None],
) -> tuple[list[Frame], Frame, bool]:
    """Capture one complete configuration tab through a verified bottom edge."""

    capture = capture_scroll_to_edge(
        "gesture_targets.goto_next:perks",
        source_label=PERK_CONFIGURATION_INDICATOR,
        screenshot=top,
        progress_region=PERK_CONTENT_REGION,
        max_swipes=MAX_AUTO_PICK_SCAN_SWIPES,
        settle_s=0.8,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    if not capture.success and capture.reason != "max_swipes_exceeded":
        raise HomePerkConfigurationError(
            f"Perks configuration page capture failed: {capture.reason}"
        )
    frames = list(capture.screenshots)
    current = frames[-1] if frames else top
    return frames, current, capture.success and capture.reason == "edge_reached"


def _first_choice_comparison(
    expected: str,
    captured: Mapping[str, Any],
) -> dict[str, Any]:
    selected = captured.get("selected")
    selected_rows = selected if isinstance(selected, list) else []
    observed = [
        str(item.get("key"))
        for item in selected_rows
        if isinstance(item, Mapping) and item.get("key") is not None
    ]
    quality = captured.get("quality")
    capture_valid = bool(
        isinstance(quality, Mapping) and quality.get("valid") is True
    )
    valid = bool(capture_valid and observed == [expected])
    if not capture_valid:
        reason = "First Perk Choice capture was incomplete or ambiguous"
    elif observed != [expected]:
        reason = "First Perk Choice did not match the strategy"
    else:
        reason = "First Perk Choice matched the strategy"
    return {
        "boundary": HomeBattleControl.NEW_BATTLE.value,
        "checked": True,
        "valid": valid,
        "ordered": True,
        "expected": expected,
        "expected_label": perk_configuration_label(expected),
        "observed": observed[0] if len(observed) == 1 else observed,
        "reason": reason,
        "capture": dict(captured),
    }


def _record_initial_mapping_observation(
    callback: Optional[Callable[[str, Mapping[str, Any]], Any]],
    check_id: str,
    captured: Mapping[str, Any],
) -> None:
    """Report only complete semantic UI evidence before any repair input."""

    if callback is None:
        return
    quality = captured.get("quality")
    selected = captured.get("selected")
    if (
        not isinstance(quality, Mapping)
        or quality.get("valid") is not True
        or not isinstance(selected, list)
        or not selected
    ):
        return
    values: list[str] = []
    for item in selected:
        if not isinstance(item, Mapping) or item.get("key") is None:
            return
        value = str(item["key"]).strip()
        if not value:
            return
        values.append(value)
    if check_id == "perk_first_choice":
        locators = {"selected": values[0]} if len(values) == 1 else {}
    else:
        locators = {
            f"rank:{index}": value for index, value in enumerate(values)
        }
    try:
        evidence = build_mapping_candidate_ui_evidence(
            check_id,
            canonical_values=values,
            locator_values=locators,
        )
        callback(check_id, evidence)
    except Exception as exc:
        # Discovery is diagnostic only and must not alter setup authority.
        log(
            "[PLAYER_SAVE_MAPPING] Initial Perk observation callback failed: "
            f"check={check_id} reason={exc}",
            "DEBUG",
        )


def _synthetic_configuration_capture(
    expected: Sequence[str],
) -> dict[str, Any]:
    """Stand in only for a field intentionally satisfied outside this UI pass."""

    return {
        "selected": [{"key": str(value)} for value in expected],
        "quality": {
            "valid": True,
            "source_complete": True,
            "source_reason": "field omitted from this UI pass",
        },
    }


def _mark_omitted_configuration_fields(
    evidence: dict[str, Any],
    omitted: set[str],
) -> None:
    """Keep skipped tabs out of UI evidence instead of claiming observation."""

    for check_id in ("perk_bans", "perk_auto_pick_order"):
        if check_id not in omitted:
            continue
        evidence[check_id] = {
            "boundary": HomeBattleControl.NEW_BATTLE.value,
            "checked": False,
            "valid": True,
            "reason": "field omitted from this UI pass",
        }
    evidence["failed_checks"] = [
        check_id
        for check_id in evidence.get("failed_checks") or ()
        if check_id not in omitted
    ]
    evidence["valid"] = not evidence["failed_checks"]


def _capture_bans_with_ocr_retries(
    current: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    visible_fn: Callable[..., bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[Frame, dict[str, Any]]:
    """Retry a non-authoritative Ban read without leaving its current tab."""

    captured = extract_configured_perk_bans(current, row_fn=row_fn)
    for retry in range(1, MAX_PERK_OCR_RETRIES + 1):
        if not _capture_quality_requests_ocr_retry(captured):
            break
        log(
            "[HOME_PERKS] Ban Perks OCR was non-authoritative; "
            f"retrying a fresh local capture ({retry}/"
            f"{MAX_PERK_OCR_RETRIES}) "
            f"{_ocr_retry_detail(captured)}",
            "DEBUG",
        )
        current = _fresh_perk_configuration_capture(
            capture_fn=capture_fn,
            detector=detector,
            visible_fn=visible_fn,
            sleep_fn=sleep_fn,
        )
        captured = extract_configured_perk_bans(current, row_fn=row_fn)
    return current, captured


def _capture_ranked_order_with_ocr_retries(
    top: Frame,
    *,
    ranking_count: int,
    capture_fn: Capture,
    detector: Detector,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[list[Frame], Frame, dict[str, Any]]:
    """Retry an uncertain ranked read locally before returning to Home."""

    current_top = top
    frames: list[Frame] = []
    current = top
    captured: dict[str, Any] = {}
    for attempt in range(MAX_PERK_OCR_RETRIES + 1):
        frames, current = _capture_ranked_frames(
            current_top,
            ranking_count=ranking_count,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            sleep_fn=sleep_fn,
        )
        captured = extract_ranked_auto_pick_order(
            frames,
            ranking_count=ranking_count,
            row_fn=row_fn,
        )
        if (
            not _capture_quality_requests_ocr_retry(captured)
            or attempt >= MAX_PERK_OCR_RETRIES
        ):
            break
        retry = attempt + 1
        log(
            "[HOME_PERKS] Auto Pick OCR was non-authoritative; "
            f"retrying locally from list top ({retry}/"
            f"{MAX_PERK_OCR_RETRIES}) "
            f"{_ocr_retry_detail(captured)}",
            "DEBUG",
        )
        fresh = _fresh_perk_configuration_capture(
            capture_fn=capture_fn,
            detector=detector,
            visible_fn=visible_fn,
            sleep_fn=sleep_fn,
        )
        current_top = _scroll_configuration_top(
            fresh,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
    return frames, current, captured


def _recapture_auto_pick_semantic_order(
    current: Frame,
    *,
    ranking_count: int,
    capture_fn: Capture,
    detector: Detector,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[Frame, list[str]]:
    """Rebuild planning state after a pre-input viewport or OCR conflict."""

    top = _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    _frames, scanned, captured = _capture_ranked_order_with_ocr_retries(
        top,
        ranking_count=ranking_count,
        capture_fn=capture_fn,
        detector=detector,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        row_fn=row_fn,
        sleep_fn=sleep_fn,
    )
    selected = [
        entry
        for entry in captured.get("selected") or ()
        if isinstance(entry, Mapping)
    ]
    observed = [entry.get("key") for entry in selected]
    quality = captured.get("quality")
    if (
        not isinstance(quality, Mapping)
        or quality.get("valid") is not True
        or len(observed) != ranking_count
        or any(key is None for key in observed)
        or len(set(observed)) != len(observed)
    ):
        raise HomePerkConfigurationError(
            "Auto Pick semantic resynchronization was incomplete or ambiguous"
        )
    refreshed_top = _scroll_configuration_top(
        scanned,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    return refreshed_top, [str(key) for key in observed]


def _fresh_perk_configuration_capture(
    *,
    capture_fn: Capture,
    detector: Detector,
    visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    sleep_fn(PERK_OCR_RETRY_SETTLE_SECONDS)
    fresh = capture_fn()
    if (
        fresh is None
        or detector(fresh).get("state") != "PERKS"
        or not visible_fn(PERK_CONFIGURATION_INDICATOR, screenshot=fresh)
    ):
        raise HomePerkConfigurationError(
            "Perks configuration identity lost during local OCR retry"
        )
    return fresh


def _capture_quality_requests_ocr_retry(
    captured: Mapping[str, Any],
) -> bool:
    quality = captured.get("quality")
    return bool(
        isinstance(quality, Mapping)
        and quality.get("valid") is not True
        and quality.get("ocr_retry_recommended") is True
    )


def _ocr_retry_detail(captured: Mapping[str, Any]) -> str:
    quality = captured.get("quality")
    if not isinstance(quality, Mapping):
        return "reason=capture_quality_unavailable"
    candidates = [
        item
        for item in quality.get("closest_matches") or ()
        if isinstance(item, Mapping)
        and item.get("retry_recommended") is True
    ]
    closest = ", ".join(
        f"{item.get('display_text')!r}->"
        f"{item.get('suggested_label') or item.get('suggested_key')} "
        f"score={float(item.get('score') or 0.0):.3f} "
        f"margin={float(item.get('margin') or 0.0):.3f}"
        for item in candidates[:3]
    )
    warnings = "; ".join(str(item) for item in quality.get("warnings") or ())
    parts = []
    if closest:
        parts.append(f"closest=[{closest}]")
    if warnings:
        parts.append(f"warnings={warnings}")
    return " ".join(parts) or "reason=uncertain_ocr"


def _capture_ranked_frames(
    top: Frame,
    *,
    ranking_count: int,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[list[Frame], Frame]:
    observed_frames: list[Frame] = []

    def ranked_stop(frame: Frame) -> Optional[str]:
        observed_frames.append(frame)
        captured = extract_ranked_auto_pick_order(
            observed_frames,
            ranking_count=ranking_count,
            row_fn=row_fn,
        )
        if len(captured["selected"]) >= ranking_count:
            return "auto_pick_ranking_count_reached"
        if captured["quality"].get("ranking_boundary_seen") is True:
            return "auto_pick_ranking_boundary_seen"
        return None

    capture = capture_scroll_to_edge(
        "gesture_targets.goto_next:perks",
        source_label=PERK_CONFIGURATION_INDICATOR,
        screenshot=top,
        progress_region=PERK_CONTENT_REGION,
        max_swipes=MAX_AUTO_PICK_SCAN_SWIPES,
        settle_s=0.8,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
        stop_fn=ranked_stop,
    )
    if not capture.success and capture.reason != "max_swipes_exceeded":
        raise HomePerkConfigurationError(
            f"Auto Pick ranked scan failed: {capture.reason}"
        )
    frames = list(capture.screenshots)
    current = frames[-1] if frames else top
    return frames, current


def _scroll_ban_configuration_top(
    current: Frame,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> Frame:
    """Reach the Ban tab's complete outlined Selected Perks block."""

    def selected_block_stop(frame: Frame) -> Optional[str]:
        captured = extract_configured_perk_bans(frame, row_fn=row_fn)
        quality = captured.get("quality")
        if (
            isinstance(quality, Mapping)
            and quality.get("selected_block_complete") is True
        ):
            return "ban_selected_block_complete"
        return None

    return _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
        stop_fn=selected_block_stop,
    )


def _scroll_configuration_top(
    current: Frame,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    sleep_fn: Callable[[float], None],
    stop_fn: Optional[ScrollStopFn] = None,
) -> Frame:
    result = scroll_to_edge(
        "gesture_targets.goto_top:perks",
        source_label=PERK_CONFIGURATION_INDICATOR,
        screenshot=current,
        progress_region=PERK_CONTENT_REGION,
        max_swipes=8,
        settle_s=0.8,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
        stop_fn=stop_fn,
    )
    if not result.success or result.screenshot is None:
        raise HomePerkConfigurationError(
            f"Perks top scroll failed: {result.reason}"
        )
    return result.screenshot


def _swipe_configuration(
    current: Frame,
    key: str,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    result = guarded_swipe(
        key,
        source_label=PERK_CONFIGURATION_INDICATOR,
        screenshot=current,
        settle_s=0.8,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    if not result.success or result.screenshot is None:
        raise HomePerkConfigurationError(
            f"Perks scroll failed: {result.reason}"
        )
    return result.screenshot


def _close_to_home(
    current: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    detect_home_control_fn: Callable[[Frame], Any],
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    if not tap_visible_fn("buttons.close:perks", screenshot=current, retries=1):
        raise HomePerkConfigurationError(
            "Perks configuration close button was not visible"
        )
    return _wait_for_new_battle_home(
        capture_fn=capture_fn,
        detector=detector,
        detect_home_control_fn=detect_home_control_fn,
        sleep_fn=sleep_fn,
    )


def _wait_for_new_battle_home(
    *,
    capture_fn: Capture,
    detector: Detector,
    detect_home_control_fn: Callable[[Frame], Any],
    sleep_fn: Callable[[float], None],
    attempts: int = 24,
) -> Frame:
    """Wait through the Home transition until its battle control is readable."""

    home_seen = False
    last_control = HomeBattleControl.UNKNOWN
    for attempt in range(attempts):
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == "HOME_SCREEN":
            home_seen = True
            home = detect_home_control_fn(frame)
            last_control = home.control
            if last_control is HomeBattleControl.NEW_BATTLE:
                return frame
            if last_control is not HomeBattleControl.UNKNOWN:
                raise HomePerkConfigurationError(
                    "Perk configuration requires NEW_BATTLE, got "
                    f"{last_control.value}"
                )
        if attempt < attempts - 1:
            sleep_fn(0.25)
    if home_seen:
        raise HomePerkConfigurationError(
            f"Perk configuration requires NEW_BATTLE, got {last_control.value}"
        )
    raise HomePerkConfigurationError("timed out waiting for HOME_SCREEN")


def _require_new_battle_home(
    frame: Frame,
    detector: Detector,
    detect_home_control_fn: Callable[[Frame], Any],
) -> None:
    if detector(frame).get("state") != "HOME_SCREEN":
        raise HomePerkConfigurationError("Perk configuration requires Home")
    home = detect_home_control_fn(frame)
    if home.control is not HomeBattleControl.NEW_BATTLE:
        raise HomePerkConfigurationError(
            f"Perk configuration requires NEW_BATTLE, got {home.control.value}"
        )


def _wait_for_state(
    state: str,
    *,
    capture_fn: Capture,
    detector: Detector,
    sleep_fn: Callable[[float], None],
    attempts: int = 24,
) -> Frame:
    for _ in range(attempts):
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == state:
            return frame
        sleep_fn(0.25)
    raise HomePerkConfigurationError(f"timed out waiting for {state}")


def _content_difference(before: Frame, after: Frame) -> float:
    return _region_difference(before, after, PERK_CONTENT_REGION)


def _ban_capture_matches(
    expected_keys: Sequence[str],
    captured: Mapping[str, Any],
) -> bool:
    quality = captured.get("quality")
    selected = [
        entry
        for entry in captured.get("selected") or ()
        if isinstance(entry, Mapping)
    ]
    observed = [entry.get("key") for entry in selected]
    return bool(
        isinstance(quality, Mapping)
        and quality.get("valid") is True
        and all(key is not None for key in observed)
        and len(observed) == len(expected_keys)
        and set(observed) == set(expected_keys)
    )


def _field_values_authoritatively_differ(
    evidence: Mapping[str, Any],
    *,
    ordered: bool,
) -> bool:
    expected = list(evidence.get("expected") or ())
    observed = list(evidence.get("observed") or ())
    capture = evidence.get("capture")
    quality = (
        capture.get("quality")
        if isinstance(capture, Mapping)
        else None
    )
    if (
        not isinstance(quality, Mapping)
        or quality.get("valid") is not True
        or len(observed) != len(expected)
        or any(key is None for key in observed)
    ):
        return False
    if ordered:
        return observed != expected
    return set(observed) != set(expected)


def _region_difference(
    before: Frame,
    after: Frame,
    region: tuple[int, int, int, int],
) -> float:
    if before.shape != after.shape:
        return float("inf")
    x, y, width, height = region
    before_crop = before[y : y + height, x : x + width]
    after_crop = after[y : y + height, x : x + width]
    if before_crop.shape != after_crop.shape or before_crop.size == 0:
        return float("inf")
    return float(
        np.abs(
            before_crop.astype(np.int16) - after_crop.astype(np.int16)
        ).mean()
    )


__all__ = [
    "HOME_PERKS_CONTROL_REGION",
    "HomePerkConfigurationError",
    "HomePerkConfigurationRepairExhausted",
    "HomePerkConfigurationResult",
    "PERK_CONFIGURATION_INDICATOR",
    "PERK_TABS",
    "detect_home_perks_configuration_control",
    "ensure_home_perk_configuration",
]
