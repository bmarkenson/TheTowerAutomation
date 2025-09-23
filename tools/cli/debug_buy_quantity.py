#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, cast

import cv2

from core.upgrade_buy_quantity import (
    BuyQuantity,
    get_buy_quantity_regions,
    read_buy_quantity_from_image,
)
from tools.cli.capture_utils import prepare_capture_recorder


def _load_image(image_path: str, capture_fn=None):
    if image_path.lower() == "adb":
        if capture_fn is None:
            from core.ss_capture import capture_adb_screenshot

            capture_fn = capture_adb_screenshot
        screenshot = capture_fn()
        if screenshot is None:
            raise RuntimeError("Failed to capture screenshot from device")
        return screenshot

    screenshot = cv2.imread(image_path)
    if screenshot is None:
        raise RuntimeError(f"Failed to read image '{image_path}'")
    return screenshot


def _draw_annotations(image, regions):
    annotated = image.copy()

    # Collapsed region
    left, top, width, height = regions["collapsed_rect"]
    cv2.rectangle(annotated, (left, top), (left + width, top + height), (0, 255, 255), 2)
    cx, cy = regions["collapsed_center"]
    cv2.circle(annotated, (cx, cy), 8, (0, 255, 0), 2)

    # Button centers
    colors = {
        "max": (255, 0, 0),
        "x100": (0, 0, 255),
        "x10": (0, 128, 255),
        "x5": (255, 128, 0),
        "x1": (0, 255, 128),
    }
    for quantity, centers in regions["button_centers"].items():
        color = colors.get(quantity, (255, 255, 0))
        for idx, (px, py) in enumerate(centers):
            cv2.circle(annotated, (px, py), 10, color, 2)
            cv2.putText(
                annotated,
                f"{quantity}{'' if len(centers) == 1 else f'#{idx+1}'}",
                (px + 6, py - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )

    return annotated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Visualize buy-quantity detection regions")
    parser.add_argument("--image", default="adb", help="Screenshot path or 'adb'")
    parser.add_argument(
        "--output",
        default="out/buy_quantity_debug.png",
        help="Annotated output path",
    )
    parser.add_argument(
        "--save-captures",
        help="Optional directory to save intermediary captures",
    )
    parser.add_argument(
        "--expected",
        choices=["max", "x100", "x10", "x5", "x1"],
        help="Optional expected quantity hint for OCR",
    )
    args = parser.parse_args(argv)

    capture_fn = prepare_capture_recorder(args.save_captures)

    screenshot = _load_image(args.image, capture_fn=capture_fn)

    regions = get_buy_quantity_regions(screenshot)
    detected_general = read_buy_quantity_from_image(screenshot)
    detected_expected: Optional[BuyQuantity] = None
    if args.expected:
        detected_expected = read_buy_quantity_from_image(
            screenshot, expected=cast(BuyQuantity, args.expected)
        )

    annotated = _draw_annotations(screenshot, regions)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), annotated)

    payload = {
        "output": str(output_path),
        "detected": detected_general,
        "detected_with_expected": detected_expected,
        "regions": regions,
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
