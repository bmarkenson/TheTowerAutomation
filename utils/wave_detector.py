#!/usr/bin/env python3
# utils/wave_detector.py

from typing import Optional, Tuple

import cv2
import numpy as np

from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import resolve_dot_path, get_clickmap
from utils.ocr_utils import preprocess_binary, ocr_digits


def _get_wave_region_bbox(dot_path: str = "_shared_match_regions.wave_number") -> Tuple[int, int, int, int]:
    """
    Returns (x, y, w, h) from clickmap for the wave-number region.
    Raises if missing or malformed.
    """
    cm = get_clickmap()
    entry = resolve_dot_path(dot_path, cm)
    if not entry or "match_region" not in entry:
        raise KeyError(f"Missing match_region at dot_path: {dot_path}")

    r = entry["match_region"]
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])


def _crop_region(img: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = bbox
    H, W = img.shape[:2]
    x2, y2 = min(x + w, W), min(y + h, H)
    x1, y1 = max(0, x), max(0, y)
    if x1 >= x2 or y1 >= y2:
        raise ValueError(f"Invalid crop bbox after clamping: {x1,y1,x2,y2}")
    return img[y1:y2, x1:x2]


def detect_wave_number_from_image(img_bgr: np.ndarray,
                                  dot_path: str = "_shared_match_regions.wave_number",
                                  debug_out: Optional[str] = None) -> Tuple[Optional[int], float]:
    """
    Core detector: crops the configured region and OCRs the wave number.
    Returns (wave_number, confidence). confidence is -1 if unavailable.
    Optionally writes preprocessed image if debug_out is provided (path).
    """
    bbox = _get_wave_region_bbox(dot_path)
    crop = _crop_region(img_bgr, bbox)

    # Shared OCR preprocessing: mirror previous behavior (alpha=1.6, block=31, C=5),
    # choose_best=True to auto-pick inverted vs normal, close=(2,2).
    bin_img = preprocess_binary(crop, alpha=1.6, block=31, C=5, close=(2, 2), invert=False, choose_best=True)

    if debug_out:
        cv2.imwrite(debug_out, bin_img)

    value, conf, _raw = ocr_digits(bin_img, psm=7)
    return value, conf


def detect_wave_number(dot_path: str = "_shared_match_regions.wave_number",
                       debug_out: Optional[str] = None) -> Tuple[Optional[int], float]:
    """
    Convenience entrypoint: captures a fresh screenshot via ADB and detects wave number.
    """
    img = capture_adb_screenshot()
    if img is None:
        raise RuntimeError("Failed to capture screenshot.")
    return detect_wave_number_from_image(img, dot_path=dot_path, debug_out=debug_out)

def get_wave_number(dot_path: str = "_shared_match_regions.wave_number"):
    """
    Minimal utility for workflows.
    Returns the wave number as int, or None if OCR failed.
    """
    val, _conf = detect_wave_number(dot_path=dot_path)
    return val


def main():
    """CLI: print detected wave number and confidence; supports --dot-path and --debug-out."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot-path", default="_shared_match_regions.wave_number")
    parser.add_argument("--debug-out", default=None, help="Write preprocessed crop to this path")
    args = parser.parse_args()

    val, conf = detect_wave_number(dot_path=args.dot_path, debug_out=args.debug_out)
    if val is None:
        print("Wave number: <not detected>")
    else:
        print(f"Wave number: {val} (conf={conf:.1f})")


if __name__ == "__main__":
    main()
