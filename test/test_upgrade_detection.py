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

_EXPECTED_COUNTS: Dict[str, Tuple[int, int]] = {
    "upgrade_test.png": (2, 1),
    "upgrade_test_defense.png": (2, 2),
    "upgrade_utils_test.png": (2, 2),
    "upgrade_test_partial.png": (2, 2),
    "upgrade_test_bottom.png": (2, 2),
    "upgrade_test_attack.png": (2, 2),
    "upgrade_ocr_test.png": (2, 1),
    "uw_test.png": (2, 1),
}

_EXPECTED_LABELS: Dict[str, Dict[str, Sequence[str]]] = {
    "upgrade_ocr_test.png": {
        "left": ["Package Chance", "Enemy Health Level Skip"],
        "right": ["Enemy Attack Level Skip"],
    },
    "uw_test.png": {
        "left": ["Poison Swamp", "Spotlight"],
        "right": ["Black Hole"],
    }
}

_EXPECTED_AFFORDABILITY: Dict[str, Dict[str, Sequence[str]]] = {
    "upgrade_ocr_test.png": {
        "left": ["maxed", "unaffordable"],
        "right": ["affordable"],
    }
}

_EXPECTED_TOGGLES: Dict[str, Dict[str, Sequence[Dict[str, str]]]] = {
    "uw_test.png": {
        "left": [
            {"primary": "on"},
            {"primary": "on", "missiles": "off"},
        ],
        "right": [
            {"primary": "on"},
        ],
    }
}


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


def _run_verification() -> int:
    base_dir = "screenshots"
    mismatches = []
    results = {}

    for name, (expected_left, expected_right) in _EXPECTED_COUNTS.items():
        path = os.path.join(base_dir, name)
        image = cv2.imread(path)
        if image is None:
            print(json.dumps({"error": f"failed to read {path}"}))
            return 2

        detected = detect_visible_boxes(image)
        actual_left = len(detected["left"])
        actual_right = len(detected["right"])

        has_error = False
        result_entry = {
            "expected": {"left": expected_left, "right": expected_right},
            "actual": {"left": actual_left, "right": actual_right},
        }

        counts_ok = actual_left == expected_left and actual_right == expected_right
        if not counts_ok:
            has_error = True

        expected_labels = _EXPECTED_LABELS.get(name)
        if expected_labels:
            actual_labels = {
                column: [box.text or "" for box in detected[column]]
                for column in ("left", "right")
            }
            result_entry["labels"] = {
                "expected": expected_labels,
                "actual": actual_labels,
            }

            for column, expected_values in expected_labels.items():
                if actual_labels.get(column, []) != list(expected_values):
                    has_error = True
                    break

        expected_affordability = _EXPECTED_AFFORDABILITY.get(name)
        if expected_affordability:
            actual_affordability = {
                column: [
                    box.affordability
                    for box in detected[column]
                    if box.affordability
                ]
                for column in ("left", "right")
            }
            result_entry["affordability"] = {
                "expected": expected_affordability,
                "actual": actual_affordability,
            }

            for column, expected_values in expected_affordability.items():
                if actual_affordability.get(column, []) != list(expected_values):
                    has_error = True
                    break

        expected_toggles = _EXPECTED_TOGGLES.get(name)
        if expected_toggles:
            actual_toggles = {
                column: [box.toggles or {} for box in detected[column]]
                for column in ("left", "right")
            }
            result_entry["toggles"] = {
                "expected": expected_toggles,
                "actual": actual_toggles,
            }

            for column, expected_values in expected_toggles.items():
                if actual_toggles.get(column, []) != list(expected_values):
                    has_error = True
                    break

        results[name] = result_entry

        if has_error:
            mismatches.append(name)

    payload = {"ok": not mismatches, "results": results}
    print("Verification results:")
    for name, info in results.items():
        exp_left = info["expected"]["left"]
        exp_right = info["expected"]["right"]
        act_left = info["actual"]["left"]
        act_right = info["actual"]["right"]
        counts_ok = exp_left == act_left and exp_right == act_right
        label_status = "n/a"
        if "labels" in info:
            expected_labels = info["labels"]["expected"]
            actual_labels = info["labels"]["actual"]
            label_status = "OK" if expected_labels == actual_labels else "FAIL"
        affordability_status = "n/a"
        if "affordability" in info:
            expected_aff = info["affordability"]["expected"]
            actual_aff = info["affordability"]["actual"]
            affordability_status = "OK" if expected_aff == actual_aff else "FAIL"
        toggle_status = "n/a"
        if "toggles" in info:
            expected_toggle = info["toggles"]["expected"]
            actual_toggle = info["toggles"]["actual"]
            toggle_status = "OK" if expected_toggle == actual_toggle else "FAIL"

        status = "PASS" if all(
            flag in ("OK", "n/a")
            for flag in (label_status, affordability_status, toggle_status)
        ) and counts_ok else "FAIL"
        message = (
            f"  {name}: {status}"
            f" (left expected={exp_left}, actual={act_left};"
            f" right expected={exp_right}, actual={act_right})"
        )
        if label_status != "n/a":
            message += f" labels={label_status}"
        if affordability_status != "n/a":
            message += f" afford={affordability_status}"
        if toggle_status != "n/a":
            message += f" toggles={toggle_status}"
        print(message)

    print(json.dumps(payload))
    return 0 if not mismatches else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Detect visible upgrade boxes")
    parser.add_argument("--image", default="adb", help="Screenshot path or 'adb'")
    parser.add_argument("--annotate", help="Optional output path for annotated image")
    parser.add_argument("--menu", help="Optional upgrade menu context (attack/defense/utility)")
    parser.add_argument(
        "--verify-all",
        action="store_true",
        help="Run detection against the known sample set and verify expected counts",
    )
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
    args = parser.parse_args(argv)

    if args.find_upgrade:
        if args.image.lower() != "adb":
            print(json.dumps({"error": "--find-upgrade requires live capture; use --image adb"}))
            return 2

        result = find_upgrade(
            args.menu,
            args.find_upgrade,
            max_scrolls=args.max_scrolls,
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
        }
        print(json.dumps(payload))
        return 0

    if args.verify_all:
        if args.annotate:
            print(json.dumps({"error": "--annotate is not supported with --verify-all"}))
            return 2
        return _run_verification()

    return _run_single(args.image, args.annotate, args.menu)


if __name__ == "__main__":
    raise SystemExit(main())
