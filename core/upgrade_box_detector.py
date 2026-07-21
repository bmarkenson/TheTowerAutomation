from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import os
import re
from typing import Dict, List, Mapping, Optional, Tuple

import cv2
import numpy as np

from core.clickmap_access import resolve_dot_path
from core.ss_capture import capture_adb_screenshot
from utils.ocr_utils import ocr_text_and_conf


@dataclass
class UpgradeBox:
    column: str
    rect: Tuple[int, int, int, int]
    text: Optional[str] = None
    confidence: float = -1.0
    raw_text: Optional[str] = None
    match_score: Optional[float] = None
    affordability: Optional[str] = None
    affordability_metrics: Optional[Dict[str, float]] = None
    toggles: Optional[Dict[str, str]] = None
    toggle_metrics: Optional[Dict[str, Dict[str, float]]] = None


_BRIGHT_THRESHOLD = 200
_ROW_MIN_FRACTION = 0.4
_COLUMN_MIN_FRACTION = 0.4
_MERGE_GAP = 22
_MIN_TILE_HEIGHT = 170
_MAX_TILE_HEIGHT = 260
_MAX_BAND_THICKNESS = 60
_EXPAND_X = 6
_EXPAND_Y = 6
_LEFT_TEXT_FRACTION = 0.55
_TEXT_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
_CLAHE = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
_RIGHT_INFO_FRACTION = 0.55
_AFF_BRIGHT_THRESHOLD = 120
_GOLD_BOX_REGION_START = 0.55
_GOLD_BOX_HUE_RANGE = (12, 38)
_GOLD_BOX_MIN_SATURATION = 90
_GOLD_BOX_MIN_VALUE = 90
_GOLD_BOX_MIN_PIXEL_RATIO = 0.04
_GOLD_BOX_MIN_ROW_RATIO = 0.60
_GOLD_BOX_MIN_COLUMN_RATIO = 0.45
_MENU_ALIASES = {
    "attack_menu": "attack",
    "attack": "attack",
    "defense_menu": "defense",
    "defense": "defense",
    "utility_menu": "utility",
    "utility": "utility",
    "uw_menu": "ultimate weapons",
    "ultimate weapons": "ultimate weapons",
}
ULTIMATE_PRIMARY_TOGGLE_REGION = ((0.05, 0.23), (0.67, 0.92))
ULTIMATE_SECONDARY_TOGGLE_REGION = ((0.42, 0.60), (0.67, 0.92))
ULTIMATE_TOGGLE_SAT_THRESHOLD = 120.0
ULTIMATE_TOGGLE_SAT_RATIO_THRESHOLD = 0.75
_ULTIMATE_TOGGLE_BRIGHT_VALUE = 170

ColumnRegions = Mapping[str, Tuple[int, int, int, int]]


def _get_column_rect(column: str) -> Tuple[int, int, int, int]:
    entry = resolve_dot_path(f"_shared_match_regions.upgrades_{column}")
    if not entry or "match_region" not in entry:
        raise RuntimeError(f"Missing column region for {column}")
    region = entry["match_region"]
    return int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])


def _resolve_column_rect(
    column: str,
    column_regions: Optional[ColumnRegions],
) -> Tuple[int, int, int, int]:
    if column_regions is None:
        return _get_column_rect(column)
    if column not in column_regions:
        raise ValueError(f"Missing scan region for {column} upgrade column")
    values = tuple(column_regions[column])
    if len(values) != 4:
        raise ValueError(f"Invalid scan region for {column} upgrade column")
    x, y, width, height = (int(value) for value in values)
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError(f"Invalid scan region for {column} upgrade column")
    return x, y, width, height


def _to_bright_mask(roi: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    mask = cv2.inRange(blur, _BRIGHT_THRESHOLD, 255)
    return (mask > 0).astype(np.uint8)


def _find_horizontal_bands(mask: np.ndarray, width: int) -> List[Tuple[int, int]]:
    row_counts = mask.sum(axis=1)
    threshold = max(1, int(_ROW_MIN_FRACTION * width))

    raw_bands: List[Tuple[int, int]] = []
    start: Optional[int] = None
    end: Optional[int] = None
    for idx, count in enumerate(row_counts):
        if count >= threshold:
            if start is None:
                start = idx
            end = idx
        else:
            if start is not None and end is not None:
                raw_bands.append((start, end))
            start = None
            end = None
    if start is not None and end is not None:
        raw_bands.append((start, end))

    merged: List[Tuple[int, int]] = []
    for band in raw_bands:
        if merged and band[0] - merged[-1][1] <= _MERGE_GAP:
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)

    return [band for band in merged if band[1] - band[0] <= _MAX_BAND_THICKNESS]


def _bands_to_rects(
    mask: np.ndarray,
    origin: Tuple[int, int],
    width: int,
    bands: List[Tuple[int, int]],
) -> List[Tuple[int, int, int, int]]:
    if len(bands) < 2:
        return []

    origin_x, origin_y = origin
    height_limit = mask.shape[0] - 1
    rects: List[Tuple[int, int, int, int]] = []

    for idx in range(len(bands) - 1):
        top_band = bands[idx]
        bottom_band = bands[idx + 1]

        top_center = 0.5 * (top_band[0] + top_band[1])
        bottom_center = 0.5 * (bottom_band[0] + bottom_band[1])
        approx_height = bottom_center - top_center
        if not (_MIN_TILE_HEIGHT <= approx_height <= _MAX_TILE_HEIGHT):
            continue

        top = max(0, top_band[0] - _EXPAND_Y)
        bottom = min(height_limit, bottom_band[1] + _EXPAND_Y)
        tile_height = bottom - top + 1
        if tile_height < _MIN_TILE_HEIGHT or tile_height > _MAX_TILE_HEIGHT + 2 * _EXPAND_Y:
            continue

        tile_slice = mask[top : bottom + 1, :]
        if tile_slice.size == 0:
            continue

        col_counts = tile_slice.sum(axis=0)
        col_threshold = max(1, int(_COLUMN_MIN_FRACTION * tile_slice.shape[0]))
        cols = np.where(col_counts >= col_threshold)[0]
        if cols.size < 2:
            continue

        left = max(0, cols[0] - _EXPAND_X)
        right = min(width - 1, cols[-1] + _EXPAND_X)
        if right <= left:
            continue

        rects.append(
            (
                int(origin_x + left),
                int(origin_y + top),
                int(right - left + 1),
                int(tile_height),
            )
        )

    return rects


@lru_cache(maxsize=1)
def _load_upgrade_manifest() -> Dict[str, Dict[str, List[str]]]:
    manifest_path = os.path.join("config", "upgrade_manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    upgrades = payload.get("upgrades", {})
    normalized: Dict[str, Dict[str, List[str]]] = {}
    for menu, columns in upgrades.items():
        normalized[menu.lower()] = {
            column.lower(): list(values)
            for column, values in columns.items()
        }
    return normalized


def _normalize_menu_name(menu: Optional[str]) -> Optional[str]:
    if not menu:
        return None
    key = menu.lower()
    return _MENU_ALIASES.get(key, key)


def _expected_labels(menu: Optional[str], column: str) -> List[str]:
    manifest = _load_upgrade_manifest()
    column = column.lower()
    labels: List[str] = []

    normalized_menu = _normalize_menu_name(menu)
    if normalized_menu:
        menu_labels = manifest.get(normalized_menu)
        if menu_labels:
            labels.extend(menu_labels.get(column, []))

    if not labels:
        for columns in manifest.values():
            labels.extend(columns.get(column, []))

    return labels


def _canonicalize_label(
    raw_text: Optional[str],
    menu: Optional[str],
    column: str,
) -> Tuple[Optional[str], Optional[float]]:
    if not raw_text:
        return None, None

    candidates = _expected_labels(menu, column)
    if not candidates:
        return raw_text, None

    raw_norm = raw_text.strip().lower()
    best_label: Optional[str] = None
    best_score = 0.0

    for candidate in candidates:
        candidate_norm = candidate.lower()
        if raw_norm == candidate_norm:
            return candidate, 1.0

        if raw_norm in candidate_norm or candidate_norm in raw_norm:
            score = (len(raw_norm) / len(candidate_norm)) if len(candidate_norm) >= len(raw_norm) else (len(candidate_norm) / len(raw_norm))
            score = max(score, SequenceMatcher(None, raw_norm, candidate_norm).ratio())
        else:
            score = SequenceMatcher(None, raw_norm, candidate_norm).ratio()

        if score > best_score:
            best_label = candidate
            best_score = score

    if best_label and best_score >= 0.6:
        return best_label, best_score

    return raw_text, best_score if best_score > 0 else None


def _clean_ocr_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("|", "I").replace("\n", " ")
    raw_tokens = re.findall(r"[A-Za-z0-9]+", text)

    merged_tokens: List[str] = []
    i = 0
    while i < len(raw_tokens):
        token = raw_tokens[i]
        if (
            len(token) == 1
            and token.isalpha()
            and i + 1 < len(raw_tokens)
            and raw_tokens[i + 1].isalpha()
            and raw_tokens[i + 1].islower()
        ):
            next_token = raw_tokens[i + 1]
            combined = token + next_token
            merged_tokens.append(combined)
            i += 2
            continue

        merged_tokens.append(token)
        i += 1

    tokens: List[str] = []
    for token in merged_tokens:
        upper = token.upper()
        if len(upper) == 1:
            continue
        if len(set(upper)) == 1 and len(upper) <= 3:
            continue
        if len(token) <= 2 and not token.isupper():
            continue
        tokens.append(token)

    while len(tokens) > 1 and tokens[0].isupper() and not tokens[1].isupper():
        tokens.pop(0)

    while len(tokens) > 1:
        token = tokens[0]
        upper_token = token.upper()
        counts = {ch: upper_token.count(ch) for ch in set(upper_token)}
        dominant = max(counts.values())
        if dominant / len(token) >= 0.7:
            tokens.pop(0)
        else:
            break

    return " ".join(tokens)


def _extract_label_text(image: np.ndarray, rect: Tuple[int, int, int, int]) -> Tuple[Optional[str], float]:
    x, y, w, h = rect
    crop = image[y : y + h, x : x + w]
    if crop.size == 0:
        return None, -1.0

    left_w = max(1, int(w * _LEFT_TEXT_FRACTION))
    text_region = crop[:, :left_w]

    gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
    enhanced = _CLAHE.apply(gray)
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = cv2.bitwise_not(binary)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, _TEXT_KERNEL, iterations=1)

    text, confidence = ocr_text_and_conf(binary, psm=6)
    cleaned = _clean_ocr_text(text)

    if cleaned:
        return cleaned, confidence

    # Fallback: adaptive threshold that can help when Otsu fails.
    adaptive = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        31,
        -5,
    )
    adaptive = cv2.bitwise_not(adaptive)
    adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, _TEXT_KERNEL, iterations=1)
    text, confidence = ocr_text_and_conf(adaptive, psm=6)
    cleaned = _clean_ocr_text(text)
    if cleaned:
        return cleaned, confidence

    return None, confidence


def _evaluate_affordability(image: np.ndarray, rect: Tuple[int, int, int, int]) -> Tuple[Optional[str], Dict[str, float]]:
    x, y, w, h = rect
    right_start = int(w * _LEFT_TEXT_FRACTION)
    right_region = image[y : y + h, x + right_start : x + w]
    if right_region.size == 0:
        return None, {}

    bottom_start = int(right_region.shape[0] * _RIGHT_INFO_FRACTION)
    bottom_region = right_region[bottom_start:]
    if bottom_region.size == 0:
        bottom_region = right_region

    hsv = cv2.cvtColor(bottom_region, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)

    bright_mask = val > _AFF_BRIGHT_THRESHOLD
    bright_ratio = float(bright_mask.mean()) if bright_mask.size else 0.0
    avg_val = float(val.mean()) if val.size else 0.0
    avg_sat = float(sat.mean()) if sat.size else 0.0
    bright_sat = float(sat[bright_mask].mean()) if bright_mask.any() else 0.0
    bright_val = float(val[bright_mask].mean()) if bright_mask.any() else 0.0

    metrics = {
        "avg_val": avg_val,
        "avg_sat": avg_sat,
        "bright_ratio": bright_ratio,
        "bright_sat": bright_sat,
        "bright_val": bright_val,
    }

    if bright_sat < 40.0:
        return "maxed", metrics

    if avg_val < 105.0 and bright_ratio < 0.20:
        return "unaffordable", metrics

    if avg_val >= 110.0 or bright_ratio >= 0.20:
        return "affordable", metrics

    return "unaffordable", metrics


def evaluate_upgrade_box_gold_box(
    image: np.ndarray,
    rect: Tuple[int, int, int, int],
) -> Tuple[bool, Dict[str, float]]:
    """Detect the rectangular gold border shown when an upgrade is Max.

    The detector intentionally examines only the lower-right purchase control.
    This is substantially cheaper and less ambiguous than OCR or the general
    affordability classifier, and it remains valid when an upgrade starts a
    run already gold boxed.
    """

    region = _extract_relative_region(
        image,
        rect,
        (_GOLD_BOX_REGION_START, 1.0),
        (_GOLD_BOX_REGION_START, 1.0),
    )
    if region is None or region.size == 0:
        return False, {}

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    gold_mask = (
        (hue >= _GOLD_BOX_HUE_RANGE[0])
        & (hue <= _GOLD_BOX_HUE_RANGE[1])
        & (saturation >= _GOLD_BOX_MIN_SATURATION)
        & (value >= _GOLD_BOX_MIN_VALUE)
    )

    pixel_ratio = float(gold_mask.mean())
    row_ratio = float(gold_mask.mean(axis=1).max())
    column_ratio = float(gold_mask.mean(axis=0).max())
    metrics = {
        "gold_pixel_ratio": pixel_ratio,
        "gold_row_ratio": row_ratio,
        "gold_column_ratio": column_ratio,
    }
    is_gold_boxed = (
        pixel_ratio >= _GOLD_BOX_MIN_PIXEL_RATIO
        and row_ratio >= _GOLD_BOX_MIN_ROW_RATIO
        and column_ratio >= _GOLD_BOX_MIN_COLUMN_RATIO
    )
    return is_gold_boxed, metrics


def _extract_relative_region(
    image: np.ndarray,
    rect: Tuple[int, int, int, int],
    x_bounds: Tuple[float, float],
    y_bounds: Tuple[float, float],
) -> Optional[np.ndarray]:
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None

    img_h, img_w = image.shape[:2]

    def _clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(value, maximum))

    start_x = x + int(round(w * x_bounds[0]))
    end_x = x + int(round(w * x_bounds[1]))
    start_y = y + int(round(h * y_bounds[0]))
    end_y = y + int(round(h * y_bounds[1]))

    start_x = _clamp(start_x, 0, img_w - 1)
    end_x = _clamp(end_x, 0, img_w)
    start_y = _clamp(start_y, 0, img_h - 1)
    end_y = _clamp(end_y, 0, img_h)

    if end_x <= start_x:
        end_x = _clamp(start_x + 4, start_x + 1, img_w)
    if end_y <= start_y:
        end_y = _clamp(start_y + 4, start_y + 1, img_h)

    if end_x <= start_x or end_y <= start_y:
        return None

    return image[start_y:end_y, start_x:end_x]


def _classify_toggle_region(
    region: np.ndarray,
) -> Tuple[str, Dict[str, float]]:
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)

    bright_mask = val > _ULTIMATE_TOGGLE_BRIGHT_VALUE
    sat_mask = sat > ULTIMATE_TOGGLE_SAT_THRESHOLD

    mean_val = float(val.mean()) if val.size else 0.0
    mean_sat = float(sat.mean()) if sat.size else 0.0
    bright_ratio = float(bright_mask.mean()) if bright_mask.size else 0.0
    sat_ratio = float(sat_mask.mean()) if sat_mask.size else 0.0

    metrics = {
        "mean_val": mean_val,
        "mean_sat": mean_sat,
        "bright_ratio": bright_ratio,
        "sat_ratio": sat_ratio,
    }

    is_on = (
        mean_sat >= ULTIMATE_TOGGLE_SAT_THRESHOLD
        or sat_ratio >= ULTIMATE_TOGGLE_SAT_RATIO_THRESHOLD
    )

    return ("on" if is_on else "off"), metrics


def _evaluate_ultimate_switches(
    image: np.ndarray,
    rect: Tuple[int, int, int, int],
    label: Optional[str],
    raw_text: Optional[str],
) -> Tuple[Dict[str, str], Dict[str, Dict[str, float]]]:
    x, y, w, h = rect
    toggles: Dict[str, str] = {}
    metrics: Dict[str, Dict[str, float]] = {}

    primary_region = _extract_relative_region(
        image,
        rect,
        ULTIMATE_PRIMARY_TOGGLE_REGION[0],
        ULTIMATE_PRIMARY_TOGGLE_REGION[1],
    )
    if primary_region is not None and primary_region.size:
        state, primary_metrics = _classify_toggle_region(primary_region)
        metrics["primary"] = primary_metrics
        toggles["primary"] = state

    if label and label.lower() == "spotlight":
        missiles_region = _extract_relative_region(
            image,
            rect,
            ULTIMATE_SECONDARY_TOGGLE_REGION[0],
            ULTIMATE_SECONDARY_TOGGLE_REGION[1],
        )
        if missiles_region is not None and missiles_region.size:
            state, missiles_metrics = _classify_toggle_region(missiles_region)
            metrics["missiles"] = missiles_metrics
            toggles["missiles"] = state

    return toggles, metrics


def _detect_boxes_for_column(
    image: np.ndarray,
    rect: Tuple[int, int, int, int],
    column: str,
    menu: Optional[str],
) -> List[UpgradeBox]:
    x, y, w, h = rect
    roi = image[y : y + h, x : x + w]
    mask = _to_bright_mask(roi)
    bands = _find_horizontal_bands(mask, w)
    rects = _bands_to_rects(mask, (x, y), w, bands)

    boxes: List[UpgradeBox] = []
    normalized_menu = _normalize_menu_name(menu)
    for rect in rects:
        raw_text, confidence = _extract_label_text(image, rect)
        canonical_text, match_score = _canonicalize_label(
            raw_text, normalized_menu, column
        )

        affordability: Optional[str] = None
        affordability_metrics: Optional[Dict[str, float]] = None
        toggles: Optional[Dict[str, str]] = None
        toggle_metrics: Optional[Dict[str, Dict[str, float]]] = None

        if normalized_menu == "ultimate weapons":
            toggles, toggle_metrics = _evaluate_ultimate_switches(
                image, rect, canonical_text, raw_text
            )
        else:
            affordability, affordability_metrics = _evaluate_affordability(
                image, rect
            )

        boxes.append(
            UpgradeBox(
                column=column,
                rect=rect,
                text=canonical_text,
                confidence=confidence,
                raw_text=raw_text,
                match_score=match_score,
                affordability=affordability,
                affordability_metrics=affordability_metrics,
                toggles=toggles,
                toggle_metrics=toggle_metrics,
            )
        )
    boxes.sort(key=lambda item: item.rect[1])
    return boxes


def detect_visible_box_rects(
    screenshot: np.ndarray,
    *,
    column_regions: Optional[ColumnRegions] = None,
) -> Dict[str, List[Tuple[int, int, int, int]]]:
    """Detect visible tile geometry in the default or supplied column regions."""

    detected: Dict[str, List[Tuple[int, int, int, int]]] = {}
    for column in ("left", "right"):
        x, y, w, h = _resolve_column_rect(column, column_regions)
        roi = screenshot[y : y + h, x : x + w]
        mask = _to_bright_mask(roi)
        bands = _find_horizontal_bands(mask, w)
        detected[column] = _bands_to_rects(mask, (x, y), w, bands)
    return detected


def detect_visible_boxes(
    screenshot: Optional[np.ndarray] = None,
    *,
    menu: Optional[str] = None,
    column_regions: Optional[ColumnRegions] = None,
) -> Dict[str, List[UpgradeBox]]:
    """Detect and OCR upgrade tiles in the default or supplied column regions."""

    if screenshot is None:
        screenshot = capture_adb_screenshot()
        if screenshot is None:
            raise RuntimeError("Failed to capture screenshot")

    resolved_menu = menu
    if resolved_menu is None:
        try:
            from core.state_detector import detect_state_and_overlays

            detection = detect_state_and_overlays(screenshot)
            resolved_menu = detection.get("menu") or None
        except Exception:
            resolved_menu = None

    left_rect = _resolve_column_rect("left", column_regions)
    right_rect = _resolve_column_rect("right", column_regions)
    return {
        "left": _detect_boxes_for_column(screenshot, left_rect, "left", resolved_menu),
        "right": _detect_boxes_for_column(screenshot, right_rect, "right", resolved_menu),
    }


def annotate_boxes(image: np.ndarray, boxes: List[UpgradeBox]) -> np.ndarray:
    annotated = image.copy()
    for box in boxes:
        color = (0, 255, 0) if box.column == "left" else (255, 0, 0)
        x, y, w, h = box.rect
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
    return annotated
