# tools/crop_region.py

import cv2
import os
import sys
import json
import subprocess
import time
from core.clickmap_access import get_clickmap

SOURCE_PATH = "screenshots/latest.png"
TEMPLATE_DIR = "assets/match_templates"
clickmap = get_clickmap()
GESTURE_LOGGER_PATH = "tools/gesture_logger.py"
SCRCPY_TITLE = "scrcpy-bridge"

os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs("coords", exist_ok=True)

cropping = False
start_point = None
end_point = None
image = cv2.imread(SOURCE_PATH)
clone = image.copy()

def load_clickmap():
    if os.path.exists(clickmap):
        with open(clickmap, "r") as f:
            return json.load(f)
    return {}

def save_clickmap(data):
    tmp = clickmap + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, clickmap)

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

def click_and_crop(event, x, y, flags, param):
    global cropping, start_point, end_point, image

    if event == cv2.EVENT_LBUTTONDOWN:
        start_point = (x, y)
        cropping = True
        end_point = None

    elif event == cv2.EVENT_MOUSEMOVE and cropping:
        end_point = (x, y)

    elif event == cv2.EVENT_LBUTTONUP:
        end_point = (x, y)
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

        # Save template image
        template_path = os.path.join(TEMPLATE_DIR, f"{name}.png")
        cv2.imwrite(template_path, crop)
        print(f"[INFO] Template saved: {template_path}")

        # Update clickmap.json
        clickmap = load_clickmap()
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

        image[:] = clone

cv2.namedWindow("Crop Tool")
cv2.setMouseCallback("Crop Tool", click_and_crop)

print("[INFO] Click and drag to select region. Press 'q' or ESC to quit.")

while True:
    display = image.copy()
    if cropping and start_point and end_point:
        cv2.rectangle(display, start_point, end_point, (0, 255, 0), 2)
    cv2.imshow("Crop Tool", display)
    key = cv2.waitKey(1) & 0xFF
    if key == 27 or key == ord("q"):
        break

cv2.destroyAllWindows()


