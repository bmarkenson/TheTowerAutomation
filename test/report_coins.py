#!/usr/bin/env python3
"""
Probe coins/min OCR on a single frame and dump rich debug artifacts.

Usage examples:
  # Run on the current screenshot and write debug to out/coins_probe
  test/report_coins.py --image screenshots/latest.png --out out/coins_probe

  # Capture via ADB, then probe
  test/report_coins.py --adb --save-capture screenshots/latest.png --out out/coins_probe

What it prints:
  - bbox used from clickmap (_shared_match_regions.coins)
  - For each binarization variant: raw OCR, confidence, parsed Decimal, pretty string, '/min' detected

What it saves:
  - overlay.png (ROI rectangle on the source)
  - crop.png (ROI crop)
  - bin_*.png (binarized variants)
"""

import argparse
import os
import json
import cv2
import numpy as np

from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import resolve_dot_path, get_clickmap
from utils.coin_detector import (
    parse_compact_number,
    format_compact_decimal,
)
from utils.ocr_utils import preprocess_binary


def _get_bbox(dot_path: str):
    cm = get_clickmap()
    entry = resolve_dot_path(dot_path, cm)
    if not entry or "match_region" not in entry:
        raise KeyError(f"Missing match_region for {dot_path}")
    r = entry["match_region"]
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])


def _white_mask(crop_bgr):
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 200), (180, 60, 255))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
    return mask


def _ocr_text_and_conf(bin_img):
    try:
        import pytesseract
    except Exception:
        return "", -1.0
    rgb = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2RGB)
    data = pytesseract.image_to_data(rgb, config="--psm 7", output_type=pytesseract.Output.DICT)
    toks = data.get("text", []) or []
    confs = data.get("conf", []) or []
    kept = []
    kconf = []
    for t, c in zip(toks, confs):
        if not t:
            continue
        kept.append(t)
        try:
            fc = float(c)
            if fc >= 0:
                kconf.append(fc)
        except Exception:
            pass
    txt = " ".join(kept).strip()
    avg_conf = float(np.mean(kconf)) if kconf else -1.0
    return txt, avg_conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="screenshots/latest.png")
    ap.add_argument("--adb", action="store_true", help="Capture via ADB before processing")
    ap.add_argument("--save-capture", default=None, help="If using --adb, save the captured frame here")
    ap.add_argument("--dot-path", default="_shared_match_regions.coins")
    ap.add_argument("--out", default="out/coins_probe")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.adb:
        img = capture_adb_screenshot()
        if img is None:
            print(json.dumps({"error": "adb capture failed"}))
            return 2
        if args.save_capture:
            os.makedirs(os.path.dirname(args.save_capture), exist_ok=True)
            cv2.imwrite(args.save_capture, img)
    else:
        img = cv2.imread(args.image)
        if img is None:
            print(json.dumps({"error": f"failed to read image: {args.image}"}))
            return 2

    x, y, w, h = _get_bbox(args.dot_path)
    crop = img[y:y+h, x:x+w].copy()
    overlay = img.copy()
    cv2.rectangle(overlay, (x, y), (x+w, y+h), (0, 0, 255), 2)
    cv2.imwrite(os.path.join(args.out, "overlay.png"), overlay)
    cv2.imwrite(os.path.join(args.out, "crop.png"), crop)

    variants = []
    # A) white mask
    try:
        wm = _white_mask(crop)
        variants.append(("white_mask", wm))
    except Exception:
        pass
    # B) preprocess_binary choose_best
    pb = preprocess_binary(crop, choose_best=True)
    variants.append(("preprocess_binary", pb))
    # C) preprocess invert=True
    pbi = preprocess_binary(crop, invert=True)
    variants.append(("preprocess_binary_inv", pbi))

    results = []
    for name, bimg in variants:
        path = os.path.join(args.out, f"bin_{name}.png")
        cv2.imwrite(path, bimg)
        raw, conf = _ocr_text_and_conf(bimg)
        parsed = parse_compact_number(raw)
        pretty = format_compact_decimal(parsed) if parsed is not None else "—"
        import re
        has_min = bool(re.search(r"/\s*m(?:in)?", (raw or "").lower()))
        results.append({
            "variant": name,
            "raw": raw,
            "conf": round(conf, 1),
            "parsed": str(parsed) if parsed is not None else None,
            "pretty": pretty,
            "has_min": has_min,
            "bin_path": path,
        })

    print(json.dumps({
        "dot_path": args.dot_path,
        "bbox": {"x": x, "y": y, "w": w, "h": h},
        "out_dir": args.out,
        "results": results,
    }, indent=2))
    # Also print a simple status line similar to main
    chosen = None
    for r in results:
        if r.get("parsed") and r.get("has_min"):
            chosen = r
            break
    if not chosen:
        # fallback: first with parsed
        for r in results:
            if r.get("parsed"):
                chosen = r
                break
    pretty = chosen.get("pretty") if chosen else "—"
    print(f"Coins / min = {pretty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
