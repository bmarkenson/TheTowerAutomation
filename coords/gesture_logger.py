# coords/gesture_logger.py

import os
import time
import json
import subprocess
import argparse
from pynput import keyboard

CLICKMAP_FILE = os.path.join(os.path.dirname(__file__), "clickmap.json")
pressed_keys = set()
capturing = False
start_pos = None
start_time = None
ENTRY_NAME = None

def get_mouse_pos():
    out = subprocess.check_output(["xdotool", "getmouselocation", "--shell"]).decode()
    pos = {}
    for line in out.strip().splitlines():
        key, val = line.split('=')
        if key in ('X', 'Y'):
            pos[key] = int(val)
    return pos['X'], pos['Y']

def get_android_screen_size():
    output = subprocess.check_output(['adb', 'shell', 'wm', 'size']).decode()
    override = next((line for line in output.splitlines() if "Override size" in line), None)
    physical = next((line for line in output.splitlines() if "Physical size" in line), None)
    line = override or physical
    w, h = map(int, line.split(':')[-1].strip().split('x'))
    return w, h

def get_scrcpy_window_rect():
    try:
        win_id = subprocess.check_output(['xdotool', 'search', '--name', 'scrcpy-bridge']).decode().strip().splitlines()[0]
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not find scrcpy window")
    geo = subprocess.check_output(['xwininfo', '-id', win_id]).decode()
    x = int(next(l for l in geo.splitlines() if "Absolute upper-left X" in l).split(':')[1])
    y = int(next(l for l in geo.splitlines() if "Absolute upper-left Y" in l).split(':')[1])
    w = int(next(l for l in geo.splitlines() if "Width" in l).split(':')[1])
    h = int(next(l for l in geo.splitlines() if "Height" in l).split(':')[1])
    return (x, y, w, h)

def map_to_android(x, y, window_rect, android_size):
    win_x, win_y, win_w, win_h = window_rect
    android_w, android_h = android_size

    android_aspect = android_w / android_h
    window_aspect = win_w / win_h

    if window_aspect > android_aspect:
        scale = win_h / android_h
        effective_w = android_w * scale
        margin_x = (win_w - effective_w) / 2
        rel_x = (x - win_x - margin_x) / effective_w
        rel_y = (y - win_y) / win_h
    else:
        scale = win_w / android_w
        effective_h = android_h * scale
        margin_y = (win_h - effective_h) / 2
        rel_x = (x - win_x) / win_w
        rel_y = (y - win_y - margin_y) / effective_h

    rel_x_clamped = max(0, min(1, rel_x))
    rel_y_clamped = max(0, min(1, rel_y))

    mapped_x = int(rel_x_clamped * android_w)
    mapped_y = int(rel_y_clamped * android_h)
    return mapped_x, mapped_y

def load_clickmap():
    if os.path.exists(CLICKMAP_FILE):
        try:
            with open(CLICKMAP_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to load clickmap: {e}")
            return {}
    return {}

def save_clickmap(clickmap):
    tmpfile = CLICKMAP_FILE + ".tmp"
    with open(tmpfile, "w") as f:
        json.dump(clickmap, f, indent=2)
    os.replace(tmpfile, CLICKMAP_FILE)

def on_press(key):
    if isinstance(key, keyboard.KeyCode) and key.char and key.char.lower() == 's':
        print("[HOTKEY] Detected 's'")
        toggle_gesture_capture()

def on_release(key):
    pressed_keys.discard(key)

def toggle_gesture_capture():
    global capturing, start_pos, start_time
    if not capturing:
        start_pos = get_mouse_pos()
        start_time = time.time()
        capturing = True
        print("[INFO] Gesture capture started. Perform swipe or tap, then hit 's' again.")
    else:
        end_pos = get_mouse_pos()
        end_time = time.time()
        duration_ms = int((end_time - start_time) * 1000)
        capturing = False
        process_gesture(start_pos, end_pos, duration_ms)

def process_gesture(start, end, duration_ms):
    window_rect = get_scrcpy_window_rect()
    android_size = get_android_screen_size()
    clickmap = load_clickmap()

    start_android = map_to_android(*start, window_rect, android_size)
    end_android = map_to_android(*end, window_rect, android_size)

    dx = abs(start_android[0] - end_android[0])
    dy = abs(start_android[1] - end_android[1])
    threshold = 10
    is_swipe = dx > threshold or dy > threshold

    print(f"[INFO] {'Swipe' if is_swipe else 'Tap'} from {start_android} to {end_android}")

    try:
        name = ENTRY_NAME or input("Enter name for this gesture (leave blank to skip): ").strip()
        if not name:
            return

        if name not in clickmap:
            clickmap[name] = {}

        if is_swipe:
            clickmap[name]["swipe"] = {
                "x1": start_android[0], "y1": start_android[1],
                "x2": end_android[0],   "y2": end_android[1],
                "duration_ms": duration_ms
            }
        else:
            clickmap[name]["tap"] = {
                "x": start_android[0],
                "y": start_android[1]
            }

        save_clickmap(clickmap)
        print(f"[INFO] Gesture saved to '{name}'")

    except KeyboardInterrupt:
        print("\n[INFO] Gesture not saved.")

def main():
    global ENTRY_NAME

    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="Name of the clickmap entry to update")
    args = parser.parse_args()

    if args.name:
        ENTRY_NAME = args.name

    print("[INFO] Press 's' to start and stop a gesture.")
    print("[INFO] Press Ctrl+C to exit.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()

if __name__ == "__main__":
    main()

