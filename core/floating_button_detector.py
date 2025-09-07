# core/floating_button_detector.py
"""
Floating (overlay) button detection and tapping helpers.

AUTO-SPEC legend used in docstrings below:
  R: Return value
  S: Side effects / external systems touched (tags: [adb], [cv2], [fs], [log])
  E: Errors raised (or error semantics)

Notes:
- Template paths are relative to assets/match_templates/.
- Inputs are OpenCV images in BGR order.
- Confidence thresholds are carried by clickmap entries (default handled upstream).
"""

import os
import cv2
from utils.template_matcher import match_region
from core.clickmap_access import get_entries_by_role
from utils.logger import log
from core.adb_utils import adb_shell


def tap_floating_button(name, buttons):
    """
    Tap a previously-detected floating button by name.

    AUTO-SPEC:
      signature: core.floating_button_detector.tap_floating_button(name: str, buttons: list[dict]) -> bool
      R: bool — True if a button with matching name was tapped; False if not found.
      S: [adb][log] — Injects a tap via ADB; emits an ACTION log line.
      E: None — adb_shell handles subprocess errors internally and returns None on failure.

    Args:
        name: The `name` field of the target button in `buttons`.
        buttons: A list of button dicts as produced by `detect_floating_buttons()`
                 (each contains keys: name, match_region, confidence, tap_point{ x,y }).

    Returns:
        True if a tap was issued; False if no matching button was present.
    """
    for b in buttons:
        if b["name"] == name:
            x, y = b["tap_point"]["x"], b["tap_point"]["y"]
            log(f"TAP_FLOATING: {name} at ({x},{y})", "ACTION")
            adb_shell(["input", "tap", str(x), str(y)])
            return True
    return False


def detect_floating_buttons(screen):
    """
    Detect all configured floating buttons in the given screen image.

    AUTO-SPEC:
      signature: core.floating_button_detector.detect_floating_buttons(screen: ndarray) -> list[dict]
      R: list[dict] — Each dict has:
           {"name": str,
            "match_region": {"x": int, "y": int, "w": int, "h": int},
            "confidence": float,
            "tap_point": {"x": int, "y": int}}
         Returns an empty list if none matched.
      S: [cv2][fs][log] — Reads template images from disk; uses OpenCV for matching; logs debug/errors.
      E: Per-entry exceptions are caught and logged; function returns partial results when possible.

    Args:
        screen: BGR ndarray of the current frame.

    Returns:
        A list of detected floating button descriptors suitable for `tap_floating_button()`.

    Notes:
        - Candidates are sourced from the clickmap entries whose roles include "floating_button".
        - For each candidate, a region match is attempted via `utils.template_matcher.match_region`.
        - Missing/unreadable template files are logged and skipped; detection continues for others.
    """
    results = []
    floating_buttons = get_entries_by_role("floating_button")

    for name, entry in floating_buttons.items():
        try:
            if not entry:
                continue

            pt, conf = match_region(screen, entry)
            if pt is None:
                log(f"{name} not matched (conf={conf:.2f})", "DEBUG")
                continue

            template_path = entry["match_template"]
            template_path_full = os.path.join("assets/match_templates", template_path)

            if not os.path.exists(template_path_full):
                log(f"Template file missing: {template_path}", "ERROR")
                continue

            template = cv2.imread(template_path_full)
            if template is None:
                log(f"Template failed to load (cv2.imread returned None): {template_path}", "ERROR")
                continue

            h, w = template.shape[:2]
            x, y = pt
            results.append({
                "name": name,
                "match_region": {"x": x, "y": y, "w": w, "h": h},
                "confidence": conf,
                "tap_point": {"x": x + w // 2, "y": y + h // 2}
            })
        except Exception as e:
            log(f"Exception during processing of {name}: {e}", "ERROR")

    return results
