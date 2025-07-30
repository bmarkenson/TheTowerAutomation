#!/usr/bin/env python3

import subprocess
import time
import re
import json
import os
from pynput import mouse

CLICKMAP_FILE = os.path.join(os.path.dirname(__file__), "clickmap.json")

def get_android_screen_size():
    output = subprocess.check_output(['adb', 'shell', 'wm', 'size']).decode()
    print(f"[DEBUG] adb wm size output:\n{output}")
    override_match = re.search(r'Override size:\s*(\d+)x(\d+)', output)
    if override_match:
        size = tuple(map(int, override_match.groups()))
        print(f"[DEBUG] Android override size: {size}")
        return size
    physical_match = re.search(r'Physical size:\s*(\d+)x(\d+)', output)
    if physical_match:
        size = tuple(map(int, physical_match.groups()))
        print(f"[DEBUG] Android physical size (fallback): {size}")
        return size
    raise RuntimeError("Could not determine Android screen size")


def get_scrcpy_window_rect():
    try:
        win_id = subprocess.check_output(['xdotool', 'search', '--name', 'scrcpy-bridge']).decode().strip().splitlines()[0]
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not find scrcpy window")

    geo = subprocess.check_output(['xwininfo', '-id', win_id]).decode()
    x = int(re.search(r"Absolute upper-left X:\s+(\d+)", geo).group(1))
    y = int(re.search(r"Absolute upper-left Y:\s+(\d+)", geo).group(1))
    width = int(re.search(r"Width:\s+(\d+)", geo).group(1))
    height = int(re.search(r"Height:\s+(\d+)", geo).group(1))

    print(f"[DEBUG] scrcpy window rect: ({x}, {y}, {width}, {height})")
    return (x, y, width, height)


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
    print(f"[DEBUG] Mouse clicked at: {x}, {y}")
    print(f"[DEBUG] Final Android tap: {mapped_x}, {mapped_y}")
    return mapped_x, mapped_y


def send_tap(x, y):
    print(f"[ADB] tap {x}, {y}")
    subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)])


def load_clickmap():
    if os.path.exists(CLICKMAP_FILE):
        with open(CLICKMAP_FILE, "r") as f:
            return json.load(f)
    return {}


def save_clickmap(clickmap):
    with open(CLICKMAP_FILE, "w") as f:
        json.dump(clickmap, f, indent=2)


def start_click_logger(window_rect, android_size):
    clickmap = load_clickmap()

    def on_click(x, y, button, pressed):
        if not pressed:
            win_x, win_y, win_w, win_h = window_rect
            if not (win_x <= x <= win_x + win_w and win_y <= y <= win_y + win_h):
                print(f"[DEBUG] Ignoring click outside scrcpy window: {x}, {y}")
                return

            mapped_x, mapped_y = map_to_android(x, y, window_rect, android_size)
            send_tap(mapped_x, mapped_y)

            try:
                name = input("Enter name for this click (leave blank to skip): ").strip()
                if not name:
                    return
                if name in clickmap:
                    confirm = input(f"'{name}' already exists. Overwrite? (y/N): ").lower()
                    if confirm != 'y':
                        return
                clickmap[name] = {"x": mapped_x, "y": mapped_y}
                save_clickmap(clickmap)
                print(f"[INFO] Saved: {name} -> ({mapped_x}, {mapped_y})")
            except KeyboardInterrupt:
                print("\n[INFO] Interrupted, not saving this click.")

    listener = mouse.Listener(on_click=on_click)
    listener.start()


def launch_scrcpy():
    subprocess.Popen(["scrcpy", "--no-control", "--window-title", "scrcpy-bridge"])
    time.sleep(2)


def main():
    launch_scrcpy()
    android_size = get_android_screen_size()
    window_rect = get_scrcpy_window_rect()
    print("Click logger running. Ctrl+C to stop.")
    start_click_logger(window_rect, android_size)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
