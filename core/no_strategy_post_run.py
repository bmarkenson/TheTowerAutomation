"""Read-only Home-boundary evidence capture for a completed No Strategy run."""

from __future__ import annotations

from dataclasses import dataclass
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
from core.input import safe_tap, tap_if_visible
from core.label_tapper import is_visible
from core.perk_configuration import parse_perk_configuration_selection
from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.workshop_preset import measure_preset_slot_selection
from utils.logger import log


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]

PERK_CONFIGURATION_INDICATOR = "indicators.perks_configuration"
PERK_CONTENT_REGION = (100, 420, 880, 1330)
PERK_TABS = (
    ("perk_first_choice", "First Perk", (218, 210, 210, 90)),
    ("perk_bans", "Ban Perks", (436, 210, 210, 90)),
    ("perk_auto_pick_order", "Auto Pick", (650, 210, 210, 90)),
)


@dataclass(frozen=True)
class PostRunLockResult:
    values: Mapping[str, Any]
    home_screenshot: Frame


@dataclass(frozen=True)
class PerkConfigurationCapture:
    fields: Mapping[str, Mapping[str, Any]]
    home_screenshot: Frame


class NoStrategyPostRunError(RuntimeError):
    pass


def inspect_post_run_free_upgrade_locks(
    home_screenshot: Frame,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    inspect_fn: Callable[..., Any] = inspect_free_upgrade_locks,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> PostRunLockResult:
    """Inspect supported lock details and restore verified no-battle Home."""

    _require_new_battle_home(home_screenshot, detector, home_control_fn)
    if not safe_tap_fn(
        "navigation.goto_workshop_home",
        require_visible=False,
        dispatch="now",
        log_label="no_strategy_post_run:workshop",
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
        safe_tap_fn=safe_tap_fn,
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
    if not safe_tap_fn(
        "navigation.goto_home",
        require_visible=False,
        dispatch="now",
        log_label="no_strategy_post_run:return_home",
    ):
        raise NoStrategyPostRunError("Home navigation tap failed after lock inspection")
    home = _wait_for(
        "HOME_SCREEN", capture_fn=capture_fn, detector=detector, sleep_fn=sleep_fn
    )
    _require_new_battle_home(home, detector, home_control_fn)
    log(
        "[NO_STRATEGY] Recorded post-run Free Upgrade locks without changing them",
        "INFO",
        console=True,
    )
    return PostRunLockResult(values, home)


def open_cards_for_post_run_perk_capture(
    home_screenshot: Frame,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Frame:
    """Open Cards and stop before the unverified Perks-configuration control."""

    _require_new_battle_home(home_screenshot, detector, home_control_fn)
    if not safe_tap_fn(
        "navigation.goto_cards_home",
        require_visible=False,
        dispatch="now",
        log_label="no_strategy_post_run:cards",
    ):
        raise NoStrategyPostRunError("Cards navigation tap failed")
    cards = _wait_for(
        "CARDS", capture_fn=capture_fn, detector=detector, sleep_fn=sleep_fn
    )
    log(
        "[NO_STRATEGY] Post-run record is holding the next battle. Open the "
        "Perks configuration panel; its three tabs will be captured read-only.",
        "INFO",
        console=True,
    )
    return cards


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
    for field, label, region in PERK_TABS:
        current = _select_perk_tab(
            current,
            label=label,
            region=region,
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            visible_fn=visible_fn,
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

    if not tap_visible_fn(
        "buttons.close:perks", screenshot=current, retries=1
    ):
        raise NoStrategyPostRunError("Perks configuration close button was not visible")
    cards = _wait_for(
        "CARDS", capture_fn=capture_fn, detector=detector, sleep_fn=sleep_fn
    )
    if not safe_tap_fn(
        "navigation.goto_home",
        require_visible=False,
        dispatch="now",
        log_label="no_strategy_post_run:perks_return_home",
    ):
        raise NoStrategyPostRunError("Home navigation tap failed after Perks capture")
    home = _wait_for(
        "HOME_SCREEN", capture_fn=capture_fn, detector=detector, sleep_fn=sleep_fn
    )
    _require_new_battle_home(home, detector, home_control_fn)
    if cards is None:  # pragma: no cover - _wait_for either returns or raises
        raise NoStrategyPostRunError("Cards screen was not restored")
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
    if not safe_tap_fn(
        (x + width // 2, y + height // 2),
        require_visible=False,
        dispatch="now",
        log_label=f"no_strategy_post_run:perk_tab:{_safe_slug(label)}",
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


def _require_new_battle_home(frame, detector, home_control_fn) -> None:
    if detector(frame).get("state") != "HOME_SCREEN":
        raise NoStrategyPostRunError("post-run inspection requires Home")
    control = home_control_fn(frame).control
    if control is not HomeBattleControl.NEW_BATTLE:
        raise NoStrategyPostRunError(
            f"post-run inspection requires NEW_BATTLE, observed {control.value}"
        )


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
    "PERK_CONFIGURATION_INDICATOR",
    "PERK_TABS",
    "PerkConfigurationCapture",
    "PostRunLockResult",
    "capture_post_run_perk_configuration",
    "inspect_post_run_free_upgrade_locks",
    "open_cards_for_post_run_perk_capture",
]
