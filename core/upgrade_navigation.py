from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from core.adb_utils import adb_shell
from core.clickmap_access import resolve_dot_path
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.tap import safe_tap
from core.upgrade_box_detector import UpgradeBox, detect_visible_boxes
from utils.logger import log

# Default timing constants
_SCROLL_SETTLE_SEC = 0.45
_MAX_SCROLL_ATTEMPTS = 12


@dataclass
class UpgradeSearchResult:
    menu: str
    column: str
    index: int
    label: str
    box: UpgradeBox
    screenshot: np.ndarray


_MENU_ALIAS = {
    "attack_menu": "attack",
    "attack": "attack",
    "defense_menu": "defense",
    "defense": "defense",
    "utility_menu": "utility",
    "utility": "utility",
    "uw_menu": "ultimate weapons",
    "ultimate weapons": "ultimate weapons",
    "ultimate": "ultimate weapons",
    "ultimate_weapon": "ultimate weapons",
}

_MENU_NAV = {
    "attack": {
        "nav": "navigation.goto_attack",
        "indicator": "indicators.menu_attack",
    },
    "defense": {
        "nav": "navigation.goto_defense",
        "indicator": "indicators.menu_defense",
    },
    "utility": {
        "nav": "navigation.goto_utility",
        "indicator": "indicators.menu_utility",
    },
    "ultimate weapons": {
        "nav": "navigation.goto_uw",
        "indicator": "indicators.menu_uw",
    },
}


def _normalize_menu(menu: Optional[str]) -> Optional[str]:
    if not menu:
        return None
    return _MENU_ALIAS.get(menu.lower(), menu.lower())


@lru_cache(maxsize=1)
def _load_manifest() -> Dict[str, Dict[str, List[str]]]:
    from pathlib import Path
    import json

    manifest_path = Path("config/upgrade_manifest.json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    upgrades = data.get("upgrades", {})
    normalized: Dict[str, Dict[str, List[str]]] = {}
    for menu, columns in upgrades.items():
        normalized[_normalize_menu(menu) or menu] = {
            col.lower(): list(entries)
            for col, entries in columns.items()
        }
    return normalized


def _manifest_entry(menu: Optional[str], label: str) -> Tuple[str, int, str]:
    manifest = _load_manifest()
    label_norm = label.strip().lower()
    candidate_menus: Sequence[str]

    if menu is not None:
        menu_key = _normalize_menu(menu)
        if not menu_key or menu_key not in manifest:
            raise ValueError(f"Unknown menu '{menu}'")
        candidate_menus = [menu_key]
    else:
        candidate_menus = manifest.keys()

    for menu_candidate in candidate_menus:
        columns = manifest[menu_candidate]
        for column, entries in columns.items():
            for idx, entry in enumerate(entries):
                if entry.lower() == label_norm:
                    return column, idx, menu_candidate

    if menu is None:
        raise ValueError(f"Label '{label}' not found in any manifest menu")
    raise ValueError(f"Label '{label}' not found in manifest menu '{menu}'")


def _perform_swipe(direction: str, extended: bool = False) -> None:
    entry = resolve_dot_path("_shared_match_regions.upgrade_menu_area")
    if not entry or "match_region" not in entry:
        raise RuntimeError("upgrade_menu_area region missing from clickmap")
    region = entry["match_region"]
    x = int(region["x"])
    y = int(region["y"])
    w = int(region["w"])
    h = int(region["h"])

    start_x = x + w // 2
    if direction == "towards_top":
        if extended:
            start_ratio, end_ratio = 0.24, 0.76
        else:
            start_ratio, end_ratio = 0.32, 0.62
        start_y = y + int(h * start_ratio)
        end_y = y + int(h * end_ratio)
    elif direction == "towards_bottom":
        if extended:
            start_ratio, end_ratio = 0.76, 0.24
        else:
            start_ratio, end_ratio = 0.68, 0.38
        start_y = y + int(h * start_ratio)
        end_y = y + int(h * end_ratio)
    else:
        raise ValueError(f"Unknown scroll direction '{direction}'")

    adb_shell([
        "input",
        "swipe",
        str(start_x),
        str(start_y),
        str(start_x),
        str(end_y),
        "350",
    ])


def _ensure_menu(menu: str, *, capture_fn: Callable[[], Optional[np.ndarray]], max_attempts: int = 4) -> Optional[np.ndarray]:
    menu_key = _normalize_menu(menu)
    if menu_key is None or menu_key not in _MENU_NAV:
        raise ValueError(f"Unsupported menu '{menu}'")

    for attempt in range(max_attempts):
        screenshot = capture_fn()
        if screenshot is None:
            continue
        detection = detect_state_and_overlays(screenshot)
        current = _normalize_menu(detection.get("menu"))
        if current == menu_key:
            return screenshot

        nav_entry = _MENU_NAV[menu_key]["nav"]
        log(f"[UPGRADE_NAV] Attempting to switch to {menu_key} (attempt {attempt + 1})", "MATCH")
        safe_tap(nav_entry, require_visible=False, dispatch='now')
        time.sleep(0.5)

    # final capture after attempts
    screenshot = capture_fn()
    return screenshot


def find_upgrade(
    menu: Optional[str],
    label: str,
    *,
    max_scrolls: int = _MAX_SCROLL_ATTEMPTS,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
    swipe_fn: Callable[[str, bool], None] = _perform_swipe,
    ensure_menu: bool = True,
) -> Optional[UpgradeSearchResult]:
    """Locate a specific upgrade tile by menu/label, scrolling as needed.

    Returns an UpgradeSearchResult with the matching UpgradeBox, or None if not found.
    """
    column, target_index, resolved_menu = _manifest_entry(menu, label)

    menu_key = _normalize_menu(resolved_menu)
    if menu_key is None:
        raise ValueError(f"Unable to resolve menu for label '{label}'")

    manifest = _load_manifest()[menu_key]
    target_label_norm = label.strip().lower()
    screenshot = None
    if ensure_menu and menu_key is not None:
        screenshot = _ensure_menu(menu_key, capture_fn=capture_fn)
    if screenshot is None:
        screenshot = capture_fn()
    if screenshot is None:
        raise RuntimeError("Unable to capture screenshot for upgrade navigation")

    last_state: Optional[Tuple[int, ...]] = None
    last_direction: Optional[str] = None

    repeat_count = 0

    for attempt in range(max_scrolls + 1):
        boxes_by_col = detect_visible_boxes(screenshot, menu=menu_key)
        visible_boxes = boxes_by_col.get(column, [])
        visible_indices: List[int] = []
        match_box: Optional[UpgradeBox] = None

        for box in visible_boxes:
            if not box.text:
                continue
            try:
                idx = manifest[column].index(box.text)
            except ValueError:
                continue
            visible_indices.append(idx)
            if box.text.lower() == target_label_norm:
                match_box = box

        if match_box is not None:
            return UpgradeSearchResult(
                menu=menu_key,
                column=column,
                index=target_index,
                label=manifest[column][target_index],
                box=match_box,
                screenshot=screenshot,
            )

        if attempt >= max_scrolls:
            break

        if not visible_indices:
            direction = "towards_bottom"
        elif target_index < visible_indices[0]:
            if visible_indices[0] == 0:
                break
            direction = "towards_top"
        elif target_index > visible_indices[-1]:
            if visible_indices[-1] == len(manifest[column]) - 1:
                break
            direction = "towards_bottom"
        else:
            mid = visible_indices[0] + (visible_indices[-1] - visible_indices[0]) // 2
            direction = "towards_top" if target_index <= mid else "towards_bottom"

        state_key = tuple(visible_indices)
        if state_key and last_state == state_key and last_direction == direction:
            repeat_count += 1
            if repeat_count > 1:
                log("[UPGRADE_NAV] No progress after extended scrolling; aborting", "WARN")
                break
            swipe_fn(direction, True)
            sleep_fn(_SCROLL_SETTLE_SEC)
            screenshot = capture_fn()
            if screenshot is None:
                screenshot = capture_fn()
                if screenshot is None:
                    raise RuntimeError("Failed to capture screenshot after scrolling")
            last_state = state_key
            last_direction = direction
            continue
        repeat_count = 0

        # If we're trying to move down and already have the bottom-most entries, stop.
        if (
            direction == "towards_bottom"
            and visible_indices
            and visible_indices[-1] == len(manifest[column]) - 1
        ):
            break

        # Likewise for moving up at the very top.
        if direction == "towards_top" and visible_indices and visible_indices[0] == 0:
            break

        last_state = state_key if state_key else None
        last_direction = direction

        swipe_fn(direction, False)
        sleep_fn(_SCROLL_SETTLE_SEC)
        screenshot = capture_fn()
        if screenshot is None:
            screenshot = capture_fn()
            if screenshot is None:
                raise RuntimeError("Failed to capture screenshot after scrolling")

    return None


__all__ = ["UpgradeSearchResult", "find_upgrade"]
