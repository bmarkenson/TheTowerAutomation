#!/usr/bin/env python3

import subprocess
import time
import threading
import tap_dispatcher

from datetime import datetime

# --- Tap configuration ---
# "Ad gem" – taps every 10 seconds
AD_GEM_X = 50
AD_GEM_Y = 1037
AD_GEM_INTERVAL = 10  # seconds

# "Floating gem" – taps every 1 second
FLOATING_GEM_X = 540
FLOATING_GEM_Y = 846
FLOATING_GEM_INTERVAL = 5  # seconds

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def tap(x, y, name):
    tap_dispatcher.tap(x, y, label=name)

def tap_loop(x, y, interval, name):
    log(f"Starting '{name}' loop at ({x}, {y}) every {interval}s", "INFO")
    while True:
        tap(x, y, name)
        time.sleep(interval)

def main():
    # Start tap loop for "Ad gem"
    thread_ad_gem = threading.Thread(
        target=tap_loop, args=(AD_GEM_X, AD_GEM_Y, AD_GEM_INTERVAL, "Ad gem"), daemon=True)

    # Start tap loop for "Floating gem"
    thread_floating_gem = threading.Thread(
        target=tap_loop, args=(FLOATING_GEM_X, FLOATING_GEM_Y, FLOATING_GEM_INTERVAL, "Floating gem"), daemon=True)

    thread_ad_gem.start()
    thread_floating_gem.start()

    log(f"Both tap loops running. Ctrl+C to stop.", "INFO")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log(f"Stopping tap loops.", "INFO")

if __name__ == "__main__":
    main()

