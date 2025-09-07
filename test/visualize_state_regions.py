#!/usr/bin/env python3
"""
Visualize which clickmap boxes are referenced by selected states/overlays.

Relies ONLY on your real APIs:
- core.clickmap_access: get_clickmap, dot_path_exists, resolve_dot_path
- core.label_tapper:   resolve_region
- core.state_detector: load_state_definitions

YAML support:
- states/overlays may be dicts OR lists of objects with {name, match_keys, ...}.

Usage:
  # draw GAME_OVER + overlays
  test/visualize_state_regions.py --states GAME_OVER --include-overlays --scale 0.8 --thickness 4 --dump out/boxes.json

  # draw multiple states
  test/visualize_state_regions.py --states GAME_OVER HOME_SCREEN --scale 0.8

Notes:
- Boxes fully off the screenshot won’t be visible; counts still print.
"""
import argparse, json, os, sys
import cv2

# repo import path
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core.clickmap_access import get_clickmap, dot_path_exists, resolve_dot_path  # type: ignore
from core.label_tapper import resolve_region  # type: ignore
from core.state_detector import load_state_definitions  # type: ignore
from utils.logger import log  # type: ignore


TRIM_SUFFIXES = (
    ".match_region",
    ".region_ref",
    ".match_template",
    ".match_threshold",
    ".roles",
    ".tap",
    ".swipe",
)

def _trim_to_parent(key: str) -> str:
    for suf in TRIM_SUFFIXES:
        i = key.find(suf)
        if i != -1:
            return key[:i]
    return key

def _font(img_shape):
    h, w = img_shape[:2]
    base = min(w, h)
    if base <= 1080: return 0.7, 2
    if base <= 1440: return 0.8, 2
    if base <= 2160: return 1.0, 2
    return 1.3, 3

def _hash_color(name: str):
    h = 0
    for ch in name: h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return int(50 + (h & 0x7F)), int(50 + ((h >> 7) & 0x7F)), int(50 + ((h >> 14) & 0x7F))  # B,G,R

def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def _collect_state_blocks(state_defs):
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
                if not isinstance(item, dict): continue
                name = item.get("name") or item.get("id") or item.get("state")
                if isinstance(name, str): out[name] = item
        return out
    states = coerce(state_defs.get("states"))
    overlays = coerce(state_defs.get("overlays"))
    return states, overlays

def _extract_match_keys(block):
    """Prefer explicit match_keys; otherwise scan for strings (lightweight)."""
    keys = []
    mk = block.get("match_keys")
    if isinstance(mk, list):
        for s in mk:
            if isinstance(s, str):
                keys.append(s)
    return keys

def _resolve_key_to_region(key: str, clickmap: dict):
    """
    key -> entry dict (match_region/region_ref) -> resolve_region
       OR key -> region dict with x/y/w/h
       returns (x,y,w,h) or raises
    """
    obj = resolve_dot_path(key)
    if not isinstance(obj, dict):
        raise TypeError(f"dot path is not a dict: {key}")
    # entry dict?
    if ("match_region" in obj) or ("region_ref" in obj):
        reg = resolve_region(obj, clickmap)
        return int(reg["x"]), int(reg["y"]), int(reg["w"]), int(reg["h"])
    # or a bare region
    if all(k in obj for k in ("x", "y", "w", "h")):
        return int(obj["x"]), int(obj["y"]), int(obj["w"]), int(obj["h"])
    raise ValueError(f"dot path has no region: {key}")

def _draw(img, groups, scale=1.0, thickness=2, title=None):
    out = img.copy()
    font_scale, default_th = _font(out.shape)
    if thickness <= 0: thickness = default_th
    for group_name, boxes in groups:
        color = _hash_color(group_name)
        for b in boxes:
            p1 = (b["x"], b["y"])
            p2 = (b["x"] + b["w"], b["y"] + b["h"])
            cv2.rectangle(out, p1, p2, color, thickness)
            label = f'{group_name}:{b["name"]}'
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, max(1, thickness - 1))
            tx, ty = p1[0], max(0, p1[1] - 4)
            cv2.rectangle(out, (tx, ty - th - 4), (tx + tw + 6, ty + 3), (0, 0, 0), -1)
            cv2.putText(out, label, (tx + 3, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, max(1, thickness - 1), cv2.LINE_AA)
    if title:
        (tw, th), _ = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        cv2.rectangle(out, (10, 10), (10 + tw + 12, 10 + th + 12), (0, 0, 0), -1)
        cv2.putText(out, title, (16, 10 + th + 2), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
    if scale != 1.0:
        out = cv2.resize(out, (int(out.shape[1] * scale), int(out.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="screenshots/latest.png")
    ap.add_argument("--out", default="out/visual_state_regions.png")
    ap.add_argument("--states", nargs="*", help="Limit to specific state names")
    ap.add_argument("--include-overlays", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--thickness", type=int, default=2)
    ap.add_argument("--dump", default=None)
    args = ap.parse_args()

    _ensure_dir(args.out)
    img = cv2.imread(args.image, cv2.IMREAD_COLOR)
    if img is None:
        print(f"[ERROR] Could not read image: {args.image}")
        sys.exit(2)
    clickmap = get_clickmap()
    state_defs = load_state_definitions()
    states_map, overlays_map = _collect_state_blocks(state_defs)

    # Filter states if requested
    if args.states:
        want = set(args.states)
        states_map = {k: v for k, v in states_map.items() if k in want}
        if not states_map:
            print("[WARN] No matching states after --states filter.")

    # Build worklist of (group_name, keys[])
    work = []
    for st_name, blk in states_map.items():
        keys = _extract_match_keys(blk)
        work.append((st_name, keys))
    if args.include_overlays:
        for ov_name, blk in overlays_map.items():
            keys = _extract_match_keys(blk)
            work.append((f"OV:{ov_name}", keys))

    # Resolve and draw
    groups = []
    summary = []
    missing = []
    offscreen_total = 0

    for group_name, keys in work:
        boxes = []
        onscreen = 0
        offscreen = 0
        for raw in keys:
            if not isinstance(raw, str): continue
            key = _trim_to_parent(raw)
            if not dot_path_exists(key):
                missing.append(key)
                continue
            try:
                x, y, w, h = _resolve_key_to_region(key, clickmap)
                boxes.append({"name": key.split(".")[-1], "x": x, "y": y, "w": w, "h": h, "dot_path": key})
                # crude on/off classification
                H, W = img.shape[:2]
                if (x + w) <= 0 or (y + h) <= 0 or x >= W or y >= H:
                    offscreen += 1
                else:
                    onscreen += 1
            except Exception as e:
                log(f"[WARN] Failed to resolve {key}: {e}", "WARN")
        if boxes:
            groups.append((group_name, boxes))
        summary.append((group_name, len(boxes), onscreen, offscreen))
        offscreen_total += offscreen

    print("\n[DISCOVERY]")
    for name, n, on, off in summary:
        print(f"  {name}: {n} boxes (onscreen={on}, offscreen={off})")
    if missing:
        uniq = sorted(set(missing))
        print("\n[WARN] YAML match_keys not found in clickmap (after normalization), first 20:")
        for s in uniq[:20]:
            print("   -", s)

    composed = _draw(img, groups, scale=args.scale, thickness=args.thickness, title="visualize_state_regions")
    cv2.imwrite(args.out, composed)
    print(f"[INFO] Wrote: {args.out}")

    if args.dump:
        payload = {
            "image": args.image,
            "image_shape": img.shape,
            "groups": {name: boxes for name, boxes in groups},
            "missing_keys": sorted(set(missing)),
        }
        _ensure_dir(args.dump)
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"[INFO] Wrote JSON dump: {args.dump}")

if __name__ == "__main__":
    main()
