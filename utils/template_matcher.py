# utils/template_matcher.py

import cv2
import cv2
import os
from core.clickmap_access import resolve_dot_path

def match_region(screen, entry, template_dir="assets/match_templates"):
    """
    Perform OpenCV template matching within a defined region from clickmap.json.
    Supports optional region_ref fallback. Returns: (match_point, confidence)
    """
    if "match_template" not in entry:
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
    padding = entry.get("match_padding", 12)

    # Expand region with padding, keep in screen bounds
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(screen.shape[1], x + w + padding)
    y2 = min(screen.shape[0], y + h + padding)

    region_img = screen[y1:y2, x1:x2]
    template = cv2.imread(template_path)
    if template is None:
        raise ValueError(f"Failed to load template: {template_path}")

    #print(f"[DEBUG] Matching {entry.get('match_template')} in region {x1},{y1} to {x2},{y2}")
    res = cv2.matchTemplate(region_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= entry.get("match_threshold", 0.9):
        match_x = x1 + max_loc[0] + template.shape[1] // 2
        match_y = y1 + max_loc[1] + template.shape[0] // 2
        return (match_x, match_y), max_val
    else:
        return None, max_val

def detect_floating_gem_square(screen, region, debug=False):
    import cv2
    import numpy as np

    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    roi = screen[y:y + h, x:x + w]

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

        # Approximate to polygon
        epsilon = 0.02 * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, epsilon, True)

        if len(approx) == 4:  # Must be 4-sided
            x_, y_, w_, h_ = cv2.boundingRect(approx)
            aspect_ratio = w_ / h_
            if 0.8 <= aspect_ratio <= 1.2:  # Must be roughly square
                if debug:
                    from utils.logger import log
                    log(f"[DEBUG] Floating gem pink square detected at ({x_}, {y_}), size {w_}x{h_}", "DEBUG")
                    debug_img = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                    cv2.drawContours(debug_img, [approx], -1, (0, 255, 0), 2)
                    cv2.imwrite("debug_floating_gem_square.png", debug_img)
                return True

    if debug:
        from utils.logger import log
        log("[DEBUG] No qualifying pink square detected in floating gem region", "DEBUG")

    return False

