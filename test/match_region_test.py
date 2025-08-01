# test/match_region_test.py

import argparse
import json
import os
import cv2
from utils.template_matcher import match_region
from core.clickmap_access import get_clickmap

clickmap = get_clickmap()


def load_clickmap():
    if os.path.exists(clickmap):
        with open(clickmap, "r") as f:
            return json.load(f)
    return {}


def main():
    parser = argparse.ArgumentParser(description="Test a single clickmap entry with template matching")
    parser.add_argument("name", help="Clickmap key to test")
    parser.add_argument("--image", default="screenshots/latest.png", help="Screenshot to use")
    parser.add_argument("--draw", action="store_true", help="Draw result on output image")
    args = parser.parse_args()

    screen = cv2.imread(args.image)
    if screen is None:
        print("[ERROR] Failed to load screenshot.")
        return

    clickmap = load_clickmap()
    entry = clickmap.get(args.name)
    if not entry:
        print(f"[ERROR] No entry named '{args.name}' in clickmap.json")
        return

    pt, conf = match_region(screen, entry)
    if pt:
        print(f"[MATCH] {args.name} matched at {pt} with confidence {conf:.3f}")
        if args.draw:
            cv2.circle(screen, pt, 10, (0, 255, 0), 2)
            out_path = f"screenshots/match_{args.name}.png"
            cv2.imwrite(out_path, screen)
            print(f"[INFO] Saved annotated match image to: {out_path}")
    else:
        print(f"[NO MATCH] {args.name} max confidence: {conf:.3f}")


if __name__ == "__main__":
    main()
