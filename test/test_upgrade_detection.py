# test/test_upgrade_detection.py
#!/usr/bin/env python3

import cv2
import numpy as np
import os
import sys

sys.path.append(".")  # so core/, utils/ etc. work if run from root

from utils.template_matcher import match_region
from core.clickmap_access import resolve_dot_path
from core.ss_capture import capture_and_save_screenshot


# ---- Behavior-critical constants (were magic numbers) ----
OFFSET_X = 220        # horizontal offset from match point to sample the color box
OFFSET_Y = 40         # vertical offset from match point to sample the color box
SAMPLE_HALF = 10      # half-size of the sampled square region (total = 2*SAMPLE_HALF)
MAXED_RANGE = (45, 50)         # inclusive BGR average range considered "maxed"
UPGRADEABLE_RANGE = (57, 60)   # inclusive BGR average range considered "upgradeable"


def classify_color(bgr):
    """
    Classify upgrade affordance by average BGR brightness.

    Args:
        bgr (array-like): A (B, G, R) triple or ndarray representing average color.

    Returns:
        str: One of {"maxed", "upgradeable", "unaffordable"} based on avg thresholds.
    """
    b, g, r = bgr
    avg = (b + g + r) / 3
    print(f"[CLASSIFY] avg={avg:.1f} from BGR={bgr}")

    if MAXED_RANGE[0] <= avg <= MAXED_RANGE[1]:
        return "maxed"
    elif UPGRADEABLE_RANGE[0] <= avg <= UPGRADEABLE_RANGE[1]:
        return "upgradeable"
    else:
        return "unaffordable"


def detect_upgrades(screen, keys):
    """
    Detect upgrade buttons and classify their affordability by sampling a nearby color box.

    For each key (dot_path) in `keys`:
      - Resolve clickmap entry.
      - Template match to find the UI element.
      - Sample a small color region at (match_point + OFFSET_X/Y).
      - Classify via `classify_color`.
      - Draw a small green rectangle over the sampled area (in-place on `screen`).

    Args:
        screen (np.ndarray): BGR screenshot image.
        keys (list[str]): Dot-path keys to check.

    Returns:
        dict: key -> status dict. Example on visible:
              {
                "status": "upgradeable" | "maxed" | "unaffordable",
                "confidence": float,
                "tap_point": (x, y),
                "avg_color": [B, G, R]
              }
              If not visible / errors:
              {
                "status": "not visible" | "clickmap entry missing" | "sample_oob",
                "confidence": float? (if matched),
              }
    """
    results = {}
    h, w = screen.shape[:2]

    for key in keys:
        entry = resolve_dot_path(key)
        if not entry:
            results[key] = {"status": "clickmap entry missing"}
            continue

        match_point, confidence = match_region(screen, entry)
        if match_point:
            x, y = match_point

            color_x = x + OFFSET_X
            color_y = y + OFFSET_Y

            # Bounds-check the sampled region to avoid IndexError
            x0 = color_x - SAMPLE_HALF
            x1 = color_x + SAMPLE_HALF
            y0 = color_y - SAMPLE_HALF
            y1 = color_y + SAMPLE_HALF

            if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
                # Do not crash; report out-of-bounds to caller
                results[key] = {
                    "status": "sample_oob",
                    "confidence": round(confidence, 3),
                    "tap_point": (x, y)
                }
                continue

            color_region = screen[y0:y1, x0:x1]
            avg = color_region.mean(axis=(0, 1))

            # Visual marker for debugging
            cv2.rectangle(
                screen,  # image
                (color_x - 5, color_y - 5),
                (color_x + 5, color_y + 5),
                (0, 255, 0),  # green box
                2  # thickness
            )

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


def main(argv=None):
    """
    Script entrypoint.

    Behavior:
      - Loads image from CLI arg or defaults to 'screenshots/latest.png'.
      - Runs detect_upgrades on four hardcoded upgrade keys.
      - Prints results and shows an OpenCV window with the debug overlay.

    CLI:
      test_upgrade_detection.py [image_path]
    """
    argv = argv if argv is not None else sys.argv[1:]

    # Default image path
    img_path = argv[0] if len(argv) >= 1 else "screenshots/latest.png"
    screen = cv2.imread(img_path)
    if screen is None:
        print(f"[ERROR] Failed to load image: {img_path}")
        return 1

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
