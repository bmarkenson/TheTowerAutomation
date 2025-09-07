#!/usr/bin/env python3
"""
Validate state_definitions.yaml against clickmap.json.

- Supports both dict schema and list-of-objects schema {name, match_keys, ...}
- Uses clickmap_access.{dot_path_exists, resolve_dot_path} (no flattening)
- Fails if required states are missing, or any state has 0 valid match_keys,
  or if there are dangling match_keys that don't exist in the clickmap.

Usage:
  python test/validate_state_defs.py
  python test/validate_state_defs.py --require RUNNING HOME_SCREEN GAME_OVER
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core.clickmap_access import dot_path_exists, resolve_dot_path  # type: ignore
from core.state_detector import load_state_definitions              # type: ignore

TRIM_SUFFIXES = (".match_region",".region_ref",".match_template",".match_threshold",".roles",".tap",".swipe")

def _trim(key: str) -> str:
    for suf in TRIM_SUFFIXES:
        i = key.find(suf)
        if i != -1:
            return key[:i]
    return key

def _collect_sections(state_defs):
    """Return (states: dict[name->block], overlays: dict[name->block]) for dict or list schema."""
    states, overlays = {}, {}
    if not isinstance(state_defs, dict):
        return states, overlays

    def coerce(sec):
        out = {}
        if isinstance(sec, dict):
            for name, blk in sec.items():
                if isinstance(name, str) and isinstance(blk, dict):
                    out[name] = blk
        elif isinstance(sec, list):
            for item in sec:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("id") or item.get("state")
                    if isinstance(name, str):
                        out[name] = item
        return out

    states = coerce(state_defs.get("states"))
    overlays = coerce(state_defs.get("overlays"))
    return states, overlays

def _extract_match_keys(block):
    keys = []
    mk = block.get("match_keys")
    if isinstance(mk, list):
        keys = [k for k in mk if isinstance(k, str)]
    return keys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--require", nargs="*", default=["GAME_OVER", "HOME_SCREEN", "RUNNING"])
    args = ap.parse_args()

    defs = load_state_definitions()
    states, overlays = _collect_sections(defs)

    ok = True

    # 1) Required states present
    missing = [s for s in args.require if s not in states]
    if missing:
        print("[ERROR] Missing required states:", ", ".join(missing))
        ok = False

    # 2) Validate each state's match_keys
    bad_refs = []
    zero_valid = []
    for st_name, blk in sorted(states.items()):
        keys = _extract_match_keys(blk)
        valid_count = 0
        for raw in keys:
            key = _trim(raw)
            if not dot_path_exists(key):
                bad_refs.append((st_name, raw))
                continue
            try:
                obj = resolve_dot_path(key)
                # must be either an entry dict with match_region/region_ref or a bare region dict
                if isinstance(obj, dict) and (("match_region" in obj) or ("region_ref" in obj) or all(k in obj for k in ("x","y","w","h"))):
                    valid_count += 1
                else:
                    bad_refs.append((st_name, raw))
            except Exception:
                bad_refs.append((st_name, raw))
        if valid_count == 0:
            zero_valid.append(st_name)

    for st in zero_valid:
        print(f"[ERROR] State {st} references 0 valid clickmap paths.")
        ok = False

    if bad_refs:
        print("\n[WARN] YAML match_keys not found/invalid in clickmap (showing up to 20):")
        shown = 0
        for st, raw in bad_refs:
            print(f"  - {st}: {raw}")
            shown += 1
            if shown >= 20:
                break
        # treat as error so CI blocks on drift/typos
        ok = False

    if ok:
        print("[OK] state_definitions.yaml passes validation.")
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
