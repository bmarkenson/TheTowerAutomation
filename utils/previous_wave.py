#!/usr/bin/env python3
# core/previous_wave.py

import os
import re
import glob
from datetime import datetime
from typing import Optional, Tuple

import cv2
import numpy as np

_CUR_LINE_RE = re.compile(r'^\s*Wave\s+(\d{1,6})\b', re.IGNORECASE | re.MULTILINE)
_HI_LINE_RE  = re.compile(r'^\s*Highest\s*Wave[: ]+\s*(\d{1,6})\b', re.IGNORECASE | re.MULTILINE)

# Optional OCR backend — same pattern you used before
try:
    import pytesseract
    _HAS_TESS = True
except Exception:
    _HAS_TESS = False


# ---------- File selection ----------

_TS_RE = re.compile(r"Game(\d{8})_(\d{4})_game_stats\.png$")  # GameYYYYMMDD_HHMM_game_stats.png

def _parse_ts_from_name(path: str) -> Optional[datetime]:
    m = _TS_RE.search(os.path.basename(path))
    if not m:
        return None
    ymd, hm = m.groups()
    try:
        return datetime.strptime(ymd + hm, "%Y%m%d%H%M")
    except ValueError:
        return None

def _latest_game_stats_image(matches_dir: str = "screenshots/matches") -> Optional[str]:
    candidates = glob.glob(os.path.join(matches_dir, "Game*_game_stats.png"))
    if not candidates:
        return None
    # Prefer filename timestamp; fallback to mtime
    with_ts = [(p, _parse_ts_from_name(p)) for p in candidates]
    with_ts.sort(key=lambda t: (t[1] is None, t[1] or datetime.fromtimestamp(os.path.getmtime(t[0]))), reverse=True)
    return with_ts[0][0]


# ---------- OCR ----------

def _preprocess_game_stats(img_bgr: np.ndarray) -> np.ndarray:
    """Boost contrast and binarize to help OCR 'Wave 1234' text."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.6, beta=0)
    # Adaptive threshold, then invert so text tends to be dark on white
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY, 35, 7)
    bin_img = cv2.bitwise_not(thr)
    # Light morphological close to connect strokes
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    bin_img = cv2.morphologyEx(bin_img, cv2.MORPH_CLOSE, kernel, iterations=1)
    return bin_img

def _ocr_text(bin_img: np.ndarray) -> str:
    if not _HAS_TESS:
        return ""
    rgb = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2RGB)
    # psm 6 = assume a block of text; allowing letters + digits
    cfg = r"--psm 6"
    return pytesseract.image_to_string(rgb, config=cfg)

_WAVE_RE = re.compile(r"\bWave\s+(\d{1,6})\b", re.IGNORECASE)

def _extract_current_and_highest(text: str):
    cur = None
    hi = None

    m_cur = _CUR_LINE_RE.search(text)
    if m_cur:
        try: cur = int(m_cur.group(1))
        except ValueError: pass

    m_hi = _HI_LINE_RE.search(text)
    if m_hi:
        try: hi = int(m_hi.group(1))
        except ValueError: pass

    return cur, hi

# ---------- Public API ----------

def get_previous_run_wave(matches_dir: str = "screenshots/matches") -> Optional[int]:
    path = _latest_game_stats_image(matches_dir)
    if not path:
        return None
    img = cv2.imread(path)
    if img is None:
        return None

    bin_img = _preprocess_game_stats(img)
    text = _ocr_text(bin_img)

    cur, _hi = _extract_current_and_highest(text)
    return cur

if __name__ == "__main__":
    val = get_previous_run_wave()
    print("Previous run wave:", "<none>" if val is None else val)

