#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Dict, Optional, cast

from core.upgrade_buy_quantity import BuyQuantity
from core.upgrade_navigation import find_upgrade
from tools.cli.capture_utils import prepare_capture_recorder


def _parse_menu_quantities(entries: Optional[list[str]]) -> Dict[str, BuyQuantity]:
    result: Dict[str, BuyQuantity] = {}
    if not entries:
        return result
    for entry in entries:
        if "=" not in entry:
            raise ValueError("--menu-quantity entries must be of the form menu=value")
        menu_name, quantity = entry.split("=", 1)
        result[menu_name.strip()] = cast(BuyQuantity, quantity.strip().lower())
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Locate an upgrade via live device scrolling")
    parser.add_argument("label", help="Canonical upgrade label to find")
    parser.add_argument("--menu", help="Optional menu context (attack/defense/utility/ultimate)")
    parser.add_argument("--max-scrolls", type=int, default=12, help="Maximum scroll attempts")
    parser.add_argument("--buy-if-affordable", action="store_true", help="Tap the upgrade if affordable")
    parser.add_argument("--quantity", help="Override purchase quantity for this search")
    parser.add_argument(
        "--menu-quantity",
        action="append",
        help="Default menu quantities, e.g. attack=x10 (repeatable)",
    )
    parser.add_argument("--save-captures", help="Directory to save captured screenshots")
    args = parser.parse_args(argv)

    try:
        menu_quantities = _parse_menu_quantities(args.menu_quantity)
        find_kwargs = {}
        if capture_fn is not None:
            find_kwargs["capture_fn"] = capture_fn

        result = find_upgrade(
            args.menu,
            args.label,
            max_scrolls=args.max_scrolls,
            attempt_purchase=args.buy_if_affordable,
            purchase_quantity=cast(BuyQuantity, args.quantity.strip().lower()) if args.quantity else None,
            menu_buy_quantities=menu_quantities or None,
            **find_kwargs,
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 2

    if result is None:
        print(json.dumps({"result": None, "error": "upgrade not found"}))
        return 1

    payload = {
        "menu": result.menu,
        "column": result.column,
        "index": result.index,
        "label": result.label,
        "box": {
            "rect": result.box.rect,
            "text": result.box.text,
            "affordability": result.box.affordability,
            "toggles": result.box.toggles,
        },
        "buy_quantity": result.buy_quantity,
        "purchase": {
            "attempted": result.purchase_attempted,
            "sent": result.purchase_sent,
            "reason": result.purchase_reason,
        },
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
