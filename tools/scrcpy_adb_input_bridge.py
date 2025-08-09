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

from core.adb_utils import adb_shell
from core.ss_capture import capture_adb_screenshot

# Global State Variables
SCRCPY_WIN_ID = None
SCRCPY_WIN_RECT = None

# Global for cleanup
SCRCPY_PROC = None

# -------------------- Window lookup helpers --------------------

def _lookup_scrcpy_window_id():
    """
    Find a visible window titled exactly 'scrcpy-bridge'.
    Returns the last (most recent) id when multiple are found.
    """
    try:
        out = subprocess.check_output(
            ["xdotool", "search", "--onlyvisible", "--name", "^scrcpy-bridge$"]
        ).decode().strip()
        ids = [line for line in out.splitlines() if line]
        return ids[-1] if ids else None
    except subprocess.CalledProcessError:
        return None


def _xwininfo_rect(win_id):
    """
    Query geometry for a window id via xwininfo.
    Returns (x, y, width, height).
    """
    geo = subprocess.check_output(["xwininfo", "-id", win_id]).decode()
    width = int(re.search(r"Width:\s+(\d+)", geo).group(1))
    height = int(re.search(r"Height:\s+(\d+)", geo).group(1))
    x = int(re.search(r"Absolute upper-left X:\s+(\d+)", geo).group(1))
    y = int(re.search(r"Absolute upper-left Y:\s+(\d+)", geo).group(1))
    return (x, y, width, height)


def _largest_child_rect(win_id):
    """
    Parse xwininfo -tree for child windows and return the largest child's rect.
    Returns (x, y, w, h) or None if no child rect obtained.
    """
    tree = subprocess.check_output(['xwininfo', '-tree', '-id', win_id]).decode()
    # Extract potential child window ids (hex ids appear at line starts or after spaces)
    child_ids = []
    for line in tree.splitlines():
        m = re.search(r"\b(0x[0-9a-fA-F]+)\b", line)
        if m:
            cid = m.group(1)
            if cid.lower() != win_id.lower():
                child_ids.append(cid)
    best = None
    best_area = -1
    for cid in child_ids:
        try:
            rect = _xwininfo_rect(cid)
        except subprocess.CalledProcessError:
            continue
        _, _, w, h = rect
        if w > 0 and h > 0:
            area = w * h
            if area > best_area:
                best, best_area = rect, area
    return best


# -------------------- Public APIs --------------------

def ensure_scrcpy_window_rect(rect_source='top', diagnose=False, android_size=None):
    """
    Ensure and cache the current scrcpy window rect based on selection policy.

    rect_source: 'top' (default), 'child', or 'auto'
    diagnose: print comparison details without altering defaults
    android_size: (aw, ah) for AR checks when using 'auto' or diagnostics
    """
    global SCRCPY_WIN_ID, SCRCPY_WIN_RECT

    def _ensure_id():
        nonlocal SCRCPY_WIN_ID
        if SCRCPY_WIN_ID is None:
            SCRCPY_WIN_ID = _lookup_scrcpy_window_id()
            if SCRCPY_WIN_ID is None:
                raise RuntimeError("Could not find scrcpy window")
        return SCRCPY_WIN_ID

    def _stabilized_top_rect(win_id, max_wait=5, interval=0.25, min_w=500, min_h=500):
        attempts = int(max_wait / interval)
        last = None
        for _ in range(attempts):
            try:
                rect = _xwininfo_rect(win_id)
            except subprocess.CalledProcessError:
                # refresh id and retry once
                win_id = _lookup_scrcpy_window_id()
                if not win_id:
                    break
                rect = _xwininfo_rect(win_id)
            x, y, w, h = rect
            last = rect
            if w >= min_w and h >= min_h:
                return rect
            time.sleep(interval)
        if last is None:
            raise RuntimeError("scrcpy window not available")
        return last

    win_id = _ensure_id()

    # Candidates
    top_rect = _stabilized_top_rect(win_id)
    child_rect = None
    try:
        child_rect = _largest_child_rect(win_id)
    except subprocess.CalledProcessError:
        child_rect = None

    # Optional diagnostics
    if diagnose:
        print(f"[DIAG] top rect:   {top_rect}")
        if child_rect:
            print(f"[DIAG] child rect: {child_rect}")
        else:
            print(f"[DIAG] child rect: <none>")
        if android_size:
            aw, ah = android_size
            aar = aw / ah
            def _ar(r): return (r[2] / r[3]) if r and r[3] != 0 else 0.0
            top_ar = _ar(top_rect)
            child_ar = _ar(child_rect) if child_rect else 0.0
            print(f"[DIAG] AR android={aar:.4f} top={top_ar:.4f} child={child_ar:.4f}")
        # Diagnostics do not change selection

    # Selection
    chosen = top_rect  # default preserves existing behavior
    if rect_source == 'child':
        if child_rect:
            chosen = child_rect
        # else fallback to top
    elif rect_source == 'auto':
        # Prefer rect that best matches android AR and exceeds a minimum size
        if android_size:
            aw, ah = android_size
            aar = aw / ah
            def _ok(r):
                if not r: return False
                _, _, w, h = r
                if w < 500 or h < 500: return False
                r_ar = w / h if h else 0.0
                return abs(r_ar - aar) <= 0.08  # ±8%
            def _ardelta(r):
                if not r: return float('inf')
                _, _, w, h = r
                if h == 0: return float('inf')
                return abs((w / h) - aar)
            if _ok(child_rect) and not _ok(top_rect):
                chosen = child_rect
            elif _ok(child_rect) and _ok(top_rect):
                chosen = child_rect if _ardelta(child_rect) < _ardelta(top_rect) else top_rect
            else:
                chosen = top_rect  # fallback
        else:
            # Without android_size, fall back to top unless child exists and is clearly larger
            if child_rect:
                _, _, tw, th = top_rect
                _, _, cw, ch = child_rect
                chosen = child_rect if (cw * ch) > (tw * th) else top_rect

    if SCRCPY_WIN_RECT != chosen:
        print(f"[INFO] Detected scrcpy window change: {SCRCPY_WIN_RECT} -> {chosen}")
        SCRCPY_WIN_RECT = chosen

    return SCRCPY_WIN_RECT


def get_android_screen_size():
    """
    Returns (width, height) of the current Android framebuffer.
    Uses centralized capture to avoid duplication and device drift.
    """
    img = capture_adb_screenshot()
    if img is None:
        raise RuntimeError("Failed to capture Android screen")
    h, w = img.shape[:2]
    return (w, h)


def get_scrcpy_window_rect(rect_source='top', diagnose=False, android_size=None):
    """
    Returns (x, y, w, h) of the drawable area based on selection policy.
    Default behavior matches prior implementation ('top').
    """
    win_rect = ensure_scrcpy_window_rect(rect_source=rect_source, diagnose=diagnose, android_size=android_size)
    return win_rect


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


def send_tap(x, y):
    print(f"[ADB] tap {x}, {y}")
    adb_shell(["input", "tap", str(x), str(y)])


def send_swipe(x1, y1, x2, y2, duration_ms):
    print(f"[ADB] swipe {x1},{y1} -> {x2},{y2} ({duration_ms}ms)")
    adb_shell(["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])


def get_pixel_color_at_android_coords(x, y):
    try:
        img = capture_adb_screenshot()
        if img is None:
            raise RuntimeError("ADB screenshot decode failed")
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
            window_rect = ensure_scrcpy_window_rect(
                rect_source=args.rect_source,
                diagnose=args.rect_diagnose,
                android_size=android_size
            )
        except RuntimeError as e:
            print(f"[ERROR] Could not resolve scrcpy window: {e}")
            return

        win_x, win_y, win_w, win_h = window_rect
        inside = (win_x <= x <= win_x + win_w and win_y <= y <= win_y + win_h)

        if not inside:
            if pressed:
                pass
            return

        if pressed:
            press_pos = (x, y)
            press_time = time.time()
            return  # don't proceed further on press

        if press_time is None:
            print("[WARN] Mouse release detected but press_time is None — ignoring.")
            return

        release_time = time.time()
        duration = int((release_time - press_time) * 1000)

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
            adb_shell(["input", "keyevent", "4"])  # BACK

        elif button == mouse.Button.middle:
            adb_shell(["input", "keyevent", "3"])  # HOME

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
    parser.add_argument("--json-stream", action="store_true",
                        help="Emit gestures as JSON to stdout and keep running")
    parser.add_argument("--rect-source", choices=["top", "child", "auto"], default="top",
                        help="Window rect selection policy (default: top)")
    parser.add_argument("--rect-diagnose", action="store_true",
                        help="Print diagnostic info about rect candidates and AR deltas")
    args = parser.parse_args()

    launch_scrcpy()
    atexit.register(cleanup_and_exit)
    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGTERM, cleanup_and_exit)

    android_size = get_android_screen_size()
    window_rect = get_scrcpy_window_rect(rect_source=args.rect_source,
                                         diagnose=args.rect_diagnose,
                                         android_size=android_size)
    print(f"[INFO] Android screen size: {android_size}")
    print(f"[INFO] scrcpy drawable window: {window_rect}")
    start_mouse_listener(android_size, args)
    print("Listening for clicks... Ctrl+C to quit.")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
