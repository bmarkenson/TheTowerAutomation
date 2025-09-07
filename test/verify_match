#!/usr/bin/env python3
"""
verify_match.py — Surgical diagnostics for a clickmap entry (default: indicators.game_over)

Checks:
  1) clickmap resolution + entry dump
  2) screenshot dims vs. region bounds
  3) ROI dump + overlay image
  4) normal match via core.matcher._match_entry
  5) fullscreen probe (bypasses region)
  6) optional multiscale probe to find best scale

Usage:
  python3 test/verify_match.py \
    --dot-path indicators.game_over \
    --screenshot screenshots/latest.png \
    --template-dir assets/match_templates \
    --multiscale-probe
"""

import os
import sys
import argparse
import json
import cv2
import numpy as np

from core.clickmap_access import get_clickmap, resolve_dot_path
from core.matcher import _match_entry  # low-level helper used by the shim

def to_gray(img):
    if img is None:
        return None
    if len(img.shape) == 3 and img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img

def multiscale_probe(roi, tpl_path, scales):
    tpl = cv2.imread(tpl_path)
    if tpl is None:
        raise FileNotFoundError(f"Template not found or unreadable: {tpl_path}")
    roi_g = to_gray(roi)
    tpl_g = to_gray(tpl)

    best = (-1.0, None, None)  # (conf, scale, loc)
    for s in scales:
        if s <= 0:
            continue
        if s == 1.0:
            t = tpl_g
        else:
            t = cv2.resize(tpl_g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        th, tw = t.shape[:2]
        rh, rw = roi_g.shape[:2]
        if th >= rh or tw >= rw or th < 5 or tw < 5:
            continue
        res = cv2.matchTemplate(roi_g, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if mv > best[0]:
            best = (mv, s, ml)
    return best  # (conf, scale, loc)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dot-path", default="indicators.game_over")
    ap.add_argument("--screenshot", default="screenshots/latest.png")
    ap.add_argument("--template-dir", default="assets/match_templates")
    ap.add_argument("--no-roi-files", action="store_true", help="don't write debug images")
    ap.add_argument("--multiscale-probe", action="store_true", help="scan scales 0.6..1.6")
    args = ap.parse_args()

    # 1) Load screenshot
    screen = cv2.imread(args.screenshot)
    if screen is None:
        print(f"[FATAL] Could not read screenshot: {args.screenshot}")
        sys.exit(2)
    H, W = screen.shape[:2]
    print(f"[INFO] Screen: {W}x{H}")

    # 2) Warm clickmap and resolve entry
    get_clickmap()
    entry = resolve_dot_path(args.dot_path)
    print(f"[INFO] Resolved {args.dot_path}? {bool(entry)}")
    if not entry:
        sys.exit(1)

    dump = {k: entry[k] for k in ("match_template", "match_region", "region_ref", "match_threshold", "match_padding") if k in entry}
    print("[INFO] Entry:", json.dumps(dump, indent=2))

    # Resolve region_ref if present
    region = entry.get("match_region")
    if region is None and "region_ref" in entry:
        ref = resolve_dot_path(f"_shared_match_regions.{entry['region_ref']}")
        region = ref.get("match_region") if ref else None
        print(f"[INFO] Region via region_ref={entry['region_ref']}: {bool(region)}")

    if not region:
        print("[WARN] Entry has no usable match_region; skipping ROI checks")
    else:
        x, y, w, h = region["x"], region["y"], region["w"], region["h"]
        in_bounds = (0 <= x < W) and (0 <= y < H) and (x + w <= W) and (y + h <= H)
        print(f"[INFO] Region: (x={x}, y={y}, w={w}, h={h}) → br=({x+w},{y+h}) | In-bounds={in_bounds}")

        if in_bounds:
            roi = screen[y:y+h, x:x+w]
            print(f"[INFO] ROI shape: {roi.shape}")
            if not args.no_roi_files:
                os.makedirs("debug", exist_ok=True)
                cv2.imwrite("debug/debug_roi.png", roi)
                dbg = screen.copy()
                cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.imwrite("debug/debug_overlay.png", dbg)
                print("[INFO] Wrote debug/debug_roi.png and debug/debug_overlay.png")

    # 3) Normal match via matcher (uses padding & threshold from entry)
    try:
        pt, conf = _match_entry(screen, entry, template_dir=args.template_dir)
        print(f"[INFO] _match_entry result: pt={pt}, conf={conf:.3f}")
    except Exception as e:
        print(f"[ERROR] _match_entry raised: {repr(e)}")
        sys.exit(3)

    # 4) Fullscreen probe (bypass region entirely) to separate region vs. scale issues
    fs_entry = {
        "match_template": entry.get("match_template"),
        "match_region": {"x": 0, "y": 0, "w": W, "h": H},
        "match_threshold": float(entry.get("match_threshold", 0.9)),
        "match_padding": 0,
    }
    try:
        fs_pt, fs_conf = _match_entry(screen, fs_entry, template_dir=args.template_dir)
        print(f"[INFO] Fullscreen probe: pt={fs_pt}, conf={fs_conf:.3f}")
    except Exception as e:
        print(f"[ERROR] Fullscreen probe raised: {repr(e)}")

    # 5) Optional multiscale probe inside the (author) region (if in-bounds)
    if args.multiscale_probe:  # hyphen not allowed in attr; fix next line
        pass

    if region and (0 <= x < W) and (0 <= y < H) and (x + w <= W) and (y + h <= H) and (not args.no_roi_files):
        tpl_path = os.path.join(args.template_dir, entry["match_template"])
        if os.path.exists(tpl_path):
            # Build ROI again
            roi = screen[y:y+h, x:x+w]
            # Optional multi-scale sweep to suggest a good scale
            scales = np.linspace(0.6, 1.6, 17) if "--multiscale-probe" in sys.argv else [1.0]
            if len(scales) > 1:
                conf, scale, loc = multiscale_probe(roi, tpl_path, scales)
                print(f"[INFO] Multiscale best: conf={conf:.3f} at scale={scale}")
        else:
            print(f"[WARN] Template path missing: {tpl_path}")

if __name__ == "__main__":
    main()
