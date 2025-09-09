#!/usr/bin/env python3
# core/scan_upgrades.py
#
# Find an upgrade label reliably, scrolling as needed,
# and return a stable ROI for the right-hand value/cost box.

import time
from typing import Optional, Tuple, Dict, List

import cv2
import numpy as np

from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import resolve_dot_path, get_clickmap
from core.label_tapper import get_label_match, page_column, tap_label_now  # <-- adaptive scroll + tap
from core.matcher import get_match

# --- Tunables -----------------------------------------------------------------

POST_SWIPE_SLEEP = 0.35

EDGE_EPSILON = 0.004

# (dx, dy, w, h) relative to label bbox top-left
COST_BOX_OFFSET = {
    "left":  (300, 20,  210, 80),   # tune once for your setup
    "right": (300, 20,  210, 80),
}

# -----------------------------------------------------------------------------


def _get_column_region(column: str) -> Tuple[int, int, int, int]:
    dot = f"_shared_match_regions.upgrades_{column}"
    entry = resolve_dot_path(dot)
    if not entry or "match_region" not in entry:
        raise RuntimeError(f"Missing shared region: {dot}")
    r = entry["match_region"]
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])

# ----------------------- Menu helpers ----------------------------------------

MENU_INDICATORS = {
    "attack": "indicators.menu_attack",
    "defense": "indicators.menu_defense",
    "utility": "indicators.menu_utility",
    "uw": "indicators.menu_uw",
}

NAVIGATE_TO = {
    "attack": "navigation.goto_attack",
    "defense": "navigation.goto_defense",
    "utility": "navigation.goto_utility",
    "uw": "navigation.goto_uw",
}


def ensure_menu(category: str, retries: int = 2, settle: float = 0.5) -> bool:
    """Ensure the requested upgrades menu is active.

    Returns True when the indicator is detected; taps the navigation button up to
    `retries` times otherwise.
    """
    category = (category or "").lower()
    if category not in MENU_INDICATORS:
        raise ValueError(f"Unknown category '{category}' (expected attack|defense|utility|uw)")

    indicator = MENU_INDICATORS[category]
    nav_key = NAVIGATE_TO.get(category)

    screen = capture_adb_screenshot()
    if screen is None:
        raise RuntimeError("Failed to capture screenshot")
    pt, conf = get_match(indicator, screenshot=screen)
    if pt is not None:
        return True

    for _ in range(max(1, retries)):
        tap_label_now(nav_key)
        time.sleep(settle)
        screen = capture_adb_screenshot()
        if screen is None:
            continue
        pt, conf = get_match(indicator, screenshot=screen)
        if pt is not None:
            return True
    return False


def _crop(img: np.ndarray, rect: Tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    return img[y:y+h, x:x+w].copy()


def _roi_change_ratio(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = cv2.resize(a, (w, h))
        b = cv2.resize(b, (w, h))
    a_g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    b_g = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(a_g, b_g)
    return float(diff.mean()) / 255.0


def _page(side: str, direction: str, settle: float = POST_SWIPE_SLEEP) -> None:
    page_column(side, direction, strength="page")
    time.sleep(settle)


def scroll_to_top(side: str = "left", max_swipes: int = 12) -> bool:
    img = capture_adb_screenshot()
    if img is None:
        raise RuntimeError("No screenshot")

    col_rect = _get_column_region(side)
    prev = _crop(img, col_rect)

    for _ in range(max_swipes):
        _page(side, "up")
        img2 = capture_adb_screenshot()
        if img2 is None:
            continue
        roi = _crop(img2, col_rect)
        change = _roi_change_ratio(prev, roi)
        if change < EDGE_EPSILON:
            return True
        prev = roi
    return False


def _resolve_upgrade_keys(side: str) -> List[str]:
    base = f"upgrades.attack.{side}"
    node = resolve_dot_path(base) or {}
    keys = []
    for k, v in (node.items() if isinstance(node, dict) else []):
        if isinstance(v, dict) and ("match_template" in v or "region_ref" in v or "match_region" in v):
            keys.append(f"{base}.{k}")
    return sorted(keys)


def _key_exists(category: str, side: str, name: str) -> bool:
    cm = get_clickmap()
    node = resolve_dot_path(f"upgrades.{category}.{side}", cm) or {}
    return isinstance(node, dict) and name in node


def derive_key(category: Optional[str], side: Optional[str], name: str) -> Tuple[str, str, str]:
    """Return (key, resolved_side, category). If side is None, prefer the side that contains the name.

    Raises ValueError if the entry doesn't exist in the clickmap.
    """
    category = (category or "").lower()
    if category and side:
        side_l = side.lower()
        key = f"upgrades.{category}.{side_l}.{name}"
        if resolve_dot_path(key) is None:
            raise ValueError(f"Clickmap entry not found: {key}")
        return key, side_l, category
    candidates_cat = [category] if category else ["attack", "defense", "utility", "uw"]
    for cat in candidates_cat:
        for s in ((side.lower(),) if side else ("left", "right")):
            if _key_exists(cat, s, name):
                return f"upgrades.{cat}.{s}.{name}", s, cat
    if category:
        raise ValueError(f"Clickmap entry not found for upgrades.{category}.<left|right>.{name}")
    raise ValueError(f"Clickmap entry not found for any category: <attack|defense|utility|uw>.<left|right>.{name}")


def find_label_or_scroll(
    label_key: str,
    side: str,
    max_pages: int = 25,
) -> Optional[Tuple[int, int, int, int]]:
    for _ in range(max_pages):
        try:
            bbox = get_label_match(label_key, screenshot=None, return_meta=False)
        except (ValueError, FileNotFoundError, RuntimeError):
            bbox = None
        if bbox:
            return bbox
        _page(side, "down")
    return None


def find_upgrade(category: Optional[str], name: str, side: Optional[str] = None, max_pages: int = 30) -> Optional[Tuple[int, int, int, int]]:
    """Find an upgrade label by category/name (and side if provided), scrolling as needed.

    Returns (x,y,w,h) of the label on success, else None.
    """
    key, resolved_side, _ = derive_key(category, side, name)
    return find_label_or_scroll(key, resolved_side, max_pages=max_pages)


def goto_and_find_upgrade(category: Optional[str], side: Optional[str], name: str, max_pages: int = 30) -> Optional[Tuple[int, int, int, int]]:
    """High-level wrapper:
    - Ensure the correct upgrades menu is active
    - Scroll to top of the appropriate column
    - Find the upgrade label by scrolling down
    Returns label bbox or None.
    """
    # Resolve full key first so we know category/side even if they were omitted
    key, resolved_side, resolved_category = derive_key(category, side, name)
    ok = ensure_menu(resolved_category)
    if not ok:
        log(f"ensure_menu({resolved_category}) failed", "WARN")
    if not scroll_to_top(resolved_side):
        log("scroll_to_top did not reach a stable top; continuing", "WARN")
    return find_label_or_scroll(key, resolved_side, max_pages=max_pages)


def cost_box_from_label_bbox(label_bbox: Tuple[int, int, int, int], side: str) -> Tuple[int, int, int, int]:
    x, y, w, h = label_bbox
    dx, dy, rw, rh = COST_BOX_OFFSET[side]
    return (x + dx, y + dy, rw, rh)


def sample_cost_color(img: np.ndarray, rect: Tuple[int, int, int, int]) -> Dict[str, float]:
    roi = _crop(img, rect)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    h, s, v = [c.mean() for c in cv2.split(hsv)]
    return {"H": float(h), "S": float(s), "V": float(v)}


def main():
    side = "left"
    label_key = "upgrades.attack.left.rend_armor_chance"  # change to any known label

    ok = scroll_to_top(side)
    log(f"scroll_to_top({side}) -> {ok}", "INFO")

    bbox = find_label_or_scroll(label_key, side, max_pages=30)
    if not bbox:
        log(f"Label not found: {label_key}", "FAIL")
        return

    log(f"FOUND {label_key} at bbox={bbox}", "INFO")

    img = capture_adb_screenshot()
    cost_rect = cost_box_from_label_bbox(bbox, side)
    stats = sample_cost_color(img, cost_rect)
    log(f"Cost-box HSV mean @ {cost_rect}: {stats}", "INFO")

    viz = img.copy()
    x,y,w,h = bbox
    cv2.rectangle(viz, (x,y), (x+w, y+h), (0,255,0), 2)
    cx,cy,cw,ch = cost_rect
    cv2.rectangle(viz, (cx,cy), (cx+cw, cy+ch), (255,0,0), 2)
    cv2.imwrite("screenshots/upgrade_debug.png", viz)
    log("Wrote screenshots/upgrade_debug.png", "INFO")


if __name__ == "__main__":
    main()
