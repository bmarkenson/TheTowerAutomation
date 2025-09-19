#!/usr/bin/env python3
from __future__ import annotations

"""
CLI tool to inspect visible upgrades using the template-free detector.

Examples:
  utils/scan_upgrades_ocr.py --category utility --side left --image screenshots/latest.png
"""

import argparse
import json
import sys

import cv2

# Ensure repository root on sys.path when run directly
if "." not in sys.path:
    sys.path.append(".")

from core.upgrade_detector import detect_all_upgrades, locate_upgrade_in_view


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scan visible upgrades using OCR + edge detection")
    ap.add_argument("--category", choices=["attack", "defense", "utility", "uw"], help="Optional menu override (auto-detected when omitted)")
    ap.add_argument("--side", choices=["left", "right"], help="Optional column filter")
    ap.add_argument("--image", default="screenshots/latest.png", help="Screenshot to analyze (BGR .png). Use 'adb' to capture live.")
    ap.add_argument("--slug", default=None, help="If provided, try to locate this canonical slug in view")
    args = ap.parse_args(argv)

    if args.image.lower() == 'adb':
        from core.ss_capture import capture_and_save_screenshot, LATEST_SCREENSHOT
        screen = capture_and_save_screenshot(LATEST_SCREENSHOT)
    else:
        screen = cv2.imread(args.image)
    if screen is None:
        print(json.dumps({"error": f"failed to load image: {args.image}"}))
        return 2

    results = detect_all_upgrades(screenshot=screen, category_hint=args.category)
    rows = results.get("rows", [])
    if args.side:
        rows = [r for r in rows if r.column == args.side]
    payload = {
        "category": results.get("category"),
        "category_inferred": results.get("category_inferred"),
        "banner_text": results.get("banner_text"),
        "side": args.side,
        "count": len(rows),
        "rows": [
            {
                "column": r.column,
                "y": r.y,
                "rect": r.rect,
                "label_rect": r.label_rect,
                "panel_rect": r.panel_rect,
                "label_text": r.label_text,
                "slug": r.slug,
                "status": r.status,
            }
            for r in rows
        ],
    }

    if args.slug:
        r = locate_upgrade_in_view(screen, results.get("category"), args.side, args.slug)
        payload["target"] = args.slug
        payload["target_row"] = (
            None
            if r is None
            else {
                "column": r.column,
                "y": r.y,
                "rect": r.rect,
                "label_rect": r.label_rect,
                "panel_rect": r.panel_rect,
                "label_text": r.label_text,
                "slug": r.slug,
                "status": r.status,
            }
        )

    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
