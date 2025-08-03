import cv2
import numpy as np
from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import get_clickmap
from core.adb_utils import adb_shell
from utils.logger import log

def resolve_region(entry, clickmap):
    if "match_region" in entry:
        return entry["match_region"]
    elif "region_ref" in entry:
        ref = entry["region_ref"]
        shared = clickmap.get("_shared_match_regions", {})
        if ref not in shared:
            raise ValueError(f"Unknown region_ref '{ref}'")
        return shared[ref]
    else:
        raise ValueError("No match_region or region_ref defined")

def get_label_match(label_key: str, screenshot=None, return_meta=False):
    """
    Matches a label using its match_template and match_region or region_ref.
    Returns (x, y, w, h) by default.
    If return_meta=True, returns a dict with match + metadata.
    """
    cm = get_clickmap()
    if label_key not in cm:
        raise ValueError(f"Label key '{label_key}' not found in clickmap")

    entry = cm[label_key]
    template = cv2.imread(f"assets/{entry['match_template']}", cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(f"Template not found: assets/{entry['match_template']}")

    if screenshot is None:
        screenshot = capture_adb_screenshot()
        if screenshot is None:
            raise RuntimeError("Failed to capture screenshot")
        screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

    region = resolve_region(entry, cm)
    region_img = screenshot[
        region["y"]:region["y"] + region["h"],
        region["x"]:region["x"] + region["w"]
    ]

    result = cv2.matchTemplate(region_img, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

    if max_val < entry.get("match_threshold", 0.9):
        raise ValueError(f"Match for {label_key} failed threshold: {max_val:.2f}")

    match_x = region["x"] + max_loc[0]
    match_y = region["y"] + max_loc[1]
    h, w = template.shape[:2]

    if return_meta:
        return {
            "x": match_x,
            "y": match_y,
            "w": w,
            "h": h,
            "menu": entry.get("menu"),
            "region_ref": entry.get("region_ref"),
            "order": entry.get("order"),
            "match_score": max_val
        }
    return (match_x, match_y, w, h)

def tap_label_now(label_key: str):
    """
    Taps a label by matching its position and applying an optional offset.
    """
    x, y, w, h = get_label_match(label_key)
    cm = get_clickmap()
    offset = cm[label_key].get("tap_offset", None)

    if offset:
        tap_x = x + offset["x"]
        tap_y = y + offset["y"]
    else:
        tap_x = x + w // 2
        tap_y = y + h // 2

    log(f"TAP_LABEL_NOW: {label_key} at ({tap_x},{tap_y})", "ACTION")
    adb_shell(["input", "tap", str(tap_x), str(tap_y)])
