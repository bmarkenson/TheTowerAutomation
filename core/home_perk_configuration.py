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
    perk_configuration_label,
    perk_entries_match,
    semantic_perk_entry,
)
from core.scrolling import guarded_swipe, scroll_to_edge
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.workshop_preset import measure_preset_slot_selection
from utils.logger import log, log_action_intent, log_result


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]
RowsFn = Callable[[Frame], list[dict[str, Any]]]

PERK_CONFIGURATION_INDICATOR = "indicators.perks_configuration"
PERK_CONTENT_REGION = (100, 420, 880, 1330)
HOME_PERKS_CONTROL_REGION = (985, 475, 90, 100)
MIN_HOME_PERKS_GRAY_PIXELS = 800
PERK_TABS = {
    "perk_bans": ("Ban Perks", (436, 210, 210, 90)),
    "perk_auto_pick_order": ("Auto Pick", (650, 210, 210, 90)),
}

BAN_AVAILABLE_START_SWIPES = 2
MAX_BAN_SCAN_SWIPES = 14
MAX_AUTO_PICK_SCAN_SWIPES = 20
MAX_AUTO_PICK_MOVE_TAPS = 300
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


def _finish_home_perk_configuration(
    result: HomePerkConfigurationResult,
) -> HomePerkConfigurationResult:
    """Emit the terminal result for one Home Perk configuration pass."""

    changed_fields = [
        check_id
        for check_id in ("perk_bans", "perk_auto_pick_order")
        if isinstance(result.evidence.get(check_id), Mapping)
        and result.evidence[check_id].get("changed") is True
    ]
    changed_labels = {
        "perk_bans": "Ban Perks",
        "perk_auto_pick_order": "Auto Pick order",
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
    measure_selection_fn: Callable[..., Any] = measure_preset_slot_selection,
    waived_fields: Sequence[str] = (),
    sleep_fn: Callable[[float], None] = time.sleep,
    operator_workflow: bool = True,
) -> HomePerkConfigurationResult:
    """Verify and restore the strategy's Ban and Auto Pick lists at Home."""

    required_bans, required_auto_pick = (
        normalize_perk_configuration_requirements(requirements)
    )
    waived = {
        str(field)
        for field in waived_fields
        if str(field) in {"perk_bans", "perk_auto_pick_order"}
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
    bans_top = _select_and_scroll_top(
        perks,
        field="perk_bans",
        capture_fn=capture_fn,
        detector=detector,
        safe_tap_fn=safe_tap_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        measure_selection_fn=measure_selection_fn,
        sleep_fn=sleep_fn,
    )
    captured_bans = extract_configured_perk_bans(
        bans_top,
        row_fn=row_fn,
    )
    if (
        "perk_bans" not in waived
        and not _ban_capture_matches(required_bans, captured_bans)
    ):
        log(
            "[HOME_PERKS] Verified Ban Perks differ from the strategy; "
            "starting guarded repair",
            "DEBUG",
        )
        bans_top = _repair_bans(
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

    auto_top = _select_and_scroll_top(
        bans_top,
        field="perk_auto_pick_order",
        capture_fn=capture_fn,
        detector=detector,
        safe_tap_fn=safe_tap_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        measure_selection_fn=measure_selection_fn,
        sleep_fn=sleep_fn,
    )
    auto_frames, current = _capture_ranked_frames(
        auto_top,
        ranking_count=len(required_auto_pick),
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        row_fn=row_fn,
        sleep_fn=sleep_fn,
    )
    evidence = evaluate_profile_perk_configuration(
        requirements,
        bans_frame=bans_top,
        auto_pick_frames=auto_frames,
        row_fn=row_fn,
    )
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
        current = _repair_auto_pick_order(
            auto_top,
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
        auto_top = _scroll_configuration_top(
            current,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        auto_frames, current = _capture_ranked_frames(
            auto_top,
            ranking_count=len(required_auto_pick),
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            sleep_fn=sleep_fn,
        )
        evidence = evaluate_profile_perk_configuration(
            requirements,
            bans_frame=bans_top,
            auto_pick_frames=auto_frames,
            row_fn=row_fn,
        )

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
    for check_id in ("perk_bans", "perk_auto_pick_order"):
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
) -> Frame:
    captured = extract_configured_perk_bans(top, row_fn=row_fn)
    if captured["quality"]["valid"] is not True:
        raise HomePerkConfigurationError(
            "Ban Perks selected block could not be read authoritatively"
        )
    selected = [
        dict(entry)
        for entry in captured["selected"]
        if isinstance(entry, Mapping)
    ]
    expected = set(expected_keys)
    current = top
    recognized_required = {
        entry.get("key")
        for entry in selected
        if entry.get("key") in expected
    }
    missing_before_removal = expected - recognized_required
    known_extras = [
        entry
        for entry in selected
        if entry.get("key") is not None
        and entry.get("key") not in expected
    ]
    unknown_entries = [
        entry for entry in selected if entry.get("key") is None
    ]
    if unknown_entries and missing_before_removal:
        raise HomePerkConfigurationError(
            "Ban Perks selected block was ambiguous; an unrecognized row "
            "could be a required ban"
        )

    # Removing an extra selection is both shorter and more authoritative from
    # the fixed Selected Perks block than searching the complete Available
    # list for that row's checkbox.
    for target in [*known_extras, *unknown_entries]:
        fresh_capture = extract_configured_perk_bans(current, row_fn=row_fn)
        if fresh_capture["quality"]["valid"] is not True:
            raise HomePerkConfigurationError(
                "Ban Perks selected block became unreadable before deselection"
            )
        fresh_selected = [
            dict(entry)
            for entry in fresh_capture["selected"]
            if isinstance(entry, Mapping)
        ]
        row = next(
            (
                entry
                for entry in fresh_selected
                if perk_entries_match(target, entry)
            ),
            None,
        )
        if row is None:
            raise HomePerkConfigurationError(
                "Ban Perks extra selection changed identity before deselection"
            )
        before_count = len(fresh_selected)
        current = _tap_configuration_row(
            current,
            row,
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
        after_capture = extract_configured_perk_bans(current, row_fn=row_fn)
        after_selected = [
            dict(entry)
            for entry in after_capture["selected"]
            if isinstance(entry, Mapping)
        ]
        if (
            after_capture["quality"]["valid"] is not True
            or len(after_selected) != before_count - 1
            or any(perk_entries_match(target, entry) for entry in after_selected)
        ):
            raise HomePerkConfigurationError(
                "Ban Perks selected row did not disappear after deselection"
            )

    current_top = _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    captured = extract_configured_perk_bans(current_top, row_fn=row_fn)
    if captured["quality"]["valid"] is not True:
        raise HomePerkConfigurationError(
            "Ban Perks selected block became unreadable after deselection"
        )
    current_keys = {
        entry.get("key")
        for entry in captured["selected"]
        if isinstance(entry, Mapping)
    }
    pending = [
        {
            "key": key,
            "display_text": perk_configuration_label(key),
        }
        for key in expected_keys
        if key not in current_keys
    ]
    if not pending:
        return current_top

    current = current_top
    for _ in range(BAN_AVAILABLE_START_SWIPES):
        current = _swipe_configuration(
            current,
            "gesture_targets.goto_next:perks",
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )

    for _page in range(MAX_BAN_SCAN_SWIPES + 1):
        rows = [semantic_perk_entry(row) for row in row_fn(current)]
        for row in rows:
            target = next(
                (item for item in pending if perk_entries_match(item, row)),
                None,
            )
            if target is None:
                continue
            current = _tap_configuration_row(
                current,
                row,
                x=BAN_TOGGLE_X,
                action=f"perk_ban_toggle:{row.get('key') or 'unknown'}",
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                visible_fn=visible_fn,
                row_fn=row_fn,
                row_near_fn=row_near_fn,
                sleep_fn=sleep_fn,
                require_identity_after=True,
            )
            pending.remove(target)
            if not pending:
                break
        if not pending:
            break
        next_frame = _swipe_configuration(
            current,
            "gesture_targets.goto_next:perks",
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        if _content_difference(current, next_frame) <= 1.0:
            break
        current = next_frame
    if pending:
        labels = [
            perk_configuration_label(
                str(item.get("key") or "") or None,
                str(item.get("display_text") or ""),
            )
            for item in pending
        ]
        raise HomePerkConfigurationError(
            "could not locate Ban Perks choices: " + ", ".join(labels)
        )

    final_top = _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    final = extract_configured_perk_bans(final_top, row_fn=row_fn)
    final_keys = [entry.get("key") for entry in final["selected"]]
    if (
        final["quality"]["valid"] is not True
        or len(final_keys) != len(expected_keys)
        or set(final_keys) != set(expected_keys)
    ):
        raise HomePerkConfigurationError(
            "Ban Perks remained different after guarded toggles"
        )
    return final_top


def _repair_auto_pick_order(
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
    observed_keys: Sequence[str | None] | None = None,
    sleep_fn: Callable[[float], None],
) -> Frame:
    current = top
    total_taps = 0
    first_mismatch = 1
    if observed_keys is not None and len(observed_keys) == len(expected_keys):
        first_mismatch = next(
            (
                rank
                for rank, (observed, expected) in enumerate(
                    zip(observed_keys, expected_keys),
                    start=1,
                )
                if observed != expected
            ),
            len(expected_keys) + 1,
        )
    for desired_rank, key in enumerate(expected_keys, start=1):
        if desired_rank < first_mismatch:
            continue
        rank, current, row = _locate_auto_pick_key(
            current,
            key,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            row_fn=row_fn,
            sleep_fn=sleep_fn,
        )
        if rank < desired_rank:
            raise HomePerkConfigurationError(
                f"{perk_configuration_label(key)} appeared above its guarded "
                f"target rank {desired_rank}"
            )
        remaining = rank - desired_rank
        while remaining > 0:
            if total_taps >= MAX_AUTO_PICK_MOVE_TAPS:
                raise HomePerkConfigurationError(
                    "Auto Pick repair exceeded its bounded move budget"
                )
            before_rank = rank
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
            observed_rank, current, row = _locate_auto_pick_key(
                current,
                key,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                row_fn=row_fn,
                sleep_fn=sleep_fn,
            )
            if observed_rank != before_rank - 1:
                raise HomePerkConfigurationError(
                    f"{perk_configuration_label(key)} moved from rank "
                    f"{before_rank} to {observed_rank}; expected exactly "
                    "one-rank upward progress"
                )
            rank = observed_rank
            remaining = rank - desired_rank

        if rank != desired_rank:
            raise HomePerkConfigurationError(
                f"{perk_configuration_label(key)} reached rank "
                f"{rank}, expected {desired_rank}"
            )

    final_top = _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    frames, _current = _capture_ranked_frames(
        final_top,
        ranking_count=len(expected_keys),
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        row_fn=row_fn,
        sleep_fn=sleep_fn,
    )
    final = extract_ranked_auto_pick_order(
        frames,
        ranking_count=len(expected_keys),
        row_fn=row_fn,
    )
    final_keys = [entry.get("key") for entry in final["selected"]]
    if final["quality"]["valid"] is not True or final_keys != list(expected_keys):
        raise HomePerkConfigurationError(
            "Auto Pick order remained different after guarded moves"
        )
    return _current


def _locate_auto_pick_key(
    current: Frame,
    key: str,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    row_fn: RowsFn,
    sleep_fn: Callable[[float], None],
) -> tuple[int, Frame, dict[str, Any]]:
    current = _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    ordered: list[dict[str, Any]] = []
    for _page in range(MAX_AUTO_PICK_SCAN_SWIPES + 1):
        for raw in row_fn(current):
            row = semantic_perk_entry(raw)
            if any(perk_entries_match(existing, row) for existing in ordered):
                continue
            ordered.append(row)
            if row.get("key") == key:
                return len(ordered), current, row
        next_frame = _swipe_configuration(
            current,
            "gesture_targets.goto_next:perks",
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        if _content_difference(current, next_frame) <= 1.0:
            break
        current = next_frame
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
    return _scroll_configuration_top(
        current,
        capture_fn=capture_fn,
        visible_fn=visible_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )


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
    frames = [top]
    current = top
    for _ in range(MAX_AUTO_PICK_SCAN_SWIPES):
        captured = extract_ranked_auto_pick_order(
            frames,
            ranking_count=ranking_count,
            row_fn=row_fn,
        )
        if len(captured["selected"]) >= ranking_count:
            return frames, current
        if captured["quality"].get("ranking_boundary_seen") is True:
            return frames, current
        next_frame = _swipe_configuration(
            current,
            "gesture_targets.goto_next:perks",
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        if _content_difference(current, next_frame) <= 1.0:
            break
        frames.append(next_frame)
        current = next_frame
    return frames, current


def _scroll_configuration_top(
    current: Frame,
    *,
    capture_fn: Capture,
    visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str], bool],
    sleep_fn: Callable[[float], None],
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
    home = _wait_for_state(
        "HOME_SCREEN",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    _require_new_battle_home(home, detector, detect_home_control_fn)
    return home


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
    "HomePerkConfigurationResult",
    "PERK_CONFIGURATION_INDICATOR",
    "PERK_TABS",
    "detect_home_perks_configuration_control",
    "ensure_home_perk_configuration",
]
