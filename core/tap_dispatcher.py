# tap_dispatcher.py
import threading
import queue
import time
import subprocess
import random
from datetime import datetime
from utils.logger import log

TAP_QUEUE = queue.Queue()
LOCK = threading.Lock()

# Configuration
KEEPALIVE_INTERVAL = 60  # seconds
#ADB_DEVICE_ID = "192.168.1.171:5555"  # or leave blank if only one device
ADB_DEVICE_ID = "ce06171664278137027e"

def log_tap(x, y, label):
    log(f"TAP {label or ''} at ({x},{y})", level="ACTION")

def adb_shell(cmd):
    base_cmd = ["adb"]
    if ADB_DEVICE_ID:
        base_cmd += ["-s", ADB_DEVICE_ID]
    full_cmd = base_cmd + ["shell"] + cmd
    subprocess.run(full_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def tap(x, y, label=None):
    """Public function for scripts to submit tap requests."""
    TAP_QUEUE.put((x, y, label))

def _tap_worker():
    last_keepalive = time.time()
    while True:
        now = time.time()
        try:
            x, y, label = TAP_QUEUE.get(timeout=1)
            adb_shell(["input", "tap", str(x), str(y)])
            log_tap(x, y, label)
        except queue.Empty:
            pass  # nothing to do

        # Keepalive swipe every KEEPALIVE_INTERVAL
        if now - last_keepalive > KEEPALIVE_INTERVAL:
            x1, y1 = random.randint(50, 100), random.randint(50, 100)
            x2, y2 = x1 + 1, y1 + 1
            adb_shell(["input", "swipe", str(x1), str(y1), str(x2), str(y2), "100"])
            log(f"KEEPALIVE swipe at ({x1},{y1})→({x2},{y2})", level="ACTION")
            last_keepalive = now

# Start worker thread
threading.Thread(target=_tap_worker, daemon=True).start()

if __name__ == "__main__":
    log("Tap dispatcher running. Press Ctrl+C to exit.", level="INFO")
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("Shutting down dispatcher.")
