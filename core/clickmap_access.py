import os
import json
from typing import Any, Dict, Optional, Tuple, List, Mapping
from core.adb_utils import adb_shell
from utils.logger import log

CLICKMAP_FILE = os.path.join(os.path.dirname(__file__), "../config/clickmap.json")

try:
    with open(CLICKMAP_FILE, "r", encoding="utf-8") as f:
        _clickmap: Dict[str, Any] = json.load(f)
except Exception as e:
    log(f"[ERROR] Failed to load clickmap: {e}", "FAIL")
    _clickmap = {}

_last_region_group: Optional[str] = None

def get_clickmap() -> Dict[str, Any]:
    """
    ---
    spec:
      r: "dict[str, Any] (mutable reference)"
      s: []
      e: []
      params: {}
      notes:
        - "Backed by module-global _clickmap loaded at import time"
        - "Mutations affect in-memory state; persist via save_clickmap()"
    ---
    Return the in-memory clickmap dict (mutable reference).
    """
    return _clickmap

def get_clickmap_path() -> str:
    """
    ---
    spec:
      r: "str (absolute path)"
      s: []
      e: []
      params: {}
      notes:
        - "Resolves to utils/config/clickmap.json relative to this module"
    ---
    Return absolute filesystem path to the clickmap JSON file.
    """
    return CLICKMAP_FILE

def resolve_dot_path(dot_path: str, data: Optional[Mapping[str, Any]] = None) -> Any:
    """
    ---
    spec:
      r: "Any | None"
      s: []
      e: []
      params:
        dot_path: "str — nested keys separated by '.' (colons ':' are part of keys)"
        data: "mapping | None — optional root mapping; defaults to global clickmap"
      notes:
        - "Returns None if any segment is missing or a non-dict is traversed"
    ---
    Resolve and return the value at a dot-separated path in the provided mapping
    (or the global clickmap). Return None if any segment is missing.
    """
    parts = dot_path.split(".")
    cur: Any = data or _clickmap
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur

def dot_path_exists(dot_path: str, data: Optional[Mapping[str, Any]] = None) -> bool:
    """
    ---
    spec:
      r: "bool"
      s: []
      e: []
      params:
        dot_path: "str"
        data: "mapping | None"
      notes:
        - "Thin wrapper over resolve_dot_path()"
    ---
    True if resolve_dot_path finds a non-None value; False otherwise.
    """
    return resolve_dot_path(dot_path, data) is not None

def set_dot_path(dot_path: str, value: Any, allow_overwrite: bool = False) -> None:
    """
    ---
    spec:
      r: "None"
      s: []
      e:
        - "KeyError if final key exists and allow_overwrite is False"
        - "ValueError if path traverses a non-dict element"
      params:
        dot_path: "str"
        value: "Any"
        allow_overwrite: "bool — default False"
      notes:
        - "Creates intermediate dicts as needed; does not persist to disk"
    ---
    Set value at dot-separated path in the global clickmap.
    Creates intermediate dicts as needed.
    Raises KeyError on existing final key unless allow_overwrite=True.
    Raises ValueError if path traverses a non-dict.
    """
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
    ---
    spec:
      r: "bool"
      s: []
      e: []
      params:
        name: "str"
      notes:
        - "Internal helper used by interactive flows"
    ---
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

def interactive_get_dot_path(clickmap: Dict[str, Any]) -> Optional[str]:
    """
    ---
    spec:
      r: "str | None"
      s: ["fs?", "stdio"]
      e: []
      params:
        clickmap: "dict[str, Any] — working mapping to display/update"
      notes:
        - "Interactive console UI; updates _last_region_group on selection"
        - "Special-cases 'upgrades' group for nested category/side/label"
        - "Returns dot-path string or None on cancel"
    ---
    Interactive helper to choose/create a top-level group and enter an entry key.
    Returns 'group.suffix' for generic groups, or 'upgrades.<category>.<side>.<label>' for upgrades,
    or None if the user cancels. Updates _last_region_group when selection is made. (I/O via input/print)
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
                print("ℹ️    That group already exists; selecting it.")
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

            label = input("Enter upgrade label key (e.g., damage, attack_speed): ").strip()
            if not _valid_group_name(label):
                print("❌ Invalid label key. Use letters/digits/underscore; start with letter/_")
                continue

            return f"{group}.{subgroup}.{side}.{label}"

        # Generic suffix
        suffix = input(f"Enter entry key for `{group}` (e.g., retry, attack_menu, claim_ad_gem): ").strip()
        if not suffix:
            print("[INFO] Skipped saving.")
            return None

        return f"{group}.{suffix}"

def prompt_roles(group: str, key: str) -> List[str]:
    """
    ---
    spec:
      r: "list[str]"
      s: ["stdio"]
      e: []
      params:
        group: "str"
        key: "str"
      notes:
        - "Suggests a role based on group; user can override via input"
        - "Returns [default] if user presses Enter"
    ---
    Suggest roles for a (group, key) and allow interactive override.
    """
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

def get_click(name: str) -> Optional[Tuple[int, int]]:
    """
    ---
    spec:
      r: "tuple[int,int] | None"
      s: ["log?"]
      e: []
      params:
        name: "str — dot-path to entry"
      notes:
        - "Prefers explicit 'tap' coords; falls back to center of 'match_region'"
        - "Logs WARN if dot-path cannot be resolved"
    ---
    Return (x, y) for a named entry. Prefers explicit 'tap' coords; falls
    back to the center of 'match_region'. Returns None if unresolved.
    Note: does not perform any device I/O.
    """
    entry = resolve_dot_path(name)
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

def get_swipe(name: str) -> Optional[Dict[str, int]]:
    """
    ---
    spec:
      r: "dict[str,int] | None"
      s: []
      e: []
      params:
        name: "str"
      notes:
        - "Returns stored swipe params {x1,y1,x2,y2,duration_ms} or None"
    ---
    Return a swipe dict {x1,y1,x2,y2,duration_ms} for a named entry, or None.
    """
    entry = resolve_dot_path(name)
    if not entry:
        return None
    return entry.get("swipe")

def has_click(name: str) -> bool:
    """
    ---
    spec:
      r: "bool"
      s: ["log?"]
      e: []
      params:
        name: "str"
      notes:
        - "Delegates to get_click(); may log WARN if unresolved"
    ---
    True if get_click(name) resolves to coordinates.
    """
    return get_click(name) is not None

def tap_now(name: str) -> None:
    """
    ---
    spec:
      r: "None"
      s: ["adb", "log"]
      e:
        - "No exception on ADB failures; adb_shell() handles errors and returns None"
      params:
        name: "str"
      notes:
        - "Logs ACTION on success; logs FAIL if coordinates unavailable"
    ---
    Issue an ADB tap at the resolved coordinates for 'name'. Logs action.
    """
    pos = get_click(name)
    if pos:
        log(f"TAP_NOW: {name} at {pos}", "ACTION")
        adb_shell(["input", "tap", str(pos[0]), str(pos[1])])
    else:
        log(f"[ERROR] tap_now: No coordinates for '{name}'", "FAIL")

def swipe_now(name: str) -> None:
    """
    ---
    spec:
      r: "None"
      s: ["adb", "log"]
      e:
        - "No exception on ADB failures; adb_shell() handles errors and returns None"
      params:
        name: "str"
      notes:
        - "Uses stored swipe parameters under entry['swipe']"
        - "Logs FAIL if swipe data missing"
    ---
    Issue an ADB swipe for 'name' using stored swipe parameters. Logs action.
    """
    swipe = get_swipe(name)
    if swipe:
        log(f"SWIPE_NOW: {name} ({swipe['x1']},{swipe['y1']})→({swipe['x2']},{swipe['x2']}) in {swipe['duration_ms']}ms", "ACTION")
        adb_shell([
            "input", "swipe",
            str(swipe["x1"]), str(swipe["y1"]),
            str(swipe["x2"]), str(swipe["y2"]),
            str(swipe["duration_ms"])
        ])
    else:
        log(f"[ERROR] swipe_now: No swipe data for '{name}'", "FAIL")

def save_clickmap(data: Optional[Dict[str, Any]] = None) -> None:
    """
    ---
    spec:
      r: "None"
      s: ["fs"]
      e:
        - "OSError/IOError may propagate on write/replace errors"
      params:
        data: "dict[str, Any] | None — defaults to global _clickmap"
      notes:
        - "Writes UTF-8 JSON atomically via temp file + os.replace"
    ---
    Persist the clickmap (or provided dict) to disk atomically as UTF-8 JSON.
    """
    if data is None:
        data = _clickmap
    tmp_path = CLICKMAP_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, CLICKMAP_FILE)
    print("[INFO] Saved clickmap to", CLICKMAP_FILE)

def flatten_clickmap(data: Optional[Dict[str, Any]] = None, prefix: str = "") -> Dict[str, Any]:
    """
    ---
    spec:
      r: "dict[str, Any] — flat dot-path → leaf value"
      s: []
      e: []
      params:
        data: "dict[str, Any] | None — defaults to global _clickmap"
        prefix: "str — optional path prefix"
      notes:
        - "Recurses into dicts; leaves non-dict values at their full dot-path"
    ---
    Return a flat mapping of dot-path → leaf value for the clickmap (or provided dict).
    """
    entries: Dict[str, Any] = {}
    if data is None:
        data = _clickmap
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            entries.update(flatten_clickmap(value, full_key))
        else:
            entries[full_key] = value
    return entries

def get_entries_by_role(role: str) -> Dict[str, Dict[str, Any]]:
    """
    ---
    spec:
      r: "dict[str, dict] — dot-path → entry with matching role"
      s: []
      e: []
      params:
        role: "str — role name to filter by (must be present in entry['roles'])"
      notes:
        - "Searches entire clickmap for dicts containing 'roles' with the given role"
    ---
    Return a dict of entries (dot-path → entry) whose 'roles' includes the given role.
    """
    results: Dict[str, Dict[str, Any]] = {}
    def _search(d: Dict[str, Any], path: str = "") -> None:
        for k, v in d.items():
            new_path = f"{path}.{k}" if path else k
            if isinstance(v, dict):
                if "roles" in v and role in v["roles"]:
                    results[new_path] = v
                _search(v, new_path)
    _search(_clickmap)
    return results
