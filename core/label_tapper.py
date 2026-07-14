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

from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.adb_utils import adb_shell
from core.matcher import match_entry_result, normalize_region, resolve_match_region
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
    return normalize_region(r)


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
    return resolve_match_region(entry, clickmap)


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

    if screenshot is None:
        screenshot = capture_adb_screenshot()
        if screenshot is None:
            raise RuntimeError("Failed to capture screenshot")
    result = match_entry_result(
        screenshot,
        entry,
        grayscale=True,
        padding=0,
        clickmap=get_clickmap(),
    )
    if result.failure_reason:
        raise ValueError(f"Match for {label_key} failed: {result.failure_reason}")
    if not result.matched or result.bbox is None:
        raise ValueError(
            f"Match for {label_key} failed threshold: {result.confidence:.2f}"
        )

    match_x, match_y, tw, th = result.bbox

    if return_meta:
        return {
            "x": match_x,
            "y": match_y,
            "w": tw,
            "h": th,
            "menu": entry.get("menu"),
            "region_ref": entry.get("region_ref"),
            "order": entry.get("order"),
            "match_score": result.confidence,
        }
    return (match_x, match_y, tw, th)


def is_visible(label_key: str, screenshot=None) -> bool:
    """Return True if label is matched above threshold; else False."""
    try:
        get_label_match(label_key, screenshot=screenshot)
        return True
    except (ValueError, KeyError, FileNotFoundError, RuntimeError):
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
