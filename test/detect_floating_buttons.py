#!/usr/bin/env python3
# tools/test_floating_buttons.py

import cv2
import os
from core.floating_button_detector import detect_floating_buttons
from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import resolve_dot_path

def main():
    print("[INFO] Capturing screen...")
    screen = capture_adb_screenshot()
    if screen is None:
        print("[ERROR] Failed to capture screen.")
        return

    print("[INFO] Detecting floating buttons...")
    matches = detect_floating_buttons(screen)

    if not matches:
        print("[RESULT] No floating buttons detected.")
        return

    for match in matches:
        print(f"[MATCH] {match['name']}:")
        print(f"  confidence = {match['confidence']:.2f}")
        print(f"  match_region = {match['match_region']}")
        print(f"  tap_point = {match['tap_point']}")

if __name__ == "__main__":
    main()
