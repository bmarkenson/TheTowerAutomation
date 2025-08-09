# core/matcher.py
"""
Centralized, clickmap-backed matching utilities.

Public API:
    get_match(dot_path, *, screenshot, template_dir="assets/match_templates")
        → ((x, y), confidence) or (None, confidence)

Private helper (for shims/tests only):
    _match_entry(screenshot, entry, template_dir)
        → ((x, y), confidence) or (None, confidence)

Notes:
- Uses OpenCV template matching (cv2.TM_CCOEFF_NORMED).
- Reads template/region/threshold from clickmap entries (via clickmap.json).
- Expands the search region by optional 'match_padding' (default 12px), clamped to screen bounds.
"""

from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
import os
import cv2
import numpy as np  # used by detect_floating_gem_square
from core.clickmap_access import resolve_dot_path


def _match_entry(
    screenshot,
    entry: Dict[str, Any],
    template_dir: str = "assets/match_templates",
) -> Tuple[Optional[Tuple[int, int]], float]:
    """
    Low-level matcher using an already-resolved clickmap entry dict.

    Entry contract:
      - 'match_template': path relative to template_dir (e.g., 'indicators/game_over.png')  [required]
      - One of:
          * 'match_region': {'x','y','w','h'}
          * 'region_ref': name referencing clickmap._shared_match_regions.<name>.match_region
      - Optional:
          * 'match_threshold' (float, default 0.9)
          * 'match_padding' (int pixels, default 12)

    Args:
        screenshot: BGR ndarray to search.
        entry: clickmap entry dict (see above).
        template_dir: base directory for templates.

    Returns:
        ((x, y), confidence) if confidence >= threshold; otherwise (None, confidence).

    Errors:
        FileNotFoundError if the template file is missing.
        ValueError if the template cannot be loaded.
        cv2.error if images are invalid.
    """
    if not entry or "match_template" not in entry:
        return None, 0.0

    template_path = os.path.join(template_dir, entry["match_template"])
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")

    # Resolve region
    region = entry.get("match_region")
    if region is None and "region_ref" in entry:
        region_entry = resolve_dot_path(f"_shared_match_regions.{entry['region_ref']}")
        region = region_entry.get("match_region") if region_entry else None

    if not region:
        return None, 0.0

    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    padding = int(entry.get("match_padding", 12))

    # Expand region with padding, clamp to screen bounds
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(screenshot.shape[1], x + w + padding)
    y2 = min(screenshot.shape[0], y + h + padding)
    if x1 >= x2 or y1 >= y2:
        return None, 0.0

    region_img = screenshot[y1:y2, x1:x2]

    template = cv2.imread(template_path)
    if template is None:
        raise ValueError(f"Failed to load template: {template_path}")

    res = cv2.matchTemplate(region_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    threshold = float(entry.get("match_threshold", 0.9))
    if max_val >= threshold:
        match_x = x1 + max_loc[0] + template.shape[1] // 2
        match_y = y1 + max_loc[1] + template.shape[0] // 2
        return (match_x, match_y), max_val
    else:
        return None, max_val


def get_match(
    dot_path: str,
    *,
    screenshot,
    template_dir: str = "assets/match_templates",
) -> Tuple[Optional[Tuple[int, int]], float]:
    """
    Resolve a clickmap entry by dot-path, then perform matching.

    Args:
        dot_path: e.g., "indicators.game_over".
        screenshot: BGR ndarray to search.
        template_dir: base directory for templates.

    Returns:
        ((x, y), confidence) if found; else (None, confidence).
    """
    entry = resolve_dot_path(dot_path)
    if not entry:
        return None, 0.0
    return _match_entry(screenshot, entry, template_dir=template_dir)


def detect_floating_gem_square(screenshot, region: Dict[str, int], debug: bool = False) -> bool:
    """
    Heuristic detector for a bright magenta square within the given region.

    Args:
        screenshot: BGR ndarray.
        region: {'x','y','w','h'} bounding box.
        debug: if True, logs and writes 'debug_floating_gem_square.png'.

    Returns:
        True if a roughly square magenta contour is found; else False.
    """
    from utils.logger import log  # local import to avoid cycles

    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    roi = screenshot[y:y + h, x:x + w]

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # Bright pink / magenta range
    lower = np.array([140, 100, 100])
    upper = np.array([170, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 150:
            continue

        epsilon = 0.02 * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, epsilon, True)

        if len(approx) == 4:  # must be 4-sided
            x_, y_, w_, h_ = cv2.boundingRect(approx)
            aspect_ratio = w_ / h_
            if 0.8 <= aspect_ratio <= 1.2:  # roughly square
                if debug:
                    log(f"[DEBUG] Floating gem pink square detected at ({x_}, {y_}), size {w_}x{h_}", "DEBUG")
                    debug_img = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                    cv2.drawContours(debug_img, [approx], -1, (0, 255, 0), 2)
                    cv2.imwrite("debug_floating_gem_square.png", debug_img)
                return True

    if debug:
        log("[DEBUG] No qualifying pink square detected in floating gem region", "DEBUG")

    return False
