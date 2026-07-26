"""Ordered OCR capture for the completed battle's Selected Perks list."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

try:
    import pytesseract
except Exception:  # pragma: no cover - exercised through the unavailable path
    pytesseract = None


Frame = np.ndarray
PerkTextFn = Callable[[Frame], tuple[str, float]]

PERKS_X1 = 107
PERKS_X2 = 973
PERKS_SCAN_TOP = 350
PERKS_SCAN_BOTTOM = 1780
PERKS_SAFE_TOP = 420
PERKS_SAFE_BOTTOM = 1755
PERK_TEXT_X1 = 270
PERK_TEXT_X2 = 950
MIN_FULL_ROW_HEIGHT = 150
DEFAULT_CONFIDENCE_THRESHOLD = 80.0
CONFIGURATION_ROW_X1 = 239
CONFIGURATION_ROW_X2 = 840
CONFIGURATION_TEXT_X1 = 400
CONFIGURATION_SCAN_TOP = 410
CONFIGURATION_SCAN_BOTTOM = 1780
CONFIGURATION_MIN_ROW_HEIGHT = 120
CONFIGURATION_MIN_BACKGROUND_VALUE = 55


def ocr_perk_rows(
    frame: Frame,
    *,
    text_fn: Optional[PerkTextFn] = None,
) -> list[dict[str, Any]]:
    """OCR visible perk tiles and retain their background brightness evidence."""

    recognize = text_fn or _ocr_perk_text
    rows = []
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    for top, bottom in _perk_row_regions(frame):
        crop = frame[top : bottom + 1, PERK_TEXT_X1:PERK_TEXT_X2]
        raw_text, confidence = recognize(crop)
        raw_text = " ".join(str(raw_text or "").split())
        if not raw_text:
            continue
        background = hsv[top : bottom + 1, PERKS_X1:PERKS_X2, 2]
        rows.append(
            {
                "top": top,
                "bottom": bottom,
                "text_raw": raw_text,
                "display_text": _normalize_perk_ocr(raw_text),
                "key": _slug(_normalize_perk_ocr(raw_text)),
                "confidence": round(float(confidence), 1),
                "background_value_median": float(np.median(background)),
            }
        )
    return rows


def ocr_perk_configuration_rows(
    frame: Frame,
    *,
    text_fn: Optional[PerkTextFn] = None,
) -> list[dict[str, Any]]:
    """OCR Home-configuration rows, including low-saturation blue tiles.

    The in-battle scanner intentionally requires saturated perk colors. Home
    Ban Perks and Auto Pick lists also contain pale blue rows, so their row
    geometry is recovered from the bright center tile instead.
    """

    recognize = text_fn or _ocr_perk_text
    rows = []
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    for top, bottom in _perk_configuration_row_regions(frame):
        raw_text, confidence, text_candidates = _recognize_configuration_text(
            frame,
            top,
            bottom,
            recognize,
        )
        raw_text = " ".join(str(raw_text or "").split())
        if not raw_text:
            continue
        display_text = _normalize_perk_ocr(raw_text)
        background = hsv[
            top : bottom + 1,
            CONFIGURATION_ROW_X1:CONFIGURATION_ROW_X2,
            2,
        ]
        rows.append(
            {
                "top": top,
                "bottom": bottom,
                "text_raw": raw_text,
                "display_text": display_text,
                "key": _slug(display_text),
                "confidence": round(float(confidence), 1),
                "background_value_median": float(np.median(background)),
                "text_candidates": text_candidates,
            }
        )
    return rows


def ocr_perk_configuration_row_near(
    frame: Frame,
    y: int,
    *,
    tolerance: int = 100,
    text_fn: Optional[PerkTextFn] = None,
) -> Optional[dict[str, Any]]:
    """OCR only the configuration row nearest one expected vertical center."""

    regions = _perk_configuration_row_regions(frame)
    if not regions:
        return None
    target = int(y)
    top, bottom = min(
        regions,
        key=lambda region: abs(((region[0] + region[1]) // 2) - target),
    )
    if abs(((top + bottom) // 2) - target) > max(0, int(tolerance)):
        return None
    recognize = text_fn or _ocr_perk_text
    raw_text, confidence, text_candidates = _recognize_configuration_text(
        frame,
        top,
        bottom,
        recognize,
    )
    raw_text = " ".join(str(raw_text or "").split())
    if not raw_text:
        return None
    display_text = _normalize_perk_ocr(raw_text)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    background = hsv[
        top : bottom + 1,
        CONFIGURATION_ROW_X1:CONFIGURATION_ROW_X2,
        2,
    ]
    return {
        "top": top,
        "bottom": bottom,
        "text_raw": raw_text,
        "display_text": display_text,
        "key": _slug(display_text),
        "confidence": round(float(confidence), 1),
        "background_value_median": float(np.median(background)),
        "text_candidates": text_candidates,
    }


def ocr_selected_perks(
    frames: Sequence[Frame],
    *,
    source_complete: bool,
    source_reason: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    text_fn: Optional[PerkTextFn] = None,
) -> dict[str, Any]:
    """Extract ordered aggregate perk entries from overlapping viewports.

    The game displays the most recently selected perk first. A leveled blue
    perk moves to the top whenever another instance is selected, while green
    and purple perks have only one instance. The resulting order therefore
    records latest-selection recency, not the original first-selection order.
    """

    if not frames:
        return _empty_perks(source_reason or "no_frames")

    recognize = text_fn or _ocr_perk_text
    selected: list[dict[str, Any]] = []
    raw_pages: list[str] = []
    for viewport, frame in enumerate(frames, start=1):
        page_text: list[str] = []
        for top, bottom in _perk_row_regions(frame):
            crop = frame[top : bottom + 1, PERK_TEXT_X1:PERK_TEXT_X2]
            raw_text, confidence = recognize(crop)
            raw_text = " ".join(str(raw_text or "").split())
            if not raw_text:
                continue
            display_text = _normalize_perk_ocr(raw_text)
            color = _perk_color(frame, top, bottom)
            observation = {
                "viewport": viewport,
                "top": top,
                "text_raw": raw_text,
                "confidence": round(float(confidence), 1),
            }
            page_text.append(raw_text)
            duplicate = _find_duplicate(selected, display_text, color)
            if duplicate is not None:
                duplicate["observations"].append(observation)
                if float(confidence) > float(duplicate["confidence"]):
                    duplicate["text_raw"] = raw_text
                    duplicate["display_text"] = display_text
                    duplicate["confidence"] = round(float(confidence), 1)
                continue

            selected.append(
                {
                    "display_text": display_text,
                    "text_raw": raw_text,
                    "key": _slug(display_text),
                    "color": color,
                    "instance_model": (
                        "leveled" if color == "blue" else "single_instance"
                    ),
                    "confidence": round(float(confidence), 1),
                    "observations": [observation],
                }
            )
        raw_pages.append(f"[viewport {viewport}]\n" + "\n".join(page_text))

    for order, perk in enumerate(selected, start=1):
        perk["latest_selection_rank"] = order

    low_confidence = [
        perk["key"]
        for perk in selected
        if float(perk["confidence"]) < float(confidence_threshold)
    ]
    unknown_colors = [perk["key"] for perk in selected if perk["color"] == "unknown"]
    warnings: list[str] = []
    if not source_complete:
        warnings.append(f"Perks capture was incomplete: {source_reason}")
    if not selected:
        warnings.append("No selected perks were recognized")
    if low_confidence:
        warnings.append("Low-confidence perks: " + ", ".join(low_confidence))
    if unknown_colors:
        warnings.append("Unclassified perk colors: " + ", ".join(unknown_colors))
    valid = bool(
        source_complete
        and selected
        and not low_confidence
        and not unknown_colors
    )
    return {
        "source_method": "ocr",
        "page_count": len(frames),
        "order_semantics": "latest_selected_first",
        "leveled_perk_semantics": "moves_to_front_when_latest_level_is_selected",
        "selected": selected,
        "raw_text": "\n\n".join(raw_pages),
        "quality": {
            "valid": valid,
            "source_complete": bool(source_complete),
            "source_reason": source_reason,
            "confidence_threshold": float(confidence_threshold),
            "perk_count": len(selected),
            "low_confidence_perks": low_confidence,
            "unknown_color_perks": unknown_colors,
            "warnings": warnings,
            "retain_source_images": not valid,
        },
    }


def _empty_perks(reason: str) -> dict[str, Any]:
    warning = "No Perks frames were captured"
    return {
        "source_method": "ocr",
        "page_count": 0,
        "order_semantics": "latest_selected_first",
        "leveled_perk_semantics": "moves_to_front_when_latest_level_is_selected",
        "selected": [],
        "raw_text": "",
        "quality": {
            "valid": False,
            "source_complete": False,
            "source_reason": reason,
            "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
            "perk_count": 0,
            "low_confidence_perks": [],
            "unknown_color_perks": [],
            "warnings": [warning],
            "retain_source_images": True,
        },
    }


def _perk_row_regions(frame: Frame) -> list[tuple[int, int]]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    roi = hsv[PERKS_SCAN_TOP:PERKS_SCAN_BOTTOM, PERKS_X1:PERKS_X2]
    colored = (roi[:, :, 1] > 45) & (roi[:, :, 2] > 70)
    active = colored.mean(axis=1) > 0.45
    indices = np.flatnonzero(active)
    if len(indices) == 0:
        return []

    regions: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for raw_index in indices[1:]:
        index = int(raw_index)
        if index > previous + 1:
            regions.append((start + PERKS_SCAN_TOP, previous + PERKS_SCAN_TOP))
            start = index
        previous = index
    regions.append((start + PERKS_SCAN_TOP, previous + PERKS_SCAN_TOP))
    return [
        (top, bottom)
        for top, bottom in regions
        if bottom - top + 1 >= MIN_FULL_ROW_HEIGHT
        and top >= PERKS_SAFE_TOP
        and bottom <= PERKS_SAFE_BOTTOM
    ]


def _perk_configuration_row_regions(frame: Frame) -> list[tuple[int, int]]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    roi = hsv[
        CONFIGURATION_SCAN_TOP:CONFIGURATION_SCAN_BOTTOM,
        CONFIGURATION_ROW_X1:CONFIGURATION_ROW_X2,
        2,
    ]
    active = (
        roi > CONFIGURATION_MIN_BACKGROUND_VALUE
    ).mean(axis=1) > 0.70
    indices = np.flatnonzero(active)
    if len(indices) == 0:
        return []

    regions: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for raw_index in indices[1:]:
        index = int(raw_index)
        if index > previous + 1:
            regions.append(
                (
                    start + CONFIGURATION_SCAN_TOP,
                    previous + CONFIGURATION_SCAN_TOP,
                )
            )
            start = index
        previous = index
    regions.append(
        (
            start + CONFIGURATION_SCAN_TOP,
            previous + CONFIGURATION_SCAN_TOP,
        )
    )
    return [
        (top, bottom)
        for top, bottom in regions
        if bottom - top + 1 >= CONFIGURATION_MIN_ROW_HEIGHT
    ]


def _recognize_configuration_text(
    frame: Frame,
    top: int,
    bottom: int,
    recognize: PerkTextFn,
) -> tuple[str, float, list[dict[str, Any]]]:
    candidates = []
    for x1 in (PERK_TEXT_X1, CONFIGURATION_TEXT_X1):
        raw_text, confidence = recognize(
            frame[top : bottom + 1, x1:CONFIGURATION_ROW_X2]
        )
        normalized = " ".join(str(raw_text or "").split())
        candidates.append(
            {
                "text_raw": normalized,
                "display_text": _normalize_perk_ocr(normalized),
                "confidence": round(float(confidence), 1),
                "text_x1": x1,
            }
        )
    best = max(
        candidates,
        key=lambda item: (
            bool(item["text_raw"]),
            float(item["confidence"]),
        ),
    )
    return (
        str(best["text_raw"]),
        float(best["confidence"]),
        candidates,
    )


def _perk_color(frame: Frame, top: int, bottom: int) -> str:
    hsv = cv2.cvtColor(frame[top : bottom + 1, PERKS_X1:PERKS_X2], cv2.COLOR_BGR2HSV)
    colored_hues = hsv[:, :, 0][(hsv[:, :, 1] > 50) & (hsv[:, :, 2] > 70)]
    if len(colored_hues) == 0:
        return "unknown"
    hue = int(np.bincount(colored_hues, minlength=180).argmax())
    if 45 <= hue <= 75:
        return "green"
    if 105 <= hue <= 130:
        return "blue"
    if 132 <= hue <= 160:
        return "purple"
    return "unknown"


def _ocr_perk_text(crop: Frame) -> tuple[str, float]:
    if pytesseract is None:
        raise RuntimeError("pytesseract is unavailable")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    data = pytesseract.image_to_data(
        enlarged,
        config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )
    tokens: list[str] = []
    confidences: list[float] = []
    for raw_text, raw_confidence in zip(data.get("text", []), data.get("conf", [])):
        text = str(raw_text or "").strip()
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if not text or confidence < 0:
            continue
        tokens.append(text)
        confidences.append(confidence)
    return (
        " ".join(tokens),
        sum(confidences) / len(confidences) if confidences else -1.0,
    )


def _normalize_perk_ocr(text: str) -> str:
    normalized = " ".join((text or "").split())
    normalized = re.sub(r"(?<=\+)\](?=\s|$)", "1", normalized)
    normalized = re.sub(
        r"(\bperk wave requirement\s+-)/(?=5\.00%)",
        r"\g<1>7",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"(?<=-)/(?=\d)", "", normalized)
    normalized = re.sub(r"([+-])\s+(?=\d)", r"\1", normalized)
    normalized = re.sub(r"\bspeec\b", "speed", normalized, flags=re.IGNORECASE)
    return normalized


def _find_duplicate(
    selected: Sequence[Mapping[str, Any]],
    display_text: str,
    color: str,
) -> Optional[dict[str, Any]]:
    normalized = _comparison_text(display_text)
    for perk in selected:
        if perk.get("color") != color:
            continue
        existing = _comparison_text(str(perk.get("display_text") or ""))
        if SequenceMatcher(None, existing, normalized).ratio() >= 0.88:
            return perk  # type: ignore[return-value]
    return None


def _comparison_text(text: str) -> str:
    return re.sub(r"[^a-z0-9.%+-]+", " ", (text or "").casefold()).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").casefold()).strip("_")


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "ocr_perk_configuration_row_near",
    "ocr_perk_configuration_rows",
    "ocr_perk_rows",
    "ocr_selected_perks",
]
