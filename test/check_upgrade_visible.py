#!/usr/bin/env python3
"""
CLI tool: Check if an upgrade label is currently visible on screen.

Behavior:
- Optionally refresh screenshot.
- Verify the expected menu indicator is present (attack/defense/utility).
- Attempt to match the upgrade label within its visible region (left/right).

Usage examples:
- python3 test/check_upgrade_visible.py --key upgrades.attack.left.damage
- python3 test/check_upgrade_visible.py --category attack --side left --name damage --refresh

Exit codes:
 0 on success (tool ran), prints JSON-like result lines.
"""

import argparse
import sys
import os
import json
import cv2

sys.path.append(".")

from core.ss_capture import capture_and_save_screenshot
from core.matcher import get_match
from core.clickmap_access import resolve_dot_path


MENU_INDICATORS = {
    "attack": "indicators.menu_attack",
    "defense": "indicators.menu_defense",
    "utility": "indicators.menu_utility",
}


def parse_key_to_parts(dot_path: str):
    """Parse upgrades dot-path to (category, side, name). Returns (None, None, None) if invalid."""
    parts = dot_path.split(".") if dot_path else []
    if len(parts) >= 4 and parts[0] == "upgrades" and parts[2] in ("left", "right"):
        return parts[1], parts[2], parts[3]
    return None, None, None


def derive_key_from_parts(category: str, side: str, name: str):
    return f"upgrades.{category}.{side}.{name}"


def is_on_expected_menu(screen, category: str):
    indicator_key = MENU_INDICATORS.get(category)
    if not indicator_key:
        return False, 0.0
    _, conf = get_match(indicator_key, screenshot=screen)
    return (conf > 0), conf


def main():
    p = argparse.ArgumentParser(description="Check if an upgrade label is visible on screen.")
    p.add_argument("--image", default="screenshots/latest.png", help="Screenshot path to use")
    p.add_argument("--refresh", action="store_true", help="Capture a fresh screenshot first")
    p.add_argument("--key", help="Full upgrades dot-path (e.g., upgrades.attack.left.damage)")
    p.add_argument("--category", choices=["attack", "defense", "utility"], help="Upgrade category")
    p.add_argument("--side", choices=["left", "right"], help="Upgrade list side")
    p.add_argument("--name", help="Upgrade name key (e.g., damage)")

    args = p.parse_args()

    if args.refresh:
        capture_and_save_screenshot(args.image)

    if not os.path.exists(args.image):
        print(json.dumps({"error": f"image not found: {args.image}"}))
        return 1

    screen = cv2.imread(args.image)
    if screen is None:
        print(json.dumps({"error": "failed to load image"}))
        return 1

    if args.key:
        category, side, name = parse_key_to_parts(args.key)
        if not category:
            print(json.dumps({"error": "invalid --key; expected upgrades.<category>.<side>.<name>"}))
            return 1
        key = args.key
    else:
        if not (args.category and args.side and args.name):
            print(json.dumps({"error": "provide --key or all of --category/--side/--name"}))
            return 1
        category, side, name = args.category, args.side, args.name
        key = derive_key_from_parts(category, side, name)

    # Ensure clickmap entry exists
    entry = resolve_dot_path(key)
    if not entry:
        print(json.dumps({
            "key": key,
            "status": "missing_clickmap_entry"
        }))
        return 0

    on_menu, menu_conf = is_on_expected_menu(screen, category)
    match_point, conf = get_match(key, screenshot=screen)

    result = {
        "key": key,
        "category": category,
        "side": side,
        "name": name,
        "on_menu": bool(on_menu),
        "menu_confidence": round(float(menu_conf), 3),
        "visible": bool(match_point is not None),
        "confidence": round(float(conf), 3),
    }

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

