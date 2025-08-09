#!/usr/bin/env python3
# core/wave_detector.py

import os
import re
from typing import Optional, Tuple

import cv2
import numpy as np

from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import resolve_dot_path, get_clickmap

# Optional: use pytesseract if available
try:
    import pytesseract
    _HAS_TESS = True
except Exception:
    _HAS_TESS = False


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


def _preprocess_for_digits(crop_bgr: np.ndarray) -> np.ndarray:
    """
    Convert to a high-contrast binary image that favors OCR of white/bright digits on
    dark/colored UI (or vice-versa). Tweak if needed.
    """
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)

    # Contrast boosting helps OCR a lot
    gray = cv2.convertScaleAbs(gray, alpha=1.6, beta=0)

    # Adaptive threshold is robust to gradients/glows; invert so digits are dark on white if that helps
    # Try both paths; keep the one with more black pixels to bias toward clearer glyphs
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY, 31, 5)
    thr_inv = cv2.bitwise_not(thr)

    # Heuristic pick: choose the version with fewer connected components noise if you want,
    # but a simple pixel-count heuristic is usually good enough:
    choose_inv = (np.count_nonzero(thr_inv == 0) > np.count_nonzero(thr == 0))
    bin_img = thr_inv if choose_inv else thr

    # Gentle dilation to close gaps in thin fonts
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    bin_img = cv2.morphologyEx(bin_img, cv2.MORPH_CLOSE, kernel, iterations=1)

    return bin_img


def _ocr_digits_tesseract(bin_img: np.ndarray) -> Tuple[Optional[int], float, str]:
    """
    OCR using pytesseract, restricted to digits. Returns (value, conf, raw_text).
    conf is average over digit symbols; -1 if unknown.
    """
    if not _HAS_TESS:
        return None, -1.0, ""

    # Tesseract works on RGB; bin_img is single-channel
    rgb = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2RGB)

    # psm 7: single text line (often best for UI counters)
    config = r"--psm 7 -c tessedit_char_whitelist=0123456789"
    data = pytesseract.image_to_data(rgb, config=config, output_type=pytesseract.Output.DICT)

    # Concatenate digits and compute average conf across digit tokens
    digits = []
    confs = []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        if txt and re.fullmatch(r"\d+", txt):
            digits.append(txt)
            try:
                c = float(conf)
                if c >= 0:
                    confs.append(c)
            except Exception:
                pass

    raw_text = "".join(digits)
    if not raw_text:
        return None, -1.0, ""

    try:
        value = int(raw_text)
    except ValueError:
        return None, -1.0, raw_text

    avg_conf = float(np.mean(confs)) if confs else -1.0
    return value, avg_conf, raw_text


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
    bin_img = _preprocess_for_digits(crop)

    if debug_out:
        cv2.imwrite(debug_out, bin_img)

    value, conf, _raw = _ocr_digits_tesseract(bin_img)
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


if __name__ == "__main__":
    # Simple CLI for manual testing
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
