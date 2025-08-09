import cv2
import numpy as np
import functools
from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import get_clickmap, resolve_dot_path
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


@functools.lru_cache(maxsize=128)
def _load_template(name: str):
    """Cached grayscale template loader."""
    tpl = cv2.imread(f"assets/match_templates/{name}", cv2.IMREAD_GRAYSCALE)
    return tpl


def get_label_match(label_key: str, screenshot=None, return_meta=False):
    """
    Matches a label using its match_template and match_region or region_ref.
    Returns (x, y, w, h) by default.
    If return_meta=True, returns a dict with match + metadata.
    """
    entry = resolve_dot_path(label_key)
    if not entry:
        raise ValueError(f"Label key '{label_key}' not found in clickmap")

    template = _load_template(entry["match_template"])
    if template is None:
        raise FileNotFoundError(
            f"Template not found: assets/match_templates/{entry['match_template']}"
        )

    if screenshot is None:
        screenshot = capture_adb_screenshot()
        if screenshot is None:
            raise RuntimeError("Failed to capture screenshot")

    # Convert to grayscale only when needed
    if screenshot is not None and getattr(screenshot, "ndim", None) == 3:
        screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

    cm = get_clickmap()
    region = resolve_region(entry, cm)

    # Clamp region to screenshot bounds (defensive)
    H, W = screenshot.shape[:2]
    x = max(0, int(region["x"]))
    y = max(0, int(region["y"]))
    w = int(region["w"])
    h = int(region["h"])
    x2 = min(W, x + max(0, w))
    y2 = min(H, y + max(0, h))
    clamped_w = x2 - x
    clamped_h = y2 - y
    if clamped_w <= 0 or clamped_h <= 0:
        raise ValueError(
            f"Region out of bounds for {label_key}: {region} within image {W}x{H}"
        )

    region_img = screenshot[y : y + clamped_h, x : x + clamped_w]

    result = cv2.matchTemplate(region_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < entry.get("match_threshold", 0.9):
        raise ValueError(f"Match for {label_key} failed threshold: {max_val:.2f}")

    match_x = x + max_loc[0]
    match_y = y + max_loc[1]
    th, tw = template.shape[:2]

    if return_meta:
        return {
            "x": match_x,
            "y": match_y,
            "w": tw,
            "h": th,
            "menu": entry.get("menu"),
            "region_ref": entry.get("region_ref"),
            "order": entry.get("order"),
            "match_score": max_val,
        }
    return (match_x, match_y, tw, th)


def tap_label_now(label_key: str) -> bool:
    """
    Taps a label if it can be visually matched.
    Returns True if the tap succeeded, False otherwise.
    """
    try:
        x, y, w, h = get_label_match(label_key)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        log(f"[SKIP] tap_label_now failed for {label_key}: {e}", "WARN")
        return False

    entry = resolve_dot_path(label_key)
    offset = entry.get("tap_offset", None)

    tap_x = x + offset["x"] if offset else x + w // 2
    tap_y = y + offset["y"] if offset else y + h // 2

    log(f"TAP_LABEL_NOW: {label_key} at ({tap_x},{tap_y})", "ACTION")
    adb_shell(["input", "tap", str(tap_x), str(tap_y)])
    return True


def is_visible(label_key: str, screenshot=None) -> bool:
    try:
        get_label_match(label_key, screenshot=screenshot)
        return True
    except ValueError:
        return False
