# utils/template_matcher.py

import cv2
import os

def match_region(screen, entry, template_dir="assets/match_templates"):
    """
    Perform OpenCV template matching within a defined region from clickmap.json.
    Supports optional region padding.
    Returns: (match_point, confidence) or (None, confidence)
    """
    if "match_template" not in entry or "match_region" not in entry:
        return None, 0.0

    template_path = os.path.join(template_dir, entry["match_template"])
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")

    region = entry["match_region"]
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    padding = entry.get("match_padding", 12)

    # Expand region with padding, but keep it within screen bounds
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(screen.shape[1], x + w + padding)
    y2 = min(screen.shape[0], y + h + padding)

    region_img = screen[y1:y2, x1:x2]

    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise ValueError(f"Failed to load template image: {template_path}")

    result = cv2.matchTemplate(region_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    threshold = entry.get("match_threshold", 0.90)
    if max_val >= threshold:
        match_x = x1 + max_loc[0] + template.shape[1] // 2
        match_y = y1 + max_loc[1] + template.shape[0] // 2
        return (match_x, match_y), max_val
    else:
        return None, max_val
