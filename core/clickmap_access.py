import os
import json
from core.adb_utils import adb_shell
from utils.logger import log

CLICKMAP_FILE = os.path.join(os.path.dirname(__file__), "../config/clickmap.json")

try:
    with open(CLICKMAP_FILE, "r") as f:
        _clickmap = json.load(f)
except Exception as e:
    log(f"[ERROR] Failed to load clickmap: {e}", "FAIL")
    _clickmap = {}

def get_clickmap():
    return _clickmap

def get_click(name):
    try:
        tap = _clickmap[name]["tap"]
        return tap["x"], tap["y"]
    except (KeyError, TypeError):
        return None

def get_swipe(name):
    try:
        return _clickmap[name]["swipe"]
    except (KeyError, TypeError):
        return None

def has_click(name):
    return get_click(name) is not None

def tap_now(name):
    pos = get_click(name)
    if pos:
        log(f"TAP_NOW: {name} at {pos}", "ACTION")
        adb_shell(["input", "tap", str(pos[0]), str(pos[1])])
    else:
        log(f"[ERROR] tap_now: No coordinates for '{name}'", "FAIL")

def swipe_now(name):
    swipe = get_swipe(name)
    if swipe:
        log(f"SWIPE_NOW: {name} ({swipe['x1']},{swipe['y1']})→({swipe['x2']},{swipe['y2']}) in {swipe['duration_ms']}ms", "ACTION")
        adb_shell([
            "input", "swipe",
            str(swipe["x1"]), str(swipe["y1"]),
            str(swipe["x2"]), str(swipe["y2"]),
            str(swipe["duration_ms"])
        ])
    else:
        log(f"[ERROR] swipe_now: No swipe data for '{name}'", "FAIL")


