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

# Edge detection when paging
EDGE_EPSILON = 0.004

# Only keep rows fully visible inside the ORIGINAL column ROI
VISIBLE_MARGIN_PX = 0
MIN_HEIGHT_FRAC_OF_MEDIAN = 0.82  # drop short rows (likely clipped)

# -----------------------------------------------------------------------------


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"

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
                         save_overlay_img: Optional[np.ndarray]) -> int:
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
    manifest_path = review_dir / "manifest.jsonl"
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for r_idx, row_full in enumerate(rows_full):
            label_rect, panel_rect, split_x_abs = _compute_label_and_panel(row_full, img)

            row_img   = _crop(img, row_full)
            label_img = _crop(img, label_rect)
            panel_img = _crop(img, panel_rect) if panel_rect else None

            pre = preprocess_binary(label_img)
            raw = (ocr_text(pre) or "").strip()
            slug = _slugify(raw)
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

            if not slug or len(re.sub(r"[^a-z]", "", slug)) < 3:
                log(f"[DRY-RUN] Would write {template_rel}", "INFO"); new_count += 1
                continue
            if slug in seen_slugs and write:
                log(f"Duplicate slug in run, skipping write: {slug}", "WARN")
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

    return new_count

# --- Main ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Auto-generate upgrade label templates and clickmap entries.")
    ap.add_argument("--category", choices=["attack","defense","utility"], default="attack")
    ap.add_argument("--side", choices=["left","right"], required=True)
    ap.add_argument("--commit", action="store_true", help="Write templates and clickmap (default dry-run).")
    ap.add_argument("--pages", type=int, default=40, help="Max pages to scan.")
    ap.add_argument("--from-top", action="store_true", help="Scroll to top before scanning.")
    ap.add_argument("--debug", action="store_true", help="Also save page overlay images with rectangles.")
    ap.add_argument("--review-dir", default=None, help="Directory to dump review images & manifest.")
    args = ap.parse_args()

    side = args.side
    category = args.category
    write = args.commit

    review_dir = _init_review_dir(args.review_dir, category, side)
    log(f"Review dump → {review_dir}", "INFO")

    if args.from_top:
        ok = scroll_to_edge(side, to_top=True, max_swipes=16)
        log(f"scroll_to_top({side}) -> {ok}", "INFO")

    seen: set = set()
    total_new = 0

    for page_idx in range(args.pages):
        img = capture_adb_screenshot()
        if img is None:
            log("Failed to capture screenshot; retrying after swipe.", "WARN")
            page_column(side, "down", strength="micro" if page_idx == 0 else "page")
            time.sleep(POST_SWIPE_SLEEP)
            continue

        overlay = img.copy() if args.debug else None
        wrote = process_visible_page(img, side, category, seen, write, page_idx, review_dir, overlay)
        total_new += wrote

        if args.debug:
            out = review_dir / f"page_overlay_p{page_idx:02d}.png"
            _save_img(out, overlay)
            log(f"Saved page overlay: {out}", "INFO")

        # Edge detection after paging
        rect = _read_shared_region(LEFT_SHARED_KEY if side=="left" else RIGHT_SHARED_KEY)
        before = _crop(img, rect)
        page_column(side, "down", strength="micro" if page_idx == 0 else "page")
        time.sleep(POST_SWIPE_SLEEP)
        after_img = capture_adb_screenshot()
        if after_img is None:
            continue
        after = _crop(after_img, rect)
        if _roi_change_ratio(before, after) < EDGE_EPSILON:
            log("Reached bottom of list.", "INFO")
            break

    if write:
        save_clickmap(get_clickmap())
        log(f"Done. New labels written: {total_new}", "INFO")
    else:
        log(f"Dry run complete. Would write {total_new} templates. Re-run with --commit.", "INFO")

if __name__ == "__main__":
    main()
