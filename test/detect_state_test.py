#!/usr/bin/env python3
# test/detect_state_test.py

import argparse
import os
import cv2
from core.state_detector import detect_state_and_overlays


def main():
    parser = argparse.ArgumentParser(description="Test state detection from screenshot")
    parser.add_argument("--image", default="screenshots/latest.png", help="Path to screenshot image")
    parser.add_argument("--highlight", action="store_true", help="Draw match region on output")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"[ERROR] Image not found: {args.image}")
        return

    screen = cv2.imread(args.image)
    if screen is None:
        print("[ERROR] Failed to load image.")
        return

    result = detect_state_and_overlays(screen)
    print(f"[TEST] Detected state: {result['state']}")
    print(f"[TEST] Detected overlays: {result['overlays']}")

    # Optional: save a highlighted version (match drawing must be added inside detect_state for now)
    if args.highlight:
        output_path = os.path.splitext(args.image)[0] + "_annotated.png"
        cv2.imwrite(output_path, screen)
        print(f"[INFO] Saved annotated image to: {output_path}")


if __name__ == "__main__":
    main()
