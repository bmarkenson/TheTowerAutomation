#!/usr/bin/env python3
# utils/wave_detector.py

from typing import Optional, Tuple, List, Dict, Any
import os
import re
import time

import cv2
import numpy as np

from core.ss_capture import capture_adb_screenshot
from core.clickmap_access import resolve_dot_path, get_clickmap
from utils.ocr_utils import ocr_number_with_fallback

# ROIs: digits-only first, full label as fallback
PRIMARY_DOT_PATH = "_shared_match_regions.wave_number_digits"
FALLBACK_DOT_PATH = "_shared_match_regions.wave_number"

# Preferences & limits
_DEFAULT_MAX_VALUE = 20000         # hard ceiling on accepted wave values
_DEFAULT_RATE_PER_MIN = 10.0       # expected waves per minute
_DEFAULT_TOLERANCE = 20            # ±window around expected

# Module-level hint + timestamp (no main.py change required)
_LAST_WAVE_SEEN: Optional[int] = None
_LAST_WAVE_TS: Optional[float] = None

# ---------------------------- hint helpers ------------------------------------

def set_wave_hint(val: Optional[int], ts: Optional[float] = None) -> None:
    """
    Seed/override the last-wave hint and timestamp used for proximity scoring.
    If ts is None, uses current time.
    """
    global _LAST_WAVE_SEEN, _LAST_WAVE_TS
    _LAST_WAVE_SEEN = val
    _LAST_WAVE_TS = (time.time() if ts is None else float(ts))

def get_wave_hint() -> Optional[int]:
    return _LAST_WAVE_SEEN

# ---------------------------- helpers: OCR debug ------------------------------

def _tess_info() -> str:
    try:
        import pytesseract
        try:
            v = pytesseract.get_tesseract_version()
            return f"pytesseract OK, tesseract {v}"
        except Exception as e:
            return f"pytesseract OK, version check failed: {e!r}"
    except Exception as e:
        return f"pytesseract import FAILED: {e!r}"

def _ocr_probe(gray_or_bin: np.ndarray, *, psm_text: int = 7, psm_digits: int = 7) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        import pytesseract
    except Exception as e:
        out["error"] = f"pytesseract import failed: {e!r}"
        return out

    rgb = cv2.cvtColor(gray_or_bin, cv2.COLOR_GRAY2RGB)

    try:
        data_text = pytesseract.image_to_data(rgb, config=f"--psm {psm_text}", output_type=pytesseract.Output.DICT)
        toks = list(zip(data_text.get("text", []), data_text.get("conf", [])))
        out["image_to_data(psm_text)"] = {"n_tokens": len(toks), "tokens": toks[:50]}
    except Exception as e:
        out["image_to_data(psm_text)"] = f"ERROR: {e!r}"

    try:
        plain = pytesseract.image_to_string(rgb, config=f"--psm {psm_text}")
        out["image_to_string(psm_text)"] = plain
    except Exception as e:
        out["image_to_string(psm_text)"] = f"ERROR: {e!r}"

    try:
        data_digits = pytesseract.image_to_data(
            rgb,
            config=f"--psm {psm_digits} -c tessedit_char_whitelist=0123456789",
            output_type=pytesseract.Output.DICT,
        )
        d_toks = list(zip(data_digits.get("text", []), data_digits.get("conf", [])))
        out["image_to_data(digits)"] = {"n_tokens": len(d_toks), "tokens": d_toks[:50]}
    except Exception as e:
        out["image_to_data(digits)"] = f"ERROR: {e!r}"

    subs = []
    try:
        for t, c in toks:
            if not t:
                continue
            for m in re.finditer(r"\d{1,9}", t):
                subs.append((m.group(0), c))
    except Exception:
        pass
    out["numeric_substrings_from_tokens"] = subs[:50]
    return out

# ---------------------------- helpers: ROI / crops ----------------------------

def _get_bbox(dot_path: str) -> Tuple[int, int, int, int]:
    cm = get_clickmap()
    entry = resolve_dot_path(dot_path, cm)
    if not entry or "match_region" not in entry:
        raise KeyError(f"Missing match_region at dot_path: {dot_path}")
    r = entry["match_region"]
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])

def _crop(img: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = bbox
    H, W = img.shape[:2]
    x2, y2 = min(x + w, W), min(y + h, H)
    x1, y1 = max(0, x), max(0, y)
    if x1 >= x2 or y1 >= y2:
        raise ValueError(f"Invalid crop bbox after clamping: {x1,y1,x2,y2}")
    return img[y1:y2, x1:x2]

def _save_overlay(img_bgr: np.ndarray, dot_path: str, out_path: str) -> None:
    try:
        x, y, w, h = _get_bbox(dot_path)
        vis = img_bgr.copy()
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.imwrite(out_path, vis)
        print(f"[DEBUG] Saved overlay to {out_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save overlay: {e}")

# ---------------------------- FAST PATH (default) -----------------------------

def _fast_variants_from_crop(crop_bgr: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """
    Minimal & reliable set:
      - Otsu + small close (2x2) at 1.0x and 1.8x
      - Both polarities
    """
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.6, beta=0)  # contrast boost
    _t, thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    base = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, k, iterations=1)

    variants: List[Tuple[str, np.ndarray]] = []
    for pol_name, img in (("otsu_close", base), ("otsu_close_inv", cv2.bitwise_not(base))):
        variants.append((f"{pol_name}_x1.0", img))
        up = cv2.resize(img, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
        variants.append((f"{pol_name}_x1.8", up))
    return variants

def _score(
    val: Optional[int],
    conf: float,
    *,
    last_wave: Optional[int],
    expected: Optional[float],
    tolerance: int,
    max_value: int,
) -> Tuple[int, int, float, float, int]:
    """
    Score candidates for max() selection.

    Returns tuple ordered by importance:
      (valid_flag, proximity_bucket, confidence, proximity_tiebreak, digits_len)

    - Discards decreases: if last_wave is set and val < last_wave -> invalid.
    - valid_flag = 1 if val is not None and val < max_value else 0.
    - proximity_bucket (if expected available):
        2 if |val - expected| <= tolerance
        1 if |val - expected| <= 2*tolerance
        0 otherwise
      Neutral (1) when no expected available.
    - proximity_tiebreak: higher is better; uses -abs(val - expected) (0 if expected is None).
    - digits_len: prefer more digits as a final tie-break to avoid single-digit misreads.
    """
    if val is None:
        return (0, 0, -1.0, -1e9, 0)

    # Monotonic: never go down
    if last_wave is not None and val < last_wave:
        return (0, 0, -1.0, -1e9, 0)

    valid_flag = 1 if val < max_value else 0

    if expected is None:
        prox_bucket = 1  # neutral
        prox_tb = 0.0
    else:
        delta = abs(val - expected)
        if delta <= tolerance:
            prox_bucket = 2
        elif delta <= 2 * tolerance:
            prox_bucket = 1
        else:
            prox_bucket = 0
        prox_tb = -float(delta)

    digits_len = len(str(val))
    return (valid_flag, prox_bucket, float(conf), prox_tb, digits_len)

def _detect_quick(
    img_bgr: np.ndarray,
    dot_path: str,
    *,
    verbose: bool,
    last_wave: Optional[int],
    expected: Optional[float],
    tolerance: int,
    max_value: int,
) -> Tuple[Optional[int], float, Optional[str], Optional[np.ndarray]]:
    """Try the fast variants on the full ROI only."""
    try:
        bbox = _get_bbox(dot_path)
    except Exception as e:
        if verbose:
            print(f"[DEBUG] resolve bbox failed for {dot_path}: {e}")
        return None, -1.0, None, None

    crop = _crop(img_bgr, bbox)
    best_val, best_conf, best_tag, best_img = None, -1.0, None, None
    best_score = _score(None, -1.0, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)

    for tag, var in _fast_variants_from_crop(crop):
        val, conf, _ = ocr_number_with_fallback(var, psm_digits=7, psm_text=7)
        cand_score = _score(val, conf, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)
        if verbose:
            h, w = var.shape[:2]
            print(f"[FAST] {dot_path}/{tag}: size={w}x{h} -> val={val} conf={conf} score={cand_score}")
        if cand_score > best_score:
            best_score, best_val, best_conf, best_tag, best_img = cand_score, val, conf, tag, var.copy()

    if verbose:
        print(f"[FAST] best={best_tag} -> value={best_val} conf={best_conf} score={best_score}")
    return best_val, best_conf, best_tag and f"{dot_path}/{best_tag}", best_img

# ----------------------------- HEAVY SWEEP (fallback/debug) -------------------

def _bins_from_crop(crop_bgr: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """Broader set of binarizations for difficult samples."""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.6, beta=0)

    bins: List[Tuple[str, np.ndarray]] = []

    # Adaptive MEAN & GAUSSIAN
    for name, method in [("mean", cv2.ADAPTIVE_THRESH_MEAN_C), ("gauss", cv2.ADAPTIVE_THRESH_GAUSSIAN_C)]:
        thr = cv2.adaptiveThreshold(gray, 255, method, cv2.THRESH_BINARY, 31, 5)
        bins.append((f"{name}", thr))
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        bins.append((f"{name}_close", cv2.morphologyEx(thr, cv2.MORPH_CLOSE, k, iterations=1)))

    # Otsu
    _t, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bins.append(("otsu", otsu))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    bins.append(("otsu_close", cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, k, iterations=1)))

    # Inversions
    invs = []
    for n, b in bins:
        invs.append((f"{n}_inv", cv2.bitwise_not(b)))
    bins.extend(invs)
    return bins

def _scaled_variants(bin_img: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    out = []
    for s in (1.0, 1.8, 2.2):
        if s == 1.0:
            out.append((f"x{s:.1f}", bin_img))
        else:
            out.append((f"x{s:.1f}", cv2.resize(bin_img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)))
    return out

def _make_crops(full: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """Full / left-trim / left-trim + right-shave."""
    H, W = full.shape[:2]
    left_trim = int(0.35 * W)
    right_shave = int(0.10 * W)
    c0 = ("full", full)
    c1 = ("left_trim", full[:, left_trim:] if left_trim < W - 1 else full)
    c2 = ("left_trim_right_shave", c1[1][:, : max(1, c1[1].shape[1] - right_shave)] if c1[1].shape[1] > 1 else c1[1])
    return [c0, c1, c2]

def _detect_heavy(
    img_bgr: np.ndarray,
    dot_path: str,
    *,
    verbose: bool,
    dump_dir: Optional[str],
    debug_out: Optional[str],
    last_wave: Optional[int],
    expected: Optional[float],
    tolerance: int,
    max_value: int,
) -> Tuple[Optional[int], float, Optional[str], Optional[np.ndarray]]:
    try:
        bbox = _get_bbox(dot_path)
    except Exception as e:
        if verbose:
            print(f"[DEBUG] resolve bbox failed for {dot_path}: {e}")
        return None, -1.0, None, None

    full = _crop(img_bgr, bbox)
    if verbose:
        print(f"[HEAVY] dot_path={dot_path} bbox={bbox} crop={full.shape[1]}x{full.shape[0]}")

    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        cv2.imwrite(os.path.join(dump_dir, f"{os.path.basename(dot_path)}_full_raw.png"), full)

    best_val, best_conf, best_tag, best_img = None, -1.0, None, None
    best_score = _score(None, -1.0, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)

    for cname, crop in _make_crops(full):
        for bname, bimg in _bins_from_crop(crop):
            for sname, simg in _scaled_variants(bimg):
                tag = f"{cname}_{bname}_{sname}"
                val, conf, _ = ocr_number_with_fallback(simg, psm_digits=7, psm_text=7)
                cand_score = _score(val, conf, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)

                if verbose:
                    h, w = simg.shape[:2]
                    print(f"[HEAVY] {dot_path}/{tag}: size={w}x{h} -> val={val} conf={conf} score={cand_score}")

                if dump_dir:
                    cv2.imwrite(os.path.join(dump_dir, f"{os.path.basename(dot_path)}_{tag}.png"), simg)
                    probes = _ocr_probe(simg, psm_text=7, psm_digits=7)
                    with open(os.path.join(dump_dir, f"{os.path.basename(dot_path)}_{tag}.txt"), "w", encoding="utf-8") as f:
                        f.write(f"Tesseract: {_tess_info()}\n")
                        f.write(f"Variant: {tag} size={simg.shape[1]}x{simg.shape[0]} val={val} conf={conf} score={cand_score}\n")
                        f.write(repr(probes))

                if cand_score > best_score:
                    best_score, best_val, best_conf, best_tag, best_img = cand_score, val, conf, f"{dot_path}/{tag}", simg.copy()

    if verbose:
        print(f"[HEAVY] best={best_tag} -> value={best_val} conf={best_conf} score={best_score}")
    if debug_out and best_img is not None:
        cv2.imwrite(debug_out, best_img)
    return best_val, best_conf, best_tag, best_img

# ------------------------ PROGRAMMATIC API (with time-based scoring) ----------

def detect_wave_number_from_image(
    img_bgr: np.ndarray,
    *,
    primary_dot_path: str = PRIMARY_DOT_PATH,
    fallback_dot_path: str = FALLBACK_DOT_PATH,
    use_heavy: bool = False,
    verbose: bool = False,
    dump_dir: Optional[str] = None,
    debug_out: Optional[str] = None,
    rate_per_min: float = _DEFAULT_RATE_PER_MIN,
    tolerance: int = _DEFAULT_TOLERANCE,
    max_value: int = _DEFAULT_MAX_VALUE,
) -> Tuple[Optional[int], float]:
    """
    Programmatic entrypoint used by main.py.
    - Monotonic: never accept decreases vs last seen wave.
    - Time-weighted proximity: prefer values near last_wave + rate_per_min * Δt(min) within ±tolerance.
    - Fast path on primary then fallback; heavy sweep if asked or still None.
    Returns (value, confidence). Updates the module hint on success.
    """
    global _LAST_WAVE_SEEN, _LAST_WAVE_TS
    now = time.time()
    last_wave = _LAST_WAVE_SEEN
    expected = None
    if last_wave is not None and _LAST_WAVE_TS is not None:
        dt_min = max(0.0, (now - _LAST_WAVE_TS) / 60.0)
        expected = last_wave + rate_per_min * dt_min

    # Fast: primary
    val, conf, _tag, best_img = _detect_quick(
        img_bgr,
        primary_dot_path,
        verbose=verbose,
        last_wave=last_wave,
        expected=expected,
        tolerance=tolerance,
        max_value=max_value,
    )
    used = primary_dot_path

    # Fast: fallback
    if val is None and fallback_dot_path:
        if verbose:
            print(f"[DEBUG] Primary ROI {primary_dot_path} failed -> trying fallback {fallback_dot_path}")
        val, conf, _tag, best_img = _detect_quick(
            img_bgr,
            fallback_dot_path,
            verbose=verbose,
            last_wave=last_wave,
            expected=expected,
            tolerance=tolerance,
            max_value=max_value,
        )
        if val is not None:
            used = fallback_dot_path

    # Heavy if asked or still None
    if use_heavy or val is None:
        for roi in (primary_dot_path, fallback_dot_path):
            hv_val, hv_conf, _hv_tag, hv_img = _detect_heavy(
                img_bgr,
                roi,
                verbose=verbose,
                dump_dir=dump_dir,
                debug_out=debug_out,
                last_wave=last_wave,
                expected=expected,
                tolerance=tolerance,
                max_value=max_value,
            )
            if _score(hv_val, hv_conf, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value) > \
               _score(val, conf, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value):
                val, conf, best_img, used = hv_val, hv_conf, hv_img, roi

    # Save winner image if requested
    if debug_out and best_img is not None:
        cv2.imwrite(debug_out, best_img)

    # Update hint on success (monotonic already enforced in scoring)
    if val is not None:
        _LAST_WAVE_SEEN = val
        _LAST_WAVE_TS = now

    return val, conf

def detect_wave_number(
    *,
    primary_dot_path: str = PRIMARY_DOT_PATH,
    fallback_dot_path: str = FALLBACK_DOT_PATH,
    use_heavy: bool = False,
    verbose: bool = False,
    dump_dir: Optional[str] = None,
    debug_out: Optional[str] = None,
    rate_per_min: float = _DEFAULT_RATE_PER_MIN,
    tolerance: int = _DEFAULT_TOLERANCE,
    max_value: int = _DEFAULT_MAX_VALUE,
) -> Tuple[Optional[int], float]:
    """Capture via ADB and run detection."""
    img = capture_adb_screenshot()
    if img is None:
        raise RuntimeError("Failed to capture screenshot.")
    return detect_wave_number_from_image(
        img,
        primary_dot_path=primary_dot_path,
        fallback_dot_path=fallback_dot_path,
        use_heavy=use_heavy,
        verbose=verbose,
        dump_dir=dump_dir,
        debug_out=debug_out,
        rate_per_min=rate_per_min,
        tolerance=tolerance,
        max_value=max_value,
    )

def get_wave_number(dot_path: str = PRIMARY_DOT_PATH) -> Optional[int]:
    """
    Back-compat convenience: capture screenshot and return just the integer wave (or None).
    The provided dot_path is treated as the primary; fallback remains the default.
    """
    val, _conf = detect_wave_number(primary_dot_path=dot_path, fallback_dot_path=FALLBACK_DOT_PATH)
    return val

def get_wave_number_from_image(img_bgr: np.ndarray, dot_path: str = PRIMARY_DOT_PATH) -> Optional[int]:
    """
    Back-compat convenience: return just the integer wave from a provided image.
    The provided dot_path is treated as the primary; fallback remains the default.
    """
    val, _conf = detect_wave_number_from_image(img_bgr, primary_dot_path=dot_path, fallback_dot_path=FALLBACK_DOT_PATH)
    return val

# ---------------------------------- CLI --------------------------------------

def main():
    """
    Fast path by default:
      - digits ROI (PRIMARY_DOT_PATH): Otsu+close at 1.0x / 1.8x, both polarities.
      - if None, fallback ROI with the same variants.
    Heavy sweep:
      - only if both fast paths fail OR when --dump-candidates is provided.

    Time-based scoring:
      - Monotonic (never decreases).
      - Prefer near last_wave + rate_per_min * Δt(min) within ±tolerance.
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot-path", default=PRIMARY_DOT_PATH, help=f"Primary ROI (default {PRIMARY_DOT_PATH})")
    parser.add_argument("--fallback-dot-path", default=FALLBACK_DOT_PATH, help=f"Fallback ROI (default {FALLBACK_DOT_PATH})")
    parser.add_argument("--image", default=None, help="Path to an image to OCR (else capture via ADB)")
    parser.add_argument("--save-input", default=None, help="Save the raw input image here")
    parser.add_argument("--save-overlay", default=None, help="Save overlay for the ROI that produced the result (or primary if none)")
    parser.add_argument("--debug-out", default=None, help="Save winner bin image here")
    parser.add_argument("--verbose", action="store_true", help="Print detailed debug info")
    parser.add_argument("--dump-candidates", default=None, help="Directory to save heavy-sweep variants and OCR probes")
    parser.add_argument("--rate-per-min", type=float, default=_DEFAULT_RATE_PER_MIN, help="Expected waves per minute for time-based scoring")
    parser.add_argument("--tolerance", type=int, default=_DEFAULT_TOLERANCE, help="±window around expected value")
    parser.add_argument("--max-value", type=int, default=_DEFAULT_MAX_VALUE, help="Hard ceiling on accepted wave values")
    args = parser.parse_args()

    # load image
    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            print(f"Failed to read image at: {args.image}")
            return
    else:
        img = capture_adb_screenshot()
        if img is None:
            raise RuntimeError("Failed to capture screenshot.")

    if args.save_input:
        try:
            cv2.imwrite(args.save_input, img)
            print(f"[DEBUG] Saved input image to {args.save_input}")
        except Exception as e:
            print(f"[ERROR] Failed to save input image: {e}")

    if args.verbose:
        print(f"[DEBUG] Tesseract: {_tess_info()}")

    val, conf = detect_wave_number_from_image(
        img,
        primary_dot_path=args.dot_path,
        fallback_dot_path=args.fallback_dot_path,
        use_heavy=(args.dump_candidates is not None),
        verbose=args.verbose,
        dump_dir=args.dump_candidates,
        debug_out=args.debug_out,
        rate_per_min=args.rate_per_min,
        tolerance=args.tolerance,
        max_value=args.max_value,
    )

    if args.save_overlay:
        _save_overlay(img, args.dot_path, args.save_overlay)

    if val is None:
        print("Wave number: <not detected>")
    else:
        print(f"Wave number: {val} (conf={conf:.1f})")

if __name__ == "__main__":
    main()
