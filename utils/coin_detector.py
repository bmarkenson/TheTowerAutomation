#!/usr/bin/env python3
# utils/coin_detector.py

from __future__ import annotations
from typing import List, Optional, Tuple
from decimal import Decimal, getcontext
import re

import cv2
import numpy as np

from core.clickmap_access import resolve_dot_path, get_clickmap
from core.battle_stats import format_tower_number, parse_tower_number
from utils.ocr_utils import ocr_text_and_conf

# Use enough precision for big idle numbers
getcontext().prec = 28

_SUFFIX_PATTERN = r"(?:[KMBTqQsSOND]|[a-z]{2})"

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
    # Drop trailing '/min' or 'min' (tolerate '/m' and '/mi'). Requiring the
    # slash for abbreviated forms prevents an uppercase M magnitude suffix
    # from being mistaken for the start of "min".
    s = re.sub(
        r"(?:/\s*m(?:i(?:n)?)?|\bmin)\b.*$",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # Extract all occurrences of number plus an optional Tower suffix. Suffix
    # case is significant: q/Q and s/S are different magnitudes.
    matches = list(
        re.finditer(
            rf"([0-9]+(?:\.[0-9]+)?)\s*({_SUFFIX_PATTERN})?",
            s,
        )
    )
    if not matches:
        return None

    # Choose best: prefer longer numeric token; tie-break by a known suffix.
    best_num, best_suf = None, ""
    for m in matches:
        num_s = m.group(1)
        suf = (m.group(2) or "").strip()
        if (
            best_num is None
            or len(num_s) > len(best_num)
            or (
                len(num_s) == len(best_num)
                and bool(suf)
                and not best_suf
            )
        ):
            best_num, best_suf = num_s, suf

    return parse_tower_number(f"{best_num}{best_suf}")

def format_compact_decimal(value: Decimal) -> str:
    """
    Format a Decimal into a compact string with suffix, keeping up to 2 decimals
    but dropping trailing ".00" (e.g., 862.28M, 1T, 987.5K, 123).
    """
    return "—" if value is None else format_tower_number(value)


def is_coin_token(token: str) -> bool:
    return bool(_ALLOWED_CHARS_RE.fullmatch(token))


def _has_coin_suffix(raw: str, tokens: List[str]) -> bool:
    if raw and re.search(
        rf"([0-9]+(?:\.[0-9]+)?)\s*{_SUFFIX_PATTERN}(?=\s*(?:/|$))",
        raw,
    ):
        return True
    for tok in tokens:
        norm = tok.strip().replace("/", "")
        if re.fullmatch(_SUFFIX_PATTERN, norm):
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
