#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Dict, Optional, cast

from core.upgrade_buy_quantity import BuyQuantity
from core.upgrade_navigation import apply_menu_buy_quantities
from tools.cli.capture_utils import prepare_capture_recorder


def _parse_menu_quantities(entries: Optional[list[str]]) -> Dict[str, BuyQuantity]:
    if not entries:
        raise ValueError("At least one --menu-quantity entry is required")

    result: Dict[str, BuyQuantity] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError("--menu-quantity entries must be of the form menu=value")
        menu_name, quantity = entry.split("=", 1)
        result[menu_name.strip()] = cast(BuyQuantity, quantity.strip().lower())
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Update buy quantity selectors on specific menus")
    parser.add_argument(
        "--menu-quantity",
        action="append",
        required=True,
        help="Menu quantity assignment, e.g. attack=x10 (repeatable)",
    )
    parser.add_argument("--save-captures", help="Directory to save captured screenshots")
    args = parser.parse_args(argv)

    try:
        menu_quantities = _parse_menu_quantities(args.menu_quantity)
    except ValueError as exc:
        parser.error(str(exc))

    capture_fn = prepare_capture_recorder(args.save_captures)
    if capture_fn is None:
        from core.ss_capture import capture_adb_screenshot

        capture_fn = capture_adb_screenshot

    try:
        applied = apply_menu_buy_quantities(menu_quantities, capture_fn=capture_fn)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 2

    print(json.dumps({"menu_quantities": applied}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
