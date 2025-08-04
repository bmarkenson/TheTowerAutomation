#!/usr/bin/env python3
# tools/crop_region.py

# 📦 Imports
import cv2
import os
import json
import subprocess
import time
from core.clickmap_access import get_clickmap, save_clickmap
from core.ss_capture import capture_and_save_screenshot

# 🗂 File and environment setup
SOURCE_PATH = "screenshots/latest.png"
TEMPLATE_DIR = "assets/match_templates"
clickmap = get_clickmap()
GESTURE_LOGGER_PATH = "tools/gesture_logger.py"
SCRCPY_TITLE = "scrcpy-bridge"
window_name = "Crop Tool"

# Ensure match template directory exists
os.makedirs(TEMPLATE_DIR, exist_ok=True)

# 🧠 Track last group used in region selection
_last_region_group = None

# 🏷 Role selection prompt based on top-level group
def prompt_roles(group: str, key: str):
    group = group.lower()
    if group == "gesture_targets":
        default = "gesture"
    elif group == "upgrades":
        default = "upgrade_label"
    elif group == "util":
        print(f"[?] Group '{group}' may refer to either a tap or a swipe.")
        user_input = input("    Enter roles manually (e.g., tap, swipe): ").strip()
        roles = [r.strip() for r in user_input.split(",") if r.strip()]
        return roles if roles else ["unknown"]
    else:
        default = group.rstrip("s")  # Generic plural stripping

    user_input = input(f"Suggested roles for `{group}:{key}`: [{default}] (edit or press Enter to accept): ").strip()
    if user_input:
        roles = [r.strip() for r in user_input.split(",")]
    else:
        roles = [default]

    return roles

# 🧭 Region name entry with schema awareness
def get_region_name(clickmap):
    global _last_region_group
    top_level_keys = list(clickmap.keys())
    existing_keys = {
        f"{group}:{key}" for group in top_level_keys
        for key in clickmap.get(group, {}).keys()
    }

    while True:
        print("\nAvailable groups:")
        for i, group in enumerate(top_level_keys):
            marker = " (last used)" if group == _last_region_group else ""
            print(f"  {i + 1}. {group}{marker}")

        prompt = "[Enter] = reuse last, [q] = cancel, number or name of group: "
        group_choice = input(prompt).strip().lower()

        if group_choice == "q":
            print("[INFO] Skipped saving.")
            return None

        if not group_choice:
            if _last_region_group:
                group = _last_region_group
                print(f"[INFO] Reusing last group: {group}")
            else:
                print("❌ No group selected yet.")
                continue
        elif group_choice.isdigit() and 1 <= int(group_choice) <= len(top_level_keys):
            group = top_level_keys[int(group_choice) - 1]
        elif group_choice in top_level_keys:
            group = group_choice
        else:
            print(f"❌ Invalid group. Choose one of: {', '.join(top_level_keys)}")
            continue

        suffix = input(f"Enter entry key for `{group}` (e.g. retry, attack_menu, claim_ad_gem): ").strip()
        if not suffix:
            print("[INFO] Skipped saving.")
            return None

        key = f"{group}:{suffix}"
        if key in existing_keys:
            confirm = input(f"⚠️  '{key}' already exists. Overwrite? (y/N): ").strip().lower()
            if confirm not in {"y", "yes"}:
                continue

        _last_region_group = group
        return key

# 🌍 Global state for crop handling and viewport
cropping = False
start_point = None
end_point = None
scroll_offset = 0
scroll_step = 60  # pixels per scroll

# 🖥 Screen size detection
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

# 🔁 Reload and refresh working image
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

# Initial load
reload_image()
viewport_width = min(img_width, screen_width)
viewport_height = min(img_height, screen_height - 100)

# ✅ Ensure scrcpy is running
def ensure_scrcpy_running():
    try:
        subprocess.run(["pgrep", "-f", SCRCPY_TITLE], check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[INFO] Starting scrcpy with window title override...")
        subprocess.Popen(["scrcpy", "--window-title", SCRCPY_TITLE])
        time.sleep(2)

# 🧙‍♂️ Bring scrcpy window to front
def activate_scrcpy_window():
    try:
        win_id = subprocess.check_output(["xdotool", "search", "--name", SCRCPY_TITLE]).decode().strip().splitlines()[0]
        subprocess.run(["xdotool", "windowactivate", win_id])
    except subprocess.CalledProcessError:
        print("[WARN] Could not bring scrcpy to front")

# 🎯 Launch gesture recorder on saved region
def launch_gesture_logger(group, key):
    ensure_scrcpy_running()
    activate_scrcpy_window()
    dot_path = f"{group}.{key}"
    subprocess.run(["python3", GESTURE_LOGGER_PATH, "--name", dot_path])
    reload_image()

# 🖱 Mouse handler for crop + scroll + save
def handle_mouse(event, x, y, flags, param):
    global cropping, start_point, end_point, scroll_offset, image

    if event == cv2.EVENT_MOUSEWHEEL:
        direction = 1 if flags > 0 else -1
        scroll_offset = min(max(0, scroll_offset - direction * scroll_step), img_height - viewport_height)
        return

    adjusted_y = y + scroll_offset

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
            print('[WARN] Empty crop. Try again.')
            return

        full_key = get_region_name(clickmap)
        if not full_key:
            print('[INFO] Skipped saving.')
            return

        try:
            group, key = full_key.split(":", 1)
        except ValueError:
            print(f"[ERROR] Invalid key format returned by get_region_name(): '{full_key}'")
            return

        threshold_input = input('Enter match threshold (default 0.90): ').strip()
        threshold = float(threshold_input) if threshold_input else 0.9

        group_template_dir = os.path.join(TEMPLATE_DIR, group)
        os.makedirs(group_template_dir, exist_ok=True)
        template_path = os.path.join(group_template_dir, f"{key}.png")
        cv2.imwrite(template_path, crop)
        print(f'[INFO] Template saved: {template_path}')

        if group not in clickmap:
            clickmap[group] = {}

        entry = clickmap[group].get(key, {})
        entry['match_template'] = f"{group}/{key}.png"
        entry['match_region'] = {'x': x1, 'y': y1, 'w': w, 'h': h}
        entry['match_threshold'] = threshold
        entry['roles'] = prompt_roles(group, key)
        clickmap[group][key] = entry

        save_clickmap(clickmap)
        print(f"[INFO] Clickmap entry saved for '{group}:{key}'")

        ask_gesture = input('Define a gesture for this region now? (Y/n): ').strip().lower()
        if ask_gesture in ('', 'y', 'yes'):
            launch_gesture_logger(group, key)
        else:
            reload_image()

# 🖼 OpenCV display loop
cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
cv2.resizeWindow(window_name, viewport_width, viewport_height)
cv2.setMouseCallback(window_name, handle_mouse)

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

    cv2.imshow(window_name, display)
    key = cv2.waitKey(20) & 0xFF

    if key == 27 or key == ord("q"):
        break
    elif key == 82:  # Up arrow
        scroll_offset = max(0, scroll_offset - scroll_step)
    elif key == 84:  # Down arrow
        scroll_offset = min(scroll_offset + scroll_step, img_height - viewport_height)
    elif key == ord("r"):
        reload_image()

cv2.destroyAllWindows()


