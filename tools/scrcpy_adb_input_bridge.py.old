#!/usr/bin/env python3

import argparse
import json
import signal
import atexit
import subprocess
import time
import re
from pynput import mouse
import cv2
import numpy as np

# Global State Variables
SCRCPY_WIN_ID = None
SCRCPY_WIN_RECT = None

# Global for cleanup
SCRCPY_PROC = None

def ensure_scrcpy_window_rect():
    global SCRCPY_WIN_ID, SCRCPY_WIN_RECT

    def lookup_window_id():
        try:
            return subprocess.check_output(
                ["xdotool", "search", "--name", "scrcpy-bridge"]
            ).decode().strip().splitlines()[0]
        except subprocess.CalledProcessError:
            return None

    if SCRCPY_WIN_ID is None:
        SCRCPY_WIN_ID = lookup_window_id()
        if SCRCPY_WIN_ID is None:
            raise RuntimeError("Could not find scrcpy window")

    # Attempt to get current geometry for that ID
    try:
        geo = subprocess.check_output(["xwininfo", "-id", SCRCPY_WIN_ID]).decode()
    except subprocess.CalledProcessError:
        print("[WARN] scrcpy window ID invalid — refreshing...")
        SCRCPY_WIN_ID = lookup_window_id()
        if SCRCPY_WIN_ID is None:
            raise RuntimeError("Could not find scrcpy window (after refresh)")
        geo = subprocess.check_output(["xwininfo", "-id", SCRCPY_WIN_ID]).decode()

    x = int(next(l for l in geo.splitlines() if "Absolute upper-left X" in l).split(":")[1])
    y = int(next(l for l in geo.splitlines() if "Absolute upper-left Y" in l).split(":")[1])
    w = int(next(l for l in geo.splitlines() if "Width" in l).split(":")[1])
    h = int(next(l for l in geo.splitlines() if "Height" in l).split(":")[1])

    new_rect = (x, y, w, h)
    if SCRCPY_WIN_RECT != new_rect:
        print(f"[INFO] Detected scrcpy window change: {SCRCPY_WIN_RECT} -> {new_rect}")
        SCRCPY_WIN_RECT = new_rect

    return SCRCPY_WIN_RECT

def get_android_screen_size():
    import numpy as np
    import cv2
    import subprocess

    #print("[DEBUG] Capturing ADB framebuffer to detect screen size...")
    result = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, check=True)
    img_bytes = result.stdout
    img_array = np.asarray(bytearray(img_bytes), dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    if img is None:
        raise RuntimeError("Failed to decode ADB screenshot")

    height, width = img.shape[:2]
    #print(f"[DEBUG] Actual framebuffer screen size: width={width}, height={height}")
    return (width, height)

def get_scrcpy_window_rect():
    import time

    try:
        win_id = subprocess.check_output(['xdotool', 'search', '--name', 'scrcpy-bridge']).decode().strip().splitlines()[0]
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not find scrcpy window")

    tree = subprocess.check_output(['xwininfo', '-tree', '-id', win_id]).decode()
    #print(f"[DEBUG] xwininfo -tree:\n{tree}")

    # Look for child window named "N/A"
    child_match = re.search(r'(0x[0-9a-f]+)\s+"N/A"', tree, re.IGNORECASE)
    if child_match:
        drawable_id = "0x" + child_match.group(1)
        width = int(child_match.group(2))
        height = int(child_match.group(3))
        x = int(child_match.group(4))
        y = int(child_match.group(5))
    else:
        #print("[DEBUG] No child drawable window found; falling back to top-level window")

        fallback_match = re.search(r'Window id: (0x[0-9a-f]+) "scrcpy-bridge"', tree)
        if not fallback_match:
            raise RuntimeError("Could not find scrcpy window at all")
        drawable_id = fallback_match.group(1)

        # Loop until the geometry becomes stable
        max_wait = 5
        interval = 0.25
        min_width = 500
        min_height = 500
        attempts = int(max_wait / interval)

        for i in range(attempts):
            geo = subprocess.check_output(['xwininfo', '-id', drawable_id]).decode()
            width = int(re.search(r"Width:\s+(\d+)", geo).group(1))
            height = int(re.search(r"Height:\s+(\d+)", geo).group(1))
            x = int(re.search(r"Absolute upper-left X:\s+(\d+)", geo).group(1))
            y = int(re.search(r"Absolute upper-left Y:\s+(\d+)", geo).group(1))

            if width > min_width and height > min_height:
                #print(f"[DEBUG] Window geometry stabilized after {i*interval:.1f}s")
                break
            else:
                #print(f"[DEBUG] Waiting for window resize: {width}x{height} too small")
                time.sleep(interval)
        else:
            raise RuntimeError("scrcpy window did not reach expected size in time")

    #print(f"[DEBUG] Drawable bounds: ({x}, {y}, {width}, {height})")
    return (x, y, width, height)


def map_to_android(x, y, window_rect, android_size):
    win_x, win_y, win_w, win_h = window_rect
    android_w, android_h = android_size

    android_aspect = android_w / android_h
    window_aspect = win_w / win_h

    #print(f"[DEBUG] Mouse clicked at: {x}, {y}")
    #print(f"[DEBUG] Window size: {win_w}x{win_h}, Android size: {android_w}x{android_h}")
    #print(f"[DEBUG] Aspect ratio: win={window_aspect:.3f}, android={android_aspect:.3f}")

    if window_aspect > android_aspect:
        scale = win_h / android_h
        effective_w = android_w * scale
        margin_x = (win_w - effective_w) / 2
        rel_x = (x - win_x - margin_x) / effective_w
        rel_y = (y - win_y) / win_h
        #print(f"[DEBUG] Letterboxing L/R: scale={scale:.4f}, margin_x={margin_x:.2f}")
    else:
        scale = win_w / android_w
        effective_h = android_h * scale
        margin_y = (win_h - effective_h) / 2
        rel_x = (x - win_x) / win_w
        rel_y = (y - win_y - margin_y) / effective_h
        #print(f"[DEBUG] Letterboxing T/B: scale={scale:.4f}, margin_y={margin_y:.2f}")

    rel_x_clamped = max(0, min(1, rel_x))
    rel_y_clamped = max(0, min(1, rel_y))
    #print(f"[DEBUG] Relative position unclamped: ({rel_x:.3f}, {rel_y:.3f})")
    #print(f"[DEBUG] Relative position clamped: ({rel_x_clamped:.3f}, {rel_y_clamped:.3f})")

    mapped_x = int(rel_x_clamped * android_w)
    mapped_y = int(rel_y_clamped * android_h)
    #print(f"[DEBUG] Final Android tap: {mapped_x}, {mapped_y}")
    return mapped_x, mapped_y

def send_tap(x, y):
    print(f"[ADB] tap {x}, {y}")
    subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)])

def send_swipe(x1, y1, x2, y2, duration_ms):
    print(f"[ADB] swipe {x1},{y1} -> {x2},{y2} ({duration_ms}ms)")
    subprocess.run([
        "adb", "shell", "input", "swipe",
        str(x1), str(y1), str(x2), str(y2), str(duration_ms)
    ])

def get_pixel_color_at_android_coords(x, y):
    try:
        result = subprocess.run(
            ["adb", "exec-out", "screencap", "-p"],
            capture_output=True,
            check=True
        )
        image_bytes = result.stdout
        image_array = np.asarray(bytearray(image_bytes), dtype=np.uint8)
        img = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        b, g, r = img[y, x]
        return (r, g, b)
    except Exception as e:
        print(f"[ERROR] Failed to get pixel color at ({x}, {y}): {e}")
        return None

def start_mouse_listener(android_size, args):
    press_pos = None
    press_time = None

    def on_click(x, y, button, pressed):
        nonlocal press_pos, press_time
    
        try:
            window_rect = ensure_scrcpy_window_rect()
        except RuntimeError as e:
            print(f"[ERROR] Could not resolve scrcpy window: {e}")
            return
    
        win_x, win_y, win_w, win_h = window_rect
        inside = (win_x <= x <= win_x + win_w and win_y <= y <= win_y + win_h)
    
        if not inside:
            if pressed:
                # Optional debug: print(f"[DEBUG] Ignoring press outside scrcpy window: {x}, {y}")
                pass
            return
    
        if pressed:
            press_pos = (x, y)
            press_time = time.time()
            return  # <-- Don't proceed further on press
    
        # 🔒 GUARD: Ignore releases if press_time wasn't set
        if press_time is None:
            print("[WARN] Mouse release detected but press_time is None — ignoring.")
            return
    
        release_time = time.time()
        duration = int((release_time - press_time) * 1000)
        # Optional debug: print(f"[DEBUG] Mouse up at: {x}, {y} (duration: {duration}ms)")
    
        if button == mouse.Button.left:
            start_x, start_y = map_to_android(*press_pos, window_rect, android_size)
            end_x, end_y = map_to_android(x, y, window_rect, android_size)
            
            if (start_x, start_y) == (end_x, end_y):
                send_tap(start_x, start_y)
                gesture_data = {
                    "type": "tap",
                    "x": start_x,
                    "y": start_y
                }
            else:
                send_swipe(start_x, start_y, end_x, end_y, duration)
                gesture_data = {
                    "type": "swipe",
                    "x1": start_x,
                    "y1": start_y,
                    "x2": end_x,
                    "y2": end_y,
                    "duration_ms": duration
                }
            
            if args.json_stream:
                print("__GESTURE_JSON__" + json.dumps(gesture_data), flush=True)

        elif button == mouse.Button.right:
            subprocess.run(["adb", "shell", "input", "keyevent", "4"])  # BACK
    
        elif button == mouse.Button.middle:
            subprocess.run(["adb", "shell", "input", "keyevent", "3"])  # HOME
    
    listener = mouse.Listener(on_click=on_click)
    listener.start()

def launch_scrcpy():
    global SCRCPY_PROC
    SCRCPY_PROC = subprocess.Popen(["scrcpy", "--no-control", "--window-title", "scrcpy-bridge"])
    time.sleep(2)

def cleanup_and_exit(signum=None, frame=None):
    global SCRCPY_PROC
    if SCRCPY_PROC and SCRCPY_PROC.poll() is None:
        print("[INFO] Cleaning up scrcpy subprocess...")
        SCRCPY_PROC.terminate()
        try:
            SCRCPY_PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            print("[WARN] scrcpy did not terminate in time — killing")
            SCRCPY_PROC.kill()
    exit(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-stream", action="store_true", help="Emit gestures as JSON to stdout and keep running")
    args = parser.parse_args()

    launch_scrcpy()
    atexit.register(cleanup_and_exit)
    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGTERM, cleanup_and_exit)

    android_size = get_android_screen_size()
    window_rect = get_scrcpy_window_rect()
    print(f"[INFO] Android screen size: {android_size}")
    print(f"[INFO] scrcpy drawable window: {window_rect}")
    start_mouse_listener(android_size, args)
    print("Listening for clicks... Ctrl+C to quit.")
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()


