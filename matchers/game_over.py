import cv2
import os

TEMPLATE_PATH = "assets/match_templates/game_stats_ga.png"
MATCH_THRESHOLD = 0.90  # Can tune later

# Coordinates for search window (from crop tool)
SEARCH_REGION = {
    "x": 323,
    "y": 628,
    "w": 101,
    "h": 74
}

template = cv2.imread(TEMPLATE_PATH, cv2.IMREAD_COLOR)
template_h, template_w = template.shape[:2]

def match_game_over(screen):
    """Return (x, y), confidence if match is found, else (None, confidence)"""

    x, y, w, h = SEARCH_REGION["x"], SEARCH_REGION["y"], SEARCH_REGION["w"], SEARCH_REGION["h"]
    region = screen[y:y+h, x:x+w]

    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= MATCH_THRESHOLD:
        match_x = x + max_loc[0] + template_w // 2
        match_y = y + max_loc[1] + template_h // 2
        return (match_x, match_y), max_val

    return None, max_val


