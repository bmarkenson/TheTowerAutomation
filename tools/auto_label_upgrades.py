#!/usr/bin/env python3
# tools/auto_label_upgrades.py
#
# Auto-generate upgrade label templates and clickmap entries by scanning the
# scrollable upgrades list. Template = FULL LEFT TILE up to—but not including—
# the small value panel on the right. Color-agnostic, adaptive scroll, review dump.

import os
import re
import time
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Dict, Optional

import cv2
import numpy as np

from utils.logger import log
from utils.ocr_utils import ocr_text, preprocess_binary
from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import resolve_dot_path, set_dot_path, save_clickmap, get_clickmap
from core.label_tapper import page_column  # adaptive scroll

# ------------------------- TUNABLES -------------------------------------------

LEFT_SHARED_KEY  = "_shared_match_regions.upgrades_left"
RIGHT_SHARED_KEY = "_shared_match_regions.upgrades_right"

POST_SWIPE_SLEEP = 0.35  # UI settle time after swipe (s)

# Expand detection ROI so rows near the edges aren't truncated
DETECT_PAD_PX = 24

# Cyan-ish border threshold (rows). Row borders are consistently neon/teal.
ROW_BORDER_HSV_LOWER = np.array([75,  80, 120], dtype=np.uint8)
ROW_BORDER_HSV_UPPER = np.array([110, 255, 255], dtype=np.uint8)

# Row filtering relative to column ROI size
MIN_ROW_AREA_FRAC = 0.06
MIN_ROW_ASPECT    = 1.6
MAX_ROW_ASPECT    = 6.0

# Right-panel detection (contours) — color-agnostic (edge-based); then
# fallback split-line (vertical gradient) if panel contour not found.
RIGHT_MIN_X_FRAC       = 0.52              # must be right half
RIGHT_AREA_FRAC_MINMAX = (0.03, 0.60)      # fraction of row area
RIGHT_ASPECT_MINMAX    = (0.30, 2.20)      # w/h, tall-ish rectangle

# Split-line search zone (relative to row width)
SPLIT_SEARCH_FRAC      = (0.50, 0.92)      # only look in right half
SPLIT_BOX_SMOOTH_PX    = 9                 # 1D smoothing window for energy
SPLIT_MIN_STD_MULT     = 0.6               # require peak > mean + k*std

# Insets & gap (pixels)
ROW_INSET_PX  = 10    # shave borders inside the row to avoid glow
LABEL_GAP_PX  = 6     # space between label crop and right panel / split

# Fallback label crop if neither panel nor split-line found
FALLBACK_LABEL_WIDTH_FRAC = 0.64

DEFAULT_THRESHOLD = 0.90
DEFAULT_ROLES     = ["upgrade_label"]
SIM_MATCH_THRESHOLD = 0.72  # fallback name inference via template similarity (tuned)

# Edge detection when paging
EDGE_EPSILON = 0.004
Y_SEEN_DELTA = 12  # px tolerance to de-dup rows across pages by Y position

# Only keep rows fully visible inside the ORIGINAL column ROI
VISIBLE_MARGIN_PX = 0
MIN_HEIGHT_FRAC_OF_MEDIAN = 0.82  # drop short rows (likely clipped)

# -----------------------------------------------------------------------------


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"

def _all_known_slugs_for_category(category: str) -> List[str]:
    """Collect existing label slugs for a category from assets folders.
    This helps name new crops when OCR is noisy.
    """
    base = Path("assets/match_templates/upgrades") / category
    slugs: set = set()
    for side in ("left", "right"):
        d = base / side
        if d.is_dir():
            for p in d.glob("*.png"):
                slugs.add(p.stem.lower())
    return sorted(slugs)

def _infer_slug_by_similarity(label_img: np.ndarray, category: str) -> Optional[str]:
    """Try to infer a canonical slug by comparing the crop to existing templates.
    Returns slug or None if no sufficiently strong match is found.
    """
    known = _all_known_slugs_for_category(category)
    if not known:
        return None

    # Prepare candidate list of (slug, template_img)
    candidates: List[Tuple[str, np.ndarray]] = []
    base = Path("assets/match_templates/upgrades") / category
    for side in ("left", "right"):
        d = base / side
        for slug in known:
            p = d / f"{slug}.png"
            if p.exists():
                tpl = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if tpl is not None:
                    candidates.append((slug, tpl))

    if not candidates:
        return None

    # Ensure working grayscale for crop
    crop = label_img
    if crop.ndim == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    best_slug: Optional[str] = None
    best_score: float = -1.0
    for slug, tpl in candidates:
        h, w = tpl.shape[:2]
        # Resize crop to template size for direct similarity
        resized = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(resized, tpl, cv2.TM_CCOEFF_NORMED)
        _minv, maxv, _minl, _maxl = cv2.minMaxLoc(res)
        if maxv > best_score:
            best_score = maxv
            best_slug = slug

    if best_score >= SIM_MATCH_THRESHOLD:
        return best_slug
    return None

def _canonical_slug_from_keywords(raw: str, category: str) -> Optional[str]:
    """Map noisy OCR text to a canonical slug using keyword rules."""
    t = raw.lower()
    letters = re.sub(r"[^a-z]+", " ", t)
    # Helpers
    has = lambda *ws: all(w in letters for w in ws)

    if category == "attack":
        # Left column
        if has("bounce", "range"): return "bounce_shot_range"
        if has("bounce", "chance"): return "bounce_shot_chance"
        if has("rapid", "chance"):  return "rapid_fire_chance"
        if has("multi", "chance"):  return "multishot_chance"
        if has("critical", "chance"):return "critical_chance"
        if has("rend", "armor") and ("mult" in letters or "multi" in letters): return "rend_armor_mult"
        if has("damage") and "per" not in letters: return "damage"
        if has("range") and "bounce" not in letters: return "range"
        # Right column
        if has("attack", "speed"): return "attack_speed"
        if has("critical", "factor"): return "critical_factor"
        if has("damage", "per", "meter"): return "damage_per_meter"
        if has("multi", "targets"): return "multishot_targets"
        if has("rapid", "duration"): return "rapid_fire_duration"
        if has("bounce", "targets"): return "bounce_shot_targets"
        if has("super", "crit", "chance"): return "super_crit_chance"
        if has("super", "crit") and ("mult" in letters or "multiplier" in letters): return "super_crit_mult"
        if has("rend", "armor", "chance"): return "rend_armor_chance"
    return None

def _read_shared_region(dot_key: str) -> Tuple[int, int, int, int]:
    entry = resolve_dot_path(dot_key)
    if not entry or "match_region" not in entry:
        raise RuntimeError(f"Missing shared region: {dot_key}")
    r = entry["match_region"]
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])

def _crop(img: np.ndarray, rect: Tuple[int,int,int,int]) -> np.ndarray:
    x,y,w,h = rect
    return img[y:y+h, x:x+w].copy()

def _roi_change_ratio(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = cv2.resize(a, (w, h))
        b = cv2.resize(b, (w, h))
    a_g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    b_g = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(a_g, b_g)
    return float(diff.mean()) / 255.0

def _visible_row_tops(img: np.ndarray, side: str) -> List[int]:
    """Return Y positions of fully visible rows inside the shared upgrades column."""
    x0,y0,w0,h0 = _read_shared_region(LEFT_SHARED_KEY if side=="left" else RIGHT_SHARED_KEY)
    Hs, Ws = img.shape[:2]
    x = x0
    y = max(0, y0 - DETECT_PAD_PX)
    w = w0
    h = min(h0 + 2*DETECT_PAD_PX, Hs - y)
    col_roi_ext = _crop(img, (x,y,w,h))
    rows_local = _detect_row_rects(col_roi_ext)
    rows_full = [(x+rx, y+ry, rw, rh) for (rx,ry,rw,rh) in rows_local]
    def _fully_visible(r):
        rx, ry, rw, rh = r
        return (ry >= y0 + VISIBLE_MARGIN_PX) and (ry + rh <= y0 + h0 - VISIBLE_MARGIN_PX)
    rows_full = [r for r in rows_full if _fully_visible(r)]
    if rows_full:
        med_h = float(np.median([rh for (_,_,_,rh) in rows_full]))
        rows_full = [r for r in rows_full if r[3] >= MIN_HEIGHT_FRAC_OF_MEDIAN * med_h]
    return [r[1] for r in rows_full]

def _visible_row_label_hashes(img: np.ndarray, side: str) -> List[Tuple[int,int]]:
    """Compute (aHash,dHash) for label crops of fully visible rows in the column."""
    x0,y0,w0,h0 = _read_shared_region(LEFT_SHARED_KEY if side=="left" else RIGHT_SHARED_KEY)
    Hs, Ws = img.shape[:2]
    x = x0
    y = max(0, y0 - DETECT_PAD_PX)
    w = w0
    h = min(h0 + 2*DETECT_PAD_PX, Hs - y)
    col_roi_ext = _crop(img, (x,y,w,h))
    rows_local = _detect_row_rects(col_roi_ext)
    rows_full = [(x+rx, y+ry, rw, rh) for (rx,ry,rw,rh) in rows_local]
    def _fully_visible(r):
        rx, ry, rw, rh = r
        return (ry >= y0 + VISIBLE_MARGIN_PX) and (ry + rh <= y0 + h0 - VISIBLE_MARGIN_PX)
    rows_full = [r for r in rows_full if _fully_visible(r)]
    if rows_full:
        med_h = float(np.median([rh for (_,_,_,rh) in rows_full]))
        rows_full = [r for r in rows_full if r[3] >= MIN_HEIGHT_FRAC_OF_MEDIAN * med_h]
    hashes: List[Tuple[int,int]] = []
    for r in rows_full:
        label_rect, _panel_rect, _ = _compute_label_and_panel(r, img)
        label_img = _crop(img, label_rect)
        hashes.append((_ahash64(label_img), _dhash64(label_img)))
    return hashes

def scroll_to_edge(column: str, to_top: bool, max_swipes: int = 16) -> bool:
    rect = _read_shared_region(LEFT_SHARED_KEY if column=="left" else RIGHT_SHARED_KEY)
    img = capture_adb_screenshot()
    if img is None:
        raise RuntimeError("No screenshot from device.")
    prev = _crop(img, rect)
    for _ in range(max_swipes):
        page_column(column, "up" if to_top else "down", strength="page")
        time.sleep(POST_SWIPE_SLEEP)
        img2 = capture_adb_screenshot()
        if img2 is None:
            continue
        roi = _crop(img2, rect)
        change = _roi_change_ratio(prev, roi)
        if change < EDGE_EPSILON:
            return True
        prev = roi
    return False

# --- Row and panel/split detection -------------------------------------------

def _detect_row_rects(col_roi: np.ndarray) -> List[Tuple[int,int,int,int]]:
    """Find row rectangles via teal border (no visibility filtering here)."""
    hsv = cv2.cvtColor(col_roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, ROW_BORDER_HSV_LOWER, ROW_BORDER_HSV_UPPER)
    kernel = np.ones((5,5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    H, W = col_roi.shape[:2]
    min_area = MIN_ROW_AREA_FRAC * (H*W)
    rects: List[Tuple[int,int,int,int]] = []
    for c in contours:
        x,y,w,h = cv2.boundingRect(c)
        area = w*h
        if area < min_area:
            continue
        ar = w / float(h + 1e-6)
        if not (MIN_ROW_ASPECT <= ar <= MAX_ROW_ASPECT):
            continue
        # clip and keep
        x = max(0, x); y = max(0, y)
        w = min(W - x, w); h = min(H - y, h)
        rects.append((x,y,w,h))

    rects.sort(key=lambda r: r[1])
    # de-dup by Y proximity
    deduped: List[Tuple[int,int,int,int]] = []
    last_y = -10**9
    for r in rects:
        if not deduped or abs(r[1] - last_y) > 10:
            deduped.append(r)
            last_y = r[1]
    return deduped

def _find_right_panel_by_contours(row_img: np.ndarray) -> Optional[Tuple[int,int,int,int]]:
    """Color-agnostic right-panel via edges + contours in right-half."""
    H, W = row_img.shape[:2]
    gray = cv2.cvtColor(row_img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5,5), 0)
    edges = cv2.Canny(gray, 40, 110)
    kernel = np.ones((3,3), np.uint8)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    row_area = float(W * H)
    cand = None
    cand_score = -1.0

    for c in contours:
        x,y,w,h = cv2.boundingRect(c)
        # ignore huge outer row or tiny fragments
        area_frac = (w*h) / row_area
        if not (RIGHT_AREA_FRAC_MINMAX[0] <= area_frac <= RIGHT_AREA_FRAC_MINMAX[1]):
            continue
        if (x + w/2.0) / W < RIGHT_MIN_X_FRAC:
            continue
        ar = w / float(h + 1e-6)
        if not (RIGHT_ASPECT_MINMAX[0] <= ar <= RIGHT_ASPECT_MINMAX[1]):
            continue
        score = (x + w) + 0.1 * (w*h)  # prefer further right & slightly larger
        if score > cand_score:
            cand = (x,y,w,h)
            cand_score = score

    return cand

def _find_split_line_x(row_img: np.ndarray) -> Optional[int]:
    """Find vertical split by gradient energy; robust to color (works for gold/blue)."""
    H, W = row_img.shape[:2]
    gray = cv2.cvtColor(row_img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3,3), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    energy = np.abs(gx).sum(axis=0)
    L = int(SPLIT_SEARCH_FRAC[0] * W)
    R = int(SPLIT_SEARCH_FRAC[1] * W)
    if R <= L + 5:
        return None
    seg = energy[L:R].astype(np.float32)
    k = max(1, int(SPLIT_BOX_SMOOTH_PX))
    seg = cv2.blur(seg.reshape(1, -1), (1, k)).flatten()
    idx = int(seg.argmax())
    peak = float(seg[idx])
    mean, std = float(seg.mean()), float(seg.std() + 1e-6)
    if peak < mean + SPLIT_MIN_STD_MULT * std:
        return None
    return L + idx

def _compute_label_and_panel(row_rect: Tuple[int,int,int,int], img: np.ndarray):
    """
    Given full-screen row_rect, compute:
      - label_rect: full left tile up to (but not including) the right panel/split
      - panel_rect: right panel rect if detected (else None)
      - split_x_abs: absolute split x if used (else None)
    """
    rx, ry, rw, rh = row_rect
    row_img = img[ry:ry+rh, rx:rx+rw]

    # 1) Try panel by contours (edge-based)
    panel_local = _find_right_panel_by_contours(row_img)
    split_x_local = None

    # 2) If not found, try split-line
    if panel_local is None:
        sx = _find_split_line_x(row_img)
        if sx is not None:
            split_x_local = sx

    inset = ROW_INSET_PX
    lx = rx + inset
    ly = ry + inset
    lh = max(1, rh - 2*inset)

    if panel_local is not None:
        px, py, pw, ph = panel_local
        panel_left_abs = rx + px
        lw = max(1, panel_left_abs - LABEL_GAP_PX - lx)
        label_rect = (lx, ly, lw, lh)
        panel_rect = (rx + px, ry + py, pw, ph)
        return label_rect, panel_rect, None

    if split_x_local is not None:
        cut_abs = rx + split_x_local
        lw = max(1, cut_abs - LABEL_GAP_PX - lx)
        label_rect = (lx, ly, lw, lh)
        return label_rect, None, cut_abs

    # 3) Fallback: fixed fraction
    lw = int(rw * FALLBACK_LABEL_WIDTH_FRAC) - inset
    lw = max(1, lw)
    label_rect = (lx, ly, lw, lh)
    return label_rect, None, None

# --- Review dump helpers ------------------------------------------------------

def _init_review_dir(base: Optional[str], category: str, side: str) -> Path:
    if base:
        p = Path(base)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        p = Path(f"screenshots/label_review/{category}_{side}_{ts}")
    p.mkdir(parents=True, exist_ok=True)
    return p

def _manifest_write(fp, rec: Dict[str, object]) -> None:
    fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    fp.flush()

def _save_img(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)

def _ahash64(img: np.ndarray) -> int:
    """Average-hash (8x8 -> 64-bit) robust to small crop/lighting changes."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(g, (8, 8), interpolation=cv2.INTER_AREA)
    mean = float(small.mean())
    bits = (small > mean).astype(np.uint8).flatten()
    val = 0
    for b in bits:
        val = (val << 1) | int(b)
    return val

def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()

def _dhash64(img: np.ndarray) -> int:
    """Difference-hash (horizontal, 8x8 -> 64-bit). Captures structure differences."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    # Resize to 9x8 so we can compare adjacent columns
    small = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    diff = (small[:, 1:] > small[:, :-1]).astype(np.uint8).flatten()
    val = 0
    for b in diff:
        val = (val << 1) | int(b)
    return val

def _write_clickmap_entry(category: str, side: str, slug: str, template_rel: str) -> None:
    dot_path = f"upgrades.{category}.{side}.{slug}"
    entry = {
        "match_template": template_rel,
        "region_ref": f"upgrades_{side}",
        "match_threshold": DEFAULT_THRESHOLD,
        "roles": DEFAULT_ROLES
    }
    set_dot_path(dot_path, entry, allow_overwrite=True)

# --- Per-page processing ------------------------------------------------------

def process_visible_page(img: np.ndarray, column: str, category: str,
                         seen_slugs: set, write: bool,
                         page_idx: int, review_dir: Path,
                         save_overlay_img: Optional[np.ndarray],
                         order_list: Optional[List[str]] = None,
                         seq_idx: int = 0,
                         seen_rows_y: Optional[List[int]] = None,
                         seen_hashes: Optional[List[Tuple[int,int]]] = None) -> Tuple[int, int]:
    """Process current visible rows; returns number of new labels written."""
    # Original column ROI (used for visibility test)
    x0,y0,w0,h0 = _read_shared_region(LEFT_SHARED_KEY if column=="left" else RIGHT_SHARED_KEY)

    # Detection ROI expanded vertically to avoid truncation at edges
    Hs, Ws = img.shape[:2]
    x = x0
    y = max(0, y0 - DETECT_PAD_PX)
    w = w0
    h = min(h0 + 2*DETECT_PAD_PX, Hs - y)

    col_roi_ext = _crop(img, (x,y,w,h))
    rows_local = _detect_row_rects(col_roi_ext)
    # convert local rects (within EXTENDED ROI) to full-screen rects
    rows_full = [(x+rx, y+ry, rw, rh) for (rx,ry,rw,rh) in rows_local]

    # keep only rows fully inside the ORIGINAL ROI with a tiny margin
    def _fully_visible(r):
        rx, ry, rw, rh = r
        return (ry >= y0 + VISIBLE_MARGIN_PX) and (ry + rh <= y0 + h0 - VISIBLE_MARGIN_PX)

    rows_full = [r for r in rows_full if _fully_visible(r)]

    # median-height guard to drop clipped rows
    if rows_full:
        med_h = float(np.median([rh for (_,_,_,rh) in rows_full]))
        rows_full = [r for r in rows_full if r[3] >= MIN_HEIGHT_FRAC_OF_MEDIAN * med_h]

    new_count = 0
    if seen_rows_y is None:
        seen_rows_y = []
    if seen_hashes is None:
        seen_hashes = []
    manifest_path = review_dir / "manifest.jsonl"
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for r_idx, row_full in enumerate(rows_full):
            ry_global = row_full[1]
            label_rect, panel_rect, split_x_abs = _compute_label_and_panel(row_full, img)

            row_img   = _crop(img, row_full)
            label_img = _crop(img, label_rect)
            panel_img = _crop(img, panel_rect) if panel_rect else None

            if order_list and seq_idx < len(order_list):
                slug = order_list[seq_idx]
                raw = "<order>"
            else:
                pre = preprocess_binary(label_img)
                raw = (ocr_text(pre) or "").strip()
                slug = _slugify(raw)
                # Trim common OCR noise suffixes like trailing _e/_ee/_eee, _el
                slug = re.sub(r"_(e|l){1,8}$", "", slug)
                # Heuristics: try keyword mapping first, then template similarity
                if slug == "unknown" or len(re.sub(r"[^a-z]", "", slug)) < 3 or re.fullmatch(r"e+", slug) or slug.endswith("_eeeeeeee"):
                    kw = _canonical_slug_from_keywords(raw, category)
                    if kw:
                        slug = kw
                    else:
                        inferred = _infer_slug_by_similarity(label_img, category)
                        if inferred:
                            slug = inferred
            pretty_slug = slug if re.search(r"[a-z0-9]", slug) else "unknown"

            y_hint = row_full[1]
            base_name = f"p{page_idx:02d}_r{r_idx:02d}_{pretty_slug}_y{y_hint}"

            # --- review artifacts ---
            _save_img(review_dir / "labels" / f"{base_name}.png", label_img)
            _save_img(review_dir / "rows" / f"{base_name}.png", row_img)
            if panel_img is not None:
                _save_img(review_dir / "panels" / f"{base_name}.png", panel_img)

            # overlay
            if save_overlay_img is not None:
                rx, ry, rw, rh = row_full
                cv2.rectangle(save_overlay_img, (rx, ry), (rx+rw, ry+rh), (0,255,0), 2)      # row
                if panel_rect is not None:
                    px, py, pw, ph = panel_rect
                    cv2.rectangle(save_overlay_img, (px, py), (px+pw, py+ph), (0,0,255), 2)  # panel
                if split_x_abs is not None:
                    cv2.line(save_overlay_img, (split_x_abs, ry+4), (split_x_abs, ry+rh-4), (255,255,0), 2)
                lx, ly, lw_, lh_ = label_rect
                cv2.rectangle(save_overlay_img, (lx, ly), (lx+lw_, ly+lh_), (255,0,0), 2)    # label

            template_rel = f"upgrades/{category}/{column}/{slug or 'unknown'}.png"

            rec = {
                "category": category,
                "side": column,
                "page": page_idx,
                "row_index": r_idx,
                "raw_text": raw,
                "slug": slug,
                "row_rect":   {"x": row_full[0],  "y": row_full[1],  "w": row_full[2],  "h": row_full[3]},
                "panel_rect": {"x": panel_rect[0], "y": panel_rect[1], "w": panel_rect[2], "h": panel_rect[3]} if panel_rect else None,
                "split_x_abs": split_x_abs,
                "label_rect": {"x": label_rect[0], "y": label_rect[1], "w": label_rect[2], "h": label_rect[3]},
                "template_rel": template_rel,
                "status": "dry-run" if not write else "committed"
            }
            _manifest_write(mf, rec)

            # If still not confident about naming, do not commit a bad filename.
            if (not order_list) and (not slug or slug == "unknown" or len(re.sub(r"[^a-z]", "", slug)) < 3):
                log(f"[SKIP] Unreliable slug -> {template_rel}. Leaving as review-only.", "WARN"); new_count += 1
                continue
            if slug in seen_slugs and write:
                log(f"Duplicate slug in run, skipping write: {slug}", "WARN")
                if order_list and seq_idx < len(order_list):
                    seq_idx += 1
                continue

            # Content-level dedup across pages to avoid overlap repeats
            a_sig = _ahash64(label_img)
            d_sig = _dhash64(label_img)
            # Duplicate only if BOTH aHash and dHash are very close to some prior capture
            if any((_hamming(a_sig, a0) <= 4 and _hamming(d_sig, d0) <= 8) for (a0, d0) in seen_hashes):
                continue

            if write:
                template_abs = Path("assets/match_templates") / template_rel
                _save_img(template_abs, label_img)
                _write_clickmap_entry(category, column, slug, template_rel)
                new_count += 1
                log(f"Wrote template+entry: {template_rel}", "INFO")
            else:
                log(f"[DRY-RUN] Would write {template_rel}", "INFO")

            seen_slugs.add(slug)
            seen_rows_y.append(ry_global)
            seen_hashes.append((a_sig, d_sig))
            if order_list and seq_idx < len(order_list):
                seq_idx += 1
            # In order mode, do NOT break here — process all remaining fully
            # visible, unique rows in this same frame before swiping. This
            # allows capturing two (or more) fully visible boxes without an
            # unnecessary scroll between them. seq_idx advances per write.

    return new_count, seq_idx

# --- Main ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Auto-generate upgrade label templates and clickmap entries.")
    ap.add_argument("--category", choices=["attack","defense","utility"], default="attack")
    ap.add_argument("--side", choices=["left","right"], required=True)
    ap.add_argument("--commit", action="store_true", help="Write templates and clickmap (default dry-run).")
    ap.add_argument("--pages", type=int, default=40, help="Max pages to scan.")
    ap.add_argument("--step", choices=["micro","page"], default="micro", help="Swipe strength between scans (default: micro)")
    ap.add_argument("--micro-tries", type=int, default=3, help="How many micro swipes to try before a page swipe fallback (order mode)")
    ap.add_argument("--order", default=None,
                    help="Comma-separated list of canonical slugs to assign in order (skips OCR). Example: 'bounce_shot_range,bounce_shot_chance,...'")
    ap.add_argument("--order-file", default=None,
                    help="Path to JSON file with {\"attack\":{\"left\":[..],\"right\":[..]}, ...}. When provided, overrides --order.")
    ap.add_argument("--from-top", action="store_true", help="Scroll to top before scanning.")
    ap.add_argument("--debug", action="store_true", help="Also save page overlay images with rectangles.")
    ap.add_argument("--review-dir", default=None, help="Directory to dump review images & manifest.")
    args = ap.parse_args()

    side = args.side
    category = args.category
    write = args.commit
    # Determine optional order-based naming list
    order_list = None
    if args.order_file:
        try:
            import json as _json
            with open(args.order_file, "r", encoding="utf-8") as f:
                mapping = _json.load(f)
            order_list = (mapping.get(category, {}) or {}).get(side)
        except Exception as e:
            log(f"[WARN] Failed to read --order-file: {e}", "WARN")
            order_list = None
    elif args.order:
        order_list = [s.strip() for s in args.order.split(',') if s.strip()]

    review_dir = _init_review_dir(args.review_dir, category, side)
    log(f"Review dump → {review_dir}", "INFO")

    if args.from_top:
        ok = scroll_to_edge(side, to_top=True, max_swipes=16)
        log(f"scroll_to_top({side}) -> {ok}", "INFO")

    seen: set = set()
    total_new = 0
    seq_idx = 0  # next index into order_list when provided
    seen_rows_y: List[int] = []  # track processed row Y positions across pages
    seen_hashes: List[int] = []  # track label content across pages (aHash list)

    for page_idx in range(args.pages):
        img = capture_adb_screenshot()
        if img is None:
            log("Failed to capture screenshot; retrying after swipe.", "WARN")
            page_column(side, "down", strength="micro" if page_idx == 0 else "page")
            time.sleep(POST_SWIPE_SLEEP)
            continue

        overlay = img.copy() if args.debug else None
        wrote, seq_idx = process_visible_page(img, side, category, seen, write, page_idx, review_dir, overlay, order_list, seq_idx, seen_rows_y, seen_hashes)
        total_new += wrote

        if order_list and seq_idx >= len(order_list):
            log("Completed order list; stopping early.", "INFO")
            break

        if args.debug:
            out = review_dir / f"page_overlay_p{page_idx:02d}.png"
            _save_img(out, overlay)
            log(f"Saved page overlay: {out}", "INFO")

        # After processing, keep swiping until a truly new row appears (order mode)
        rect = _read_shared_region(LEFT_SHARED_KEY if side=="left" else RIGHT_SHARED_KEY)
        before_img = _crop(img, rect)
        before_tops = _visible_row_tops(img, side)
        attempts = 0
        # Try a few micro swipes to coax the next row fully into view, then escalate
        max_attempts = (args.micro_tries if order_list else 1)
        while attempts < max_attempts:
            page_column(side, "down", strength=args.step)
            time.sleep(POST_SWIPE_SLEEP)
            probe = capture_adb_screenshot()
            if probe is None:
                attempts += 1
                continue
            after_img = _crop(probe, rect)
            # If content changed and at least one new row Y appeared, break
            changed = _roi_change_ratio(before_img, after_img) >= EDGE_EPSILON
            tops = _visible_row_tops(probe, side)
            # New if any visible row hash is not similar to previously seen hashes
            row_hashes = _visible_row_label_hashes(probe, side)
            # New when there exists a row whose (aHash,dHash) pair is not close to any prior
            def _is_new(pair):
                return all(not (_hamming(pair[0], a0) <= 4 and _hamming(pair[1], d0) <= 8) for (a0,d0) in seen_hashes)
            has_new = any(_is_new(p) for p in row_hashes)
            # Stop as soon as content actually changed; the next outer loop will
            # process any newly visible rows. This avoids over-swiping past
            # frames where a new row is already fully visible but hashes were
            # judged too similar.
            if changed:
                img = probe
                break
            attempts += 1
        else:
            # Fallback: one page-sized swipe if micro didn’t move content
            if args.step == "micro":
                page_column(side, "down", strength="page")
                time.sleep(POST_SWIPE_SLEEP)
        after_frame = capture_adb_screenshot()
        if after_frame is None:
            continue
        after_crop = _crop(after_frame, rect)
        if _roi_change_ratio(before_img, after_crop) < EDGE_EPSILON:
            log("Reached bottom of list.", "INFO")
            break

    if write:
        save_clickmap(get_clickmap())
        log(f"Done. New labels written: {total_new}", "INFO")
    else:
        log(f"Dry run complete. Would write {total_new} templates. Re-run with --commit.", "INFO")

if __name__ == "__main__":
    main()
