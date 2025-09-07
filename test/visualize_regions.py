#!/usr/bin/env python3
"""
Generic clickmap/region visualizer with optional detector overlays.

Features:
- Load an image (or capture via ADB) and draw:
  * Shared regions from _shared_match_regions (e.g., floating_buttons)
  * Entries by role (e.g., --roles floating_button,indicator)
  * Arbitrary clickmap dot paths (--dot-paths indicators.attack_menu ...)
  * All entries under a clickmap namespace (--namespace floating_buttons)
- Optional floating-button detector overlay (scores + best boxes)
- JSON dump of boxes for downstream tooling

Usage examples:
  # Visualize shared floating_buttons slice and draw floating_button entries
  test/visualize_regions.py --image screenshots/latest.png \
    --shared floating_buttons --roles floating_button --out out/regions.png

  # Capture via ADB, run floating button detector, write heatmaps
  test/visualize_regions.py --adb --shared floating_buttons --roles floating_button \
    --detector floating_buttons --heatmaps --out out/fb_overlay.png

  # Visualize arbitrary dot paths
  test/visualize_regions.py --image screenshots/latest.png \
    --dot-paths "floating_buttons.missile_barrage" "indicators.wall_icon" \
    --out out/selected_boxes.png
"""

import argparse, os, sys, json, pathlib
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from utils.logger import log
from core.clickmap_access import get_clickmap, resolve_dot_path, get_entries_by_role
from core.label_tapper import resolve_region
from core.ss_capture import capture_adb_screenshot, capture_and_save_screenshot

# Optional detector imports (kept local to avoid import cycles when unused)
def _import_detector(name: str):
    if name == "floating_buttons":
        from core.floating_button_detector import detect_floating_buttons
        return detect_floating_buttons
    raise ValueError(f"Unknown detector: {name}")

def _heatmap(res):
    r = cv2.normalize(res, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")
    return cv2.applyColorMap(r, cv2.COLORMAP_JET)

def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def _add_box(group, name, region, boxes):
    try:
        x,y,w,h = int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])
        boxes.setdefault(group, []).append({"name": name, "x": x, "y": y, "w": w, "h": h})
    except Exception as e:
        log(f"Bad region for {group}:{name} -> {region} ({e})", "WARN")

def collect_boxes(clickmap, shared_names, roles, dot_paths, namespace):
    """Return dict[group] -> [ {name,x,y,w,h}, ... ]"""
    boxes = {}

    # Shared regions
    shared = (clickmap.get("_shared_match_regions") or {})
    for s in shared_names or []:
        reg = (shared.get(s) or {}).get("match_region")
        if reg:
            _add_box("shared", s, reg, boxes)
        else:
            log(f"Shared region '{s}' missing in _shared_match_regions", "WARN")

    # Roles
    for role in roles or []:
        entries = get_entries_by_role(role)
        for name, entry in entries.items():
            try:
                r = resolve_region(entry, clickmap)
                _add_box(f"role:{role}", name, r, boxes)
            except Exception as e:
                log(f"resolve_region failed for role:{role} {name}: {e}", "WARN")

    # Dot paths
    for dp in dot_paths or []:
        obj = resolve_dot_path(dp)
        if isinstance(obj, dict):
            # If this is a clickmap entry, resolve its region; else if it's a region, draw it directly
            if "match_region" in obj or "region_ref" in obj:
                try:
                    r = resolve_region(obj, clickmap)
                    _add_box("dot", dp, r, boxes)
                except Exception as e:
                    log(f"resolve_region failed for dot:{dp}: {e}", "WARN")
            elif all(k in obj for k in ("x","y","w","h")):
                _add_box("dot", dp, obj, boxes)
            else:
                log(f"dot-path '{dp}' does not resolve to a region or entry", "WARN")
        else:
            log(f"dot-path '{dp}' not found", "WARN")

    # Namespace: draw everything under a top-level clickmap key (e.g., floating_buttons)
    # Assumes entries under namespace are entry dicts with region info or refs.
    if namespace:
        ns = clickmap.get(namespace, {})
        if not isinstance(ns, dict):
            log(f"Namespace '{namespace}' not found or not a dict", "WARN")
        else:
            for k, entry in ns.items():
                if isinstance(entry, dict) and ('match_region' in entry or 'region_ref' in entry or 'match_template' in entry):
                    try:
                        r = resolve_region(entry, clickmap)
                        _add_box(f"ns:{namespace}", f"{namespace}.{k}", r, boxes)
                    except Exception as e:
                        log(f"resolve_region failed for ns:{namespace}.{k}: {e}", "WARN")

    return boxes

def draw_overlay(img, groups, scale=1.0, thickness=2):
    out = img.copy()
    # deterministic colors per group
    def _color(name):
        h = abs(hash(name)) % (1<<24)
        return (h & 255, (h>>8) & 255, (h>>16) & 255)
    for group_name, boxes in groups:
        color = _color(group_name)
        for b in boxes:
            p1 = (b["x"], b["y"])
            p2 = (b["x"] + b["w"], b["y"] + b["h"])
            cv2.rectangle(out, p1, p2, color, thickness)
            label = f'{group_name}:{b["name"]}'
            cv2.putText(out, label, (b["x"]+2, max(0, b["y"]-6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    if scale != 1.0:
        h, w = out.shape[:2]
        out = cv2.resize(out, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_AREA)
    return out

def run_floating_button_scoring(img, clickmap, out_dir, thresh_bump=0.0, draw_best=True, heatmaps=False):
    # Enforce shared slice for scoring to reflect intended behavior
    shared = (clickmap.get("_shared_match_regions") or {}).get("floating_buttons", {})
    reg = shared.get("match_region")
    if not reg:
        log("Shared region for floating_buttons missing", "ERROR"); return []
    xs, ys, ws, hs = [int(reg[k]) for k in ("x","y","w","h")]
    roi = img[ys:ys+hs, xs:xs+ws].copy()

    entries = get_entries_by_role("floating_button")
    results = []
    for name, e in entries.items():
        t_rel = e.get("match_template")
        if not t_rel:
            continue
        t_path = os.path.join(REPO, "assets", "match_templates", t_rel)
        templ = cv2.imread(t_path, cv2.IMREAD_COLOR)
        if templ is None:
            log(f"Missing template: {t_rel}", "ERROR")
            continue
        res = cv2.matchTemplate(roi, templ, cv2.TM_CCOEFF_NORMED)
        _, maxVal, _, maxLoc = cv2.minMaxLoc(res)
        thr = float(e.get("match_threshold", 0.9)) - float(thresh_bump)
        thr = max(0.0, min(1.0, thr))
        th_, tw_ = templ.shape[:2]
        if draw_best:
            tl = (maxLoc[0], maxLoc[1]); br = (tl[0]+tw_, tl[1]+th_)
            cv2.rectangle(roi, tl, br, (0,200,0) if maxVal>=thr else (0,0,255), 2)
            cv2.putText(roi, f"{name.split('.')[-1]}:{maxVal:.2f}",
                        (tl[0]+2, max(0, tl[1]-6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0,200,0) if maxVal>=thr else (0,0,255), 1, cv2.LINE_AA)
        if heatmaps:
            hm = _heatmap(res)
            cv2.imwrite(os.path.join(out_dir, f"fb_{name.split('.')[-1]}_heatmap.png"), hm)
        results.append({"name": name, "max": float(maxVal), "thr": float(thr), "template_wh": [int(tw_), int(th_)]})
    # paste back annotated ROI for context (caller draws the outer box via groups)
    return results, (xs, ys, ws, hs), roi

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb", action="store_true", help="Capture via ADB before processing")
    ap.add_argument("--image", default="screenshots/latest.png", help="Input image path if not using --adb")
    ap.add_argument("--save-capture", default="screenshots/latest.png", help="Where to save the ADB capture")
    ap.add_argument("--out", default="out/regions_overlay.png", help="Output overlay image")
    ap.add_argument("--dump", default=None, help="Optional JSON dump of drawn boxes")
    ap.add_argument("--scale", type=float, default=1.0, help="Overlay scale")
    ap.add_argument("--thickness", type=int, default=2, help="Box line thickness")

    # What to draw
    ap.add_argument("--shared", nargs="*", default=[], help="Names from _shared_match_regions (e.g., floating_buttons coins)")
    ap.add_argument("--roles", default="", help="Comma-separated roles (e.g., floating_button,indicator)")
    ap.add_argument("--dot-paths", nargs="*", default=[], help="Arbitrary clickmap dot paths to draw")
    ap.add_argument("--namespace", default="", help="Draw all entries under a clickmap namespace (e.g., floating_buttons)")

    # Optional detector overlay and scoring
    ap.add_argument("--detector", default="", help="Detector name to run (e.g., floating_buttons)")
    ap.add_argument("--heatmaps", action="store_true", help="Also emit heatmaps for detector scoring")
    ap.add_argument("--thresh-bump", type=float, default=0.0, help="Subtract from match_threshold (e.g., 0.05)")

    args = ap.parse_args()
    _ensure_dir(args.out)
    if args.dump: _ensure_dir(args.dump)

    # Load or capture image
    if args.adb:
        img = capture_adb_screenshot()
        if img is None:
            log("ADB capture returned None", "ERROR"); sys.exit(2)
        if args.save_capture:
            _ensure_dir(args.save_capture)
            cv2.imwrite(args.save_capture, img)
            log(f"ADB capture saved: {args.save_capture}", "INFO")
    else:
        img = cv2.imread(args.image, cv2.IMREAD_COLOR)
        if img is None:
            log(f"Could not read image: {args.image}", "ERROR"); sys.exit(2)

    clickmap = get_clickmap()

    # Collect requested boxes
    roles = [r.strip() for r in args.roles.split(",") if r.strip()] if args.roles else []
    boxes_by_group = collect_boxes(clickmap, args.shared, roles, args.dot_paths, args.namespace)

    # If a detector was specified, run it and augment overlay
    detector_results = None
    annotated_roi = None
    if args.detector:
        det = _import_detector(args.detector)
        out_dir = os.path.dirname(args.out) or "."
        # For floating_buttons, score inside shared slice and annotate ROI with best boxes & scores
        if args.detector == "floating_buttons":
            detector_results, shared_rect, roi_annot = run_floating_button_scoring(
                img, clickmap, out_dir, thresh_bump=args.thresh_bump,
                draw_best=True, heatmaps=args.heatmaps
            )
            if shared_rect:
                xs, ys, ws, hs = shared_rect
                # ensure the shared rect is drawn in groups (if not already requested)
                if not any(g for g in boxes_by_group if g == "shared"):
                    boxes_by_group.setdefault("shared", []).append({"name": "floating_buttons", "x": xs, "y": ys, "w": ws, "h": hs})
                # paste annotated ROI for context
                annotated_roi = (shared_rect, roi_annot)
        else:
            # Generic path: run detector and draw match boxes returned by detector
            matches = det(img) or []
            for m in matches:
                mr = m.get("match_region") or {}
                _add_box(f"det:{args.detector}", m.get("name","item"), mr, boxes_by_group)

    # Render
    groups_sorted = sorted(boxes_by_group.items(), key=lambda kv: kv[0])
    overlay = draw_overlay(img, groups_sorted, scale=1.0, thickness=args.thickness)

    # If we have an annotated ROI from detector scoring, composite it back (already colored)
    if annotated_roi:
        (xs, ys, ws, hs), roi_ann = annotated_roi
        overlay[ys:ys+hs, xs:xs+ws] = roi_ann

    # Scale and write
    if args.scale != 1.0:
        h, w = overlay.shape[:2]
        overlay = cv2.resize(overlay, (int(w*args.scale), int(h*args.scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(args.out, overlay)
    print(f"[OK] wrote {args.out}")

    # Dump JSON if requested
    if args.dump:
        payload = {
            "image": args.image if not args.adb else args.save_capture,
            "image_shape": img.shape,
            "groups": {name: boxes for name, boxes in groups_sorted},
            "detector": args.detector or None,
            "detector_results": detector_results,
        }
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"[OK] wrote {args.dump}")

if __name__ == "__main__":
    main()
