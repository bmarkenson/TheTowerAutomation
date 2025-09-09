#!/usr/bin/env python3
"""
CLI/utility to navigate to an upgrades menu and locate a specific upgrade label,
optionally tapping it.

Uses high-level helpers in core.scan_upgrades:
  - ensure_menu(category)
  - goto_and_find_upgrade(category, side, name, max_pages)
  - derive_key(category, side|None, name) to build the dot path

Examples:
  # Navigate to Attack, left column, find 'damage' and print bbox
  utils/find_upgrade.py --category attack --side left --name damage

  # Auto-resolve side (looks in clickmap), then tap the label
  utils/find_upgrade.py --category utility --name package_chance --tap
"""

import argparse
import json
import sys

from utils.logger import log
from core.scan_upgrades import (
    ensure_menu,
    goto_and_find_upgrade,
    derive_key,
)
from core.label_tapper import tap_label_now


def main() -> int:
    ap = argparse.ArgumentParser(description="Find an upgrade label and optionally tap it")
    ap.add_argument("--category", required=False, choices=["attack", "defense", "utility", "uw"], default=None, help="Upgrades menu (optional; auto-resolve if omitted)")
    ap.add_argument("--name", required=True, help="Upgrade key name (e.g., damage, package_chance)")
    ap.add_argument("--side", choices=["left", "right"], default=None, help="Column side; omit to auto-resolve via clickmap")
    ap.add_argument("--max-pages", type=int, default=30, help="Max page-down attempts while searching")
    ap.add_argument("--tap", action="store_true", help="Tap the label after finding it")
    args = ap.parse_args()

    # Ensure correct menu is visible
    # goto_and_find_upgrade now resolves category/side if they are omitted
    bbox = goto_and_find_upgrade(args.category, args.side, args.name, max_pages=args.max_pages)
    # Re-derive to report resolved category/side in output
    from core.scan_upgrades import derive_key
    try:
        _, resolved_side, resolved_category = derive_key(args.category, args.side, args.name)
    except Exception:
        resolved_side, resolved_category = args.side, args.category

    result = {
        "category": resolved_category,
        "name": args.name,
        "side": resolved_side,
        "bbox": bbox,
        "tapped": False,
        "status": "found" if bbox else "not_found",
    }

    if bbox and args.tap:
        # Rebuild the dot-path with the resolved side
        key, resolved_side, _ = derive_key(args.category, args.side, args.name)
        ok = tap_label_now(key)
        result["tapped"] = bool(ok)

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
