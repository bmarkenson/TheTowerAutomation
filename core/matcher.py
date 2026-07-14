# core/matcher.py
"""
Centralized, clickmap-backed matching utilities.

Public API:
    get_match_result(dot_path, *, screenshot, template_dir=...)
        → MatchResult(bbox, center, confidence, threshold, search_region)
    get_match(dot_path, *, screenshot, template_dir="assets/match_templates")
        → ((x, y), confidence) or (None, confidence)

Private helper (for shims/tests only):
    _match_entry(screenshot, entry, template_dir)
        → ((x, y), confidence) or (None, confidence)

Notes:
- Uses OpenCV template matching (cv2.TM_CCOEFF_NORMED).
- Reads template/region/threshold from clickmap entries (via clickmap.json).
- Expands the search region by optional 'match_padding' (default 12px), clamped to screen bounds.
- Supplies the shared low-level engine used by state detection and label tapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import cv2
import numpy as np  # used by detect_floating_gem_square

from core.clickmap_access import resolve_dot_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE_DIR = PROJECT_ROOT / "assets" / "match_templates"
Rect = Tuple[int, int, int, int]


@dataclass(frozen=True)
class MatchResult:
    """Complete result from one template-match evaluation.

    ``bbox`` is the best candidate even when it is below threshold. Callers must
    use ``matched`` before treating it as a valid detection.
    """

    bbox: Optional[Rect]
    confidence: float
    threshold: float
    search_region: Optional[Rect]
    failure_reason: Optional[str] = None

    @property
    def matched(self) -> bool:
        return (
            self.failure_reason is None
            and self.bbox is not None
            and self.confidence >= self.threshold
        )

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        if self.bbox is None:
            return None
        x, y, w, h = self.bbox
        return x + w // 2, y + h // 2


def normalize_region(region: Any) -> Dict[str, int]:
    """Normalize a supported region representation to ``x/y/w/h`` integers."""

    if isinstance(region, Mapping) and isinstance(region.get("match_region"), Mapping):
        region = region["match_region"]
    if isinstance(region, Mapping) and all(key in region for key in ("x", "y", "w", "h")):
        return {key: int(region[key]) for key in ("x", "y", "w", "h")}
    if isinstance(region, Mapping) and all(
        key in region for key in ("left", "top", "width", "height")
    ):
        return {
            "x": int(region["left"]),
            "y": int(region["top"]),
            "w": int(region["width"]),
            "h": int(region["height"]),
        }
    if isinstance(region, (list, tuple)) and len(region) == 4:
        x, y, w, h = region
        return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
    raise ValueError(f"Unsupported region format: {region!r}")


def resolve_match_region(
    entry: Mapping[str, Any],
    clickmap: Optional[Mapping[str, Any]] = None,
) -> Dict[str, int]:
    """Resolve an entry's direct or shared match region."""

    if "match_region" in entry:
        return normalize_region(entry["match_region"])
    region_ref = entry.get("region_ref")
    if not region_ref:
        raise ValueError("No match_region or region_ref defined")
    shared = resolve_dot_path(f"_shared_match_regions.{region_ref}", clickmap)
    if not isinstance(shared, Mapping):
        raise ValueError(f"Unknown region_ref '{region_ref}'")
    return normalize_region(shared)


@lru_cache(maxsize=256)
def _read_template_cached(
    path: str,
    imread_flag: int,
    mtime_ns: int,
    size: int,
):
    # mtime/size are cache-key inputs so an asset refresh is observed without a
    # process restart. They are intentionally unused in the function body.
    del mtime_ns, size
    return cv2.imread(path, imread_flag)


def _load_template(path: Path, *, grayscale: bool):
    if not path.is_file():
        raise FileNotFoundError(f"Template not found: {path}")
    stat = path.stat()
    flag = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
    template = _read_template_cached(
        str(path.resolve()),
        flag,
        stat.st_mtime_ns,
        stat.st_size,
    )
    if template is None:
        raise ValueError(f"Failed to load template: {path}")
    return template


def match_entry_result(
    screenshot,
    entry: Mapping[str, Any],
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
    *,
    grayscale: bool = False,
    padding: Optional[int] = None,
    clickmap: Optional[Mapping[str, Any]] = None,
) -> MatchResult:
    """Match a resolved clickmap entry and return geometry plus confidence.

    ``grayscale`` and ``padding`` are explicit compatibility profiles. State
    detection currently uses color and entry/default padding; label tapping uses
    grayscale with zero padding. Both profiles share all other matching logic.
    """

    threshold = float(entry.get("match_threshold", 0.9)) if entry else 0.9
    if not entry or "match_template" not in entry:
        return MatchResult(None, 0.0, threshold, None, "missing match_template")
    try:
        region = resolve_match_region(entry, clickmap)
    except ValueError as exc:
        return MatchResult(None, 0.0, threshold, None, str(exc))

    if screenshot is None or not hasattr(screenshot, "shape") or len(screenshot.shape) < 2:
        return MatchResult(None, 0.0, threshold, None, "invalid screenshot")

    x, y, w, h = (region[key] for key in ("x", "y", "w", "h"))
    effective_padding = int(entry.get("match_padding", 12) if padding is None else padding)
    if w <= 0 or h <= 0 or effective_padding < 0:
        return MatchResult(None, 0.0, threshold, None, "invalid match region or padding")

    screen_h, screen_w = screenshot.shape[:2]
    x1 = max(0, x - effective_padding)
    y1 = max(0, y - effective_padding)
    x2 = min(screen_w, x + w + effective_padding)
    y2 = min(screen_h, y + h + effective_padding)
    search_region = (x1, y1, x2 - x1, y2 - y1)
    if x1 >= x2 or y1 >= y2:
        return MatchResult(None, 0.0, threshold, search_region, "match region is out of bounds")

    template_path = Path(template_dir) / str(entry["match_template"])
    template = _load_template(template_path, grayscale=grayscale)
    region_img = screenshot[y1:y2, x1:x2]
    if grayscale and getattr(region_img, "ndim", None) == 3:
        region_img = cv2.cvtColor(region_img, cv2.COLOR_BGR2GRAY)

    region_h, region_w = region_img.shape[:2]
    template_h, template_w = template.shape[:2]
    if region_h < template_h or region_w < template_w:
        reason = (
            f"template {template_w}x{template_h} exceeds search region "
            f"{region_w}x{region_h}"
        )
        return MatchResult(None, 0.0, threshold, search_region, reason)

    result = cv2.matchTemplate(region_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    bbox = (x1 + max_loc[0], y1 + max_loc[1], template_w, template_h)
    return MatchResult(bbox, float(max_val), threshold, search_region)


def _match_entry(
    screenshot,
    entry: Dict[str, Any],
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
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
    result = match_entry_result(screenshot, entry, template_dir=template_dir)
    return (result.center if result.matched else None), result.confidence


def get_match_result(
    dot_path: str,
    *,
    screenshot,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
) -> MatchResult:
    """Resolve a clickmap entry and return its complete match result."""

    entry = resolve_dot_path(dot_path)
    if not isinstance(entry, Mapping):
        return MatchResult(None, 0.0, 0.9, None, f"unknown clickmap path '{dot_path}'")
    return match_entry_result(screenshot, entry, template_dir=template_dir)


def get_match(
    dot_path: str,
    *,
    screenshot,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
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
    result = get_match_result(dot_path, screenshot=screenshot, template_dir=template_dir)
    return (result.center if result.matched else None), result.confidence


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
