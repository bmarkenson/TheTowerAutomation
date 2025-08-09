#!/usr/bin/env python3

import cv2
import numpy as np
import os
import sys

sys.path.append(".")  # so core/, utils/ etc. work if run from root

from utils.template_matcher import match_region
from core.clickmap_access import resolve_dot_path
from core.ss_capture import capture_and_save_screenshot


def classify_color(bgr):
    b, g, r = bgr
    avg = (b + g + r) / 3
    print(f"[CLASSIFY] avg={avg:.1f} from BGR={bgr}")
    if 45 <= avg <= 50:
        return "maxed"
    elif 57 <= avg <= 60:
        return "upgradeable"
    else:
        return "unaffordable"

def detect_upgrades(screen, keys):
    results = {}
    for key in keys:
        entry = resolve_dot_path(key)
        if not entry:
            results[key] = {"status": "clickmap entry missing"}
            continue

        match_point, confidence = match_region(screen, entry)
        if match_point:
            x, y = match_point
            offset_x = 220
            offset_y = 40
            color_x = x + offset_x
            color_y = y + offset_y
            color_region = screen[color_y-10:color_y+10, color_x-10:color_x+10]
            avg = color_region.mean(axis=(0, 1))
            cv2.rectangle(
                screen,  # image
                (color_x - 5, color_y - 5),
                (color_x + 5, color_y + 5),
                (0, 255, 0),  # green box
                2  # thickness
            )
            #region = screen[y-5:y+5, x-5:x+5]
            #avg = region.mean(axis=(0, 1))
            avg_color = avg.astype(int).tolist()
            status = classify_color(avg)
            print(f"[DEBUG] {key} → avg={(avg_color[0] + avg_color[1] + avg_color[2]) / 3:.1f}  raw={avg_color} → {status}")
            
            results[key] = {
                "status": status,
                "confidence": round(confidence, 3),
                "tap_point": (x, y),
                "avg_color": avg_color
            }
        else:
            results[key] = {
                "status": "not visible",
                "confidence": round(confidence, 3)
            }
    return results


if __name__ == "__main__":
    # Default image path
    img_path = sys.argv[1] if len(sys.argv) > 1 else "screenshots/latest.png"
    screen = cv2.imread(img_path)
    if screen is None:
        print(f"[ERROR] Failed to load image: {img_path}")
        sys.exit(1)

    keys = [
        "upgrades.attack.left.upgrade_super_crit_mult",
        "upgrades.attack.right.upgrade_super_crit_chance",
        "upgrades.attack.left.upgrade_rend_armor_mult",
        "upgrades.attack.right.upgrade_rend_armor_chance"
    ]

    results = detect_upgrades(screen, keys)
    for key, info in results.items():
        print(f"{key}: {info}")

    cv2.imshow("Debug Overlay", screen)
    cv2.waitKey(0)  # Waits for a keypress
    cv2.destroyAllWindows()

