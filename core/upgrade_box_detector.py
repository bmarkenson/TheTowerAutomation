from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import os
import re
from typing import Dict, List, Optional, Tuple

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
_ULTIMATE_TOGGLE_BRIGHT_RATIO = 0.22
_ULTIMATE_TOGGLE_VAL_THRESHOLD = 118.0
_ULTIMATE_SECONDARY_BRIGHT_RATIO = 0.25
_ULTIMATE_SECONDARY_VAL_THRESHOLD = 118.0


def _get_column_rect(column: str) -> Tuple[int, int, int, int]:
    entry = resolve_dot_path(f"_shared_match_regions.upgrades_{column}")
    if not entry or "match_region" not in entry:
        raise RuntimeError(f"Missing column region for {column}")
    region = entry["match_region"]
    return int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])


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


def _evaluate_ultimate_switches(
    image: np.ndarray,
    rect: Tuple[int, int, int, int],
    label: Optional[str],
    raw_text: Optional[str],
) -> Tuple[Dict[str, str], Dict[str, Dict[str, float]]]:
    x, y, w, h = rect
    toggles: Dict[str, str] = {}
    metrics: Dict[str, Dict[str, float]] = {}

    primary_region = image[
        y + int(h * 0.78) : y + int(h * 0.98),
        x + int(w * 0.06) : x + int(w * 0.33),
    ]
    if primary_region.size:
        hsv = cv2.cvtColor(primary_region, cv2.COLOR_BGR2HSV)
        val = hsv[:, :, 2].astype(np.float32)
        bright_mask = val > 170
        mean_val = float(val.mean()) if val.size else 0.0
        bright_ratio = float(bright_mask.mean()) if bright_mask.size else 0.0
        metrics["primary"] = {
            "mean_val": mean_val,
            "bright_ratio": bright_ratio,
        }
        toggles["primary"] = (
            "on"
            if bright_ratio >= _ULTIMATE_TOGGLE_BRIGHT_RATIO
            or mean_val >= _ULTIMATE_TOGGLE_VAL_THRESHOLD
            else "off"
        )

    if label and label.lower() == "spotlight":
        missiles_region = image[
            y + int(h * 0.78) : y + int(h * 0.92),
            x + int(w * 0.50) : x + int(w * 0.68),
        ]
        if missiles_region.size:
            hsv = cv2.cvtColor(missiles_region, cv2.COLOR_BGR2HSV)
            val = hsv[:, :, 2].astype(np.float32)
            bright_mask = val > 170
            mean_val = float(val.mean()) if val.size else 0.0
            bright_ratio = float(bright_mask.mean()) if bright_mask.size else 0.0
            metrics["missiles"] = {
                "mean_val": mean_val,
                "bright_ratio": bright_ratio,
            }
            toggles["missiles"] = (
                "on"
                if bright_ratio >= _ULTIMATE_SECONDARY_BRIGHT_RATIO
                or mean_val >= _ULTIMATE_SECONDARY_VAL_THRESHOLD
                else "off"
            )

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


def detect_visible_boxes(
    screenshot: Optional[np.ndarray] = None,
    *,
    menu: Optional[str] = None,
) -> Dict[str, List[UpgradeBox]]:
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

    left_rect = _get_column_rect("left")
    right_rect = _get_column_rect("right")
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
