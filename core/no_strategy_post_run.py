"""Read-only Home-boundary evidence capture for a completed No Strategy run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.battle_lifecycle import HomeBattleControl
from core.free_upgrade_locks import (
    FARM_FREE_UPGRADE_LOCKS,
    inspect_free_upgrade_locks,
)
from core.home_battle import detect_home_battle_control
from core.input import TapVerification, safe_tap, swipe_now, tap_if_visible
from core.label_tapper import is_visible
from core.perk_configuration import parse_perk_configuration_selection
from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_navigation import swipe_upgrade_menu
from core.workshop_preset import measure_preset_slot_selection
from utils.logger import log


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]

PERK_CONFIGURATION_INDICATOR = "indicators.perks_configuration"
PERK_CONTENT_REGION = (100, 420, 880, 1330)
HOME_PERKS_CONTROL_REGION = (985, 475, 90, 100)
_MIN_HOME_PERKS_GRAY_PIXELS = 800
PERK_TABS = (
    ("perk_first_choice", "First Perk", (218, 210, 210, 90)),
    ("perk_bans", "Ban Perks", (436, 210, 210, 90)),
    ("perk_auto_pick_order", "Auto Pick", (650, 210, 210, 90)),
)
POST_RUN_RECOVERY_WINDOW = timedelta(hours=6)


@dataclass(frozen=True)
class PostRunLockResult:
    values: Mapping[str, Any]
    home_screenshot: Frame
    workshop_screenshot: Frame


@dataclass(frozen=True)
class PostRunPerksOpenResult:
    cards_screenshot: Frame
    perks_screenshot: Frame


@dataclass(frozen=True)
class PerkConfigurationCapture:
    fields: Mapping[str, Mapping[str, Any]]
    home_screenshot: Frame


class NoStrategyPostRunError(RuntimeError):
    pass


class NoStrategyPostRunPaused(NoStrategyPostRunError):
    pass


def load_pending_no_strategy_record(
    *,
    records_dir: Path | str = Path("logs/battles"),
    now: Optional[datetime] = None,
    recovery_window: timedelta = POST_RUN_RECOVERY_WINDOW,
) -> Optional[dict[str, Any]]:
    """Load the newest recent unfinished No Strategy terminal record."""

    current_time = now or datetime.now().astimezone()
    candidates = sorted(
        Path(records_dir).glob("Battle*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    recoverable: list[tuple[datetime, dict[str, Any]]] = []
    finalized_fingerprints: set[tuple[Any, ...]] = set()
    for path in candidates:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            observed = record.get("observed_run_configuration")
            captured_at = datetime.fromisoformat(str(record.get("captured_at") or ""))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if record.get("strategy") is not None or not isinstance(observed, Mapping):
            continue
        if observed.get("collection_mode") != "no_strategy_observation":
            continue
        if captured_at.tzinfo is None:
            continue
        age = current_time - captured_at.astimezone(current_time.tzinfo)
        if timedelta(0) <= age <= recovery_window:
            if observed.get("finalized") is True:
                fingerprint = _terminal_record_fingerprint(record)
                if fingerprint:
                    finalized_fingerprints.add(fingerprint)
            else:
                recoverable.append((captured_at, record))
    recoverable = [
        item
        for item in recoverable
        if not _terminal_record_fingerprint(item[1])
        or _terminal_record_fingerprint(item[1]) not in finalized_fingerprints
    ]
    if not recoverable:
        return None
    recoverable.sort(key=lambda item: item[0], reverse=True)
    newest_fingerprint = _terminal_record_fingerprint(recoverable[0][1])
    related = [
        item
        for item in recoverable
        if not newest_fingerprint
        or _terminal_record_fingerprint(item[1]) == newest_fingerprint
    ]
    _captured_at, best = max(
        related,
        key=lambda item: (_observation_coverage(item[1]), item[0]),
    )
    return best


def _terminal_record_fingerprint(record: Mapping[str, Any]) -> tuple[Any, ...]:
    fields = record.get("game_stats", {}).get("fields", {})
    if not isinstance(fields, Mapping):
        return ()
    values = []
    for name in ("tier", "wave", "total_coins_earned"):
        field = fields.get(name)
        if not isinstance(field, Mapping):
            values.append(None)
            continue
        values.append(field.get("value", field.get("decimal", field.get("raw"))))
    return tuple(values) if any(value is not None for value in values) else ()


def _observation_coverage(record: Mapping[str, Any]) -> int:
    observed = record.get("observed_run_configuration")
    coverage = observed.get("coverage", {}) if isinstance(observed, Mapping) else {}
    if not isinstance(coverage, Mapping):
        return 0
    return sum(
        int(coverage.get(name) or 0)
        for name in ("observed", "evidence_captured", "unavailable")
    )


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
    gray_pixels = int(
        (
            (hsv[:, :, 1] < 60)
            & (hsv[:, :, 2] >= 20)
        ).sum()
    )
    return {
        "visible": gray_pixels >= _MIN_HOME_PERKS_GRAY_PIXELS,
        "gray_pixels": gray_pixels,
        "minimum_gray_pixels": _MIN_HOME_PERKS_GRAY_PIXELS,
        "region": list(HOME_PERKS_CONTROL_REGION),
    }


def inspect_post_run_free_upgrade_locks(
    home_screenshot: Frame,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    inspect_fn: Callable[..., Any] = inspect_free_upgrade_locks,
    workshop_swipe_fn: Callable[[str, str], Any] = swipe_upgrade_menu,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> PostRunLockResult:
    """Inspect supported lock details and restore verified no-battle Home."""

    _require_new_battle_home(home_screenshot, detector, home_control_fn)

    def guarded_tap(*args, **kwargs) -> bool:
        _require_action(action_guard_fn)
        return bool(safe_tap_fn(*args, **kwargs))

    def guarded_swipe(direction: str, span: str) -> Any:
        _require_action(action_guard_fn)
        return workshop_swipe_fn(direction, span)

    if not guarded_tap(
        "navigation.goto_workshop_home",
        dispatch="now",
        log_label="no_strategy_post_run:workshop",
        screenshot=home_screenshot,
    ):
        raise NoStrategyPostRunError("Workshop navigation tap failed")
    workshop = _wait_for(
        "WORKSHOP", capture_fn=capture_fn, detector=detector, sleep_fn=sleep_fn
    )
    result = inspect_fn(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=workshop,
        enforce=False,
        capture_fn=capture_fn,
        detector=detector,
        safe_tap_fn=guarded_tap,
        swipe_fn=guarded_swipe,
        sleep_fn=sleep_fn,
    )
    if getattr(result, "changed_labels", ()):
        raise NoStrategyPostRunError("read-only lock inspection reported changes")
    values = result.evidence.as_dict()
    values.update(
        boundary=HomeBattleControl.NEW_BATTLE.value,
        checked=True,
        changed_labels=[],
    )
    workshop_observation = result.screenshot
    if not guarded_tap(
        "navigation.goto_home",
        dispatch="now",
        log_label="no_strategy_post_run:return_home",
        screenshot=result.screenshot,
    ):
        raise NoStrategyPostRunError("Home navigation tap failed after lock inspection")
    home = _wait_for_new_battle_home(
        capture_fn=capture_fn,
        detector=detector,
        home_control_fn=home_control_fn,
        sleep_fn=sleep_fn,
    )
    log(
        "[NO_STRATEGY] Recorded post-run Free Upgrade locks without changing them",
        "INFO",
        console=True,
    )
    return PostRunLockResult(values, home, workshop_observation)


def open_perks_configuration_for_post_run_capture(
    home_screenshot: Frame,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> PostRunPerksOpenResult:
    """Open Cards, verify its expanded menu, and open Perks configuration."""

    _require_new_battle_home(home_screenshot, detector, home_control_fn)
    _require_action(action_guard_fn)
    if not safe_tap_fn(
        "navigation.goto_cards_home",
        dispatch="now",
        log_label="no_strategy_post_run:cards",
        screenshot=home_screenshot,
    ):
        raise NoStrategyPostRunError("Cards navigation tap failed")
    cards = _wait_for(
        "CARDS", capture_fn=capture_fn, detector=detector, sleep_fn=sleep_fn
    )
    perks = open_perks_configuration_from_cards(
        cards,
        capture_fn=capture_fn,
        detector=detector,
        home_control_fn=home_control_fn,
        safe_tap_fn=safe_tap_fn,
        action_guard_fn=action_guard_fn,
        sleep_fn=sleep_fn,
    )
    log(
        "[NO_STRATEGY] Opened Home Perks configuration for automatic "
        "read-only capture",
        "INFO",
        console=True,
    )
    return PostRunPerksOpenResult(cards, perks)


def open_perks_configuration_from_cards(
    cards_screenshot: Frame,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Frame:
    """Return from Cards and open the verified no-battle Home Perks item."""

    if detector(cards_screenshot).get("state") != "CARDS":
        raise NoStrategyPostRunError("Perks configuration requires Cards")
    _require_action(action_guard_fn)
    if not safe_tap_fn(
        "navigation.goto_home",
        dispatch="now",
        log_label="no_strategy_post_run:return_home_from_cards",
        screenshot=cards_screenshot,
    ):
        raise NoStrategyPostRunError("Home navigation tap failed from Cards")
    home = _wait_for_new_battle_home(
        capture_fn=capture_fn,
        detector=detector,
        home_control_fn=home_control_fn,
        sleep_fn=sleep_fn,
    )
    if not detect_home_perks_configuration_control(home)["visible"]:
        raise NoStrategyPostRunError(
            "Home Perks menu item was not independently verified"
        )
    _require_action(action_guard_fn)
    if not safe_tap_fn(
        "navigation.home_perks_configuration",
        dispatch="now",
        log_label="no_strategy_post_run:perks_configuration",
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
        raise NoStrategyPostRunError("Perks configuration tap failed")
    perks = _wait_for(
        "PERKS", capture_fn=capture_fn, detector=detector, sleep_fn=sleep_fn
    )
    if not is_visible(PERK_CONFIGURATION_INDICATOR, screenshot=perks):
        raise NoStrategyPostRunError(
            "Perks destination was not the Home configuration panel"
        )
    return perks


def capture_post_run_perk_configuration(
    screenshot: Frame,
    *,
    battle_id: str,
    evidence_root: Path | str = Path("logs/battle_observations"),
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    visible_fn: Callable[..., bool] = is_visible,
    scroll_top_fn: Callable[..., Any] = scroll_to_edge,
    capture_scroll_fn: Callable[..., Any] = capture_scroll_to_edge,
    swipe_fn: Callable[[str], bool] = swipe_now,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> PerkConfigurationCapture:
    """Capture all Perks configuration tabs, preserving raw evidence pages."""

    if detector(screenshot).get("state") != "PERKS" or not visible_fn(
        PERK_CONFIGURATION_INDICATOR, screenshot=screenshot
    ):
        raise NoStrategyPostRunError("Perks configuration panel is not visible")

    directory = Path(evidence_root) / _safe_slug(battle_id) / "perk_configuration"
    fields: dict[str, Mapping[str, Any]] = {}
    current = screenshot

    def guarded_swipe(label: str) -> bool:
        _require_action(action_guard_fn)
        return bool(swipe_fn(label))

    for field, label, region in PERK_TABS:
        current = _select_perk_tab(
            current,
            label=label,
            region=region,
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
            action_guard_fn=action_guard_fn,
            sleep_fn=sleep_fn,
        )
        top = scroll_top_fn(
            "gesture_targets.goto_top:perks",
            source_label=PERK_CONFIGURATION_INDICATOR,
            screenshot=current,
            progress_region=PERK_CONTENT_REGION,
            max_swipes=8,
            settle_s=0.8,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=guarded_swipe,
            sleep_fn=sleep_fn,
        )
        if top.screenshot is None:
            raise NoStrategyPostRunError(f"{label} top capture failed: {top.reason}")
        capture = capture_scroll_fn(
            "gesture_targets.goto_next:perks",
            source_label=PERK_CONFIGURATION_INDICATOR,
            screenshot=top.screenshot,
            progress_region=PERK_CONTENT_REGION,
            max_swipes=20,
            settle_s=0.8,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=guarded_swipe,
            sleep_fn=sleep_fn,
        )
        pages = list(capture.screenshots)
        if not pages:
            pages = [top.screenshot]
        paths = _save_evidence_pages(directory, field, pages)
        source_complete = bool(top.success and capture.success)
        source_reason = (
            capture.reason if top.success else f"top_{top.reason}:{capture.reason}"
        )
        fields[field] = parse_perk_configuration_selection(
            pages,
            field=field,
            source_complete=source_complete,
            source_reason=source_reason,
            evidence_images=[str(path) for path in paths],
        )
        fields[field]["tab"] = label
        current = pages[-1]

    _require_action(action_guard_fn)
    if not tap_visible_fn(
        "buttons.close:perks", screenshot=current, retries=1
    ):
        raise NoStrategyPostRunError("Perks configuration close button was not visible")
    destination = _wait_for_any(
        {"CARDS", "HOME_SCREEN"},
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    if detector(destination).get("state") == "CARDS":
        _require_action(action_guard_fn)
        if not safe_tap_fn(
            "navigation.goto_home",
            dispatch="now",
            log_label="no_strategy_post_run:perks_return_home",
            screenshot=destination,
        ):
            raise NoStrategyPostRunError(
                "Home navigation tap failed after Perks capture"
            )
        home = _wait_for_new_battle_home(
            capture_fn=capture_fn,
            detector=detector,
            home_control_fn=home_control_fn,
            sleep_fn=sleep_fn,
        )
    else:
        home = _wait_for_new_battle_home(
            initial=destination,
            capture_fn=capture_fn,
            detector=detector,
            home_control_fn=home_control_fn,
            sleep_fn=sleep_fn,
        )
    log(
        "[NO_STRATEGY] Captured post-run First Perk, Ban Perks, and Auto Pick evidence",
        "INFO",
        console=True,
    )
    return PerkConfigurationCapture(fields, home)


def _select_perk_tab(
    current: Frame,
    *,
    label: str,
    region: tuple[int, int, int, int],
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    visible_fn: Callable[..., bool],
    action_guard_fn: Optional[Callable[[], bool]],
    sleep_fn: Callable[[float], None],
) -> Frame:
    selection = measure_preset_slot_selection(current, region)
    if selection.selected:
        return current
    if detector(current).get("state") != "PERKS" or not visible_fn(
        PERK_CONFIGURATION_INDICATOR, screenshot=current
    ):
        raise NoStrategyPostRunError(f"lost Perks configuration before {label}")
    x, y, width, height = region
    _require_action(action_guard_fn)
    if not safe_tap_fn(
        (x + width // 2, y + height // 2),
        dispatch="now",
        log_label=f"no_strategy_post_run:perk_tab:{_safe_slug(label)}",
        verification=TapVerification(
            screenshot=current,
            target_region=region,
            description=f"perk_configuration_tab:{_safe_slug(label)}",
            verifier=lambda frame: (
                detector(frame).get("state") == "PERKS"
                and visible_fn(
                    PERK_CONFIGURATION_INDICATOR,
                    screenshot=frame,
                )
                and not measure_preset_slot_selection(frame, region).selected
            ),
        ),
    ):
        raise NoStrategyPostRunError(f"{label} tab tap failed")
    for _ in range(8):
        sleep_fn(0.25)
        fresh = capture_fn()
        if fresh is None:
            continue
        if detector(fresh).get("state") != "PERKS" or not visible_fn(
            PERK_CONFIGURATION_INDICATOR, screenshot=fresh
        ):
            raise NoStrategyPostRunError(f"lost Perks configuration after {label}")
        if measure_preset_slot_selection(fresh, region).selected:
            return fresh
    raise NoStrategyPostRunError(f"{label} tab did not become selected")


def _wait_for(
    state: str,
    *,
    capture_fn: Capture,
    detector: Detector,
    sleep_fn: Callable[[float], None],
    attempts: int = 12,
) -> Frame:
    for _ in range(attempts):
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == state:
            return frame
        sleep_fn(0.25)
    raise NoStrategyPostRunError(f"timed out waiting for {state}")


def _wait_for_any(
    states: set[str],
    *,
    capture_fn: Capture,
    detector: Detector,
    sleep_fn: Callable[[float], None],
    attempts: int = 12,
) -> Frame:
    for _ in range(attempts):
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") in states:
            return frame
        sleep_fn(0.25)
    raise NoStrategyPostRunError(
        "timed out waiting for " + " or ".join(sorted(states))
    )


def _require_new_battle_home(frame, detector, home_control_fn) -> None:
    if detector(frame).get("state") != "HOME_SCREEN":
        raise NoStrategyPostRunError("post-run inspection requires Home")
    control = home_control_fn(frame).control
    if control is not HomeBattleControl.NEW_BATTLE:
        raise NoStrategyPostRunError(
            f"post-run inspection requires NEW_BATTLE, observed {control.value}"
        )


def _wait_for_new_battle_home(
    *,
    capture_fn: Capture,
    detector: Detector,
    home_control_fn: Callable[[Frame], Any],
    sleep_fn: Callable[[float], None],
    initial: Optional[Frame] = None,
    attempts: int = 24,
) -> Frame:
    """Wait for both Home and its no-battle control to finish settling."""

    last_state = "UNKNOWN"
    last_control = HomeBattleControl.UNKNOWN
    for attempt in range(attempts):
        frame = initial if attempt == 0 and initial is not None else capture_fn()
        if frame is not None:
            last_state = str(detector(frame).get("state") or "UNKNOWN")
            if last_state == "HOME_SCREEN":
                last_control = home_control_fn(frame).control
                if last_control is HomeBattleControl.NEW_BATTLE:
                    return frame
        if attempt + 1 < attempts:
            sleep_fn(0.25)
    raise NoStrategyPostRunError(
        "post-run inspection timed out waiting for NEW_BATTLE Home; "
        f"last state={last_state}, control={last_control.value}"
    )


def restore_post_run_home(
    screenshot: Frame,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Frame:
    """Restore a paused/retried post-run pass to verified no-battle Home."""

    current = screenshot
    state = str(detector(current).get("state") or "UNKNOWN")
    if state == "HOME_SCREEN":
        _require_new_battle_home(current, detector, home_control_fn)
        return current
    if state == "PERKS" and is_visible(
        PERK_CONFIGURATION_INDICATOR, screenshot=current
    ):
        _require_action(action_guard_fn)
        if not tap_visible_fn("buttons.close:perks", screenshot=current, retries=1):
            raise NoStrategyPostRunError("could not close Perks configuration")
        current = _wait_for_any(
            {"CARDS", "HOME_SCREEN"},
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        state = str(detector(current).get("state") or "UNKNOWN")
        if state == "HOME_SCREEN":
            return _wait_for_new_battle_home(
                initial=current,
                capture_fn=capture_fn,
                detector=detector,
                home_control_fn=home_control_fn,
                sleep_fn=sleep_fn,
            )
    if state not in {"CARDS", "WORKSHOP"}:
        raise NoStrategyPostRunError(
            f"cannot restore post-run Home from state={state}"
        )
    _require_action(action_guard_fn)
    if not safe_tap_fn(
        "navigation.goto_home",
        dispatch="now",
        log_label="no_strategy_post_run:restore_home",
        screenshot=current,
    ):
        raise NoStrategyPostRunError("post-run Home restoration tap failed")
    home = _wait_for_new_battle_home(
        capture_fn=capture_fn,
        detector=detector,
        home_control_fn=home_control_fn,
        sleep_fn=sleep_fn,
    )
    return home


def _require_action(action_guard_fn: Optional[Callable[[], bool]]) -> None:
    if action_guard_fn is not None and not action_guard_fn():
        raise NoStrategyPostRunPaused("automation paused during post-run inventory")


def _save_evidence_pages(
    directory: Path,
    field: str,
    pages: list[Frame],
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, frame in enumerate(pages, start=1):
        path = directory / f"{field}_{index:02d}.png"
        if not cv2.imwrite(str(path), frame):
            raise NoStrategyPostRunError(f"could not save evidence image {path}")
        paths.append(path)
    return paths


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")


__all__ = [
    "NoStrategyPostRunError",
    "NoStrategyPostRunPaused",
    "HOME_PERKS_CONTROL_REGION",
    "PERK_CONFIGURATION_INDICATOR",
    "PERK_TABS",
    "PerkConfigurationCapture",
    "PostRunLockResult",
    "PostRunPerksOpenResult",
    "capture_post_run_perk_configuration",
    "detect_home_perks_configuration_control",
    "inspect_post_run_free_upgrade_locks",
    "load_pending_no_strategy_record",
    "open_perks_configuration_for_post_run_capture",
    "open_perks_configuration_from_cards",
    "restore_post_run_home",
]
