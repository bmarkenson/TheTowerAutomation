#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Optional

import cv2  # type: ignore

from core.upgrade_box_detector import annotate_boxes, detect_visible_boxes
from tools.cli.capture_utils import prepare_capture_recorder


def _load_screenshot(image_path: str, capture_fn=None):
    if image_path.lower() == "adb":
        if capture_fn is None:
            from core.ss_capture import capture_adb_screenshot

            capture_fn = capture_adb_screenshot
        screenshot = capture_fn()
        if screenshot is None:
            raise RuntimeError("failed to capture screenshot")
        return screenshot

    screenshot = cv2.imread(image_path)
    if screenshot is None:
        raise RuntimeError(f"failed to read {image_path}")
    return screenshot


def run_detection(
    *,
    image_path: str,
    menu: Optional[str],
    annotate_path: Optional[str],
    capture_fn=None,
) -> dict:
    screenshot = _load_screenshot(image_path, capture_fn=capture_fn)
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

    if annotate_path:
        all_boxes = [box for rows in boxes.values() for box in rows]
        annotated = annotate_boxes(screenshot, all_boxes)
        cv2.imwrite(annotate_path, annotated)

    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Detect visible upgrade boxes")
    parser.add_argument("--image", default="adb", help="Screenshot path or 'adb'")
    parser.add_argument("--menu", help="Optional upgrade menu context")
    parser.add_argument("--annotate", help="Optional output path for annotated image")
    parser.add_argument("--save-captures", help="Directory to save captured screenshots")
    args = parser.parse_args(argv)

    capture_fn = prepare_capture_recorder(args.save_captures)

    try:
        payload = run_detection(
            image_path=args.image,
            menu=args.menu,
            annotate_path=args.annotate,
            capture_fn=capture_fn,
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 2

    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_detection"]
