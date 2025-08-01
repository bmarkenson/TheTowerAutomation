#!/usr/bin/env python3
# tools/crop_region.py

import cv2
import os
import json
import subprocess
import time
from core.clickmap_access import get_clickmap, save_clickmap
from core.ss_capture import capture_and_save_screenshot

SOURCE_PATH = "screenshots/latest.png"
TEMPLATE_DIR = "assets/match_templates"
clickmap = get_clickmap()
GESTURE_LOGGER_PATH = "tools/gesture_logger.py"
SCRCPY_TITLE = "scrcpy-bridge"
window_name = "Crop Tool"

os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs("coords", exist_ok=True)

cropping = False
start_point = None
end_point = None
scroll_offset = 0
scroll_step = 60  # pixels per scroll or arrow press

# Try to detect screen height
try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    screen_height = root.winfo_screenheight()
    screen_width = root.winfo_screenwidth()
    root.destroy()
except Exception as e:
    print("[WARN] Could not detect screen size reliably, using fallback 1920x1080:", e)
    screen_width, screen_height = 1920, 1080

def reload_image():
    global image, clone, img_height, img_width, scroll_offset
    image = capture_and_save_screenshot()
    if image is None:
        raise RuntimeError("[ERROR] Could not capture screenshot.")
    clone = image.copy()
    img_height, img_width = image.shape[:2]
    scroll_offset = 0
    print("[INFO] Screenshot updated. Ready for next region.")
    subprocess.run(["xdotool", "search", "--name", window_name, "windowactivate"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

reload_image()
viewport_width = min(img_width, screen_width)
viewport_height = min(img_height, screen_height - 100)  # leave some room

def ensure_scrcpy_running():
    try:
        subprocess.run(["pgrep", "-f", SCRCPY_TITLE], check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[INFO] Starting scrcpy with window title override...")
        subprocess.Popen(["scrcpy", "--window-title", SCRCPY_TITLE])
        time.sleep(2)

def activate_scrcpy_window():
    try:
        win_id = subprocess.check_output(["xdotool", "search", "--name", SCRCPY_TITLE]).decode().strip().splitlines()[0]
        subprocess.run(["xdotool", "windowactivate", win_id])
    except subprocess.CalledProcessError:
        print("[WARN] Could not bring scrcpy to front")

def launch_gesture_logger(name):
    ensure_scrcpy_running()
    activate_scrcpy_window()
    subprocess.run(["python3", GESTURE_LOGGER_PATH, "--name", name])
    reload_image()

def handle_mouse(event, x, y, flags, param):
    global cropping, start_point, end_point, scroll_offset, image

    # Scroll handling
    if event == cv2.EVENT_MOUSEWHEEL:
        direction = 1 if flags > 0 else -1
        scroll_offset = min(max(0, scroll_offset - direction * scroll_step), img_height - viewport_height)
        return

    adjusted_y = y + scroll_offset

    # Crop handling
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
        w, h = x2 - x1, y2 - y1
        crop = clone[y1:y2, x1:x2]

        if crop.size == 0:
            print("[WARN] Empty crop. Try again.")
            return

        name = input("Enter template name (e.g., resume_game): ").strip()
        if not name:
            print("[INFO] Skipped saving.")
            return

        threshold_input = input("Enter match threshold (default 0.90): ").strip()
        threshold = float(threshold_input) if threshold_input else 0.90

        template_path = os.path.join(TEMPLATE_DIR, f"{name}.png")
        cv2.imwrite(template_path, crop)
        print(f"[INFO] Template saved: {template_path}")

        entry = clickmap.get(name, {})
        entry["match_template"] = f"{name}.png"
        entry["match_region"] = {"x": x1, "y": y1, "w": w, "h": h}
        entry["match_threshold"] = threshold
        clickmap[name] = entry
        save_clickmap(clickmap)
        print(f"[INFO] Clickmap entry saved for '{name}'")

        ask_gesture = input("Define a gesture for this region now? (Y/n): ").strip().lower()
        if ask_gesture in ("", "y", "yes"):
            launch_gesture_logger(name)

cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
cv2.resizeWindow(window_name, viewport_width, viewport_height)
cv2.setMouseCallback(window_name, handle_mouse)

print("[INFO] Click and drag to select region.")
print("[INFO] Scroll with mouse wheel or ↑/↓ arrow keys. Press 'q' or ESC to quit.")

while True:
    top = scroll_offset
    bottom = min(scroll_offset + viewport_height, img_height)
    display = image[top:bottom].copy()

    if cropping and start_point and end_point:
        sp = (start_point[0], start_point[1] - scroll_offset)
        ep = (end_point[0], end_point[1] - scroll_offset)
        cv2.rectangle(display, sp, ep, (0, 255, 0), 2)

    cv2.imshow(window_name, display)
    key = cv2.waitKey(20) & 0xFF

    if key == 27 or key == ord("q"):
        break
    elif key == 82:  # Up arrow
        scroll_offset = max(0, scroll_offset - scroll_step)
    elif key == 84:  # Down arrow
        scroll_offset = min(scroll_offset + scroll_step, img_height - viewport_height)

cv2.destroyAllWindows()
