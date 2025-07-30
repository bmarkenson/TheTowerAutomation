import time
import os
import json
from core.adb_utils import adb_shell
from utils.logger import log

CLICKMAP_FILE = os.path.join(os.path.dirname(__file__), "../coords/clickmap.json")

def load_clickmap():
    try:
        with open(CLICKMAP_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        log(f"[ERROR] Failed to load clickmap: {e}", "FAIL")
        return {}

def tap_named(clickmap, name):
    entry = clickmap.get(name)
    if not entry or "x" not in entry or "y" not in entry:
        log(f"[ERROR] Tap '{name}' not found or invalid in clickmap", "FAIL")
        return
    log(f"TAP {name} at ({entry['x']},{entry['y']})", "ACTION")
    adb_shell(["input", "tap", str(entry["x"]), str(entry["y"])])

def swipe_named(clickmap, name):
    entry = clickmap.get(name)
    if not entry or any(k not in entry for k in ("x1", "y1", "x2", "y2", "duration_ms")):
        log(f"[ERROR] Swipe '{name}' not found or invalid in clickmap", "FAIL")
        return
    log(f"SWIPE {name} ({entry['x1']},{entry['y1']})→({entry['x2']},{entry['y2']}) in {entry['duration_ms']}ms", "ACTION")
    adb_shell([
        "input", "swipe",
        str(entry["x1"]), str(entry["y1"]),
        str(entry["x2"]), str(entry["y2"]),
        str(entry["duration_ms"])
    ])
    time.sleep(entry["duration_ms"] / 1000.0 + .25)


