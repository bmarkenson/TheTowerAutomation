"""
core/label_tapper.py

Spec legend for embedded YAML blocks
---
spec_legend:
  r: "Return value"
  s: "Side effects"
  e: "Errors/exceptions (raised or propagated)"
  params: "Parameter annotations"
  notes: "Important details/guards/defaults"
defaults:
  match_threshold: 0.90
  image_space: "OpenCV BGR; origin top-left; regions {x,y,w,h}"
  matching: "cv2.TM_CCOEFF_NORMED"
---
"""

import cv2
import numpy as np
import functools
from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.adb_utils import adb_shell
from utils.logger import log


def _normalize_region(r):
    """
    ---
    spec:
      r: "dict{x:int,y:int,w:int,h:int}"
      s: []
      e:
        - "ValueError on unsupported/invalid region format"
      params:
        r: "dict|tuple|list — supports entry, entry.match_region, {left,top,width,height}, or (x,y,w,h)"
      notes:
        - "Coerces values to int"
    ---
    """
    if isinstance(r, dict) and "match_region" in r and isinstance(r["match_region"], dict):
        r = r["match_region"]
    if isinstance(r, dict) and all(k in r for k in ("x", "y", "w", "h")):
        return {"x": int(r["x"]), "y": int(r["y"]), "w": int(r["w"]), "h": int(r["h"])}
    if isinstance(r, dict) and all(k in r for k in ("left", "top", "width", "height")):
        return {"x": int(r["left"]), "y": int(r["top"]), "w": int(r["width"]), "h": int(r["height"])}
    if isinstance(r, (list, tuple)) and len(r) == 4:
        x, y, w, h = r
        return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
    raise ValueError(f"Unsupported region format: {r!r}")


def resolve_region(entry, clickmap):
    """
    ---
    spec:
      r: "dict{x:int,y:int,w:int,h:int}"
      s: []
      e:
        - "ValueError if region_ref unknown or neither match_region nor region_ref present"
      params:
        entry: "clickmap entry dict"
        clickmap: "dict — full clickmap mapping"
      notes:
        - "match_region takes precedence over region_ref"
        - "region_ref is resolved under _shared_match_regions.<name>"
    ---
    """
    if "match_region" in entry:
        return _normalize_region(entry["match_region"])
    elif "region_ref" in entry:
        ref = entry["region_ref"]
        shared = clickmap.get("_shared_match_regions", {})
        if ref not in shared:
            raise ValueError(f"Unknown region_ref '{ref}'")
        return _normalize_region(shared[ref])
    else:
        raise ValueError("No match_region or region_ref defined")


@functools.lru_cache(maxsize=128)
def _load_template(name: str):
    """
    ---
    spec:
      r: "ndarray|None (grayscale template)"
      s: ["fs"]
      e: []
      params:
        name: "str — path relative to assets/match_templates/"
      notes:
        - "Uses cv2.imread(..., IMREAD_GRAYSCALE); returns None if missing"
        - "Cached via lru_cache(128)"
    ---
    Cached grayscale template loader.
    """
    tpl = cv2.imread(f"assets/match_templates/{name}", cv2.IMREAD_GRAYSCALE)
    return tpl


def get_label_match(label_key: str, screenshot=None, return_meta=False):
    """
    ---
    spec:
      r: "(x:int,y:int,w:int,h:int) | dict(meta)"
      s: ["adb?", "cv2"]
      e:
        - "ValueError if key missing / region out-of-bounds / threshold fail"
        - "FileNotFoundError if template file not found"
        - "RuntimeError if screenshot capture fails"
      params:
        label_key: "str — clickmap dot-path"
        screenshot: "ndarray|None — BGR or gray; capture via ADB when None"
        return_meta: "bool — when True, return dict with metadata and match_score"
      notes:
        - "Converts screenshot to grayscale for matching"
        - "Threshold defaults to 0.9 unless entry.match_threshold provided"
        - "Clamps region to image bounds defensively"
    ---
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
    ---
    spec:
      r: "bool — True if tap issued, else False"
      s: ["adb", "log"]
      e: []
      params:
        label_key: "str"
      notes:
        - "Catches ValueError/FileNotFoundError/RuntimeError from get_label_match and returns False"
        - "Supports optional entry.tap_offset {x,y}"
        - "Taps center of matched bbox when no offset"
    ---
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
    """
    ---
    spec:
      r: "bool"
      s: ["adb?"]
      e: []
      params:
        label_key: "str"
        screenshot: "ndarray|None"
      notes:
        - "Returns True when get_label_match succeeds, else False (ValueError path)"
    ---
    """
    try:
        get_label_match(label_key, screenshot=screenshot)
        return True
    except ValueError:
        return False


def _get_shared_upgrade_region(side: str):
    """
    ---
    spec:
      r: "tuple(x:int,y:int,w:int,h:int)"
      s: []
      e:
        - "RuntimeError if shared region missing/invalid"
      params:
        side: "'left'|'right'"
      notes:
        - "Resolves _shared_match_regions.upgrades_<side>.match_region"
    ---
    Resolve _shared_match_regions.upgrades_left/right -> (x,y,w,h).
    Raises RuntimeError if missing.
    """
    key = f"_shared_match_regions.upgrades_{side}"
    entry = resolve_dot_path(key)
    if not entry or "match_region" not in entry:
        raise RuntimeError(f"Shared region not found or invalid: {key}")
    r = entry["match_region"]
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])


def swipe_relative_in_region(region, start_frac=(0.50, 0.82), end_frac=(0.50, 0.25), duration_ms=260):
    """
    ---
    spec:
      r: "None"
      s: ["adb", "log"]
      e: []
      params:
        region: "tuple(x,y,w,h) — base rect in screen coords"
        start_frac: "(fx,fy) — relative inside inset rect"
        end_frac: "(fx,fy) — relative inside inset rect"
        duration_ms: "int"
      notes:
        - "Insets region by max(12, 1.2% of min(w,h)) to avoid borders"
        - "Clamps fractions to [0..1]"
    ---
    Send a raw ADB swipe using start/end positions relative to a region rect.

    region: (x,y,w,h)
    start_frac/end_frac: (fx, fy) with 0..1 inside the region AFTER insets.
    """
    x, y, w, h = map(int, region)
    # inset a bit to avoid borders/accidental chrome hits
    inset = max(12, int(0.012 * min(w, h)))
    x0, y0 = x + inset, y + inset
    w2, h2 = max(1, w - 2 * inset), max(1, h - 2 * inset)

    sx = int(x0 + max(0.0, min(1.0, start_frac[0])) * w2)
    sy = int(y0 + max(0.0, min(1.0, start_frac[1])) * h2)
    ex = int(x0 + max(0.0, min(1.0, end_frac[0])) * w2)
    ey = int(y0 + max(0.0, min(1.0, end_frac[1])) * h2)

    log(f"SWIPE_REL: ({sx},{sy})→({ex},{ey}) in {duration_ms}ms", "ACTION")
    adb_shell(["input", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration_ms)])


def page_column(side: str, direction: str, strength: str = "page", duration_ms: int = 260):
    """
    ---
    spec:
      r: "None"
      s: ["adb", "log"]
      e:
        - "ValueError if side/direction/strength invalid"
      params:
        side: "'left'|'right'"
        direction: "'up'|'down'"
        strength: "'page'|'micro'"
        duration_ms: "int"
      notes:
        - "Chooses pre-tuned swipe vectors per strength & direction"
        - "Uses shared upgrade column region"
    ---
    Scroll the upgrades list adaptively within the shared column region.

    side: "left" | "right"
    direction: "up" (toward TOP/earlier rows) or "down" (toward BOTTOM/later rows)
    strength: "page" (~75% height) or "micro" (~25% height)
    """
    side = side.lower()
    if side not in ("left", "right"):
        raise ValueError("side must be 'left' or 'right'")
    direction = direction.lower()
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    strength = strength.lower()
    if strength not in ("page", "micro"):
        raise ValueError("strength must be 'page' or 'micro'")

    region = _get_shared_upgrade_region(side)

    if strength == "page":
        # Large move
        up_start, up_end   = (0.50, 0.32), (0.50, 0.65)  # finger moves DOWN (content goes UP)
        down_start, down_end = (0.50, 0.65), (0.50, 0.32)
    else:
        # Smaller move for fine search
        up_start, up_end   = (0.50, 0.45), (0.50, 0.55)
        down_start, down_end = (0.50, 0.55), (0.50, 0.45)

    if direction == "up":
        swipe_relative_in_region(region, start_frac=up_start, end_frac=up_end, duration_ms=duration_ms)
    else:
        swipe_relative_in_region(region, start_frac=down_start, end_frac=down_end, duration_ms=duration_ms)
