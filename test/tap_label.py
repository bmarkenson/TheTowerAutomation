#!/usr/bin/env python3
"""
Test helper: tap a label by dot-path.

Usage:
  test/tap_label.py upgrades.attack.left.damage
  test/tap_label.py indicators.menu_attack --refresh

Notes:
  - Uses core.label_tapper.tap_label_now(key) which performs matching
    inside the configured region and issues an ADB tap on success.
  - Optionally captures a fresh screenshot first with --refresh.
  - Returns JSON-like output describing the attempt.
"""

import argparse
import json
import sys
import time

from core.clickmap_access import resolve_dot_path
from core.ss_capture import capture_and_save_screenshot
from core.label_tapper import tap_label_now


def main() -> int:
    p = argparse.ArgumentParser(description="Tap a configured label by dot-path")
    p.add_argument("key", help="Clickmap dot-path (e.g., upgrades.attack.left.damage)")
    p.add_argument("--refresh", action="store_true", help="Capture a fresh screenshot before matching")
    p.add_argument("--retries", type=int, default=1, help="Max attempts if initial match fails")
    p.add_argument("--sleep", type=float, default=0.5, help="Seconds to wait between retries")
    args = p.parse_args()

    # Validate clickmap entry exists
    entry = resolve_dot_path(args.key)
    if not entry:
        print(json.dumps({
            "key": args.key,
            "status": "missing_clickmap_entry"
        }))
        return 1

    if args.refresh:
        capture_and_save_screenshot()

    attempts = 0
    success = False
    while attempts < max(1, args.retries) and not success:
        attempts += 1
        success = tap_label_now(args.key)
        if success:
            break
        if attempts < args.retries:
            time.sleep(max(0.0, args.sleep))

    print(json.dumps({
        "key": args.key,
        "status": "tapped" if success else "not_found",
        "attempts": attempts
    }))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())

