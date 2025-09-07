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
from core.clickmap_access import resolve_dot_path
from core.label_tapper import get_label_match, page_column  # <-- adaptive scroll

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


def find_label_or_scroll(
    label_key: str,
    side: str,
    max_pages: int = 25,
) -> Optional[Tuple[int, int, int, int]]:
    for _ in range(max_pages):
        bbox = get_label_match(label_key, screenshot=None, return_meta=False)
        if bbox:
            return bbox
        _page(side, "down")
    return None


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
