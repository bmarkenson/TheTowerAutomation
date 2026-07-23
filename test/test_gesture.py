#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

import cv2

from core.clickmap_access import resolve_dot_path
from core.input import tap_if_visible, tap_unchecked_for_tooling, swipe_now
from core.ss_capture import capture_and_save_screenshot
from utils.logger import log


def _load_screenshot(path: str | Path) -> "cv2.Mat | None":
    """Load a screenshot from disk if available, logging failures."""

    if not path:
        return None
    img = cv2.imread(str(path))
    if img is None:
        log(f"[WARN] Failed to load screenshot from {path}", "WARN")
    return img


def run_gesture(dot_path, screenshot=None):
    """
    Execute a single gesture defined in clickmap.json.

    Resolution order:
      1) If entry has "match_template": use visual tap via tap_if_visible(dot_path).
      2) Else if entry has "tap": perform an explicit tooling-only static tap.
      3) Else if entry has "swipe": perform swipe via swipe_now(dot_path).
      4) Otherwise: log an error.

    Args:
      dot_path (str): Dot-path key to an entry in clickmap.json.
      screenshot (np.ndarray | None): Optional BGR screenshot used for visual taps.

    Returns:
      bool: True if a gesture was executed (and, for visual path, the match/tap returned True);
            False if resolution failed or no actionable gesture was found.

    Errors:
      Exceptions from resolve_dot_path are caught and logged; function returns False.
      Underlying ADB/tap errors may surface via called utilities (logged elsewhere).
    """
    try:
        entry = resolve_dot_path(dot_path)
    except Exception as e:
        log(f"[ERROR] Failed to resolve dot_path '{dot_path}': {e}", "FAIL")
        return False

    if not entry:
        log(f"[ERROR] No entry found for dot_path: '{dot_path}'", "FAIL")
        return False

    # 1. Try visual tap if match_template is defined
    if "match_template" in entry:
        log(f"[INFO] Using visual matcher for: {dot_path}", "DEBUG")
        success = tap_if_visible(dot_path, screenshot=screenshot)
        if not success:
            log(f"[ERROR] tap_if_visible failed for: {dot_path}", "FAIL")
            return False
        return True

    # 2. Try static tap
    if "tap" in entry:
        log(f"[INFO] Executing static tap gesture: {dot_path}", "DEBUG")
        tap_unchecked_for_tooling(
            dot_path,
            reason="explicit test_gesture invocation",
        )
        return True

    # 3. Try swipe
    if "swipe" in entry:
        log(f"[INFO] Executing swipe gesture: {dot_path}", "DEBUG")
        swipe_now(dot_path)
        return True

    log(f"[ERROR] No actionable gesture defined in entry: '{dot_path}'", "FAIL")
    return False


def main():
    """
    CLI entrypoint for executing a single clickmap gesture.

    Usage:
      test/test_gesture.py <dot_path> [--screenshot path] [--refresh]

    Behavior:
      Optionally refreshes/loads a screenshot for visual taps, resolves <dot_path>,
      and triggers the appropriate tap/swipe. Exit code is 0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="Execute a single gesture by dot-path from clickmap.json"
    )
    parser.add_argument("dot_path", help="Dot path to the gesture in clickmap.json")
    parser.add_argument(
        "--screenshot",
        default="screenshots/latest.png",
        help="Screenshot to use for visual taps (default: %(default)s)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Capture a fresh screenshot before executing the tap",
    )
    args = parser.parse_args()

    screenshot = None
    if args.refresh:
        screenshot = capture_and_save_screenshot(args.screenshot)
        if screenshot is None:
            log(
                "[WARN] Screenshot capture failed; falling back to existing file if available",
                "WARN",
            )

    if screenshot is None and args.screenshot:
        screenshot = _load_screenshot(args.screenshot)

    ok = run_gesture(args.dot_path, screenshot=screenshot)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
