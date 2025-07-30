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

# Shared helpers

def resolve_named_point(entry):
    """Return (x, y) from either flat or 'tap' nested format"""
    if "x" in entry and "y" in entry:
        return entry["x"], entry["y"]
    elif "tap" in entry and "x" in entry["tap"] and "y" in entry["tap"]:
        return entry["tap"]["x"], entry["tap"]["y"]
    return None, None

def resolve_named_swipe(entry):
    """Return swipe dict from either flat or nested 'swipe' format"""
    if all(k in entry for k in ("x1", "y1", "x2", "y2", "duration_ms")):
        return entry
    elif "swipe" in entry and all(k in entry["swipe"] for k in ("x1", "y1", "x2", "y2", "duration_ms")):
        return entry["swipe"]
    return None

# Updated public functions

def tap_named(clickmap, name):
    entry = clickmap.get(name)
    if not entry:
        log(f"[ERROR] Tap '{name}' not found in clickmap", "FAIL")
        return

    x, y = resolve_named_point(entry)
    if x is None or y is None:
        log(f"[ERROR] Tap '{name}' entry missing coordinates", "FAIL")
        return

    log(f"TAP {name} at ({x},{y})", "ACTION")
    adb_shell(["input", "tap", str(x), str(y)])

def swipe_named(clickmap, name):
    entry = clickmap.get(name)
    if not entry:
        log(f"[ERROR] Swipe '{name}' not found in clickmap", "FAIL")
        return

    swipe = resolve_named_swipe(entry)
    if swipe is None:
        log(f"[ERROR] Swipe '{name}' entry missing swipe data", "FAIL")
        return

    log(f"SWIPE {name} ({swipe['x1']},{swipe['y1']})→({swipe['x2']},{swipe['y2']}) in {swipe['duration_ms']}ms", "ACTION")
    adb_shell([
        "input", "swipe",
        str(swipe["x1"]), str(swipe["y1"]),
        str(swipe["x2"]), str(swipe["y2"]),
        str(swipe["duration_ms"])
    ])
    time.sleep(swipe["duration_ms"] / 1000.0 + .25)
