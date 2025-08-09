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

_last_region_group = None

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

def dot_path_exists(dot_path, data=None):
    return resolve_dot_path(dot_path, data) is not None

def set_dot_path(dot_path, value, allow_overwrite=False):
    parts = dot_path.split(".")
    cur = _clickmap
    for p in parts[:-1]:
        if p not in cur:
            cur[p] = {}
        elif not isinstance(cur[p], dict):
            raise ValueError(f"Cannot set path through non-dict element: {p}")
        cur = cur[p]

    final_key = parts[-1]
    if final_key in cur and not allow_overwrite:
        raise KeyError(f"Key '{dot_path}' already exists. Use allow_overwrite=True to overwrite.")
    cur[final_key] = value

def _valid_group_name(name: str) -> bool:
    """
    Keep this simple and predictable:
    - start with letter or underscore
    - then letters, digits, or underscores
    """
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    for c in name[1:]:
        if not (c.isalnum() or c == "_"):
            return False
    return True

def interactive_get_dot_path(clickmap):
    """
    Pick an existing group or create a new one, then enter the entry key (suffix).
    - [Enter] reuses last group
    - [q] cancels
    - [n] creates a new group (with validation + confirmation)
    - numeric or exact name selects an existing group
    Special-case for 'upgrades' remains intact.
    """
    global _last_region_group
    top_level_keys = list(clickmap.keys())

    while True:
        print("\nAvailable groups:")
        for i, group in enumerate(top_level_keys):
            marker = " (last used)" if group == _last_region_group else ""
            print(f"  {i + 1}. {group}{marker}")
        print("  n. <create new group>")

        prompt = "[Enter]=reuse last, [n]=new group, [q]=cancel, or choose number/name: "
        choice = input(prompt).strip()

        if choice.lower() == "q":
            print("[INFO] Skipped saving.")
            return None

        # Reuse last group
        if choice == "":
            if _last_region_group:
                group = _last_region_group
                print(f"[INFO] Reusing last group: {group}")
            else:
                print("❌ No group selected yet.")
                continue

        # Create new group
        elif choice.lower() == "n":
            new_group = input("Enter new group name (letters/digits/underscore; must start with letter/_): ").strip()
            if not _valid_group_name(new_group):
                print("❌ Invalid group name.")
                continue
            if new_group in top_level_keys:
                print("ℹ️  That group already exists; selecting it.")
                group = new_group
            else:
                confirm = input(f"Create new group '{new_group}'? (Y/n): ").strip().lower()
                if confirm not in ("", "y", "yes"):
                    print("[INFO] Creation cancelled.")
                    continue
                # Create it in-memory so it appears immediately
                clickmap[new_group] = {}
                top_level_keys.append(new_group)
                group = new_group
                print(f"[INFO] Group '{group}' created.")

        # Numeric select
        elif choice.isdigit() and 1 <= int(choice) <= len(top_level_keys):
            group = top_level_keys[int(choice) - 1]

        # Name select
        elif choice in top_level_keys:
            group = choice

        else:
            print(f"❌ Invalid selection. Choose one of: {', '.join(top_level_keys)} or 'n' for new.")
            continue

        _last_region_group = group

        # Upgrades retains its specialized flow
        if group == "upgrades":
            subgroup = input("Enter upgrade category [attack, defense, utility]: ").strip().lower()
            if subgroup not in {"attack", "defense", "utility"}:
                print("[ERROR] Invalid upgrade subgroup.")
                continue

            side = input("Enter side [left, right]: ").strip().lower()
            if side not in {"left", "right"}:
                print("[ERROR] Invalid upgrade side.")
                continue

            return f"{group}.{subgroup}.{side}"

        # Generic suffix
        suffix = input(f"Enter entry key for `{group}` (e.g., retry, attack_menu, claim_ad_gem): ").strip()
        if not suffix:
            print("[INFO] Skipped saving.")
            return None

        return f"{group}.{suffix}"

def prompt_roles(group, key):
    group = group.lower()
    if group == "gesture_targets":
        default = "gesture"
    elif group == "upgrades":
        default = "upgrade_label"
    elif group == "util":
        print(f"[?] Group '{group}' may refer to either a tap or a swipe.")
        user_input = input("    Enter roles manually (e.g., tap, swipe): ").strip()
        roles = [r.strip() for r in user_input.split(",") if r.strip()]
        return roles if roles else ["unknown"]
    else:
        default = group.rstrip("s")
    user_input = input(f"Suggested roles for `{group}:{key}`: [{default}] (edit or press Enter to accept): ").strip()
    if user_input:
        roles = [r.strip() for r in user_input.split(",")]
    else:
        roles = [default]

    return roles

def get_click(name):
    entry = resolve_dot_path(name)
    #log(f"[DEBUG] get_click: entry for {name} = {entry}", "DEBUG")
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


