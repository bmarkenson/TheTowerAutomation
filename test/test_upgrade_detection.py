#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, Optional, Sequence, Tuple

import cv2

if "." not in sys.path:
    sys.path.append(".")

from core.upgrade_box_detector import annotate_boxes, detect_visible_boxes
from core.upgrade_navigation import find_upgrade


def _run_single(image_path: str, annotate_path: Optional[str], menu: Optional[str]) -> int:
    if image_path.lower() == "adb":
        from core.ss_capture import capture_adb_screenshot

        screenshot = capture_adb_screenshot()
        if screenshot is None:
            print(json.dumps({"error": "failed to capture screenshot"}))
            return 2
    else:
        screenshot = cv2.imread(image_path)
        if screenshot is None:
            print(json.dumps({"error": f"failed to read {image_path}"}))
            return 2

    boxes = detect_visible_boxes(screenshot, menu=menu)
    payload_columns = {}

    print("Detection summary:")
    for column, box_list in boxes.items():
        print(f"  {column}: {len(box_list)} box(es)")
        column_payload = []
        for idx, box in enumerate(box_list, start=1):
            rect_info = f"x={box.rect[0]}, y={box.rect[1]}, w={box.rect[2]}, h={box.rect[3]}"
            label = box.text or ""
            raw = box.raw_text if box.raw_text and box.raw_text != label else None
            label_info = f" text='{label}'" if label else ""
            if raw:
                label_info += f" raw='{box.raw_text}'"
            if box.confidence >= 0:
                label_info += f" (conf={box.confidence:.1f})"
            if box.match_score is not None:
                label_info += f" match={box.match_score:.2f}"
            if box.affordability:
                label_info += f" status={box.affordability}"
            if box.toggles:
                toggle_desc = ", ".join(f"{k}={v}" for k, v in box.toggles.items())
                label_info += f" toggles({toggle_desc})"
            print(f"    #{idx}: {rect_info}{label_info}")

            entry = {"rect": box.rect}
            if box.text:
                entry["text"] = box.text
            if box.confidence >= 0:
                entry["confidence"] = box.confidence
            if box.raw_text and box.raw_text != box.text:
                entry["raw_text"] = box.raw_text
            if box.match_score is not None:
                entry["match_score"] = box.match_score
            if box.affordability:
                entry["affordability"] = box.affordability
            if box.affordability_metrics:
                entry["affordability_metrics"] = box.affordability_metrics
            if box.toggles:
                entry["toggles"] = box.toggles
            if box.toggle_metrics:
                entry["toggle_metrics"] = box.toggle_metrics
            column_payload.append(entry)
        payload_columns[column] = column_payload

    payload = {"columns": payload_columns}

    print(json.dumps(payload))

    if annotate_path:
        all_boxes = [box for rows in boxes.values() for box in rows]
        annotated = annotate_boxes(screenshot, all_boxes)
        cv2.imwrite(annotate_path, annotated)

    return 0

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Detect visible upgrade boxes")
    parser.add_argument("--image", default="adb", help="Screenshot path or 'adb'")
    parser.add_argument("--annotate", help="Optional output path for annotated image")
    parser.add_argument("--menu", help="Optional upgrade menu context (attack/defense/utility)")
    parser.add_argument(
        "--find-upgrade",
        help="Locate an upgrade by name using optional scrolling (menu inferred if omitted)",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=12,
        help="Maximum scroll attempts when using --find-upgrade",
    )
    parser.add_argument(
        "--buy-if-affordable",
        action="store_true",
        help="Attempt to tap the upgrade's purchase area when it is affordable",
    )
    args = parser.parse_args(argv)

    if args.find_upgrade:
        if args.image.lower() != "adb":
            print(json.dumps({"error": "--find-upgrade requires live capture; use --image adb"}))
            return 2

        result = find_upgrade(
            args.menu,
            args.find_upgrade,
            max_scrolls=args.max_scrolls,
            attempt_purchase=args.buy_if_affordable,
        )

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
            "purchase": {
                "attempted": result.purchase_attempted,
                "sent": result.purchase_sent,
                "reason": result.purchase_reason,
            },
        }
        print(json.dumps(payload))
        return 0

    return _run_single(args.image, args.annotate, args.menu)


if __name__ == "__main__":
    raise SystemExit(main())
