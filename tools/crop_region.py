#!/usr/bin/env python3
# tools/crop_region.py

import os
import cv2
import json
import time
import subprocess

from core.ss_capture import capture_and_save_screenshot
from core.clickmap_access import (
    get_clickmap,
    save_clickmap,
    interactive_get_dot_path,
    prompt_roles,
    set_dot_path,
)

# Constants
SOURCE_PATH = "screenshots/latest.png"
TEMPLATE_DIR = "assets/match_templates"
GESTURE_LOGGER_PATH = "tools/gesture_logger.py"
SCRCPY_TITLE = "scrcpy-bridge"
WINDOW_NAME = "Crop Tool"
DEFAULT_THRESHOLD = 0.9
SCROLL_STEP = 60

# Globals
clickmap = get_clickmap()
cropping = False
start_point = None
end_point = None
scroll_offset = 0

# Regions that are coordinates-only (no template image saved)
COORDS_ONLY_GROUPS = {"_shared_match_regions"}   # e.g., shared helpers for region_ref consumers
COORDS_ONLY_PREFIXES = set()  # e.g., {"util.scroll_areas"} if needed later

# Detect screen size
try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    screen_height = root.winfo_screenheight()
    screen_width = root.winfo_screenwidth()
    root.destroy()
except Exception as e:
    print("[WARN] Could not detect screen size. Defaulting to 1920x1080")
    screen_width, screen_height = 1920, 1080

def reload_image():
    """Capture a fresh screenshot, initialize globals (image/clone/img_w/h), reset scroll, and focus the window.

    Inputs: none (uses ADB via capture_and_save_screenshot()).
    Writes: updates globals image, clone, img_height, img_width; resets scroll_offset; saves screenshots/latest.png on disk.
    Prompts: none (silent, except printed INFO).
    """
    global image, clone, img_height, img_width, scroll_offset
    image = capture_and_save_screenshot()
    if image is None:
        raise RuntimeError("[ERROR] Could not capture screenshot.")
    clone = image.copy()
    img_height, img_width = image.shape[:2]
    scroll_offset = 0
    print("[INFO] Screenshot updated.")
    subprocess.run(["xdotool", "search", "--name", WINDOW_NAME, "windowactivate"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def is_coords_only(dot_path: str) -> bool:
    """Return True if the dot_path belongs to a coords-only group or prefix (no template to save)."""
    parts = dot_path.split(".")
    if not parts:
        return False
    if parts[0] in COORDS_ONLY_GROUPS:
        return True
    return any(dot_path == p or dot_path.startswith(p + ".") for p in COORDS_ONLY_PREFIXES)

def _dot_path_exists(root: dict, dot_path: str) -> bool:
    """Internal: check whether dot_path already exists in the clickmap dict."""
    cur = root
    for part in dot_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True

def save_template_crop_and_entry(x1, y1, x2, y2):
    """Persist a cropped region to disk and clickmap; optionally launch gesture logger.

    Inputs: crop box (x1,y1,x2,y2) in screenshot coordinates.
    Writes:
      - For coords-only: stores match_region under the chosen dot_path in clickmap.json.
      - For template-backed: writes PNG under assets/match_templates/<...>, and stores match_template/match_region/match_threshold[/roles].
    Prompts:
      - Dot-path selection.
      - Overwrite confirmation if the key exists.
      - For template-backed: threshold (default 0.90), roles, and optional gesture capture.
    """
    global clickmap

    w, h = x2 - x1, y2 - y1
    crop = clone[y1:y2, x1:x2]
    if crop.size == 0:
        print("[WARN] Empty crop. Try again.")
        return

    dot_path = interactive_get_dot_path(clickmap)
    if dot_path is None:
        return

    parts = dot_path.split(".")
    if len(parts) < 2:
        print(f"[ERROR] Invalid dot-path key: '{dot_path}'")
        return

    group = parts[0]
    key = parts[-1]
    subdir_parts = parts[1:-1]

    coordinate_only = is_coords_only(dot_path)

    # Overwrite confirmation
    if _dot_path_exists(clickmap, dot_path):
        resp = input(f"[WARN] '{dot_path}' exists. Overwrite? (y/N): ").strip().lower()
        if resp not in ("y", "yes"):
            print("[INFO] Skipping save (user declined overwrite).")
            reload_image()
            return

    if coordinate_only:
        entry = {
            "match_region": {"x": x1, "y": y1, "w": w, "h": h}
        }
        set_dot_path(dot_path, entry, allow_overwrite=True)
        save_clickmap(clickmap)
        print(f"[INFO] (coords-only) Region saved for '{dot_path}' (no image/threshold/roles)")
        # Skip gesture prompt for coords-only
        reload_image()
        return

    if subdir_parts:
        template_subdir = os.path.join(*subdir_parts)
        template_dir = os.path.join(TEMPLATE_DIR, group, template_subdir)
        match_template = f"{group}/{template_subdir}/{key}.png"
    else:
        template_dir = os.path.join(TEMPLATE_DIR, group)
        match_template = f"{group}/{key}.png"

    # Save template image
    os.makedirs(template_dir, exist_ok=True)
    template_path = os.path.join(template_dir, f"{key}.png")
    cv2.imwrite(template_path, crop)

    print(f"[INFO] Template saved: {template_path}")

    threshold_input = input(f"Enter match threshold (default {DEFAULT_THRESHOLD:.2f}): ").strip()
    threshold = float(threshold_input) if threshold_input else DEFAULT_THRESHOLD

    roles = prompt_roles(group, key)
    entry = {
        "match_template": match_template,
        "match_region": {"x": x1, "y": y1, "w": w, "h": h},
        "match_threshold": threshold,
        "roles": roles
    }

    set_dot_path(dot_path, entry, allow_overwrite=True)
    save_clickmap(clickmap)
    print(f"[INFO] Clickmap entry saved for '{dot_path}'")

    ask_gesture = input("Define a gesture for this region now? (Y/n): ").strip().lower()
    if ask_gesture in ("", "y", "yes"):
        subprocess.run(["python3", GESTURE_LOGGER_PATH, "--name", dot_path])

    reload_image()

def handle_mouse(event, x, y, flags, param):
    """Mouse callback: manages scroll with wheel, drag-selects a box, and triggers save on release.

    Inputs: cv2 mouse event args.
    Writes: may update globals (start_point, end_point, cropping, scroll_offset); may persist template/region on mouse up.
    Prompts: as per save_template_crop_and_entry().
    """
    global cropping, start_point, end_point, scroll_offset, image
    adjusted_y = y + scroll_offset

    if event == cv2.EVENT_MOUSEWHEEL:
        direction = 1 if flags > 0 else -1
        scroll_offset = min(max(0, scroll_offset - direction * SCROLL_STEP), img_height - viewport_height)
        return

    if event == cv2.EVENT_LBUTTONDOWN:
        start_point = (x, adjusted_y)
        cropping = True
        end_point = None

    elif event == cv2.EVENT_MOUSEMOVE and cropping:
        end_point = (x, adjusted_y)

    elif event == cv2.EVENT_LBUTTONUP:
        end_point = (x, adjusted_y)
        cropping = False
        x1, y1 = min(start_point[0], end_point[0]), min(start_point[1], end_point[1])
        x2, y2 = max(start_point[0], end_point[0]), max(start_point[1], end_point[1])
        save_template_crop_and_entry(x1, y1, x2, y2)

def main():
    # --- Main Loop ---
    reload_image()
    viewport_width = min(img_width, screen_width)
    viewport_height = min(img_height, screen_height - 100)

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, viewport_width, viewport_height)
    cv2.setMouseCallback(WINDOW_NAME, handle_mouse)

    print("[INFO] Click and drag to select region.")
    print("[INFO] Scroll with mouse wheel or ↑/↓ arrow keys. Press 'q' or ESC to quit.")
    print("[INFO] Press 'r' to reload screenshot at any time.")

    while True:
        top = scroll_offset
        bottom = min(scroll_offset + viewport_height, img_height)
        display = image[top:bottom].copy()

        if cropping and start_point and end_point:
            sp = (start_point[0], start_point[1] - scroll_offset)
            ep = (end_point[0], end_point[1] - scroll_offset)
            cv2.rectangle(display, sp, ep, (0, 255, 0), 2)

        cv2.imshow(WINDOW_NAME, display)
        key = cv2.waitKey(20) & 0xFF

        if key == 27 or key == ord("q"):
            break
        elif key == 82:  # Up arrow
            scroll_offset = max(0, scroll_offset - SCROLL_STEP)
        elif key == 84:  # Down arrow
            scroll_offset = min(scroll_offset + SCROLL_STEP, img_height - viewport_height)
        elif key == ord("r"):
            reload_image()

    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
