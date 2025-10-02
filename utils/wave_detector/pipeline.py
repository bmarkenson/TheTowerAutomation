"""Wave-detection OCR pipeline utilities."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from utils.logger import log
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.ss_capture import capture_adb_screenshot
from utils.ocr_utils import ocr_number_with_fallback

from .hint import get_hint_state, set_wave_hint

# ROIs: use the full label region as primary (more stable across UIs)
# The previous digits-only ROI appears misaligned in some setups.
PRIMARY_DOT_PATH = "_shared_match_regions.wave_number"
FALLBACK_DOT_PATH = ""  # disabled by default; can be overridden via args

# Preferences & limits
# Keep conservative ceiling to avoid selecting spurious large OCR reads
_DEFAULT_MAX_VALUE = 20000         # hard ceiling on accepted wave values
_DEFAULT_RATE_PER_MIN = 10.0       # expected waves per minute
_DEFAULT_TOLERANCE = 20            # ±window around expected


def _tess_info() -> str:
    """Return a human-readable summary of the pytesseract installation state."""

    try:
        import pytesseract
        try:
            v = pytesseract.get_tesseract_version()
            return f"pytesseract OK, tesseract {v}"
        except Exception as exc:
            return f"pytesseract OK, version check failed: {exc!r}"
    except Exception as exc:
        return f"pytesseract import FAILED: {exc!r}"


def _ocr_probe(gray_or_bin: np.ndarray, *, psm_text: int = 7, psm_digits: int = 7) -> Dict[str, Any]:
    """Collect diagnostic OCR artifacts for a given image binarization."""

    out: Dict[str, Any] = {}
    try:
        import pytesseract
    except Exception as exc:
        out["error"] = f"pytesseract import failed: {exc!r}"
        return out

    rgb = cv2.cvtColor(gray_or_bin, cv2.COLOR_GRAY2RGB)

    try:
        data_text = pytesseract.image_to_data(rgb, config=f"--psm {psm_text}", output_type=pytesseract.Output.DICT)
        toks = list(zip(data_text.get("text", []), data_text.get("conf", [])))
        out["image_to_data(psm_text)"] = {"n_tokens": len(toks), "tokens": toks[:50]}
    except Exception as exc:
        out["image_to_data(psm_text)"] = f"ERROR: {exc!r}"

    try:
        plain = pytesseract.image_to_string(rgb, config=f"--psm {psm_text}")
        out["image_to_string(psm_text)"] = plain
    except Exception as exc:
        out["image_to_string(psm_text)"] = f"ERROR: {exc!r}"

    try:
        data_digits = pytesseract.image_to_data(
            rgb,
            config=f"--psm {psm_digits} -c tessedit_char_whitelist=0123456789",
            output_type=pytesseract.Output.DICT,
        )
        d_toks = list(zip(data_digits.get("text", []), data_digits.get("conf", [])))
        out["image_to_data(digits)"] = {"n_tokens": len(d_toks), "tokens": d_toks[:50]}
    except Exception as exc:
        out["image_to_data(digits)"] = f"ERROR: {exc!r}"

    subs = []
    try:
        for text, conf in toks:
            if not text:
                continue
            for match in re.finditer(r"\d{1,9}", text):
                subs.append((match.group(0), conf))
    except Exception:
        pass
    out["numeric_substrings_from_tokens"] = subs[:50]
    return out


def _get_bbox(dot_path: str) -> Tuple[int, int, int, int]:
    """Resolve a clickmap dot-path into an (x, y, w, h) bounding box."""

    cm = get_clickmap()
    entry = resolve_dot_path(dot_path, cm)
    if not entry or "match_region" not in entry:
        raise KeyError(f"Missing match_region at dot_path: {dot_path}")
    region = entry["match_region"]
    return int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])


def _crop(img: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    """Clamp and crop a bounding box from the provided image."""

    x, y, w, h = bbox
    height, width = img.shape[:2]
    x2, y2 = min(x + w, width), min(y + h, height)
    x1, y1 = max(0, x), max(0, y)
    if x1 >= x2 or y1 >= y2:
        raise ValueError(f"Invalid crop bbox after clamping: {x1,y1,x2,y2}")
    return img[y1:y2, x1:x2]


def _save_overlay(img_bgr: np.ndarray, dot_path: str, out_path: str) -> None:
    """Persist an image annotated with the resolved ROI rectangle."""

    try:
        x, y, w, h = _get_bbox(dot_path)
        visual = img_bgr.copy()
        cv2.rectangle(visual, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.imwrite(out_path, visual)
        log(f"Saved overlay to {out_path}", "DEBUG")
    except Exception as exc:
        log(f"Failed to save overlay: {exc}", "ERROR")


def _fast_variants_from_crop(crop_bgr: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """Return fast binarisation variants for the given crop."""

    variants: List[Tuple[str, np.ndarray]] = []

    # White mask via HSV: low saturation, high value
    try:
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 0, 200), (180, 60, 255))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        variants.append(("white_mask_x1.0", mask))
        upscaled = cv2.resize(mask, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
        variants.append(("white_mask_x1.8", upscaled))
    except Exception:
        pass

    # Otsu baseline + polarity flip
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.6, beta=0)
    _threshold, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    base = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)

    for name, image in (("otsu_close", base), ("otsu_close_inv", cv2.bitwise_not(base))):
        variants.append((f"{name}_x1.0", image))
        upscaled = cv2.resize(image, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
        variants.append((f"{name}_x1.8", upscaled))
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
    """Rank OCR candidates for max() selection."""

    if val is None:
        return (1, 0, 0, -1.0, -1e9)

    if last_wave is not None and val < last_wave:
        return (0, 0, 0, -1.0, -1e9)

    valid_flag = 2 if val < max_value else 0

    if expected is None:
        prox_bucket = 1
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

    scale_hint: Optional[float] = None
    try:
        if expected is not None:
            scale_hint = float(expected)
    except Exception:
        scale_hint = None
    if scale_hint is None and last_wave is not None:
        try:
            scale_hint = float(last_wave)
        except Exception:
            scale_hint = None

    if scale_hint is not None:
        if scale_hint >= 1000:
            min_digits = 4
        elif scale_hint >= 100:
            min_digits = 3
        elif scale_hint >= 10:
            min_digits = 2
        else:
            min_digits = 1
        if digits_len < min_digits:
            valid_flag = min(valid_flag, 1)
            prox_bucket = min(prox_bucket, 0)

    return (valid_flag, prox_bucket, digits_len, float(conf), prox_tb)


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
    """Run the fast OCR path on the given ROI."""

    try:
        bbox = _get_bbox(dot_path)
    except Exception as exc:
        if verbose:
            log(f"Wave detector failed to resolve bbox for {dot_path}: {exc}", "DEBUG")
        return None, -1.0, None, None

    crop = _crop(img_bgr, bbox)
    if verbose:
        try:
            height, width = crop.shape[:2]
            log(f"FAST ROI {dot_path} bbox={bbox} crop={width}x{height}", "DEBUG")
        except Exception:
            pass
    best_val, best_conf, best_tag, best_img = None, -1.0, None, None
    best_score = _score(None, -1.0, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)

    for tag, variant in _fast_variants_from_crop(crop):
        val, conf, _ = ocr_number_with_fallback(variant, psm_digits=7, psm_text=7)
        cand_score = _score(val, conf, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)
        if verbose:
            height, width = variant.shape[:2]
            log(
                f"FAST candidate {dot_path}/{tag}: size={width}x{height} val={val} conf={conf} score={cand_score}",
                "DEBUG",
            )
        if cand_score > best_score:
            best_score, best_val, best_conf, best_tag, best_img = cand_score, val, conf, tag, variant.copy()

    if verbose:
        log(
            f"FAST best={best_tag} value={best_val} conf={best_conf} score={best_score}",
            "DEBUG",
        )
    return best_val, best_conf, best_tag and f"{dot_path}/{best_tag}", best_img


def _bins_from_crop(crop_bgr: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """Broader set of binarisations for difficult samples."""

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.6, beta=0)

    bins: List[Tuple[str, np.ndarray]] = []

    for name, method in [("mean", cv2.ADAPTIVE_THRESH_MEAN_C), ("gauss", cv2.ADAPTIVE_THRESH_GAUSSIAN_C)]:
        thresh = cv2.adaptiveThreshold(gray, 255, method, cv2.THRESH_BINARY, 31, 5)
        bins.append((name, thresh))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        bins.append((f"{name}_close", cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)))

    _threshold, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bins.append(("otsu", otsu))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    bins.append(("otsu_close", cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel, iterations=1)))

    inversions = []
    for name, bin_img in bins:
        inversions.append((f"{name}_inv", cv2.bitwise_not(bin_img)))
    bins.extend(inversions)
    return bins


def _scaled_variants(bin_img: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """Return scaled versions of a binary image to probe OCR robustness."""

    output: List[Tuple[str, np.ndarray]] = []
    for scale in (1.0, 1.8, 2.2):
        if scale == 1.0:
            output.append((f"x{scale:.1f}", bin_img))
        else:
            output.append((f"x{scale:.1f}", cv2.resize(bin_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)))
    return output


def _make_crops(full: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """Generate region crops (full and left trims) for stubborn samples."""

    height, width = full.shape[:2]
    left_trim = int(0.35 * width)
    right_shave = int(0.10 * width)
    crop_full = ("full", full)
    crop_left = ("left_trim", full[:, left_trim:] if left_trim < width - 1 else full)
    crop_combo = (
        "left_trim_right_shave",
        crop_left[1][:, : max(1, crop_left[1].shape[1] - right_shave)] if crop_left[1].shape[1] > 1 else crop_left[1],
    )
    return [crop_full, crop_left, crop_combo]


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
    """Enumerate heavyweight OCR binarisations and select the best candidate."""

    try:
        bbox = _get_bbox(dot_path)
    except Exception as exc:
        if verbose:
            log(f"Wave detector failed to resolve bbox for {dot_path}: {exc}", "DEBUG")
        return None, -1.0, None, None

    full = _crop(img_bgr, bbox)
    if verbose:
        log(f"HEAVY ROI {dot_path} bbox={bbox} crop={full.shape[1]}x{full.shape[0]}", "DEBUG")

    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        cv2.imwrite(os.path.join(dump_dir, f"{os.path.basename(dot_path)}_full_raw.png"), full)

    best_val, best_conf, best_tag, best_img = None, -1.0, None, None
    best_score = _score(None, -1.0, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)

    for crop_name, crop in _make_crops(full):
        for bin_name, bin_img in _bins_from_crop(crop):
            for scale_name, scaled in _scaled_variants(bin_img):
                tag = f"{crop_name}_{bin_name}_{scale_name}"
                val, conf, _ = ocr_number_with_fallback(scaled, psm_digits=7, psm_text=7)
                cand_score = _score(val, conf, last_wave=last_wave, expected=expected, tolerance=tolerance, max_value=max_value)

                if verbose:
                    height, width = scaled.shape[:2]
                    log(
                        f"HEAVY candidate {dot_path}/{tag}: size={width}x{height} val={val} conf={conf} score={cand_score}",
                        "DEBUG",
                    )

                if dump_dir:
                    cv2.imwrite(os.path.join(dump_dir, f"{os.path.basename(dot_path)}_{tag}.png"), scaled)
                    probes = _ocr_probe(scaled, psm_text=7, psm_digits=7)
                    with open(os.path.join(dump_dir, f"{os.path.basename(dot_path)}_{tag}.txt"), "w", encoding="utf-8") as fh:
                        fh.write(f"Tesseract: {_tess_info()}\n")
                        fh.write(
                            f"Variant: {tag} size={scaled.shape[1]}x{scaled.shape[0]} val={val} conf={conf} score={cand_score}\n"
                        )
                        fh.write(repr(probes))

                if cand_score > best_score:
                    best_score, best_val, best_conf, best_tag, best_img = (
                        cand_score,
                        val,
                        conf,
                        f"{dot_path}/{tag}",
                        scaled.copy(),
                    )

    if verbose:
        log(
            f"HEAVY best={best_tag} value={best_val} conf={best_conf} score={best_score}",
            "DEBUG",
        )
    if debug_out and best_img is not None:
        cv2.imwrite(debug_out, best_img)
    return best_val, best_conf, best_tag, best_img


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
    """Detect the wave number from a frame using fast and heavy OCR pipelines."""

    now = time.time()
    last_wave, last_ts = get_hint_state()
    expected = None
    if last_wave is not None and last_ts is not None:
        dt_min = max(0.0, (now - last_ts) / 60.0)
        expected = last_wave + rate_per_min * dt_min

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

    if (val is None or (val is not None and len(str(val)) <= 2)) and fallback_dot_path:
        if verbose:
            reason = "failed" if val is None else f"too-short({val})"
            log(
                f"Primary ROI {primary_dot_path} {reason}; trying fallback {fallback_dot_path}",
                "DEBUG",
            )
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
            if _score(
                hv_val,
                hv_conf,
                last_wave=last_wave,
                expected=expected,
                tolerance=tolerance,
                max_value=max_value,
            ) > _score(
                val,
                conf,
                last_wave=last_wave,
                expected=expected,
                tolerance=tolerance,
                max_value=max_value,
            ):
                val, conf, best_img, used = hv_val, hv_conf, hv_img, roi

    if debug_out and best_img is not None:
        cv2.imwrite(debug_out, best_img)

    if val is not None and last_wave is not None and last_ts is not None:
        dt_min = max(0.0, (now - last_ts) / 60.0)
        near = True if expected is None else (abs(val - expected) <= 2 * tolerance)
        allowed_inc = 3.0 * rate_per_min * dt_min + (2 * tolerance)
        massive_jump = (val - last_wave) > allowed_inc
        low_conf = conf < 60.0
        too_short = (last_wave >= 1000 and len(str(val)) < 4) or (100 <= last_wave < 1000 and len(str(val)) < 3)
        suspicious = (not near and (massive_jump or low_conf)) or too_short
        if suspicious:
            hv_best: Tuple[Optional[int], float, Optional[str], Optional[np.ndarray]] = (None, -1.0, None, None)
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
                if _score(
                    hv_val,
                    hv_conf,
                    last_wave=last_wave,
                    expected=expected,
                    tolerance=tolerance,
                    max_value=max_value,
                ) > _score(
                    hv_best[0],
                    hv_best[1],
                    last_wave=last_wave,
                    expected=expected,
                    tolerance=tolerance,
                    max_value=max_value,
                ):
                    hv_best = (hv_val, hv_conf, roi, hv_img)

            hv_val, hv_conf, hv_used, hv_img = hv_best
            if hv_val is not None:
                hv_near = True if expected is None else (abs(hv_val - expected) <= 2 * tolerance)
                hv_massive_jump = (hv_val - last_wave) > allowed_inc
                hv_too_short = (
                    (last_wave >= 1000 and len(str(hv_val)) < 4)
                    or (100 <= last_wave < 1000 and len(str(hv_val)) < 3)
                )
                hv_suspicious = (not hv_near and (hv_massive_jump or hv_conf < 60.0)) or hv_too_short
                if not hv_suspicious:
                    val, conf = hv_val, hv_conf
                    best_img = hv_img if hv_img is not None else best_img
                    used = hv_used or used
                else:
                    return last_wave, conf

    if val is not None:
        set_wave_hint(val, ts=now)

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
    """Capture via ADB and run wave detection."""

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
    """Convenience wrapper returning only the detected wave number."""

    val, _conf = detect_wave_number(primary_dot_path=dot_path, fallback_dot_path=FALLBACK_DOT_PATH)
    return val


def get_wave_number_from_image(img_bgr: np.ndarray, dot_path: str = PRIMARY_DOT_PATH) -> Optional[int]:
    """Convenience wrapper returning only the detected wave number from an image."""

    val, _conf = detect_wave_number_from_image(
        img_bgr,
        primary_dot_path=dot_path,
        fallback_dot_path=FALLBACK_DOT_PATH,
    )
    return val


__all__ = [
    "FALLBACK_DOT_PATH",
    "PRIMARY_DOT_PATH",
    "_DEFAULT_MAX_VALUE",
    "_DEFAULT_RATE_PER_MIN",
    "_DEFAULT_TOLERANCE",
    "_save_overlay",
    "_tess_info",
    "detect_wave_number",
    "detect_wave_number_from_image",
    "get_wave_number",
    "get_wave_number_from_image",
]
