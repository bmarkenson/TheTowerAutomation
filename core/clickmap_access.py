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

def get_clickmap_path():
    return CLICKMAP_FILE

def resolve_dot_path(dot_path, data=None):
    parts = dot_path.split(".")
    cur = data or _clickmap
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur

def get_click(name):
    entry = resolve_dot_path(name)
    log(f"[DEBUG] get_click: entry for {name} = {entry}", "DEBUG")
    if not entry:
        log(f"[DEBUG] get_click: resolve_dot_path failed for {name}", "WARN")
        return None
    if "tap" in entry:
        tap = entry["tap"]
        return tap["x"], tap["y"]
    elif "match_region" in entry:
        region = entry["match_region"]
        x, y, w, h = map(int, (region["x"], region["y"], region["w"], region["h"]))
        return x + w // 2, y + h // 2
    return None

def get_swipe(name):
    entry = resolve_dot_path(name)
    if not entry:
        return None
    return entry.get("swipe")

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

def save_clickmap(data=None):
    if data is None:
        data = _clickmap
    tmp_path = CLICKMAP_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, CLICKMAP_FILE)
    print("[INFO] Saved clickmap to", CLICKMAP_FILE)

def flatten_clickmap(data=None, prefix=""):
    entries = {}
    if data is None:
        data = _clickmap
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            entries.update(flatten_clickmap(value, full_key))
        else:
            entries[full_key] = value
    return entries

def get_entries_by_role(role):
    results = {}
    def _search(d, path=""):
        for k, v in d.items():
            new_path = f"{path}.{k}" if path else k
            if isinstance(v, dict):
                if "roles" in v and role in v["roles"]:
                    results[new_path] = v
                _search(v, new_path)
    _search(_clickmap)
    return results


