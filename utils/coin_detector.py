#!/usr/bin/env python3
# utils/coin_detector.py

from __future__ import annotations
from typing import List, Optional, Tuple
from decimal import Decimal, getcontext
import re

import cv2
import numpy as np

from core.clickmap_access import resolve_dot_path, get_clickmap
from utils.ocr_utils import ocr_text_and_conf

# Use enough precision for big idle numbers
getcontext().prec = 28

# Compact suffix multipliers (case-insensitive)
_SUFFIX = {
    "K": Decimal("1e3"),
    "M": Decimal("1e6"),
    "B": Decimal("1e9"),
    "T": Decimal("1e12"),
    "q": Decimal("1e15"),   # quadrillion
    "Q": Decimal("1e18"),   # quintillion
    # Extend as the game introduces larger magnitudes (e.g., sextillion)
}

# Allow per-character filter to keep '/', so '/min' survives until we strip it
_ALLOWED_CHARS_RE = re.compile(r"[0-9\.\,\s\$\w/]+")

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

def _white_mask(crop_bgr: np.ndarray) -> np.ndarray:
    """Isolate bright, low-saturation digits on dark header background."""
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 200), (180, 60, 255))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
    return mask

def parse_compact_number(text: str) -> Optional[Decimal]:
    """
    Parse strings like: "$862.28M", "862.28M", "862,280,000", "3.43T", "3.43 Q"
    Robust to OCR artifacts where "/min" becomes "min" or partially truncated ("/mi").
    Also scans the whole string and chooses the best numeric token with optional suffix.

    Returns Decimal or None if parse fails.
    """
    if not text:
        return None

    # Keep only allowed characters, normalize spaces/commas
    s = "".join(ch for ch in text if _ALLOWED_CHARS_RE.match(ch))
    s = s.replace(",", "").replace("$", "").strip()
    # Drop leading currency/label letters like 'C' or 'Coins'
    s = re.sub(r"^[A-Za-z]+\s*", "", s)
    # Drop trailing '/min' or 'min' (with or without slash; tolerate '/mi')
    s = re.sub(r"(?:/\s*)?m(?:in)?\b.*$", "", s, flags=re.IGNORECASE)

    # Extract all occurrences of number + optional one-letter suffix anywhere in the string
    matches = list(re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z])?", s))
    if not matches:
        return None

    # Choose best: prefer longer numeric token; tie-break by known suffix (K/M/B/T/Q/q)
    best_num, best_suf = None, ""
    for m in matches:
        num_s = m.group(1)
        suf = (m.group(2) or "").strip()
        if (
            best_num is None
            or len(num_s) > len(best_num)
            or (len(num_s) == len(best_num) and bool(_SUFFIX.get(suf)) and not _SUFFIX.get(best_suf))
        ):
            best_num, best_suf = num_s, suf

    try:
        base = Decimal(best_num)
    except Exception:
        return None

    if not best_suf:
        return base
    mult = _SUFFIX.get(best_suf)
    return base if mult is None else base * mult

def format_compact_decimal(value: Decimal) -> str:
    """
    Format a Decimal into a compact string with suffix, keeping up to 2 decimals
    but dropping trailing ".00" (e.g., 862.28M, 1T, 987.5K, 123).
    """
    if value is None:
        return "—"

    def _fmt_2dp_trim(d: Decimal) -> str:
        s = str(d.quantize(Decimal("0.01")))
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    abs_val = value.copy_abs()
    for suf, mult in [
        ("Q", Decimal("1e18")),
        ("q", Decimal("1e15")),
        ("T", Decimal("1e12")),
        ("B", Decimal("1e9")),
        ("M", Decimal("1e6")),
        ("K", Decimal("1e3")),
    ]:
        if abs_val >= mult:
            out = (value / mult)
            return f"{_fmt_2dp_trim(out)}{suf}"
    return _fmt_2dp_trim(value)

_SUFFIX_KEYS = {k.upper() for k in _SUFFIX.keys()}


def is_coin_token(token: str) -> bool:
    return bool(_ALLOWED_CHARS_RE.fullmatch(token))


def _has_coin_suffix(raw: str, tokens: List[str]) -> bool:
    if raw and re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmbtq])\b", raw, re.IGNORECASE):
        return True
    for tok in tokens:
        norm = tok.strip().replace("/", "")
        if len(norm) == 1 and norm.upper() in _SUFFIX_KEYS:
            return True
    return False


def _ocr_coins_bin(bin_img) -> Tuple[Optional[Decimal], float, str, bool]:
    """Run OCR on a binarized coin crop and parse to Decimal."""
    raw, avg_conf, tokens, _ = ocr_text_and_conf(
        bin_img,
        psm=7,
        token_filter=is_coin_token,
        return_tokens=True,
    )
    value = parse_compact_number(raw)
    has_suffix = _has_coin_suffix(raw, tokens)
    return value, avg_conf, raw, has_suffix

def detect_coins_from_image(img_bgr,
                            dot_path: str = "_shared_match_regions.coins",
                            debug_out: Optional[str] = None) -> Tuple[Optional[Decimal], float, bool]:
    """
    OCR coins/min using a robust white-mask binarization.
    Returns (Decimal value, confidence, has_min_token).

    Strategy: white mask at 1.0x and 1.8x; choose the candidate that parses,
    preferring one that contains '/m(in)?'. Tie-break by confidence.
    """
    bbox = _get_bbox(dot_path)
    crop = _crop(img_bgr, bbox)

    candidates: list[Tuple[Optional[Decimal], float, str, np.ndarray, bool]] = []
    try:
        wm = _white_mask(crop)
        v, c, raw, suf = _ocr_coins_bin(wm)
        candidates.append((v, c, raw or "", wm, suf))
        # upscale variant for clarity
        up = cv2.resize(wm, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
        v2, c2, raw2, suf2 = _ocr_coins_bin(up)
        candidates.append((v2, c2, raw2 or "", up, suf2))
        # slight dilation to reconnect thin strokes (low-risk extra candidate)
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        wm_dil = cv2.dilate(wm, k, iterations=1)
        v3, c3, raw3, suf3 = _ocr_coins_bin(wm_dil)
        candidates.append((v3, c3, raw3 or "", wm_dil, suf3))
    except Exception:
        pass

    if not candidates:
        return None, -1.0, False

    def has_min(raw: str) -> bool:
        return bool(re.search(r"/\s*m(?:in)?", (raw or "").lower()))

    # Pick best: require parsed value; prefer one with '/min'; tie-break by conf
    valid = [(v, c, raw, bimg, suf) for (v, c, raw, bimg, suf) in candidates if v is not None]
    if not valid:
        # fall back to best confidence raw even if parse failed
        best = max(candidates, key=lambda t: (has_min(t[2]), t[4], t[1]))
        return best[0], best[1], has_min(best[2])

    best = max(valid, key=lambda t: (has_min(t[2]), t[4], t[1]))

    # Optionally save the chosen bin image
    if debug_out and isinstance(best[3], np.ndarray):
        cv2.imwrite(debug_out, best[3])

    return best[0], best[1], has_min(best[2])


def get_coins_from_image(img_bgr,
                         dot_path: str = "_shared_match_regions.coins",
                         debug_out: Optional[str] = None) -> Tuple[Optional[Decimal], float]:
    """Back-compat wrapper: returns (value, confidence)."""
    val, conf, _has_min = detect_coins_from_image(img_bgr, dot_path=dot_path, debug_out=debug_out)
    return val, conf
