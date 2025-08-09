#!/usr/bin/env python3
# test/detect_floating_buttons.py

"""
CLI test harness:
- Captures a screen via ADB
- Runs floating button detection
- Prints human-readable matches

Intended for quick manual verification. No JSON or tap actions here.
"""

from core.floating_button_detector import detect_floating_buttons
from core.ss_capture import capture_adb_screenshot


def main():
    """
    Capture the current screen and print detected floating buttons.

    Returns: None (prints results only)
    Side effects: Uses ADB to capture the screen; CPU-bound image matching.
    Exit behavior:
      - Prints an error and returns early if the screen capture fails.
      - Prints a summary when no buttons are detected.
      - Prints details for each detected button otherwise.
    """
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
