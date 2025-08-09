#!/usr/bin/env python3

import argparse
import sys
from core.clickmap_access import resolve_dot_path, tap_now, swipe_now
from core.label_tapper import tap_label_now
from utils.logger import log


def run_gesture(dot_path):
    """
    Execute a single gesture defined in clickmap.json.

    Resolution order:
      1) If entry has "match_template": use visual tap via tap_label_now(dot_path).
      2) Else if entry has "tap": perform static tap via tap_now(dot_path).
      3) Else if entry has "swipe": perform swipe via swipe_now(dot_path).
      4) Otherwise: log an error.

    Args:
      dot_path (str): Dot-path key to an entry in clickmap.json.

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
        success = tap_label_now(dot_path)
        if not success:
            log(f"[ERROR] tap_label_now failed for: {dot_path}", "FAIL")
            return False
        return True

    # 2. Try static tap
    if "tap" in entry:
        log(f"[INFO] Executing static tap gesture: {dot_path}", "DEBUG")
        tap_now(dot_path)
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
      test/test_gesture.py <dot_path>

    Behavior:
      Resolves <dot_path> in clickmap.json and triggers the appropriate gesture.
      Exit code is 0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(description="Execute a single gesture by dot-path from clickmap.json")
    parser.add_argument("dot_path", help="Dot path to the gesture in clickmap.json")
    args = parser.parse_args()

    ok = run_gesture(args.dot_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
