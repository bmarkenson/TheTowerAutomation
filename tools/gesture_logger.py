#!/usr/bin/env python3

import os
import time
import json
import subprocess
import argparse
from pynput import keyboard, mouse
from core.clickmap_access import get_clickmap, save_clickmap, resolve_dot_path

clickmap = get_clickmap()
ENTRY_NAME = None
capturing = False
start_pos = None
start_time = None

def get_mouse_pos():
    out = subprocess.check_output(["xdotool", "getmouselocation", "--shell"]).decode()
    pos = {}
    for line in out.strip().splitlines():
        key, val = line.split("=")
        if key in ("X", "Y"):
            pos[key] = int(val)
    return pos["X"], pos["Y"]

def get_android_screen_size():
    output = subprocess.check_output(["adb", "shell", "wm", "size"]).decode()
    override = next((line for line in output.splitlines() if "Override size" in line), None)
    physical = next((line for line in output.splitlines() if "Physical size" in line), None)
    line = override or physical
    w, h = map(int, line.split(":")[-1].strip().split("x"))
    return w, h

def get_scrcpy_window_rect():
    try:
        win_id = subprocess.check_output(["xdotool", "search", "--name", "scrcpy-bridge"]).decode().strip().splitlines()[0]
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not find scrcpy window")
    geo = subprocess.check_output(["xwininfo", "-id", win_id]).decode()
    x = int(next(l for l in geo.splitlines() if "Absolute upper-left X" in l).split(":")[1])
    y = int(next(l for l in geo.splitlines() if "Absolute upper-left Y" in l).split(":")[1])
    w = int(next(l for l in geo.splitlines() if "Width" in l).split(":")[1])
    h = int(next(l for l in geo.splitlines() if "Height" in l).split(":")[1])
    return (x, y, w, h)

def activate_scrcpy_window():
    try:
        win_id = subprocess.check_output(["xdotool", "search", "--name", "scrcpy-bridge"]).decode().strip().splitlines()[0]
        subprocess.run(["xdotool", "windowactivate", win_id])
    except subprocess.CalledProcessError:
        print("[WARN] Could not bring scrcpy window to front")

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

def start_capture():
    global capturing, start_pos, start_time
    start_pos = get_mouse_pos()
    start_time = time.time()
    capturing = True
    print("[INFO] Gesture capture started. Perform swipe or tap...")

def complete_capture():
    global capturing
    end_pos = get_mouse_pos()
    end_time = time.time()
    duration_ms = int((end_time - start_time) * 1000)
    capturing = False
    process_gesture(start_pos, end_pos, duration_ms)

def process_gesture(start, end, duration_ms):
    window_rect = get_scrcpy_window_rect()
    android_size = get_android_screen_size()
    clickmap = get_clickmap()

    start_android = map_to_android(*start, window_rect, android_size)
    end_android = map_to_android(*end, window_rect, android_size)

    entry = resolve_dot_path(ENTRY_NAME)
    if entry is None:
        confirm = input(f"[WARN] Entry '{name}' does not exist in clickmap. Create new blind gesture entry? (y/N): ").strip().lower()
        if confirm != 'y':
            print("[INFO] Gesture not saved.")
            return
        # Attempt to insert into the clickmap
        try:
            group, key = name.split(".", 1)
        except ValueError:
            print("[ERROR] Invalid dot-path format. Expected 'group.key'")
            return
    
        if group not in clickmap:
            clickmap[group] = {}
    
        clickmap[group][key] = {
            "roles": ["gesture"]
        }
        entry = clickmap[group][key]

    if start_android == end_android:
        gesture_type = "tap"
        new_gesture = { "x": start_android[0], "y": start_android[1] }
        log_msg = f"[INFO] Recorded tap at {start_android}"
    else:
        gesture_type = "swipe"
        new_gesture = {
            "x1": start_android[0], "y1": start_android[1],
            "x2": end_android[0],   "y2": end_android[1],
            "duration_ms": duration_ms
        }
        log_msg = f"[INFO] Recorded swipe from {start_android} to {end_android} in {duration_ms}ms"

    entry[gesture_type] = new_gesture
    save_clickmap(clickmap)
    print(log_msg)
    print(f"[INFO] Gesture saved under '{ENTRY_NAME}'")

def on_key_press(key):
    if isinstance(key, keyboard.KeyCode) and key.char and key.char.lower() == "s":
        if not capturing:
            start_capture()
        else:
            complete_capture()

def on_mouse_click(x, y, button, pressed):
    if not ENTRY_NAME:
        return
    global capturing
    if pressed and not capturing:
        start_capture()
    elif not pressed and capturing:
        complete_capture()
        print("[INFO] Exiting after one gesture (implicit once-mode).")
        return False  # stop listener

def main():
    global ENTRY_NAME
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="dot_path to target")
    args = parser.parse_args()
    ENTRY_NAME = args.name

    if ENTRY_NAME:
        print(f"[INFO] Recording gesture for '{ENTRY_NAME}'. Click to start and release to finish.")
        activate_scrcpy_window()
        with mouse.Listener(on_click=on_mouse_click) as listener:
            listener.join()
    else:
        print("[INFO] Press 's' to start/stop gesture recording. Ctrl+C to quit.")
        with keyboard.Listener(on_press=on_key_press) as listener:
            listener.join()

if __name__ == "__main__":
    main()
