#!/usr/bin/env python3
# utils/previous_wave.py

import os
import re
import glob
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

from utils.ocr_utils import preprocess_binary, ocr_text

_CUR_LINE_RE = re.compile(r'^\s*Wave\s+(\d{1,6})\b', re.IGNORECASE | re.MULTILINE)
_HI_LINE_RE  = re.compile(r'^\s*Highest\s*Wave[: ]+\s*(\d{1,6})\b', re.IGNORECASE | re.MULTILINE)

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


# ---------- Parsing ----------

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
    """
    Load the latest 'GameYYYYMMDD_HHMM_game_stats.png' under matches_dir,
    OCR the text, and return the parsed current Wave number (or None on failure).
    """
    path = _latest_game_stats_image(matches_dir)
    if not path:
        return None
    img = cv2.imread(path)
    if img is None:
        return None

    # Mirror the original preprocessing behavior (invert + slightly larger block/C)
    bin_img = preprocess_binary(img, alpha=1.6, block=35, C=7, close=(2, 2), invert=True, choose_best=False)
    text = ocr_text(bin_img, psm=6)

    cur, _hi = _extract_current_and_highest(text)
    return cur


def main():
    """CLI: prints previous run's wave; supports --matches-dir."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches-dir", default="screenshots/matches", help="Directory containing Game*_game_stats.png")
    args = parser.parse_args()

    val = get_previous_run_wave(matches_dir=args.matches_dir)
    print("Previous run wave:", "<none>" if val is None else val)


if __name__ == "__main__":
    main()
