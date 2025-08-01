#!/usr/bin/env python3

import subprocess
import re
import signal
import atexit
import sys

# Target framebuffer height
EXPANDED_HEIGHT = 3600

def get_current_resolution():
    output = subprocess.check_output(["xrandr"]).decode()
    match = re.search(r"current (\d+) x (\d+)", output)
    if match:
        return int(match.group(1)), int(match.group(2))
    raise RuntimeError("Failed to detect current resolution.")

def set_framebuffer(width, height):
    subprocess.run(["xrandr", "--fb", f"{width}x{height}"], check=True)

def run_crop_region():
    subprocess.run(["python3", "tools/crop_region.py"])

def main():
    width, height = get_current_resolution()
    print(f"[INFO] Current resolution: {width}x{height}")

    def restore_resolution():
        print(f"[INFO] Restoring resolution to {width}x{height}")
        set_framebuffer(width, height)

    atexit.register(restore_resolution)
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))

    try:
        print(f"[INFO] Expanding framebuffer to {width}x{EXPANDED_HEIGHT}")
        set_framebuffer(width, EXPANDED_HEIGHT)
        run_crop_region()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
