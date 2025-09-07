#!/usr/bin/env python3
# utils/coin_detector.py

from __future__ import annotations
from typing import Optional, Tuple
from decimal import Decimal, getcontext
import re

import cv2
import numpy as np

from core.clickmap_access import resolve_dot_path, get_clickmap
from utils.ocr_utils import preprocess_binary

# Use enough precision for big idle numbers
getcontext().prec = 28

# Compact suffix multipliers (case-insensitive)
_SUFFIX = {
    "K": Decimal("1e3"),
    "M": Decimal("1e6"),
    "B": Decimal("1e9"),
    "T": Decimal("1e12"),
    "q": Decimal("1e15"),   # some UIs use 'q' or 'Q' for quadrillion
    "Q": Decimal("1e15"),
    # Add more if the game goes beyond (e.g., 's' for sextillion, etc.)
}

_ALLOWED_CHARS_RE = re.compile(r"[0-9\.\,\s\$\w]+")

def _get_bbox(dot_path: str) -> Tuple[int, int, int, int]:
    cm = get_clickmap()
    entry = resolve_dot_path(dot_path, cm)
    if not entry or "match_region" not in entry:
        raise KeyError(f"Missing match_region at dot_path: {dot_path}")
    r = entry["match_region"]
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])

def _crop(img, bbox):
    x, y, w, h = bbox
    H, W = img.shape[:2]
    return img[max(0,y):min(y+h,H), max(0,x):min(x+w,W)]

def parse_compact_number(text: str) -> Optional[Decimal]:
    """
    Parse strings like: "$862.28M", "862.28M", "862,280,000", "3.43T", "3.43 Q"
    Returns Decimal or None if parse fails.
    """
    if not text:
        return None

    # Keep only allowed characters, normalize spaces/commas
    s = "".join(ch for ch in text if _ALLOWED_CHARS_RE.match(ch))
    s = s.replace(",", "").replace("$", "").strip()

    # Extract number and optional suffix
    # Examples: "862.28M", "3.43 T", "203.43T"
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z])?$", s)
    if not m:
        return None

    num_s = m.group(1)
    suf = m.group(2) if m.group(2) is not None else ""
    try:
        base = Decimal(num_s)
    except Exception:
        return None

    if not suf:
        return base
    mult = _SUFFIX.get(suf, None)
    if mult is None:
        # Unknown suffix -> treat as plain number
        return base
    return base * mult

def format_compact_decimal(value: Decimal) -> str:
    """
    Format a Decimal back into a compact string with suffix.
    """
    if value is None:
        return "—"
    abs_val = value.copy_abs()
    for suf, mult in [("Q", Decimal("1e15")), ("T", Decimal("1e12")), ("B", Decimal("1e9")), ("M", Decimal("1e6")), ("K", Decimal("1e3"))]:
        if abs_val >= mult:
            out = (value / mult).quantize(Decimal("0.01"))
            return f"${out}{suf}"
    out = value.quantize(Decimal("0.01"))
    return f"${out}"

def _ocr_coins_bin(bin_img) -> Tuple[Optional[Decimal], float, str]:
    """
    Use Tesseract word boxes to get a reasonable confidence and join tokens.
    We avoid strict whitelists so unit letters (M/B/T) survive.
    """
    try:
        import pytesseract
    except Exception:
        return None, -1.0, ""

    rgb = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2RGB)
    data = pytesseract.image_to_data(rgb, config="--psm 7", output_type=pytesseract.Output.DICT)

    toks = data.get("text", [])
    confs = data.get("conf", [])
    # Keep tokens that contain allowed chars (digits, letters for suffix)
    kept = []
    kept_conf = []
    for t, c in zip(toks, confs):
        if not t:
            continue
        if _ALLOWED_CHARS_RE.fullmatch(t):
            kept.append(t)
            try:
                fc = float(c)
                if fc >= 0:
                    kept_conf.append(fc)
            except Exception:
                pass

    raw = " ".join(kept).strip()
    value = parse_compact_number(raw)
    avg_conf = float(np.mean(kept_conf)) if kept_conf else -1.0
    return value, avg_conf, raw

def get_coins_from_image(img_bgr,
                         dot_path: str = "_shared_match_regions.coins_text",
                         debug_out: Optional[str] = None) -> Tuple[Optional[Decimal], float]:
    """
    Crop the coin region from the given image and OCR it into a Decimal.
    Returns (value, avg_conf). avg_conf = -1.0 if unavailable.
    """
    bbox = _get_bbox(dot_path)
    crop = _crop(img_bgr, bbox)

    # Reuse your OCR preprocessing
    bin_img = preprocess_binary(crop, alpha=1.6, block=31, C=5, close=(2, 2), invert=False, choose_best=True)

    if debug_out:
        cv2.imwrite(debug_out, bin_img)

    val, conf, _raw = _ocr_coins_bin(bin_img)
    return val, conf
