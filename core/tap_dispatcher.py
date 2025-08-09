# tap_dispatcher.py
import threading
import queue
import time
import random
from utils.logger import log
from core.adb_utils import adb_shell

TAP_QUEUE = queue.Queue()

# Configuration
KEEPALIVE_INTERVAL = 60  # seconds


def log_tap(x, y, label):
    log(f"TAP {label or ''} at ({x},{y})", level="ACTION")


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


def main():
    log("Tap dispatcher running. Press Ctrl+C to exit.", level="INFO")
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("Shutting down dispatcher.")


if __name__ == "__main__":
    main()
