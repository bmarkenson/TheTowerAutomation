#!/usr/bin/env python3

import argparse
from core.clickmap_access import resolve_dot_path, tap_now, swipe_now
from core.label_tapper import tap_label_now
from utils.logger import log

def run_gesture(dot_path):
    entry = resolve_dot_path(dot_path)
    if not entry:
        log(f"[ERROR] No entry found for dot_path: '{dot_path}'", "FAIL")
        return

    # 1. Try visual tap if match_template is defined
    if "match_template" in entry:
        log(f"[INFO] Using visual matcher for: {dot_path}", "DEBUG")
        success = tap_label_now(dot_path)
        if not success:
            log(f"[ERROR] tap_label_now failed for: {dot_path}", "FAIL")
        return

    # 2. Try static tap
    if "tap" in entry:
        log(f"[INFO] Executing static tap gesture: {dot_path}", "DEBUG")
        tap_now(dot_path)
        return

    # 3. Try swipe
    if "swipe" in entry:
        log(f"[INFO] Executing swipe gesture: {dot_path}", "DEBUG")
        swipe_now(dot_path)
        return

    log(f"[ERROR] No actionable gesture defined in entry: '{dot_path}'", "FAIL")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dot_path", help="Dot path to the gesture in clickmap.json")
    args = parser.parse_args()
    run_gesture(args.dot_path)

if __name__ == "__main__":
    main()


